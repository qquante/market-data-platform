"""Bounded received-time valuation series over already published token facts."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import re

from .valuation import PATHS, build_valuation

TRAJECTORY_VERSION = 'gmgn-trajectory-v1'
METHODS = {
    'gmgn.token_info.price_times_circulating_supply.v1': ('token_info', 720),
    'gmgn.market_rank.v4.market_cap': ('market_rank', 120),
    'gmgn.trenches.v1.usd_market_cap': ('trenches', 120),
    'gmgn.trenches.observed_market_cap_usd.v1': ('trenches', 120),
}
MAX_POINTS = 2000
MAX_POINT_FACTS = len(PATHS) + 2  # Optional name/symbol establish an item with missing economics.


class TrajectoryLimit(ValueError):
    pass


def item_pattern(endpoint):
    bases = {
        'token_info': r'((?:/data)?)',
        'market_rank': r'((?:/data)?(?:/rank)?/[0-9]+|/data/data/rank/[0-9]+)',
        'trenches': r'((?:/data)?/(?:new_creation|pump|near_completion|completed)/[0-9]+)',
    }
    return '^' + bases[endpoint] + '/(?:' + '|'.join(re.escape(p) for p in (*PATHS, 'name', 'symbol')) + ')$'


SQL = """
WITH candidates AS MATERIALIZED (
    SELECT DISTINCT c.capture_id,c.received_at,c.recorded_at,c.raw_sha256,c.evidence_kind
    FROM facts history JOIN captures c USING(capture_id)
    WHERE history.source_kind=$1 AND history.chain=$2 AND history.token_address=$3
      AND history.available_at<=$6 AND c.source_kind=$1 AND c.endpoint=$7
      AND c.received_at>=$4 AND c.received_at<$5
    ORDER BY c.received_at,c.capture_id LIMIT $9
)
SELECT c.*, revision.normalizer_version,revision.available_at,revision.revision_count,
       revision.previous_normalizer_version,revision.content_sha256,
       coalesce(inputs.rows,'[]'::jsonb) AS input_rows
FROM candidates c
JOIN LATERAL (
    SELECT normalizer_version,available_at,content_sha256,
           count(*) OVER () AS revision_count,
           lead(normalizer_version) OVER (ORDER BY available_at DESC,normalizer_version DESC)
               AS previous_normalizer_version
    FROM normalization_runs
    WHERE capture_id=c.capture_id AND available_at<=$6
    ORDER BY available_at DESC,normalizer_version DESC LIMIT 1
) revision ON true
LEFT JOIN LATERAL (
    SELECT jsonb_agg(to_jsonb(selected)-'numeric_value' ORDER BY selected.pointer,selected.fact_id) AS rows
    FROM (
        SELECT f.* FROM facts f WHERE f.capture_id=c.capture_id
          AND f.normalizer_version=revision.normalizer_version AND f.available_at<=$6
          AND f.source_kind=$1 AND f.chain=$2 AND f.token_address=$3 AND f.pointer ~ $8
        ORDER BY f.pointer,f.fact_id LIMIT 15
    ) selected
) inputs ON true
ORDER BY c.received_at,c.capture_id
"""


def build_point(record, endpoint, method, *, include_context=False):
    facts = record['input_rows']
    if isinstance(facts, str):
        facts = json.loads(facts)
    if include_context:
        # JSONB may trim fractional trailing zeros; match the native timestamptz reader.
        facts = [dict(f, available_at=datetime.fromisoformat(f['available_at']).astimezone(timezone.utc))
                 if isinstance(f.get('available_at'), str) else f for f in facts]
    point = {
        'point_id': str(record['capture_id']), 'capture_id': str(record['capture_id']),
        'observed_at': record['received_at'], 'capture_recorded_at': record['recorded_at'],
        'available_at': record['available_at'], 'normalizer_version': record['normalizer_version'],
        'revision_count': record['revision_count'],
        'previous_normalizer_version': record['previous_normalizer_version'],
        'interpretation_sha256': record['content_sha256'],
        'raw_sha256': record['raw_sha256'], 'evidence_kind': record['evidence_kind'],
        'method': method, 'state': 'unavailable', 'value_text': None, 'unit': 'usd',
        'anchor_fact_id': None, 'item_pointer': None, 'input_fact_ids': [],
    }
    if len(facts) > MAX_POINT_FACTS:
        point['reason'] = 'snapshot_input_budget_exceeded'
        return point
    pattern = re.compile(item_pattern(endpoint))
    groups = {}
    for fact in facts:
        match = pattern.fullmatch(fact['pointer'])
        if not match:
            point['reason'] = 'unsupported_item_pointer'
            return point
        groups.setdefault(match[1], []).append(fact)
    if len(groups) != 1:
        point['reason'] = 'no_current_token_facts' if not groups else 'ambiguous_token_items'
        return point
    base, facts = next(iter(groups.items()))
    anchor = facts[0]
    valuation = build_valuation(anchor, endpoint, base, facts, method=method)
    cap = valuation['market_cap']
    if cap.get('method') != method:
        point['reason'] = 'method_mismatch'
        return point
    point.update(
        state=cap['state'], value_text=cap['value_text'], kind=cap['kind'],
        supply_basis=cap['supply_basis'], input_fact_ids=cap['input_fact_ids'],
        anchor_fact_id=str(anchor['fact_id']), item_pointer=base,
        price={k:valuation['price'].get(k) for k in ('state','value_text','unit','reason')},
    )
    from .trench_economics import METHOD as EMPIRICAL_METHOD,QUALIFICATION
    if method==EMPIRICAL_METHOD:
        point.update(economic_qualification=QUALIFICATION,semantic_policy='empirical interpretation learned later')
    if cap['state'] != 'available':
        point['reason'] = cap.get('reason', 'input_unavailable')
    if include_context:
        point['valuation_context'] = {
            'valuation_version':valuation['version'],
            'economic_inputs_version':valuation['economic_inputs']['version'],
            'price':valuation['price'],'market_cap':cap,
            'economic_inputs':valuation['economic_inputs']['fields'],
        }
    return point


def build_series(records, *, source_kind, chain, token, method, start, end, cutoff, gap_after_seconds,
                 include_context=False):
    endpoint = METHODS[method][0]
    points = [build_point(r, endpoint, method, include_context=include_context) for r in records]
    points.sort(key=lambda p:(p['observed_at'], p['capture_id']))
    timestamps = Counter(p['observed_at'] for p in points)
    gaps = []
    previous = None
    for point in points:
        reason = None
        if point['state'] != 'available':
            reason = 'unavailable_observation'
        elif timestamps[point['observed_at']] > 1 or (
                previous and timestamps[previous['observed_at']] > 1):
            reason = 'ambiguous_observation_order'
        elif previous:
            if previous['state'] != 'available':
                reason = 'after_unavailable_observation'
            elif (point['observed_at']-previous['observed_at']).total_seconds() > gap_after_seconds:
                reason = 'observation_gap'
        point['break_before'] = reason
        point['same_timestamp_count'] = timestamps[point['observed_at']]
        previous = point
    valid = [p for p in points if p['state']=='available']
    # These intervals mark lack of eligible samples; no values are interpolated or carried forward.
    bounds = [(start, None)] + [(p['observed_at'],p['capture_id']) for p in valid] + [(end,None)]
    for (left,lid),(right,rid) in zip(bounds,bounds[1:]):
        delta = (right-left).total_seconds()
        if not valid or delta > gap_after_seconds:
            gaps.append({'start':left,'end':right,'duration_seconds':delta,
                         'left_capture_id':lid,'right_capture_id':rid,
                         'reason':'no_eligible_observations' if not valid else
                             'leading_gap' if lid is None else 'trailing_gap' if rid is None else 'observation_gap'})
    return {
        'version':TRAJECTORY_VERSION, 'source_kind':source_kind,'chain':chain,'token_address':token,
        'method':method,'endpoint':endpoint,'unit':'usd','time_basis':'capture_received_at',
        'start':start,'end':end,'knowledge_cutoff':cutoff,
        'points':points,'gaps':gaps,'query_complete':True,
        'coverage':{
            'observed_points':len(points),'eligible_points':len(valid),'unavailable_points':len(points)-len(valid),
            'revised_points':sum(p['revision_count']>1 for p in points),
            'ambiguous_timestamp_points':sum(p['same_timestamp_count']>1 for p in points),
            'gap_after_seconds':gap_after_seconds,'interpolation':'none',
            'basis':'captures with published token facts known at cutoff; not all provider events or admitted periods',
        },
        'revision_policy':'latest whole interpretation per capture known at cutoff, including empty invalidating revisions',
        'evidence_policy':'persisted facts and raw hash references; original bytes are not reread by this series query',
        'pool_scope':'provider token quote for this endpoint; no claim of pool equivalence or cross-method continuity',
    }


async def read_trajectory(store, *, source_kind, chain, token, method, start, end, cutoff,
                          max_points=500, gap_after_seconds=None, include_context=False):
    if method not in METHODS:
        raise ValueError('unsupported_trajectory_method')
    if not 1 <= max_points <= MAX_POINTS:
        raise ValueError('invalid_trajectory_point_budget')
    endpoint, default_gap = METHODS[method]
    rows = await store._temporal_fetch(cutoff,SQL,source_kind,chain,token,start,end,cutoff,
                                       endpoint,item_pattern(endpoint),max_points+1)
    if len(rows)>max_points:
        raise TrajectoryLimit('Too many points; reduce interval or increase max_points up to 2000')
    return build_series([dict(r) for r in rows],source_kind=source_kind,chain=chain,token=token,method=method,
                        start=start,end=end,cutoff=cutoff,
                        gap_after_seconds=gap_after_seconds if gap_after_seconds is not None else default_gap,
                        include_context=include_context)

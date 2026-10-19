"""Versioned market-cap thresholds and bounded, retrospective crossings."""
from bisect import bisect_right
from decimal import Decimal
import hashlib
import json

from .trajectory import TrajectoryLimit

GRID_VERSION = 'gmgn-market-cap-grid-v1'
CROSSINGS_VERSION = 'gmgn-crossings-v1'
BANDS = ((20000,100000,10000),(100000,200000,25000),(200000,500000,50000),
         (500000,1000000,100000),(1000000,3000000,250000),(3000000,5000000,500000),
         (5000000,10000000,1000000),(10000000,100000000,10000000))
LEVELS = tuple(sorted({n for low,high,step in BANDS for n in range(low,high+1,step)}))
MAX_EVENTS = 2000


def _id(parts):
    return hashlib.sha256(json.dumps(parts,separators=(',',':')).encode()).hexdigest()


def _changed(left, right):
    if left.get('state')!='available' or right.get('state')!='available':
        return None
    return Decimal(left['value_text']) != Decimal(right['value_text'])


def build_crossings(series, *, grid_version=GRID_VERSION, max_events=1000):
    if grid_version != GRID_VERSION:
        raise ValueError('Unsupported market-cap grid')
    if not series['query_complete']:
        raise ValueError('Crossings require a complete bounded trajectory')
    if not 1 <= max_events <= MAX_EVENTS:
        raise ValueError('Invalid crossings event budget')
    identity = [CROSSINGS_VERSION,grid_version,series['source_kind'],series['chain'],
                series['token_address'],series['method'],series['coverage']['gap_after_seconds']]
    events, segments, snapshots = [], [], {}
    previous = None
    seen_above = set()
    segment = None
    required_knowledge_at = None

    def snapshot(point):
        key = point['point_id']
        if key not in snapshots:
            snapshots[key] = {'point_id':key,'capture_id':point['capture_id'],
                'raw_sha256':point['raw_sha256'],'interpretation_sha256':point['interpretation_sha256'],
                'available_at':point['available_at'],'context':point['valuation_context']}

    for point in series['points']:
        if point['state']!='available' or point['same_timestamp_count']>1:
            previous = None
            continue
        value = Decimal(point['value_text'])
        if previous is None or point['break_before'] is not None:
            seen_above = set(LEVELS[:bisect_right(LEVELS,value)])
            segment = {
                'segment_id':_id(identity+[point['point_id'],point['interpretation_sha256']]),
                'start_point_id':point['point_id'],'end_point_id':point['point_id'],
                'start_at':point['observed_at'],'end_at':point['observed_at'],
                'start_reason':point['break_before'] or 'no_eligible_predecessor_in_query',
                'initial_levels_at_or_above':[str(n) for n in sorted(seen_above)],
            }
            segments.append(segment)
            snapshot(point)
            required_knowledge_at = point['available_at']
            previous = point
            continue
        prior = Decimal(previous['value_text'])
        required_knowledge_at = max(required_knowledge_at,point['available_at'])
        low, high = sorted((prior,value))
        crossed = LEVELS[bisect_right(LEVELS,low):bisect_right(LEVELS,high)]
        # The array order below is presentation only, not an inferred path between snapshots.
        if value < prior:
            crossed = tuple(reversed(crossed))
        if len(events)+len(crossed)>max_events:
            raise TrajectoryLimit('Too many crossings; reduce interval or increase max_events up to 2000')
        if crossed:
            snapshot(previous);snapshot(point)
            left_context,right_context=previous['valuation_context'],point['valuation_context']
            changes={
                'price_changed':_changed(left_context['price'],right_context['price']),
                'circulating_supply_changed':_changed(
                    left_context['economic_inputs']['circulating_supply'],
                    right_context['economic_inputs']['circulating_supply']),
            }
            for level in crossed:
                kind = ('reentry' if level in seen_above else 'upcross') if value>prior else 'downcross'
                if value>prior:
                    seen_above.add(level)
                event_id=_id(identity+[segment['segment_id'],previous['point_id'],
                    previous['interpretation_sha256'],point['point_id'],point['interpretation_sha256'],str(level),kind])
                events.append({
                    'event_id':event_id,'segment_id':segment['segment_id'],'level_text':str(level),
                    'direction':'up' if value>prior else 'down','kind':kind,
                    'left_point_id':previous['point_id'],'right_point_id':point['point_id'],
                    'interval_start':previous['observed_at'],'interval_end':point['observed_at'],
                    'interval_bounds':'(start,end]','exact_crossing_at':None,
                    'required_knowledge_at':required_knowledge_at,'evidence':'inferred_between_observed_snapshots',
                    'context_changes':changes,
                })
        segment['end_point_id']=point['point_id'];segment['end_at']=point['observed_at']
        previous=point
    # Context appears once per referenced snapshot, not once per level or in every curve point.
    points=[{k:v for k,v in p.items() if k!='valuation_context'} for p in series['points']]
    return dict(series,points=points,crossings={
        'version':CROSSINGS_VERSION,
        'grid':{'version':grid_version,'unit':'usd','levels_text':[str(n) for n in LEVELS],
                'bands':[{'from':low,'through':high,'step':step} for low,high,step in BANDS],
                'outside_range':'retain observations; no thresholds below 20000 or above 100000000'},
        'events':events,'segments':segments,'context_snapshots':list(snapshots.values()),
        'counts':{kind:sum(e['kind']==kind for e in events) for kind in ('upcross','downcross','reentry')},
        'max_events':max_events,'query_complete':True,
        'reentry_scope':'prior at-or-above observation in the same uninterrupted segment of this query',
        'first_passage_scope':'no lifetime or global first-passage claim; query start supplies no predecessor',
        'gap_policy':'unavailable values, tied times and trajectory breaks reset comparison and reentry state',
        'time_policy':'received-time interval only; skipped thresholds share endpoints; no interpolated snapshots or intrainterval order',
        'derivation_mode':'retrospective at the requested knowledge cutoff; no notifications, strategy state or execution',
    })

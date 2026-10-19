"""Small, process-local feature reuse over bounded, already published trajectories."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from uuid import UUID

from .trajectory import MAX_POINTS, TrajectoryLimit

FEATURE_VERSION = 'gmgn-window-features-v1'
# Latest observation and window statistics have different dependency sets.
POINT_INPUTS = ('point_id', 'observed_at', 'available_at', 'normalizer_version',
    'interpretation_sha256', 'raw_sha256', 'state', 'reason', 'value_text',
    'input_fact_ids', 'anchor_fact_id', 'same_timestamp_count', 'kind', 'supply_basis')


def _json(value):
    def encode(item):
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, UUID):
            return str(item)
        raise TypeError(type(item).__name__)
    return json.dumps(value, default=encode, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _missing(reason, unit='usd'):
    return dict(state='unavailable', value_text=None, unit=unit, reason=reason, input_point_ids=[])


def _value(point):
    return dict(state='available', value_text=point['value_text'], unit='usd',
                input_point_ids=[point['point_id']])


def _latest(series):
    points = series['points']
    if not points:
        return _missing('no_observations')
    last = points[-1]
    if last['same_timestamp_count'] > 1:
        value = _missing('ambiguous_latest_observation')
    elif last['state'] != 'available':
        value = _missing('latest_observation_unavailable')
    elif (series['end']-last['observed_at']).total_seconds() > series['coverage']['gap_after_seconds']:
        value = _missing('stale_at_window_end')
    else:
        value = _value(last)
    value.update(observed_at=last['observed_at'],
                 age_at_window_end_seconds=(series['end']-last['observed_at']).total_seconds(),
                 input_point_ids=[p['point_id'] for p in points if p['observed_at'] == last['observed_at']])
    return value


def _percent(first, last):
    a, b = Decimal(first), Decimal(last)
    # Exact subtraction/scaling before one explicitly rounded division, including tiny changes.
    precision = max(34, max(a.adjusted(), b.adjusted()) - min(a.as_tuple().exponent, b.as_tuple().exponent) + 4)
    context = Context(prec=precision, rounding=ROUND_HALF_EVEN, Emin=-9999, Emax=9999,
                      capitals=1, clamp=0, traps=[InvalidOperation, DivisionByZero, Overflow])
    with localcontext(context) as ctx:
        numerator = (b-a)*100
        ctx.prec = 34
        return str(numerator/a)


def _window(series):
    points = series['points']
    valid = [p for p in points if p['state'] == 'available']
    result = {}
    values = [(Decimal(p['value_text']), p) for p in valid]
    for name, choose in (('market_cap_min_observed', min), ('market_cap_max_observed', max)):
        if not values:
            result[name] = _missing('no_eligible_observations')
            continue
        extreme = choose(v for v, _ in values)
        matches = [p for v, p in values if v == extreme]
        result[name] = dict(_value(matches[0]), input_point_ids=[p['point_id'] for p in matches])
    reason = None
    if len(valid) < 2:
        reason = 'insufficient_observations'
    elif len(valid) != len(points):
        reason = 'unavailable_observation_in_window'
    elif any(p['same_timestamp_count'] > 1 for p in points):
        reason = 'ambiguous_observation_order'
    elif series['gaps'] or any(p['break_before'] for p in points):
        reason = 'window_has_gaps'
    elif Decimal(valid[0]['value_text']) == 0:
        reason = 'zero_baseline'
    if reason:
        change = _missing(reason, 'percent')
    else:
        first, last = valid[0], valid[-1]
        change = dict(state='available', unit='percent', value_text=_percent(first['value_text'], last['value_text']),
            input_point_ids=[first['point_id'], last['point_id']], first_observed_at=first['observed_at'],
            last_observed_at=last['observed_at'], observed_span_seconds=(last['observed_at']-first['observed_at']).total_seconds())
    change.update(formula='100 * (last_sample - first_sample) / first_sample',
                  precision='34 significant digits; ROUND_HALF_EVEN',
                  basis='observed market cap samples; not exact boundary return or price-only performance')
    result['market_cap_sample_change_pct'] = change
    return result


class SharedFeatures:
    """One synchronous owner per API process; bounded serialized values, no I/O."""
    def __init__(self, max_entries=128, max_bytes=2*1024**2):
        if max_entries < 1 or max_bytes < 1:
            raise ValueError('Feature cache budgets must be positive')
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.entries = OrderedDict()
        self.bytes = 0
        self.computations = 0
        self.reuses = 0

    def _node(self, key, compute):
        if key in self.entries:
            self.entries.move_to_end(key)
            self.reuses += 1
            return json.loads(self.entries[key]), True
        result = _json(compute())
        self.computations += 1
        if len(result) <= self.max_bytes:
            while self.entries and (len(self.entries) >= self.max_entries or self.bytes+len(result) > self.max_bytes):
                _, old = self.entries.popitem(last=False)
                self.bytes -= len(old)
            self.entries[key] = result
            self.bytes += len(result)
        # Decoding also prevents one caller from mutating another caller's cached value.
        return json.loads(result), False

    def build(self, series):
        if not series['query_complete'] or len(series['points']) > MAX_POINTS:
            raise TrajectoryLimit('Features require a complete bounded trajectory')
        scope = {k:series[k] for k in ('version', 'source_kind', 'chain', 'token_address',
            'method', 'unit', 'time_basis', 'pool_scope', 'start', 'end')}
        scope.update(feature_version=FEATURE_VERSION, gap_after_seconds=series['coverage']['gap_after_seconds'])
        points = series['points']
        inputs = [{k:p.get(k) for k in POINT_INPUTS} for p in points]
        latest_inputs = [p for p in inputs if p['observed_at'] == inputs[-1]['observed_at']] if inputs else []
        latest_key = _hash([scope, 'latest', latest_inputs])
        window_key = _hash([scope, 'window', inputs, series['gaps'],
                            [p['break_before'] for p in points]])
        latest, latest_hit = self._node(latest_key, lambda: _latest(series))
        window, window_hit = self._node(window_key, lambda: _window(series))
        frame = dict(version=FEATURE_VERSION, frame_id=_hash([FEATURE_VERSION, latest_key, window_key]),
            window=dict(start=series['start'], end=series['end'], boundaries='[start,end)',
                        duration_seconds=(series['end']-series['start']).total_seconds()),
            dependency_keys=dict(latest=latest_key, window=window_key),
            required_knowledge_at=max((p['available_at'] for p in points), default=None),
            features=dict(market_cap_latest=latest, **window),
            quality=dict(unavailable_points=series['coverage']['unavailable_points'],
                ambiguous_timestamp_points=series['coverage']['ambiguous_timestamp_points'],
                flagged_gap_count=len(series['gaps']), interpolation='none'),
            provenance='dependency keys cover source/method/window/policy and ordered observation revisions; see trajectory points',
            scope='read-only sampled window; not a strategy decision or a durable consumed frame')
        return dict(series, features=frame), dict(reused=int(latest_hit)+int(window_hit),
                                                  computed=2-int(latest_hit)-int(window_hit))

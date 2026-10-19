"""UTC-day summary of an existing bounded trajectory; no writes or resampling."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

DAILY_VERSION = 'gmgn-daily-summary-v1'


def day_bounds(day: date, cutoff: datetime):
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError('Timezone required for knowledge_cutoff')
    cutoff = cutoff.astimezone(timezone.utc)
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    try:
        day_end = start + timedelta(days=1)
    except OverflowError:
        raise ValueError('UTC day exceeds the supported calendar') from None
    if cutoff <= start:
        raise ValueError('UTC day must start before knowledge_cutoff')
    return start, min(day_end, cutoff), day_end


def _mark(points, *, ordered=False):
    if not points:
        return {'state':'unavailable','reason':'no_eligible_observations',
                'value_text':None,'point_id':None,'matching_point_ids':[]}
    ids = [p['point_id'] for p in points]
    if ordered and len({Decimal(p['value_text']) for p in points}) > 1:
        return {'state':'unavailable','reason':'ambiguous_observation_order',
                'value_text':None,'point_id':None,'matching_point_ids':ids,
                'observed_at':points[0]['observed_at']}
    p = points[0]
    return {'state':'available','value_text':p['value_text'],'point_id':p['point_id'],
            'capture_id':p['capture_id'],'observed_at':p['observed_at'],
            'normalizer_version':p['normalizer_version'],'anchor_fact_id':p['anchor_fact_id'],
            'matching_point_ids':ids}


def build_daily(series, day: date):
    start, end, day_end = day_bounds(day, series['knowledge_cutoff'])
    if series['start'] != start or series['end'] != end or not series['query_complete']:
        raise ValueError('Daily summary requires the complete UTC-day query at this cutoff')
    valid = [p for p in series['points'] if p['state']=='available']
    if valid:
        first_time, last_time = valid[0]['observed_at'], valid[-1]['observed_at']
        values = [(Decimal(p['value_text']), p) for p in valid]
        minimum, maximum = min(v for v,_ in values), max(v for v,_ in values)
        first = _mark([p for p in valid if p['observed_at']==first_time], ordered=True)
        last = _mark([p for p in valid if p['observed_at']==last_time], ordered=True)
        low = _mark([p for v,p in values if v==minimum])
        high = _mark([p for v,p in values if v==maximum])
    else:
        first_time = last_time = None
        first, last, low, high = (_mark([]) for _ in range(4))
    boundaries = [start]+[p['observed_at'] for p in valid]+[end]
    summary = {
        'version':DAILY_VERSION,'date_utc':day.isoformat(),'day_start':start,'day_end':day_end,
        'through_at':end,'calendar_state':'ended' if end==day_end else 'in_progress',
        'state':'available' if valid else 'unavailable',
        'first_observation':first,'last_observation':last,
        'minimum_observed':low,'maximum_observed':high,
        'has_known_revisions':series['coverage']['revised_points']>0,
        'coverage':{
            'query_seconds':(end-start).total_seconds(),
            'not_elapsed_seconds':(day_end-end).total_seconds(),
            'first_eligible_at':first_time,'last_eligible_at':last_time,
            'leading_seconds':(first_time-start).total_seconds() if valid else None,
            'trailing_seconds':(end-last_time).total_seconds() if valid else None,
            'largest_sample_interval_seconds':max((b-a).total_seconds() for a,b in zip(boundaries,boundaries[1:])),
            'flagged_gap_count':len(series['gaps']),
            'flagged_gap_seconds':sum(g['duration_seconds'] for g in series['gaps']),
            'continuity_breaks':sum(p['break_before'] is not None for p in series['points']),
            'basis':'sample intervals and configured gap threshold; not continuous market coverage',
        },
        'scope':'observed market cap only; not exchange OHLC, full-day market extremes or historical ATH',
        'finality':'calendar end does not freeze data; later publication may revise the summary at a later cutoff',
    }
    if not valid:
        summary['reason']='no_eligible_observations'
    return dict(series, daily=summary)

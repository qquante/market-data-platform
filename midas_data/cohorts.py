"""Pure cohort labels and research frames. Missing follow-up never becomes a loss."""
from __future__ import annotations
from collections import Counter
from datetime import timedelta
from decimal import Decimal,localcontext
from .contracts import utc_datetime
from .research_contracts import LABEL_VERSION,number
from .features import _hash

STATES=('reached','not_reached','censored','unknown')


def known(point,at):
    return (point.get('state')=='available' and point.get('same_timestamp_count',1)==1
            and utc_datetime(point['available_at'])<=at and utc_datetime(point['observed_at'])<=at)


def latest_frame(series,at,gap):
    if not series['query_complete']:raise ValueError('partial_series')
    candidates=[p for p in series['points'] if utc_datetime(p['observed_at'])<=at
                and utc_datetime(p['available_at'])<=at]
    if not candidates:return {'state':'unknown','reason':'no_known_quote','point':None,'decision_at':at}
    last=max(candidates,key=lambda p:(utc_datetime(p['observed_at']),p['capture_id']))
    if not known(last,at) or (at-utc_datetime(last['observed_at'])).total_seconds()>gap:
        return {'state':'unknown','reason':'invalid_ambiguous_or_stale_quote','point':last,'decision_at':at}
    return {'state':'available','reason':None,'point':last,'decision_at':at}


def labels(points,*,start,end,cutoff,gap_seconds,levels):
    # Retrospective labels are explicit. They are never selection/entry features.
    eligible=[p for p in points if start<=utc_datetime(p['observed_at'])<=end
              and utc_datetime(p['available_at'])<=cutoff and known(p,cutoff)]
    eligible.sort(key=lambda p:(utc_datetime(p['observed_at']),p['capture_id']))
    invalid=sum(start<=utc_datetime(p['observed_at'])<=end and utc_datetime(p['available_at'])<=cutoff
                and not known(p,cutoff) for p in points)
    gaps=[]
    bounds=[start]+[utc_datetime(p['observed_at']) for p in eligible]+[min(end,cutoff)]
    for a,b in zip(bounds,bounds[1:]):
        if (b-a).total_seconds()>gap_seconds:gaps.append({'start':a,'end':b,'seconds':(b-a).total_seconds()})
    complete=bool(eligible) and cutoff>=end and not gaps and not invalid
    result=[]
    for level in levels:
        witness=next((p for p in eligible if number(p['value_text'])>=number(level,positive=True)),None)
        if witness:state,reason='reached','at_or_above_observed'
        elif not eligible:state,reason='unknown','no_eligible_followup'
        elif cutoff<end:state,reason='censored','horizon_not_mature'
        elif not complete:state,reason='censored','followup_gap_or_invalid'
        else:state,reason='not_reached','no_observed_sample_reached_level'
        result.append({'version':LABEL_VERSION,'level_usd':level,'state':state,'reason':reason,
            'witness_capture_id':witness['capture_id'] if witness else None,
            'witness_observed_at':witness['observed_at'] if witness else None,
            'event_definition':'at_or_above_at_observed_samples; no claim about between-sample crossings'})
    return {'labels':result,'coverage':{'eligible_points':len(eligible),'invalid_points':invalid,
        'complete_under_sampling_policy':complete,'horizon_mature':cutoff>=end,'gaps':gaps,
        'interval_censoring':'between observations unobserved','interpolation':'none'}}


def holder_feature(snapshot,decision_at,max_age):
    unknown=lambda reason:dict(state='unknown',reason=reason,value_text=None,capture_id=None)
    if snapshot is None:return unknown('no_holder_snapshot')
    run=snapshot['run']
    if utc_datetime(run['available_at'])>decision_at:return unknown('holder_not_known_at_decision')
    meta=run['metadata'];params=meta['request_params'];params=params.get('query',params)
    if (params.get('order_by')!='amount_percentage' or params.get('direction')!='desc'
            or params.get('tag') is not None or run['state']!='ready'):
        return unknown('holder_selection_or_quality_incompatible')
    if (decision_at-utc_datetime(meta['received_at'])).total_seconds()>max_age:
        return unknown('holder_stale')
    top=sorted(snapshot['items'],key=lambda x:x['ordinal'])[:10]
    if len(top)!=10 or len({h['wallet_address'] for h in top})!=10:
        return unknown('insufficient_distinct_top10')
    amounts=[]
    for row in top:
        value=row['values'].get('amount_percentage')
        if row['identity_quality']!='valid' or value is None or value['quality']!='valid':
            return unknown('unverified_holder_share')
        amounts.append(number(value['value_text']))
    with localcontext() as ctx:
        ctx.prec=50;share=sum(amounts,Decimal(0))
    if share>1:return unknown('inconsistent_share_total')
    return {'state':'available','value_text':str(share),'unit':'fraction','capture_id':str(run['capture_id']),
        'scope':'sum of ten returned wallet shares; provider supply denominator unverified',
        'raw_sha256':run['raw_sha256'],'available_at':run['available_at']}


def summarize(members,levels):
    summaries=[]
    for level in levels:
        counts=Counter(next(l for l in m['outcome']['labels'] if l['level_usd']==level)['state'] for m in members)
        n=len(members);resolved=counts['reached']+counts['not_reached']
        with localcontext() as ctx:
            ctx.prec=34
            ratio=lambda numerator,denominator:None if not denominator else str(Decimal(numerator)/denominator)
            summaries.append({'level_usd':level,'N':n,**{state:counts[state] for state in STATES},
                'resolved':resolved,'observed_rate_among_resolved':ratio(counts['reached'],resolved),
                'population_lower_bound':ratio(counts['reached'],n),
                'population_upper_bound':ratio(n-counts['not_reached'],n),
                'interpretation':'descriptive bounds for this observed cohort; sampling probabilities unknown'})
        assert sum(counts.values())==n
    return {'population':len(members),'levels':summaries,
        'entry_available':sum(m['entry']['state']=='available' for m in members),
        'entry_unknown':sum(m['entry']['state']!='available' for m in members),
        'holder_available':sum(m['holder']['state']=='available' for m in members),
        'member_ids_sha256':_hash([m['registry_id'] for m in members]),
        'conclusion_level':'descriptive','cross_token_independence':'not established'}

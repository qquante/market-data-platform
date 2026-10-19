"""Reproducible quote-based portfolio simulations; never orders or fill evidence."""
from __future__ import annotations
from collections import Counter
from datetime import datetime,timedelta
from decimal import Decimal,localcontext
from typing import Literal
from pydantic import Field,StrictInt,StrictStr,field_validator,model_validator
from .contracts import ContractModel,utc_datetime
from .cohorts import known,latest_frame,labels,holder_feature,summarize
from .features import _hash,_json
from .research_contracts import ResearchSpec,RESEARCH_VERSION,MAX_DATASET_BYTES,number
from .studies import StudySpec,run_studies

EXPERIMENT_VERSION='gmgn-quote-experiment-v1'


class Strategy(ContractModel):
    name:StrictStr=Field(min_length=1,max_length=80)
    market_cap_min:StrictStr|None=None
    market_cap_max:StrictStr|None=None
    holder_top10_share_max:StrictStr|None=None

    @model_validator(mode='after')
    def bounds(self):
        for v in [self.market_cap_min,self.market_cap_max,self.holder_top10_share_max]:
            if v is not None:number(v)
        if self.market_cap_min is not None and self.market_cap_max is not None and number(self.market_cap_min)>number(self.market_cap_max):
            raise ValueError('inverted_market_cap_range')
        if self.holder_top10_share_max is not None and number(self.holder_top10_share_max)>1:
            raise ValueError('holder_share_is_fraction')
        return self


class ExperimentSpec(ContractModel):
    strategies:list[Strategy]=Field(min_length=1,max_length=20)
    studies:list[StudySpec]=Field(default_factory=list,max_length=10)
    train_end:datetime
    validation_end:datetime
    test_end:datetime
    embargo_seconds:StrictInt=Field(ge=0,le=86400)
    fee_bps:StrictStr
    slippage_bps:StrictStr
    network_usd_per_side:StrictStr
    notional_usd:StrictStr
    starting_cash_usd:StrictStr
    max_positions:StrictInt=Field(ge=1,le=100)
    purpose:Literal['exploratory']='exploratory'

    @field_validator('train_end','validation_end','test_end',mode='before')
    @classmethod
    def clocks(cls,v):return utc_datetime(v)

    @model_validator(mode='after')
    def valid(self):
        if not self.train_end<self.validation_end<self.test_end:raise ValueError('chronological_splits_required')
        for v in [self.network_usd_per_side,self.notional_usd,self.starting_cash_usd]:number(v)
        number(self.notional_usd,positive=True);number(self.starting_cash_usd,positive=True)
        if any(number(v)>=10000 for v in [self.fee_bps,self.slippage_bps]):
            raise ValueError('cost_bps_out_of_range')
        if len({_hash(s.model_dump()) for s in self.strategies})!=len(self.strategies):
            raise ValueError('duplicate_strategy')
        return self


def verify_bundle(bundle):
    if bundle.get('version')!=RESEARCH_VERSION or len(_json(bundle))>MAX_DATASET_BYTES:
        raise ValueError('research_bundle_contract')
    definition=bundle['definition']
    spec=ResearchSpec.model_validate({k:v for k,v in definition.items() if k in ResearchSpec.model_fields})
    if definition!=spec.definition():raise ValueError('research_definition_changed')
    if bundle['job_id']!=_hash({'definition':definition,'implementation':bundle['implementation']}):
        raise ValueError('research_identity_changed')
    members=bundle['members']
    if len(members)>spec.max_members or len({m['registry_id'] for m in members})!=len(members):
        raise ValueError('research_membership_invalid')
    if len({(m['source_kind'],m['chain'],m['token_address']) for m in members})!=len(members):
        raise ValueError('duplicate_token_membership')
    if spec.sampling_seed is not None:
        from .research_sampling import verify
        verify(bundle.get('sampling_frame'),members,spec.sampling_seed,spec.max_members)
    elif 'sampling_frame' in bundle:raise ValueError('unexpected_sampling_frame')
    for m in members:
        registered=utc_datetime(m['registered_at']);entry=registered+timedelta(seconds=spec.entry_delay_seconds)
        end=entry+timedelta(seconds=spec.horizon_seconds)
        if not spec.cohort_start<=registered<spec.cohort_end or m['source_kind']!=spec.source_kind or m['chain']!=spec.chain:
            raise ValueError('member_outside_universe')
        if utc_datetime(m['entry_at'])!=entry or utc_datetime(m['exit_at'])!=end:raise ValueError('member_clock_changed')
        evidence=m['evidence']
        expected_entry=latest_frame(evidence['entry_series'],entry,spec.max_gap_seconds)
        expected_exit=latest_frame(evidence['exit_series'],end,spec.max_gap_seconds)
        outcome=labels(evidence['outcome_series']['points'],start=entry,end=end,cutoff=spec.knowledge_cutoff,
                       gap_seconds=spec.max_gap_seconds,levels=spec.levels_usd)
        holder=holder_feature(evidence['holder_snapshot'],entry,3600)
        for actual,expected in [(m['entry'],expected_entry),(m['exit'],expected_exit),(m['outcome'],outcome),(m['holder'],holder)]:
            if _hash(actual)!=_hash(expected):raise ValueError('derived_research_member_changed')
        for name,limit in [('entry_series',entry),('exit_series',end),('outcome_series',spec.knowledge_cutoff)]:
            series=evidence[name]
            if not series['query_complete']:raise ValueError('incomplete_archived_series')
            if len(series['points'])>spec.max_points_per_series:raise ValueError('archived_series_budget')
            for p in series['points']:
                if utc_datetime(p['available_at'])>min(limit,spec.knowledge_cutoff):
                    raise ValueError('future_evidence_in_archived_series')
    if _hash(summarize(members,spec.levels_usd))!=_hash(bundle['summary']):
        raise ValueError('research_denominator_changed')
    return spec


def selection(member,strategy):
    if member['entry']['state']!='available':return 'unknown'
    cap=number(member['entry']['point']['value_text'])
    if strategy.market_cap_min is not None and cap<number(strategy.market_cap_min):return 'nonmatch'
    if strategy.market_cap_max is not None and cap>number(strategy.market_cap_max):return 'nonmatch'
    if strategy.holder_top10_share_max is not None:
        h=member['holder']
        if h['state']!='available':return 'unknown'
        if number(h['value_text'])>number(strategy.holder_top10_share_max):return 'nonmatch'
    return 'match'


def quote(member,side,cutoff):
    at=utc_datetime(member[side+'_at'])
    if at>cutoff:return None
    frame=member[side]
    if frame['state']!='available' or not known(frame['point'],at):return None
    p=frame['point'].get('price')
    if not p or p.get('state')!='available' or p.get('unit')!='usd_per_token':return None
    try:return number(p['value_text'],positive=True)
    except ValueError:return None


def temporal_split(m,research,spec):
    registered=utc_datetime(m['registered_at']);entry=utc_datetime(m['entry_at']);end=utc_datetime(m['exit_at'])
    spans=[('train',research.cohort_start,spec.train_end),('validation',spec.train_end,spec.validation_end),
           ('test',spec.validation_end,spec.test_end)]
    for name,start,stop in spans:
        if start<=registered<stop:
            # Embargo derives from the longest input lookback used in this v1 (holder 1h).
            # Purge the whole label horizon at the right boundary, including equality.
            if registered<start+timedelta(seconds=spec.embargo_seconds) or end>=stop:return 'purged'
            if entry>=stop:return 'purged'
            return name
    return 'outside_period'


def portfolio(members,strategy,spec,cutoff):
    cash=number(spec.starting_cash_usd,positive=True);initial=cash
    notional=number(spec.notional_usd,positive=True);gas=number(spec.network_usd_per_side)
    fee=number(spec.fee_bps)/10000;slip=number(spec.slippage_bps)/10000
    events=[];decisions={};lots={};closed=[];timeline=[];peak=initial;max_dd=Decimal(0);realized=Decimal(0)
    total_fees=total_network=total_slippage=Decimal(0);max_open=0
    for m in members:
        events += [(utc_datetime(m['entry_at']),1,m['registry_id'],m),
                   (utc_datetime(m['exit_at']),0,m['registry_id'],m)]
    for at,kind,key,m in sorted(events,key=lambda e:e[:3]):
        if kind==1:
            decision=selection(m,strategy)
            if decision!='match':decisions[key]=decision;continue
            price=quote(m,'entry',cutoff)
            if price is None:decisions[key]='unknown';continue
            if len(lots)>=spec.max_positions or cash-len(lots)*gas<notional+2*gas:
                decisions[key]='blocked_capital';continue
            entry_fee=notional*fee
            qty=(notional-entry_fee)/(price*(1+slip))
            cash-=notional+gas
            total_fees+=entry_fee;total_network+=gas;total_slippage+=qty*price*slip
            lots[key]={'registry_id':key,'token_address':m['token_address'],'entry_at':at,
                       'entry_price':price,'quantity':qty,'cash_cost':notional+gas,
                       'entry_capture_id':m['entry']['point']['capture_id']}
            decisions[key]='opened';max_open=max(max_open,len(lots))
            timeline.append({'at':at,'kind':'opened','registry_id':key,'cash':str(cash)})
        elif key in lots:
            price=quote(m,'exit',cutoff)
            if price is None:continue
            lot=lots.pop(key);gross=lot['quantity']*price*(1-slip);exit_fee=gross*fee
            proceeds=gross-exit_fee-gas;cash+=proceeds;pnl=proceeds-lot['cash_cost'];realized+=pnl
            total_fees+=exit_fee;total_network+=gas;total_slippage+=lot['quantity']*price*slip
            closed.append({**{k:str(v) if isinstance(v,Decimal) else v for k,v in lot.items()},
                'exit_at':at,'exit_price':str(price),'proceeds_usd':str(proceeds),'net_pnl_usd':str(pnl),
                'exit_capture_id':m['exit']['point']['capture_id']})
            decisions[key]='closed'
            equity=initial+realized;peak=max(peak,equity);max_dd=max(max_dd,peak-equity)
            timeline.append({'at':at,'kind':'closed','registry_id':key,'cash':str(cash),'closed_lot_pnl':str(realized)})
    counts=Counter(decisions.values())
    assert sum(counts.values())==len(members)
    return {'population':len(members),'states':{k:counts[k] for k in ['unknown','nonmatch','blocked_capital','opened','closed']},
        'starting_cash_usd':str(initial),'ending_cash_usd':str(cash),'reserved_exit_network_usd':str(len(lots)*gas),
        'available_cash_usd':str(cash-len(lots)*gas),'closed_lot_pnl_usd':str(realized),
        'total_equity_usd':None if lots else str(cash),
        'return_pct':None if lots else str((cash-initial)*100/initial),
        'closed_lot_drawdown_usd':str(max_dd),'drawdown_basis':'closed lots only; not marked-to-market',
        'max_simultaneous_positions':max_open,'unresolved_positions':[
            {k:str(v) if isinstance(v,Decimal) else v for k,v in lot.items()} for lot in lots.values()],
        'fees_usd':str(total_fees),'network_usd':str(total_network),'slippage_usd':str(total_slippage),
        'closed':closed,'ledger':timeline,
        'decision_membership':[{'registry_id':key,'state':decisions[key]} for key in sorted(decisions)]}


def compare(bundle,spec):
    research=verify_bundle(bundle)
    if not research.cohort_start<spec.train_end or spec.test_end>research.cohort_end:
        raise ValueError('splits_outside_cohort')
    # All v1 features use at most 1h of holder history and max_gap of quote history.
    uses_holders=any(s.holder_top10_share_max is not None for s in spec.strategies) or any(s.kind=='matched_controls' for s in spec.studies)
    required_embargo=max(research.max_gap_seconds,3600 if uses_holders else 0)
    if spec.embargo_seconds<required_embargo:raise ValueError('embargo_shorter_than_feature_lookback')
    groups={name:[] for name in ['train','validation','test','purged','outside_period']}
    for m in bundle['members']:groups[temporal_split(m,research,spec)].append(m)
    results=[]
    # Own decimal context makes replay independent of ambient precision.
    with localcontext() as ctx:
        ctx.prec=80
        for strategy in spec.strategies:
            results.append({'strategy_id':_hash(strategy.model_dump()),'definition':strategy.model_dump(),
                'splits':{name:portfolio(groups[name],strategy,spec,research.knowledge_cutoff) for name in ['train','validation','test']}})
    result={'version':EXPERIMENT_VERSION,'dataset_sha256':_hash(bundle),'definition':spec.model_dump(mode='json'),
        'execution_mode':'quote_based_simulation','fill_evidence':False,'cost_source':'explicit scenario assumptions',
        'allocation':'independent strategy and split portfolios; exits before entries at identical timestamps',
        'arithmetic':'Decimal precision 80; exit network cost reserved at admission',
        'search_trials_in_this_comparison':len(spec.strategies),'automatic_selection':False,
        'test_role':'exploratory comparison; inspecting/reranking test consumes the holdout',
        'inference':'descriptive simulation; no independent-token, predictive or causal claim',
        'membership':{k:[m['registry_id'] for m in v] for k,v in groups.items()},'results':results,
        'studies':run_studies(bundle,research,groups,spec)}
    result['result_sha256']=_hash(result)
    return result


def main():
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Replay a frozen research bundle with explicit simulated costs')
    parser.add_argument('--bundle',required=True)
    parser.add_argument('--spec',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--verify')
    args=parser.parse_args()
    raw=Path(args.bundle).read_bytes();config=Path(args.spec).read_bytes()
    if len(raw)>MAX_DATASET_BYTES or len(config)>16384:raise ValueError('offline_input_budget')
    report=compare(json.loads(raw),ExperimentSpec.model_validate_json(config))
    body=_json(report)
    if args.verify and body!=_json(json.loads(Path(args.verify).read_bytes())):
        raise ValueError('offline_report_mismatch')
    target=Path(args.output)
    if target.exists() and target.read_bytes()!=body:raise ValueError('output_exists_with_different_content')
    target.write_bytes(body)
    print(json.dumps({'version':EXPERIMENT_VERSION,'result_sha256':report['result_sha256'],
        'strategies':len(report['results']),'reproduced':bool(args.verify),'execution_mode':report['execution_mode']}))


if __name__=='__main__':main()

import copy,json
from datetime import datetime,timedelta,timezone
from decimal import Decimal,localcontext
import pytest
from midas_data.cohorts import latest_frame,labels,holder_feature,summarize
from midas_data.experiments import Strategy,ExperimentSpec,compare,portfolio,selection,verify_bundle
from midas_data.features import _hash,_json
from midas_data.research_contracts import ResearchSpec,RESEARCH_VERSION

T=datetime(2026,9,9,tzinfo=timezone.utc)


def definitions(**changes):
    r=ResearchSpec(source_kind='fixture',cohort_start=T,cohort_end=T+timedelta(hours=6),
        knowledge_cutoff=T+timedelta(hours=7),method='gmgn.token_info.price_times_circulating_supply.v1',
        entry_delay_seconds=0,horizon_seconds=120,max_gap_seconds=60,levels_usd=['200'])
    e=ExperimentSpec(**dict(dict(strategies=[Strategy(name='baseline')],train_end=T+timedelta(hours=2),
        validation_end=T+timedelta(hours=4),test_end=T+timedelta(hours=6),embargo_seconds=60,
        fee_bps='100',slippage_bps='100',network_usd_per_side='1',notional_usd='100',
        starting_cash_usd='1000',max_positions=2),**changes))
    return r,e


def member(key=1,offset=600,exit_price='2',exit_missing=False):
    r,_=definitions();entry=T+timedelta(seconds=offset);end=entry+timedelta(seconds=120)
    def p(at,price):
        return {'capture_id':f'{key}-{at.isoformat()}','point_id':f'{key}-{at.isoformat()}',
            'observed_at':at,'available_at':at,'same_timestamp_count':1,'state':'available',
            'value_text':'100','method':r.method,
            'price':{'state':'available','value_text':price,'unit':'usd_per_token'}}
    first=p(entry,'1');last=p(end,exit_price)
    entry_series={'query_complete':True,'points':[first]}
    exit_series={'query_complete':True,'points':[] if exit_missing else [last]}
    outcomes={'query_complete':True,'points':[first,p(entry+timedelta(seconds=60),'1'),last]}
    return {'registry_id':key,'token_address':f'Token{key}','source_kind':'fixture','chain':'sol',
        'registered_at':entry,'entry_at':entry,'exit_at':end,
        'entry':latest_frame(entry_series,entry,60),'exit':latest_frame(exit_series,end,60),
        'holder':holder_feature(None,entry,3600),
        'outcome':labels(outcomes['points'],start=entry,end=end,cutoff=r.knowledge_cutoff,gap_seconds=60,levels=r.levels_usd),
        'input_points':len(entry_series['points'])+len(exit_series['points'])+len(outcomes['points']),
        'evidence':{'entry_series':entry_series,'exit_series':exit_series,'outcome_series':outcomes,'holder_snapshot':None}}


def bundle(members=None):
    r,_=definitions();members=members if members is not None else [member()]
    definition=r.definition();implementation={'fixture':'synthetic quote study, not provider evidence'}
    b={'version':RESEARCH_VERSION,'definition':definition,'implementation':implementation,
       'job_id':_hash({'definition':definition,'implementation':implementation}),'members':members,
       'summary':summarize(members,r.levels_usd)}
    return json.loads(_json(b))


def test_costs_use_price_not_market_cap_and_replay_is_decimal_deterministic():
    b=bundle();_,spec=definitions()
    first=compare(b,spec)
    with localcontext() as ctx:
        ctx.prec=2
        assert compare(b,spec)==first
    result=first['results'][0]['splits']['train'];trade=result['closed'][0]
    assert Decimal(trade['net_pnl_usd'])<100 and Decimal(trade['net_pnl_usd'])>80
    assert Decimal(result['fees_usd'])>2 and result['network_usd']=='2'
    assert Decimal(result['slippage_usd'])>0
    with localcontext() as ctx:
        ctx.prec=80
        assert Decimal(result['ending_cash_usd'])==Decimal('1000')+Decimal(trade['net_pnl_usd'])
    assert first['fill_evidence'] is False and first['automatic_selection'] is False


def test_missing_exit_keeps_capital_reserved_and_total_return_unknown():
    b=bundle([member(exit_missing=True)]);_,spec=definitions()
    result=compare(b,spec)['results'][0]['splits']['train']
    assert result['states']['opened']==1 and result['return_pct'] is None and result['total_equity_usd'] is None
    assert result['ending_cash_usd']=='899' and result['reserved_exit_network_usd']=='1'
    assert result['available_cash_usd']=='898' and len(result['unresolved_positions'])==1


def test_losses_are_retained_and_capital_limit_blocks_overlapping_entries():
    b=bundle([member(1,600,exit_price='.5'),member(2,600)])
    _,spec=definitions(starting_cash_usd='102',max_positions=2)
    result=compare(b,spec)['results'][0]['splits']['train']
    assert result['states']['closed']==1 and result['states']['blocked_capital']==1
    assert Decimal(result['closed_lot_pnl_usd'])<0 and Decimal(result['closed_lot_drawdown_usd'])>0
    assert len(result['decision_membership'])==2


def test_exit_network_cost_is_reserved_before_entry():
    _,spec=definitions(starting_cash_usd='101')
    result=compare(bundle(),spec)['results'][0]['splits']['train']
    assert result['states']['blocked_capital']==1 and result['ending_cash_usd']=='101'


def test_temporal_embargo_and_horizon_boundary_are_purged():
    b=bundle([member(1,30),member(2,7080),member(3,7200),member(4,7800)])
    _,spec=definitions();result=compare(b,spec)
    assert result['membership']['purged']==[1,2,3]
    assert result['membership']['validation']==[4]
    assert result['results'][0]['splits']['validation']['population']==1


def test_holder_unknown_is_not_false_and_strategy_nonmatches_stay_in_population():
    m=member();assert selection(m,Strategy(name='holders',holder_top10_share_max='.3'))=='unknown'
    _,spec=definitions(strategies=[Strategy(name='outside',market_cap_min='200'),Strategy(name='baseline')])
    result=compare(bundle(),spec)
    assert result['results'][0]['splits']['train']['states']['nonmatch']==1
    assert result['results'][1]['splits']['train']['states']['closed']==1


@pytest.mark.parametrize('mutation',[
    lambda b:b['members'][0]['entry']['point'].update(value_text='999'),
    lambda b:b['summary'].update(population=999),
    lambda b:b['members'].append(copy.deepcopy(b['members'][0])),
    lambda b:b['members'][0].update(entry_at=(T+timedelta(days=2)).isoformat()),
    lambda b:b['members'][0]['evidence']['entry_series']['points'][0].update(
        available_at=(T+timedelta(days=2)).isoformat())
])
def test_modified_or_future_evidence_fails_reproduction(mutation):
    b=bundle();mutation(b)
    with pytest.raises(ValueError):verify_bundle(b)


@pytest.mark.parametrize('change',[
    {'fee_bps':'10000'},{'slippage_bps':'-1'},{'notional_usd':'NaN'},
    {'network_usd_per_side':0},{'max_positions':True},
    {'test_end':T},{'embargo_seconds':-1}
])
def test_invalid_execution_cost_or_temporal_contract_rejected(change):
    with pytest.raises(ValueError):definitions(**change)

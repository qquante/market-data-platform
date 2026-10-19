"""Shared simulated cash and cost exposure; deterministic monetary examples."""
from decimal import Decimal,Inexact,ROUND_UP,localcontext
import pytest
from pydantic import ValidationError
from midas_data.paper_account import AccountPolicy,initial_state,account_change,balances,position_definition
from midas_data.paper_model import buy,sell,CASH_CONTEXT
from midas_data.feature_windows import WindowUnavailable
from midas_data.trajectory import TrajectoryLimit
from test_paper_model import policy as execution_policy

def policy(**changes):
    return AccountPolicy(**dict(dict(initial_cash_usd='200',max_position_usd='150',
        max_exposure_usd='180',max_open_positions=2),**changes))

def test_reserved_cash_opens_with_exact_residual_and_closes_with_net_proceeds():
    p=policy();e=execution_policy(budget_usd='102.01',fee_bps=100,slippage_bps=100)
    state,delta=account_change(p,initial_state(p),'requested',e.budget_usd,{})
    assert balances(p,state)==dict(available_usd='97.99',committed_usd='102.01')
    position=dict(entry=buy(e,'1'))
    state,_=account_change(p,state,'opened',e.budget_usd,position)
    assert Decimal(state['cash_usd'])==Decimal('97.99') and Decimal(state['reserved_usd'])==0
    assert Decimal(state['invested_cost_usd'])==Decimal('102.01')
    position['exit']=sell(e,position['entry'],'2')
    state,delta=account_change(p,state,'closed',e.budget_usd,position)
    assert Decimal(state['cash_usd'])==Decimal('294.01')
    assert Decimal(state['realized_pnl_usd'])==Decimal('94.01')
    assert Decimal(state['invested_cost_usd'])==0 and state['active_positions']==0
    assert Decimal(delta['cash_usd'])==Decimal('196.02')

def test_fractional_residual_is_released_without_recrediting_it_on_exit():
    p=policy();e=execution_policy(fee_bps=123,slippage_bps=456)
    state,_=account_change(p,initial_state(p),'requested','100',{})
    entry=buy(e,'0.000000123456789123456789123456789')
    exit=sell(e,entry,'0.000000023456789123456789123456789')
    with localcontext() as ctx:
        ctx.prec=2;ctx.rounding=ROUND_UP;ctx.traps[Inexact]=True
        opened,_=account_change(p,state,'opened','100',dict(entry=entry))
        closed,_=account_change(p,opened,'closed','100',dict(entry=entry,exit=exit))
    with localcontext(CASH_CONTEXT):
        assert Decimal(opened['cash_usd'])==100+Decimal(entry['unspent_usd'])
        assert Decimal(closed['cash_usd'])==200+Decimal(exit['pnl_usd'])
        assert Decimal(closed['realized_pnl_usd'])<0

def test_expiry_releases_full_reservation_without_changing_cash_or_pnl():
    p=policy();start=initial_state(p)
    state,_=account_change(p,start,'requested','120',{})
    final,delta=account_change(p,state,'entry_expired','120',{})
    assert final==start
    assert delta['reserved_usd']=='-120' and delta['cash_usd']=='0'

@pytest.mark.parametrize('changes,budgets,reason',[
    ({},['151'],'amount limit'),
    (dict(initial_cash_usd='100'),['70','40'],'available paper cash'),
    ({},['100','81'],'committed exposure'),
    (dict(max_open_positions=1),['10','10'],'count limit')])
def test_admission_limits_do_not_mutate_previous_state(changes,budgets,reason):
    p=policy(**changes);state=initial_state(p)
    for budget in budgets[:-1]:state,_=account_change(p,state,'requested',budget,{})
    before=dict(state)
    with pytest.raises(TrajectoryLimit,match=reason):account_change(p,state,'requested',budgets[-1],{})
    assert state==before

def test_open_entry_cost_still_consumes_exposure_after_reservation_is_released():
    p=policy();e=execution_policy(budget_usd='120')
    s,_=account_change(p,initial_state(p),'requested','120',{})
    s,_=account_change(p,s,'opened','120',dict(entry=buy(e,'2')))
    with pytest.raises(TrajectoryLimit,match='committed exposure'):
        account_change(p,s,'requested','70',{})

def test_lost_money_is_not_minted_again_by_next_admission():
    p=policy(initial_cash_usd='100');e=execution_policy()
    s,_=account_change(p,initial_state(p),'requested','100',{})
    position=dict(entry=buy(e,'2'))
    s,_=account_change(p,s,'opened','100',position)
    position['exit']=sell(e,position['entry'],'1')
    s,_=account_change(p,s,'closed','100',position)
    with pytest.raises(TrajectoryLimit,match='available paper cash'):
        account_change(p,s,'requested','51',{})
    assert Decimal(s['cash_usd'])==50

def test_account_identity_is_canonical_and_distinct_from_legacy_execution():
    p=policy();assert policy(initial_cash_usd='200.00').key()==p.key()
    assert policy(max_open_positions=3).key()!=p.key()
    e=execution_policy()
    assert position_definition(e,None)==e.definition()
    assert position_definition(e,p)['account_policy_id']==p.key()

@pytest.mark.parametrize('changes',[
    dict(initial_cash_usd='0'),dict(initial_cash_usd='1e3'),dict(initial_cash_usd=100),
    dict(max_position_usd='0.001'),dict(max_position_usd='181'),dict(max_exposure_usd='-1'),
    dict(max_open_positions=0),dict(max_open_positions=5),dict(max_open_positions=True),
    dict(version='future'),dict(extra='ignored')])
def test_invalid_account_configuration_is_rejected(changes):
    with pytest.raises(ValidationError):policy(**changes)

def test_inconsistent_cash_is_not_served_as_an_usable_balance():
    p=policy();s=initial_state(p);s['cash_usd']='201'
    with pytest.raises(WindowUnavailable):balances(p,s)

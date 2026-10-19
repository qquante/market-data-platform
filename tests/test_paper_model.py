"""Paper arithmetic and forward eligibility on explicit UTC synthetic samples."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import pytest
from pydantic import ValidationError
from midas_data.paper_model import PaperPolicy,buy,sell,quote,transition,CASH_CONTEXT
from test_features import window
from test_trajectory import START

def policy(**changes):
    value=dict(budget_usd='100',fee_bps=0,slippage_bps=0,entry_latency_seconds=0,
        entry_timeout_seconds=30,hold_seconds=5,max_price_age_seconds=30,max_signal_age_seconds=120)
    value.update(changes)
    return PaperPolicy(**value)

def at(seconds):return START+timedelta(seconds=seconds)

def snapshot(observed=10,price='2',now=11):
    series=window(('1000',),times=[observed],end=now,gap=120)
    point=series['points'][-1]
    point['available_at']=at(observed+1)
    point['price']=dict(state='available',value_text=price,unit='usd_per_token')
    return dict(next_due_at=at(now+60),publication_cutoff=at(now),generation=1,result=series)

def pending():
    return dict(status='entry_pending',reason='awaiting_new_price',requested_at=at(5).isoformat(),
        entry_not_before=at(5).isoformat(),entry_deadline=at(35).isoformat(),next_due_at=at(35).isoformat())

def test_round_trip_uses_price_not_cap_and_conserves_cash():
    p=policy(budget_usd='102.01',fee_bps=100,slippage_bps=100)
    entry=buy(p,'1');exit=sell(p,entry,'2')
    assert Decimal(entry['quantity'])==100
    assert Decimal(entry['fill_price'])==Decimal('1.01')
    assert Decimal(entry['cost_usd'])==Decimal('102.01') and Decimal(entry['unspent_usd'])==0
    assert Decimal(exit['fill_price'])==Decimal('1.98')
    assert Decimal(exit['proceeds_usd'])==Decimal('196.02') and Decimal(exit['pnl_usd'])==Decimal('94.01')

def test_fractional_quantity_rounds_down_without_overspending_or_context_dependence():
    p=policy(fee_bps=123,slippage_bps=456)
    baseline=buy(p,'0.000000123456789123456789123456789')
    with localcontext() as ctx:
        ctx.prec=2;ctx.rounding=ROUND_UP;ctx.traps[Inexact]=True
        assert buy(p,'0.000000123456789123456789123456789')==baseline
        closed=sell(p,baseline,'0.000000234567891234567891234567891')
    with localcontext(CASH_CONTEXT):
        assert Decimal(baseline['cost_usd'])<=100
        assert Decimal(baseline['cost_usd'])+Decimal(baseline['unspent_usd'])==100
        assert Decimal(closed['pnl_usd'])==Decimal(closed['proceeds_usd'])-Decimal(baseline['cost_usd'])

def test_forward_entry_then_timed_exit_require_separate_new_samples():
    p=policy();state=pending()
    state,event=transition(p,state,snapshot(observed=0,now=11),at(11))
    assert state['status']=='entry_pending' and event['reason']=='awaiting_new_price'
    state,event=transition(p,state,snapshot(),at(11))
    assert state['status']=='open' and Decimal(state['entry']['quantity'])==50
    assert event['cash_delta_usd']=='-100' and state['exit_due_at']==at(16).isoformat()
    state,event=transition(p,state,snapshot(observed=14,price='99',now=15),at(15))
    assert event is None  # No take-profit assumption is inferred from an intermediate observation.
    state,event=transition(p,state,snapshot(observed=14,price='99',now=20),at(20))
    assert state['status']=='open' and event['reason']=='awaiting_new_price'
    state,event=transition(p,state,snapshot(observed=20,price='3',now=21),at(21))
    assert state['status']=='closed' and Decimal(state['exit']['pnl_usd'])==50
    assert event['cash_delta_usd']=='150'
    assert transition(p,state,snapshot(observed=40,price='9',now=41),at(41))==(state,None)

def test_entry_expiry_never_manufactures_a_fill_even_when_a_late_quote_exists():
    state,event=transition(policy(),pending(),snapshot(observed=35,now=36),at(36))
    assert state['status']=='entry_expired' and event['cash_delta_usd']=='0'
    assert 'entry' not in state
    assert transition(policy(),state,snapshot(observed=40,now=41),at(41))==(state,None)

@pytest.mark.parametrize('value',['0','-1','NaN','Infinity','1e101','1e-101','1'*101,'not-a-number'])
def test_invalid_prices_never_use_market_cap_as_fallback(value):
    value,reason=quote(snapshot(price=value),policy(),at(11),at(5))
    assert value is None and reason=='price_numeric_budget'

@pytest.mark.parametrize('mutation,reason',[
    ('missing','price_unavailable'),('unit','price_unavailable'),('ambiguous','latest_observation_unavailable'),
    ('invalid','latest_observation_unavailable'),('future_known','price_not_known'),
    ('future_received','price_not_known'),('cutoff','price_not_known'),('deadline','frame_expired')])
def test_price_quality_and_knowledge_gates(mutation,reason):
    frame=snapshot();point=frame['result']['points'][-1]
    if mutation=='missing':point['price']=None
    elif mutation=='unit':point['price']['unit']='usd'
    elif mutation=='ambiguous':point['same_timestamp_count']=2
    elif mutation=='invalid':point['state']='unavailable'
    elif mutation=='future_known':point['available_at']=at(12)
    elif mutation=='future_received':point['observed_at']=at(12)
    elif mutation=='cutoff':frame['publication_cutoff']=at(10)
    elif mutation=='deadline':frame['next_due_at']=at(11)
    value,why=quote(frame,policy(),at(11),at(5))
    assert value is None and why==reason

def test_missing_and_stale_exit_remains_open_with_no_cashflow():
    state,_=transition(policy(),pending(),snapshot(),at(11))
    state,event=transition(policy(),state,None,at(20))
    assert state['status']=='open' and event['cash_delta_usd']=='0' and 'exit' not in state
    value,reason=quote(snapshot(observed=20,now=100),policy(),at(100),at(16))
    assert value is None and reason=='price_stale'

def test_loose_price_age_does_not_bypass_source_window_gap():
    value,reason=quote(snapshot(observed=20,now=141),policy(max_price_age_seconds=86400),at(141),at(5))
    assert value is None and reason=='price_stale'

def test_policy_identity_includes_costs_latency_and_exit():
    p=policy();wire=p.model_dump();wire['budget_usd']='100.00'
    assert PaperPolicy(**wire).key()==p.key()
    for field,value in [('fee_bps',1),('slippage_bps',1),('hold_seconds',6),
                        ('entry_latency_seconds',1),('entry_timeout_seconds',31),
                        ('max_price_age_seconds',31),('max_signal_age_seconds',121)]:
        modified=p.model_dump();modified[field]=value
        assert PaperPolicy(**modified).key()!=p.key()

@pytest.mark.parametrize('field,value',[
    ('budget_usd','0'),('budget_usd','1e2'),('budget_usd','1.001'),('budget_usd',100),
    ('fee_bps',-1),('slippage_bps',5001),('hold_seconds',True),('entry_latency_seconds',30),
    ('version','future'),('extra','ignored')])
def test_invalid_policy_is_rejected(field,value):
    body=policy().model_dump();body[field]=value
    with pytest.raises(ValidationError):PaperPolicy(**body)

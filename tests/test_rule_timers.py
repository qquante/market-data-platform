"""Temporal policy tests use explicit UTC clocks and synthetic observations."""
from datetime import timedelta
from copy import deepcopy
import pytest
from pydantic import ValidationError
from midas_data.rule_timers import ActiveRule, advance
from test_rules import definition
from test_features import window
from test_trajectory import START

TRUE=dict(state='true',reason='comparison_matched')
FALSE=dict(state='false',reason='comparison_not_matched')
UNKNOWN=dict(state='unknown',reason='window_not_ready')


def config(debounce=0,cooldown=0):
    body=definition();body['threshold']='100'
    return ActiveRule(comparison=body,debounce_seconds=debounce,cooldown_seconds=cooldown)


def snapshot(times=(0,),values=None,*,gap=120,end=180):
    return dict(result=window(values or tuple('120' for _ in times),times=list(times),end=end,gap=gap))


def at(seconds):
    return START+timedelta(seconds=seconds)


def test_zero_debounce_signal_latch_false_rearm_and_cooldown_timer():
    rule=config(cooldown=10);frame=snapshot()
    state,decision=advance(rule,{},TRUE,frame,at(1))
    assert decision['disposition']=='signal' and decision['eligible']
    state,decision=advance(rule,state,TRUE,frame,at(2))
    assert decision['reason']=='already_signaled'
    state,decision=advance(rule,state,UNKNOWN,None,at(3))
    assert not state['true_since'] and state['latched'] and decision['reason']=='data_unknown'
    state,decision=advance(rule,state,TRUE,frame,at(4),reset=True)
    assert decision['reason']=='already_signaled'
    state,decision=advance(rule,state,FALSE,frame,at(5))
    assert not state['latched'] and not state['true_since']
    state,decision=advance(rule,state,TRUE,frame,at(6))
    assert decision['reason']=='cooldown' and state['next_due_at']==at(11).isoformat()
    state,decision=advance(rule,state,TRUE,frame,at(11))
    assert decision['disposition']=='signal' and state['last_signal_at']==at(11).isoformat()


def test_silence_does_not_count_as_debounce_coverage_but_new_samples_do():
    rule=config(debounce=10)
    state,decision=advance(rule,{},TRUE,snapshot(),at(5))
    assert decision['reason']=='debouncing' and state['next_due_at']==at(15).isoformat()
    state,decision=advance(rule,state,TRUE,snapshot(),at(15))
    assert decision['reason']=='awaiting_observation_coverage' and state['next_due_at'] is None
    state,decision=advance(rule,state,TRUE,snapshot((0,16)),at(17))
    assert decision['disposition']=='signal' and state['observed_through']==at(16).isoformat()


@pytest.mark.parametrize('values,times,gap,reason',[
    (('120','90','120'),(0,10,20),120,'condition_interrupted'),
    (('120',None,'120'),(0,10,20),120,'coverage_reset'),
    (('120','120','120'),(0,10,10),120,'coverage_reset'),
    (('120','120'),(0,20),15,'coverage_reset')])
def test_coalesced_false_invalid_ambiguity_and_gap_cannot_confirm(values,times,gap,reason):
    rule=config(debounce=10)
    # Make the policy's tolerated gap equal the sampled-series gap.
    body=rule.wire();body['comparison']['window']['gap_after_seconds']=gap
    rule=ActiveRule.model_validate(body)
    state,_=advance(rule,{},TRUE,snapshot(),at(5))
    state,decision=advance(rule,state,TRUE,snapshot(times,values,gap=gap),at(21))
    assert decision['reason']==reason and decision['disposition']=='pending'
    assert state['true_since']==at(21).isoformat() and not state['latched']


def test_recovery_resets_unconfirmed_duration_and_keeps_emission_latch():
    rule=config(debounce=10)
    state,_=advance(rule,{},TRUE,snapshot(),at(5))
    state,decision=advance(rule,state,TRUE,snapshot((0,20)),at(21),reset=True)
    assert decision['reason']=='debouncing' and state['true_since']==at(21).isoformat()
    state,_=advance(rule,state,TRUE,snapshot((0,20,32)),at(33))
    assert state['latched']
    state,decision=advance(rule,state,TRUE,snapshot((0,20,32)),at(34),reset=True)
    assert state['latched'] and decision['reason']=='already_signaled'


def test_unknown_cancels_duration_and_lost_left_boundary_restarts_it():
    rule=config(debounce=10)
    state,_=advance(rule,{},TRUE,snapshot(),at(5))
    state,_=advance(rule,state,UNKNOWN,None,at(7))
    assert state['true_since'] is None
    state,decision=advance(rule,state,TRUE,snapshot((0,16)),at(17))
    assert decision['reason']=='debouncing'
    source=snapshot((0,16,30));source['result']['start']=at(18)
    state,decision=advance(rule,state,TRUE,source,at(31))
    assert decision['reason']=='coverage_reset'


def test_temporal_settings_and_canonical_inputs_change_identity():
    a=config(10,20);b=a.wire()
    b['comparison']['threshold']='100.000'
    assert ActiveRule.model_validate(b).key()==a.key()
    for field,value in [('debounce_seconds',11),('cooldown_seconds',21)]:
        other=deepcopy(b);other[field]=value
        assert ActiveRule.model_validate(other).key()!=a.key()


@pytest.mark.parametrize('field,value',[
    ('debounce_seconds',-1),('debounce_seconds',180),('debounce_seconds',True),
    ('cooldown_seconds','2'),('cooldown_seconds',86401),('version','future'),
    ('emission_policy','every_poll'),('extra',1)])
def test_invalid_active_definitions_are_rejected(field,value):
    body=config().wire();body[field]=value
    with pytest.raises(ValidationError):ActiveRule.model_validate(body)


def test_active_v1_rejects_unimplemented_window_statistic_debounce():
    body=config().wire();body['comparison']['feature']='market_cap_max_observed'
    with pytest.raises(ValidationError):ActiveRule.model_validate(body)

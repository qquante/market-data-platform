"""Small temporal policy for latest-market-cap predicates on observed samples."""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .features import _hash
from .rules import ComparisonRule, OPERATORS

ACTIVE_VERSION = 'gmgn-active-latest-rule-v1'


def instant(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


class ActiveRule(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal['gmgn-active-latest-rule-v1'] = ACTIVE_VERSION
    comparison: ComparisonRule
    debounce_seconds: int = Field(ge=0, le=86400)
    cooldown_seconds: int = Field(ge=0, le=86400)
    emission_policy: Literal['once_until_observed_false_v1'] = 'once_until_observed_false_v1'

    @model_validator(mode='after')
    def supported_scope(self):
        if self.comparison.feature != 'market_cap_latest':
            raise ValueError('Active v1 supports market_cap_latest only')
        if self.debounce_seconds >= self.comparison.window.window_seconds:
            raise ValueError('Debounce must be shorter than the retained window')
        return self

    def definition(self):
        return dict(version=self.version, comparison=self.comparison.definition(),
            debounce_seconds=self.debounce_seconds, cooldown_seconds=self.cooldown_seconds,
            emission_policy=self.emission_policy,
            continuity_policy='observed_samples_cover_debounce_v1; reset_on_restart_or_gap')

    def wire(self):
        value=self.model_dump()
        cfg=self.comparison.window.canonical()
        value['comparison']['window']={k:v for k,v in cfg.items() if k!='version'}
        return value

    def key(self):
        return _hash(self.definition())


def coverage(rule, snapshot, since):
    series=snapshot['result']
    if instant(series['start'])>since:
        return 'coverage_reset', None
    points=series['points']
    before=[p for p in points if instant(p['observed_at'])<=since]
    if not before:
        return 'coverage_reset', None
    # Keep every tied sample at the boundary, never choose an ambiguous one.
    anchor=instant(before[-1]['observed_at'])
    samples=[p for p in points if instant(p['observed_at'])>=anchor]
    gap=rule.comparison.window.canonical()['gap_after_seconds']
    if (since-anchor).total_seconds()>gap or any(
            p['state']!='available' or p['same_timestamp_count']>1 for p in samples):
        return 'coverage_reset', None
    if any((instant(b['observed_at'])-instant(a['observed_at'])).total_seconds()>gap
           for a,b in zip(samples,samples[1:])):
        return 'coverage_reset', None
    compare=OPERATORS[rule.comparison.operator]
    threshold=Decimal(rule.comparison.threshold)
    if any(not compare(Decimal(p['value_text']),threshold) for p in samples):
        return 'condition_interrupted', None
    return None, instant(samples[-1]['observed_at'])


def advance(rule, previous, condition, snapshot, now, *, reset=False):
    """Unknown clears duration but never rearms a signal; only observed false rearms it."""
    state=dict(raw_state=condition['state'], true_since=previous.get('true_since'),
        latched=previous.get('latched',False), last_signal_at=previous.get('last_signal_at'),
        next_due_at=None, observed_through=None)
    decision=dict(raw_state=condition['state'], condition_reason=condition['reason'],
        stabilized=False, eligible=False, disposition='abstain', reason=None)
    if reset:
        state['true_since']=None
    if condition['state']!='true':
        state['true_since']=None
        if condition['state']=='false':
            state['latched']=False
            decision['reason']='condition_false'
        else:
            decision['reason']='data_unknown'
        return state,decision
    if state['true_since'] is None:
        state['true_since']=now.isoformat()
    since=instant(state['true_since'])
    if state['latched']:
        decision.update(disposition='suppressed',reason='already_signaled')
        return state,decision
    target=since+timedelta(seconds=rule.debounce_seconds)
    if rule.debounce_seconds:
        reason,through=coverage(rule,snapshot,since)
        if reason:
            state['true_since']=now.isoformat()
            state['next_due_at']=(now+timedelta(seconds=rule.debounce_seconds)).isoformat()
            decision.update(disposition='pending',reason=reason)
            return state,decision
        state['observed_through']=through.isoformat()
        if now<target or through<target:
            if now<target:
                state['next_due_at']=target.isoformat()
            decision.update(disposition='pending',
                            reason='debouncing' if now<target else 'awaiting_observation_coverage')
            return state,decision
    decision['stabilized']=True
    last=instant(state['last_signal_at'])
    cooldown=last+timedelta(seconds=rule.cooldown_seconds) if last else now
    if now<cooldown:
        state['next_due_at']=cooldown.isoformat()
        decision.update(disposition='suppressed',reason='cooldown')
        return state,decision
    state.update(latched=True,last_signal_at=now.isoformat())
    decision.update(eligible=True,disposition='signal',reason='condition_confirmed')
    return state,decision

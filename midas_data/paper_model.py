"""Versioned forward-only paper lot: observed price proxy, explicit scenario costs."""
from datetime import datetime, timedelta
from decimal import Context, Decimal, DivisionByZero, Inexact, InvalidOperation, Overflow, ROUND_DOWN, ROUND_HALF_EVEN, localcontext
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .features import _hash
from .rule_timers import instant
from .valuation import VALUATION_VERSION
from .trajectory import TRAJECTORY_VERSION
from .paper_prices import TRACKED_PAPER_VERSION,PRICE_VERSION,FRAME_VERSION,source_definition

PAPER_VERSION='gmgn-paper-timed-lot-v1'
CASH_CONTEXT=Context(prec=512, rounding=ROUND_HALF_EVEN, Emin=-9999, Emax=9999,
                     traps=[Inexact, InvalidOperation, DivisionByZero, Overflow])


class PaperPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version: Literal['gmgn-paper-timed-lot-v1','gmgn-paper-timed-lot-v2']=PAPER_VERSION
    budget_usd: str=Field(pattern=r'^(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?$')
    fee_bps: int=Field(ge=0,le=5000)
    slippage_bps: int=Field(ge=0,le=5000)
    entry_latency_seconds: int=Field(ge=0,le=300)
    entry_timeout_seconds: int=Field(ge=1,le=3600)
    hold_seconds: int=Field(ge=1,le=86400)
    max_price_age_seconds: int=Field(ge=1,le=86400)
    max_signal_age_seconds: int=Field(ge=1,le=3600)

    @field_validator('budget_usd')
    @classmethod
    def positive_cash(cls,value):
        if Decimal(value)<=0:
            raise ValueError('Paper budget must be positive')
        return value.rstrip('0').rstrip('.') if '.' in value else value

    @model_validator(mode='after')
    def temporal_order(self):
        if self.entry_latency_seconds>=self.entry_timeout_seconds:
            raise ValueError('Entry latency must be shorter than entry timeout')
        if self.version==TRACKED_PAPER_VERSION and self.entry_timeout_seconds-self.entry_latency_seconds<720:
            raise ValueError('Tracked paper requires at least 720 seconds after entry latency; collection is not instant')
        return self

    def definition(self):
        result=dict(self.model_dump(),valuation_version=VALUATION_VERSION,trajectory_version=TRAJECTORY_VERSION,price_model='fresh_received_sample_after_action_deadline',
            fill_model='full_fractional_proxy; no executable liquidity claim',
            quantity_precision='34 significant digits ROUND_DOWN; exact cash arithmetic up to 512 digits',
            fees='configured bps of gross value on both sides; no inferred gas or provider fee',
            cash_scope='isolated lot budget; no shared portfolio',
            entry_policy='consume signal once; await post-request quote without rechecking the predicate',
            exit_policy='first consumed fresh sample received at or after hold deadline')
        if self.version==TRACKED_PAPER_VERSION:result['price_source']=source_definition()
        return result

    def key(self):
        return _hash(self.definition())


class PaperRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    rule_id: str=Field(pattern=r'^[0-9a-f]{64}$')
    signal_sequence: int=Field(ge=1,le=9223372036854775807)
    policy: PaperPolicy


def requested_state(policy,now,signal_event_id,identity):
    state=dict(status='entry_pending',reason='awaiting_new_price',requested_at=now.isoformat(),
        entry_not_before=(now+timedelta(seconds=policy.entry_latency_seconds)).isoformat(),
        entry_deadline=(now+timedelta(seconds=policy.entry_timeout_seconds)).isoformat(),
        next_due_at=(now+timedelta(seconds=policy.entry_timeout_seconds)).isoformat(),
        signal_event_id=signal_event_id,budget_usd=policy.budget_usd,execution='simulated')
    if policy.version==TRACKED_PAPER_VERSION:state['price_identity']=identity
    return state


def quote(snapshot,policy,now,not_before,*,identity=None):
    if snapshot is None:
        return None,'price_frame_unavailable' if identity is not None else 'window_unavailable'
    if identity is not None:
        series=snapshot.get('result',{})
        if (snapshot.get('version')!=FRAME_VERSION or snapshot.get('source_policy_id')!=_hash(source_definition())
                or series.get('version')!=PRICE_VERSION or series.get('endpoint')!='token_info'
                or any(series.get(k)!=v for k,v in identity.items())):
            return None,'price_source_mismatch'
    if instant(snapshot['next_due_at'])<=now:
        return None,'frame_expired'
    series=snapshot['result'];points=series['points']
    if not points:return None,'no_observations'
    point=points[-1]
    if point['state']!='available' or point['same_timestamp_count']!=1:
        return None,'latest_observation_unavailable'
    observed,available=instant(point['observed_at']),instant(point['available_at'])
    if observed<not_before:return None,'awaiting_new_price'
    if observed>now or available>now or available>instant(snapshot['publication_cutoff']):
        return None,'price_not_known'
    if (now-observed).total_seconds()>min(policy.max_price_age_seconds,series['coverage']['gap_after_seconds']):
        return None,'price_stale'
    price=point.get('price') or {}
    if price.get('state')!='available' or price.get('unit')!='usd_per_token':
        return None,'price_unavailable'
    try:
        value=Decimal(price['value_text'])
    except (InvalidOperation,TypeError,ValueError):
        return None,'price_numeric_budget'
    if not value.is_finite() or value<=0 or len(value.as_tuple().digits)>100 or abs(value.adjusted())>100:
        return None,'price_numeric_budget'
    result=dict(value_text=price['value_text'],unit='usd_per_token',point_id=point['point_id'],
        observed_at=observed.isoformat(),available_at=available.isoformat(),
        raw_sha256=point['raw_sha256'],normalizer_version=point['normalizer_version'],
        interpretation_sha256=point['interpretation_sha256'],item_pointer=point['item_pointer'],
        anchor_fact_id=point['anchor_fact_id'],
        price_pointer=point['item_pointer']+('/price/price' if series['endpoint']=='token_info' else '/price'),
        frame_generation=snapshot['generation'],publication_cutoff=snapshot['publication_cutoff'],
        provenance='price field of this archived capture/item/interpretation; not market cap')
    if identity is not None:result.update(**identity,endpoint='token_info',source_policy_id=_hash(source_definition()))
    return result,None


def buy(policy,reference):
    with localcontext(CASH_CONTEXT) as ctx:
        budget=Decimal(policy.budget_usd)
        fee=Decimal(policy.fee_bps)/10000
        price=Decimal(reference)*(1+Decimal(policy.slippage_bps)/10000)
        denominator=price*(1+fee)
        with localcontext(ctx) as qctx:
            qctx.prec=34;qctx.rounding=ROUND_DOWN;qctx.traps[Inexact]=False
            quantity=budget/denominator
        gross=quantity*price;fees=gross*fee;cost=gross+fees
        assert cost<=budget
        return {k:str(v) for k,v in dict(quantity=quantity,fill_price=price,gross_usd=gross,
            fee_usd=fees,cost_usd=cost,unspent_usd=budget-cost).items()}


def sell(policy,entry,reference):
    with localcontext(CASH_CONTEXT):
        price=Decimal(reference)*(1-Decimal(policy.slippage_bps)/10000)
        gross=Decimal(entry['quantity'])*price
        fees=gross*Decimal(policy.fee_bps)/10000
        proceeds=gross-fees
        final=Decimal(entry['unspent_usd'])+proceeds
        return {k:str(v) for k,v in dict(fill_price=price,gross_usd=gross,fee_usd=fees,
            proceeds_usd=proceeds,final_cash_usd=final,pnl_usd=final-Decimal(policy.budget_usd)).items()}


def transition(policy,previous,snapshot,now):
    state=dict(previous)
    if state['status'] not in ('entry_pending','open'):
        return state,None
    if state['status']=='entry_pending':
        if now>=instant(state['entry_deadline']):
            state.update(status='entry_expired',reason='entry_timeout',next_due_at=None)
            return state,dict(kind='entry_expired',reason='entry_timeout',cash_delta_usd='0')
        not_before=instant(state['entry_not_before'])
    else:
        not_before=instant(state['exit_due_at'])
        if now<not_before:
            return state,None
    amendment=state.get('price_source_change')
    identity=state.get('price_identity') if policy.version==TRACKED_PAPER_VERSION else None
    if amendment:
        not_before=max(not_before,instant(amendment['effective_after']))
        identity=amendment['identity']
    if policy.version==TRACKED_PAPER_VERSION and identity is None:
        return state,dict(kind='waiting',reason='price_identity_unavailable',cash_delta_usd='0')
    value,reason=quote(snapshot,policy,now,not_before,identity=identity)
    if reason:
        state.update(reason=reason,next_due_at=state['entry_deadline'] if state['status']=='entry_pending' else None)
        return state,dict(kind='waiting',reason=reason,cash_delta_usd='0')
    if state['status']=='entry_pending':
        fill=buy(policy,value['value_text'])
        exit_due=now+timedelta(seconds=policy.hold_seconds)
        state.update(status='open',reason='simulated_entry',entry=fill,entry_quote=value,
            opened_at=now.isoformat(),exit_due_at=exit_due.isoformat(),next_due_at=exit_due.isoformat())
        return state,dict(kind='opened',reason='simulated_entry',quote=value,fill=fill,cash_delta_usd='-'+fill['cost_usd'])
    fill=sell(policy,state['entry'],value['value_text'])
    state.update(status='closed',reason='simulated_timed_exit',exit=fill,exit_quote=value,
                 closed_at=now.isoformat(),next_due_at=None)
    return state,dict(kind='closed',reason='simulated_timed_exit',quote=value,fill=fill,cash_delta_usd=fill['proceeds_usd'])

"""Durable automatic paper admission on real PostgreSQL, synthetic prices only."""
import asyncio,json,os
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from midas_data.paper_bindings import BindingRequest
from midas_data.features import _hash
from test_paper_model import policy as execution_policy
from test_paper_account import policy as account_policy
from test_paper_account_integration import fixture

integration=pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')


def binding_config(rule_id,**changes):
    return dict(rule_id=rule_id,policy=execution_policy(budget_usd='60',hold_seconds=1,entry_timeout_seconds=20,**changes).model_dump())


def test_binding_identity_versions_delivery_and_account_limits():
    p=BindingRequest(**binding_config('a'*64));a=account_policy()
    assert p.definition(a)['start_policy'].startswith('strictly after')
    assert _hash(p.definition(a))!=_hash(p.definition(account_policy(max_open_positions=3)))
    assert _hash(p.definition(a))!=_hash(BindingRequest(**dict(p.model_dump(),rule_id='b'*64)).definition(a))


@pytest.mark.parametrize('change',[dict(rule_id='x'),dict(version='future'),dict(extra=True)])
def test_binding_rejects_ambiguous_registration(change):
    value=binding_config('a'*64);value.update(change)
    with pytest.raises(ValidationError):BindingRequest(**value)


async def initialize(f,cash='100'):
    r=await f['client'].post('/v1/paper/account',json=dict(initial_cash_usd=cash,max_position_usd='60',
        max_exposure_usd='100',max_open_positions=2));assert r.status_code==202,r.text


async def rule(f,threshold='150',cooldown=0):
    cfg=next(iter(f['current'][0].windows.values()))['config']
    window={k:v for k,v in cfg.items() if k!='version'}
    config=dict(comparison=dict(window=window,feature='market_cap_latest',operator='gte',threshold=threshold,unit='usd'),
        debounce_seconds=0,cooldown_seconds=cooldown)
    r=await f['client'].post('/v1/rules/active',json=config);assert r.status_code==202,r.text
    return r.json()['rule_id']


async def bind(f,rid,policy=None):
    body=policy or binding_config(rid)
    r=await f['client'].post('/v1/paper/bindings',json=body);assert r.status_code==202,r.text
    return body,r.json()


async def quote_step(f,value):
    await f['capture'](value);await f['current'][0].step()


async def wait_empty(m):
    async with asyncio.timeout(10):
        while m.paper.entries:await asyncio.sleep(.05)


@integration
def test_future_only_registration_idempotency_and_pause_keeps_open_exit_running(tmp_path,monkeypatch):
    async def run():
        async with fixture(tmp_path) as f:
            c,db,m=f['client'],f['pool'],f['manager'];rid=await rule(f)
            assert (await c.post('/v1/paper/bindings',json=binding_config(rid))).status_code==409
            await initialize(f)
            await m.step();old_signal=m.rules.entries[rid]['sequence']
            replies=await asyncio.gather(*[c.post('/v1/paper/bindings',json=binding_config(rid)) for _ in range(20)])
            assert all(r.status_code==202 for r in replies) and sum(r.json()['created'] for r in replies)==1
            assert all(r.json()['cursor']==old_signal for r in replies)
            for _ in range(3):await m.step()
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==0
            assert (await c.post('/v1/paper/bindings',json=dict(binding_config(rid),policy=execution_policy(budget_usd='20').model_dump()))).status_code==409
            assert (await c.post('/v1/paper/bindings',content=b'x'*8193)).status_code==413
            assert (await c.get('/v1/paper/bindings',headers={'authorization':'bad'})).status_code==401
            assert (await c.post('/v1/paper/bindings/'+'0'*64+'/pause')).status_code==404
            assert (await c.post('/v1/paper/bindings',json=binding_config('0'*64))).status_code==422
            await quote_step(f,'1');await quote_step(f,'2')
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==1
            key=next(iter(m.paper.entries));assert m.paper.entries[key]['state']['status']=='entry_pending'
            await quote_step(f,'2');assert m.paper.entries[key]['state']['status']=='open'
            assert (await c.post('/v1/paper/bindings/'+rid+'/pause')).status_code==204
            revision=m.paper.bindings.entries[rid]['revision']
            assert (await c.post('/v1/paper/bindings/'+rid+'/pause')).status_code==204
            assert m.paper.bindings.entries[rid]['revision']==revision
            reads=m.reads
            replies=await asyncio.gather(*[c.get('/v1/paper/bindings') for _ in range(20)])
            assert all(r.status_code==200 for r in replies) and m.reads==reads
            import midas_data.feature_windows as fw
            monkeypatch.setattr(fw,'POLL_SECONDS',.05);m.start();await asyncio.sleep(1.1);await f['capture']('3')
            await wait_empty(m)
            account=(await c.get('/v1/paper/account')).json()
            assert Decimal(account['state']['cash_usd'])==130
            assert await db.fetchval("SELECT count(*) FROM paper_binding_events WHERE kind='admitted'")==1
            assert await db.fetchval("SELECT count(*) FROM paper_account_events WHERE kind='requested'")==1
            page=(await c.get('/v1/paper/bindings/'+rid+'/events',params=dict(limit=2))).json()
            assert page['has_more']
            older=(await c.get('/v1/paper/bindings/'+rid+'/events',params=dict(before_revision=page['next_before_revision']))).json()
            assert len(older['items'])==1 and older['items'][0]['kind']=='enabled'
    asyncio.run(run())


@integration
def test_signal_survives_rollback_overwritten_last_decision_and_lost_outer_commit(tmp_path,monkeypatch):
    async def run():
        async with fixture(tmp_path) as f:
            await initialize(f);m=f['manager'];db=f['pool'];rid=await rule(f,threshold='250')
            await bind(f,rid);await m.step()
            original=m.paper.bindings.persist
            async def fail(*args,**kwargs):
                update=await original(*args,**kwargs)
                if kwargs.get('event',{}).get('kind')=='admitted':raise RuntimeError('Injected binding rollback')
                return update
            m.paper.bindings.persist=fail
            with pytest.raises(RuntimeError,match='binding rollback'):await quote_step(f,'3')
            assert not m.paper.connected
            assert await db.fetchval("SELECT count(*) FROM rule_evaluations WHERE disposition='signal'")==1
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==0
            assert await db.fetchval('SELECT revision FROM paper_account')==1
            assert await db.fetchval("SELECT count(*) FROM paper_binding_events WHERE kind='admitted'")==0
            m=await f['reload']()
            # Recovery overwrites last_decision with suppression; the inbox query must still find the signal.
            con=m.connection;armed=[False];persist=m.paper.bindings.persist
            async def mark(*args,**kwargs):
                update=await persist(*args,**kwargs)
                if kwargs.get('event',{}).get('kind')=='admitted':armed[0]=True
                return update
            m.paper.bindings.persist=mark
            class Tx:
                def __init__(self,tx):self.tx=tx
                async def __aenter__(self):return await self.tx.__aenter__()
                async def __aexit__(self,*args):
                    result=await self.tx.__aexit__(*args)
                    if args[0] is None and armed[0]:
                        armed[0]=False;raise RuntimeError('Injected binding commit acknowledgement loss')
                    return result
            class LostCommit:
                def __getattr__(self,name):return getattr(con,name)
                def transaction(self,*args,**kwargs):
                    tx=con.transaction(*args,**kwargs)
                    return tx if con.is_in_transaction() else Tx(tx)
            m.connection=LostCommit()
            with pytest.raises(RuntimeError,match='acknowledgement loss'):await m.step()
            assert m.rules.entries[rid]['last']['disposition']!='signal'
            assert not m.paper.connected
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==1
            assert await db.fetchval('SELECT revision FROM paper_account')==2
            assert await db.fetchval("SELECT count(*) FROM paper_binding_events WHERE kind='admitted'")==1
            m.connection=con;m=await f['reload']()
            await m.step();await m.step()
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==1
            assert await db.fetchval('SELECT revision FROM paper_account')==2
            # Unchanged rules cause no binding SQL at all.
            class NoSQL:
                def __getattr__(self,name):
                    if name in ('fetchrow','fetchval','execute'):raise AssertionError('Unexpected unchanged binding SQL')
                    return getattr(actual,name)
            actual=m.connection;m.connection=NoSQL()
            try:await m.paper.bindings.step()
            finally:m.connection=actual
    asyncio.run(run())


@integration
def test_insufficient_cash_is_recorded_once_and_never_retried_when_cash_returns(tmp_path,monkeypatch):
    async def run():
        async with fixture(tmp_path) as f:
            await initialize(f);db=f['pool'];m=f['manager']
            ids=[await rule(f,threshold='250',cooldown=n) for n in (1,2)]
            for rid in ids:
                cfg=binding_config(rid);cfg['policy']['entry_timeout_seconds']=2
                await bind(f,rid,cfg)
            await m.step();await quote_step(f,'3')
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==1
            events=[json.loads(r['record']) for r in await db.fetch("SELECT record FROM paper_binding_events WHERE kind='rejected'")]
            assert len(events)==1 and 'available paper cash' in events[0]['reason']
            rejected=events[0]
            assert await db.fetchval('SELECT count(*) FROM paper_positions WHERE rule_id=$1',rejected['rule_id'])==0
            import midas_data.feature_windows as fw
            monkeypatch.setattr(fw,'POLL_SECONDS',.05);m.start();await wait_empty(m)
            assert Decimal((await f['client'].get('/v1/paper/account')).json()['state']['cash_usd'])==100
            m=await f['reload']();await m.step()
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==1
            assert await db.fetchval("SELECT count(*) FROM paper_binding_events WHERE kind='rejected'")==1
    asyncio.run(run())


@integration
def test_binding_pause_resume_skips_backlog_without_replaying_old_signals(tmp_path,monkeypatch):
    async def run():
        async with fixture(tmp_path) as f:
            await initialize(f);m=f['manager'];c=f['client'];db=f['pool'];rid=await rule(f,threshold='250')
            config,_=await bind(f,rid);await m.step()
            original=m.paper.bindings.step
            async def noop():pass
            m.paper.bindings.step=noop
            await quote_step(f,'3');seq=m.rules.entries[rid]['sequence']
            assert (await c.post('/v1/paper/bindings/'+rid+'/pause')).status_code==204
            await quote_step(f,'2');await quote_step(f,'3')
            assert m.rules.entries[rid]['sequence']>seq
            m.paper.bindings.step=original
            r=await c.post('/v1/paper/bindings',json=config);assert r.status_code==202
            assert r.json()['cursor']==m.rules.entries[rid]['sequence']
            await m.step();assert await db.fetchval('SELECT count(*) FROM paper_positions')==0
            await quote_step(f,'2');await quote_step(f,'3')
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==1
            m=await f['reload']()
            assert m.paper.bindings.entries[rid]['enabled'] and m.paper.bindings.entries[rid]['cursor']>seq
    asyncio.run(run())


@integration
@pytest.mark.parametrize('manual_budget,expected',[('60','existing'),('50','rejected')])
def test_manual_signal_consumption_cannot_be_duplicated_by_binding(tmp_path,manual_budget,expected):
    async def run():
        async with fixture(tmp_path) as f:
            await initialize(f);m=f['manager'];c=f['client'];db=f['pool'];rid=await rule(f,threshold='250')
            config,_=await bind(f,rid);await m.step();original=m.paper.bindings.step
            async def noop():pass
            m.paper.bindings.step=noop;await quote_step(f,'3')
            seq=m.rules.entries[rid]['sequence']
            body=dict(rule_id=rid,signal_sequence=seq,policy=dict(config['policy'],budget_usd=manual_budget))
            r=await c.post('/v1/paper/positions',json=body);assert r.status_code==202
            # A source rule pause does not retract a signal already committed to the inbox.
            assert (await c.post('/v1/rules/active/'+rid+'/pause')).status_code==204
            m.paper.bindings.step=original;await m.step()
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==1
            assert await db.fetchval('SELECT revision FROM paper_account')==2
            assert await db.fetchval('SELECT kind FROM paper_binding_events WHERE signal_sequence=$1',seq)==expected
    asyncio.run(run())


@integration
def test_stale_signal_is_rejected_without_reservation(tmp_path,monkeypatch):
    async def run():
        async with fixture(tmp_path) as f:
            await initialize(f);m=f['manager'];db=f['pool'];rid=await rule(f,threshold='250')
            await bind(f,rid);await m.step();original=m.paper.bindings.step
            async def noop():pass
            m.paper.bindings.step=noop;await quote_step(f,'3');m.paper.bindings.step=original
            import midas_data.paper_positions as pp
            real=pp.datetime
            class Late:
                @classmethod
                def now(cls,tz):return real.now(tz)+timedelta(seconds=3600)
            monkeypatch.setattr(pp,'datetime',Late)
            await m.paper.bindings.step()
            assert await db.fetchval('SELECT count(*) FROM paper_positions')==0
            assert await db.fetchval('SELECT revision FROM paper_account')==1
            record=json.loads(await db.fetchval("SELECT record FROM paper_binding_events WHERE kind='rejected'"))
            assert record['reason']=='Signal is too old or in the future'
    asyncio.run(run())

"""Real PostgreSQL, private synthetic schema, and actual owner loop; no provider calls."""
import asyncio
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,os
from uuid import uuid4
import pytest

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]


def test_active_rules_atomic_signals_timers_resume_and_api(tmp_path,monkeypatch):
    async def run():
        import asyncpg,httpx
        import midas_data.feature_windows as fw
        from midas_data.active_rules import ActiveRules
        from midas_data.api import make_app
        from midas_data.contracts import CaptureMetadata
        from midas_data.normalize import normalize_capture
        from midas_data.raw_store import RawStore
        from midas_data.settings import Settings
        from midas_data.store import Store
        from midas_data.trajectory import METHODS

        settings=Settings.from_env();schema='active_rule_fixture_'+uuid4().hex
        admin=await asyncpg.connect(settings.database_dsn,command_timeout=15)
        pool=None;manager=None
        try:
            assert await admin.fetchval('SELECT current_database()')=='midas_data'
            await admin.execute(f'CREATE SCHEMA {schema}')
            pool=await asyncpg.create_pool(settings.database_dsn,min_size=1,max_size=4,command_timeout=15,
                server_settings={'search_path':schema,'timezone':'UTC'})
            async with pool.acquire() as con:
                for path in sorted(Path('midas_data/migrations').glob('*.sql')):
                    await con.execute(path.read_text(encoding='utf-8'))
            store=Store('fixture',RawStore(tmp_path,min_free_bytes=0));store.pool=pool
            token='0x'+'a'*40;start=datetime.now(timezone.utc)-timedelta(seconds=30)
            async def capture(seconds,value):
                meta=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint='token_info',chain='base',
                    requested_at=start+timedelta(seconds=seconds-1),received_at=start+timedelta(seconds=seconds),
                    http_status=200,evidence_kind='original_response')
                raw=json.dumps({'data':{'address':token,'price':{'price':'1'},'circulating_supply':value}}).encode()
                await store.accept_capture(meta,raw)
                await store.record_normalization(meta.capture_id,normalize_capture(meta,raw))
            await capture(0,'120');await capture(20,'120')
            def owner():
                value=fw.FeatureWindows(store);value.rules=ActiveRules(value);return value
            manager=owner();await manager.connect()
            app=make_app(settings);app.state.store=store;app.state.feature_windows=manager
            params=dict(source_kind='fixture',chain='base',token=token,method=next(iter(METHODS)),
                        window_seconds=180,gap_after_seconds=120)
            base=dict(comparison=dict(window=params,feature='market_cap_latest',operator='gte',threshold='100',unit='usd'),
                      debounce_seconds=0,cooldown_seconds=5)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                                        headers={'authorization':'Bearer '+settings.api_key}) as client:
                async def register(body):
                    response=await client.post('/v1/rules/active',json=body)
                    assert response.status_code==202,response.text
                    return response.json()['rule_id']
                async def events(key,**params):
                    r=await client.get('/v1/rules/active/'+key+'/evaluations',params=params)
                    assert r.status_code==200,r.text
                    return r.json()
                def current(key):return manager.rules.entries[key]['last']
                async def wait_for(predicate):
                    async with asyncio.timeout(12):
                        while not predicate():await asyncio.sleep(.05)
                assert (await client.post('/v1/rules/active',json=base)).status_code==422
                reply=await client.post('/v1/feature-windows',params=params)
                assert reply.status_code==202
                window_key=reply.json()['window_id']
                a=await register(base)
                b=await register(dict(base,debounce_seconds=3,cooldown_seconds=0))
                assert await register(base)==a
                await manager.step()
                assert current(a)['disposition']=='signal' and current(b)['reason']=='debouncing'
                first_frame=current(a)['frame_key']
                assert first_frame==current(b)['frame_key']
                assert await pool.fetchval('SELECT count(*) FROM rule_frames')==1
                assert await pool.fetchval("SELECT count(*) FROM rule_evaluations WHERE disposition='signal'")==1
                seq=manager.rules.entries[a]['sequence'];reads=manager.reads
                await manager.step()
                assert manager.rules.entries[a]['sequence']==seq and manager.reads==reads
                # List/status consumers perform no SQL and do not drive evaluations.
                class NoSql:
                    def __getattr__(self,name):raise AssertionError('Consumer attempted SQL: '+name)
                store.pool=NoSql()
                try:
                    responses=await asyncio.gather(*[client.get('/v1/rules/active') for _ in range(20)])
                    assert all(r.status_code==200 and r.content==responses[0].content for r in responses)
                finally:store.pool=pool
                assert manager.rules.entries[a]['sequence']==seq
                # A real false rearms; the subsequent true is observed during cooldown.
                await capture(25,'90');await manager.step()
                assert current(a)['reason']=='condition_false'
                await capture(28,'120');await manager.step()
                assert current(a)['reason']=='cooldown' and current(b)['reason']=='debouncing'
                monkeypatch.setattr(fw,'POLL_SECONDS',.05)
                manager.start()
                await wait_for(lambda: current(b)['reason']=='awaiting_observation_coverage')
                assert current(b)['trigger']=='timer'
                # Fresh observation after the duration, with no missing samples, can confirm B.
                await capture((datetime.now(timezone.utc)-start).total_seconds(),'120')
                await wait_for(lambda: current(b)['disposition']=='signal')
                await wait_for(lambda: current(a)['disposition']=='signal')
                assert current(a)['trigger']=='timer'
                await manager.close()
                manager=owner();await manager.connect();app.state.feature_windows=manager
                signal_count=await pool.fetchval("SELECT count(*) FROM rule_evaluations WHERE disposition='signal'")
                await manager.step()
                assert await pool.fetchval("SELECT count(*) FROM rule_evaluations WHERE disposition='signal'")==signal_count
                assert current(a)['reason']==current(b)['reason']=='already_signaled'
                manager.rules.last_step_monotonic-=11
                await manager.step()
                assert current(a)['trigger']=='runtime_gap' and current(a)['continuity_reset']
                assert current(a)['unevaluated_since'] is not None
                assert current(a)['reason']=='already_signaled'
                # Failure inside the decision transaction rolls back shared frame and state.
                await capture((datetime.now(timezone.utc)-start).total_seconds(),'125')
                old_sequence=manager.rules.entries[a]['sequence']
                frame_count=await pool.fetchval('SELECT count(*) FROM rule_frames')
                con=manager.connection
                class FailInsert:
                    def __getattr__(self,name):return getattr(con,name)
                    async def execute(self,sql,*args):
                        if sql.startswith('INSERT INTO rule_evaluations'):
                            raise RuntimeError('Injected decision failure')
                        return await con.execute(sql,*args)
                manager.connection=FailInsert()
                with pytest.raises(RuntimeError,match='Injected'):await manager.step()
                assert manager.rules.entries[a]['sequence']==old_sequence
                assert await pool.fetchval('SELECT sequence FROM active_rules WHERE rule_id=$1',a)==old_sequence
                assert await pool.fetchval('SELECT count(*) FROM rule_frames')==frame_count
                manager.connection=con
                await manager.disconnect()
                assert manager.rules.listing()['items'][0]['evaluation_pending']
                await manager.connect();await manager.step()
                # Commit succeeded but the process lost its memory update: reload must not re-emit.
                cbody=dict(base,cooldown_seconds=0)
                c=await register(cbody)
                original=manager.rules.persist
                async def lost_ack(key,entry,*args,**kwargs):
                    previous=dict(entry)
                    await original(key,entry,*args,**kwargs)
                    if key==c:
                        entry.clear();entry.update(previous)
                        raise RuntimeError('Injected lost acknowledgement')
                manager.rules.persist=lost_ack
                with pytest.raises(RuntimeError,match='acknowledgement'):await manager.step()
                assert await pool.fetchval("SELECT count(*) FROM rule_evaluations WHERE rule_id=$1 AND disposition='signal'",c)==1
                assert manager.rules.entries[c]['sequence']==0
                await manager.disconnect()
                manager=owner();await manager.connect();app.state.feature_windows=manager
                await manager.step()
                assert current(c)['reason']=='already_signaled'
                assert await pool.fetchval("SELECT count(*) FROM rule_evaluations WHERE rule_id=$1 AND disposition='signal'",c)==1
                # Pagination and immutable source evidence remain readable after the window moves.
                page=await events(a,limit=2)
                assert len(page['items'])==2 and page['has_more']
                next_page=await events(a,limit=2,before_sequence=page['next_before_sequence'])
                assert max(x['sequence'] for x in next_page['items'])<min(x['sequence'] for x in page['items'])
                frame=(await client.get('/v1/rule-frames/'+first_frame)).json()
                assert frame['generation']==1
                stored=await pool.fetchval('SELECT payload FROM rule_frames WHERE frame_key=$1',first_frame)
                assert json.loads(bytes(stored))==frame
                import hashlib
                assert hashlib.sha256(bytes(stored)).hexdigest()==first_frame
                assert (await client.get('/v1/rule-frames/'+'0'*64)).status_code==404
                assert (await client.post('/v1/rules/active',content=b'x'*8193)).status_code==413
                assert (await client.get('/v1/rules/active',headers={'authorization':'bad'})).status_code==401
                bad=dict(base,comparison=dict(base['comparison'],feature='market_cap_max_observed'))
                assert (await client.post('/v1/rules/active',json=bad)).status_code==422
                # Pause persists and is idempotent. Re-enabling preserves the existing signal latch.
                pause='/v1/rules/active/'+c+'/pause'
                assert (await client.post(pause)).status_code==204
                sequence=manager.rules.entries[c]['sequence']
                assert (await client.post(pause)).status_code==204
                assert manager.rules.entries[c]['sequence']==sequence
                assert not await pool.fetchval('SELECT enabled FROM active_rules WHERE rule_id=$1',c)
                assert await register(cbody)==c
                await manager.step()
                assert current(c)['reason']=='already_signaled'
                for number in range(13):
                    await register(dict(base,cooldown_seconds=100+number))
                assert len(manager.rules.entries)==16
                assert (await client.post('/v1/rules/active',json=dict(base,cooldown_seconds=999))).status_code==429
                # Window removal makes all its enabled rules unknown; it creates no new collection.
                await client.delete('/v1/feature-windows/'+window_key)
                await manager.step()
                assert all(e['last']['raw_state']=='unknown' for e in manager.rules.entries.values())
                sequence=manager.rules.entries[a]['sequence'];await manager.step()
                assert manager.rules.entries[a]['sequence']==sequence
                assert await pool.fetchval("""SELECT count(*) FROM rule_evaluations e JOIN active_rules r USING(rule_id)
                    WHERE e.sequence>r.sequence""")==0
        finally:
            if manager:await manager.close()
            if pool:await pool.close()
            assert schema.startswith('active_rule_fixture_') and len(schema)==len('active_rule_fixture_')+32
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE');await admin.close()
    asyncio.run(run())

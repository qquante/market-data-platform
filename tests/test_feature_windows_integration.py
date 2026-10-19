"""Durable shared windows on private PostgreSQL, with synthetic publication events."""
import asyncio
from datetime import datetime,timedelta,timezone
from pathlib import Path
import os
from uuid import uuid4
import pytest

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]


def test_shared_window_publications_failure_recovery_owner_and_clock(tmp_path,monkeypatch):
    async def run():
        import asyncpg,httpx,json
        import midas_data.feature_windows as fw
        from midas_data.api import make_app
        from midas_data.contracts import CaptureMetadata
        from midas_data.normalize import normalize_capture
        from midas_data.raw_store import RawStore
        from midas_data.settings import Settings
        from midas_data.store import Store
        from midas_data.trajectory import METHODS

        settings=Settings.from_env();schema='window_fixture_'+uuid4().hex
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
            token='0x'+'a'*40
            start=datetime.now(timezone.utc)-timedelta(seconds=30)
            async def capture(seconds,value,address=token):
                meta=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint='token_info',chain='base',
                    requested_at=start+timedelta(seconds=seconds-1),received_at=start+timedelta(seconds=seconds),
                    http_status=200,evidence_kind='original_response')
                raw=json.dumps({'data':{'address':address,'price':{'price':'1'},'circulating_supply':value}}).encode()
                await store.accept_capture(meta,raw);result=normalize_capture(meta,raw)
                await store.record_normalization(meta.capture_id,result)
                return meta,result
            await capture(0,'100')
            peak,run_peak=await capture(10,'150')
            await capture(20,'120')
            manager=fw.FeatureWindows(store);await manager.connect()
            other=fw.FeatureWindows(store)
            with pytest.raises(fw.WindowUnavailable):
                await other.connect()
            assert other.connection is None
            app=make_app(settings);app.state.store=store;app.state.feature_windows=manager
            params=dict(source_kind='fixture',chain='BASE',token='0x'+'A'*40,method=next(iter(METHODS)),
                        window_seconds=300,gap_after_seconds=60)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                    headers={'authorization':'Bearer '+settings.api_key}) as client:
                registration=await client.post('/v1/feature-windows',params=params)
                assert registration.status_code==202,registration.text
                key=registration.json()['window_id'];route='/v1/feature-windows/'+key
                assert (await client.post('/v1/feature-windows',params=params)).json()['window_id']==key
                assert (await client.get(route)).status_code==503
                rule_body=dict(window=params,feature='market_cap_latest',operator='gt',threshold='121',unit='usd')
                async def decision():
                    answer=await client.post('/v1/rules/evaluate',json=rule_body)
                    assert answer.status_code==200,answer.text
                    return answer.json()
                assert (await decision())['state']=='unknown'
                await manager.step()
                first_decision=await decision()
                assert first_decision['state']=='false'
                original=(await client.get(route)).json()
                assert original['generation']==1
                assert original['result']['features']['features']['market_cap_latest']['value_text']=='120'
                original_blob=manager.read(key)
                assert bytes(await pool.fetchval('SELECT payload FROM feature_windows WHERE window_id=$1',key))==original_blob
                reads,polls=manager.reads,manager.polls
                # The HTTP read path works even if all Store pool I/O is forbidden.
                class NoSql:
                    def __getattr__(self,name):
                        raise AssertionError('Consumer attempted SQL: '+name)
                store.pool=NoSql()
                try:
                    replies=await asyncio.gather(*[client.get(route) for _ in range(20)])
                    assert all(r.status_code==200 and r.content==original_blob for r in replies)
                    decisions=await asyncio.gather(*[decision() for _ in range(20)])
                    assert all(d==first_decision for d in decisions)
                finally:
                    store.pool=pool
                assert (manager.reads,manager.polls)==(reads,polls)
                await capture(21,'999999','0x'+'b'*40)
                await manager.step()
                assert manager.reads==reads and manager.read(key)==original_blob

                revised=run_peak.model_copy(update={'normalizer_version':'window-revision','facts':[
                    f.model_copy(update={'fact_id':uuid4(),'value_text':'90' if f.metric=='circulating_supply' else f.value_text})
                    for f in run_peak.facts]})
                await store.record_normalization(peak.capture_id,revised)
                await manager.step()
                corrected=json.loads(manager.read(key))
                assert corrected['generation']==2 and corrected['trigger']=='publication'
                corrected_decision=await decision()
                assert corrected_decision['state']=='false'
                assert corrected_decision['rule_id']==first_decision['rule_id']
                assert corrected_decision['evaluation_id']!=first_decision['evaluation_id']
                assert corrected['result']['features']['features']['market_cap_min_observed']['value_text']=='90'
                cutoff=original['publication_cutoff'];old=original['result']
                historical=await client.get('/v1/trajectory/features',params=dict(source_kind='fixture',chain='base',token=token,
                    method=next(iter(METHODS)),start=old['start'],end=old['end'],knowledge_cutoff=cutoff,gap_after_seconds=60,max_points=2000))
                assert historical.status_code==200 and historical.json()==old

                late,late_run=await capture(15,'200')
                real_con=manager.connection
                class FailSave:
                    def __getattr__(self,name):return getattr(real_con,name)
                    async def execute(self,sql,*args):
                        if sql.startswith('UPDATE feature_windows SET payload'):
                            raise RuntimeError('Injected checkpoint failure')
                        return await real_con.execute(sql,*args)
                manager.connection=FailSave()
                try:
                    await manager.step()
                    assert (await client.get(route)).status_code==503
                    assert (await decision())['state']=='unknown'
                    saved=json.loads(bytes(await pool.fetchval('SELECT payload FROM feature_windows WHERE window_id=$1',key)))
                    assert saved==corrected
                finally:
                    manager.connection=real_con
                await manager.step()
                recovered=json.loads(manager.read(key))
                assert recovered['generation']==3
                assert recovered['result']['features']['features']['market_cap_max_observed']['value_text']=='200'
                await store.record_normalization(late.capture_id,late_run)
                reads=manager.reads
                await manager.step()
                assert manager.reads==reads  # Replay keeps the original publication marker.
                await store.record_normalization(late.capture_id,late_run.model_copy(
                    update={'normalizer_version':'window-invalidation','facts':[]}))
                await manager.step()
                invalidated=json.loads(manager.read(key))
                assert invalidated['result']['coverage']['unavailable_points']==1
                assert invalidated['result']['features']['features']['market_cap_max_observed']['value_text']=='120'

                generation=invalidated['generation']
                await manager.disconnect()
                with pytest.raises(fw.WindowUnavailable):manager.read(key)
                manager=fw.FeatureWindows(store);await manager.connect();app.state.feature_windows=manager
                assert manager.windows[key]['generation']==generation
                assert (await client.get(route)).status_code==503
                await manager.step()
                resumed=json.loads(manager.read(key))
                assert resumed['generation']==generation+1 and resumed['trigger']=='recovery'
                assert resumed['result']['points']==invalidated['result']['points']
                assert (await decision())['state']=='false'

                # Overflow coalesces to one full refresh per window; it never silently skips a tail.
                monkeypatch.setattr(fw,'PUBLICATION_BATCH',1)
                await capture(22,'121');await capture(23,'122')
                await manager.step()
                assert json.loads(manager.read(key))['result']['features']['features']['market_cap_latest']['value_text']=='122'
                assert (await decision())['state']=='true'
                assert (await decision())['rule_id']==first_decision['rule_id']
                monkeypatch.setattr(fw,'PUBLICATION_BATCH',128)

                for char in 'bcd':
                    reply=await client.post('/v1/feature-windows',params=dict(params,token='0x'+char*40))
                    assert reply.status_code==202
                assert (await client.post('/v1/feature-windows',params=dict(params,token='0x'+'e'*40))).status_code==429
                for entry in list(manager.windows):
                    if entry!=key:assert (await client.delete('/v1/feature-windows/'+entry)).status_code==204
                assert (await client.get('/v1/feature-windows/'+'0'*64)).status_code==404
                assert (await client.get(route,headers={'authorization':'invalid'})).status_code==401
                assert (await client.post('/v1/feature-windows',params=dict(params,window_seconds=0))).status_code==422

                # Run the actual loop: no HTTP reader is needed to advance a clock deadline.
                await manager.disconnect()
                monkeypatch.setattr(fw,'POLL_SECONDS',0.05)
                monkeypatch.setattr(fw,'MAX_FRAME_SECONDS',1)
                manager=fw.FeatureWindows(store);app.state.feature_windows=manager;manager.start()
                async def wait_for(predicate):
                    async with asyncio.timeout(8):
                        while not predicate():await asyncio.sleep(0.05)
                await wait_for(lambda: manager.windows.get(key,{}).get('ready',False))
                first_generation=manager.windows[key]['generation']
                await wait_for(lambda: manager.clock_updates>=1 and manager.windows[key]['ready'])
                assert manager.windows[key]['generation']>first_generation
                manager.windows[key]['expires_at']=datetime.now(timezone.utc)-timedelta(seconds=1)
                assert (await client.get(route)).status_code==503
                assert (await decision())['state']=='unknown'
                await client.delete(route)
                assert await pool.fetchval('SELECT count(*) FROM feature_windows')==0
        finally:
            if manager:await manager.close()
            if pool:await pool.close()
            assert schema.startswith('window_fixture_') and len(schema)==len('window_fixture_')+32
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE');await admin.close()
    asyncio.run(run())

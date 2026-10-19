"""Admission is governed by storage; backlog never gates raw capture."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from midas_data.api import make_app
from midas_data.bridge import Bridge, BridgeConfig, bridge_healthy
from midas_data.raw_store import RawStore, StoragePressure
from midas_data.settings import Settings
from midas_data.importer import ImportFailure


def test_storage_pressure_prevents_new_object_and_recovers(tmp_path):
    store=RawStore(tmp_path,max_bytes=1024,min_free_bytes=100)
    with patch('midas_data.raw_store.shutil.disk_usage',return_value=SimpleNamespace(free=100)):
        assert not store.admission()['accepting']
        with pytest.raises(StoragePressure):store.write(b'new')
        assert not list(tmp_path.rglob('*.gz'))
        assert not list(tmp_path.rglob('.pending-*'))
    with patch('midas_data.raw_store.shutil.disk_usage',return_value=SimpleNamespace(free=100000)):
        obj=store.write(b'new')
    with patch('midas_data.raw_store.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
        assert store.write(b'new')==obj
        assert store.read(obj.sha256)==b'new'


@pytest.mark.parametrize('fresh,state,error,healthy',[
    (True,'ready',None,True),(True,'paused','storage_backpressure',True),
    (False,'paused','storage_backpressure',False),(True,'degraded','TimeoutError',False),
    (True,'paused','unexpected',False),(True,'starting',None,False),
])
def test_liveness_does_not_confuse_storage_pause_with_readiness(fresh,state,error,healthy):
    assert bridge_healthy({'fresh':fresh,'state':state,'last_error':error}) is healthy
    assert not bridge_healthy(None)


class Database:
    def __init__(self):
        self.state={'progress':json.dumps({'turn':0})};self.pulses=[];self.saved=[]
    def is_closed(self):return False
    async def fetchrow(self,sql,*args):return self.state
    async def fetchval(self,sql,*args):
        assert 'outbox' not in sql and 'normalization_runs' not in sql, 'backlog must not gate admission'
        return 0
    async def execute(self,sql,*args):
        if 'heartbeat_at' in sql:self.pulses.append(args)
        else:self.saved.append(args)


@pytest.mark.parametrize('accepting',[False,True])
def test_admission_precedes_source_and_storage_pause_keeps_cursor(accepting):
    async def proof():
        db=Database();seen=[]
        async def admission():return {'accepting':accepting,'free_bytes':2000 if accepting else 0,'required_free_bytes':1000,'basis':'storage_filesystems_free_space'}
        class Source:
            async def clock(self):seen.append('source');raise RuntimeError('source_reached')
        bridge=Bridge(BridgeConfig('fixture:admission',datetime.now(timezone.utc),'fixture',source_kind='fixture'),db,Source(),None,admission)
        bridge.locked=True
        if accepting:
            with pytest.raises(RuntimeError,match='source_reached'):await bridge.step()
            assert seen==['source']
        else:
            result=await bridge.step()
            assert result['state']=='paused' and not seen and not db.saved
            assert db.pulses[-1][-2]=='storage_backpressure'
    asyncio.run(proof())


def test_pressure_mid_page_preserves_cursor_and_retries_same_identity():
    async def proof():
        from midas_data.bridge import initial_progress
        origin=datetime(2026,9,8,tzinfo=timezone.utc);db=Database();db.state['progress']=json.dumps(initial_progress(origin))
        state={'pressure':False};cid=uuid4();seen=[]
        class Source:
            async def clock(self):return origin.replace(minute=3)
            async def page(self,*args):return [{'received_at_utc':origin,'call_id':cid}]
        async def admission():return {'accepting':not state['pressure'],'free_bytes':0 if state['pressure'] else 2000,'required_free_bytes':1000,'basis':'storage_filesystems_free_space'}
        bridge=Bridge(BridgeConfig('fixture:admission',origin,'fixture',source_kind='fixture'),db,Source(),None,admission);bridge.locked=True
        async def row(item):
            seen.append(item['call_id'])
            if len(seen)==1:state['pressure']=True;raise ImportFailure('api_http_503')
            return 'accepted'
        bridge.process_row=row
        assert (await bridge.step())['state']=='paused'
        assert not db.saved
        state['pressure']=False
        assert (await bridge.step())['page']=={'accepted':1}
        assert seen==[cid,cid] and len(db.saved)==1
    asyncio.run(proof())


def test_invalid_capacity_is_not_silently_accepted():
    async def proof():
        async def admission():return {'accepting':True,'free_bytes':0,'required_free_bytes':1000,'basis':'storage_filesystems_free_space'}
        bridge=Bridge(BridgeConfig('fixture:admission',datetime.now(timezone.utc),'fixture',source_kind='fixture'),Database(),None,None,admission);bridge.locked=True
        with pytest.raises(ValueError,match='admission'):await bridge.step()
    asyncio.run(proof())


def test_api_exposes_capacity_and_returns_retryable_503_without_accepting(tmp_path):
    async def proof():
        raw=RawStore(tmp_path,1024,min_free_bytes=100)
        settings=Settings('unused','unused','fixture-key','unused','unused','unused','unused',tmp_path,'unused')
        app=make_app(settings)
        class Store:
            async def accept_capture(self,metadata,body):raw.write(body);raise AssertionError('should_pause_before_accept')
        store=Store();store.raw=raw;app.state.store=store
        stamp=datetime.now(timezone.utc).isoformat()
        meta={'capture_id':str(uuid4()),'source_kind':'fixture','endpoint':'token_info','chain':'base','requested_at':stamp,'received_at':stamp,'http_status':200,'request_params':{},'evidence_kind':'original_response'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.get('/v1/admission')).status_code==401
            with patch('midas_data.raw_store.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
                headers={'authorization':'Bearer fixture-key'}
                result=await client.get('/v1/admission',headers=headers)
                assert result.status_code==200 and result.json()['accepting'] is False
                reply=await client.post('/v1/captures',headers={**headers,'x-midas-metadata':json.dumps(meta)},content=b'{}')
                assert reply.status_code==503 and reply.headers['retry-after']=='5'
                assert reply.json()['detail']=='storage_backpressure'
        assert not list(tmp_path.rglob('*.gz'))
    asyncio.run(proof())
@pytest.mark.integration
@pytest.mark.skipif(__import__('os').getenv('DATA_INTEGRATION')!='1',reason='Isolated PostgreSQL opt-in')
def test_postgres_and_api_accept_beyond_500_then_replay_and_normalize(tmp_path):
    async def proof():
        import hashlib,os,zlib
        from pathlib import Path
        import asyncpg
        from midas_data.contracts import CaptureMetadata
        from midas_data.normalize import normalize_capture
        from midas_data.store import Store
        dsn=os.environ['DATA_DATABASE_DSN'];schema='admission_fixture_'+uuid4().hex
        admin=await asyncpg.connect(dsn);pool=None
        try:
            assert await admin.fetchval('SELECT current_database()')=='midas_data'
            await admin.execute(f'CREATE SCHEMA {schema}')
            pool=await asyncpg.create_pool(dsn,min_size=1,max_size=3,server_settings={'search_path':schema,'timezone':'UTC','statement_timeout':'15000'})
            async with pool.acquire() as con:
                for migration in sorted(Path('midas_data/migrations').glob('*.sql')):
                    await con.execute(migration.read_text())
            raw=RawStore(tmp_path,1024,min_free_bytes=100)
            store=Store('unused',raw);store.pool=pool
            origin=datetime(2026,9,8,12,tzinfo=timezone.utc);seed_id=uuid4()
            body=b'{"address":"0x1111111111111111111111111111111111111111","price":{"price":"1.2300"}}'
            meta=CaptureMetadata(capture_id=seed_id,source_kind='fixture',endpoint='token_info',chain='base',requested_at=origin,received_at=origin,http_status=200,evidence_kind='original_response')
            await store.accept_capture(meta,body)
            # Structural SQL fixtures simulate an existing durable backlog cheaply.
            await pool.execute("""WITH copies AS (INSERT INTO captures(capture_id,fingerprint,raw_sha256,metadata,source_kind,evidence_kind,endpoint,chain,received_at)
                SELECT x.id,'fixture:'||x.id,c.raw_sha256,jsonb_set(c.metadata,'{capture_id}',to_jsonb(x.id::text)),
                c.source_kind,c.evidence_kind,c.endpoint,c.chain,c.received_at
                FROM captures c CROSS JOIN (SELECT gen_random_uuid() AS id FROM generate_series(1,500)) x
                WHERE c.capture_id=$1 RETURNING capture_id)
                INSERT INTO outbox(capture_id) SELECT capture_id FROM copies""",seed_id)
            assert await pool.fetchval('SELECT count(*) FROM outbox WHERE consumed_at IS NULL')==501
            settings=Settings('unused','unused','fixture-key','unused','unused','unused','unused',tmp_path,'unused')
            app=make_app(settings);app.state.store=store;cid=uuid4();sha=hashlib.sha256(body).hexdigest()
            row={'call_id':cid,'received_at_utc':origin,'requested_at_utc':origin,'endpoint':'token_info','chain':'base','status':'success','http_status':200,'response_sha256':sha,'payload_sha256':sha,'request_params':{'address':'0x1111111111111111111111111111111111111111'}}
            class Source:
                pages=0
                async def clock(self):return origin.replace(minute=3)
                async def page(self,*args):self.pages+=1;return [row]
                async def blob(self,*args):return {'payload':zlib.compress(body),'raw_bytes':len(body),'codec':'zlib'}
            source=Source()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'authorization':'Bearer fixture-key'}) as client:
                async def admission():return (await client.get('/v1/admission')).json()
                async def deliver(capture):
                    response=await client.post('/v1/captures',headers={'x-midas-metadata':capture.metadata_header()},content=capture.raw)
                    if response.status_code!=202:raise ImportFailure('api_http_'+str(response.status_code))
                    return response.json()
                async with pool.acquire() as con:
                    bridge=await Bridge(BridgeConfig('fixture:'+schema,origin,'fixture',source_kind='fixture'),con,source,deliver,admission).open()
                    result=await bridge.step()
                    assert result['page']=={'accepted':1}
                    assert await pool.fetchval('SELECT count(*) FROM outbox WHERE consumed_at IS NULL')==502
                    assert await pool.fetchval('SELECT count(*) FROM normalization_runs')==0
                    stored=await store.get_capture(cid)
                    assert raw.read(stored['raw_sha256'])==body
                    assert stored['available_at'] is not None
                    assert (await bridge.step())['page']=={'duplicate':1}
                    assert await pool.fetchval('SELECT count(*) FROM captures')==502
                    before=await con.fetchval('SELECT progress FROM bridge_state WHERE source_name=$1',bridge.config.source_name)
                    pages=source.pages
                    with patch('midas_data.raw_store.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
                        assert (await bridge.step())['state']=='paused'
                        new_meta=meta.model_copy(update={'capture_id':uuid4()})
                        blocked=await client.post('/v1/captures',headers={'x-midas-metadata':new_meta.model_dump_json()},content=body)
                        assert blocked.status_code==503
                        assert not await pool.fetchval('SELECT EXISTS(SELECT 1 FROM captures WHERE capture_id=$1)',new_meta.capture_id)
                        replay=await client.post('/v1/captures',headers={'x-midas-metadata':json.dumps(stored['metadata'])},content=body)
                        assert replay.status_code==202 and replay.json()['duplicate'] is True
                    assert source.pages==pages
                    assert await con.fetchval('SELECT progress FROM bridge_state WHERE source_name=$1',bridge.config.source_name)==before
                    assert (await bridge.step())['page']=={'duplicate':1}
                normalized=normalize_capture(CaptureMetadata.model_validate(stored['metadata']),body)
                receipt=await store.record_normalization(cid,normalized)
                facts=await store.batch_rows(receipt['batch_id'])
                assert any(f['metric']=='price' and f['value_text']=='1.2300' for f in facts)
                assert await pool.fetchval('SELECT consumed_at IS NOT NULL FROM outbox WHERE capture_id=$1',cid)
        finally:
            if pool:await pool.close()
            assert schema.startswith('admission_fixture_') and len(schema)==50
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
            await admin.close()
    asyncio.run(proof())

def test_host_capacity_is_checked_even_when_virtual_disk_has_space(tmp_path):
    store=RawStore(tmp_path,max_bytes=100,min_free_bytes=100,host_capacity_path=tmp_path/'host')
    with patch('midas_data.raw_store.shutil.disk_usage',side_effect=[SimpleNamespace(free=10**12),SimpleNamespace(free=100)]):
        result=store.admission()
        assert result['free_bytes']==100 and result['accepting'] is False
        assert result['host_free_bytes']==100 and result['raw_free_bytes']==10**12
    with patch('midas_data.raw_store.shutil.disk_usage',side_effect=[SimpleNamespace(free=10**12),OSError('host probe unavailable')]):
        with pytest.raises(OSError):store.admission()

"""Real PostgreSQL, private schema: packet/legacy compatibility and atomicity."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from midas_data.contracts import CaptureMetadata
from midas_data.normalize import normalize_capture
from midas_data.raw_store import RawStore
from midas_data.store import Store

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('DATA_INTEGRATION') != '1', reason='Private PostgreSQL opt-in')]


def test_packet_catalog_legacy_queries_replay_and_rollback(tmp_path):
    async def run():
        import asyncpg
        import httpx
        from midas_data.api import make_app
        from midas_data.settings import Settings
        dsn=os.environ['DATA_DATABASE_DSN']; schema='catalog_fixture_'+uuid4().hex
        admin=await asyncpg.connect(dsn,command_timeout=15); pool=None
        try:
            assert await admin.fetchval('SELECT current_database()')=='midas_data'
            await admin.execute(f'CREATE SCHEMA {schema}')
            pool=await asyncpg.create_pool(dsn,min_size=1,max_size=2,command_timeout=15,
                server_settings={'search_path':schema,'timezone':'UTC'})
            async with pool.acquire() as con:
                for path in sorted(Path('midas_data/migrations').glob('*.sql')):
                    await con.execute(path.read_text(encoding='utf-8'))
            store=Store('fixture',RawStore(tmp_path,min_free_bytes=0)); store.pool=pool
            raw=json.dumps({'address':'0x'+'1'*40,'price':{'price':'1.2300000000000000000001'},
                'name':"d'Agua Ω",'website':None,'x/y~z':False},ensure_ascii=False).encode()
            ids=[];outputs=[]
            for _ in range(2):
                cid=uuid4(); ids.append(cid)
                meta=CaptureMetadata(capture_id=cid,source_kind='fixture',endpoint='token_info',chain='base',
                    requested_at='2026-09-09T12:00:00Z',received_at='2026-09-09T12:00:01Z',
                    http_status=200,evidence_kind='original_response')
                await store.accept_capture(meta,raw)
                result=normalize_capture(meta,raw); outputs.append(result)
                await store.record_normalization(cid,result)
                packet=await pool.fetchval('SELECT fields FROM field_catalog_batches WHERE capture_id=$1',cid)
                assert json.loads(packet)==[f.model_dump(mode='json') for f in result.fields]
            assert await pool.fetchval('SELECT count(*) FROM field_observations')==0
            # Simulate one old-format run only inside this disposable fixture schema.
            old_fields=[f.model_dump(mode='json') for f in outputs[0].fields]
            await pool.copy_records_to_table('field_observations',records=[
                (ids[0],outputs[0].normalizer_version,*(f[k] for k in ('pointer','category','value_type','quality','reason','contract_id')))
                for f in old_fields],columns=('capture_id','normalizer_version','pointer','category','value_type','quality','reason','contract_id'))
            # During overlap the packet must take precedence, without double counting.
            overlap=await store.catalog('fixture',1000,20)
            assert all(r['observations']==2 for r in overlap)
            await pool.execute('DELETE FROM field_catalog_batches WHERE capture_id=$1',ids[0])
            assert await store.catalog('fixture',1000,20)==overlap
            for cid in ids:
                rows=await store.catalog('fixture',1000,1,cid)
                assert len(rows)==len(old_fields) and all(r['observations']==1 for r in rows)
            assert await store.catalog('gmgn',1000,20)==[]
            before=await store.get_capture(ids[0])
            await store.record_normalization(ids[0],outputs[0])
            assert (await store.get_capture(ids[0]))['runs']==before['runs']
            assert await pool.fetchval('SELECT count(*) FROM field_catalog_batches WHERE capture_id=$1',ids[0])==0
            # New fields are invisible until their interpretation is published.
            await pool.execute('UPDATE normalization_runs SET available_at=NULL WHERE capture_id=$1',ids[1])
            assert await store.catalog('fixture',1000,1,ids[1])==[]
            await store.record_normalization(ids[1],outputs[1])
            assert len(await store.catalog('fixture',1000,1,ids[1]))==len(old_fields)
            # A new packet COPY error must roll back the new run and its facts.
            await pool.execute("ALTER TABLE field_catalog_batches ADD CONSTRAINT fixture_reject CHECK (normalizer_version <> 'fixture-fail')")
            changed=outputs[1].model_copy(update={'normalizer_version':'fixture-fail',
                'facts':[f.model_copy(update={'fact_id':uuid4()}) for f in outputs[1].facts]})
            with pytest.raises(asyncpg.CheckViolationError):await store.record_normalization(ids[1],changed)
            for table in ('normalization_runs','facts','field_catalog_batches'):
                assert await pool.fetchval(f"SELECT count(*) FROM {table} WHERE normalizer_version='fixture-fail'")==0
            assert store.raw.read(before['raw_sha256'])==raw
            # Exercise the actual route against the private Store, without app lifespan/migrations.
            app=make_app(Settings.from_env());app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                    headers={'authorization':'Bearer '+Settings.from_env().api_key}) as client:
                response=await client.get('/v1/catalog',params={'source_kind':'fixture','capture_id':str(ids[1]),'limit':2})
                assert response.status_code==200
                body=response.json(); assert len(body['items'])==2 and body['has_more'] and body['truncated']
                assert body['capture_limit']==1 and body['capture_id']==str(ids[1])
        finally:
            if pool:await pool.close()
            assert schema.startswith('catalog_fixture_') and len(schema)==48
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
            await admin.close()
    asyncio.run(run())

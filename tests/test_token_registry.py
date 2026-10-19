"""Registry identity and real SQL/API acceptance; fixtures never call GMGN."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import pytest
from midas_data.contracts import CaptureMetadata
from midas_data.normalize import normalize_capture
from midas_data.token_registry import TokenRegistry, identities

def meta(endpoint='market_rank',chain='sol',source='fixture'):
    return CaptureMetadata(capture_id=uuid4(),source_kind=source,endpoint=endpoint,chain=chain,
        requested_at='2026-09-09T12:00:00Z',received_at='2026-09-09T12:00:01Z',
        http_status=200,evidence_kind='original_response')

def extract(metadata,body):
    raw=json.dumps(body).encode()
    result=normalize_capture(metadata,raw)
    return identities(metadata,[f.model_dump() for f in result.fields],[i.model_dump() for i in result.issues],raw)

@pytest.mark.parametrize('endpoint,body,pointer',[
    ('market_rank',{'rank':[{'address':'TokenOnly'}]},'/rank/0'),
    ('trenches',{'data':{'new_creation':[{'address':'TokenOnly'}]}},'/data/new_creation/0'),
    ('token_info',{'address':'TokenOnly'},'')])
def test_address_only_tokens_register_without_economic_facts(endpoint,body,pointer):
    assert extract(meta(endpoint),body)==[('sol','TokenOnly',pointer)]

def test_identity_namespace_case_dedup_and_wallet_roles():
    address='0x'+'A'*40
    body={'rank':[{'address':address,'dev':{'address':'Developer'}},{'address':address.lower()}]}
    assert len(extract(meta(chain='base'),body))==1
    assert extract(meta(chain='base'),body)[0][1]==address.lower()
    assert len(extract(meta(chain='sol'),body))==2

@pytest.mark.parametrize('body',[
    {'chain':'base','rank':[{'address':'Token'}]},
    {'rank':[{'address':'Token','chain':'base'}]},
    {'rank':[{'address':'  whitespace'}]},
    {'rank':[{'dev':{'address':'Developer'}}]},
    {'code':1,'data':{'rank':[{'address':'Token'}]}}])
def test_rejected_identity_or_non_token_roles_do_not_register(body):
    assert extract(meta(),body)==[]

integration=pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private PostgreSQL opt-in')

@asynccontextmanager
async def database(tmp_path):
    import asyncpg
    from midas_data.settings import Settings
    from midas_data.store import Store
    from midas_data.raw_store import RawStore
    settings=Settings.from_env();schema='registry_fixture_'+uuid4().hex
    admin=await asyncpg.connect(settings.database_dsn,command_timeout=15);pool=None
    try:
        assert await admin.fetchval('SELECT current_database()')=='midas_data'
        await admin.execute(f'CREATE SCHEMA {schema}')
        pool=await asyncpg.create_pool(settings.database_dsn,min_size=1,max_size=3,
            command_timeout=15,server_settings={'search_path':schema,'timezone':'UTC'})
        async with pool.acquire() as con:
            for path in sorted(Path('midas_data/migrations').glob('*.sql')):
                await con.execute(path.read_text(encoding='utf-8'))
        store=Store('fixture',RawStore(tmp_path,min_free_bytes=0));store.pool=pool
        async def capture(body,endpoint='market_rank',chain='sol',source='fixture',publish=True):
            m=meta(endpoint,chain,source);raw=json.dumps(body).encode()
            await store.accept_capture(m,raw)
            result=normalize_capture(m,raw)
            if publish:await store.record_normalization(m.capture_id,result)
            return m,result
        yield store,capture,settings
    finally:
        if pool:await pool.close()
        assert schema.startswith('registry_fixture_') and len(schema)==49
        await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        await admin.close()

@pytest.mark.integration
@integration
def test_registration_survives_exit_reentry_reopen_and_respects_source_time_pagination(tmp_path):
    async def run():
        import httpx
        from midas_data.api import make_app
        async with database(tmp_path) as (store,capture,settings):
            old,_=await capture({'rank':[{'address':'Old'}]})
            await store.pool.execute("UPDATE captures SET recorded_at=(SELECT started_at-interval '1 second' FROM token_registry_config) WHERE capture_id=$1",old.capture_id)
            first,_=await capture({'rank':[{'address':'TokenA'},{'address':'TokenA'}]})
            unpublished,result=await capture({'rank':[{'address':'Pending'}]},publish=False)
            before=await store.pool.fetchval('SELECT clock_timestamp()')
            registry=TokenRegistry(store)
            assert await registry.step()==1
            assert await store.pool.fetchval('SELECT count(*) FROM token_registry')==1
            original=dict(await store.pool.fetchrow('SELECT * FROM token_registry'))
            assert original['item_pointer']=='/rank/0' and original['capture_id']==first.capture_id
            await capture({'rank':[{'address':'TokenB'}]})  # TokenA exits the observed ranking.
            assert await TokenRegistry(store).step()==1
            assert await store.pool.fetchval("SELECT count(*) FROM token_registry WHERE token_address='TokenA'")==1
            await capture({'rank':[{'address':'TokenA'}]})  # Reentry must not overwrite provenance.
            await capture({'rank':[{'address':'TokenA'}]},source='gmgn')
            assert await registry.step()==2
            assert dict(await store.pool.fetchrow("SELECT * FROM token_registry WHERE registry_id=$1",original['registry_id']))==original
            assert await TokenRegistry(store).step()==0
            await store.record_normalization(unpublished.capture_id,result)
            assert await registry.step()==1
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                    headers={'authorization':'Bearer '+settings.api_key}) as client:
                cutoff=(await client.get('/v1/clock')).json()['knowledge_cutoff']
                params={'source_kind':'fixture','chain':'sol','knowledge_cutoff':cutoff,'limit':1}
                first_page=await client.get('/v1/tokens',params=params)
                assert first_page.status_code==200,first_page.text
                data=first_page.json()
                assert data['items'][0]['token_address']=='TokenA' and data['has_more']
                assert data['tracking_enabled'] is False
                second=await client.get('/v1/tokens',params=dict(params,after_registry_id=data['next_after_registry_id']))
                assert second.json()['items'][0]['token_address']=='TokenB'
                assert (await client.get('/v1/tokens',params=dict(params,knowledge_cutoff=before.isoformat()))).json()['items']==[]
                for target,count in [('TokenA',1),('Missing',0),('Old',0)]:
                    assert len((await client.get('/v1/tokens',params=dict(params,token=target))).json()['items'])==count
                wrong=await client.get('/v1/tokens',params=dict(params,source_kind='legacy_gmgn'))
                assert wrong.json()['items']==[]
                assert (await client.get('/v1/tokens',params=dict(params,limit=201))).status_code==422
                assert (await client.get('/v1/tokens',params=dict(params,knowledge_cutoff=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))).status_code==422
                assert (await client.get('/v1/tokens',params=dict(params,knowledge_cutoff='2026-09-09T12:00:00'))).status_code==422
            assert await store.pool.fetchval('SELECT count(*) FROM facts')==0  # Registry did not invent facts.
    asyncio.run(run())

@pytest.mark.integration
@integration
def test_failed_checkpoint_rolls_back_tokens_retries_and_allows_other_captures(tmp_path):
    async def run():
        async with database(tmp_path) as (store,capture,settings):
            failed,_=await capture({'rank':[{'address':'RetryToken'}]})
            registry=TokenRegistry(store)
            await store.pool.execute("""CREATE FUNCTION reject_registry_completion() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.completed_at IS NOT NULL THEN RAISE EXCEPTION 'fixture checkpoint failure'; END IF; RETURN NEW; END $$;
                CREATE TRIGGER reject_registry_completion BEFORE INSERT OR UPDATE ON token_registry_runs
                FOR EACH ROW EXECUTE FUNCTION reject_registry_completion();""")
            with pytest.raises(Exception,match='fixture checkpoint failure'):await registry.step()
            assert await store.pool.fetchval('SELECT count(*) FROM token_registry')==0
            assert (await registry.status())['queue']['errors']==1
            await store.pool.execute('DROP TRIGGER reject_registry_completion ON token_registry_runs')
            await capture({'rank':[{'address':'OtherToken'}]})
            assert await registry.step()==1  # Failed first capture is deferred durably.
            assert await TokenRegistry(store).step()==0
            await store.pool.execute("UPDATE token_registry_runs SET retry_at=clock_timestamp()-interval '1 second'")
            assert await TokenRegistry(store).step()==1
            snapshot=await store.pool.fetch('SELECT * FROM token_registry ORDER BY registry_id')
            assert len(snapshot)==2 and (await registry.status())['queue']['pending']==0
            # Repeating a committed registration cannot duplicate or alter its origin.
            await registry.publish(failed.capture_id,'gmgn-p0-v4','fixture',[('sol','RetryToken','/rank/0')])
            assert await store.pool.fetch('SELECT * FROM token_registry ORDER BY registry_id')==snapshot
    asyncio.run(run())

@pytest.mark.integration
@integration
def test_missing_raw_is_retryable_and_identity_conflicts_are_not_promoted(tmp_path):
    async def run():
        async with database(tmp_path) as (store,capture,settings):
            missing,_=await capture({'rank':[{'address':'Unavailable'}]})
            registry=TokenRegistry(store);original_read=store.raw.read
            with patch.object(store.raw,'read',side_effect=OSError('fixture missing evidence')):
                with pytest.raises(OSError):await registry.step()
            assert await store.pool.fetchval('SELECT count(*) FROM token_registry')==0
            await capture({'rank':[{'address':'Conflict','chain':'base'}]})
            assert await registry.step()==1
            assert await store.pool.fetchval('SELECT count(*) FROM token_registry')==0
            await store.pool.execute("UPDATE token_registry_runs SET retry_at=clock_timestamp()-interval '1 second'")
            assert await TokenRegistry(store).step()==1
            assert await store.pool.fetchval('SELECT count(*) FROM token_registry')==1
    asyncio.run(run())

@pytest.mark.integration
@integration
def test_batch_lookup_preserves_namespace_cutoff_and_bounds(tmp_path):
    async def run():
        import httpx
        from midas_data.api import make_app
        async with database(tmp_path) as (store,capture,settings):
            cutoff_before=await store.pool.fetchval('SELECT clock_timestamp()')
            await capture({'rank':[{'address':'LookupA'},{'address':'LookupB'}]})
            registry=TokenRegistry(store);assert await registry.step()==1
            cutoff=await store.pool.fetchval('SELECT clock_timestamp()')
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                    headers={'authorization':'Bearer '+settings.api_key}) as client:
                params=dict(source_kind='fixture',chain='sol',knowledge_cutoff=cutoff.isoformat(),addresses=['LookupA','Unknown'])
                response=await client.get('/v1/tokens/lookup',params=params)
                assert response.status_code==200,response.text
                rows=response.json()['items'];assert len(rows)==1 and rows[0]['token_address']=='LookupA'
                assert rows[0]['capture_id'] and rows[0]['registry_id']>0
                assert (await client.get('/v1/tokens/lookup',params=dict(params,source_kind='gmgn'))).json()['items']==[]
                assert (await client.get('/v1/tokens/lookup',params=dict(params,knowledge_cutoff=cutoff_before.isoformat()))).json()['items']==[]
                for addresses in (['LookupA']*2,[str(i) for i in range(101)],[' whitespace']):
                    assert (await client.get('/v1/tokens/lookup',params=dict(params,addresses=addresses))).status_code==422
    asyncio.run(run())

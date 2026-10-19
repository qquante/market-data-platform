"""Real SQL in private schemas; no live provider calls or production migrations."""
import asyncio
import json
from datetime import datetime,timedelta,timezone
from unittest.mock import patch,AsyncMock
from uuid import uuid4
import pytest

from midas_data.holders import parse_holders,PARSER_VERSION
from midas_data.holder_store import HolderStore
from midas_data.store import CaptureConflict,FutureCutoff
from test_holders_http import metadata
from test_token_registry import database,integration

pytestmark=[pytest.mark.integration,integration]


async def add(store,rows=None,**changes):
    m=metadata(**changes)
    raw=json.dumps({'code':0,'data':{'list':rows if rows is not None else [
        {'address':'Wallet','balance':'1.25','usd_value':None},{'address':'Wallet2','balance':'0'}]}}).encode()
    await store.accept_capture(m,raw)
    return m,parse_holders(m,raw)


def test_sql_provenance_temporal_pagination_idempotency_and_reopen(tmp_path):
    async def run():
        async with database(tmp_path) as (store,_,settings):
            m,r=await add(store)
            hs=HolderStore(store);before=await store.pool.fetchval('SELECT clock_timestamp()')
            receipt=await hs.record(r);cutoff=await store.pool.fetchval('SELECT clock_timestamp()')
            assert await hs.snapshot(m.capture_id,'fixture',before) is None
            assert await hs.snapshot(m.capture_id,'gmgn',cutoff) is None
            one=await hs.snapshot(m.capture_id,'fixture',cutoff,1)
            assert one['has_more'] and one['items'][0]['values']['usd_value']['quality']=='missing'
            two=await HolderStore(store).snapshot(m.capture_id,'fixture',cutoff,1,one['next_after_ordinal'])
            assert two['items'][0]['wallet_address']=='Wallet2' and not two['has_more']
            again=await hs.record(r)
            assert again['duplicate'] and again['available_at']==receipt['available_at']
            assert await store.pool.fetchval('SELECT count(*) FROM facts')==0
            assert await store.pool.fetchval('SELECT count(*) FROM holder_observations')==2
            assert await store.pool.fetchval('SELECT consumed_at IS NOT NULL FROM outbox WHERE capture_id=$1',m.capture_id)
            listing=await hs.listing('fixture','sol','Token',m.received_at-timedelta(seconds=2),
                m.received_at+timedelta(seconds=2),cutoff)
            assert len(listing['items'])==1 and listing['items'][0]['accepted_rows']==2
            assert listing['items'][0]['raw_sha256']==r.raw_sha256
            import httpx
            from midas_data.api import make_app
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture') as c:
                path=f'/v1/holders/{m.capture_id}'
                assert (await c.get(path)).status_code==401
                headers={'Authorization':'Bearer '+settings.api_key}
                params={'source_kind':'fixture','knowledge_cutoff':cutoff.isoformat(),'limit':1}
                response=await c.get(path,headers=headers,params=params)
                assert response.status_code==200 and response.json()['has_more']
                assert (await c.get(path,headers=headers,params={**params,'limit':101})).status_code==422
                assert (await c.get(path,headers=headers,params={**params,'knowledge_cutoff':(cutoff+timedelta(days=1)).isoformat()})).status_code==422
                assert (await c.get(path,headers=headers,params={**params,'source_kind':'gmgn'})).status_code==404
    asyncio.run(run())


def test_conflicting_output_and_failed_copy_roll_back_every_publication(tmp_path):
    async def run():
        import asyncpg
        async with database(tmp_path) as (store,_,_settings):
            m,r=await add(store)
            hs=HolderStore(store)
            # COPY unique violation must roll back run, catalogue, rows and ACK.
            broken=r.model_copy(update={'holders':[r.holders[0],r.holders[0]]})
            with pytest.raises(asyncpg.UniqueViolationError): await hs.record(broken)
            for table in ['normalization_runs','holder_runs','holder_observations','field_catalog_batches']:
                assert await store.pool.fetchval(f'SELECT count(*) FROM {table}')==0
            assert await store.pool.fetchval('SELECT consumed_at FROM outbox WHERE capture_id=$1',m.capture_id) is None
            await hs.record(r)
            with pytest.raises(CaptureConflict):await hs.record(r.model_copy(update={'holders':[]}))
            wrong=r.model_copy(update={'raw_sha256':'a'*64})
            with pytest.raises(CaptureConflict):await hs.record(wrong)
            assert await store.pool.fetchval('SELECT count(*) FROM holder_observations')==2
    asyncio.run(run())


def test_concurrent_replay_publishes_once_and_empty_differs_from_quarantine(tmp_path):
    async def run():
        async with database(tmp_path) as (store,_,_settings):
            m,r=await add(store,rows=[])
            results=await asyncio.gather(*(HolderStore(store).record(r) for _ in range(3)))
            assert sum(not x['duplicate'] for x in results)==1
            cutoff=await store.pool.fetchval('SELECT clock_timestamp()')
            empty=await HolderStore(store).snapshot(m.capture_id,'fixture',cutoff)
            assert empty['run']['state']=='ready' and empty['run']['returned_rows']==0
            bad=metadata();raw=b'{"code":0,"data":{}}'
            await store.accept_capture(bad,raw);await HolderStore(store).record(parse_holders(bad,raw))
            cutoff=await store.pool.fetchval('SELECT clock_timestamp()')
            failed=await HolderStore(store).snapshot(bad.capture_id,'fixture',cutoff)
            assert failed['run']['state']=='quarantined' and failed['run']['returned_rows'] is None
    asyncio.run(run())

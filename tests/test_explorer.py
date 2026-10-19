"""Explorer precision, pagination, source/cutoff isolation and read-only behavior."""
import asyncio
from datetime import datetime,timedelta,timezone
from pathlib import Path
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from midas_data.api import make_app
from midas_data.explorer import raw_fields,RawFieldsError
from midas_data.settings import Settings


def settings():
    return Settings(database_dsn='fixture',lake_catalog_dsn='fixture',api_key='explorer-fixture',nats_url='fixture',
        clickhouse_url='fixture',clickhouse_user='fixture',clickhouse_password='fixture',raw_path=Path('/tmp/unused'),lake_path='/tmp/unused')


def test_raw_numbers_null_zero_false_and_empty_containers_remain_distinct():
    raw=b'{"a/b":{"~key":123456789012345678901234567890},"price":1.23000000000000000001e-19,"null":null,"zero":0,"false":false,"list":[],"object":{},"string":"001.230"}'
    rows=raw_fields(raw)['items'];fields={r['pointer']:r for r in rows}
    assert fields['/a~1b/~0key']['value_text']=='123456789012345678901234567890'
    assert fields['/price']['value_text']=='1.23000000000000000001e-19'
    assert fields['/price']['value_type']=='number' and fields['/string']['value_type']=='string'
    assert fields['/null']['value_text']=='null' and fields['/false']['value_text']=='false'
    assert fields['/zero']['value_text']=='0' and fields['/list']['value_text']=='[]' and fields['/object']['value_text']=='{}'


def test_raw_paging_and_search_never_omit_matches():
    raw=b'{"tokens":[{"address":"A","price":1},{"address":"B","price":2}]}'
    assert raw_fields(raw,query='price')['total']==2
    a=raw_fields(raw,query='price',limit=1);b=raw_fields(raw,query='price',offset=1,limit=1)
    assert a['has_more'] and not b['has_more'] and a['items'][0]['pointer']!=b['items'][0]['pointer']
    assert raw_fields(raw,query='address')['total']==2
    assert raw_fields(raw,query='unknown')['total']==0


@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'{"a":NaN}',b'not json',b'\xff',b'['*34+b'0'+b']'*34])
def test_invalid_or_ambiguous_raw_is_not_silently_rewritten(raw):
    with pytest.raises(RawFieldsError):raw_fields(raw)


def test_raw_page_byte_budget_does_not_truncate_values():
    with pytest.raises(RawFieldsError,match='field_page_byte_budget'):raw_fields(b'{"s":"'+b'a'*(1024*1024)+b'"}')


def test_routes_auth_query_validation_and_new_ui_modules_without_database():
    async def run():
        app=make_app(settings());app.state.store=SimpleNamespace(pool=SimpleNamespace(fetch=AsyncMock(return_value=[])))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture') as c:
            assert (await c.get('/v1/explorer/captures')).status_code==401
            for path in ['/ui/explorer.js','/ui/research_views.js']:
                r=await c.get(path);assert r.status_code==200 and 'frame-ancestors' in r.headers['content-security-policy']
            c.headers['Authorization']='Bearer explorer-fixture'
            at=datetime.now(timezone.utc);base={'knowledge_cutoff':at.isoformat(),'source_kind':'fixture','start':(at-timedelta(days=1)).isoformat(),'end':at.isoformat()}
            r=await c.get('/v1/explorer/captures',params=base);assert r.status_code==200 and r.json()['items']==[]
            for change in [{'limit':201},{'before_at':at.isoformat()},{'start':(at-timedelta(days=8)).isoformat()},{'knowledge_cutoff':(at+timedelta(days=1)).isoformat()}]:
                assert (await c.get('/v1/explorer/captures',params={**base,**change})).status_code==422
            assert (await c.post('/v1/explorer/captures',json={})).status_code==405
    asyncio.run(run())


@pytest.mark.integration
@pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='private PostgreSQL opt-in')
def test_private_sql_capture_cursor_fields_and_facts_respect_source_cutoff(tmp_path):
    from test_token_registry import database
    async def run():
        async with database(tmp_path) as (store,capture,config):
            first,_=await capture({'rank':[{'address':'TokenFirst','market_cap':'123.456789012345678901'}]})
            middle=await store.pool.fetchval('SELECT clock_timestamp()')
            second,_=await capture({'rank':[{'address':'TokenSecond','market_cap':'456'}]})
            await capture({'rank':[{'address':'OtherSource','market_cap':'99'}]},source='gmgn')
            cutoff=await store.pool.fetchval('SELECT clock_timestamp()');app=make_app(config);app.state.store=store
            base={'source_kind':'fixture','knowledge_cutoff':cutoff.isoformat(),'start':(cutoff-timedelta(days=1)).isoformat(),'end':cutoff.isoformat(),'limit':1}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture',headers={'Authorization':'Bearer '+config.api_key}) as c:
                one=(await c.get('/v1/explorer/captures',params=base)).json();assert one['items'][0]['capture_id']==str(second.capture_id)
                two=(await c.get('/v1/explorer/captures',params={**base,**one['next_cursor']})).json();assert two['items'][0]['capture_id']==str(first.capture_id) and not two['has_more']
                route='/v1/explorer/captures/'+str(first.capture_id)
                args={'source_kind':'fixture','knowledge_cutoff':cutoff.isoformat()}
                r=await c.get(route+'/fields',params=args);assert r.status_code==200
                assert any(v['value_text']=='123.456789012345678901' for v in r.json()['items'])
                assert (await c.get(route+'/fields',params={**args,'source_kind':'gmgn'})).status_code==404
                assert (await c.get('/v1/explorer/captures/'+str(second.capture_id)+'/fields',params={**args,'knowledge_cutoff':middle.isoformat()})).status_code==404
                r=await c.get(route+'/facts',params=args);assert r.status_code==200 and r.json()['items']
                assert any(v['value_text']=='123.456789012345678901' for v in r.json()['items'])
                assert (await c.get(route+'/facts',params={**args,'source_kind':'gmgn'})).json()['items']==[]
                r=(await c.get('/v1/explorer/captures',params={**base,'endpoint':'token_kline'})).json();assert r['items']==[]
    asyncio.run(run())

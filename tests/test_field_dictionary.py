"""Dictionary cannot turn captured values, wrong types or later contracts into evidence."""
import asyncio,json,os
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from midas_data.field_dictionary import dictionary_page,assess_field,_catalog,VERSION
from midas_data.explorer import raw_fields,interpreted_fields
from midas_data.normalize import normalize_capture
from midas_data.api import make_app
from test_token_registry import meta
from test_explorer import settings


def interpreted(body,endpoint='token_info'):
    metadata=meta(endpoint);raw=json.dumps(body).encode();result=normalize_capture(metadata,raw)
    row={'issues':[i.model_dump() for i in result.issues],'normalizer_version':result.normalizer_version,
        'fields':[f.model_dump() for f in result.fields],'raw_sha256':'fixture','endpoint':endpoint,'chain':'sol'}
    return interpreted_fields(SimpleNamespace(raw=SimpleNamespace(read=lambda _:raw)),row,limit=200)


def test_catalog_is_closed_unique_and_keeps_explicit_dispositions():
    doc,index=_catalog();assert len(index)==len(doc['fields'])
    assert {r['endpoint'] for r in doc['fields']}=={'market_rank','trenches','token_info','token_top_holders','token_kline'}
    for r in doc['fields']:
        assert r['meaning'] and r['restriction'] and r['storage'] and r['null_policy']
        if r['usage']=='metric':assert r['unit'] and r['adapter_contract_ids']
    assert any(r['semantic_status']=='conflicting_documentation' for r in doc['fields'])


def test_schema_wildcard_only_applies_to_real_arrays_not_numeric_object_keys():
    r=raw_fields(b'{"data":{"0":{"address":1},"list":[{"address":2}]}}')
    assert [x['schema_path'] for x in r['items']]==['/data/0/address','/data/list/*/address']


def test_valid_adapter_numbers_qualify_but_unknown_fields_null_and_wrong_types_do_not():
    r=interpreted({'code':0,'data':{'address':'Token','circulating_supply':'123.456789012345678901',
        'price':{'price':'0.01234567890123456789'},'holder_count':None,'new_field':55,'total_supply':'100'}})
    rows={x['pointer']:x for x in r['items']}
    assert rows['/data/price/price']['interpretation']['calculation_allowed']
    assert rows['/data/circulating_supply']['value_text']=='123.456789012345678901'
    assert not rows['/data/total_supply']['interpretation']['calculation_allowed']
    assert rows['/data/holder_count']['interpretation']['quality']=='missing'
    assert not rows['/data/new_field']['interpretation']['calculation_allowed']
    assert '/data/new_field' in r['quality_summary']['uncatalogued_paths']


@pytest.mark.parametrize('bad',[True,{'value':10},[10],'1.5','NaN',''])
def test_invalid_holder_count_never_qualifies(bad):
    r=interpreted({'code':0,'data':{'address':'Token','holder_count':bad}})
    for row in r['items']:
        if 'holder_count' in row['pointer']:assert not row['interpretation']['calculation_allowed']


def test_capture_identity_conflict_blocks_every_numeric_occurrence():
    r=interpreted({'code':0,'data':{'address':'Token','chain':'base','holder_count':10}})
    assert r['quality_summary']['calculation_allowed_occurrences']==0


def test_search_and_pagination_cannot_hide_schema_drift():
    raw=b'{"code":0,"data":{"price":{"price":"1.2"},"brand_new":3}}'
    a=raw_fields(raw,endpoint='token_info',query='price',limit=1)
    b=raw_fields(raw,endpoint='token_info',offset=2,limit=1)
    assert a['quality_summary']==b['quality_summary']
    assert a['quality_summary']['uncatalogued_path_count']==1


def test_no_adapter_no_units_alias_or_chain_shortcut():
    f={'schema_path':'/data/list/*/native_balance','value_type':'number','value_text':'1000000000'}
    assert not assess_field('token_top_holders',f,chain='sol',accepted=True)['calculation_allowed']
    obs={'quality':'valid','contract_id':'gmgn.token_top_holders.v1:native_balance'}
    assert assess_field('token_top_holders',f,obs,chain='sol',accepted=True)['unit']=='lamport'
    assert assess_field('token_top_holders',f,obs,chain='sol',accepted=True)['calculation_allowed']
    assert not assess_field('token_top_holders',f,obs,chain='base',accepted=True)['calculation_allowed']
    assert not assess_field('token_top_holders',f,{**obs,'contract_id':'another-version'},chain='sol',accepted=True)['calculation_allowed']


def test_type_change_of_known_metadata_is_drift_not_new_semantic_contract():
    f={'schema_path':'/data/logo','value_type':'boolean','value_text':'true'}
    a=assess_field('token_info',f,chain='sol',accepted=True)
    assert a['schema_drift'] and a['reason']=='json_type_drift' and not a['calculation_allowed']


def test_dictionary_filter_pages_are_complete_and_isolated():
    rows=[];offset=0
    while True:
        r=dictionary_page(endpoint='token_top_holders',usage='blocked',offset=offset,limit=7)
        rows.extend(r['items'])
        if not r['has_more']:break
        offset+=7
    assert len(rows)==r['total']==len({r['id'] for r in rows})
    assert all(r['endpoint']=='token_top_holders' and r['usage']=='blocked' for r in rows)
    assert dictionary_page(query='native_balance')['total']==1


def test_readonly_routes_validation_and_bounded_coverage_cache():
    async def run():
        app=make_app(settings());pool=SimpleNamespace(fetchval=AsyncMock(side_effect=[datetime.now(timezone.utc),None,None,None,None,None]))
        app.state.store=SimpleNamespace(pool=pool)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture') as c:
            assert (await c.get('/v1/dictionary')).status_code==401
            c.headers['Authorization']='Bearer explorer-fixture'
            for path in ['/v1/dictionary?limit=101','/v1/dictionary?offset=-1','/v1/dictionary?endpoint=wallet_history']:
                assert (await c.get(path)).status_code==422
            r=await c.get('/v1/dictionary');assert r.status_code==200 and r.json()['items']
            assert (await c.post('/v1/dictionary',json={})).status_code==405
            one=await c.get('/v1/dictionary/coverage');two=await c.get('/v1/dictionary/coverage')
            assert one.status_code==two.status_code==200 and one.json()==two.json()
            assert two.headers['x-read-cache']=='hit' and pool.fetchval.await_count==6
    asyncio.run(run())


@pytest.mark.integration
@pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='isolated PostgreSQL opt-in')
def test_live_sql_qualification_uses_published_run_and_source_cutoff(tmp_path):
    from test_token_registry import database
    async def run():
        async with database(tmp_path) as (store,capture,config):
            old=await store.pool.fetchval('SELECT clock_timestamp()')
            m,result=await capture({'code':0,'data':{'address':'Token','holder_count':12,'new_signal':9}},endpoint='token_info')
            cutoff=await store.pool.fetchval('SELECT clock_timestamp()');app=make_app(config);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture',headers={'Authorization':'Bearer '+config.api_key}) as c:
                route='/v1/explorer/captures/'+str(m.capture_id)+'/fields'
                args={'source_kind':'fixture','knowledge_cutoff':cutoff.isoformat(),'query':'holder_count'}
                r=await c.get(route,params=args);assert r.status_code==200,r.text
                data=r.json();assert data['items'][0]['interpretation']['calculation_allowed']
                assert data['quality_summary']['uncatalogued_path_count']==1
                assert (await c.get(route,params={**args,'knowledge_cutoff':old.isoformat()})).status_code==404
                assert (await c.get(route,params={**args,'source_kind':'gmgn'})).status_code==404
    asyncio.run(run())

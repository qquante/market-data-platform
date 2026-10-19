"""API + real PostgreSQL in a private schema; no GMGN calls."""
import asyncio
from datetime import datetime,timedelta,timezone
from pathlib import Path
import os
from uuid import uuid4
import pytest

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]


def test_valuation_route_respects_capture_version_item_and_knowledge_cutoff(tmp_path):
    async def run():
        import asyncpg,httpx,json
        from midas_data.api import make_app
        from midas_data.contracts import CaptureMetadata
        from midas_data.normalize import normalize_capture
        from midas_data.raw_store import RawStore
        from midas_data.settings import Settings
        from midas_data.store import Store
        settings=Settings.from_env();schema='valuation_fixture_'+uuid4().hex
        admin=await asyncpg.connect(settings.database_dsn,command_timeout=15);pool=None
        try:
            assert await admin.fetchval('SELECT current_database()')=='midas_data'
            await admin.execute(f'CREATE SCHEMA {schema}')
            pool=await asyncpg.create_pool(settings.database_dsn,min_size=1,max_size=2,command_timeout=15,
                server_settings={'search_path':schema,'timezone':'UTC'})
            async with pool.acquire() as con:
                for path in sorted(Path('midas_data/migrations').glob('*.sql')):
                    await con.execute(path.read_text(encoding='utf-8'))
            store=Store('fixture',RawStore(tmp_path,min_free_bytes=0));store.pool=pool
            token='0x'+'1'*40
            async def capture(endpoint,data):
                meta=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint=endpoint,chain='base',
                    requested_at='2026-09-09T12:00:00Z',received_at='2026-09-09T12:00:01Z',
                    http_status=200,evidence_kind='original_response')
                raw=json.dumps({'data':data}).encode();await store.accept_capture(meta,raw)
                result=normalize_capture(meta,raw);await store.record_normalization(meta.capture_id,result)
                return meta,result
            meta,original=await capture('token_info',{'address':token,
                'price':{'price':'2.5000','volume_5m':'12.3400'},'circulating_supply':'100',
                'liquidity':'45.678900','decimals':9,'total_supply':'200','max_supply':'300'})
            first=next(f for f in original.facts if f.metric=='price')
            cutoff=await pool.fetchval('SELECT clock_timestamp()')
            second_meta,second=await capture('token_info',{'address':token,'price':{'price':'3'}})
            absent=next(f for f in second.facts if f.metric=='price')
            # A later interpretation of the same capture cannot provide an operand to the old fact.
            altered=original.model_copy(update={'normalizer_version':'fixture-new-interpretation',
                'facts':[f.model_copy(update={'fact_id':uuid4(),'value_text':'999'}) for f in original.facts]})
            await store.record_normalization(meta.capture_id,altered)
            # Same token twice, in different list items: second item's cap cannot fill the first.
            _,rank=await capture('market_rank',{'rank':[{'address':token,'price':'1'},
                {'address':token,'price':'2','market_cap':'999'}]})
            rank_anchor=next(f for f in rank.facts if f.pointer=='/data/rank/0/price')
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                    headers={'authorization':'Bearer '+settings.api_key}) as client:
                async def query(fid,cut=None,source='fixture'):
                    return await client.get(f'/v1/observations/{fid}/valuation',params={
                        'source_kind':source,'knowledge_cutoff':(cut or datetime.now(timezone.utc)).isoformat()})
                before=await pool.fetchval('SELECT count(*) FROM facts')
                reply=await query(first.fact_id,cutoff);assert reply.status_code==200,reply.text
                result=reply.json();cap=result['valuation']['market_cap']
                assert cap['state']=='available' and cap['value_text']=='250.0000' and cap['kind']=='derived'
                assert result['context']['source']['capture_id']==str(meta.capture_id)
                assert result['valuation']['normalizer_version']==original.normalizer_version
                fields=result['valuation']['economic_inputs']['fields']
                assert fields['liquidity']['state']=='available' and fields['liquidity']['value_text']=='45.678900'
                assert fields['volume_5m']['state']=='available' and fields['volume_5m']['value_text']=='12.3400'
                assert fields['decimals']['value_text']=='9' and fields['total_supply']['state']=='unavailable'
                assert fields['total_supply']['reason']==fields['max_supply']['reason']=='input_unverified'
                # New fields also stay in the original interpretation after a later revision.
                later=(await query(first.fact_id)).json()['valuation']['economic_inputs']['fields']
                assert later==fields
                no_context=(await query(absent.fact_id)).json()['valuation']['economic_inputs']['fields']
                assert no_context['liquidity']['state']=='unavailable' and no_context['volume_5m']['state']=='unavailable'
                assert (await query(first.fact_id)).json()['valuation']['market_cap']['value_text']=='250.0000'
                assert (await query(absent.fact_id)).json()['valuation']['market_cap']['state']=='unavailable'
                assert (await query(rank_anchor.fact_id)).json()['valuation']['market_cap']['state']=='unavailable'
                assert (await query(absent.fact_id,cutoff)).status_code==404
                assert (await query(altered.facts[0].fact_id,cutoff)).status_code==404
                assert (await query(first.fact_id,source='gmgn')).status_code==404
                assert (await query(first.fact_id,datetime.now(timezone.utc)+timedelta(days=1))).status_code==422
                assert (await client.get(f'/v1/observations/{first.fact_id}/valuation',params={
                    'source_kind':'fixture','knowledge_cutoff':'2026-09-09T12:00:00'})).status_code==422
                assert await pool.fetchval('SELECT count(*) FROM facts')==before
                # Missing raw is an explicit evidence error, not a fallback or a partial valuation.
                store.raw._path((await store.get_capture(meta.capture_id))['raw_sha256']).unlink()
                failure=await query(first.fact_id)
                assert failure.status_code==503 and failure.json()=={'detail':'Valuation evidence unavailable'}
        finally:
            if pool:await pool.close()
            assert schema.startswith('valuation_fixture_') and len(schema)==len('valuation_fixture_')+32
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE');await admin.close()
    asyncio.run(run())

import asyncio,json
from datetime import timedelta
import httpx,pytest
from midas_data.normalize import normalize_capture
from midas_data.api import make_app
from test_candles import metadata,candle
from test_token_registry import database,integration
pytestmark=[pytest.mark.integration,integration]

def test_candle_reader_preserves_raw_source_cutoff_and_zero_fact_projection(tmp_path):
    async def run():
        async with database(tmp_path) as (store,_,settings):
            m=metadata();raw=json.dumps({'code':0,'data':{'list':[candle()]}}).encode()
            await store.accept_capture(m,raw);await store.record_normalization(m.capture_id,normalize_capture(m,raw))
            cutoff=await store.pool.fetchval('SELECT clock_timestamp()')
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture') as c:
                url='/v1/candles/'+str(m.capture_id);q={'source_kind':'fixture','knowledge_cutoff':cutoff.isoformat()}
                assert (await c.get(url,params=q)).status_code==401
                c.headers['Authorization']='Bearer '+settings.api_key
                r=await c.get(url,params=q);assert r.status_code==200,r.text
                assert r.json()['coverage']['eligible_rows']==1
                for changed in [dict(q,source_kind='gmgn'),dict(q,knowledge_cutoff=m.received_at.isoformat())]:
                    assert (await c.get(url,params=changed)).status_code==404
                assert (await c.get(url,params=dict(q,knowledge_cutoff=(cutoff+timedelta(hours=1)).isoformat()))).status_code==422
                assert await store.pool.fetchval('SELECT count(*) FROM facts WHERE capture_id=$1',m.capture_id)==0
    asyncio.run(run())


def test_candle_reclassification_preserves_old_version_and_cutoff(tmp_path):
    from midas_data.normalize import _normalize_capture
    async def run():
        async with database(tmp_path) as (store,_,settings):
            m=metadata();raw=json.dumps({'code':0,'data':{'list':[candle()]}}).encode()
            await store.accept_capture(m,raw)
            legacy=_normalize_capture(m,raw)
            first=await store.record_normalization(m.capture_id,legacy)
            before=await store.pool.fetchval('SELECT clock_timestamp()')
            old=await store.get_capture(m.capture_id)
            assert old['normalization_state']=='quarantined'
            old_run=dict(old['runs'][0])
            latest=normalize_capture(m,raw)
            await store.record_normalization(m.capture_id,latest)
            await store.record_normalization(m.capture_id,latest)
            await store.record_normalization(m.capture_id,legacy)
            current=await store.get_capture(m.capture_id)
            assert current['normalization_state']=='ready'
            assert current['raw_sha256']==old['raw_sha256']
            assert current['available_at']==old['available_at']
            assert len(current['runs'])==2
            assert dict(current['runs'][0])==old_run
            assert current['runs'][1]['available_at']>before
            assert await store.pool.fetchval('SELECT count(*) FROM facts WHERE capture_id=$1',m.capture_id)==0
            visible=await store.pool.fetch('SELECT normalizer_version FROM normalization_runs WHERE capture_id=$1 AND available_at<=$2',m.capture_id,before)
            assert [r['normalizer_version'] for r in visible]==['gmgn-p0-v4']
    asyncio.run(run())

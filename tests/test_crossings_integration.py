"""Real PostgreSQL in a private schema; crossings revisions and context snapshots."""
import asyncio
from datetime import datetime,timedelta,timezone
from pathlib import Path
import os
from uuid import uuid4
import pytest

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]


def test_crossings_api_revisions_gaps_utc_and_original_context(tmp_path):
    async def run():
        import asyncpg,httpx,json
        from midas_data.api import make_app
        from midas_data.contracts import CaptureMetadata
        from midas_data.normalize import normalize_capture
        from midas_data.raw_store import RawStore
        from midas_data.settings import Settings
        from midas_data.store import Store
        from midas_data.trajectory import METHODS
        settings=Settings.from_env();schema='crossings_fixture_'+uuid4().hex
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
            token='0x'+'a'*40
            start=(datetime.now(timezone.utc)-timedelta(days=2)).replace(hour=23,minute=59,second=0,microsecond=0)
            end=start+timedelta(minutes=20)
            async def capture(seconds,value,endpoint='token_info',source='fixture'):
                meta=CaptureMetadata(capture_id=uuid4(),source_kind=source,endpoint=endpoint,chain='base',
                    requested_at=start+timedelta(seconds=seconds-1),received_at=start+timedelta(seconds=seconds),
                    http_status=200,evidence_kind='original_response')
                data={'address':token,'price':{'price':'1','volume_5m':'123.4500'},
                    'circulating_supply':value,'liquidity':'567.89','decimals':9}
                if endpoint=='market_rank':data={'rank':[{'address':token,'price':'1','market_cap':value}]}
                raw=json.dumps({'data':data}).encode()
                await store.accept_capture(meta,raw);result=normalize_capture(meta,raw)
                await store.record_normalization(meta.capture_id,result)
                return meta,result
            first,_=await capture(0,'19000')
            peak,peak_run=await capture(30,'21000')
            low,_=await capture(60,'19000')
            last,_=await capture(90,'22000')
            await capture(120,'180000','market_rank')
            await capture(150,'260000','market_rank')
            await capture(20,'999999',source='legacy_gmgn')
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                    headers={'authorization':'Bearer '+settings.api_key}) as client:
                async def query(cut=None,**overrides):
                    params=dict(chain='BASE',token=token.upper().replace('0X','0x'),source_kind='fixture',
                        method=next(iter(METHODS)),start=start.isoformat(),end=end.isoformat(),
                        knowledge_cutoff=(cut or await pool.fetchval('SELECT clock_timestamp()')).isoformat(),
                        gap_after_seconds=120)
                    params.update(overrides)
                    return await client.get('/v1/trajectory/crossings',params=params)
                old_cutoff=await pool.fetchval('SELECT clock_timestamp()')
                reply=await query(old_cutoff);assert reply.status_code==200,reply.text
                old=reply.json();c=old['crossings']
                assert [e['kind'] for e in c['events']]==['upcross','downcross','reentry']
                assert len(c['segments'])==1 and len(c['context_snapshots'])==4
                event=c['events'][1]
                assert event['interval_start'][:10]!=event['interval_end'][:10]
                for snap in c['context_snapshots']:
                    point=next(p for p in old['points'] if p['point_id']==snap['point_id'])
                    reply=await client.get('/v1/observations/'+point['anchor_fact_id']+'/valuation',params={
                        'source_kind':'fixture','knowledge_cutoff':old_cutoff.isoformat()})
                    assert reply.status_code==200,reply.text
                    v=reply.json()['valuation'];ctx=snap['context']
                    assert ctx['price']==v['price'] and ctx['market_cap']==v['market_cap']
                    assert ctx['economic_inputs']==v['economic_inputs']['fields']
                    assert ctx['economic_inputs']['liquidity']['value_text']=='567.89'
                # Latest interpretation moves the old peak below the threshold.
                revised=peak_run.model_copy(update={'normalizer_version':'crossings-revision-1','facts':[
                    f.model_copy(update={'fact_id':uuid4(),'value_text':'18000' if f.metric=='circulating_supply' else f.value_text})
                    for f in peak_run.facts]})
                await store.record_normalization(peak.capture_id,revised)
                revised_cutoff=await pool.fetchval('SELECT clock_timestamp()')
                revised_view=(await query(revised_cutoff)).json()
                assert [e['kind'] for e in revised_view['crossings']['events']]==['upcross']
                assert (await query(old_cutoff)).json()==old
                # A late missing-supply capture between points breaks continuity without filling from older facts.
                await capture(75,None)
                latest=(await query()).json()
                assert latest['crossings']['events']==[]
                assert latest['coverage']['unavailable_points']==1
                assert (await query(revised_cutoff)).json()==revised_view
                assert (await query(old_cutoff)).json()==old
                rank=(await query(method='gmgn.market_rank.v4.market_cap')).json()['crossings']
                assert [e['level_text'] for e in rank['events']]==['200000','250000']
                assert rank['events'][0]['context_changes']['circulating_supply_changed'] is None
                assert len(rank['context_snapshots'])==2
                # Existing trajectory remains exactly the same without the optional crossings payload.
                reply=await client.get('/v1/trajectory',params=dict(chain='base',token=token,source_kind='fixture',
                    method=next(iter(METHODS)),start=start.isoformat(),end=end.isoformat(),
                    knowledge_cutoff=old_cutoff.isoformat(),gap_after_seconds=120))
                assert reply.status_code==200 and reply.json()=={k:v for k,v in old.items() if k!='crossings'}
                assert (await query(old_cutoff,max_events=2)).status_code==413
                assert (await query(old_cutoff,max_points=2)).status_code==413
                assert (await query(source_kind='gmgn')).json()['crossings']['events']==[]
                for override in ({'grid_version':'future-grid'},{'max_events':0},{'max_events':2001},
                    {'method':'all'},{'gap_after_seconds':0},
                    {'start':start.replace(tzinfo=None).isoformat()},
                    {'start':(start-timedelta(days=2)).isoformat()},
                    {'knowledge_cutoff':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}):
                    assert (await query(**override)).status_code==422,override
                assert (await client.get('/v1/trajectory/crossings',headers={'authorization':'invalid'})).status_code==401
                counts="SELECT (SELECT count(*) FROM captures),(SELECT count(*) FROM normalization_runs),(SELECT count(*) FROM facts),(SELECT count(*) FROM outbox)"
                before=await pool.fetchrow(counts)
                await query();await query(old_cutoff)
                assert await pool.fetchrow(counts)==before
        finally:
            if pool:await pool.close()
            assert schema.startswith('crossings_fixture_') and len(schema)==len('crossings_fixture_')+32
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE');await admin.close()
    asyncio.run(run())

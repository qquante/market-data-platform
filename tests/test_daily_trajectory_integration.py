"""UTC rollover and revised daily extrema via HTTP + isolated PostgreSQL."""
import asyncio
from datetime import datetime,timedelta,timezone
from pathlib import Path
import os
from uuid import uuid4
import pytest

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]


def test_daily_api_utc_rollover_revisions_missing_values_and_fixed_cutoff(tmp_path):
    async def run():
        import asyncpg,httpx,json
        from midas_data.api import make_app
        from midas_data.contracts import CaptureMetadata
        from midas_data.normalize import normalize_capture
        from midas_data.raw_store import RawStore
        from midas_data.settings import Settings
        from midas_data.store import Store
        from midas_data.trajectory import METHODS
        settings=Settings.from_env();schema='daily_fixture_'+uuid4().hex
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
            start=(datetime.now(timezone.utc)-timedelta(days=2)).replace(hour=0,minute=0,second=0,microsecond=0)
            async def capture(seconds,value,endpoint='token_info',source='fixture'):
                meta=CaptureMetadata(capture_id=uuid4(),source_kind=source,endpoint=endpoint,chain='base',
                    requested_at=start+timedelta(seconds=seconds-1),received_at=start+timedelta(seconds=seconds),
                    source_event_at=start-timedelta(days=10),http_status=200,evidence_kind='original_response')
                data={'address':token,'price':{'price':'1'},'circulating_supply':value}
                if endpoint=='market_rank':data={'rank':[{'address':token,'price':'1','market_cap':value}]}
                raw=json.dumps({'data':data}).encode()
                await store.accept_capture(meta,raw);result=normalize_capture(meta,raw)
                await store.record_normalization(meta.capture_id,result)
                return meta,result
            previous,_=await capture(-1,'9000')
            opening,_=await capture(0,'10')
            peak,peak_run=await capture(3600,'30')
            closing,_=await capture(86399,'20')
            tomorrow,_=await capture(86400,'8000')
            await capture(3601,'9999',source='legacy_gmgn')
            await capture(3602,'7777',endpoint='market_rank')
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                    headers={'authorization':'Bearer '+settings.api_key}) as client:
                async def query(cut=None,**overrides):
                    params=dict(chain='BASE',token=token.upper().replace('0X','0x'),source_kind='fixture',
                        method=next(iter(METHODS)),day=start.date().isoformat(),
                        knowledge_cutoff=(cut or await pool.fetchval('SELECT clock_timestamp()')).isoformat())
                    params.update(overrides)
                    return await client.get('/v1/trajectory/daily',params=params)
                old_cutoff=await pool.fetchval('SELECT clock_timestamp()')
                reply=await query(old_cutoff);assert reply.status_code==200,reply.text
                old=reply.json();d=old['daily']
                assert len(old['points'])==3 and d['calendar_state']=='ended'
                assert d['first_observation']['point_id']==str(opening.capture_id)
                assert d['last_observation']['point_id']==str(closing.capture_id)
                assert d['maximum_observed']['value_text']=='30'
                assert d['minimum_observed']['value_text']=='10'
                assert d['coverage']['query_seconds']==86400 and d['coverage']['trailing_seconds']==1
                tomorrow_reply=await query(day=(start+timedelta(days=1)).date().isoformat())
                assert [p['point_id'] for p in tomorrow_reply.json()['points']]==[str(tomorrow.capture_id)]
                # Published after the first query: historical maximum changes only for later cutoffs.
                late,_=await capture(7200,'40')
                revised=peak_run.model_copy(update={'normalizer_version':'daily-revision-1','facts':[
                    f.model_copy(update={'fact_id':uuid4(),'value_text':'50' if f.metric=='circulating_supply' else f.value_text})
                    for f in peak_run.facts]})
                await store.record_normalization(peak.capture_id,revised)
                revised_cutoff=await pool.fetchval('SELECT clock_timestamp()')
                current=(await query(revised_cutoff)).json()
                assert current['daily']['maximum_observed']['value_text']=='50'
                assert current['daily']['has_known_revisions']
                assert (await query(old_cutoff)).json()==old
                # Invalidating the revised peak exposes another observed maximum, never the old peak.
                empty=peak_run.model_copy(update={'normalizer_version':'daily-revision-2','facts':[]})
                await store.record_normalization(peak.capture_id,empty)
                latest=(await query()).json()
                assert latest['daily']['maximum_observed']['point_id']==str(late.capture_id)
                assert latest['daily']['maximum_observed']['value_text']=='40'
                assert latest['coverage']['unavailable_points']==1
                assert (await query(revised_cutoff)).json()==current
                assert (await query(old_cutoff)).json()==old
                # Same cutoff/day is precisely the existing raw trajectory plus the summary.
                reply=await client.get('/v1/trajectory',params=dict(chain='base',token=token,source_kind='fixture',
                    method=next(iter(METHODS)),start=start.isoformat(),end=(start+timedelta(days=1)).isoformat(),
                    knowledge_cutoff=old_cutoff.isoformat(),max_points=2000))
                assert reply.status_code==200 and reply.json()=={k:v for k,v in old.items() if k!='daily'}
                missing=(await query(token='0x'+'b'*40)).json()
                assert missing['daily']['state']=='unavailable' and missing['daily']['maximum_observed']['value_text'] is None
                assert (await query(source_kind='gmgn')).json()['points']==[]
                rank=(await query(method='gmgn.market_rank.v4.market_cap')).json()
                assert rank['daily']['maximum_observed']['value_text']=='7777'
                offset_cutoff=old_cutoff.astimezone(timezone(timedelta(hours=-3))).isoformat()
                assert (await query(knowledge_cutoff=offset_cutoff)).json()==old
                live=(await query(day=old_cutoff.date().isoformat(),cut=old_cutoff)).json()['daily']
                assert live['calendar_state']=='in_progress'
                assert live['through_at']==old_cutoff.isoformat()
                assert (await query(max_points=2)).status_code==413
                for override in ({'day':'2026-02-30'},{'day':'9999-12-31'},
                    {'day':(old_cutoff+timedelta(days=1)).date().isoformat()},
                    {'knowledge_cutoff':old_cutoff.replace(tzinfo=None).isoformat()},
                    {'knowledge_cutoff':(old_cutoff+timedelta(days=1)).isoformat()},
                    {'method':'all'},{'gap_after_seconds':0},{'max_points':2001}):
                    assert (await query(**override)).status_code==422,override
                assert (await client.get('/v1/trajectory/daily',headers={'authorization':'invalid'})).status_code==401
                counts="SELECT (SELECT count(*) FROM captures),(SELECT count(*) FROM normalization_runs),(SELECT count(*) FROM facts),(SELECT count(*) FROM outbox)"
                before=await pool.fetchrow(counts)
                await query();await query(old_cutoff)
                assert await pool.fetchrow(counts)==before
        finally:
            if pool:await pool.close()
            assert schema.startswith('daily_fixture_') and len(schema)==len('daily_fixture_')+32
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE');await admin.close()
    asyncio.run(run())

"""Real PostgreSQL in an isolated schema; synthetic captures, no GMGN requests."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
from uuid import uuid4
import pytest

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='Private SQL opt-in')]


def test_series_replays_cutoff_handles_revisions_and_enforces_api_budgets(tmp_path):
    async def run():
        import asyncpg,httpx,json
        from midas_data.api import make_app
        from midas_data.contracts import CaptureMetadata
        from midas_data.normalize import normalize_capture
        from midas_data.raw_store import RawStore
        from midas_data.settings import Settings
        from midas_data.store import Store
        from midas_data.trajectory import METHODS
        settings=Settings.from_env();schema='trajectory_fixture_'+uuid4().hex
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
            start=datetime.now(timezone.utc)-timedelta(hours=3)
            end=start+timedelta(hours=1)
            async def capture(seconds,endpoint='token_info',body=None,source='fixture'):
                meta=CaptureMetadata(capture_id=uuid4(),source_kind=source,endpoint=endpoint,chain='base',
                    requested_at=start+timedelta(seconds=seconds-1),received_at=start+timedelta(seconds=seconds),
                    source_event_at=start-timedelta(days=2),http_status=200,evidence_kind='original_response')
                raw=json.dumps({'data':body if body is not None else
                    {'address':token,'price':{'price':'2.5000'},'circulating_supply':'100'}}).encode()
                await store.accept_capture(meta,raw)
                result=normalize_capture(meta,raw);await store.record_normalization(meta.capture_id,result)
                return meta,result
            first,original=await capture(0)
            last,_=await capture(1800)
            # An excluded right boundary must not be reported as a point.
            await capture(3600)
            old_cutoff=await pool.fetchval('SELECT clock_timestamp()')
            app=make_app(settings);app.state.store=store
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                    headers={'authorization':'Bearer '+settings.api_key}) as client:
                async def query(cut=None,**overrides):
                    params=dict(chain='BASE',token=token.upper().replace('0X','0x'),source_kind='fixture',
                        method=next(iter(METHODS)),start=start.isoformat(),end=end.isoformat(),
                        knowledge_cutoff=(cut or await pool.fetchval('SELECT clock_timestamp()')).isoformat())
                    params.update(overrides)
                    return await client.get('/v1/trajectory',params=params)
                response=await query(old_cutoff);assert response.status_code==200,response.text
                old=response.json()
                assert old['chain']=='base' and old['token_address']==token
                assert [p['capture_id'] for p in old['points']]==[str(first.capture_id),str(last.capture_id)]
                assert all(p['value_text']=='250.0000' and p['revision_count']==1 for p in old['points'])
                assert old['time_basis']=='capture_received_at' and len(old['gaps'])==2
                # Late publication inside the old received-time window cannot leak into the old cutoff.
                late,_=await capture(600)
                revised=original.model_copy(update={'normalizer_version':'fixture-revision-1','facts':[
                    f.model_copy(update={'fact_id':uuid4(),'value_text':'200' if f.metric=='circulating_supply' else f.value_text})
                    for f in original.facts]})
                await store.record_normalization(first.capture_id,revised)
                revised_cutoff=await pool.fetchval('SELECT clock_timestamp()')
                current=(await query(revised_cutoff)).json()
                assert len(current['points'])==3 and current['points'][0]['value_text']=='500.0000'
                assert current['points'][0]['revision_count']==2
                assert current['points'][0]['previous_normalizer_version']==original.normalizer_version
                assert (await query(old_cutoff)).json()==old
                # A newest interpretation with only one operand must invalidate the old complete one.
                partial=revised.model_copy(update={'normalizer_version':'fixture-revision-2','facts':[
                    f.model_copy(update={'fact_id':uuid4()}) for f in revised.facts if f.metric=='price']})
                await store.record_normalization(first.capture_id,partial)
                p=(await query()).json()['points'][0]
                assert p['state']=='unavailable' and p['value_text'] is None
                # Even an empty revision replaces the entire snapshot.
                empty=original.model_copy(update={'normalizer_version':'fixture-revision-3','facts':[]})
                await store.record_normalization(first.capture_id,empty)
                p=(await query()).json()['points'][0]
                assert p['reason']=='no_current_token_facts' and p['revision_count']==4
                assert (await query(revised_cutoff)).json()==current
                assert (await query(old_cutoff)).json()==old
                # Method/source boundaries and duplicate item handling are enforced by the SQL path.
                await capture(300,source='legacy_gmgn')
                rank_body={'rank':[{'address':token,'market_cap':'99','price':'1'},
                                   {'address':token,'market_cap':'999','price':'2'}]}
                rank,_=await capture(120,'market_rank',rank_body)
                ranked=(await query(method='gmgn.market_rank.v4.market_cap')).json()
                assert len(ranked['points'])==1
                assert ranked['points'][0]['capture_id']==str(rank.capture_id)
                assert ranked['points'][0]['reason']=='ambiguous_token_items'
                assert len((await query()).json()['points'])==3
                assert (await query(source_kind='gmgn')).json()['points']==[]
                assert (await query(max_points=2)).status_code==413
                for overrides in (
                    {'method':'all'}, {'start':start.replace(tzinfo=None).isoformat()},
                    {'start':(end-timedelta(days=2)).isoformat()}, {'end':start.isoformat()},
                    {'knowledge_cutoff':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()},
                    {'knowledge_cutoff':start.isoformat()}, {'max_points':2001}, {'gap_after_seconds':0}):
                    assert (await query(**overrides)).status_code==422,overrides
                assert (await client.get('/v1/trajectory',headers={'authorization':'invalid'})).status_code==401
                # Querying does not create captures, interpretations, facts, or outbox work.
                sql="SELECT (SELECT count(*) FROM captures),(SELECT count(*) FROM normalization_runs),(SELECT count(*) FROM facts),(SELECT count(*) FROM outbox)"
                before=await pool.fetchrow(sql)
                await query();await query(old_cutoff)
                assert await pool.fetchrow(sql)==before
        finally:
            if pool:await pool.close()
            assert schema.startswith('trajectory_fixture_') and len(schema)==len('trajectory_fixture_')+32
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE');await admin.close()
    asyncio.run(run())

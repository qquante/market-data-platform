"""Joint temporal acceptance on real PostgreSQL, synthetic data in a private schema."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import os
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('DATA_INTEGRATION') != '1', reason='Private SQL opt-in')]


def test_joint_views_late_revisions_utc_gaps_and_pool_timeout_recovery(tmp_path):
    async def run():
        import asyncpg
        import httpx
        import json
        from midas_data.api import make_app
        from midas_data.contracts import CaptureMetadata
        from midas_data.normalize import normalize_capture
        from midas_data.raw_store import RawStore
        from midas_data.settings import Settings
        from midas_data.store import Store
        from midas_data.trajectory import METHODS

        settings = Settings.from_env()
        schema = 'temporal_fixture_' + uuid4().hex
        admin = await asyncpg.connect(settings.database_dsn, command_timeout=15)
        pool = None
        try:
            assert await admin.fetchval('SELECT current_database()') == 'midas_data'
            await admin.execute(f'CREATE SCHEMA {schema}')
            pool = await asyncpg.create_pool(settings.database_dsn, min_size=1, max_size=1,
                command_timeout=15, server_settings={'search_path':schema, 'timezone':'UTC'})
            async with pool.acquire() as con:
                for path in sorted(Path('midas_data/migrations').glob('*.sql')):
                    await con.execute(path.read_text(encoding='utf-8'))
            store = Store('fixture', RawStore(tmp_path, min_free_bytes=0))
            store.pool = pool
            token = '0x'+'a'*40
            start = (datetime.now(timezone.utc)-timedelta(days=2)).replace(
                hour=23, minute=58, second=0, microsecond=0)
            end = start + timedelta(minutes=22)
            days = [start.date(), end.date()]

            async def capture(seconds, value):
                meta = CaptureMetadata(capture_id=uuid4(), source_kind='fixture', endpoint='token_info',
                    chain='base', requested_at=start+timedelta(seconds=seconds-1),
                    received_at=start+timedelta(seconds=seconds), http_status=200,
                    evidence_kind='original_response')
                raw = json.dumps({'data':{'address':token, 'price':{'price':'1', 'volume_5m':'123.45'},
                    'circulating_supply':value, 'liquidity':'567.89', 'decimals':9}}).encode()
                await store.accept_capture(meta, raw)
                result = normalize_capture(meta, raw)
                await store.record_normalization(meta.capture_id, result)
                return meta, result

            for seconds, value in [(0,'19000'), (60,'21000'), (120,'19000'), (180,'22000'),
                                   (780,'18000'), (840,'23000'), (900,None),
                                   (960,'25000'), (1020,'26000')]:
                await capture(seconds, value)
            app = make_app(settings)
            app.state.store = store
            counts = """SELECT (SELECT count(*) FROM captures), (SELECT count(*) FROM normalization_runs),
                        (SELECT count(*) FROM facts), (SELECT count(*) FROM outbox)"""
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                    headers={'authorization':'Bearer '+settings.api_key}) as client:
                async def get(route, params):
                    reply = await client.get(route, params=params)
                    assert reply.status_code == 200, reply.text
                    return reply.json()

                def params(cutoff):
                    return dict(chain='base', token=token, source_kind='fixture', method=next(iter(METHODS)),
                        start=start.isoformat(), end=end.isoformat(), knowledge_cutoff=cutoff.isoformat(),
                        gap_after_seconds=120)

                async def bundle(cutoff):
                    before = await pool.fetchrow(counts)
                    base = params(cutoff)
                    series = await get('/v1/trajectory', base)
                    crossings = await get('/v1/trajectory/crossings', base)
                    assert {k:v for k,v in crossings.items() if k != 'crossings'} == series
                    features = await get('/v1/trajectory/features', base)
                    assert {k:v for k,v in features.items() if k != 'features'} == series
                    repeat = await client.get('/v1/trajectory/features', params=base)
                    assert repeat.json() == features
                    assert repeat.headers['x-midas-feature-cache'] == 'reused=2; computed=0'
                    values = features['features']['features']
                    valid = [p for p in series['points'] if p['state'] == 'available']
                    assert Decimal(values['market_cap_min_observed']['value_text']) == min(Decimal(p['value_text']) for p in valid)
                    assert Decimal(values['market_cap_max_observed']['value_text']) == max(Decimal(p['value_text']) for p in valid)
                    assert values['market_cap_latest']['reason'] == 'stale_at_window_end'
                    assert values['market_cap_sample_change_pct']['state'] == 'unavailable'
                    daily = []
                    for day in days:
                        query = {k:v for k,v in base.items() if k not in ('start','end')}
                        query['day'] = day.isoformat()
                        result = await get('/v1/trajectory/daily', query)
                        expected = [p for p in series['points'] if p['observed_at'][:10] == day.isoformat()]
                        # Continuity is local to each window; evidence and values must be identical.
                        stable = lambda points: [{k:v for k,v in p.items() if k != 'break_before'} for p in points]
                        assert stable(result['points']) == stable(expected)
                        valid = [p for p in expected if p['state'] == 'available']
                        summary = result['daily']
                        assert summary['calendar_state'] == 'ended'
                        assert Decimal(summary['minimum_observed']['value_text']) == min(Decimal(p['value_text']) for p in valid)
                        assert Decimal(summary['maximum_observed']['value_text']) == max(Decimal(p['value_text']) for p in valid)
                        assert summary['first_observation']['point_id'] == valid[0]['point_id']
                        assert summary['last_observation']['point_id'] == valid[-1]['point_id']
                        daily.append(result)

                    by_id = {p['point_id']:p for p in series['points']}
                    contexts = {}
                    for snap in crossings['crossings']['context_snapshots']:
                        point = by_id[snap['point_id']]
                        value = await get('/v1/observations/'+point['anchor_fact_id']+'/valuation',
                            dict(source_kind='fixture', knowledge_cutoff=cutoff.isoformat()))
                        ctx, valuation = snap['context'], value['valuation']
                        assert ctx['price'] == valuation['price']
                        assert ctx['market_cap'] == valuation['market_cap']
                        assert ctx['economic_inputs'] == valuation['economic_inputs']['fields']
                        assert ctx['market_cap']['value_text'] == point['value_text']
                        contexts[point['point_id']] = value
                    assert await pool.fetchrow(counts) == before
                    return dict(series=series, crossings=crossings['crossings'], daily=daily, contexts=contexts,
                                features=features['features'])

                saved = []
                async def checkpoint():
                    cutoff = await pool.fetchval('SELECT clock_timestamp()')
                    result = await bundle(cutoff)
                    saved.append((cutoff, result))
                    return result

                original = await checkpoint()
                assert [e['kind'] for e in original['crossings']['events']] == [
                    'upcross', 'downcross', 'reentry', 'upcross']
                midnight = original['crossings']['events'][1]
                assert midnight['interval_start'][:10] != midnight['interval_end'][:10]
                assert len(original['crossings']['segments']) == 3
                assert original['series']['coverage']['unavailable_points'] == 1
                assert original['daily'][0]['daily']['maximum_observed']['value_text'] == '21000'
                assert original['daily'][1]['daily']['maximum_observed']['value_text'] == '26000'

                late, interpretation = await capture(90, '41000')
                arrived = await checkpoint()
                assert arrived['daily'][0]['daily']['maximum_observed']['value_text'] == '41000'
                assert len(arrived['crossings']['events']) > len(original['crossings']['events'])
                revised = interpretation.model_copy(update={'normalizer_version':'temporal-acceptance-revision', 'facts':[
                    f.model_copy(update={'fact_id':uuid4(),
                        'value_text':'18000' if f.metric == 'circulating_supply' else f.value_text})
                    for f in interpretation.facts]})
                await store.record_normalization(late.capture_id, revised)
                corrected = await checkpoint()
                assert corrected['daily'][0]['daily']['maximum_observed']['value_text'] == '21000'
                assert corrected['daily'][0]['daily']['last_observation']['value_text'] == '18000'
                assert corrected['daily'][0]['daily']['has_known_revisions'] is True
                assert corrected['crossings']['events'] != arrived['crossings']['events']

                empty = interpretation.model_copy(update={
                    'normalizer_version':'temporal-acceptance-invalidation', 'facts':[]})
                await store.record_normalization(late.capture_id, empty)
                invalidated = await checkpoint()
                point = next(p for p in invalidated['series']['points'] if p['capture_id'] == str(late.capture_id))
                assert point['state'] == 'unavailable' and point['value_text'] is None
                assert point['normalizer_version'] == 'temporal-acceptance-invalidation'
                assert invalidated['series']['coverage']['unavailable_points'] == 2
                assert invalidated['daily'][0]['daily']['last_observation']['value_text'] == '21000'
                assert len(invalidated['crossings']['events']) == 3
                for cutoff, expected in saved:
                    assert await bundle(cutoff) == expected

                for override in ({'max_points':2}, {'start':(start-timedelta(days=2)).isoformat()}):
                    reply = await client.get('/v1/trajectory/features', params=dict(params(saved[-1][0]), **override))
                    assert reply.status_code == (413 if 'max_points' in override else 422)
                assert (await client.get('/v1/trajectory/features', headers={'authorization':'invalid'})).status_code == 401
                # Exhaust only this private pool. No global advisory lock or live worker is blocked.
                cutoff = saved[-1][0]
                cached=await get('/v1/trajectory',params(cutoff))
                uncached_cutoff=await pool.fetchval('SELECT clock_timestamp()')
                async with pool.acquire():
                    # Frozen responses remain usable, but a new cutoff must consult the pool.
                    assert await get('/v1/trajectory',params(cutoff))==cached
                    reply = await asyncio.wait_for(client.get('/v1/trajectory', params=params(uncached_cutoff)), 8)
                assert reply.status_code == 503
                assert reply.json() == {'detail':'Trajectory query timed out; reduce interval'}
                # The same app, semaphore and real pool must be reusable after cancellation.
                assert await asyncio.wait_for(bundle(cutoff), 8) == saved[-1][1]
                fresh=await get('/v1/trajectory',params(uncached_cutoff))
                assert fresh['points']==cached['points']
        finally:
            if pool:
                await pool.close()
            assert schema.startswith('temporal_fixture_') and len(schema) == len('temporal_fixture_')+32
            await admin.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
            await admin.close()

    asyncio.run(run())

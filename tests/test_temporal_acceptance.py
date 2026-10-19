"""HTTP admission and response budgets; controlled readers, no provider calls."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from midas_data import api
from midas_data.settings import Settings
from midas_data.trajectory import build_series, METHODS

ROUTES = ('/v1/trajectory', '/v1/trajectory/daily', '/v1/trajectory/crossings', '/v1/trajectory/features')


def request_params(route):
    start = (datetime.now(timezone.utc) - timedelta(days=2)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    params = dict(source_kind='fixture', chain='base', token='0x'+'a'*40,
        method=next(iter(METHODS)), knowledge_cutoff=(start+timedelta(days=1)).isoformat(),
        gap_after_seconds=120)
    if route.endswith('/daily'):
        params['day'] = start.date().isoformat()
    else:
        params.update(start=start.isoformat(), end=(start+timedelta(days=1)).isoformat())
    return params


def empty_series(kwargs):
    return build_series([], **{k:v for k,v in kwargs.items() if k != 'max_points'})


def make_test_app(tmp_path):
    settings = Settings('unused', 'unused', 'fixture-key', 'nats://unused',
        'http://unused', 'fixture', 'fixture', tmp_path, str(tmp_path))
    app = api.make_app(settings)
    app.state.store = object()
    return app


def test_shared_admission_rejects_third_reader_and_recovers(tmp_path, monkeypatch):
    async def run():
        entered = 0
        both_entered, release = asyncio.Event(), asyncio.Event()

        async def reader(store, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await release.wait()
            return empty_series(kwargs)

        monkeypatch.setattr(api, 'read_trajectory', reader)
        app = make_test_app(tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                headers={'authorization':'Bearer fixture-key'}) as client:
            active = [asyncio.create_task(client.get(route, params=request_params(route)))
                      for route in ROUTES[:2]]
            try:
                await asyncio.wait_for(both_entered.wait(), 2)
                for route in ROUTES[2:]:
                    reply = await client.get(route, params=request_params(route))
                    assert reply.status_code == 429 and reply.headers['retry-after'] == '1'
                assert entered == 2
            finally:
                release.set()
                replies = await asyncio.gather(*active)
            assert all(r.status_code == 200 for r in replies)
            reply = await client.get(ROUTES[2], params=request_params(ROUTES[2]))
            assert reply.status_code == 200 and entered == 3

    asyncio.run(run())


@pytest.mark.parametrize('route', ROUTES)
def test_response_bytes_budget_rejects_whole_result_and_releases_slot(tmp_path, monkeypatch, route):
    async def run():
        oversized = True

        async def reader(store, **kwargs):
            result = empty_series(kwargs)
            if oversized:
                # Fault injection at the reader boundary, not a capacity benchmark.
                result['fixture_padding'] = 'x' * (4 * 1024**2)
            return result

        monkeypatch.setattr(api, 'read_trajectory', reader)
        app = make_test_app(tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                headers={'authorization':'Bearer fixture-key'}) as client:
            # More than two rejected requests detects a slot leaked by the error path.
            for _ in range(3):
                reply = await client.get(route, params=request_params(route))
                assert reply.status_code == 413
                assert reply.json() == {'detail':'Trajectory response exceeds 4 MiB; reduce interval'}
            oversized = False
            for _ in range(3):
                assert (await client.get(route, params=request_params(route))).status_code == 200

    asyncio.run(run())

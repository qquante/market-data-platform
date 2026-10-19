"""Two online paper lots, private SQL and synthetic prices; no live financial actions."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
import os

import pytest

from midas_data.features import _json
from midas_data.paper_replay import PAIR_VERSION, ReplayError, export_bundle, export_pair, replay
from midas_data.paper_prices import TRACKED_PAPER_VERSION
from test_paper_account_integration import fixture
from test_paper_bindings import initialize
from test_paper_prices_integration import capture

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('DATA_INTEGRATION') != '1', reason='Private SQL opt-in')]


async def run_pair(tmp_path, mode):
    async with fixture(tmp_path) as f:
        await initialize(f)
        c, m = f['client'], f['manager']
        bodies = [await f['signal'](number) for number in (1, 2)]
        for index, body in enumerate(bodies):
            body['policy'] = dict(body['policy'], version=TRACKED_PAPER_VERSION, budget_usd='20',
                fee_bps=100, slippage_bps=200, entry_timeout_seconds=720, hold_seconds=2 if index == 0 else 1)
        if mode == 'expiry':
            bodies[0]['policy'].update(version='gmgn-paper-timed-lot-v1', entry_timeout_seconds=2)
        keys = []
        for body in bodies:
            response = await c.post('/v1/paper/positions', json=body)
            assert response.status_code == 202, response.text
            keys.append(response.json()['position_id'])
            await c.post('/v1/rules/active/' + body['rule_id'] + '/pause')
        await capture(f, '3')
        await m.step()
        # Legacy expiry case has no usable market-cap frame, while v2 opens on independent price.
        assert m.paper.entries[keys[0]]['state']['status'] == ('open' if mode == 'close' else 'entry_pending')
        assert m.paper.entries[keys[1]]['state']['status'] == 'open'
        m = await f['reload']()
        await asyncio.sleep(1.05)
        await capture(f, '4')
        await m.step()
        assert keys[1] not in m.paper.entries
        await asyncio.sleep(1.05)
        await capture(f, '5')
        m.paper.prices.clear()
        await m.step()
        assert not m.paper.entries
        before = (await c.get('/v1/paper/account')).json()
        events_before = await f['pool'].fetchval('SELECT count(*) FROM paper_account_events')
        async with f['pool'].acquire() as con:
            class ReadOnlyProof:
                def __getattr__(self, name):
                    return getattr(con, name)

                async def fetchval(self, sql, *args):
                    value = await con.fetchval(sql, *args)
                    if sql == 'SHOW transaction_read_only':
                        import asyncpg
                        assert value == 'on'
                        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                            async with con.transaction():
                                await con.execute('CREATE TEMP TABLE replay_pair_write_guard (n int)')
                    return value
            bundle = await export_pair(ReadOnlyProof(), keys)
            assert bundle == await export_pair(con, list(reversed(keys)))
            with pytest.raises(ReplayError, match='selected lot budget'):
                await export_bundle(con, keys[0])
            with pytest.raises(ReplayError, match='distinct bounded'):
                await export_pair(con, [keys[0], keys[0]])
            with pytest.raises(ReplayError, match='distinct bounded'):
                await export_pair(con, keys + ['0' * 64])
        # Retrying admission and recovering terminal lots cannot append a second credit.
        m = await f['reload']()
        for body in bodies:
            repeated = await c.post('/v1/paper/positions', json=body)
            assert repeated.status_code == 202 and not repeated.json()['created']
        await m.step()
        assert (await c.get('/v1/paper/account')).json() == before
        assert await f['pool'].fetchval('SELECT count(*) FROM paper_account_events') == events_before
        result = replay(bundle)
        assert result['status'] == 'verified', result
        assert result['final_account'] == before['state']
        assert result['account_final_revision'] == before['revision']
        for key in keys:
            assert result['final_positions'][key] == (await c.get('/v1/paper/positions/' + key)).json()['state']
        return bundle


@pytest.fixture(scope='module', params=['close', 'expiry'])
def pair_bundle(request, tmp_path_factory):
    return asyncio.run(run_pair(tmp_path_factory.mktemp('pair_' + request.param), request.param))


def test_interleaved_account_matches_online_and_offline(pair_bundle, monkeypatch):
    import midas_data.rules as rules
    class NoWallClock:
        fromisoformat = datetime.fromisoformat

        @staticmethod
        def now(*args, **kwargs):
            raise AssertionError('Offline replay read wall clock')
    monkeypatch.setattr(rules, 'datetime', NoWallClock)
    original = deepcopy(pair_bundle)
    result = replay(pair_bundle)
    assert result['status'] == 'verified', result
    assert result['version'] == PAIR_VERSION
    assert result == replay(json.loads(_json(pair_bundle)))
    assert pair_bundle == original
    assert result['account_movements'] in (5, 6)
    events = pair_bundle['account']['events']
    assert events[0]['kind'] == events[1]['kind'] == 'requested'
    assert events[0]['position_id'] != events[1]['position_id']
    assert events[-1]['position_id'] == events[0]['position_id']  # The first admitted finishes last.
    assert result['archived_frames'] < sum(
        len({e['frame_key'] for e in lot['events'] + lot['evaluations'] if e['frame_key']})
        for lot in pair_bundle['lots'])  # Shared frame bytes are retained once.


@pytest.mark.parametrize('change', [
    'missing_movement', 'duplicate_credit', 'foreign_movement', 'revision_gap',
    'reordered_movements', 'wrong_position_revision', 'changed_reservation',
    'changed_second_fill', 'missing_lot', 'duplicate_lot', 'extra_lot',
])
def test_pair_rejects_incomplete_order_or_double_accounting(pair_bundle, change):
    b = deepcopy(pair_bundle)
    events = b['account']['events']
    if change == 'missing_movement':
        del events[2]
    elif change == 'duplicate_credit':
        events.append(deepcopy(next(e for e in events if e['kind'] == 'closed')))
    elif change == 'foreign_movement':
        events[1]['position_id'] = '0' * 64
    elif change == 'revision_gap':
        events[2]['revision'] += 1
    elif change == 'reordered_movements':
        events[0], events[1] = events[1], events[0]
    elif change == 'wrong_position_revision':
        events[-1]['position_revision'] = 1
    elif change == 'changed_reservation':
        events[0]['state']['reserved_usd'] = '0'
    elif change == 'changed_second_fill':
        next(e for lot in b['lots'] for e in lot['events'] if e['kind'] == 'opened')['fill']['quantity'] = '999'
    elif change == 'missing_lot':
        b['lots'].pop()
    elif change == 'duplicate_lot':
        b['lots'][1] = deepcopy(b['lots'][0])
    elif change == 'extra_lot':
        b['lots'].append(deepcopy(b['lots'][0]))
    result = replay(b)
    assert result['status'] == 'failed', (change, result)
    assert result['path']


def test_export_does_not_filter_a_third_lot_out_of_the_account_interval(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            await initialize(f)
            keys = []
            for number in (1, 2, 3):
                body = await f['signal'](number)
                body['policy'] = dict(body['policy'], budget_usd='20', entry_timeout_seconds=1)
                response = await f['client'].post('/v1/paper/positions', json=body)
                assert response.status_code == 202, response.text
                keys.append(response.json()['position_id'])
                await f['client'].post('/v1/rules/active/' + body['rule_id'] + '/pause')
                await asyncio.sleep(1.05)
                await f['current'][0].step()
                assert not f['current'][0].paper.entries
            async with f['pool'].acquire() as con:
                bundle = await export_pair(con, [keys[0], keys[2]])
            assert len(bundle['account']['events']) == 6  # Includes the unselected middle lot.
            assert keys[1] in {e['position_id'] for e in bundle['account']['events']}
            result = replay(bundle)
            assert result['status'] == 'failed' and result['path'] == 'account.events.count'
    asyncio.run(run())

"""Private PostgreSQL online -> export -> offline replay, with synthetic prices."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

import pytest

from midas_data.features import _json
from midas_data.paper_replay import export_bundle, replay
from midas_data.paper_prices import PriceSourceChange
from test_paper_account_integration import fixture
from test_paper_bindings import initialize
from test_paper_prices_integration import capture, create, opened_legacy

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('DATA_INTEGRATION') != '1', reason='Private SQL opt-in')]


async def cycle(tmp_path, mode):
    async with fixture(tmp_path) as f:
        await initialize(f)
        c = f['client']
        if mode == 'amended':
            key, body, original = await opened_legacy(f)
            await f['current'][0].paper.change_price_source(key, PriceSourceChange(
                expected_policy_id=original['policy_id'], reason='Fixture future source'))
        else:
            key, body = await create(f, tracked=mode == 'v2')
            await c.post('/v1/rules/active/' + body['rule_id'] + '/pause')
            if mode == 'expired':
                # Persist a real expiry through the online transition, without a 30-second wait.
                from midas_data.paper_model import transition
                from midas_data.rule_timers import instant
                m = f['current'][0]
                entry = m.paper.entries[key]
                now = instant(entry['state']['entry_deadline'])
                state, event = transition(entry['policy'], entry['state'], None, now)
                await m.paper.persist(key, entry, state, event, None, now, 'timer')
            else:
                await capture(f, '3')
                await f['current'][0].step()
        if mode != 'expired':
            await asyncio.sleep(1.05)
            # Recovery is exercised by the online owner; replay consumes its recorded action time.
            m = await f['reload']()
            await capture(f, '4')
            await m.step()
        pool = f['pool']
        before = await pool.fetchval("SELECT state FROM paper_account WHERE account_id='main'")
        async with pool.acquire() as con:
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
                                await con.execute('CREATE TEMP TABLE replay_write_guard (n int)')
                    return value
            bundle = await export_bundle(ReadOnlyProof(), key)
        assert await pool.fetchval("SELECT state FROM paper_account WHERE account_id='main'") == before
        return bundle


@pytest.fixture(scope='module', params=['v2', 'amended', 'expired'])
def bundle(request, tmp_path_factory):
    return asyncio.run(cycle(tmp_path_factory.mktemp(request.param), request.param))


def test_online_cycle_matches_offline_at_recorded_time(bundle, monkeypatch):
    import midas_data.rules as rules
    class NoWallClock:
        fromisoformat = datetime.fromisoformat

        @staticmethod
        def now(*args, **kwargs):
            raise AssertionError('Replay consulted wall clock')
    monkeypatch.setattr(rules, 'datetime', NoWallClock)
    original = deepcopy(bundle)
    result = replay(bundle)
    assert result['status'] == 'verified', result
    assert bundle == original  # Inputs and ledger checkpoint are not mutated.
    assert result == replay(json.loads(_json(bundle)))
    assert result['final_position'] == bundle['position']['state']
    assert result['final_account'] == bundle['account']['events'][-1]['state']


@pytest.mark.parametrize('change', [
    'missing_event', 'event_sequence', 'missing_frame', 'frame_bytes', 'signal_decision',
    'signal_condition', 'final_state', 'cash_movement', 'missing_movement', 'interleaved_movement',
    'future_checkpoint', 'policy_identity', 'source_policy', 'future_knowledge', 'backwards_time',
    'extra_frame', 'over_budget', 'terminal_status',
])
def test_incomplete_or_modified_ledger_never_passes(bundle, change):
    b = deepcopy(bundle)
    if change == 'missing_event':
        b['events'].pop()
    elif change == 'event_sequence':
        b['events'][0]['revision'] = 2
    elif change == 'missing_frame':
        b['frames'].pop(next(iter(b['frames'])))
    elif change == 'frame_bytes':
        key = next(iter(b['frames']))
        b['frames'][key] += ' '
    elif change == 'signal_decision':
        b['evaluations'][-1]['eligible'] = False
    elif change == 'signal_condition':
        b['evaluations'][-1]['condition_id'] = '0' * 64
    elif change == 'final_state':
        b['position']['state']['budget_usd'] = '999'
    elif change == 'cash_movement':
        b['account']['events'][-1]['delta']['cash_usd'] = '999'
    elif change == 'missing_movement':
        b['account']['events'].pop()
    elif change == 'interleaved_movement':
        b['account']['events'][-1]['revision'] += 1
    elif change == 'future_checkpoint':
        b['account']['checkpoint']['recorded_action_at'] = '2099-01-01T00:00:00+00:00'
    elif change == 'policy_identity':
        b['position']['policy_id'] = '0' * 64
    elif change == 'source_policy':
        b['position']['definition']['price_model'] = 'other'
    elif change == 'future_knowledge':
        event = b['evaluations'][-1]
        key = event['frame_key']
        frame = json.loads(b['frames'][key])
        frame['result']['points'][-1]['available_at'] = '2099-01-01T00:00:00+00:00'
        raw = _json(frame).decode()
        new_key = hashlib.sha256(raw.encode()).hexdigest()
        del b['frames'][key]
        b['frames'][new_key] = raw
        for item in b['events'] + b['evaluations']:
            if item['frame_key'] == key:
                item['frame_key'] = new_key
    elif change == 'backwards_time':
        b['events'][-1]['recorded_action_at'] = '2000-01-01T00:00:00+00:00'
    elif change == 'extra_frame':
        b['frames']['0' * 64] = '{}'
    elif change == 'over_budget':
        b['evaluations'] *= 513
    elif change == 'terminal_status':
        b['position']['status'] = 'open'
    result = replay(b)
    assert result['status'] == 'failed', (change, result)
    assert result['path']


def test_modified_source_amendment_and_fill_are_detected(bundle):
    if bundle['position']['status'] == 'entry_expired':
        # The timeout event itself must be reproduced, not accepted as input.
        b = deepcopy(bundle)
        b['events'][-1]['reason'] = 'simulated_entry'
        assert replay(b)['status'] == 'failed'
        return
    b = deepcopy(bundle)
    entry = next(e for e in b['events'] if e['kind'] == 'opened')
    entry['fill']['quantity'] = '999'
    assert replay(b)['path'].endswith('.fill.quantity')
    amendment = next((e for e in bundle['events'] if e['kind'] == 'price_source_changed'), None)
    if amendment:
        for field, value in [('effective_after', '2000-01-01T00:00:00+00:00'),
                             ('source_policy_id', '0' * 64),
                             ('identity', {'source_kind': 'gmgn', 'chain': 'sol', 'token_address': 'other'})]:
            b = deepcopy(bundle)
            next(e for e in b['events'] if e['kind'] == 'price_source_changed')['price_source_change'][field] = value
            assert replay(b)['status'] == 'failed'

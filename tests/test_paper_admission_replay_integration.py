"""Account refusal evidence and offline replay on real private SQL; synthetic prices only."""
import asyncio
from copy import deepcopy
import json
import os
import pytest

from midas_data.features import _json
from midas_data.paper_admission_replay import export_rejection
from midas_data.paper_replay import replay
from test_paper_account_integration import fixture
from test_paper_bindings import bind, binding_config, quote_step, rule

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('DATA_INTEGRATION') != '1', reason='Private SQL opt-in')]

REASONS = dict(cash='Insufficient available paper cash', count='Paper account position count limit',
               exposure='Paper committed exposure limit')


async def prepare(f, mode, timeout=2):
    response = await f['client'].post('/v1/paper/account', json=dict(
        initial_cash_usd='200' if mode == 'exposure' else '100', max_position_usd='60',
        max_exposure_usd='100', max_open_positions=1 if mode == 'count' else 2))
    assert response.status_code == 202, response.text
    ids = [await rule(f, threshold='250', cooldown=n) for n in (1, 2)]
    for rid in ids:
        config = binding_config(rid)
        config['policy']['entry_timeout_seconds'] = timeout
        await bind(f, rid, config)
    await f['current'][0].step()
    return sorted(ids)


async def recorded_refusal(tmp_path, mode):
    async with fixture(tmp_path) as f:
        await prepare(f, mode)
        await quote_step(f, '3')
        db, m = f['pool'], f['current'][0]
        event = json.loads(await db.fetchval("SELECT record FROM paper_binding_events WHERE kind='rejected'"))
        assert event['reason'] == REASONS[mode]
        assert event['account_check']['account_revision'] == 2
        assert await db.fetchval('SELECT count(*) FROM paper_positions') == 1
        assert await db.fetchval('SELECT count(*) FROM paper_account_events') == 2
        assert not await db.fetchval('SELECT EXISTS(SELECT 1 FROM paper_positions WHERE rule_id=$1)', event['rule_id'])
        async with db.acquire() as con:
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
                                await con.execute('CREATE TEMP TABLE refusal_write_guard (n int)')
                    return value
            before = await export_rejection(ReadOnlyProof(), event['rule_id'], event['signal_sequence'])
        # Cash/count/exposure become available again, but the rejected durable signal is not retried.
        await asyncio.sleep(2.05)
        await m.step()
        assert not m.paper.entries
        assert await db.fetchval('SELECT revision FROM paper_account') == 3
        m = await f['reload']()
        await m.step()
        assert await db.fetchval("SELECT count(*) FROM paper_binding_events WHERE kind='rejected'") == 1
        assert await db.fetchval('SELECT count(*) FROM paper_positions') == 1
        async with db.acquire() as con:
            after = await export_rejection(con, event['rule_id'], event['signal_sequence'])
        assert after == before  # Uses revision 2, never today's revision 3.
        result = replay(after)
        assert result['status'] == 'verified', result
        assert result['reason'] == REASONS[mode] and result['account_movements'] == 0
        return after


@pytest.fixture(scope='module', params=['cash', 'count', 'exposure'])
def refusal_bundle(request, tmp_path_factory):
    return asyncio.run(recorded_refusal(tmp_path_factory.mktemp(request.param), request.param))


def test_exact_refusal_after_balance_changes_and_restart(refusal_bundle, monkeypatch):
    import midas_data.rules as rules
    from datetime import datetime
    class NoWallClock:
        fromisoformat = datetime.fromisoformat

        @staticmethod
        def now(*args, **kwargs):
            raise AssertionError('Replay used current time')
    monkeypatch.setattr(rules, 'datetime', NoWallClock)
    original = deepcopy(refusal_bundle)
    result = replay(refusal_bundle)
    assert result['status'] == 'verified', result
    assert result == replay(json.loads(_json(refusal_bundle)))
    assert original == refusal_bundle
    assert result['account_revision'] == 2 and result['account_movements'] == 0


@pytest.mark.parametrize('change', ['reason', 'balance', 'revision', 'hash', 'signal', 'legacy',
                                    'cursor', 'future_check', 'missing_frame'])
def test_modified_or_missing_evidence_never_passes(refusal_bundle, change):
    b = deepcopy(refusal_bundle)
    if change == 'reason':
        b['event']['reason'] = 'different refusal'
    elif change == 'balance':
        b['account']['checkpoint']['state']['reserved_usd'] = '0'
    elif change == 'revision':
        b['event']['account_check']['account_revision'] += 1
    elif change == 'hash':
        b['event']['account_check']['state_sha256'] = '0' * 64
    elif change == 'signal':
        b['evaluations'][-1]['eligible'] = False
    elif change == 'legacy':
        del b['event']['account_check']
    elif change == 'cursor':
        b['previous_event']['cursor'] = b['event']['signal_sequence']
    elif change == 'future_check':
        b['event']['account_check']['checked_at'] = '2099-01-01T00:00:00+00:00'
    elif change == 'missing_frame':
        b['frames'].pop(next(iter(b['frames'])))
    result = replay(b)
    assert result['status'] == 'failed', (change, result)
    if change == 'legacy':
        assert result['path'] == 'event.account_check'
        assert result['reason'] == 'historical account evidence missing'


def test_refusal_evidence_rolls_back_and_survives_lost_commit_ack(tmp_path):
    async def run():
        async with fixture(tmp_path) as f:
            ids = await prepare(f, 'cash', timeout=20)
            m, db = f['current'][0], f['pool']
            step = m.paper.bindings.step
            async def noop():
                pass
            m.paper.bindings.step = noop
            await quote_step(f, '3')
            m.paper.bindings.step = step
            persist = m.paper.bindings.persist
            async def failure(*args, **kwargs):
                if kwargs.get('event', {}).get('kind') == 'rejected':
                    raise RuntimeError('Injected refusal write failure')
                return await persist(*args, **kwargs)
            m.paper.bindings.persist = failure
            with pytest.raises(RuntimeError, match='refusal write'):
                await m.paper.bindings.step()
            assert await db.fetchval('SELECT count(*) FROM paper_positions') == 1
            assert await db.fetchval('SELECT revision FROM paper_account') == 2
            assert await db.fetchval("SELECT count(*) FROM paper_binding_events WHERE kind='rejected'") == 0
            m = await f['reload']()
            con = m.connection
            class LostTransaction:
                def __init__(self, tx):
                    self.tx = tx
                async def __aenter__(self):
                    return await self.tx.__aenter__()
                async def __aexit__(self, *args):
                    result = await self.tx.__aexit__(*args)
                    if args[0] is None:
                        raise RuntimeError('Injected refusal commit acknowledgement loss')
                    return result
            class LostCommit:
                def __getattr__(self, name):
                    return getattr(con, name)
                def transaction(self, *args, **kwargs):
                    tx = con.transaction(*args, **kwargs)
                    return tx if con.is_in_transaction() else LostTransaction(tx)
            m.connection = LostCommit()
            with pytest.raises(RuntimeError, match='acknowledgement loss'):
                await m.paper.bindings.step()
            event = json.loads(await db.fetchval("SELECT record FROM paper_binding_events WHERE kind='rejected'"))
            assert event['rule_id'] == ids[-1] and event['account_check']['account_revision'] == 2
            m.connection = con
            m = await f['reload']()
            await m.step()
            assert json.loads(await db.fetchval("SELECT record FROM paper_binding_events WHERE kind='rejected'")) == event
            assert await db.fetchval('SELECT revision FROM paper_account') == 2
            async with db.acquire() as read:
                result = replay(await export_rejection(read, event['rule_id'], event['signal_sequence']))
            assert result['status'] == 'verified', result
    asyncio.run(run())

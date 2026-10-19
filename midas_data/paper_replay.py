"""Bounded replay of one or two terminal paper lots from archived read-only inputs.

Replays recorded evaluation/action times, not the scheduler or raw normalization.
Cash follows global account revisions; the preceding event is a trusted checkpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

from .features import _hash, _json
from .feature_windows import WindowUnavailable
from .paper_account import ACCOUNT_VERSION, AccountPolicy, account_change, balances, position_definition
from .paper_model import PAPER_VERSION, PaperPolicy, requested_state, transition
from .paper_prices import TRACKED_PAPER_VERSION, PriceSourceChange, source_definition
from .rule_timers import ACTIVE_VERSION, ActiveRule, advance, instant
from .rules import evaluate

VERSION = 'gmgn-paper-replay-v1'
PAIR_VERSION = 'gmgn-paper-replay-pair-v1'
MAX_EVENTS = 512
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024


class ReplayError(ValueError):
    def __init__(self, path, reason):
        self.path, self.reason = path, reason
        super().__init__(path + ': ' + reason)


def require(condition, path, reason):
    if not condition:
        raise ReplayError(path, reason)


def equal(actual, expected, path):
    """Return the first differing field without dumping whole frames or ledgers."""
    if type(actual) is not type(expected):
        raise ReplayError(path, 'type differs')
    if isinstance(expected, dict):
        require(actual.keys() == expected.keys(), path, 'fields differ')
        for key in expected:
            equal(actual[key], expected[key], path + '.' + key)
    elif isinstance(expected, list):
        require(len(actual) == len(expected), path, 'length differs')
        for index, (a, b) in enumerate(zip(actual, expected)):
            equal(a, b, path + f'[{index}]')
    else:
        require(actual == expected, path, 'value differs')


class ArchivedOwner:
    def __init__(self, payload=None, reason=None):
        self.payload, self.reason = payload, reason

    def read(self, _key):
        if self.payload is not None:
            return self.payload
        if self.reason == 'window_not_registered':
            raise KeyError(_key)
        raise WindowUnavailable('Recorded unavailable window')


def timestamp(value, path):
    at = instant(value)
    require(at is not None and at.utcoffset() == timedelta(0), path, 'UTC timestamp required')
    return at


def replay(bundle):
    """Pure offline comparison. Invalid/incomplete inputs never return verified."""
    try:
        if bundle.get('version')=='gmgn-paper-admission-replay-v1':
            from .paper_admission_replay import replay_rejection
            return replay_rejection(bundle)
        return _replay(bundle)
    except ReplayError as error:
        return dict(version=bundle.get('version', VERSION), status='failed', path=error.path, reason=error.reason)
    except (ValueError, TypeError, KeyError, ArithmeticError, WindowUnavailable) as error:
        return dict(version=bundle.get('version', VERSION), status='failed', path='bundle', reason=type(error).__name__)


def _replay(bundle):
    version = bundle['version']
    require(version in (VERSION, PAIR_VERSION), 'version', 'unsupported replay format')
    require(len(_json(bundle)) <= MAX_BUNDLE_BYTES, 'bundle', 'byte limit')
    lots = [bundle] if version == VERSION else bundle['lots']
    equal(len(lots), 1 if version == VERSION else 2, 'lots.count')
    ids = [lot['position']['position_id'] for lot in lots]
    equal(len(set(ids)), len(ids), 'lots.unique')
    frames = bundle['frames']
    size = verify_frames([e for lot in lots for e in lot['evaluations'] + lot['events']], frames)
    a = bundle['account']
    account = AccountPolicy.model_validate(a['config'])
    equal(a['definition'], account.definition(), 'account.definition')
    equal(a['policy_id'], account.key(), 'account.policy_id')
    results, flows = {}, {}
    for lot in lots:
        key = lot['position']['position_id']
        try:
            results[key], flows[key] = _replay_lot(lot, account, frames)
        except ReplayError as error:
            if version == VERSION:
                raise
            raise ReplayError('lots.' + key + '.' + error.path, error.reason) from error
    final_account, final_revision = _replay_account(a, account, flows)
    result = dict(version=version, status='verified', input_sha256=_hash(bundle),
        rule_evaluations=sum(len(lot['evaluations']) for lot in lots),
        paper_events=sum(len(lot['events']) for lot in lots),
        account_movements=len(a['events']), archived_frames=len(frames), archived_frame_bytes=size,
        account_checkpoint_revision=a['checkpoint']['revision'], account_final_revision=final_revision,
        final_account=final_account,
        scope='one recorded terminal cycle; archived frames and action times; trusted prior account checkpoint',
        exclusions=['scheduler completeness', 'raw normalization', 'checkpoint history', 'interleaved account movements'])
    if version == VERSION:
        result.update(position_id=ids[0], final_position=results[ids[0]])
    else:
        result.update(position_ids=ids, final_positions=results,
            scope='two recorded terminal cycles; account revision order; trusted prior account checkpoint',
            exclusions=['scheduler completeness', 'raw normalization', 'checkpoint history', 'unselected account movements'])
    return result


def _replay_account(a, account, flows):
    checkpoint = a['checkpoint']
    equal(checkpoint['account_id'], 'main', 'account.checkpoint.account_id')
    equal(checkpoint['policy_id'], account.key(), 'account.checkpoint.policy_id')
    state = checkpoint['state']
    balances(account, state)
    movements = a['events']
    equal(len(movements), sum(len(items) for items in flows.values()), 'account.events.count')
    revision = checkpoint['revision']
    previous_at = timestamp(checkpoint['recorded_action_at'], 'account.checkpoint')
    cursors = dict.fromkeys(flows, 0)
    for index, stored in enumerate(movements):
        path = f'account.events[{index}]'
        key = stored['position_id']
        require(key in flows, path, 'movement from an unselected position')
        cursor = cursors[key]
        require(cursor < len(flows[key]), path, 'duplicate or extra account movement')
        action = flows[key][cursor]
        now = timestamp(action['recorded_action_at'], path)
        require(now >= previous_at, path, 'future checkpoint or account actions out of order')
        revision += 1
        state, delta = account_change(account, state, action['kind'], action['budget_usd'], action['state'])
        expected = dict(version=ACCOUNT_VERSION, account_id='main', revision=revision, kind=action['kind'],
            position_id=key, position_revision=action['position_revision'], recorded_action_at=now.isoformat(),
            execution='simulated', policy_id=account.key(), delta=delta, state=state)
        equal(stored, expected, path)
        cursors[key] += 1
        previous_at = now
    for key, items in flows.items():
        equal(cursors[key], len(items), 'account.consumed.' + key)
    return state, revision


def verify_frames(events, frames):
    required_keys = {e['frame_key'] for e in events if e['frame_key'] is not None}
    equal(set(frames), required_keys, 'frames.keys')
    size = 0
    for key, value in frames.items():
        raw = value.encode('utf-8')
        size += len(raw)
        require(len(raw) <= 4 * 1024 * 1024 and size <= MAX_FRAME_BYTES, 'frames', 'byte limit')
        equal(hashlib.sha256(raw).hexdigest(), key, 'frames.' + key + '.sha256')
    return size


def archived_frame(event, at, path, frames):
    key = event['frame_key']
    snapshot = json.loads(frames[key]) if key else None
    if snapshot:
        cutoff = timestamp(snapshot['publication_cutoff'], path + '.publication_cutoff')
        require(cutoff <= at, path, 'frame published after action')
        # All archived points must respect knowledge time, including unavailable observations.
        for point in snapshot['result']['points']:
            for field in ('observed_at', 'available_at'):
                if point.get(field):
                    require(timestamp(point[field], path + '.' + field) <= cutoff,
                            path, 'point beyond publication cutoff')
    return snapshot


def replay_signal(rule, evaluations, frames):
    require(0 < len(evaluations) <= MAX_EVENTS, 'evaluations', 'bounded signal prefix required')
    rule_state = {}
    last_at = None
    for sequence, stored in enumerate(evaluations, 1):
        path = f'evaluations[{sequence}]'
        now = timestamp(stored['evaluated_at'], path)
        require(last_at is None or now >= last_at, path, 'time moved backwards')
        snapshot = archived_frame(stored, now, path, frames)
        trigger = stored['trigger']
        require(trigger in ('pause', 'runtime_gap', 'recovery_or_registration', 'timer', 'window'),
                path + '.trigger', 'unsupported input')
        reset = trigger in ('recovery_or_registration', 'runtime_gap')
        if trigger == 'pause':
            require(snapshot is None, path, 'pause cannot consume a frame')
            condition = dict(state='unknown', reason='operator_paused', frame=None, evaluation_id=None)
        else:
            reason = stored['condition_reason']
            require(snapshot is not None or reason in
                    ('window_owner_unavailable', 'window_not_registered', 'window_not_ready', 'incompatible_frame'),
                    path, 'condition evidence missing')
            owner = None if snapshot is None and reason == 'window_owner_unavailable' else ArchivedOwner(
                frames[stored['frame_key']] if snapshot else None, reason)
            # Incompatible frames are not persisted by online evaluation, so cannot be reconstructed.
            require(snapshot is not None or reason != 'incompatible_frame', path, 'incompatible frame not archived')
            condition = evaluate(rule.comparison, owner, now=now)
        state, decision = advance(rule, rule_state, condition, snapshot, now, reset=reset or trigger == 'pause')
        expected = dict(version=ACTIVE_VERSION, rule_id=rule.key(), sequence=sequence,
            evaluated_at=now.isoformat(), trigger=trigger, condition_id=condition['evaluation_id'],
            frame_key=stored['frame_key'], frame=condition['frame'], continuity_reset=reset,
            unevaluated_since=rule_state.get('last_evaluated_at') if reset else None,
            true_since=state['true_since'], observed_through=state['observed_through'],
            next_due_at=state['next_due_at'], **decision, event_id=_hash([rule.key(), sequence]))
        equal(stored, expected, path)
        rule_state = dict(state, last_evaluated_at=now.isoformat())
        last_at = now
    signal = evaluations[-1]
    require(signal['disposition'] == 'signal' and signal['eligible'], 'signal', 'eligible signal required')

    return signal


def _replay_lot(lot, account, frames):
    p, r = lot['position'], lot['rule']
    rule = ActiveRule.model_validate(r['config'])
    policy = PaperPolicy.model_validate(p['policy'])
    equal(r['definition'], rule.definition(), 'rule.definition')
    equal(p['rule_id'], rule.key(), 'position.rule_id')
    equal(p['window_id'], _hash(rule.comparison.window.canonical()), 'position.window_id')
    equal(p['position_id'], _hash([PAPER_VERSION, rule.key(), p['signal_sequence']]), 'position.position_id')
    equal(p['account_id'], 'main', 'position.account_id')
    equal(p['definition'], position_definition(policy, account), 'position.definition')
    equal(p['policy_id'], _hash(p['definition']), 'position.policy_id')
    require(p['status'] in ('closed', 'entry_expired'), 'position.status', 'terminal lot required')
    evaluations, events = lot['evaluations'], lot['events']
    require(0 < len(evaluations) <= MAX_EVENTS and len(evaluations) == p['signal_sequence'],
            'evaluations', 'complete bounded prefix through signal required')
    require(0 < len(events) <= MAX_EVENTS and len(events) == p['revision'],
            'events', 'complete bounded position history required')
    signal = replay_signal(rule, evaluations, frames)

    flows = []
    cfg = rule.comparison.window.canonical()
    identity = dict(source_kind=cfg['source_kind'], chain=cfg['chain'], token_address=cfg['token'])
    state = None
    last_at = None
    for revision, stored in enumerate(events, 1):
        path = f'events[{revision}]'
        now = timestamp(stored['recorded_action_at'], path)
        require(last_at is None or now >= last_at, path, 'time moved backwards')
        snapshot = archived_frame(stored, now, path, frames)
        trigger = stored['trigger']
        if revision == 1:
            require(snapshot is None and trigger == 'request', path, 'request input required')
            age = (now - timestamp(signal['evaluated_at'], 'signal')).total_seconds()
            require(0 <= age <= policy.max_signal_age_seconds, path, 'signal outside admission age')
            state = requested_state(policy, now, signal['event_id'], identity)
            event = dict(kind='requested', reason='signal_consumed', cash_delta_usd='0')
        elif stored['kind'] == 'price_source_changed':
            require(state['status'] == 'open' and policy.version != TRACKED_PAPER_VERSION
                    and not state.get('price_source_change') and snapshot is None
                    and trigger == 'explicit_source_amendment', path, 'invalid source amendment')
            request = PriceSourceChange.model_validate(stored['price_source_change']['request'])
            equal(request.expected_policy_id, p['policy_id'], path + '.expected_policy_id')
            amendment = dict(request=request.model_dump(), definition=source_definition(),
                source_policy_id=_hash(source_definition()), identity=identity, effective_after=now.isoformat(),
                revision=revision, original_policy_id=p['policy_id'], original_window_id=p['window_id'])
            state = dict(state, price_source_change=amendment)
            event = dict(kind='price_source_changed', reason=request.reason,
                         price_source_change=amendment, cash_delta_usd='0')
        else:
            require(trigger in ('recovery_or_request', 'timer', 'window', 'token_info_frame'),
                    path + '.trigger', 'unsupported input')
            state, event = transition(policy, state, snapshot, now)
            require(event is not None, path, 'unexpected persisted action')
        expected = dict(version=PAPER_VERSION, position_id=p['position_id'], revision=revision,
            event_id=_hash([p['position_id'], revision]), recorded_action_at=now.isoformat(),
            execution='simulated', trigger=trigger, frame_key=stored['frame_key'],
            policy_id=p['policy_id'], account_id='main', **event)
        equal(stored, expected, path)
        if event['kind'] not in ('waiting', 'price_source_changed'):
            flows.append(dict(kind=event['kind'], position_revision=revision, recorded_action_at=now.isoformat(),
                              budget_usd=policy.budget_usd, state=state))
        last_at = now
    equal(state, p['state'], 'position.state')
    equal(state['status'], p['status'], 'position.status')
    return state, flows


async def export_bundle(con, position_id):
    """Backward-compatible single-lot export."""
    return await _export(con, [position_id], VERSION)


async def export_pair(con, position_ids):
    """Export exactly two lots and the FULL account interval, including foreign movements."""
    return await _export(con, position_ids, PAIR_VERSION)


async def _export(con, position_ids, version):
    require(len(position_ids) == (1 if version == VERSION else 2)
            and len(set(position_ids)) == len(position_ids), 'position_ids', 'distinct bounded lot selection required')
    for key in position_ids:
        require(len(key) == 64 and all(c in '0123456789abcdef' for c in key), 'position_id', 'invalid identifier')
    async with con.transaction(isolation='repeatable_read', readonly=True):
        require(await con.fetchval('SHOW transaction_read_only') == 'on', 'database', 'read-only required')
        lots, selected_movements = [], []
        for key in sorted(position_ids):
            lot = await _export_lot(con, key)
            lots.append(lot)
            rows = await con.fetch(
                'SELECT record FROM paper_account_events WHERE position_id=$1 ORDER BY position_revision LIMIT 4', key)
            require(0 < len(rows) <= 3, 'account.events', 'movement history missing or over budget')
            selected_movements.extend(json.loads(row['record']) for row in rows)
        first = min(e['revision'] for e in selected_movements)
        last = max(e['revision'] for e in selected_movements)
        require(last - first + 1 <= 3 * len(lots), 'account.events', 'account interval exceeds selected lot budget')
        # Global revision range prevents silently filtering out a third lot's cash movements.
        movements = [json.loads(row['record']) for row in await con.fetch(
            "SELECT record FROM paper_account_events WHERE account_id='main' AND revision BETWEEN $1 AND $2 ORDER BY revision LIMIT 7",
            first, last)]
        checkpoint = await con.fetchval(
            "SELECT record FROM paper_account_events WHERE account_id='main' AND revision=$1", first - 1)
        require(checkpoint is not None, 'account.checkpoint', 'missing')
        account = await con.fetchrow("SELECT config,definition,policy_id FROM paper_account WHERE account_id='main'")
        frames, size = {}, 0
        for key in sorted({e['frame_key'] for lot in lots for e in lot['evaluations'] + lot['events'] if e['frame_key']}):
            length = await con.fetchval('SELECT octet_length(payload) FROM rule_frames WHERE frame_key=$1', key)
            require(length is not None, 'frames.' + key, 'missing')
            size += length
            require(length <= 4 * 1024 * 1024 and size <= MAX_FRAME_BYTES, 'frames', 'byte limit')
            payload = await con.fetchval('SELECT payload FROM rule_frames WHERE frame_key=$1', key)
            frames[key] = payload.decode('utf-8')
        bundle = dict(version=version, frames=frames,
            account=dict(config=json.loads(account['config']), definition=json.loads(account['definition']),
                         policy_id=account['policy_id'], checkpoint=json.loads(checkpoint), events=movements))
        if version == VERSION:
            bundle.update(lots[0])
        else:
            bundle['lots'] = lots
        return bundle


async def _export_lot(con, key):
    row = await con.fetchrow("""SELECT position_id,rule_id,signal_sequence,policy_id,policy,definition,
        window_id,state,status,revision,account_id FROM paper_positions WHERE position_id=$1""", key)
    require(row is not None, 'position', 'not found')
    p = dict(row)
    for field in ('policy', 'definition', 'state'):
        p[field] = json.loads(p[field])
    require(p['status'] in ('closed', 'entry_expired') and p['account_id'] == 'main',
            'position', 'terminal shared-account lot required')
    require(0 < p['revision'] <= MAX_EVENTS and 0 < p['signal_sequence'] <= MAX_EVENTS,
            'position', 'event budget exceeded')
    r = await con.fetchrow('SELECT config,definition FROM active_rules WHERE rule_id=$1', p['rule_id'])
    evaluations = [json.loads(row['decision']) for row in await con.fetch(
        'SELECT decision FROM rule_evaluations WHERE rule_id=$1 AND sequence<=$2 ORDER BY sequence LIMIT 513',
        p['rule_id'], p['signal_sequence'])]
    events = [json.loads(row['record']) for row in await con.fetch(
        'SELECT record FROM paper_events WHERE position_id=$1 ORDER BY revision LIMIT 513', key)]
    return dict(position=p, rule={field: json.loads(r[field]) for field in ('config', 'definition')},
                evaluations=evaluations, events=events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--position-id', help='Read a terminal lot from DATA_DATABASE_DSN')
    mode.add_argument('--position-ids', nargs=2, help='Read two terminal lots and reconcile their shared account')
    mode.add_argument('--rejected-signal', nargs=2, metavar=('RULE_ID','SEQUENCE'), help='Read one automatic account refusal')
    mode.add_argument('--input', type=Path, help='Replay an exported bundle with no database or provider access')
    parser.add_argument('--export', type=Path, help='Write the input bundle for offline reproduction')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()

    async def load():
        import asyncpg
        con = await asyncpg.connect(os.environ['DATA_DATABASE_DSN'], command_timeout=30)
        try:
            if args.rejected_signal:
                from .paper_admission_replay import export_rejection
                return await export_rejection(con, args.rejected_signal[0], int(args.rejected_signal[1]))
            return await export_pair(con, args.position_ids) if args.position_ids else await export_bundle(con, args.position_id)
        finally:
            await con.close()

    if args.input:
        with args.input.open('rb') as source:
            raw = source.read(MAX_BUNDLE_BYTES + 1)
        require(len(raw) <= MAX_BUNDLE_BYTES, 'bundle', 'byte limit')
        bundle = json.loads(raw)
    else:
        bundle = asyncio.run(load())
    report = replay(bundle)
    if args.export:
        args.export.write_bytes(_json(bundle))
    args.report.write_bytes(_json(report))
    print(json.dumps({k: v for k, v in report.items() if k not in ('final_position', 'final_positions', 'final_account')}))
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())

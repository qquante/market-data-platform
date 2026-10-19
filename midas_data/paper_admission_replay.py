"""Replay one automatic account refusal using its explicitly witnessed account revision."""
import json
from .features import _hash, _json
from .paper_account import ACCOUNT_VERSION, ACCOUNT_CHECK_VERSION, AccountPolicy, account_change, balances
from .paper_bindings import BINDING_VERSION, BindingRequest
from .paper_replay import MAX_BUNDLE_BYTES, MAX_EVENTS, MAX_FRAME_BYTES, equal, require, timestamp, verify_frames, replay_signal
from .rule_timers import ActiveRule
from .trajectory import TrajectoryLimit

VERSION = 'gmgn-paper-admission-replay-v1'


def replay_rejection(bundle):
    equal(bundle['version'], VERSION, 'version')
    require(len(_json(bundle)) <= MAX_BUNDLE_BYTES, 'bundle', 'byte limit')
    event = bundle['event']
    require(event['kind'] == 'rejected', 'event.kind', 'account refusal required')
    require(event.get('account_check') is not None, 'event.account_check', 'historical account evidence missing')
    check = event['account_check']
    account = AccountPolicy.model_validate(bundle['account']['config'])
    equal(bundle['account']['definition'], account.definition(), 'account.definition')
    equal(bundle['account']['policy_id'], account.key(), 'account.policy_id')
    binding = BindingRequest.model_validate(bundle['binding']['config'])
    definition = binding.definition(account)
    equal(bundle['binding']['definition'], definition, 'binding.definition')
    equal(bundle['binding']['definition_id'], _hash(definition), 'binding.definition_id')
    rule = ActiveRule.model_validate(bundle['rule']['config'])
    equal(bundle['rule']['definition'], rule.definition(), 'rule.definition')
    equal(rule.key(), binding.rule_id, 'binding.rule_id')
    evaluations, frames = bundle['evaluations'], bundle['frames']
    equal(len(evaluations), event['signal_sequence'], 'signal.sequence')
    size = verify_frames(evaluations, frames)
    signal = replay_signal(rule, evaluations, frames)
    equal(signal['event_id'], event['signal_event_id'], 'signal.event_id')
    now = timestamp(check['checked_at'], 'account_check.checked_at')
    recorded_at = timestamp(event['recorded_at'], 'event.recorded_at')
    require(now <= recorded_at, 'event', 'account check after refusal')
    age = (now - timestamp(signal['evaluated_at'], 'signal.evaluated_at')).total_seconds()
    require(0 <= age <= binding.policy.max_signal_age_seconds, 'signal', 'outside admission age')

    previous = bundle['previous_event']
    equal(previous['version'], BINDING_VERSION, 'previous_event.version')
    equal(previous['rule_id'], binding.rule_id, 'previous_event.rule_id')
    equal(previous['definition_id'], _hash(definition), 'previous_event.definition_id')
    equal(previous['revision'] + 1, event['revision'], 'event.revision')
    require(previous['enabled'] is True and 0 <= previous['cursor'] < signal['sequence'],
            'previous_event', 'signal was not eligible after binding cursor')
    require(timestamp(previous['recorded_at'], 'previous_event.recorded_at') <= now,
            'previous_event', 'binding event after account check')

    checkpoint = bundle['account']['checkpoint']
    require(checkpoint is not None, 'account.checkpoint', 'missing')
    equal(checkpoint['version'], ACCOUNT_VERSION, 'account.checkpoint.version')
    equal(checkpoint['account_id'], 'main', 'account.checkpoint.account_id')
    equal(checkpoint['policy_id'], account.key(), 'account.checkpoint.policy_id')
    require(timestamp(checkpoint['recorded_action_at'], 'account.checkpoint.recorded_action_at') <= now,
            'account.checkpoint', 'future account revision')
    expected_check = dict(version=ACCOUNT_CHECK_VERSION, checked_at=now.isoformat(), account_id='main',
        account_revision=checkpoint['revision'], account_policy_id=account.key(), state_sha256=_hash(checkpoint['state']))
    equal(check, expected_check, 'event.account_check')
    state = checkpoint['state']
    before = _json(state)
    try:
        account_change(account, state, 'requested', binding.policy.budget_usd, {})
    except TrajectoryLimit as exc:
        reason = str(exc)
    else:
        require(False, 'event.reason', 'account would admit this budget')
    equal(_json(state), before, 'account.unchanged')
    expected = dict(version=BINDING_VERSION, rule_id=binding.rule_id, definition_id=_hash(definition),
        revision=previous['revision']+1, cursor=signal['sequence'], enabled=True, execution='simulated',
        recorded_at=recorded_at.isoformat(), signal_sequence=signal['sequence'], signal_event_id=signal['event_id'],
        kind='rejected', reason=reason, account_check=expected_check)
    equal(event, expected, 'event')
    return dict(version=VERSION, status='verified', input_sha256=_hash(bundle), rule_id=binding.rule_id,
        signal_sequence=signal['sequence'], rule_evaluations=len(evaluations), reason=reason,
        account_revision=checkpoint['revision'], budget_usd=binding.policy.budget_usd,
        account_state=state, balances=balances(account, state), account_movements=0,
        archived_frames=len(frames), archived_frame_bytes=size,
        scope='one automatic account refusal at its witnessed revision; checkpoint history trusted',
        exclusions=['manual HTTP refusals', 'unwitnessed historical refusals', 'non-account admission gates',
                    'scheduler completeness', 'raw normalization'])


async def export_rejection(con, rule_id, signal_sequence):
    """Select immutable inputs only; old refusals remain unverified without a witness."""
    require(len(rule_id) == 64 and all(c in '0123456789abcdef' for c in rule_id), 'rule_id', 'invalid identifier')
    require(type(signal_sequence) is int and 0 < signal_sequence <= MAX_EVENTS, 'signal_sequence', 'event budget')
    async with con.transaction(isolation='repeatable_read', readonly=True):
        require(await con.fetchval('SHOW transaction_read_only') == 'on', 'database', 'read-only required')
        raw = await con.fetchval('SELECT record FROM paper_binding_events WHERE rule_id=$1 AND signal_sequence=$2',
                                rule_id, signal_sequence)
        require(raw is not None, 'event', 'not found')
        event = json.loads(raw)
        require(event['kind'] == 'rejected', 'event.kind', 'refusal required')
        binding = await con.fetchrow('SELECT config,definition,definition_id FROM paper_bindings WHERE rule_id=$1', rule_id)
        rule = await con.fetchrow('SELECT config,definition FROM active_rules WHERE rule_id=$1', rule_id)
        previous = await con.fetchval('SELECT record FROM paper_binding_events WHERE rule_id=$1 AND revision=$2',
                                     rule_id, event['revision']-1)
        require(previous is not None, 'previous_event', 'missing binding history')
        account = await con.fetchrow("SELECT config,definition,policy_id FROM paper_account WHERE account_id='main'")
        checkpoint = None
        if event.get('account_check'):
            checkpoint = await con.fetchval(
                "SELECT record FROM paper_account_events WHERE account_id='main' AND revision=$1",
                event['account_check']['account_revision'])
        evaluations = [json.loads(row['decision']) for row in await con.fetch(
            'SELECT decision FROM rule_evaluations WHERE rule_id=$1 AND sequence<=$2 ORDER BY sequence LIMIT 513',
            rule_id, signal_sequence)]
        frames, size = {}, 0
        for key in sorted({e['frame_key'] for e in evaluations if e['frame_key']}):
            length = await con.fetchval('SELECT octet_length(payload) FROM rule_frames WHERE frame_key=$1', key)
            require(length is not None, 'frames.'+key, 'missing')
            size += length
            require(length <= 4*1024*1024 and size <= MAX_FRAME_BYTES, 'frames', 'byte limit')
            frames[key] = (await con.fetchval('SELECT payload FROM rule_frames WHERE frame_key=$1', key)).decode('utf-8')
        return dict(version=VERSION, event=event, previous_event=json.loads(previous),
            binding=dict(config=json.loads(binding['config']), definition=json.loads(binding['definition']),
                         definition_id=binding['definition_id']),
            rule={k: json.loads(rule[k]) for k in ('config','definition')},
            account=dict(config=json.loads(account['config']), definition=json.loads(account['definition']),
                         policy_id=account['policy_id'], checkpoint=json.loads(checkpoint) if checkpoint else None),
            evaluations=evaluations, frames=frames)

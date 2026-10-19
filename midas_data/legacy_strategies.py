"""Frozen, read-only import of previously selected GMGN strategies.

This catalog does not register rules or imply compatibility with the Solana
research simulator. Run, chain, horizon and age remain part of every identity.
"""
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
import hashlib
import json
import math
from decimal import Decimal

VERSION = 'gmgn-legacy-strategies-v1'
SOURCE_KEYS = ('partial70_shadow', 'selected_partials', 'fast_partials')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def compatibility(entry):
    complete = all(all(finite(c.get(k)) for k in ('mean', 'std', 'weight', 'direction'))
                   and c['std'] > 0 and c['weight'] > 0 and c['direction'] in (-1, 1)
                   and c.get('transform') in ('identity', 'log', 'signed_log') for c in entry['conditions'])
    reasons = [
        {'code': 'chain_not_supported', 'message': 'A pesquisa atual aceita Solana; esta calibração é de Robinhood.'},
        {'code': 'legacy_features_not_mapped', 'message': 'Falta mapear e homologar as features e seus clocks no bundle novo.'},
        {'code': 'partial_exit_engine_not_supported', 'message': 'O comparador atual usa saída fixa; ainda não reproduz estas parciais, SL e saída após o horizonte.'},
        {'code': 'temporal_anchor_not_mapped', 'message': 'A idade do token do legado não equivale automaticamente ao tempo após inscrição na base nova.'},
    ]
    if not complete:
        reasons.insert(0, {'code': 'calibration_missing', 'message': 'O relatório preservou pesos e corte, mas não as médias, desvios e transformações deste run.'})
    return {'state': 'blocked', 'calibration': 'complete' if complete else 'missing',
            'required_features': [c['key'] for c in entry['conditions']], 'reasons': reasons}


def record(source_id, source, identity, entry, exit_policy, historical, label=None):
    if identity['chain_scope'] != 'robinhood':
        raise ValueError('unexpected_legacy_chain')
    if not entry['conditions'] or not finite(entry['score_cutoff']):
        raise ValueError('invalid_legacy_entry')
    targets, sells = exit_policy['targets_gain_percent'], exit_policy['sell_percent']
    if not targets or len(targets) != len(sells) or sorted(set(targets)) != targets:
        raise ValueError('invalid_legacy_targets')
    if not all(finite(v) and v > 0 for v in targets + sells):
        raise ValueError('invalid_legacy_partials')
    if abs(sum(Decimal(str(v)) for v in sells) + Decimal(str(exit_policy['moonbag_percent'])) - 100) > Decimal('.0000001'):
        raise ValueError('legacy_position_conservation')
    definition = {**identity, 'entry_rule': entry, 'exit_policy': exit_policy}
    return {'strategy_id': digest(definition), 'definition': definition,
            'label': label or ('GMGN · ' + str(len(entry['conditions'])) + ' critérios'),
            'source_id': source_id, 'source': source, 'historical_evidence': historical,
            'compatibility': compatibility(entry), 'runtime_status': 'catalog_only',
            'strategy_validated_in_current_base': False}


def import_selected(data, source):
    for values in data['rows']:
        row = dict(zip(data['columns'], values, strict=True))
        horizon, age, rule_id = row['entry_key_horizon_age_rule'].split('|')
        entry = dict(zip(data['entry_columns'], data['entries'][row['entry_key_horizon_age_rule']], strict=True))
        entry['conditions'] = [dict(zip(data['condition_columns'], c, strict=True)) for c in entry['conditions']]
        identity = {'run_id': data['run'], 'chain_scope': data['scope'], 'horizon_seconds': int(horizon),
                    'target_age_seconds': int(age), 'pair_id': row['pair_id'], 'entry_rule_id': rule_id,
                    'exit_policy_id': row['exit_policy_id']}
        historical = {'kind': 'historical_quote_simulation', 'snapshot_at': data['at'],
                      'metrics': {k: v for k, v in row.items() if k not in data['columns'][:3]}}
        yield record('selected_partials', source, identity, entry, data['exits'][row['exit_policy_id']], historical)


def import_fast(data, source):
    for row in data['strategies']:
        entry = {'score_cutoff': row['cutoff'], 'feature_count': row['features'],
                 'conditions': [dict(zip(('key', 'direction', 'weight'), c, strict=True)) for c in row['conditions']],
                 'normalization': None, 'missing_policy': None, 'required_gates': None}
        identity = {'run_id': data['metadata']['run_id'], 'chain_scope': row['scope'], 'horizon_seconds': row['horizon'],
                    'target_age_seconds': row['age'], 'pair_id': row['pair'], 'entry_rule_id': row['entry_id'],
                    'exit_policy_id': None}
        historical = {'kind': 'historical_quote_simulation_postselected', 'snapshot_at': data['metadata']['at'],
                      'metrics': {k: row[k] for k in ('stats', 'robust', 'forensic_ready', 'eligible', 'test_n', 'test_win')}}
        yield record('fast_partials', source, identity, entry, row['exit'], historical, row['label'])


def import_profile(data, source):
    strategy = data['strategy']
    identity = {k: strategy[k] for k in ('run_id', 'chain_scope', 'horizon_seconds', 'target_age_seconds', 'pair_id', 'entry_rule_id')}
    fingerprint = {**identity, 'entry_rule': strategy['entry_rule'], 'exit_policy': strategy['exit_policy']}
    if digest(fingerprint) != data['strategy_sha256'] or data['broadcast_enabled'] is not False or data['auto_promote_live'] is not False:
        raise ValueError('legacy_profile_identity_changed')
    identity['exit_policy_id'] = None
    historical = {'kind': 'historical_shadow_profile', 'snapshot_at': data['activated_at_utc'],
                  'original_status': data['status'], 'original_strategy_sha256': data['strategy_sha256'],
                  'metrics': {k: strategy[k] for k in ('robust_gate', 'net_train_mean', 'net_test_mean', 'stress_test_mean')}}
    yield record('partial70_shadow', source, identity, strategy['entry_rule'], strategy['exit_policy'], historical,
                 'Fluxo + estrutura · parcial 70% · shadow')


@lru_cache(maxsize=1)
def catalog():
    root = files('midas_data').joinpath('strategy_sources')
    manifest = json.loads(root.joinpath('manifest.json').read_bytes())
    records = []
    for source_id in SOURCE_KEYS:
        source = manifest[source_id]
        raw = root.joinpath(source_id + '.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError('legacy_source_hash_mismatch')
        data = json.loads(raw.decode('utf-8-sig'))
        importer = {'selected_partials': import_selected, 'fast_partials': import_fast, 'partial70_shadow': import_profile}[source_id]
        records.extend(importer(data, source))
    ids = [r['strategy_id'] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate_legacy_strategy_identity')
    return {'version': VERSION, 'sources': manifest, 'total': len(records), 'items': records,
            'execution_mode': 'catalog_only', 'current_validation': 'none',
            'entry_score_semantics': 'weighted direction * clipped [-4,4] train zscore; missing required feature rejects entry',
            'exit_semantics': 'sell_percent refers to initial position; SL applies to remainder; first observable price at or after horizon',
            'limitations': ['Historical quote simulations are not new results or fill evidence.',
                            'Source run calibrations are never replaced by another run.',
                            'These selected sources do not contain every candidate ever generated by the legacy lab.']}


def summary(row):
    d = row['definition'];e = d['exit_policy']
    return {k: deepcopy(row[k]) for k in ('strategy_id', 'label', 'source_id', 'runtime_status')} | {
        'run_id': d['run_id'], 'pair_id': d['pair_id'], 'chain': d['chain_scope'],
        'horizon_seconds': d['horizon_seconds'], 'target_age_seconds': d['target_age_seconds'],
        'feature_count': len(d['entry_rule']['conditions']), 'first_target_gain_percent': e['targets_gain_percent'][0],
        'first_sell_percent': e['sell_percent'][0], 'stop_loss_percent': e['stop_loss_percent'],
        'calibration': row['compatibility']['calibration'], 'snapshot_at': row['historical_evidence']['snapshot_at']}


def listing(*, offset=0, limit=20, source=None, first_target=None, query=''):
    c = catalog();q = query.casefold().strip()
    rows = [r for r in c['items'] if (source is None or r['source_id'] == source)
            and (first_target is None or r['definition']['exit_policy']['targets_gain_percent'][0] == first_target)
            and (not q or q in (' '.join([r['label'], r['definition']['pair_id'], r['definition']['run_id']]
                 + r['compatibility']['required_features'])).casefold())]
    return {'version': VERSION, 'catalog_total': c['total'], 'total': len(rows), 'offset': offset, 'limit': limit,
            'has_more': offset + limit < len(rows), 'items': [summary(r) for r in rows[offset:offset + limit]],
            'sources': [{'id': k, **v, 'count': sum(r['source_id'] == k for r in c['items'])} for k, v in c['sources'].items()],
            'target_groups': dict(sorted(Counter(r['definition']['exit_policy']['targets_gain_percent'][0] for r in c['items']).items()))}


def detail(strategy_id):
    return next((deepcopy(r) for r in catalog()['items'] if r['strategy_id'] == strategy_id), None)


def export(strategy_id=None):
    result = catalog() if strategy_id is None else detail(strategy_id)
    if result is None:
        return None
    body = encoded(result)
    return body, hashlib.sha256(body).hexdigest()

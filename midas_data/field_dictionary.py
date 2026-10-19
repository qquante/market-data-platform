"""Versioned read dictionary; only published adapter acceptance licenses calculations."""
from collections import Counter
from functools import lru_cache
from importlib.resources import files
import json

VERSION = 'gmgn-flow-dictionary-v1'
ENDPOINTS = ('market_rank', 'trenches', 'token_info', 'token_top_holders', 'token_kline')


@lru_cache(maxsize=1)
def _catalog():
    document = json.loads(files('midas_data').joinpath('field_contracts', VERSION + '.json').read_text('utf-8'))
    assert document['version'] == VERSION
    index = {(r['endpoint'], r['path']): r for r in document['fields']}
    assert len(index) == len(document['fields'])
    return document, index


def dictionary_page(endpoint=None, category=None, usage=None, query='', offset=0, limit=50):
    document, _ = _catalog()
    q = query.casefold()
    rows = [r for r in document['fields'] if (not endpoint or r['endpoint'] == endpoint)
            and (not category or r['category'] == category) and (not usage or r['usage'] == usage)
            and (not q or q in (r['path'] + ' ' + r['meaning'] + ' ' + r['logical_name']).casefold())]
    return {'version': VERSION, 'scope': document['scope'], 'baseline': document['baseline'],
            'sources': document['sources'], 'categories': sorted({r['category'] for r in document['fields']}),
            'summary': document['summary'], 'items': rows[offset:offset+limit], 'total': len(rows),
            'offset': offset, 'limit': limit, 'has_more': offset+limit < len(rows),
            'interpretation': 'current dictionary; not historical knowledge or upstream verification'}


def assess_field(endpoint, field, observation=None, *, chain=None, accepted=False):
    entry = _catalog()[1].get((endpoint, field['schema_path']))
    source = observation or {}; quality = source.get('quality', 'unverified')
    kind = field['value_type']
    missing = kind == 'null' or (kind == 'string' and field['value_text'] == '')
    if entry is None:
        return {'catalogued': False, 'category': 'provider_extension', 'quality': 'missing' if missing else 'unverified',
                'reason': 'uncatalogued_path', 'schema_drift': True, 'calculation_allowed': False,
                'unit': None, 'usage': 'blocked', 'dictionary_version': VERSION}
    type_changed = kind != 'null' and kind not in entry['accepted_json_types']
    contract_matches = bool(source.get('contract_id')) and source['contract_id'] in entry['adapter_contract_ids']
    allowed = (chain == 'sol' and accepted and entry['usage'] == 'metric'
               and contract_matches and quality == 'valid' and not missing and not type_changed)
    if missing:
        quality, reason = 'missing', 'explicit_null' if kind == 'null' else 'explicit_empty_string'
    elif type_changed:
        quality, reason = 'invalid', 'json_type_drift'
    elif quality in {'invalid', 'missing'}:
        reason = source.get('reason', 'adapter_rejected_value')
    elif allowed:
        reason = 'published_adapter_contract_checked'
    elif chain != 'sol':
        quality, reason = 'unverified', 'dictionary_chain_out_of_scope'
    elif not accepted:
        quality, reason = 'unverified', 'capture_not_accepted'
    elif entry['usage'] == 'metric':
        quality, reason = 'unverified', 'published_adapter_contract_required'
    else:
        quality, reason = 'unverified', entry['restriction']
    return {'catalogued': True, 'dictionary_id': entry['id'], 'category': entry['category'],
            'meaning': entry['meaning'], 'usage': entry['usage'], 'unit': entry['unit'],
            'window': entry['window'], 'semantic_status': entry['semantic_status'],
            'quality': quality, 'reason': reason, 'schema_drift': type_changed,
            'calculation_allowed': allowed, 'adapter_contract_id': source.get('contract_id'),
            'dictionary_version': VERSION}


def summarize_fields(rows):
    qualities = Counter(); unknown = set(); changed = set(); categories = Counter(); reasons = Counter(); allowed = 0
    for row in rows:
        a = row['interpretation']; qualities[a['quality']] += 1; categories[a['category']] += 1
        allowed += a['calculation_allowed']
        if not a['calculation_allowed']: reasons[a['reason']] += 1
        if not a['catalogued']: unknown.add(row['schema_path'])
        elif a['schema_drift']: changed.add(row['schema_path'])
    return {'field_occurrences': len(rows), 'calculation_allowed_occurrences': allowed,
            'qualities': dict(qualities), 'categories': dict(categories), 'blocking_reasons': dict(reasons),
            'uncatalogued_path_count': len(unknown), 'type_drift_path_count': len(changed),
            'uncatalogued_paths': sorted(unknown)[:100], 'type_drift_paths': sorted(changed)[:100],
            'drift_lists_truncated': len(unknown) > 100 or len(changed) > 100,
            'dictionary_version': VERSION, 'scope': 'all raw leaves before search and pagination'}

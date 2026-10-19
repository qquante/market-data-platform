"""On-demand provenance for one persisted fact; no writes or economic inference."""
from __future__ import annotations

from decimal import Decimal
import json

from .normalize import (
    MAX_RAW_BYTES, MAX_TRENCH_JSON_NODES, WINDOWS, _constant, _entities, _nodes,
    _pairs, _PayloadError, _INTEGER, _value_text, canonical_identity,
)

CONTEXT_VERSION = 'gmgn-observation-context-v2'


class ContextUnavailable(ValueError):
    pass


def _parameter(scopes, names, valid):
    entries = [{'pointer': base + '/' + name, 'value': values[name]}
               for base, values in scopes for name in names if name in values]
    if not entries:
        return {'state': 'missing', 'value': None, 'pointers': []}
    first = entries[0]['value']
    same = all(type(e['value']) is type(first) and e['value'] == first for e in entries)
    state = ('conflict' if not same else 'null' if first is None
             else 'provided' if valid(first) else 'invalid')
    return {'state': state, 'value': first if state == 'provided' else None,
            'pointers': [e['pointer'] for e in entries]}


def _temporal_attributes(item: dict, pointer: str, endpoint: str) -> dict:
    # Endpoint-specific event attributes, never a replacement for snapshot clocks.
    names = (('created_timestamp', 'open_timestamp', 'complete_timestamp', 'complete_cost_time')
             if endpoint == 'trenches' else ('creation_timestamp', 'open_timestamp'))
    result = {}
    for name in names:
        value = item.get(name)
        text = _value_text(value)
        seconds = None
        duration = name == 'complete_cost_time'
        if name not in item:
            state = 'absent'
        elif value is None:
            state = 'null'
        elif value == '':
            state = 'empty'
        elif type(value) is not int and not (isinstance(value, str) and _INTEGER.fullmatch(value)):
            state = 'invalid_type'
        elif len(str(value)) > 20 or not -(2**63) <= int(value) <= 2**63 - 1:
            state = 'invalid_range'
        elif int(value) < 0:
            state = 'negative_invalid'
        elif int(value) == 0 and not duration:
            state = 'zero_unusable'
        else:
            state, seconds = 'reported', str(int(value))
        result[name] = {'state': state, 'pointer': pointer + '/' + name if name in item else None,
                        'value_text': text, 'usable_seconds_text': seconds,
                        'unit': 'second' if duration else 'unix_second'}
    return result


def build_observation_context(fact: dict, capture: dict, raw: bytes) -> dict:
    """Read only the explicit endpoint shapes already accepted by the normalizer."""
    try:
        if len(raw) > MAX_RAW_BYTES:
            raise ContextUnavailable('context_payload_limit')
        root = json.loads(raw.decode('utf-8-sig'), parse_float=Decimal,
                          parse_constant=_constant, object_pairs_hook=_pairs)
        _nodes(root, max_nodes=MAX_TRENCH_JSON_NODES if capture['endpoint'] == 'trenches' else None)
        entities, issues = _entities(root, capture['endpoint'])
        if any(issue.code != 'invalid_token_item' for issue in issues):
            raise ContextUnavailable('context_shape_unverified')
        candidates = [(pointer, item) for pointer, item in entities
                      if fact['pointer'].startswith(pointer + '/')]
        if len(candidates) != 1:
            raise ContextUnavailable('context_entity_ambiguous')
        pointer, item = candidates[0]
        identity = canonical_identity(fact['chain'], item.get('address'))
        if identity != (fact['chain'], fact['token_address']):
            raise ContextUnavailable('context_identity_mismatch')
    except (ValueError, UnicodeError, RecursionError, _PayloadError) as exc:
        raise ContextUnavailable('context_evidence_unavailable') from exc

    endpoint = capture['endpoint']
    parts = pointer.split('/')
    indexed = endpoint in {'market_rank', 'trenches'}
    index = int(parts[-1]) if indexed else None
    category = parts[-2] if endpoint == 'trenches' else None
    stage = {'new_creation': 'new_creation', 'pump': 'near_completion',
             'near_completion': 'near_completion', 'completed': 'completed'}.get(category)
    rank = {'state': 'not_applicable', 'value_text': None, 'pointer': None}
    if endpoint == 'market_rank':
        value = item.get('rank')
        rank = {'state': 'missing' if 'rank' not in item else 'null' if value is None
                else 'reported' if type(value) is int and 1 <= value <= 2**63-1 else 'invalid',
                'value_text': None, 'pointer': pointer + '/rank' if 'rank' in item else None}
        if rank['state'] == 'reported':
            rank['value_text'] = str(value)

    params = capture['metadata']['request_params']
    scopes = [('/request_params', params)]
    query = params.get('query')
    if isinstance(query, dict):
        scopes.append(('/request_params/query', query))
    # Only the request category matching this response item is considered.
    request_category = 'near_completion' if category == 'pump' else category
    if request_category:
        for base, container in [('/request_params', params), ('/request_params/body', params.get('body'))]:
            if isinstance(container, dict) and isinstance(container.get(request_category), dict):
                scopes.append((base + '/' + request_category, container[request_category]))

    text = lambda x: isinstance(x, str) and bool(x)
    selection = []
    for base, values in scopes:
        subset = {k: v for k, v in values.items() if k in {
            'filter', 'filters', 'platform', 'launchpad_platform', 'launchpad_platform_v2',
            'quote_address_type'} or k.startswith(('min_', 'max_'))}
        if subset:
            selection.append({'pointer': base, 'parameters': subset})
    window = (_parameter(scopes[:2] if isinstance(query, dict) else scopes[:1], ('interval',),
                         lambda x: isinstance(x, str) and x in WINDOWS)
              if endpoint == 'market_rank' else {'state': 'not_applicable', 'value': None, 'pointers': []})
    if endpoint == 'market_rank' and 'query' in params and not isinstance(query, dict):
        window = {'state': 'invalid', 'value': None, 'pointers': ['/request_params/query']}
    clocks = capture['metadata']
    temporal = _temporal_attributes(item, pointer, endpoint)
    consistent = None
    if endpoint == 'trenches':
        values = [temporal[name]['usable_seconds_text'] for name in
                  ('created_timestamp', 'complete_timestamp', 'complete_cost_time')]
        if all(value is not None for value in values):
            created, completed, duration = map(int, values)
            consistent = completed >= created and completed - created == duration
    requested_limit = _parameter(scopes, ('limit',), lambda x: type(x) is int and x > 0)
    list_count, list_pointer = None, None
    comparison = 'not_applicable'
    if indexed:
        container = root
        for segment in parts[1:-1]:
            container = container[segment]
        list_count, list_pointer = len(container), '/'.join(parts[:-1])
        comparison = ('exceeded' if list_count > requested_limit['value'] else 'within_requested') if requested_limit['state'] == 'provided' else 'unknown'
    return {
        'context_version': CONTEXT_VERSION,
        'source': {'endpoint': endpoint, 'source_kind': fact['source_kind'],
                   'evidence_kind': fact['evidence_kind'], 'capture_id': fact['capture_id'],
                   'raw_sha256': capture['raw_sha256'], 'http_status': clocks['http_status']},
        'entity': {'chain': fact['chain'], 'token_address': fact['token_address'],
                   'item_pointer': pointer, 'response_index': index,
                   'response_position': index + 1 if index is not None else None,
                   'response_category': category, 'lifecycle_stage': stage, 'reported_rank': rank},
        'request': {'parameters': params, 'window': window,
                    'order_by': _parameter(scopes, ('order_by',), text),
                    'direction': _parameter(scopes, ('direction',), lambda x: x in ('asc', 'desc')),
                    'limit': requested_limit,
                    'filter_parameters': selection,
                    'filter_basis': 'recorded_request_only; provider_defaults_not_inferred'},
        'response': {'raw_bytes': len(raw), 'list_pointer': list_pointer,
                     'list_item_count': list_count, 'requested_limit_comparison': comparison},
        'temporal_attributes': temporal,
        'temporal_consistency': {'duration_matches_reported_clocks': consistent,
                                 'basis': 'reported_attributes_only; zero_does_not_prove_event_absence'},
        'clocks': {'requested_at': clocks['requested_at'], 'received_at': clocks['received_at'],
                   'source_event_at': clocks.get('source_event_at'),
                   'capture_recorded_at': capture['capture_recorded_at'],
                   'occurred_at': fact['occurred_at'], 'time_basis': fact['time_basis'],
                   'available_at': fact['available_at']},
    }

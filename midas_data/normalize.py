"""Bounded, deterministic adapters for GMGN token_info and market_rank only.

No I/O, current clock, float conversion, inferred units, derived market cap or
recursive token discovery is used here. Original bytes belong to Evidence Store.
Unknown shapes still receive a complete bounded field catalogue, never facts.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .contracts import (CaptureMetadata, Fact, FieldObservation, Issue, NormalizationResult,
                        MAX_POINTER_BYTES, MAX_TOKEN_ADDRESS_BYTES)

NORMALIZER_VERSION = 'gmgn-p0-v2'
MAX_RAW_BYTES = 8 * 1024 * 1024
MAX_JSON_NODES = 20_000  # Includes root, containers and leaves; no silent truncation.
MAX_JSON_DEPTH = 32  # Root depth is zero.
MAX_FACTS = 10_000  # Downstream batch contract; overflow is not a partial batch.
MAX_OUTPUT_BYTES = 16 * 1024 * 1024  # Compact UTF-8 JSON of {facts: [...], fields: [...]}.
FATAL_ISSUE_CODES = frozenset({
    'payload_too_large', 'invalid_utf8', 'invalid_unicode', 'invalid_json', 'duplicate_json_key',
    'non_finite_json_number', 'json_structure_limit', 'http_error',
    'provider_error', 'identity_conflict', 'missing_token_identity',
    'fact_count_limit', 'output_size_limit', 'pointer_size_limit', 'token_identity_limit',
})
UNSUPPORTED_ISSUE_CODES = frozenset({'unsupported_endpoint', 'unsupported_shape'})
_FACT_NAMESPACE = uuid5(NAMESPACE_URL, 'https://midas.local/contracts/fact/v1')
_EVM_CHAINS = frozenset({'eth', 'bsc', 'base', 'robinhood', 'arc', 'stable'})
_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z')
_INTEGER = re.compile(r'-?(?:0|[1-9][0-9]*)\Z')


@dataclass(frozen=True)
class _Spec:
    metric: str
    value_type: str
    category: str
    unit: str | None = None
    quality: str = 'unverified'
    nonnegative: bool = False
    positive: bool = False
    allowed_integers: frozenset[int] | None = None


def _specs(endpoint: str) -> dict[tuple[str, ...], _Spec]:
    """Whitelist exact paths. Similar names and aliases are not equivalent."""
    specs: dict[tuple[str, ...], _Spec] = {}
    for name in ('name', 'symbol'):
        specs[(name,)] = _Spec(name, 'string', 'identity', quality='valid')
    for name in ('decimals', 'holder_count'):
        specs[(name,)] = _Spec(name, 'integer', 'supply' if name == 'decimals' else 'holders', nonnegative=True)
    for name in ('total_supply', 'circulating_supply', 'max_supply'):
        specs[(name,)] = _Spec(name, 'decimal', 'supply', nonnegative=True)
    for name in ('creation_timestamp', 'open_timestamp', 'migrated_timestamp'):
        # Retain provider numbers. This adapter does not choose seconds vs ms.
        specs[(name,)] = _Spec(name, 'integer', 'temporal', nonnegative=True)
    for name in ('liquidity', 'initial_liquidity'):
        # Scope differs by endpoint; no pool/token aggregation is implied.
        specs[(name,)] = _Spec(name, 'decimal', 'liquidity', nonnegative=True)
    for name in ('launchpad', 'launchpad_status', 'launchpad_platform'):
        specs[(name,)] = _Spec(name, 'string', 'launch')
    for name in ('launchpad_progress', 'locked_ratio'):
        specs[(name,)] = _Spec(name, 'decimal', 'risk')

    if endpoint == 'token_info':
        # token_info documents numeric 0/1/2. Ranking status has a different
        # source contract and remains its original unverified string field.
        specs[('launchpad_status',)] = _Spec('launchpad_status', 'integer', 'launch',
            quality='valid', allowed_integers=frozenset({0, 1, 2}))
        # USD/token is documented for this exact path. Valid means this adapter
        # contract passed, not independent verification of upstream economics.
        specs[('price', 'price')] = _Spec('price', 'decimal', 'valuation', 'usd_per_token', 'valid', positive=True)
        specs[('ath_price',)] = _Spec('ath_price', 'decimal', 'valuation', positive=True)
        specs[('migration_market_cap',)] = _Spec('migration_market_cap', 'decimal', 'valuation', nonnegative=True)
        for name in (
            'top_10_holder_rate', 'dev_team_hold_rate', 'top_rat_trader_percentage',
            'top_bundler_trader_percentage', 'top_entrapment_trader_percentage', 'bot_degen_rate',
        ):
            specs[('stat', name)] = _Spec(name, 'decimal', 'risk')
        specs[('stat', 'bot_degen_count')] = _Spec('bot_degen_count', 'integer', 'holders', nonnegative=True)
        for name in ('smart_wallets', 'renowned_wallets', 'sniper_wallets'):
            specs[('wallet_tags_stat', name)] = _Spec(name, 'integer', 'holders', nonnegative=True)
        for name in ('twitter_username', 'twitter', 'telegram', 'website', 'description'):
            specs[('link', name)] = _Spec(name, 'string', 'social')
        for window in ('1m', '5m', '1h', '6h', '24h'):
            specs[('price', f'price_{window}')] = _Spec(f'window_reference_price_{window}', 'decimal', 'valuation', 'usd_per_token', positive=True)
            for name in ('volume', 'buy_volume', 'sell_volume'):
                specs[('price', f'{name}_{window}')] = _Spec(f'{name}_{window}', 'decimal', 'flow', 'usd', nonnegative=True)
            for name in ('buys', 'sells', 'swaps'):
                specs[('price', f'{name}_{window}')] = _Spec(f'{name}_{window}', 'integer', 'flow', 'count', nonnegative=True)
    elif endpoint == 'market_rank':
        for name in ('price', 'market_cap', 'history_highest_market_cap'):
            specs[(name,)] = _Spec(name, 'decimal', 'valuation', positive=name == 'price', nonnegative=name != 'price')
        for name in (
            'buy_tax', 'sell_tax', 'rug_ratio', 'bundler_rate', 'bundler_trader_amount_rate',
            'insider_rate', 'rat_trader_amount_rate', 'entrapment_ratio', 'top_10_holder_rate',
            'top70_sniper_hold_rate', 'dev_team_hold_rate', 'burn_ratio', 'bot_degen_rate',
        ):
            specs[(name,)] = _Spec(name, 'decimal', 'risk')
        for name in ('swaps', 'buys', 'sells', 'visiting_count', 'smart_degen_count', 'renowned_count', 'bot_degen_count'):
            specs[(name,)] = _Spec(name, 'integer', 'flow' if name in {'swaps', 'buys', 'sells'} else 'holders', nonnegative=True)
        for name in ('volume', 'price_change_percent', 'price_change_percent1m', 'price_change_percent5m', 'price_change_percent1h'):
            specs[(name,)] = _Spec(name, 'decimal', 'flow' if name == 'volume' else 'valuation', nonnegative=name == 'volume')
        for name in ('is_honeypot', 'is_open_source', 'is_renounced', 'renounced_mint', 'renounced_freeze_account', 'is_wash_trading', 'creator_close'):
            specs[(name,)] = _Spec(name, 'boolean', 'risk')
    return specs


class _PayloadError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _PayloadError('duplicate_json_key', 'Duplicate object key; no last-value-wins parsing is allowed.')
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise _PayloadError('non_finite_json_number', 'Non-finite JSON number is forbidden.')


def _pointer(parent: str, key: str | int) -> str:
    key = str(key)
    # Reject giant components before copying/escaping them into every prefix.
    if len(parent) + len(key) + 1 > MAX_POINTER_BYTES:
        raise _PayloadError('pointer_size_limit', 'JSON pointer exceeds its UTF-8 byte limit.')
    try:
        size = len(parent.encode('utf-8')) + len(key.encode('utf-8')) + key.count('~') + key.count('/') + 1
    except UnicodeEncodeError:
        raise _PayloadError('invalid_unicode', 'JSON contains an unpaired Unicode surrogate.') from None
    if size > MAX_POINTER_BYTES:
        raise _PayloadError('pointer_size_limit', 'JSON pointer exceeds its UTF-8 byte limit.')
    escaped = key.replace('~', '~0').replace('/', '~1')
    return f'{parent}/{escaped}'


class _OutputBudget:
    """Exact byte accounting without serializing the entire output in memory."""
    def __init__(self) -> None:
        self.bytes = len(b'{"facts":[],"fields":[]}')
        self.field_count = 0
        self.fact_count = 0
        self._grow(0)

    @staticmethod
    def _size(record: Fact | FieldObservation) -> int:
        return len(record.model_dump_json().encode('utf-8'))

    def _grow(self, amount: int) -> None:
        if self.bytes + amount > MAX_OUTPUT_BYTES:
            raise _PayloadError('output_size_limit', 'Normalized facts and fields exceed the aggregate UTF-8 JSON byte budget.')
        self.bytes += amount

    def add_field(self, record: FieldObservation) -> None:
        self._grow(self._size(record) + int(self.field_count > 0))
        self.field_count += 1

    def replace_field(self, previous: FieldObservation, record: FieldObservation) -> None:
        self._grow(self._size(record) - self._size(previous))

    def add_fact(self, record: Fact) -> None:
        if self.fact_count >= MAX_FACTS:
            raise _PayloadError('fact_count_limit', 'Capture exceeds the maximum number of normalized facts.')
        self._grow(self._size(record) + int(self.fact_count > 0))
        self.fact_count += 1


def _replace_field(observations: dict[str, FieldObservation], budget: _OutputBudget,
                   record: FieldObservation) -> None:
    budget.replace_field(observations[record.pointer], record)
    observations[record.pointer] = record


def _nodes(root: Any) -> list[tuple[str, Any]]:
    stack = [('', root, 0)]
    nodes = []
    pointer_bytes = 0

    def schedule(parent: str, key: str | int, child: Any, depth: int) -> None:
        nonlocal pointer_bytes
        child_pointer = _pointer(parent, key)
        pointer_bytes += len(child_pointer.encode('utf-8'))
        if pointer_bytes > MAX_OUTPUT_BYTES:
            raise _PayloadError('output_size_limit', 'Aggregate JSON pointer bytes exceed the output budget before catalogue creation.')
        stack.append((child_pointer, child, depth))

    while stack:
        pointer, value, depth = stack.pop()
        if depth > MAX_JSON_DEPTH or len(nodes) >= MAX_JSON_NODES:
            raise _PayloadError('json_structure_limit', 'JSON node or depth limit exceeded; nothing was partially catalogued.')
        if '\x00' in pointer or isinstance(value, str) and '\x00' in value:
            raise _PayloadError('invalid_unicode', 'JSON text contains NUL, unsupported by the storage text contract.')
        try:
            pointer.encode('utf-8')
            if isinstance(value, str):
                value.encode('utf-8')
        except UnicodeEncodeError:
            raise _PayloadError('invalid_unicode', 'JSON contains an unpaired Unicode surrogate.') from None
        nodes.append((pointer, value))
        if isinstance(value, (dict, list)) and len(nodes) + len(stack) + len(value) > MAX_JSON_NODES:
            raise _PayloadError('json_structure_limit', 'JSON node limit exceeded; nothing was partially catalogued.')
        if isinstance(value, dict):
            for key, child in reversed(list(value.items())):
                schedule(pointer, key, child, depth + 1)
        elif isinstance(value, list):
            for index in range(len(value) - 1, -1, -1):
                schedule(pointer, index, value[index], depth + 1)
    return nodes


def _json_type(value: Any) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, Decimal):
        return 'decimal'
    if isinstance(value, str):
        return 'string'
    return 'object' if isinstance(value, dict) else 'array'


def _value_text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _check(value: Any, spec: _Spec) -> tuple[str, str]:
    if value is None:
        return 'missing', 'explicit_null'
    if value == '':
        return 'missing', 'explicit_empty_string'
    if spec.value_type == 'string':
        valid = isinstance(value, str)
    elif spec.value_type == 'boolean':
        # No truthiness, numeric 0/1, or string truth-value coercion.
        valid = isinstance(value, bool)
    else:
        if isinstance(value, bool) or not isinstance(value, (int, Decimal, str)):
            return 'invalid', 'expected_numeric_scalar'
        text = str(value)
        if not _NUMBER.fullmatch(text):
            return 'invalid', 'invalid_numeric_syntax'
        try:
            number = Decimal(text)
        except InvalidOperation:
            return 'invalid', 'invalid_numeric_syntax'
        if not number.is_finite():
            return 'invalid', 'non_finite_number'
        if spec.value_type == 'integer':
            # Deliberately do not cast 1.5 -> 1 or accept undocumented string
            # exponent/decimal syntax for an integer counter.
            valid = isinstance(value, int) or (isinstance(value, str) and bool(_INTEGER.fullmatch(value)))
        else:
            valid = True
        if spec.allowed_integers is not None:
            if type(value) is not int:
                return 'invalid', 'expected_integer_enum'
            if value not in spec.allowed_integers:
                return 'invalid', 'enum_value_not_allowed'
        if valid and spec.positive and number <= 0:
            return 'invalid', 'expected_positive_value'
        if valid and spec.nonnegative and number < 0:
            return 'invalid', 'expected_nonnegative_value'
    if not valid:
        return 'invalid', f'expected_{spec.value_type}'
    return spec.quality, 'contract_checked' if spec.quality == 'valid' else 'unit_scale_window_or_scope_unverified'


def _address(value: Any, chain: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if len(value.encode('utf-8')) > MAX_TOKEN_ADDRESS_BYTES:
        raise _PayloadError('token_identity_limit', 'Token identity exceeds the supported UTF-8 byte limit; no address role is inferred.')
    if value != value.strip() or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    if chain in _EVM_CHAINS and re.fullmatch(r'0x[0-9a-fA-F]{40}', value):
        return value.lower()
    return value  # Solana and unknown namespaces remain case-sensitive.


def canonical_identity(chain: str, token: str) -> tuple[str, str]:
    """Canonicalize the query namespace without inferring chain/address aliases.

    Known EVM 0x+40-hex addresses use lowercase. Solana and other namespaces
    retain address case; this validates transport identity, not on-chain existence.
    """
    if not isinstance(chain, str) or not chain or len(chain) > 64 or chain != chain.strip():
        raise ValueError('invalid chain identity')
    if any(char.isspace() or ord(char) < 32 for char in chain):
        raise ValueError('invalid chain identity')
    normalized_chain = chain.casefold()
    try:
        normalized_chain.encode('utf-8')
        address = _address(token, normalized_chain)
    except (_PayloadError, UnicodeEncodeError):
        raise ValueError('invalid token identity') from None
    if address is None:
        raise ValueError('invalid token identity')
    return normalized_chain, address


def _id(metadata: CaptureMetadata, pointer: str, metric: str) -> UUID:
    # JSON tuple encoding avoids delimiter collisions in provider field names.
    name = json.dumps([str(metadata.capture_id), NORMALIZER_VERSION, pointer, metric], separators=(',', ':'))
    return uuid5(_FACT_NAMESPACE, name)


def _issue(code: str, pointer: str, detail: str) -> Issue:
    return Issue(code=code, pointer=pointer, detail=detail)


def _entities(root: Any, endpoint: str) -> tuple[list[tuple[str, dict[str, Any]]], list[Issue]]:
    data, base = root, ''
    if isinstance(root, dict) and 'data' in root:
        if 'address' in root or 'rank' in root:
            return [], [_issue('unsupported_shape', '', 'Ambiguous envelope and direct-object structure.')]
        data, base = root['data'], '/data'
    if isinstance(data, dict) and 'code' in data and (type(data['code']) is not int or data['code'] != 0):
        return [], [_issue('provider_error', _pointer(base, 'code'), 'Inner provider status is not the supported integer success code 0.')]
    if endpoint == 'token_info' and isinstance(data, dict):
        return [(base, data)], []
    if endpoint == 'market_rank':
        if isinstance(data, dict) and ('code' in data or 'data' in data):
            # Only the observed second HTTP envelope is supported. Do not
            # recurse into arbitrary data objects or select an ambiguous rank.
            nested = data.get('data')
            exact_outer = (isinstance(root, dict) and set(root) == {'code', 'data'}
                           and type(root.get('code')) is int and root['code'] == 0 and base == '/data')
            exact_inner = ({'code', 'data'} <= set(data) <= {'code', 'data', 'message', 'reason'}
                           and type(data.get('code')) is int and data['code'] == 0)
            exact_rank = isinstance(nested, dict) and set(nested) == {'rank'} and isinstance(nested['rank'], list)
            if not (exact_outer and exact_inner and exact_rank):
                return [], [_issue('unsupported_shape', base, 'Nested ranking requires the exact two-success-envelope contract and one rank array.')]
            data, base = nested, _pointer(base, 'data')
        if isinstance(data, dict) and isinstance(data.get('rank'), list):
            data, base = data['rank'], _pointer(base, 'rank')
        if isinstance(data, list):
            entities, issues = [], []
            for index, value in enumerate(data):
                pointer = _pointer(base, index)
                if isinstance(value, dict):
                    entities.append((pointer, value))
                else:
                    issues.append(_issue('invalid_token_item', pointer, 'Ranking item is not a token object; its fields remain catalogued.'))
            return entities, issues
    return [], [_issue('unsupported_shape', base, 'Endpoint JSON structure has no explicitly supported adapter.')]


def _normalize_capture(metadata: CaptureMetadata, raw: bytes) -> NormalizationResult:
    """Normalize a capture without changing raw evidence or performing I/O.

    Fatal/unsupported issue sets are public so the writer can quarantine or
    retain unsupported captures. Absent keys generate no invented fact; explicit
    null and empty strings generate missing facts at their actual pointers.
    """
    if not isinstance(metadata, CaptureMetadata):
        raise TypeError('metadata must be CaptureMetadata')
    if not isinstance(raw, bytes):
        raise TypeError('raw must be original bytes')
    result = NormalizationResult(normalizer_version=NORMALIZER_VERSION)
    try:
        if len(raw) > MAX_RAW_BYTES:
            raise _PayloadError('payload_too_large', 'Raw response exceeds the normalizer byte limit.')
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise _PayloadError('invalid_utf8', 'Response is not UTF-8 JSON.') from None
        try:
            root = json.loads(text, parse_float=Decimal, parse_int=int, parse_constant=_constant, object_pairs_hook=_pairs)
        except _PayloadError:
            raise
        except (ValueError, InvalidOperation, RecursionError):
            raise _PayloadError('invalid_json', 'Response cannot be parsed as bounded, exact JSON.') from None
        nodes = _nodes(root)
    except _PayloadError as exc:
        result.issues.append(_issue(exc.code, '', exc.detail))
        return result

    budget = _OutputBudget()
    observations = {}
    for pointer, value in nodes:
        observation = FieldObservation(pointer=pointer, category='unknown', value_type=_json_type(value),
                                       quality='missing' if value is None else 'unverified',
                                       reason='explicit_null' if value is None else 'unmapped_field')
        budget.add_field(observation)
        observations[pointer] = observation
    result.fields.extend(observations.values())
    if not 200 <= metadata.http_status < 300:
        result.issues.append(_issue('http_error', '', 'HTTP response is not successful; no facts were promoted.'))
        return result
    if isinstance(root, dict) and 'code' in root and (type(root['code']) is not int or root['code'] != 0):
        result.issues.append(_issue('provider_error', '/code', 'Provider status is not the supported integer success code 0.'))
        return result
    if metadata.endpoint not in {'token_info', 'market_rank'}:
        result.issues.append(_issue('unsupported_endpoint', '', 'No adapter is registered for this endpoint.'))
        return result

    entities, issues = _entities(root, metadata.endpoint)
    result.issues.extend(issues)
    if any(issue.code in UNSUPPORTED_ISSUE_CODES for issue in issues):
        return result
    chain = metadata.chain.casefold()
    if isinstance(root, dict) and 'data' in root and 'chain' in root:
        if not isinstance(root['chain'], str) or root['chain'].casefold() != chain:
            result.issues.append(_issue('identity_conflict', '/chain', 'Envelope chain conflicts with capture metadata.'))
    elif metadata.endpoint == 'market_rank' and isinstance(root, dict) and 'chain' in root:
        if not isinstance(root['chain'], str) or root['chain'].casefold() != chain:
            result.issues.append(_issue('identity_conflict', '/chain', 'Ranking chain conflicts with capture metadata.'))
    specs = _specs(metadata.endpoint)
    node_values = dict(nodes)
    for base, item in entities:
        address = _address(item.get('address'), chain)
        if address is None:
            code = 'missing_token_identity' if metadata.endpoint == 'token_info' else 'invalid_token_item'
            result.issues.append(_issue(code, _pointer(base, 'address'), 'Token address is absent or invalid at the supported token-object path.'))
        if 'chain' in item and (not isinstance(item['chain'], str) or item['chain'].casefold() != chain):
            result.issues.append(_issue('identity_conflict', _pointer(base, 'chain'), 'Response token chain conflicts with capture metadata.'))
        if metadata.endpoint == 'token_info' and address is not None:
            for key in ('address', 'token_address'):
                if key in metadata.request_params:
                    expected = _address(metadata.request_params[key], chain)
                    if expected is None or expected != address:
                        result.issues.append(_issue('identity_conflict', _pointer(base, 'address'), 'Response token address conflicts with the explicit request address.'))
        for key in ('address', 'chain'):
            pointer = _pointer(base, key)
            if pointer in node_values:
                valid_identity = address is not None if key == 'address' else isinstance(item[key], str) and item[key].casefold() == chain
                _replace_field(observations, budget, FieldObservation(pointer=pointer, category='identity', value_type=_json_type(item[key]),
                    quality='valid' if valid_identity else 'invalid', reason='identity_role_checked' if valid_identity else 'invalid_identity',
                    contract_id=f'gmgn.{metadata.endpoint}.v2:{key}'))
        for path, spec in specs.items():
            pointer = base
            for component in path:
                pointer = _pointer(pointer, component)
            if pointer not in node_values:
                continue
            value = node_values[pointer]
            quality, reason = _check(value, spec)
            _replace_field(observations, budget, FieldObservation(pointer=pointer, category=spec.category, value_type=_json_type(value),
                quality=quality, reason=reason, contract_id=f'gmgn.{metadata.endpoint}.v2:{".".join(path)}'))
            if quality == 'invalid':
                result.issues.append(_issue('invalid_field', pointer, f'Value does not satisfy the {spec.value_type} field contract: {reason}.'))
            if address is None:
                continue
            if len(result.facts) >= MAX_FACTS:
                raise _PayloadError('fact_count_limit', 'Capture exceeds the maximum number of normalized facts.')
            fact = Fact(fact_id=_id(metadata, pointer, spec.metric), capture_id=metadata.capture_id,
                chain=chain, token_address=address, metric=spec.metric, value_text=_value_text(value),
                value_type=spec.value_type, unit=spec.unit, category=spec.category,
                occurred_at=metadata.source_event_at if metadata.source_event_at is not None else metadata.received_at,
                time_basis='source' if metadata.source_event_at is not None else 'received',
                pointer=pointer, quality=quality, contract_version=f'gmgn.{metadata.endpoint}.v2')
            budget.add_fact(fact)
            result.facts.append(fact)
    result.fields[:] = list(observations.values())
    if any(issue.code in FATAL_ISSUE_CODES for issue in result.issues):
        result.facts.clear()  # A conflicting identity invalidates the whole capture.
    return result


def normalize_capture(metadata: CaptureMetadata, raw: bytes) -> NormalizationResult:
    """Return bounded v2 output, or one explicit fatal issue without partial rows.

    Limits: 10,000 facts; 16 MiB compact UTF-8 JSON for facts+fields; 2,048 bytes
    per pointer; 256 bytes per token identity. Raw evidence is retained by the
    caller, including when a capture cannot fit this downstream batch contract.
    """
    try:
        return _normalize_capture(metadata, raw)
    except _PayloadError as exc:
        return NormalizationResult(normalizer_version=NORMALIZER_VERSION,
                                   issues=[_issue(exc.code, '', exc.detail)])

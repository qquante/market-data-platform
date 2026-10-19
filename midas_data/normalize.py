"""Bounded, deterministic adapters for GMGN token observations.

No I/O, current clock, float conversion, inferred units, derived market cap or
recursive token discovery is used here. Original bytes belong to Evidence Store.
Unknown shapes still receive a complete bounded field catalogue, never facts.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .contracts import (CaptureMetadata, Fact, FieldObservation, Issue, NormalizationResult,
                        MAX_POINTER_BYTES, MAX_TOKEN_ADDRESS_BYTES)

NORMALIZER_VERSION = 'gmgn-p0-v4'
CONTRACT_VERSIONS = {'token_info': 'v3', 'market_rank': 'v4', 'trenches': 'v1'}
TRENCH_GROUPS = ('new_creation', 'pump', 'near_completion', 'completed')
MAX_TRENCH_JSON_NODES = 40_000  # Observed 3x60-token bodies exceed 20k; supports 3x80 within output/fact budgets.
WINDOWS = frozenset({'1m', '5m', '1h', '6h', '24h'})
MAX_RAW_BYTES = 8 * 1024 * 1024
MAX_JSON_NODES = 20_000  # Includes root, containers and leaves; no silent truncation.
MAX_JSON_DEPTH = 32  # Root depth is zero.
MAX_FACTS = 10_000  # Downstream batch contract; overflow is not a partial batch.
MAX_OUTPUT_BYTES = 16 * 1024 * 1024  # Compact UTF-8 JSON of {facts: [...], fields: [...]}.
FATAL_ISSUE_CODES = frozenset({
    'payload_too_large', 'invalid_utf8', 'invalid_unicode', 'invalid_json', 'duplicate_json_key',
    'non_finite_json_number', 'json_structure_limit', 'http_error',
    'provider_error', 'identity_conflict', 'missing_token_identity', 'candle_contract_error',
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
    fraction: bool = False
    reason: str | None = None
    supported_chains: frozenset[str] | None = None


def _trench_specs() -> dict[tuple[str, ...], _Spec]:
    """Exact Trenches paths; never inherit ranking aliases or units."""
    specs = {}
    for name in ('name', 'symbol', 'launchpad_platform', 'exchange'):
        specs[(name,)] = _Spec(name, 'string', 'identity' if name in {'name', 'symbol'} else 'launch',
                              quality='valid', reason='provider_untrusted_metadata_text')
    specs[('usd_market_cap',)] = _Spec('usd_market_cap', 'decimal', 'valuation', 'usd', 'valid',
        nonnegative=True, reason='provider_reported_usd_cap_supply_basis_unspecified')
    # Observed market_cap is not silently aliased to the documented usd_market_cap.
    specs[('market_cap',)] = _Spec('market_cap', 'decimal', 'valuation', nonnegative=True,
        reason='trenches_market_cap_alias_currency_supply_basis_unverified')
    specs[('price',)] = _Spec('price', 'decimal', 'valuation', positive=True,
        reason='trenches_exact_price_path_currency_unverified')
    specs[('liquidity',)] = _Spec('liquidity', 'decimal', 'liquidity', 'usd', nonnegative=True,
        reason='trenches_liquidity_pool_vs_aggregate_scope_unverified')
    specs[('total_supply',)] = _Spec('total_supply', 'decimal', 'supply', nonnegative=True,
        reason='human_vs_atomic_supply_scale_unverified')
    specs[('decimals',)] = _Spec('decimals', 'integer', 'supply', nonnegative=True,
        reason='trenches_exact_decimals_path_contract_unverified')
    for name in ('created_timestamp', 'open_timestamp', 'complete_timestamp'):
        specs[(name,)] = _Spec(name, 'integer', 'temporal', 'unix_second', 'valid', positive=True,
            reason='provider_event_attribute_unix_seconds')
    specs[('complete_cost_time',)] = _Spec('complete_cost_time', 'integer', 'temporal', 'second', 'valid',
        nonnegative=True, reason='provider_creation_to_completion_duration')
    for name in ('holder_count', 'smart_degen_count', 'renowned_count', 'sniper_count'):
        specs[(name,)] = _Spec(name, 'integer', 'holders', 'count', 'valid', nonnegative=True,
            reason='provider_holder_count_not_independent_wallet_verification')
    for name in ('swaps_1m', 'swaps_1h', 'swaps_24h', 'buys_24h', 'sells_24h'):
        specs[(name,)] = _Spec(name, 'integer', 'flow', 'count', 'valid', nonnegative=True,
            reason='provider_transaction_count_named_window')
    for name in ('volume_1h', 'volume_24h', 'net_buy_24h'):
        specs[(name,)] = _Spec(name, 'decimal', 'flow', 'usd', 'valid', nonnegative=name != 'net_buy_24h',
            reason='provider_volume_named_window_no_cross_window_sum')
    for name in ('twitter', 'telegram', 'website', 'instagram', 'tiktok'):
        specs[(name,)] = _Spec(name, 'string', 'social', quality='valid',
            reason='provider_untrusted_link_not_verified_post_or_affiliation')
    specs[('x_user_follower',)] = _Spec('x_user_follower', 'integer', 'social', 'count', 'valid',
        nonnegative=True, reason='provider_profile_follower_count_not_post_content')
    return specs


def _rank_window(params: dict[str, Any]) -> tuple[str | None, str]:
    values = [params['interval']] if 'interval' in params else []
    query = params.get('query')
    if query is not None and not isinstance(query, dict):
        return None, 'ranking_query_parameters_invalid'
    if isinstance(query, dict) and 'interval' in query:
        values.append(query['interval'])
    if not values:
        return None, 'queried_window_not_encoded_in_fact'
    if any(not isinstance(v, str) or v not in WINDOWS for v in values):
        return None, 'ranking_window_unsupported_or_invalid'
    if len(set(values)) != 1:
        return None, 'ranking_window_conflicts_between_parameter_locations'
    return values[0], 'provider_requested_window_end_not_measured'


def _specs(endpoint: str, params: dict[str, Any] | None = None) -> dict[tuple[str, ...], _Spec]:
    """Exact provider paths; documented contracts are endpoint-specific.

    FIELD_CONTRACTS.md and contracts/gmgn-p0-v3.json record the evidence and
    limitations. Valid is a provider-field contract, never an on-chain verdict.
    """
    if endpoint == 'trenches':
        return _trench_specs()
    specs: dict[tuple[str, ...], _Spec] = {}
    for name in ('name', 'symbol'):
        specs[(name,)] = _Spec(name, 'string', 'identity', quality='valid')
    specs[('decimals',)] = _Spec('decimals', 'integer', 'supply', nonnegative=True,
                               reason='exact_path_scale_contract_unverified')
    specs[('holder_count',)] = _Spec('holder_count', 'integer', 'holders', 'count', 'valid', nonnegative=True,
                                    reason='provider_unique_holder_count')
    for name in ('total_supply', 'circulating_supply', 'max_supply'):
        specs[(name,)] = _Spec(name, 'decimal', 'supply', nonnegative=True,
                              reason='human_vs_atomic_supply_scale_unverified')
    for name in ('creation_timestamp', 'open_timestamp'):
        # These are event attributes of the token. They never replace the
        # snapshot clock, and zero is not accepted as a real 1970 event.
        specs[(name,)] = _Spec(name, 'integer', 'temporal', 'unix_second', 'valid', positive=True,
                              reason='provider_event_attribute_unix_seconds')
    specs[('migrated_timestamp',)] = _Spec('migrated_timestamp', 'integer', 'temporal', nonnegative=True,
                                         reason='exact_migration_timestamp_path_unit_unverified')
    specs[('liquidity',)] = _Spec('liquidity', 'decimal', 'liquidity', nonnegative=True,
                                 reason='liquidity_currency_pool_scope_unverified')
    specs[('initial_liquidity',)] = _Spec('initial_liquidity', 'decimal', 'liquidity', nonnegative=True,
                                         reason='initial_liquidity_currency_event_scope_unverified')
    for name in ('launchpad', 'launchpad_status', 'launchpad_platform'):
        specs[(name,)] = _Spec(name, 'string', 'launch', reason='endpoint_launchpad_vocabulary_unverified')
    for name in ('launchpad_progress', 'locked_ratio'):
        specs[(name,)] = _Spec(name, 'decimal', 'risk', reason='endpoint_ratio_scale_denominator_unverified')

    if endpoint == 'token_info':
        specs[('decimals',)] = _Spec('decimals', 'integer', 'supply', 'decimal_places', 'valid', nonnegative=True)
        specs[('circulating_supply',)] = _Spec('circulating_supply', 'decimal', 'supply', 'token', 'valid', nonnegative=True,
                                             reason='provider_human_token_units_no_decimal_rescaling')
        specs[('liquidity',)] = _Spec('liquidity', 'decimal', 'liquidity', 'usd', 'valid', nonnegative=True,
                                      reason='provider_main_pool_liquidity_usd')
        for name in ('launchpad', 'launchpad_platform'):
            specs[(name,)] = _Spec(name, 'string', 'launch', quality='valid')
        # token_info numeric 0/1/2 is not the ranking string status vocabulary.
        specs[('launchpad_status',)] = _Spec('launchpad_status', 'integer', 'launch',
            quality='valid', allowed_integers=frozenset({0, 1, 2}))
        for name in ('launchpad_progress', 'locked_ratio'):
            specs[(name,)] = _Spec(name, 'decimal', 'risk', 'ratio', 'valid', fraction=True)
        specs[('price', 'price')] = _Spec('price', 'decimal', 'valuation', 'usd_per_token', 'valid', positive=True)
        specs[('ath_price',)] = _Spec('ath_price', 'decimal', 'valuation', 'usd_per_token', 'valid', positive=True,
                                    reason='provider_reported_ath_price_peak_time_unspecified')
        specs[('migration_market_cap',)] = _Spec('migration_market_cap', 'decimal', 'valuation', 'usd', nonnegative=True,
                                               reason='migration_cap_supply_basis_event_quote_unverified')
        for name in (
            'top_10_holder_rate', 'dev_team_hold_rate', 'top_rat_trader_percentage',
            'top_bundler_trader_percentage', 'top_entrapment_trader_percentage', 'bot_degen_rate',
        ):
            specs[('stat', name)] = _Spec(name, 'decimal', 'risk', reason='ratio_scale_denominator_window_unverified')
        specs[('stat', 'top_10_holder_rate')] = _Spec('top_10_holder_rate', 'decimal', 'risk', 'ratio', 'valid', fraction=True,
                                                    reason='provider_top10_supply_share')
        for name in ('top_rat_trader_percentage', 'top_bundler_trader_percentage', 'top_entrapment_trader_percentage'):
            specs[('stat', name)] = replace(specs[('stat', name)], reason='volume_share_scale_window_unverified')
        specs[('stat', 'bot_degen_count')] = _Spec('bot_degen_count', 'integer', 'holders', 'count', 'valid', nonnegative=True)
        for name in ('smart_wallets', 'renowned_wallets', 'sniper_wallets'):
            specs[('wallet_tags_stat', name)] = _Spec(name, 'integer', 'holders', 'count', 'valid', nonnegative=True,
                                                     reason='provider_tagged_wallet_count_methodology_unspecified')
        for name in ('twitter_username', 'telegram', 'website', 'description'):
            specs[('link', name)] = _Spec(name, 'string', 'social', quality='valid', reason='provider_untrusted_metadata_text')
        specs[('link', 'twitter')] = _Spec('twitter', 'string', 'social', reason='undocumented_twitter_alias_not_username_or_url')
        for window in ('1m', '5m', '1h', '6h', '24h'):
            specs[('price', f'price_{window}')] = _Spec(f'window_reference_price_{window}', 'decimal', 'valuation',
                'usd_per_token', 'valid', positive=True, reason='provider_price_at_start_of_named_window')
            for name in ('volume', 'buy_volume', 'sell_volume'):
                specs[('price', f'{name}_{window}')] = _Spec(f'{name}_{window}', 'decimal', 'flow', 'usd', 'valid',
                    nonnegative=True, reason='provider_volume_named_window_no_cross_window_sum')
            for name in ('buys', 'sells', 'swaps'):
                specs[('price', f'{name}_{window}')] = _Spec(f'{name}_{window}', 'integer', 'flow', 'count', 'valid',
                    nonnegative=True, reason='provider_transaction_count_named_window')
    elif endpoint == 'market_rank':
        specs[('launchpad_platform',)] = _Spec('launchpad_platform', 'string', 'launch', quality='valid')
        specs[('price',)] = _Spec('price', 'decimal', 'valuation', 'usd_per_token', 'valid', positive=True)
        for name in ('market_cap', 'history_highest_market_cap'):
            specs[(name,)] = _Spec(name, 'decimal', 'valuation', 'usd', 'valid', nonnegative=True,
                                  reason='provider_reported_usd_cap_supply_basis_unspecified')
        specs[('liquidity',)] = _Spec('liquidity', 'decimal', 'liquidity', 'usd', nonnegative=True,
                                     reason='ranking_liquidity_pool_vs_aggregate_scope_unverified')
        for name in (
            'buy_tax', 'sell_tax', 'rug_ratio', 'bundler_rate', 'bundler_trader_amount_rate',
            'insider_rate', 'rat_trader_amount_rate', 'entrapment_ratio', 'top_10_holder_rate',
            'top70_sniper_hold_rate', 'dev_team_hold_rate', 'burn_ratio', 'bot_degen_rate',
        ):
            specs[(name,)] = _Spec(name, 'decimal', 'risk', reason='ratio_scale_denominator_window_unverified')
        for name in ('buy_tax', 'sell_tax'):
            specs[(name,)] = replace(specs[(name,)], reason='ranking_tax_scale_unverified_empty_sentinel_not_converted')
        for name in ('bundler_trader_amount_rate', 'insider_rate', 'burn_ratio'):
            specs[(name,)] = replace(specs[(name,)], reason='exact_ranking_path_semantics_unverified_no_alias')
        for name in ('bundler_rate', 'rat_trader_amount_rate'):
            specs[(name,)] = replace(specs[(name,)], reason='volume_share_scale_window_unverified')
        specs[('top_10_holder_rate',)] = _Spec('top_10_holder_rate', 'decimal', 'risk', 'ratio', 'valid', fraction=True,
                                             reason='provider_top10_supply_share')
        specs[('rug_ratio',)] = _Spec('rug_ratio', 'decimal', 'risk', 'score_0_1', 'valid', fraction=True,
                                    reason='provider_risk_score_not_calibrated_probability')
        for name in ('swaps', 'buys', 'sells'):
            specs[(name,)] = _Spec(name, 'integer', 'flow', 'count', nonnegative=True,
                                  reason='queried_window_not_encoded_in_fact')
        specs[('visiting_count',)] = _Spec('visiting_count', 'integer', 'attention', nonnegative=True,
                                         reason='visit_heat_unique_population_window_unverified')
        for name in ('smart_degen_count', 'renowned_count', 'bot_degen_count'):
            specs[(name,)] = _Spec(name, 'integer', 'holders', 'count', 'valid', nonnegative=True,
                                  reason='provider_tagged_wallet_count_methodology_unspecified')
        specs[('volume',)] = _Spec('volume', 'decimal', 'flow', 'usd', nonnegative=True,
                                  reason='queried_window_not_encoded_in_fact')
        for name in ('price_change_percent', 'price_change_percent1m', 'price_change_percent5m', 'price_change_percent1h'):
            specs[(name,)] = _Spec(name, 'decimal', 'valuation', reason='percent_vs_ratio_documentation_conflict')
        for name in ('is_honeypot', 'is_open_source', 'is_renounced', 'renounced_mint', 'renounced_freeze_account'):
            chains = (frozenset({'bsc', 'base'}) if name == 'is_honeypot' else
                      frozenset({'sol'}) if name in {'renounced_mint', 'renounced_freeze_account'} else None)
            specs[(name,)] = _Spec(name, 'integer', 'risk', 'boolean_code_01', 'valid',
                allowed_integers=frozenset({0, 1}), supported_chains=chains,
                reason='provider_integer_flag_not_independent_security_verdict')
        for name in ('is_wash_trading', 'creator_close'):
            specs[(name,)] = _Spec(name, 'boolean', 'risk', quality='valid',
                                  reason='provider_boolean_flag_not_independent_security_verdict')
    if endpoint == 'market_rank':
        window, reason = _rank_window(params or {})
        for name in ('volume', 'swaps', 'buys', 'sells'):
            spec = specs[(name,)]
            specs[(name,)] = replace(spec, metric=f'{name}_{window}' if window else name,
                                    quality='valid' if window else 'unverified', reason=reason)
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


def _nodes(root: Any, *, max_nodes: int | None = None) -> list[tuple[str, Any]]:
    node_limit = MAX_JSON_NODES if max_nodes is None else max_nodes
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
        if depth > MAX_JSON_DEPTH or len(nodes) >= node_limit:
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
        if isinstance(value, (dict, list)) and len(nodes) + len(stack) + len(value) > node_limit:
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
        if valid and spec.fraction and not Decimal(0) <= number <= Decimal(1):
            return 'invalid', 'expected_fraction_between_zero_and_one'
        if valid and spec.positive and number <= 0:
            return 'invalid', 'expected_positive_value'
        if valid and spec.nonnegative and number < 0:
            return 'invalid', 'expected_nonnegative_value'
    if not valid:
        return 'invalid', f'expected_{spec.value_type}'
    return spec.quality, spec.reason or 'provider_field_contract_checked'


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
    if endpoint == 'trenches' and isinstance(data, dict):
        if not any(group in data for group in TRENCH_GROUPS) or any(
            key in data for key in ('address', 'rank', 'data')
        ) or ('pump' in data and 'near_completion' in data):
            return [], [_issue('unsupported_shape', base, 'Trenches requires category arrays and only one of pump/near_completion.')]
        entities, issues = [], []
        for group in TRENCH_GROUPS:
            if group not in data:
                continue  # Omitted category is not an observed empty category.
            group_base = _pointer(base, group)
            if not isinstance(data[group], list):
                return [], [_issue('unsupported_shape', group_base, 'Trenches category is not an array.')]
            for index, item in enumerate(data[group]):
                pointer = _pointer(group_base, index)
                if isinstance(item, dict):
                    entities.append((pointer, item))
                else:
                    issues.append(_issue('invalid_token_item', pointer, 'Trenches item is not a token object.'))
        return entities, issues
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
        nodes = _nodes(root, max_nodes=MAX_TRENCH_JSON_NODES if metadata.endpoint == 'trenches' else None)
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
    if metadata.endpoint not in CONTRACT_VERSIONS:
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
    elif metadata.endpoint in {'market_rank', 'trenches'} and isinstance(root, dict) and 'chain' in root:
        if not isinstance(root['chain'], str) or root['chain'].casefold() != chain:
            result.issues.append(_issue('identity_conflict', '/chain', 'Ranking chain conflicts with capture metadata.'))
    if metadata.endpoint == 'trenches' and isinstance(root, dict) and isinstance(root.get('data'), dict):
        envelope_chain = root['data'].get('chain', chain)
        if not isinstance(envelope_chain, str) or envelope_chain.casefold() != chain:
            result.issues.append(_issue('identity_conflict', '/data/chain', 'Trenches chain conflicts with capture metadata.'))
    specs = _specs(metadata.endpoint, metadata.request_params)
    contract = f'gmgn.{metadata.endpoint}.{CONTRACT_VERSIONS[metadata.endpoint]}'
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
                    contract_id=f'{contract}:{key}'))
        for path, spec in specs.items():
            pointer = base
            for component in path:
                pointer = _pointer(pointer, component)
            if pointer not in node_values:
                continue
            value = node_values[pointer]
            quality, reason = _check(value, spec)
            if quality == 'valid' and spec.supported_chains is not None and chain not in spec.supported_chains:
                quality, reason = 'unverified', 'field_chain_applicability_unverified'
            _replace_field(observations, budget, FieldObservation(pointer=pointer, category=spec.category, value_type=_json_type(value),
                quality=quality, reason=reason, contract_id=f'{contract}:{".".join(path)}'))
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
                pointer=pointer, quality=quality, contract_version=contract)
            budget.add_fact(fact)
            result.facts.append(fact)
    result.fields[:] = list(observations.values())
    if any(issue.code in FATAL_ISSUE_CODES for issue in result.issues):
        result.facts.clear()  # A conflicting identity invalidates the whole capture.
    return result


def normalize_capture(metadata: CaptureMetadata, raw: bytes) -> NormalizationResult:
    """Return bounded versioned output, or one explicit fatal issue without partial rows.

    Limits: 10,000 facts; 16 MiB compact UTF-8 JSON for facts+fields; 2,048 bytes
    per pointer; 256 bytes per token identity. Raw evidence is retained by the
    caller, including when a capture cannot fit this downstream batch contract.
    """
    version = NORMALIZER_VERSION
    if isinstance(metadata, CaptureMetadata) and metadata.endpoint == 'token_kline':
        from .candle_normalization import normalize_candle_capture, VERSION
        version = VERSION
    try:
        result = _normalize_capture(metadata, raw)
        if version != NORMALIZER_VERSION:
            return normalize_candle_capture(metadata, raw, result)
        return result
    except _PayloadError as exc:
        return NormalizationResult(normalizer_version=version,
                                   issues=[_issue(exc.code, '', exc.detail)])

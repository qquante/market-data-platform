"""Bounded GMGN holders parser; original HTTP and explicit legacy data objects.

No I/O, SQL, clock reads or token Fact projection. The caller retains raw bytes.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, StrictStr, field_validator

from .contracts import (
    CaptureMetadata, ContractModel, FieldObservation, Issue, NonEmptyString,
    ValueType, MAX_POINTER_BYTES, _bounded_utf8, utc_datetime,
)
from . import normalize as common

CONTRACT_VERSION = 'gmgn.token_top_holders.v1'
PARSER_VERSION = 'gmgn-holders-v2'
MAX_ROWS = 100
MAX_OUTPUT_BYTES = common.MAX_OUTPUT_BYTES
_NAMESPACE = uuid5(NAMESPACE_URL, 'https://midas.local/contracts/holder/v1')
_ORDER_BY = frozenset({'amount_percentage', 'profit', 'unrealized_profit',
                       'buy_volume_cur', 'sell_volume_cur'})
_TAGS = frozenset({'smart_degen', 'renowned', 'fresh_wallet', 'dev', 'sniper',
                   'rat_trader', 'bundler', 'transfer_in', 'dex_bot', 'bluechip_owner'})
# Same bounded, exact scalar checks as token normalization; no inherited aliases.
_SPECS = {
    'balance': common._Spec('wallet_token_balance', 'decimal', 'holders', 'token',
        'valid', nonnegative=True, reason='provider_reported_human_units'),
    'amount_cur': common._Spec('wallet_token_balance_alias', 'decimal', 'holders', 'token',
        'valid', nonnegative=True, reason='provider_documented_alias_balance'),
    'amount_percentage': common._Spec('wallet_supply_share_reported', 'decimal', 'holders',
        'fraction', 'valid', fraction=True, reason='provider_total_supply_method_unverified'),
    'native_balance': common._Spec('wallet_native_balance_raw', 'integer', 'holders',
        'lamport', 'valid', nonnegative=True, reason='provider_reported_native_smallest_unit'),
    'usd_value': common._Spec('wallet_holdings_value_reported', 'decimal', 'holders',
        'USD', 'valid', nonnegative=True, reason='provider_reported_valuation_not_execution_price'),
}


class HolderValue(FieldObservation):
    """One present field: source JSON type plus the declared semantic type."""
    name: NonEmptyString
    value_text: StrictStr | None
    declared_type: ValueType
    unit: StrictStr | None


class HolderObservation(ContractModel):
    holder_id: UUID
    capture_id: UUID
    chain: Literal['sol']
    token_address: NonEmptyString
    wallet_address: NonEmptyString
    token_account_address: NonEmptyString | None
    pointer: StrictStr
    contract_version: Literal['gmgn.token_top_holders.v1'] = CONTRACT_VERSION
    occurred_at: datetime
    time_basis: Literal['source', 'received']
    identity_quality: Literal['valid', 'unverified']
    values: dict[str, HolderValue]

    @field_validator('token_address', 'wallet_address', 'token_account_address')
    @classmethod
    def address_size(cls, value):
        if value is not None and _address(value) is None:
            raise ValueError('holder identity must be a bounded address')
        return value

    @field_validator('pointer')
    @classmethod
    def pointer_size(cls, value):
        return _bounded_utf8(value, MAX_POINTER_BYTES)

    @field_validator('occurred_at', mode='before')
    @classmethod
    def clock(cls, value):
        return utc_datetime(value)


class HolderParseResult(ContractModel):
    metadata: CaptureMetadata
    raw_sha256: StrictStr
    parser_version: Literal['gmgn-holders-v2'] = PARSER_VERSION
    contract_version: Literal['gmgn.token_top_holders.v1'] = CONTRACT_VERSION
    collection_scope: Literal['partial_list'] = 'partial_list'
    # Null: no supported list was read. Zero: an observed empty list.
    returned_rows: int | None = Field(default=None, ge=0)
    holders: list[HolderObservation] = Field(default_factory=list)
    fields: list[FieldObservation] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


def _address(value) -> str | None:
    try:
        return common._address(value, 'sol')
    except common._PayloadError:
        return None


def _issue(result, code, pointer, detail):
    result.issues.append(Issue(code=code, pointer=pointer, detail=detail))


def _field(key, value, pointer) -> HolderValue:
    if key in {'address', 'account_address'}:
        quality, reason = common._check(value, common._Spec(key, 'string', 'identity', quality='valid'))
        if quality == 'valid' and _address(value) is None:
            quality, reason = 'invalid', 'invalid_holder_identity'
        elif quality == 'valid':
            reason = 'provider_wallet_identity' if key == 'address' else 'provider_token_account_not_owner_mint_verified'
        name = 'wallet_address' if key == 'address' else 'token_account_address'
        declared_type, unit, category = 'string', None, 'identity'
    else:
        spec = _SPECS[key]
        quality, reason = common._check(value, spec)
        name, declared_type, unit, category = spec.metric, spec.value_type, spec.unit, spec.category
    return HolderValue(pointer=pointer, category=category, value_type=common._json_type(value),
        quality=quality, reason=reason, contract_id=f'{CONTRACT_VERSION}:{key}',
        name=name, value_text=common._value_text(value), declared_type=declared_type, unit=unit)


def _context(result, root, prefix='') -> str | None:
    meta = result.metadata
    if meta.endpoint != 'token_top_holders':
        _issue(result, 'unsupported_endpoint', '', 'This parser supports token_top_holders only.')
        return None
    if meta.chain is None or meta.chain.casefold() != 'sol':
        _issue(result, 'unsupported_chain', '', 'This holders contract is limited to explicit Solana metadata.')
        return None
    params = meta.request_params
    if 'query' in params:
        query = params['query']
        if not isinstance(query, dict) or any(k in params and params[k] != v for k, v in query.items()):
            _issue(result, 'identity_conflict', '', 'Nested and flat request parameters conflict.')
            return None
        params = {**params, **query}
    chain = params.get('chain')
    token = _address(params.get('address'))
    if not isinstance(chain, str) or chain.casefold() != 'sol' or token is None:
        _issue(result, 'missing_request_identity', '', 'Request chain and token address must be explicit and valid.')
        return None
    if 'token_address' in params and params['token_address'] != token:
        _issue(result, 'identity_conflict', '', 'An additional request token identity conflicts with address.')
        return None
    if 'chain' in root and (not isinstance(root['chain'], str) or root['chain'].casefold() != 'sol'):
        _issue(result, 'identity_conflict', prefix + '/chain', 'Response chain conflicts with the request namespace.')
        return None
    # Top-level address has no verified role in the CLI list contract.
    if 'address' in root or 'token_address' in root:
        _issue(result, 'unsupported_shape', prefix, 'Top-level address roles are not supported for this list shape.')
        return None
    if 'limit' in params:
        quality, _ = common._check(params['limit'],
            common._Spec('limit', 'integer', 'request', quality='valid', positive=True))
        if quality != 'valid' or Decimal(str(params['limit'])) > MAX_ROWS:
            _issue(result, 'invalid_request_context', '', 'Requested limit must be an exact integer from 1 to 100.')
            return None
        if result.returned_rows > int(params['limit']):
            _issue(result, 'request_limit_exceeded', prefix + '/list', 'Returned rows exceed the explicit requested limit.')
            return None
    for name, allowed in [('order_by', _ORDER_BY), ('direction', {'asc', 'desc'}), ('tag', _TAGS)]:
        if name in params and (not isinstance(params[name], str) or params[name] not in allowed):
            _issue(result, 'invalid_request_context', '', f'Explicit {name} does not satisfy the request contract.')
            return None
    return token


def _parse(result, raw):
    if len(raw) > common.MAX_RAW_BYTES:
        raise common._PayloadError('payload_too_large', 'Raw response exceeds the existing byte limit.')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise common._PayloadError('invalid_utf8', 'Response is not UTF-8 JSON.') from None
    try:
        root = json.loads(text, parse_float=Decimal, parse_int=int,
                          parse_constant=common._constant, object_pairs_hook=common._pairs)
    except common._PayloadError:
        raise
    except (ValueError, InvalidOperation, RecursionError):
        raise common._PayloadError('invalid_json', 'Response cannot be parsed as bounded exact JSON.') from None
    nodes = common._nodes(root)
    observations = {}
    budget = common._OutputBudget()
    for pointer, value in nodes:
        field = FieldObservation(pointer=pointer, category='unknown', value_type=common._json_type(value),
            quality='missing' if value is None else 'unverified',
            reason='explicit_null' if value is None else 'unmapped_field')
        budget.add_field(field)
        observations[pointer] = field
    result.fields.extend(observations.values())
    if not 200 <= result.metadata.http_status < 300:
        _issue(result, 'http_error', '', 'Metadata declares an unsuccessful HTTP status.')
        return result
    if isinstance(root, dict) and 'code' in root and (type(root['code']) is not int or root['code'] != 0):
        _issue(result, 'provider_error', '/code', 'Provider status is not the integer success code 0.')
        return result
    prefix = ''
    if result.metadata.evidence_kind == 'original_response':
        if (not isinstance(root, dict) or type(root.get('code')) is not int
                or root['code'] != 0 or not isinstance(root.get('data'), dict)
                or 'list' in root or 'address' in root or 'token_address' in root
                or ('chain' in root and root['chain'] != result.metadata.chain)):
            _issue(result, 'unsupported_evidence_format', '', 'Original HTTP requires an unambiguous code=0 data envelope.')
            return result
        root, prefix = root['data'], '/data'
    if (not isinstance(root, dict) or not isinstance(root.get('list'), list)
            or 'data' in root or 'code' in root):
        _issue(result, 'unsupported_shape', prefix, 'Expected one direct list in the declared evidence format.')
        return result
    rows = root['list']
    result = result.model_copy(update={'returned_rows': len(rows)})
    if len(rows) > MAX_ROWS:
        _issue(result, 'holder_count_limit', prefix + '/list', 'At most 100 holder rows are supported; none were truncated.')
        return result
    token = _context(result, root, prefix)
    if token is None:
        return result
    wallets = Counter(address for row in rows if isinstance(row, dict)
                      if (address := _address(row.get('address'))) is not None)
    for index, item in enumerate(rows):
        base = common._pointer(prefix + '/list', index)
        if not isinstance(item, dict):
            _issue(result, 'invalid_holder_item', base, 'Holder list item must be an object.')
            continue
        values = {key: _field(key, item[key], common._pointer(base, key))
                  for key in ('address', 'account_address', *_SPECS) if key in item}
        for value in values.values():
            if value.quality == 'invalid':
                _issue(result, 'invalid_field', value.pointer, value.reason)
        if 'chain' in item and (not isinstance(item['chain'], str) or item['chain'].casefold() != 'sol'):
            _issue(result, 'identity_conflict', common._pointer(base, 'chain'), 'Holder row chain conflicts with capture metadata.')
        balance, alias = values.get('balance'), values.get('amount_cur')
        if (balance is not None and alias is not None and balance.quality == alias.quality == 'valid'
                and Decimal(balance.value_text) != Decimal(alias.value_text)):
            for key in ('balance', 'amount_cur'):
                values[key] = values[key].model_copy(update={'quality':'unverified', 'reason':'balance_alias_conflict'})
            _issue(result, 'balance_alias_conflict', balance.pointer, 'Both balance values are preserved; no unified balance is verified.')
        wallet = _address(item.get('address'))
        if wallet is None:
            _issue(result, 'invalid_holder_identity', common._pointer(base, 'address'), 'Wallet address is missing or invalid; no holder row emitted.')
        elif wallets[wallet] > 1:
            values['address'] = values['address'].model_copy(update={'quality':'unverified', 'reason':'duplicate_wallet'})
            _issue(result, 'duplicate_wallet', common._pointer(base, 'address'), 'Repeated wallet rows are retained without merging or summing.')
        for value in values.values():
            observations[value.pointer] = FieldObservation(**value.model_dump(include=set(FieldObservation.model_fields)))
        if wallet is not None:
            account = values.get('account_address')
            meta = result.metadata
            result.holders.append(HolderObservation(
                holder_id=uuid5(_NAMESPACE, f'{meta.capture_id}:{CONTRACT_VERSION}:{base}'),
                capture_id=meta.capture_id, chain='sol', token_address=token,
                wallet_address=wallet,
                token_account_address=account.value_text if account is not None and account.quality == 'valid' else None,
                pointer=base, occurred_at=meta.source_event_at or meta.received_at,
                time_basis='source' if meta.source_event_at is not None else 'received',
                identity_quality='unverified' if wallets[wallet] > 1 else 'valid', values=values))
    result.fields[:] = observations.values()
    if any(issue.code == 'identity_conflict' for issue in result.issues):
        result.holders.clear()

    return result


def parse_holders(metadata: CaptureMetadata, raw: bytes) -> HolderParseResult:
    """Parse a bounded original HTTP or reserialized snapshot; no I/O.

    CaptureMetadata must have truthful transport clocks/status. Archived CLI-only
    samples lacking them can exercise this parser with explicitly fixture metadata.
    The result preserves that evidence label; it never invents available_at.
    """
    if not isinstance(metadata, CaptureMetadata):
        raise TypeError('metadata must be CaptureMetadata')
    if not isinstance(raw, bytes):
        raise TypeError('raw must be bytes')
    result = HolderParseResult(metadata=metadata.model_copy(deep=True),
                               raw_sha256=hashlib.sha256(raw).hexdigest())
    try:
        result = _parse(result, raw)
        if len(result.model_dump_json().encode('utf-8')) > MAX_OUTPUT_BYTES:
            raise common._PayloadError('output_size_limit', 'Complete holder result exceeds the UTF-8 JSON output budget.')
        return result
    except common._PayloadError as exc:
        # Structural/output limits never return a partially catalogued snapshot.
        return HolderParseResult(metadata=result.metadata, raw_sha256=result.raw_sha256,
            issues=[Issue(code=exc.code, pointer='', detail=exc.detail)])

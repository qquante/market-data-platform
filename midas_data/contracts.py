"""Public, side-effect-free contracts for the independent GMGN data foundation.

A ``valid`` fact satisfies its versioned adapter contract. It does not certify
upstream truth or change fixture evidence into an original GMGN observation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import math
import re
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

SourceKind = Literal['fixture', 'gmgn', 'legacy_gmgn']
EvidenceKind = Literal['original_response', 'legacy_reserialized']
Quality = Literal['valid', 'unverified', 'invalid', 'missing']
ValueType = Literal['decimal', 'integer', 'boolean', 'string']
NonEmptyString = Annotated[StrictStr, Field(min_length=1)]
MAX_POINTER_BYTES = 2048
MAX_TOKEN_ADDRESS_BYTES = 256


def _bounded_utf8(value: str, limit: int) -> str:
    try:
        size = len(value.encode('utf-8'))
    except UnicodeEncodeError:
        raise ValueError('contract text must be valid UTF-8') from None
    if '\x00' in value or size > limit:
        raise ValueError('contract text exceeds its UTF-8 byte limit or contains NUL')
    return value

_SENSITIVE_KEYS = frozenset({
    'authorization', 'proxyauthorization', 'cookie', 'setcookie', 'password',
    'passwd', 'secret', 'clientsecret', 'apikey', 'xapikey', 'privatekey',
    'secretkey', 'accesstoken', 'refreshtoken', 'bearertoken', 'authtoken',
    'sessiontoken', 'sessionid', 'credentials', 'credential', 'mnemonic',
    'seedphrase', 'signingkey',
})
_CREDENTIAL_ASSIGNMENT = re.compile(
    r'(?i)(?:^|[?&\s;])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|'
    r'private[_-]?key|password|client[_-]?secret|authorization)\s*[=:]'
)


def _key_name(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', value.casefold())


def _sensitive_key(value: str) -> bool:
    name = _key_name(value)
    return name in _SENSITIVE_KEYS or name.endswith(('apikey', 'privatekey', 'secretkey', 'password', 'clientsecret'))


def _assert_safe_text(value: str) -> None:
    """Reject recognizable credential carriers, without including their values.

    This guards structured metadata, not arbitrary secret classification. A
    caller must still never put credentials into unlabelled metadata strings.
    """
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('metadata text must be valid UTF-8') from None
    if '\x00' in value:
        raise ValueError('metadata text cannot contain NUL')
    if re.match(r'(?i)^\s*(?:bearer|basic)\s+', value) or '-----BEGIN ' in value:
        raise ValueError('credentials are forbidden in capture metadata')
    if _CREDENTIAL_ASSIGNMENT.search(value):
        raise ValueError('credentials are forbidden in capture metadata')
    if '://' in value:
        try:
            parsed = urlsplit(value)
            sensitive_query = any(_sensitive_key(key) for key, _ in parse_qsl(parsed.query))
            if parsed.username is not None or parsed.password is not None or sensitive_query:
                raise ValueError('credentials are forbidden in capture metadata')
        except ValueError:
            raise ValueError('unsafe URL in capture metadata') from None


def utc_datetime(value: Any) -> datetime:
    """Accept only explicit aware clocks; epoch unit inference is forbidden."""
    if isinstance(value, str):
        if re.search(r'\d{2}:\d{2}:\d{2}[.,]\d{7,}', value):
            raise ValueError('timestamp precision finer than microseconds is unsupported')
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError('timestamp must be an aware ISO 8601 datetime') from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timestamp must include an explicit timezone')
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise ValueError('timestamp is outside the supported UTC range') from None


class ContractModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)


class CaptureMetadata(ContractModel):
    capture_id: UUID
    source_kind: SourceKind
    endpoint: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    # Missing chain is retained only for raw captures without a token adapter.
    # It never becomes a fabricated global/unknown chain or a nullable Fact.
    chain: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None
    requested_at: datetime
    received_at: datetime
    source_event_at: datetime | None = None
    http_status: Annotated[int, Field(strict=True, ge=100, le=599)]
    request_params: dict[str, Any] = Field(default_factory=dict)
    evidence_kind: EvidenceKind

    @field_validator('requested_at', 'received_at', mode='before')
    @classmethod
    def validate_required_clock(cls, value: Any) -> datetime:
        return utc_datetime(value)

    @field_validator('source_event_at', mode='before')
    @classmethod
    def validate_optional_clock(cls, value: Any) -> datetime | None:
        return None if value is None else utc_datetime(value)

    @field_validator('endpoint', 'chain')
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError('identifier contains surrounding whitespace or control characters')
        _assert_safe_text(value)
        return value

    @field_validator('request_params', mode='before')
    @classmethod
    def validate_params(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError('request_params must be a JSON object')
        stack = [(value, 0)]
        nodes = text_size = 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if depth > 8 or nodes > 512:
                raise ValueError('request_params exceeds depth or node limit')
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError('request_params keys must be strings')
                    if _sensitive_key(key):
                        raise ValueError('credentials are forbidden in capture metadata')
                    _assert_safe_text(key)
                    text_size += len(key)
                    stack.append((child, depth + 1))
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                _assert_safe_text(item)
                text_size += len(item)
            elif item is None or isinstance(item, (bool, int)):
                pass
            elif isinstance(item, Decimal):
                if not item.is_finite():
                    raise ValueError('request_params numbers must be finite')
            elif isinstance(item, float):
                if not math.isfinite(item):
                    raise ValueError('request_params numbers must be finite')
            else:
                raise ValueError('request_params contains a non-JSON value')
            if text_size > 16_384:
                raise ValueError('request_params exceeds text limit')
        return value

    @model_validator(mode='after')
    def validate_clock_order(self) -> CaptureMetadata:
        if self.chain is None and self.endpoint in {'token_info', 'market_rank', 'trenches'}:
            raise ValueError('token adapters require an explicit capture chain')
        parameter_chain = self.request_params.get('chain')
        # Null query chain remains null/unspecified, preserving legacy metadata.
        # Only a non-null explicit namespace must match the capture namespace.
        if parameter_chain is not None and (self.chain is None or not isinstance(parameter_chain, str) or parameter_chain.casefold() != self.chain.casefold()):
            raise ValueError('request chain conflicts with capture chain')
        if self.requested_at > self.received_at:
            raise ValueError('requested_at must not be later than received_at')
        return self


class Fact(ContractModel):
    fact_id: UUID
    capture_id: UUID
    chain: NonEmptyString
    token_address: NonEmptyString
    metric: NonEmptyString
    value_text: StrictStr | None
    value_type: ValueType
    unit: StrictStr | None
    category: NonEmptyString
    occurred_at: datetime
    time_basis: Literal['source', 'received']
    pointer: StrictStr
    quality: Quality
    contract_version: NonEmptyString

    @field_validator('token_address')
    @classmethod
    def validate_token_address_size(cls, value: str) -> str:
        return _bounded_utf8(value, MAX_TOKEN_ADDRESS_BYTES)

    @field_validator('pointer')
    @classmethod
    def validate_pointer_size(cls, value: str) -> str:
        return _bounded_utf8(value, MAX_POINTER_BYTES)

    @field_validator('occurred_at', mode='before')
    @classmethod
    def validate_clock(cls, value: Any) -> datetime:
        return utc_datetime(value)


class FieldObservation(ContractModel):
    pointer: StrictStr
    category: NonEmptyString
    value_type: NonEmptyString
    quality: Quality
    reason: NonEmptyString
    contract_id: StrictStr | None = None

    @field_validator('pointer')
    @classmethod
    def validate_pointer_size(cls, value: str) -> str:
        return _bounded_utf8(value, MAX_POINTER_BYTES)


class Issue(ContractModel):
    code: NonEmptyString
    pointer: StrictStr
    detail: NonEmptyString

    @field_validator('pointer')
    @classmethod
    def validate_pointer_size(cls, value: str) -> str:
        return _bounded_utf8(value, MAX_POINTER_BYTES)


class NormalizationResult(ContractModel):
    facts: list[Fact] = Field(default_factory=list)
    fields: list[FieldObservation] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    normalizer_version: NonEmptyString

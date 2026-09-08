"""Bounded import of verified legacy GMGN response bytes into the local data API.

This module never opens the legacy database. The companion PowerShell exporter
performs one bounded READ ONLY query; this CLI validates the whole JSONL snapshot
before sending any capture and never logs response bodies or credentials.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID
import zlib

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from .contracts import CaptureMetadata, utc_datetime
from .normalize import normalize_capture

MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_EXPORT_LINES = 100
MAX_RAW_BYTES = 4 * 1024 * 1024
MAX_COMPRESSED_BYTES = MAX_RAW_BYTES + 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_METADATA_HEADER_BYTES = 16_384
ALLOWED_PARAMS = frozenset({'chain', 'address', 'token_address', 'interval', 'limit', 'order_by', 'direction'})
_ALLOWED_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1', 'api'})


class ImportFailure(ValueError):
    """A safe machine-readable error; never include source values in details."""
    def __init__(self, code: str, line: int | None = None):
        self.code = code
        self.line = line
        super().__init__(code)


class LegacyRow(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    export_version: Literal['gmgn-legacy-v1']
    call_id: UUID
    endpoint: Literal['token_info', 'market_rank']
    chain: StrictStr
    requested_at_utc: datetime
    received_at_utc: datetime
    http_status: Annotated[int, Field(strict=True, ge=200, le=299)]
    request_params: dict[str, Any]
    response_sha256: Annotated[StrictStr, Field(pattern=r'^[a-f0-9]{64}$')]
    payload_sha256: Annotated[StrictStr, Field(pattern=r'^[a-f0-9]{64}$')]
    codec: Literal['zlib']
    raw_bytes: Annotated[int, Field(strict=True, ge=0, le=MAX_RAW_BYTES)]
    payload_base64: Annotated[StrictStr, Field(max_length=4 * ((MAX_COMPRESSED_BYTES + 2) // 3))]

    @field_validator('requested_at_utc', 'received_at_utc', mode='before')
    @classmethod
    def validate_clock(cls, value: Any) -> datetime:
        return utc_datetime(value)


@dataclass(frozen=True)
class VerifiedCapture:
    metadata: CaptureMetadata
    raw: bytes
    sha256: str

    def metadata_header(self) -> str:
        # HTTP headers are ASCII; escaped JSON retains original Unicode values.
        header = json.dumps(self.metadata.model_dump(mode='json'), ensure_ascii=True, separators=(',', ':'))
        if len(header.encode('ascii')) > MAX_METADATA_HEADER_BYTES:
            raise ImportFailure('metadata_header_limit')
        return header


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ImportFailure('duplicate_export_key')
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ImportFailure('invalid_export_number')


def load_export(path: Path) -> list[LegacyRow]:
    """Snapshot at most 16 MiB so later file changes cannot alter the import."""
    try:
        with path.open('rb') as stream:
            snapshot = stream.read(MAX_EXPORT_BYTES + 1)
    except OSError:
        raise ImportFailure('export_file_unreadable') from None
    if len(snapshot) > MAX_EXPORT_BYTES:
        raise ImportFailure('export_byte_limit')
    # Physical lines count toward the limit; blanks are invalid, not discarded.
    lines = snapshot.splitlines()
    if len(lines) > MAX_EXPORT_LINES:
        raise ImportFailure('export_line_limit')
    rows = []
    seen_ids: set[UUID] = set()
    for number, line in enumerate(lines, 1):
        try:
            obj = json.loads(line.decode('utf-8'), object_pairs_hook=_pairs,
                             parse_float=Decimal, parse_constant=_constant)
            row = LegacyRow.model_validate(obj)
        except ImportFailure as exc:
            raise ImportFailure(exc.code, number) from None
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ImportFailure('invalid_export_row', number) from None
        if row.call_id in seen_ids:
            raise ImportFailure('duplicate_capture_in_export', number)
        seen_ids.add(row.call_id)
        rows.append(row)
    return rows


def safe_request_params(params: dict[str, Any]) -> dict[str, Any]:
    """Keep only observed query keys; never copy headers or arbitrary nesting."""
    sources = [params]
    if 'query' in params:
        if not isinstance(params['query'], dict):
            raise ImportFailure('invalid_request_query')
        sources.append(params['query'])
    result = {}
    for source in sources:
        for key in sorted(ALLOWED_PARAMS & source.keys()):
            value = source[key]
            if value is not None and not isinstance(value, (str, int, bool)):
                raise ImportFailure('invalid_allowlisted_parameter')
            if key in result and (type(result[key]) is not type(value) or result[key] != value):
                raise ImportFailure('conflicting_request_parameter')
            result[key] = value
    return result


def verify_row(row: LegacyRow) -> VerifiedCapture:
    """Bound decompression and require exact raw length plus both legacy hashes."""
    try:
        compressed = base64.b64decode(row.payload_base64, validate=True)
    except (ValueError, binascii.Error):
        raise ImportFailure('invalid_payload_base64') from None
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise ImportFailure('compressed_byte_limit')
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, MAX_RAW_BYTES + 1)
    except zlib.error:
        raise ImportFailure('invalid_zlib_payload') from None
    if len(raw) > MAX_RAW_BYTES or decoder.unconsumed_tail:
        raise ImportFailure('decompressed_byte_limit')
    if not decoder.eof or decoder.unused_data:
        raise ImportFailure('incomplete_or_trailing_zlib_data')
    if len(raw) != row.raw_bytes:
        raise ImportFailure('raw_length_mismatch')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != row.response_sha256 or digest != row.payload_sha256:
        raise ImportFailure('raw_sha256_mismatch')
    try:
        metadata = CaptureMetadata(capture_id=row.call_id, source_kind='legacy_gmgn',
            endpoint=row.endpoint, chain=row.chain, requested_at=row.requested_at_utc,
            received_at=row.received_at_utc, source_event_at=None, http_status=row.http_status,
            request_params=safe_request_params(row.request_params), evidence_kind='original_response')
    except ValidationError:
        raise ImportFailure('invalid_capture_metadata') from None
    verified = VerifiedCapture(metadata, raw, digest)
    verified.metadata_header()
    return verified


def preflight(rows: list[LegacyRow]) -> dict[str, Any]:
    """Validate every row first; retain compressed rows, not all inflated bodies."""
    if len(rows) > MAX_EXPORT_LINES:
        raise ImportFailure('export_line_limit')
    field_quality: Counter = Counter()
    fact_quality: Counter = Counter()
    issues: Counter = Counter()
    unknown = raw_total = 0
    records = []
    seen: set[UUID] = set()
    for number, row in enumerate(rows, 1):
        if row.call_id in seen:
            raise ImportFailure('duplicate_capture_in_export', number)
        seen.add(row.call_id)
        try:
            capture = verify_row(row)
        except ImportFailure as exc:
            raise ImportFailure(exc.code, number) from None
        result = normalize_capture(capture.metadata, capture.raw)
        field_quality.update(field.quality for field in result.fields)
        fact_quality.update(fact.quality for fact in result.facts)
        issues.update(issue.code for issue in result.issues)
        unknown += sum(field.category == 'unknown' for field in result.fields)
        raw_total += len(capture.raw)
        records.append({'capture_id': str(row.call_id), 'endpoint': row.endpoint,
                        'raw_sha256': capture.sha256, 'raw_bytes': len(capture.raw)})
    return {'rows': len(rows), 'hashes_verified': len(rows), 'raw_bytes': raw_total,
            'source_kind': 'legacy_gmgn', 'evidence_kind': 'original_response', 'records': records,
            'normalization_preview': {'scope': 'local preflight; not proof of sink persistence',
                'field_quality': dict(sorted(field_quality.items())), 'fact_quality': dict(sorted(fact_quality.items())),
                'unknown_fields': unknown, 'issues': dict(sorted(issues.items()))}}


def local_api_url(value: str) -> str:
    try:
        url = urlsplit(value)
        valid = (url.scheme == 'http' and url.hostname in _ALLOWED_HOSTS and url.port == 8791
                 and url.username is None and url.password is None and not url.query and not url.fragment
                 and url.path in ('', '/'))
    except ValueError:
        valid = False
    if not valid:
        raise ImportFailure('local_api_url_required')
    return value.rstrip('/')


def _request_json(client: httpx.Client, method: str, url: str, headers: dict[str, str],
                  *, body: bytes | None = None, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    try:
        with client.stream(method, url, headers=headers, content=body, params=params, follow_redirects=False) as response:
            data = bytearray()
            for part in response.iter_bytes(chunk_size=64 * 1024):
                if len(data) + len(part) > MAX_RESPONSE_BYTES:
                    raise ImportFailure('api_response_byte_limit')
                data.extend(part)
            status = response.status_code
    except httpx.HTTPError:
        raise ImportFailure('api_transport_error_delivery_may_be_uncertain') from None
    if status < 200 or status >= 300:
        # Never copy the server body, URL, or headers into the error/report.
        raise ImportFailure(f'api_http_{status}')
    try:
        return status, json.loads(data)
    except ValueError:
        raise ImportFailure('invalid_api_response') from None


def catalog_sample(client: httpx.Client, url: str, headers: dict[str, str], capture_limit: int) -> dict[str, Any]:
    _, body = _request_json(client, 'GET', url + '/v1/catalog', headers,
                           params={'source_kind': 'legacy_gmgn', 'capture_limit': max(1, min(100, capture_limit)), 'limit': 200})
    if not isinstance(body, dict) or not isinstance(body.get('items'), list):
        raise ImportFailure('invalid_catalog_response')
    unknown = valid = observed = 0
    for item in body['items']:
        if not isinstance(item, dict) or type(item.get('observations')) is not int or item['observations'] < 0:
            raise ImportFailure('invalid_catalog_response')
        count = item['observations']
        observed += count
        unknown += count if item.get('category') == 'unknown' else 0
        valid += count if item.get('quality') == 'valid' else 0
    return {'scope': 'server latest legacy captures; first 200 grouped catalogue rows',
            'capture_limit': max(1, min(100, capture_limit)), 'groups_returned': len(body['items']),
            'possibly_truncated': len(body['items']) >= 200, 'observations': observed,
            'unknown_observations': unknown, 'valid_observations': valid,
            'completeness': 'sample only; asynchronous normalization may still be pending'}


def import_rows(rows: list[LegacyRow], api_url: str, api_key: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    url = local_api_url(api_url)
    if not api_key or any(char in api_key for char in '\r\n'):
        raise ImportFailure('data_api_key_required')
    summary = preflight(rows)  # No HTTP effect until the entire snapshot passes.
    headers = {'authorization': 'Bearer ' + api_key}
    own_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(10.0, connect=3.0), trust_env=False, follow_redirects=False)
    accepted = duplicates = 0
    try:
        for row in rows:
            capture = verify_row(row)
            status, acknowledgement = _request_json(client, 'POST', url + '/v1/captures',
                {**headers, 'content-type': 'application/octet-stream', 'x-midas-metadata': capture.metadata_header()},
                body=capture.raw)
            if (status != 202 or not isinstance(acknowledgement, dict)
                or acknowledgement.get('capture_id') != str(row.call_id)
                or acknowledgement.get('raw_sha256') != capture.sha256
                or type(acknowledgement.get('bytes')) is not int or acknowledgement['bytes'] != len(capture.raw)
                or type(acknowledgement.get('duplicate')) is not bool):
                raise ImportFailure('capture_acknowledgement_mismatch')
            accepted += 1
            duplicates += acknowledgement['duplicate']
        sample = None
        if rows:
            try:
                sample = catalog_sample(client, url, headers, len(rows))
            except ImportFailure as exc:
                sample = {'error': exc.code, 'completeness': 'unavailable; capture acknowledgements remain valid'}
        return {**summary, 'mode': 'import', 'accepted': accepted, 'duplicates': duplicates, 'catalog_sample': sample}
    except ImportFailure as exc:
        # The caller can safely rerun the same IDs; no automatic retries are made.
        return {**summary, 'mode': 'import', 'accepted': accepted, 'duplicates': duplicates,
                'error': exc.code, 'resume': 'rerun the same export; capture IDs remain unchanged'}
    finally:
        if own_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Validate/import a bounded legacy GMGN JSONL snapshot into the local API.')
    parser.add_argument('export_file', type=Path)
    parser.add_argument('--api-url', default='http://127.0.0.1:8791')
    parser.add_argument('--validate-only', action='store_true', help='verify hashes/contracts and preview normalization without HTTP')
    args = parser.parse_args(argv)
    try:
        rows = load_export(args.export_file)
        if args.validate_only:
            summary = {**preflight(rows), 'mode': 'validate_only', 'accepted': 0}
        else:
            summary = import_rows(rows, args.api_url, os.environ.get('DATA_API_KEY', ''))
    except ImportFailure as exc:
        summary = {'error': exc.code, 'line': exc.line, 'accepted': 0}
    print(json.dumps(summary, ensure_ascii=True, separators=(',', ':')))
    return 1 if 'error' in summary else 0


if __name__ == '__main__':
    sys.exit(main())

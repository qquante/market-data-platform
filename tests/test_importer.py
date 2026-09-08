import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4
import zlib

import httpx
import pytest
from pydantic import ValidationError

from midas_data import importer

RAW = b'{"data":{"address":"FixtureToken","price":{"price":0.00000000000000000000123},"new_field":true}}\n'


def row_values(raw=RAW, **changes):
    digest = hashlib.sha256(raw).hexdigest()
    values = dict(export_version='gmgn-legacy-v1', call_id=str(uuid4()), endpoint='token_info', chain='sol',
                  requested_at_utc='2026-09-08T12:00:00Z', received_at_utc='2026-09-08T12:00:01Z',
                  http_status=200, request_params={'query': {'chain': 'sol', 'address': 'FixtureToken'}},
                  response_sha256=digest, payload_sha256=digest, codec='zlib', raw_bytes=len(raw),
                  payload_base64=base64.b64encode(zlib.compress(raw)).decode('ascii'))
    values.update(changes)
    return values


def legacy_row(raw=RAW, **changes):
    return importer.LegacyRow(**row_values(raw, **changes))


def write_export(tmp_path, values):
    path = tmp_path / 'export.jsonl'
    path.write_text('\n'.join(json.dumps(row) for row in values) + '\n', encoding='utf-8')
    return path


def test_verified_capture_preserves_bytes_call_id_and_original_clocks():
    row = legacy_row()
    capture = importer.verify_row(row)
    assert capture.raw == RAW
    assert capture.sha256 == hashlib.sha256(RAW).hexdigest()
    assert capture.metadata.capture_id == row.call_id
    assert capture.metadata.source_kind == 'legacy_gmgn'
    assert capture.metadata.evidence_kind == 'original_response'
    assert capture.metadata.received_at == row.received_at_utc
    assert capture.metadata.source_event_at is None
    assert capture.metadata.request_params == {'address': 'FixtureToken', 'chain': 'sol'}
    assert '0.00000000000000000000123' in capture.raw.decode()


@pytest.mark.parametrize('changes, code', [
    ({'response_sha256': '0' * 64}, 'raw_sha256_mismatch'),
    ({'payload_sha256': '0' * 64}, 'raw_sha256_mismatch'),
    ({'raw_bytes': 1}, 'raw_length_mismatch'),
    ({'payload_base64': '%%%'}, 'invalid_payload_base64'),
    ({'payload_base64': base64.b64encode(b'not-zlib').decode()}, 'invalid_zlib_payload'),
    ({'payload_base64': base64.b64encode(zlib.compress(RAW)[:-1]).decode()}, 'incomplete_or_trailing_zlib_data'),
    ({'payload_base64': base64.b64encode(zlib.compress(RAW) + b'trailing').decode()}, 'incomplete_or_trailing_zlib_data'),
])
def test_integrity_failures_cannot_be_labeled_original_response(changes, code):
    with pytest.raises(importer.ImportFailure, match=code):
        importer.verify_row(legacy_row(**changes))


def test_zlib_decompression_is_bounded_before_inflating_the_full_body(monkeypatch):
    monkeypatch.setattr(importer, 'MAX_RAW_BYTES', 8)
    row = legacy_row(b'x' * 10_000, raw_bytes=8)
    with pytest.raises(importer.ImportFailure, match='decompressed_byte_limit'):
        importer.verify_row(row)


def test_compressed_input_has_a_separate_byte_limit(monkeypatch):
    monkeypatch.setattr(importer, 'MAX_COMPRESSED_BYTES', 8)
    with pytest.raises(importer.ImportFailure, match='compressed_byte_limit'):
        importer.verify_row(legacy_row())


def test_concatenated_zlib_streams_are_not_silently_accepted():
    row = legacy_row(payload_base64=base64.b64encode(zlib.compress(RAW) + zlib.compress(b'extra')).decode())
    with pytest.raises(importer.ImportFailure, match='incomplete_or_trailing_zlib_data'):
        importer.verify_row(row)


def test_only_allowlisted_query_params_survive_and_headers_are_never_copied():
    params = {'api_key': 'synthetic-secret', 'headers': {'Authorization': 'Bearer synthetic-secret'},
              'watch_reason': 'strategy-private', 'query': {'address': 'FixtureToken', 'chain': 'sol',
                                                          'limit': 10, 'unknown': 'synthetic-secret'}}
    capture = importer.verify_row(legacy_row(request_params=params))
    assert capture.metadata.request_params == {'address': 'FixtureToken', 'chain': 'sol', 'limit': 10}
    assert 'synthetic-secret' not in capture.metadata_header()
    assert 'strategy-private' not in capture.metadata_header()


def test_conflicting_flat_and_nested_params_are_rejected():
    row = legacy_row(request_params={'address': 'One', 'query': {'address': 'Other'}})
    with pytest.raises(importer.ImportFailure, match='conflicting_request_parameter'):
        importer.verify_row(row)


@pytest.mark.parametrize('changes', [
    {'codec': 'gzip'}, {'endpoint': 'swap'}, {'http_status': 429},
    {'requested_at_utc': '2026-09-08T12:00:00'}, {'raw_bytes': True},
])
def test_export_contract_rejects_unsupported_sources_and_clocks(changes):
    with pytest.raises(ValidationError):
        legacy_row(**changes)


def test_jsonl_load_is_bounded_and_keeps_independent_capture_ids(tmp_path):
    rows = importer.load_export(write_export(tmp_path, [row_values(), row_values()]))
    assert len(rows) == 2 and rows[0].call_id != rows[1].call_id


def test_jsonl_duplicate_capture_ids_are_explicit_errors(tmp_path):
    values = row_values()
    with pytest.raises(importer.ImportFailure, match='duplicate_capture_in_export'):
        importer.load_export(write_export(tmp_path, [values, values]))


def test_jsonl_byte_limit_is_enforced_before_parsing(tmp_path, monkeypatch):
    path = write_export(tmp_path, [row_values()])
    monkeypatch.setattr(importer, 'MAX_EXPORT_BYTES', 8)
    with pytest.raises(importer.ImportFailure, match='export_byte_limit'):
        importer.load_export(path)


def test_jsonl_line_limit_counts_physical_lines(tmp_path):
    path = tmp_path / 'export.jsonl'
    path.write_bytes(b'\n' * 101)
    with pytest.raises(importer.ImportFailure, match='export_line_limit'):
        importer.load_export(path)


@pytest.mark.parametrize('raw, code', [(b'\n', 'invalid_export_row'), (b'{"x":1,"x":2}\n', 'duplicate_export_key'),
                                      (b'{"x":NaN}\n', 'invalid_export_number'), (b'\xff\n', 'invalid_export_row')])
def test_malformed_export_rows_fail_without_partial_parsing(tmp_path, raw, code):
    path = tmp_path / 'export.jsonl'
    path.write_bytes(raw)
    with pytest.raises(importer.ImportFailure, match=code):
        importer.load_export(path)


def test_all_hashes_are_checked_before_the_first_http_effect():
    requests = []
    transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(500))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(importer.ImportFailure, match='raw_sha256_mismatch'):
            importer.import_rows([legacy_row(), legacy_row(response_sha256='0' * 64)],
                                 'http://127.0.0.1:8791', 'synthetic-key', client=client)
    assert requests == []


def test_import_posts_original_bytes_and_reports_acknowledgements_separately_from_catalogue():
    row = legacy_row()
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer synthetic-key'
        if request.method == 'POST':
            assert request.content == RAW
            metadata = json.loads(request.headers['x-midas-metadata'])
            assert metadata['capture_id'] == str(row.call_id)
            assert metadata['source_kind'] == 'legacy_gmgn'
            assert metadata['evidence_kind'] == 'original_response'
            return httpx.Response(202, json={'capture_id': str(row.call_id), 'raw_sha256': row.response_sha256,
                                           'bytes': len(RAW), 'duplicate': True})
        assert request.url.path == '/v1/catalog'
        assert request.url.params['source_kind'] == 'legacy_gmgn'
        return httpx.Response(200, json={'items': [{'category': 'unknown', 'quality': 'unverified', 'observations': 3},
                                                 {'category': 'valuation', 'quality': 'valid', 'observations': 2}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = importer.import_rows([row], 'http://127.0.0.1:8791', 'synthetic-key', client=client)
    assert report['accepted'] == 1 and report['duplicates'] == 1
    assert report['hashes_verified'] == 1
    assert report['catalog_sample']['unknown_observations'] == 3
    assert report['catalog_sample']['valid_observations'] == 2
    assert 'sample only' in report['catalog_sample']['completeness']
    assert 'not proof of sink persistence' in report['normalization_preview']['scope']
    assert len(requests) == 2
    assert 'synthetic-key' not in json.dumps(report)


def test_api_redirects_are_never_followed_and_error_bodies_are_not_echoed():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={'location': 'https://external.test/'}, text='synthetic-secret')

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        report = importer.import_rows([legacy_row()], 'http://api:8791', 'synthetic-key', client=client)
    assert report['error'] == 'api_http_302' and report['accepted'] == 0
    assert len(requests) == 1
    assert 'synthetic-secret' not in json.dumps(report)


@pytest.mark.parametrize('url', ['https://external.test', 'http://external.test:8791', 'http://user:secret@localhost:8791',
                               'http://localhost:8791/?api_key=secret', 'http://localhost:8791/other', 'http://localhost:80'])
def test_only_explicit_local_api_destinations_are_allowed(url):
    with pytest.raises(importer.ImportFailure, match='local_api_url_required'):
        importer.local_api_url(url)


def test_acknowledgement_hash_mismatch_stops_without_claiming_acceptance():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(202, json={
        'capture_id': str(uuid4()), 'raw_sha256': '0' * 64, 'bytes': len(RAW), 'duplicate': False}))) as client:
        report = importer.import_rows([legacy_row()], 'http://api:8791', 'synthetic-key', client=client)
    assert report['error'] == 'capture_acknowledgement_mismatch'
    assert report['accepted'] == 0
    assert 'rerun the same export' in report['resume']


def test_api_response_size_is_bounded(monkeypatch):
    monkeypatch.setattr(importer, 'MAX_RESPONSE_BYTES', 8)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(202, text='x' * 100))) as client:
        report = importer.import_rows([legacy_row()], 'http://api:8791', 'synthetic-key', client=client)
    assert report['error'] == 'api_response_byte_limit'


def test_validate_only_does_not_require_a_key_or_create_an_http_client(tmp_path, monkeypatch, capsys):
    path = write_export(tmp_path, [row_values()])
    monkeypatch.delenv('DATA_API_KEY', raising=False)
    monkeypatch.setattr(importer.httpx, 'Client', lambda **kwargs: pytest.fail('validate-only cannot create HTTP clients'))
    assert importer.main([str(path), '--validate-only']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['mode'] == 'validate_only' and report['accepted'] == 0
    assert report['hashes_verified'] == 1
    assert report['normalization_preview']['unknown_fields'] > 0


def test_cli_errors_do_not_echo_bad_row_values(tmp_path, capsys):
    path = tmp_path / 'export.jsonl'
    path.write_text('{"password":"synthetic-secret"}', encoding='utf-8')
    assert importer.main([str(path), '--validate-only']) == 1
    output = capsys.readouterr().out
    assert 'synthetic-secret' not in output
    assert json.loads(output)['error'] == 'invalid_export_row'

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from midas_data.contracts import CaptureMetadata


def metadata_values(**changes):
    values = dict(
        capture_id=UUID('317f3564-e11f-4fa1-a5c4-03f01b8bd705'),
        source_kind='fixture', endpoint='token_info', chain='sol',
        requested_at='2026-09-08T12:00:00Z',
        received_at='2026-09-08T12:00:01Z',
        http_status=200, request_params={'token_address': 'FixtureToken'},
        evidence_kind='original_response',
    )
    values.update(changes)
    return values


def test_metadata_preserves_fixture_and_serializes_required_fields():
    metadata = CaptureMetadata(**metadata_values())
    result = metadata.model_dump(mode='json')
    assert result['source_kind'] == 'fixture'
    assert result['capture_id'] == '317f3564-e11f-4fa1-a5c4-03f01b8bd705'
    assert result['received_at'] == '2026-09-08T12:00:01Z'
    assert result['source_event_at'] is None


def test_aware_offsets_are_normalized_to_utc():
    metadata = CaptureMetadata(**metadata_values(
        requested_at=datetime(2026, 9, 8, 9, tzinfo=timezone(timedelta(hours=-3))),
        received_at='2026-09-08T09:00:01-03:00',
        source_event_at='2026-09-08T13:00:00+01:00',
    ))
    assert metadata.requested_at.hour == 12
    assert metadata.requested_at.tzinfo is timezone.utc
    assert metadata.source_event_at == metadata.requested_at


@pytest.mark.parametrize('value', ['2026-09-08T12:00:00', datetime(2026, 9, 8), 1788868800, True])
def test_naive_or_epoch_clocks_are_rejected(value):
    with pytest.raises(ValidationError):
        CaptureMetadata(**metadata_values(requested_at=value))


def test_clock_order_is_validated():
    with pytest.raises(ValidationError):
        CaptureMetadata(**metadata_values(requested_at='2026-09-08T12:00:02Z'))


@pytest.mark.parametrize('value', [True, '200', 99, 600])
def test_http_status_is_a_real_status_integer(value):
    with pytest.raises(ValidationError):
        CaptureMetadata(**metadata_values(http_status=value))


@pytest.mark.parametrize('params', [
    {'api_key': 'synthetic-secret'},
    {'headers': {'Authorization': 'Bearer synthetic-secret'}},
    {'url': 'https://user:synthetic-secret@example.test/path'},
    {'url': 'https://example.test/path?access_token=synthetic-secret'},
    {'nested': [{'privateKey': 'synthetic-secret'}]},
])
def test_credentials_are_rejected_without_echoing_values(params):
    with pytest.raises(ValidationError) as caught:
        CaptureMetadata(**metadata_values(request_params=params))
    assert 'synthetic-secret' not in str(caught.value)
    assert 'synthetic-secret' not in str(caught.value.errors(include_input=False))


def test_extra_metadata_fields_are_rejected():
    with pytest.raises(ValidationError):
        CaptureMetadata(**metadata_values(password='synthetic-secret'))


def test_metadata_rejects_submicrosecond_precision_instead_of_truncating():
    with pytest.raises(ValidationError, match='precision'):
        CaptureMetadata(**metadata_values(requested_at='2026-09-08T12:00:00.123456789Z'))


@pytest.mark.parametrize('params', [
    {'GMGN_API_KEY': 'synthetic-secret'},
    {'url': 'https://example.test/?api%5Fkey=synthetic-secret'},
    {'arbitrary': 'Bearer synthetic-secret'},
])
def test_additional_credential_carriers_are_rejected(params):
    with pytest.raises(ValidationError) as caught:
        CaptureMetadata(**metadata_values(request_params=params))
    assert 'synthetic-secret' not in str(caught.value)


def test_public_identifiers_and_nullable_params_are_accepted():
    params = {'token_address': 'PublicToken', 'chain': 'SOL', 'wallet_address': 'PublicWallet', 'filters': [None, False, 0]}
    assert CaptureMetadata(**metadata_values(request_params=params)).request_params == params


def test_metadata_chain_cannot_conflict_with_explicit_request_chain():
    with pytest.raises(ValidationError, match='chain conflicts'):
        CaptureMetadata(**metadata_values(request_params={'chain': 'eth'}))


@pytest.mark.parametrize('params', [{'value': float('nan')}, {'value': object()}, {'value': 'x' * 16_385}])
def test_non_json_or_unbounded_metadata_is_rejected(params):
    with pytest.raises(ValidationError):
        CaptureMetadata(**metadata_values(request_params=params))


def test_fact_rejects_naive_clock_and_numeric_value_text():
    from midas_data.contracts import Fact
    values = dict(fact_id=UUID('dc0d7462-2e7c-4ac9-b9cc-c849a2c1c31c'),
                  capture_id=UUID('317f3564-e11f-4fa1-a5c4-03f01b8bd705'), chain='sol',
                  token_address='FixtureToken', metric='price', value_text='0.1', value_type='decimal',
                  unit='usd_per_token', category='valuation', occurred_at='2026-09-08T12:00:00Z',
                  time_basis='received', pointer='/price/price', quality='valid', contract_version='gmgn.token_info.v1')
    assert Fact(**values).model_dump(mode='json')['value_text'] == '0.1'
    with pytest.raises(ValidationError):
        Fact(**{**values, 'value_text': 0.1})
    with pytest.raises(ValidationError):
        Fact(**{**values, 'occurred_at': '2026-09-08T12:00:00'})


@pytest.mark.parametrize('value', ['\x00', '\ud800'])
def test_metadata_text_must_survive_utf8_storage(value):
    with pytest.raises(ValidationError):
        CaptureMetadata(**metadata_values(request_params={'value': value}))


@pytest.mark.parametrize('pointer', ['/' + 'x' * 2048, '/' + '\u00e9' * 1024])
def test_field_contract_enforces_pointer_byte_limit(pointer):
    from midas_data.contracts import FieldObservation
    with pytest.raises(ValidationError, match='byte limit'):
        FieldObservation(pointer=pointer, category='unknown', value_type='null', quality='missing', reason='explicit_null')


def test_fact_contract_enforces_token_identity_byte_limit():
    from midas_data.contracts import Fact
    with pytest.raises(ValidationError, match='byte limit'):
        Fact(fact_id=UUID('dc0d7462-2e7c-4ac9-b9cc-c849a2c1c31c'),
             capture_id=UUID('317f3564-e11f-4fa1-a5c4-03f01b8bd705'), chain='sol',
             token_address='\u00e9' * 129, metric='price', value_text='1', value_type='decimal',
             unit='usd_per_token', category='valuation', occurred_at='2026-09-08T12:00:00Z',
             time_basis='received', pointer='/price/price', quality='valid', contract_version='gmgn.token_info.v1')


@pytest.mark.parametrize('endpoint', ['hot_searches', 'future_global_endpoint'])
def test_raw_capture_can_preserve_absent_chain_without_inventing_namespace(endpoint):
    meta = CaptureMetadata(**metadata_values(endpoint=endpoint, chain=None, request_params={}))
    assert meta.chain is None
    assert meta.model_dump(mode='json')['chain'] is None


@pytest.mark.parametrize('endpoint', ['token_info', 'market_rank'])
def test_token_adapters_reject_missing_chain(endpoint):
    with pytest.raises(ValidationError, match='explicit capture chain'):
        CaptureMetadata(**metadata_values(endpoint=endpoint, chain=None, request_params={}))


@pytest.mark.parametrize('capture_chain, parameter_chain', [(None, 'sol'), ('sol', 'eth'), (None, 0)])
def test_explicit_query_chain_must_match_a_real_metadata_chain(capture_chain, parameter_chain):
    with pytest.raises(ValidationError, match='chain conflicts'):
        CaptureMetadata(**metadata_values(endpoint='hot_searches', chain=capture_chain, request_params={'chain': parameter_chain}))


def test_raw_capture_chain_remains_required_as_an_explicit_nullable_field():
    values = metadata_values(endpoint='hot_searches')
    del values['chain']
    with pytest.raises(ValidationError):
        CaptureMetadata(**values)


def test_fact_chain_is_still_non_nullable():
    from midas_data.contracts import Fact
    values = dict(fact_id=UUID('dc0d7462-2e7c-4ac9-b9cc-c849a2c1c31c'),
                  capture_id=UUID('317f3564-e11f-4fa1-a5c4-03f01b8bd705'), chain=None,
                  token_address='FixtureToken', metric='price', value_text='1', value_type='decimal',
                  unit='usd_per_token', category='valuation', occurred_at='2026-09-08T12:00:00Z',
                  time_basis='received', pointer='/price', quality='valid', contract_version='gmgn.market_rank.v3')
    with pytest.raises(ValidationError):
        Fact(**values)


@pytest.mark.parametrize('capture_chain', [None, 'sol'])
def test_null_query_chain_preserves_unspecified_legacy_parameter(capture_chain):
    meta = CaptureMetadata(**metadata_values(endpoint='hot_searches', chain=capture_chain, request_params={'chain': None}))
    assert meta.chain == capture_chain
    assert 'chain' in meta.request_params and meta.request_params['chain'] is None

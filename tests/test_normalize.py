import json
from uuid import UUID

import pytest

from midas_data.contracts import CaptureMetadata
from midas_data.normalize import NORMALIZER_VERSION, normalize_capture


def metadata(**changes):
    values = dict(capture_id=UUID('317f3564-e11f-4fa1-a5c4-03f01b8bd705'),
                  source_kind='fixture', endpoint='token_info', chain='sol',
                  requested_at='2026-09-08T12:00:00Z', received_at='2026-09-08T12:00:01Z',
                  http_status=200, request_params={}, evidence_kind='original_response')
    values.update(changes)
    return CaptureMetadata(**values)


def fact_for(result, metric):
    return next(fact for fact in result.facts if fact.metric == metric)


def test_token_info_decimal_precision_and_complete_serialization():
    raw = b'{"data":{"address":"FixtureToken","price":{"price":0.123456789012345678901234567890},"unknown":null}}'
    result = normalize_capture(metadata(), raw)
    fact = fact_for(result, 'price')
    assert fact.value_text == '0.123456789012345678901234567890'
    assert fact.unit == 'usd_per_token'
    assert fact.quality == 'valid'
    assert fact.pointer == '/data/price/price'
    assert set(fact.model_dump(mode='json')) == {
        'fact_id', 'capture_id', 'chain', 'token_address', 'metric', 'value_text',
        'value_type', 'unit', 'category', 'occurred_at', 'time_basis', 'pointer',
        'quality', 'contract_version',
    }
    json.dumps(result.model_dump(mode='json'), allow_nan=False)


def test_ids_replay_stably_and_do_not_merge_distinct_captures():
    raw = b'{"address":"FixtureToken","price":{"price":"0.0000000000000000001"}}'
    first = normalize_capture(metadata(), raw)
    assert first == normalize_capture(metadata(), raw)
    other = normalize_capture(metadata(capture_id=UUID('aaf05752-9848-4986-9482-58354efad573')), raw)
    assert first.facts[0].fact_id != other.facts[0].fact_id


def test_unknown_fields_are_catalogued_with_json_pointers():
    result = normalize_capture(metadata(), b'{"address":"FixtureToken","other":{"a/b~c":[null,false,0]}}')
    fields = {field.pointer: field for field in result.fields}
    assert '/other/a~1b~0c/0' in fields
    assert fields['/other/a~1b~0c/1'].category == 'unknown'
    assert fields['/other/a~1b~0c/2'].value_type == 'integer'


def test_ranking_uses_documented_provider_cap_without_deriving_or_guessing_supply():
    result = normalize_capture(metadata(endpoint='market_rank'), b'{"data":{"rank":[{"address":"FixtureToken","price":"0.01","market_cap":"50000","top_10_holder_rate":"50"}]}}')
    cap = fact_for(result, 'market_cap')
    assert cap.value_text == '50000'
    assert cap.unit == 'usd'
    assert cap.quality == 'valid'
    assert not any(f.metric in {'fdv', 'derived_market_cap'} for f in result.facts)
    assert fact_for(result, 'top_10_holder_rate').value_text == '50'


@pytest.mark.parametrize('body, code', [
    (b'{"x":1,"x":2}', 'duplicate_json_key'),
    (b'{"nested":{"x":1,"x":2}}', 'duplicate_json_key'),
    (b'{"x":NaN}', 'non_finite_json_number'),
    (b'{"x":Infinity}', 'non_finite_json_number'),
    (b'{"x":-Infinity}', 'non_finite_json_number'),
    (b'\xff', 'invalid_utf8'),
    (b'{', 'invalid_json'),
    (b'{"x":"\\u0000"}', 'invalid_unicode'),
    (b'{"\\u0000":1}', 'invalid_unicode'),
    (b'{"x":"\\ud800"}', 'invalid_unicode'),
    (b'{"\\ud800":1}', 'invalid_unicode'),
])
def test_parse_errors_never_promote_partial_evidence(body, code):
    result = normalize_capture(metadata(), body)
    assert not result.facts
    assert not result.fields
    assert [issue.code for issue in result.issues] == [code]


@pytest.mark.parametrize('limit, value, raw', [
    ('MAX_RAW_BYTES', 2, b'{"x":1}'),
    ('MAX_JSON_NODES', 3, b'{"a":1,"b":2,"c":3}'),
    ('MAX_JSON_DEPTH', 1, b'{"a":{"b":1}}'),
])
def test_limits_are_explicit_rejections_not_silent_truncation(monkeypatch, limit, value, raw):
    import midas_data.normalize as normalizer
    monkeypatch.setattr(normalizer, limit, value)
    result = normalizer.normalize_capture(metadata(), raw)
    assert not result.fields and not result.facts
    assert result.issues[0].code in normalizer.FATAL_ISSUE_CODES


def test_exact_node_limit_accepts_the_complete_catalogue(monkeypatch):
    import midas_data.normalize as normalizer
    monkeypatch.setattr(normalizer, 'MAX_JSON_NODES', 4)
    result = normalizer.normalize_capture(metadata(endpoint='unknown'), b'{"a":1,"b":2,"c":3}')
    assert {field.pointer for field in result.fields} == {'', '/a', '/b', '/c'}


def test_unsupported_endpoint_catalogues_every_node_without_guessing_tokens():
    result = normalize_capture(metadata(endpoint='token_pool'), b'{"data":{"address":"PoolAddress","price":"1","tokens":[{"address":"OtherToken"}]}}')
    assert not result.facts
    assert {field.pointer for field in result.fields} == {'', '/data', '/data/address', '/data/price', '/data/tokens', '/data/tokens/0', '/data/tokens/0/address'}
    assert result.issues[0].code == 'unsupported_endpoint'
    assert all(field.category == 'unknown' for field in result.fields)


@pytest.mark.parametrize('body', [b'null', b'42', b'[]', b'{"data":null}', b'{"data":{"data":{"address":"NestedToken"}}}'])
def test_token_info_requires_a_supported_object_with_explicit_identity(body):
    result = normalize_capture(metadata(), body)
    assert not result.facts
    assert any(issue.code in {'unsupported_shape', 'missing_token_identity'} for issue in result.issues)


def test_nested_pool_and_creator_addresses_do_not_become_token_identities():
    result = normalize_capture(metadata(), b'{"pool":{"address":"PoolAddress","price":"99"},"dev":{"address":"CreatorAddress"}}')
    assert not result.facts
    assert '/pool/price' in {field.pointer for field in result.fields}
    assert result.issues[0].code == 'missing_token_identity'


@pytest.mark.parametrize('body, params', [
    (b'{"address":"FixtureToken","chain":"eth","price":{"price":"1"}}', {}),
    (b'{"chain":"eth","data":{"address":"FixtureToken","price":{"price":"1"}}}', {}),
    (b'{"address":"OtherToken","price":{"price":"1"}}', {'token_address': 'FixtureToken'}),
])
def test_conflicting_identity_quarantines_entire_capture(body, params):
    result = normalize_capture(metadata(request_params=params), body)
    assert not result.facts
    assert any(issue.code == 'identity_conflict' for issue in result.issues)
    assert result.fields


@pytest.mark.parametrize('body', [
    b'[{"address":"FixtureToken","price":"1"}]',
    b'{"rank":[{"address":"FixtureToken","price":"1"}]}',
    b'{"data":[{"address":"FixtureToken","price":"1"}]}',
    b'{"data":{"rank":[{"address":"FixtureToken","price":"1"}]}}',
])
def test_explicit_ranking_shapes(body):
    result = normalize_capture(metadata(endpoint='market_rank'), body)
    assert len(result.facts) == 1
    assert not result.issues


def test_empty_ranking_is_successful_and_invalid_items_are_visible():
    empty = normalize_capture(metadata(endpoint='market_rank'), b'{"data":{"rank":[]}}')
    assert not empty.issues and not empty.facts
    result = normalize_capture(metadata(endpoint='market_rank'), b'[null,{"pool":{"address":"pool"}},{"address":"FixtureToken","price":"1"}]')
    assert len(result.facts) == 1
    assert [issue.code for issue in result.issues] == ['invalid_token_item', 'invalid_token_item']
    assert {field.pointer for field in result.fields} == {'', '/0', '/1', '/1/pool', '/1/pool/address', '/2', '/2/address', '/2/price'}


def test_identity_collision_in_one_ranking_item_clears_all_facts():
    result = normalize_capture(metadata(endpoint='market_rank'), b'[{"address":"One","price":"1"},{"address":"Two","chain":"eth","price":"2"}]')
    assert not result.facts
    assert any(issue.code == 'identity_conflict' for issue in result.issues)


def test_addresses_are_case_sensitive_on_solana_and_canonical_for_known_evm():
    evm_address = '0x' + 'aB' * 20
    evm = normalize_capture(metadata(chain='base'), json.dumps({'address': evm_address, 'price': {'price': '1'}}).encode())
    assert evm.facts[0].token_address == evm_address.lower()
    sol = normalize_capture(metadata(), b'{"address":"aBcDe","price":{"price":"1"}}')
    assert sol.facts[0].token_address == 'aBcDe'


def test_source_and_received_clocks_are_explicit_and_creation_does_not_override_them():
    raw = b'{"address":"FixtureToken","creation_timestamp":1780000000,"price":{"price":"1"}}'
    received = normalize_capture(metadata(), raw)
    assert fact_for(received, 'price').occurred_at == metadata().received_at
    assert fact_for(received, 'price').time_basis == 'received'
    source_metadata = metadata(source_event_at='2026-09-08T11:59:59Z')
    source = normalize_capture(source_metadata, raw)
    assert fact_for(source, 'price').occurred_at == source_metadata.source_event_at
    assert fact_for(source, 'price').time_basis == 'source'
    assert fact_for(source, 'creation_timestamp').unit == 'unix_second'
    assert fact_for(source, 'creation_timestamp').quality == 'valid'


@pytest.mark.parametrize('raw_value, expected_text, quality', [
    ('null', None, 'missing'), ('""', '', 'missing'), ('0', '0', 'invalid'),
    ('false', 'false', 'invalid'), ('"NaN"', 'NaN', 'invalid'), ('"1,23"', '1,23', 'invalid'),
    ('"0.00000000000000000000000000100"', '0.00000000000000000000000000100', 'valid'),
    ('1e-100', '1E-100', 'valid'),
    ('" 1 "', ' 1 ', 'invalid'),
])
def test_price_missing_invalid_and_exact_values_stay_distinct(raw_value, expected_text, quality):
    raw = ('{"address":"FixtureToken","price":{"price":' + raw_value + '}}').encode()
    fact = fact_for(normalize_capture(metadata(), raw), 'price')
    assert fact.value_text == expected_text
    assert fact.quality == quality


def test_absent_key_does_not_create_a_fictitious_missing_observation():
    result = normalize_capture(metadata(), b'{"address":"FixtureToken"}')
    assert not result.facts
    assert '/price/price' not in {field.pointer for field in result.fields}


@pytest.mark.parametrize('raw_count, text, quality', [
    ('9007199254740993', '9007199254740993', 'valid'),
    ('0', '0', 'valid'), ('1.5', '1.5', 'invalid'),
    ('"2.0"', '2.0', 'invalid'), ('true', 'true', 'invalid'), ('-1', '-1', 'invalid'),
])
def test_counts_never_pass_through_float_or_fractional_truncation(raw_count, text, quality):
    raw = ('{"address":"FixtureToken","holder_count":' + raw_count + '}').encode()
    fact = fact_for(normalize_capture(metadata(), raw), 'holder_count')
    assert fact.value_text == text and fact.quality == quality


@pytest.mark.parametrize('ratio', ['0.5', '1.01', '50', '100', '-0.1'])
def test_opaque_ratios_are_never_rescaled_by_magnitude(ratio):
    raw = ('[{"address":"FixtureToken","buy_tax":' + ratio + '}]').encode()
    fact = fact_for(normalize_capture(metadata(endpoint='market_rank'), raw), 'buy_tax')
    assert fact.value_text == ratio
    assert fact.unit is None and fact.quality == 'unverified'


def test_window_and_metric_families_remain_distinct():
    result = normalize_capture(metadata(), b'{"address":"FixtureToken","price":{"price":"2","price_1m":"1","volume_1m":"30","volume_5m":"60","buys_1m":7},"circulating_supply":"1000000000","ath_price":"10"}')
    facts = {fact.metric: fact for fact in result.facts}
    assert facts['window_reference_price_1m'].value_text == '1'
    assert facts['volume_1m'].value_text == '30'
    assert facts['volume_5m'].value_text == '60'
    assert facts['buys_1m'].value_text == '7'
    assert all(fact.quality == 'valid' for fact in facts.values())
    assert 'market_cap' not in facts and 'history_highest_market_cap' not in facts


@pytest.mark.parametrize('raw_bool, text, quality', [('false', 'false', 'valid'), ('true', 'true', 'valid'), ('1', '1', 'invalid'), ('"false"', 'false', 'invalid')])
def test_risk_boolean_never_uses_generic_truthiness(raw_bool, text, quality):
    raw = ('[{"address":"FixtureToken","is_wash_trading":' + raw_bool + '}]').encode()
    fact = fact_for(normalize_capture(metadata(endpoint='market_rank'), raw), 'is_wash_trading')
    assert fact.value_text == text and fact.quality == quality


@pytest.mark.parametrize('status, body, code', [
    (429, b'{"address":"FixtureToken","price":{"price":"1"}}', 'http_error'),
    (200, b'{"code":1,"data":{"address":"FixtureToken","price":{"price":"1"}}}', 'provider_error'),
    (200, b'{"code":false,"data":{"address":"FixtureToken","price":{"price":"1"}}}', 'provider_error'),
    (200, b'{"code":"0","data":{"address":"FixtureToken","price":{"price":"1"}}}', 'provider_error'),
])
def test_errors_preserve_catalogue_but_do_not_promote_facts(status, body, code):
    result = normalize_capture(metadata(http_status=status), body)
    assert not result.facts
    assert result.fields
    assert result.issues[0].code == code


def test_success_envelope_and_utf8_bom_are_explicitly_supported():
    raw = b'\xef\xbb\xbf{"code":0,"data":{"address":"FixtureToken","price":{"price":"1"},"name":"\\ud83d\\ude00"}}'
    result = normalize_capture(metadata(), raw)
    assert not result.issues
    assert fact_for(result, 'name').value_text == '\U0001f600'


def test_unknown_scalar_price_is_not_reinterpreted_as_nested_info_price():
    result = normalize_capture(metadata(), b'{"address":"FixtureToken","price":"1"}')
    assert not result.facts
    field = next(field for field in result.fields if field.pointer == '/price')
    assert field.category == 'unknown' and field.quality == 'unverified'


def _normalized_output_bytes(result):
    output = result.model_dump(mode='json')
    return len(json.dumps({'facts': output['facts'], 'fields': output['fields']},
                          ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def test_output_byte_budget_accepts_exact_boundary_and_rejects_one_byte_less(monkeypatch):
    import midas_data.normalize as normalizer
    # Include UTF-8, JSON escaping, and a known-field replacement in the budget.
    raw = json.dumps({'address': 'FixtureToken', 'name': 'caf\u00e9\n', 'price': {'price': '1'}}).encode()
    baseline = normalizer.normalize_capture(metadata(), raw)
    output_bytes = _normalized_output_bytes(baseline)
    monkeypatch.setattr(normalizer, 'MAX_OUTPUT_BYTES', output_bytes)
    accepted = normalizer.normalize_capture(metadata(), raw)
    assert accepted == baseline
    monkeypatch.setattr(normalizer, 'MAX_OUTPUT_BYTES', output_bytes - 1)
    rejected = normalizer.normalize_capture(metadata(), raw)
    assert rejected.facts == [] and rejected.fields == []
    assert [issue.code for issue in rejected.issues] == ['output_size_limit']


def test_field_only_catalogue_obeys_aggregate_output_budget(monkeypatch):
    import midas_data.normalize as normalizer
    raw = b'{"new_field":[1,2,3]}'
    baseline = normalizer.normalize_capture(metadata(endpoint='unknown'), raw)
    monkeypatch.setattr(normalizer, 'MAX_OUTPUT_BYTES', _normalized_output_bytes(baseline) - 1)
    rejected = normalizer.normalize_capture(metadata(endpoint='unknown'), raw)
    assert rejected.fields == [] and rejected.facts == []
    assert [issue.code for issue in rejected.issues] == ['output_size_limit']


def test_repeated_prefix_bytes_are_rejected_before_catalogue_models_exist(monkeypatch):
    import midas_data.normalize as normalizer
    raw = json.dumps({'x' * 180: {'a': 1, 'b': 2, 'c': 3}}).encode()
    monkeypatch.setattr(normalizer, 'MAX_OUTPUT_BYTES', 400)

    def catalogue_must_not_be_built(**kwargs):
        raise AssertionError('pointer budget must be checked during traversal')

    monkeypatch.setattr(normalizer, 'FieldObservation', catalogue_must_not_be_built)
    result = normalizer.normalize_capture(metadata(endpoint='unknown'), raw)
    assert not result.facts and not result.fields
    assert [issue.code for issue in result.issues] == ['output_size_limit']
    assert 'before catalogue' in result.issues[0].detail


@pytest.mark.parametrize('key', ['x' * 2048, '\u00e9' * 1024, '/~' * 512])
def test_pointer_limit_counts_utf8_and_rfc6901_escape_expansion(key):
    result = normalize_capture(metadata(endpoint='unknown'), json.dumps({key: 1}).encode())
    assert result.facts == [] and result.fields == []
    assert [issue.code for issue in result.issues] == ['pointer_size_limit']


def test_pointer_accepts_exact_2048_utf8_bytes():
    key = '\u00e9' * 1023 + 'a'
    result = normalize_capture(metadata(endpoint='unknown'), json.dumps({key: 1}).encode())
    assert [issue.code for issue in result.issues] == ['unsupported_endpoint']
    assert len(result.fields[-1].pointer.encode('utf-8')) == 2048


def test_nested_prefix_is_part_of_pointer_byte_limit():
    result = normalize_capture(metadata(endpoint='unknown'), json.dumps({'a' * 1023: {'b' * 1024: 1}}).encode())
    assert result.facts == [] and result.fields == []
    assert [issue.code for issue in result.issues] == ['pointer_size_limit']


@pytest.mark.parametrize('endpoint, payload', [
    ('token_info', {'address': '\u00e9' * 129, 'price': {'price': '1'}}),
    ('market_rank', [{'address': 'FixtureToken', 'price': '1'}, {'address': 'x' * 257, 'price': '2'}]),
])
def test_token_identity_byte_overflow_rejects_the_whole_capture(endpoint, payload):
    result = normalize_capture(metadata(endpoint=endpoint), json.dumps(payload).encode())
    assert result.facts == [] and result.fields == []
    assert [issue.code for issue in result.issues] == ['token_identity_limit']


def test_token_identity_accepts_256_utf8_bytes_without_inventing_address_kind():
    address = '\u00e9' * 128  # Transport/role fixture, not an on-chain address validation.
    raw = json.dumps({'address': address, 'price': {'price': '1'}}).encode()
    result = normalize_capture(metadata(), raw)
    assert not result.issues
    assert result.facts[0].token_address == address
    assert len(result.facts[0].token_address.encode('utf-8')) == 256


@pytest.mark.parametrize('token_count, expected_count', [(500, 10_000), (501, 0)])
def test_default_fact_limit_accepts_10000_and_rejects_an_overflowing_capture(token_count, expected_count):
    item = dict(address='FixtureToken', price='1', market_cap='2', liquidity='3',
                holder_count=4, swaps=5, buys=6, sells=7, volume='8', top_10_holder_rate='0.9',
                buy_tax='0.01', sell_tax='0.02', history_highest_market_cap='10',
                initial_liquidity='2', total_supply='100', circulating_supply='90', max_supply='100',
                decimals=6, name='Fixture', symbol='FIX', locked_ratio='0.5')
    assert len(item) - 1 == 20
    result = normalize_capture(metadata(endpoint='market_rank'), json.dumps([item] * token_count).encode())
    assert len(result.facts) == expected_count
    if expected_count:
        assert not result.issues
        assert len({fact.fact_id for fact in result.facts}) == 10_000
        assert _normalized_output_bytes(result) <= 16 * 1024 * 1024
    else:
        assert result.fields == []
        assert [issue.code for issue in result.issues] == ['fact_count_limit']


def test_output_limit_codes_are_public_fatal_contract():
    from midas_data.normalize import FATAL_ISSUE_CODES
    assert {'fact_count_limit', 'output_size_limit', 'pointer_size_limit', 'token_identity_limit'} <= FATAL_ISSUE_CODES


def test_public_canonical_identity_normalizes_known_evm_without_solana_case_loss():
    from midas_data.normalize import canonical_identity
    evm = '0x' + 'aB' * 20
    assert canonical_identity('BASE', evm) == ('base', evm.lower())
    assert canonical_identity('SOL', 'AbCdEf') == ('sol', 'AbCdEf')
    assert canonical_identity('custom', evm) == ('custom', evm)


@pytest.mark.parametrize('chain, token', [(' sol', 'Token'), ('sol', ''), ('sol', '\u00e9' * 129), ('sol', '\ud800'), ('\ud800', 'Token')])
def test_public_canonical_identity_rejects_invalid_transport_identity(chain, token):
    from midas_data.normalize import canonical_identity
    with pytest.raises(ValueError):
        canonical_identity(chain, token)


@pytest.mark.parametrize('item_count', [2, 1, 2], ids=['rank-response-a', 'rank-response-b', 'rank-response-c'])
def test_v2_supports_only_observed_double_envelope_ranking_with_original_pointers(item_count):
    # Structural replicas of three observed responses; all business values are fixtures.
    items = [{'address': f'FixtureToken{index}', 'chain': 'sol', 'price': '1.25',
              'market_cap': '1250', 'launchpad_status': '0', 'unknown_v2': False} for index in range(item_count)]
    body = {'code': 0, 'data': {'code': 0, 'data': {'rank': items}, 'message': '', 'reason': ''}}
    result = normalize_capture(metadata(endpoint='market_rank'), json.dumps(body).encode())
    assert not result.issues
    assert result.normalizer_version == NORMALIZER_VERSION
    assert len(result.facts) == 3 * item_count
    for index in range(item_count):
        assert any(fact.pointer == f'/data/data/rank/{index}/price' for fact in result.facts)
        assert any(field.pointer == f'/data/data/rank/{index}/unknown_v2' and field.category == 'unknown' for field in result.fields)
    assert all(fact.contract_version == 'gmgn.market_rank.v3' for fact in result.facts)
    assert all('.v3:' in field.contract_id for field in result.fields if field.contract_id)
    assert all(fact.quality == ('unverified' if fact.metric == 'launchpad_status' else 'valid') for fact in result.facts)


@pytest.mark.parametrize('inner_code', [1, -1, '0', False, None])
def test_v2_nested_provider_error_cannot_promote_ranking_facts(inner_code):
    body = {'code': 0, 'data': {'code': inner_code, 'data': {'rank': [{'address': 'FixtureToken', 'name': 'Fixture', 'price': '1'}]}}}
    result = normalize_capture(metadata(endpoint='market_rank'), json.dumps(body).encode())
    assert result.facts == []
    assert result.fields
    assert [issue.code for issue in result.issues] == ['provider_error']
    assert result.issues[0].pointer == '/data/code'


def test_v2_inner_error_also_blocks_token_info_even_if_token_fields_are_present():
    body = {'code': 0, 'data': {'code': 1, 'address': 'FixtureToken', 'price': {'price': '1'}}}
    result = normalize_capture(metadata(), json.dumps(body).encode())
    assert not result.facts
    assert [issue.code for issue in result.issues] == ['provider_error']


@pytest.mark.parametrize('body', [
    {'data': {'code': 0, 'data': {'rank': []}}},
    {'code': 0, 'extra': True, 'data': {'code': 0, 'data': {'rank': []}}},
    {'code': 0, 'data': {'data': {'rank': []}}},
    {'code': 0, 'data': {'code': 0, 'rank': [], 'data': {'rank': []}}},
    {'code': 0, 'data': {'code': 0, 'data': {'rank': [], 'alternate': []}}},
    {'code': 0, 'data': {'code': 0, 'data': {'code': 0, 'data': {'rank': []}}}},
    {'code': 0, 'data': {'code': 0, 'data': {'rank': {}}}},
])
def test_v2_does_not_arbitrarily_unwrap_ambiguous_or_further_nested_payloads(body):
    result = normalize_capture(metadata(endpoint='market_rank'), json.dumps(body).encode())
    assert not result.facts
    assert [issue.code for issue in result.issues] == ['unsupported_shape']


@pytest.mark.parametrize('status', [0, 1, 2])
def test_v2_token_info_launchpad_status_uses_documented_integer_enum(status):
    result = normalize_capture(metadata(), json.dumps({'address': 'FixtureToken', 'launchpad_status': status}).encode())
    fact = fact_for(result, 'launchpad_status')
    assert fact.value_type == 'integer' and fact.value_text == str(status)
    assert fact.quality == 'valid' and fact.unit is None
    assert fact.contract_version == 'gmgn.token_info.v3'
    assert not result.issues


@pytest.mark.parametrize('status', [-1, 3, '0', 0.0, False])
def test_v2_launchpad_status_rejects_unknown_values_and_implicit_coercion(status):
    result = normalize_capture(metadata(), json.dumps({'address': 'FixtureToken', 'launchpad_status': status}).encode())
    fact = fact_for(result, 'launchpad_status')
    assert fact.quality == 'invalid'
    assert [issue.code for issue in result.issues] == ['invalid_field']


def test_v2_does_not_assign_token_info_launchpad_enum_meanings_to_ranking():
    result = normalize_capture(metadata(endpoint='market_rank'), b'[{"address":"FixtureToken","launchpad_status":"1"}]')
    fact = fact_for(result, 'launchpad_status')
    assert fact.value_type == 'string' and fact.value_text == '1'
    assert fact.quality == 'unverified'


def test_v2_fact_identity_does_not_reuse_v1_ids_for_the_same_capture():
    from uuid import NAMESPACE_URL, uuid5
    raw = b'{"address":"FixtureToken","price":{"price":"1"}}'
    result = normalize_capture(metadata(), raw)
    fact = fact_for(result, 'price')
    namespace = uuid5(NAMESPACE_URL, 'https://midas.local/contracts/fact/v1')
    previous_name = json.dumps([str(metadata().capture_id), 'gmgn-p0-v1', '/price/price', 'price'], separators=(',', ':'))
    assert fact.fact_id != uuid5(namespace, previous_name)
    assert result == normalize_capture(metadata(), raw)


@pytest.mark.parametrize('endpoint, body, unit, quality, reason', [
    ('token_info', {'circulating_supply': '123.4500'}, 'token', 'valid', 'provider_human_token_units_no_decimal_rescaling'),
    ('market_rank', {'circulating_supply': '123.4500'}, None, 'unverified', 'human_vs_atomic_supply_scale_unverified'),
    ('token_info', {'liquidity': '123.4500'}, 'usd', 'valid', 'provider_main_pool_liquidity_usd'),
    ('market_rank', {'liquidity': '123.4500'}, 'usd', 'unverified', 'ranking_liquidity_pool_vs_aggregate_scope_unverified'),
])
def test_v3_units_and_scope_do_not_propagate_between_endpoints(endpoint, body, unit, quality, reason):
    metric = next(iter(body))
    item = dict(address='FixtureToken', decimals=9, **body)
    payload = item if endpoint == 'token_info' else [item]
    result = normalize_capture(metadata(endpoint=endpoint), json.dumps(payload).encode())
    fact = fact_for(result, metric)
    assert (fact.value_text, fact.unit, fact.quality) == ('123.4500', unit, quality)
    field = next(field for field in result.fields if field.pointer == fact.pointer)
    assert field.reason == reason
    assert not any(f.metric in {'fdv', 'market_cap', 'derived_market_cap'} for f in result.facts)


@pytest.mark.parametrize('window', ['1m', '5m', '1h', '6h', '24h'])
def test_v3_named_windows_remain_explicit_and_ranking_query_window_is_not_silently_lost(window):
    info = normalize_capture(metadata(), json.dumps({'address': 'FixtureToken', 'price': {
        f'price_{window}': '0.01', f'volume_{window}': '20.00', f'buys_{window}': 0}}).encode())
    assert {f.metric for f in info.facts} == {f'window_reference_price_{window}', f'volume_{window}', f'buys_{window}'}
    assert all(f.quality == 'valid' for f in info.facts)
    rank = normalize_capture(metadata(endpoint='market_rank', request_params={'interval': window}),
                             b'[{"address":"FixtureToken","volume":"20.00","buys":0,"sells":2,"swaps":2}]')
    assert all(f.quality == 'unverified' for f in rank.facts)
    assert {f.unit for f in rank.facts} == {'usd', 'count'}
    assert all(field.reason == 'queried_window_not_encoded_in_fact' for field in rank.fields if field.category == 'flow')


@pytest.mark.parametrize('name', ['creation_timestamp', 'open_timestamp'])
@pytest.mark.parametrize('endpoint', ['token_info', 'market_rank'])
def test_v3_epoch_seconds_are_attributes_and_zero_does_not_become_a_1970_token(endpoint, name):
    item = {'address': 'FixtureToken', name: 0, 'migrated_timestamp': 1780000000000}
    result = normalize_capture(metadata(endpoint=endpoint), json.dumps(item if endpoint == 'token_info' else [item]).encode())
    assert fact_for(result, name).quality == 'invalid'
    assert fact_for(result, name).value_text == '0'
    assert fact_for(result, name).unit == 'unix_second'
    assert all(f.occurred_at == metadata().received_at for f in result.facts)
    migrated = fact_for(result, 'migrated_timestamp')
    assert migrated.unit is None and migrated.quality == 'unverified'
    assert migrated.value_text == '1780000000000'  # No seconds/ms guessing by magnitude.


@pytest.mark.parametrize('name, chain, quality', [
    ('is_honeypot', 'bsc', 'valid'), ('is_honeypot', 'base', 'valid'),
    ('is_honeypot', 'eth', 'unverified'), ('is_honeypot', 'arc', 'unverified'),
    ('is_open_source', 'stable', 'valid'), ('is_renounced', 'eth', 'valid'),
    ('renounced_mint', 'sol', 'valid'), ('renounced_mint', 'base', 'unverified'),
    ('renounced_freeze_account', 'sol', 'valid'), ('renounced_freeze_account', 'stable', 'unverified'),
])
@pytest.mark.parametrize('value', [0, 1])
def test_v3_ranking_numeric_flags_preserve_encoding_and_chain_applicability(name, chain, quality, value):
    result = normalize_capture(metadata(endpoint='market_rank', chain=chain),
                             json.dumps([{'address': 'FixtureToken', name: value}]).encode())
    fact = fact_for(result, name)
    assert (fact.value_type, fact.value_text, fact.unit, fact.quality) == ('integer', str(value), 'boolean_code_01', quality)
    if quality == 'unverified':
        assert next(f for f in result.fields if f.pointer == fact.pointer).reason == 'field_chain_applicability_unverified'
    assert not result.issues


@pytest.mark.parametrize('value', [False, True, '0', '1', 0.0, 2, -1])
def test_v3_numeric_flags_do_not_coerce_boolean_string_fraction_or_unknown_code(value):
    result = normalize_capture(metadata(endpoint='market_rank', chain='base'),
                             json.dumps([{'address': 'FixtureToken', 'is_honeypot': value}]).encode())
    assert fact_for(result, 'is_honeypot').quality == 'invalid'


@pytest.mark.parametrize('value, quality', [('0', 'valid'), ('1', 'valid'), ('0.50', 'valid'), ('50', 'invalid'), ('1.01', 'invalid'), ('-0.1', 'invalid')])
def test_v3_documented_supply_fraction_bounds_never_rescale_percentages(value, quality):
    result = normalize_capture(metadata(), json.dumps({'address': 'FixtureToken', 'stat': {'top_10_holder_rate': value}}).encode())
    fact = fact_for(result, 'top_10_holder_rate')
    assert (fact.unit, fact.value_text, fact.quality) == ('ratio', value, quality)


def test_v3_rank_market_cap_is_provider_reported_and_never_replaced_by_price_times_supply():
    result = normalize_capture(metadata(endpoint='market_rank'), b'[{"address":"FixtureToken","price":"3","market_cap":"2","total_supply":"100","history_highest_market_cap":"9"}]')
    cap = fact_for(result, 'market_cap')
    assert (cap.value_text, cap.unit, cap.quality) == ('2', 'usd', 'valid')
    assert next(f for f in result.fields if f.pointer == cap.pointer).reason == 'provider_reported_usd_cap_supply_basis_unspecified'
    assert fact_for(result, 'history_highest_market_cap').occurred_at == metadata().received_at
    assert not any(f.metric == 'fdv' for f in result.facts)


def test_v3_tax_empty_sentinel_and_percent_conflicts_are_not_silently_interpreted():
    result = normalize_capture(metadata(endpoint='market_rank'), b'[{"address":"FixtureToken","buy_tax":"","price_change_percent1m":"50","visiting_count":7}]')
    assert fact_for(result, 'buy_tax').quality == 'missing'
    assert fact_for(result, 'buy_tax').value_text == ''
    pct = fact_for(result, 'price_change_percent1m')
    assert (pct.value_text, pct.unit, pct.quality) == ('50', None, 'unverified')
    assert fact_for(result, 'visiting_count').category == 'attention'


def test_v3_has_specific_reasons_for_every_unverified_path():
    from midas_data.normalize import _specs
    for endpoint in ('token_info', 'market_rank'):
        for path, spec in _specs(endpoint).items():
            if spec.quality == 'unverified':
                assert spec.reason and spec.reason != 'unit_scale_window_or_scope_unverified', (endpoint, path)


@pytest.mark.parametrize('previous_version', ['gmgn-p0-v1', 'gmgn-p0-v2'])
def test_v3_preserves_previous_versions_and_uses_new_fact_ids(previous_version):
    from uuid import NAMESPACE_URL, uuid5
    result = normalize_capture(metadata(), b'{"address":"FixtureToken","price":{"price":"1"}}')
    namespace = uuid5(NAMESPACE_URL, 'https://midas.local/contracts/fact/v1')
    name = json.dumps([str(metadata().capture_id), previous_version, '/price/price', 'price'], separators=(',', ':'))
    assert fact_for(result, 'price').fact_id != uuid5(namespace, name)
    assert result.normalizer_version == 'gmgn-p0-v3'



def test_v3_global_raw_capture_is_catalogued_before_chain_operations_and_never_promotes_tokens():
    result = normalize_capture(metadata(endpoint='hot_searches', chain=None, request_params={}),
                             b'{"code":0,"data":{"rank":[{"address":"FixtureToken","chain":"sol","price":"1"}]}}')
    assert result.facts == []
    assert [issue.code for issue in result.issues] == ['unsupported_endpoint']
    assert any(field.pointer == '/data/rank/0/address' and field.category == 'unknown' for field in result.fields)
    assert all(field.contract_id is None for field in result.fields)

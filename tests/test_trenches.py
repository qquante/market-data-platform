"""Synthetic contract tests; no GMGN calls or business database access."""
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from midas_data.contracts import CaptureMetadata
from midas_data.normalize import NORMALIZER_VERSION, normalize_capture


def metadata(**changes):
    fields = dict(capture_id=uuid4(), source_kind='fixture', endpoint='trenches', chain='base',
        requested_at='2026-09-09T10:00:00Z', received_at='2026-09-09T10:00:01Z',
        http_status=200, request_params={}, evidence_kind='original_response')
    fields.update(changes)
    return CaptureMetadata(**fields)


def normalize(item, **changes):
    return normalize_capture(metadata(**changes), json.dumps({'code': 0, 'data': {
        'new_creation': [dict(address='FixtureToken', **item)]}}).encode())


def test_trenches_requires_chain():
    with pytest.raises(ValidationError):
        metadata(chain=None)


def test_three_groups_keep_distinct_pointers_and_ids_for_same_token():
    meta = metadata()
    raw = json.dumps({'code': 0, 'data': {group: [{'address': 'FixtureToken', 'holder_count': 4}]
        for group in ('new_creation', 'pump', 'completed')}}).encode()
    result = normalize_capture(meta, raw)
    assert not result.issues
    assert len(result.facts) == len({f.fact_id for f in result.facts}) == 3
    assert {f.pointer for f in result.facts} == {
        '/data/new_creation/0/holder_count', '/data/pump/0/holder_count', '/data/completed/0/holder_count'}
    assert all(f.contract_version == 'gmgn.trenches.v1' for f in result.facts)
    assert result == normalize_capture(meta, raw)


def test_exact_precision_alias_separation_and_no_invented_market_cap():
    raw = b'{"data":{"pump":[{"address":"FixtureToken","price":0.00000000000000000012345678901234567890,"market_cap":"50000.001","usd_market_cap":"60000.002","total_supply":"1000"}]}}'
    result = normalize_capture(metadata(), raw)
    facts = {f.metric: f for f in result.facts}
    assert facts['price'].value_text == '1.2345678901234567890E-19'
    assert (facts['market_cap'].value_text, facts['market_cap'].unit, facts['market_cap'].quality) == ('50000.001', None, 'unverified')
    assert (facts['usd_market_cap'].value_text, facts['usd_market_cap'].unit, facts['usd_market_cap'].quality) == ('60000.002', 'usd', 'valid')
    assert set(facts) == {'price', 'market_cap', 'usd_market_cap', 'total_supply'}
    assert facts['total_supply'].unit is None


@pytest.mark.parametrize('value,quality', [(None, 'missing'), ('', 'missing'), (0, 'valid'), (False, 'invalid'), (-1, 'invalid')])
def test_missing_zero_false_are_not_conflated(value, quality):
    result = normalize({'holder_count': value})
    assert len(result.facts) == 1
    assert result.facts[0].quality == quality
    assert not normalize({}).facts


def test_negative_net_flow_is_valid_and_window_is_preserved():
    result = normalize({'net_buy_24h': '-45.001', 'volume_24h': '50', 'volume_1h': '10'})
    assert {f.metric for f in result.facts} == {'net_buy_24h', 'volume_24h', 'volume_1h'}
    assert all(f.quality == 'valid' and f.unit == 'usd' for f in result.facts)


@pytest.mark.parametrize('source_event', [None, '2026-09-09T09:59:59Z'])
def test_token_event_attributes_do_not_replace_observation_clock(source_event):
    meta = metadata(source_event_at=source_event)
    raw = b'{"completed":[{"address":"FixtureToken","created_timestamp":1788900000,"open_timestamp":0,"complete_timestamp":1788900600,"complete_cost_time":600}]}'
    result = normalize_capture(meta, raw)
    assert all(f.occurred_at == (meta.source_event_at or meta.received_at) for f in result.facts)
    assert all(f.time_basis == ('source' if source_event else 'received') for f in result.facts)
    assert next(f for f in result.facts if f.metric == 'open_timestamp').quality == 'invalid'
    assert next(f for f in result.facts if f.metric == 'complete_cost_time').unit == 'second'


@pytest.mark.parametrize('data', [{}, {'new_creation': None}, {'new_creation': {}},
    {'pump': [], 'near_completion': []},
    {'new_creation': [], 'data': {'pump': []}}, {'address': 'X', 'new_creation': []}])
def test_unknown_or_ambiguous_groups_are_not_silently_accepted(data):
    result = normalize_capture(metadata(), json.dumps({'data': data}).encode())
    assert not result.facts
    assert any(i.code == 'unsupported_shape' for i in result.issues)
    assert result.fields  # Original paths still catalogued.


def test_empty_category_distinguished_from_missing_category():
    result = normalize_capture(metadata(), b'{"data":{"new_creation":[]}}')
    assert not result.facts and not result.issues
    assert '/data/new_creation' in {f.pointer for f in result.fields}
    assert '/data/pump' not in {f.pointer for f in result.fields}


@pytest.mark.parametrize('data', [
    {'chain': 'sol', 'new_creation': [{'address': 'X', 'holder_count': 1}]},
    {'new_creation': [{'address': 'X', 'chain': 'sol', 'holder_count': 1}]},
])
def test_conflicting_chain_never_emits_facts(data):
    result = normalize_capture(metadata(), json.dumps({'data': data}).encode())
    assert not result.facts
    assert any(i.code == 'identity_conflict' for i in result.issues)


def test_invalid_item_is_reported_without_discovering_nested_wallets():
    result = normalize_capture(metadata(), b'{"new_creation":[1,{"address":"X","holder_count":2,"pool":{"address":"Pool","holder_count":3}},{"holder_count":9}]}')
    assert len(result.facts) == 1 and result.facts[0].token_address == 'X'
    assert len(result.issues) == 2
    assert any(f.pointer == '/new_creation/1/pool/address' and f.category == 'unknown' for f in result.fields)


def test_social_link_is_not_a_post_and_unknown_nested_data_is_preserved():
    result = normalize({'twitter': 'https://x.com/example/status/123', 'future': {'a/b': [False, None]}})
    assert {f.metric for f in result.facts} == {'twitter'}
    assert 'not_verified_post' in next(f for f in result.fields if f.pointer.endswith('/twitter')).reason
    assert any(f.pointer.endswith('/future/a~1b/0') and f.value_type == 'boolean' for f in result.fields)


@pytest.mark.parametrize('window', ['1m', '5m', '1h', '6h', '24h'])
@pytest.mark.parametrize('shape', ['flat', 'query', 'both'])
def test_ranking_request_window_survives_normalization(window, shape):
    params = {'interval': window} if shape == 'flat' else {'query': {'interval': window}}
    if shape == 'both': params['interval'] = window
    result = normalize_capture(metadata(endpoint='market_rank', request_params=params),
        b'{"data":{"rank":[{"address":"X","volume":"1.0001","swaps":2}]}}')
    assert not result.issues
    assert {f.metric for f in result.facts} == {f'volume_{window}', f'swaps_{window}'}
    assert all(f.quality == 'valid' and f.contract_version == 'gmgn.market_rank.v4' for f in result.facts)
    assert result.normalizer_version == NORMALIZER_VERSION


@pytest.mark.parametrize('params,reason', [
    ({}, 'queried_window_not_encoded_in_fact'),
    ({'interval': None}, 'ranking_window_unsupported_or_invalid'),
    ({'interval': []}, 'ranking_window_unsupported_or_invalid'),
    ({'interval': '7d'}, 'ranking_window_unsupported_or_invalid'),
    ({'query': []}, 'ranking_query_parameters_invalid'),
    ({'interval': '1m', 'query': {'interval': '24h'}}, 'ranking_window_conflicts_between_parameter_locations'),
])
def test_ranking_missing_or_conflicting_window_does_not_get_a_default(params, reason):
    result = normalize_capture(metadata(endpoint='market_rank', request_params=params),
        b'[{"address":"X","volume":"9","price":"1"}]')
    facts = {f.metric: f for f in result.facts}
    assert facts['volume'].quality == 'unverified'
    assert facts['price'].quality == 'valid'
    assert next(f for f in result.fields if f.pointer == '/0/volume').reason == reason


@pytest.mark.parametrize('group', ['pump', 'near_completion'])
def test_documented_and_observed_near_completion_paths_are_preserved(group):
    result = normalize_capture(metadata(), json.dumps({'data': {group: [{'address': 'X', 'holder_count': 1}]}}).encode())
    assert not result.issues
    assert result.facts[0].pointer == f'/data/{group}/0/holder_count'


def test_full_three_by_eighty_capture_preserves_unknowns_with_bounded_budget():
    item = dict(address='X', holder_count=1, **{f'unknown_{i}': i for i in range(130)})
    payload = {'data': {group: [item] * 80 for group in ('new_creation', 'near_completion', 'completed')}}
    result = normalize_capture(metadata(), json.dumps(payload).encode())
    assert not result.issues
    assert len(result.facts) == 240
    assert len(result.fields) > 30000
    assert any(f.pointer == '/data/completed/79/unknown_129' for f in result.fields)


def test_trenches_node_overflow_never_partially_publishes(monkeypatch):
    import midas_data.normalize as module
    monkeypatch.setattr(module, 'MAX_TRENCH_JSON_NODES', 6)
    result = normalize({'holder_count': 1})
    assert not result.facts and not result.fields
    assert result.issues[0].code == 'json_structure_limit'

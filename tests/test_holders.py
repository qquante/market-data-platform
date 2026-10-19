"""Offline behavioral tests: synthetic data only, no provider or SQL access."""
import hashlib
import json
from uuid import UUID, uuid4

import pytest

from midas_data.contracts import CaptureMetadata
from midas_data.holders import HolderParseResult, parse_holders
from midas_data import holders, normalize as common


def metadata(**changes):
    data = dict(capture_id=UUID('ae34520c-bd2e-4af6-8d21-1b361e8c3437'),
        source_kind='fixture', endpoint='token_top_holders', chain='sol',
        requested_at='2026-09-10T00:00:00Z', received_at='2026-09-10T00:00:01Z',
        http_status=200, evidence_kind='legacy_reserialized',
        request_params={'chain':'sol', 'address':'TokenCaseSensitive'})
    data.update(changes)
    return CaptureMetadata(**data)


def parse(rows, meta=None, **extra):
    return parse_holders(meta or metadata(), json.dumps({'list':rows, **extra}).encode())


def codes(result):
    return {x.code for x in result.issues}


def test_two_wallets_keep_token_identity_and_distinct_stable_row_ids():
    raw = b'{"list":[{"address":"WalletA","balance":"1"},{"address":"WalletB","balance":"2"}]}'
    meta = metadata()
    first = parse_holders(meta, raw)
    assert not first.issues and first.returned_rows == 2
    assert [x.token_address for x in first.holders] == ['TokenCaseSensitive'] * 2
    assert [x.wallet_address for x in first.holders] == ['WalletA', 'WalletB']
    assert [x.pointer for x in first.holders] == ['/list/0', '/list/1']
    assert len({x.holder_id for x in first.holders}) == 2
    assert first == parse_holders(meta, raw)
    other = parse_holders(metadata(capture_id=uuid4()), raw)
    assert {x.holder_id for x in first.holders}.isdisjoint(x.holder_id for x in other.holders)
    assert first.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert HolderParseResult.model_validate_json(first.model_dump_json()) == first
    assert not hasattr(first, 'facts')


def test_units_and_precision_never_use_float_or_token_decimals():
    raw = b'{"list":[{"address":"Wallet","account_address":"TokenAccount","balance":0.00000000000000000012345678901234567890,"amount_cur":"0.00000000000000000012345678901234567890","native_balance":"18446744073709551615","amount_percentage":0.05,"usd_value":"1000.001","decimals":9}]}'
    result = parse_holders(metadata(), raw)
    assert not result.issues
    row = result.holders[0]
    assert row.wallet_address == 'Wallet' and row.token_account_address == 'TokenAccount'
    values = row.values
    assert values['balance'].value_text == '1.2345678901234567890E-19'
    assert values['balance'].unit == 'token' and values['balance'].value_type == 'decimal'
    assert values['amount_cur'].value_type == 'string'
    assert values['native_balance'].value_text == '18446744073709551615'
    assert values['native_balance'].unit == 'lamport'
    assert values['amount_percentage'].value_text == '0.05' and values['amount_percentage'].unit == 'fraction'
    assert values['usd_value'].unit == 'USD'
    assert set(values) == {'address','account_address','balance','amount_cur','native_balance','amount_percentage','usd_value'}
    assert all(x.quality == 'valid' for x in values.values())


@pytest.mark.parametrize('value,quality', [(None,'missing'), ('','missing'), (0,'valid'),
    (False,'invalid'), (-1,'invalid'), ('NaN','invalid'), ('1_000','invalid'),
    (' 1','invalid'), ('+1','invalid'), ('01','invalid'), ({'v':1},'invalid')])
def test_bad_or_missing_balance_is_preserved_not_zero_or_coerced(value, quality):
    result = parse([{'address':'Wallet', 'balance':value}])
    field = result.holders[0].values['balance']
    assert field.quality == quality
    assert field.pointer == '/list/0/balance'
    assert ('invalid_field' in codes(result)) == (quality == 'invalid')
    assert next(x for x in result.fields if x.pointer == field.pointer).quality == quality
    if isinstance(value, dict):
        assert field.value_type == 'object' and field.value_text is None
        assert '/list/0/balance/v' in {x.pointer for x in result.fields}


def test_absent_balance_does_not_fall_back_to_alias_or_zero():
    result = parse([{'address':'Wallet', 'amount_cur':'50'}])
    assert set(result.holders[0].values) == {'address','amount_cur'}
    assert result.holders[0].token_account_address is None
    assert '/list/0/balance' not in {x.pointer for x in result.fields}


def test_alias_conflict_preserves_both_values_with_unverified_quality():
    result = parse([{'address':'Wallet','balance':'1','amount_cur':'1.00000000000000000000000000001'}])
    assert codes(result) == {'balance_alias_conflict'}
    values = result.holders[0].values
    assert values['balance'].value_text == '1'
    assert values['amount_cur'].value_text == '1.00000000000000000000000000001'
    assert values['balance'].quality == values['amount_cur'].quality == 'unverified'
    assert all(x.quality == 'unverified' for x in result.fields if x.pointer.endswith(('/balance','/amount_cur')))


@pytest.mark.parametrize('key,value,quality', [
    ('amount_percentage',1,'valid'), ('amount_percentage','1.01','invalid'),
    ('amount_percentage','-0.01','invalid'), ('amount_percentage',50,'invalid'),
    ('native_balance',42,'valid'), ('native_balance','42','valid'),
    ('native_balance',42.0,'invalid'), ('native_balance','4.2e1','invalid'),
    ('native_balance',True,'invalid'), ('native_balance',-1,'invalid'),
    ('usd_value','-0.01','invalid'),
])
def test_fraction_native_and_valuation_domains(key,value,quality):
    field = parse([{'address':'Wallet',key:value}]).holders[0].values[key]
    assert field.quality == quality


@pytest.mark.parametrize('address', [None,'',' Wallet','Wal let',False,33,'x'*257,'é'*129])
def test_invalid_wallet_skips_only_that_row_and_preserves_catalogue(address):
    result = parse([{'address':address,'balance':'10'},{'address':'Good','balance':'20'}])
    assert result.returned_rows == 2
    assert [x.wallet_address for x in result.holders] == ['Good']
    assert 'invalid_holder_identity' in codes(result)
    assert '/list/0/balance' in {x.pointer for x in result.fields}


def test_invalid_optional_account_is_not_replaced_by_wallet():
    result = parse([{'address':'Wallet','account_address':9}])
    assert len(result.holders) == 1 and result.holders[0].token_account_address is None
    field = result.holders[0].values['account_address']
    assert field.quality == 'invalid' and field.value_text == '9'
    assert field.value_type == 'integer' and field.declared_type == 'string'


def test_repeated_wallets_keep_both_rows_and_mark_identity_ambiguous():
    result = parse([{'address':'Same','balance':'5'},{'address':'Same','balance':'7'}])
    assert result.returned_rows == len(result.holders) == 2
    assert len({x.holder_id for x in result.holders}) == 2
    assert [x.values['balance'].value_text for x in result.holders] == ['5','7']
    assert all(x.identity_quality == x.values['address'].quality == 'unverified' for x in result.holders)
    assert codes(result) == {'duplicate_wallet'}


def test_non_object_item_is_reported_without_losing_other_rows():
    result = parse([None,[],{'address':'Wallet'}])
    assert result.returned_rows == 3 and len(result.holders) == 1
    assert result.holders[0].pointer == '/list/2'
    assert codes(result) == {'invalid_holder_item'}


@pytest.mark.parametrize('change,code', [
    ({'request_params':{}},'missing_request_identity'),
    ({'request_params':{'chain':'sol','address':''}},'missing_request_identity'),
    ({'request_params':{'chain':'sol','address':'x'*257}},'missing_request_identity'),
    ({'request_params':{'chain':'sol','address':'Token','token_address':'Other'}},'identity_conflict'),
    ({'chain':None,'request_params':{'address':'Token'}},'unsupported_chain'),
    ({'chain':'base','request_params':{'chain':'base','address':'Token'}},'unsupported_chain'),
    ({'endpoint':'token_top_traders'},'unsupported_endpoint'),
    ({'evidence_kind':'original_response'},'unsupported_evidence_format'),
    ({'http_status':429},'http_error'),
])
def test_invalid_capture_context_emits_no_holder_rows(change,code):
    result = parse([{'address':'Wallet','balance':'10'}],metadata(**change))
    assert not result.holders and code in codes(result) and result.fields


@pytest.mark.parametrize('extra', [{'chain':'base'},{'chain':None}])
def test_top_level_chain_conflict_rejects_snapshot(extra):
    result = parse([{'address':'Wallet'}],**extra)
    assert not result.holders and codes(result) == {'identity_conflict'}


def test_row_chain_conflict_rejects_the_whole_snapshot():
    result = parse([{'address':'A'},{'address':'B','chain':'base'}])
    assert not result.holders and codes(result) == {'identity_conflict'}
    assert '/list/1/address' in {x.pointer for x in result.fields}


def test_unknown_fields_are_catalogued_without_discovering_wallets_or_metrics():
    result = parse([{'address':'Wallet','native_transfer':{'address':'Funder'},'a/b~c':{'tag':'text'},'profit':999}],
                   extra={'address':'NestedOther','list':[{'address':'Phantom'}]})
    assert len(result.holders) == 1 and set(result.holders[0].values) == {'address'}
    fields = {x.pointer:x for x in result.fields}
    assert fields['/list/0/a~1b~0c/tag'].reason == 'unmapped_field'
    assert fields['/extra/list/0/address'].quality == 'unverified'
    assert '/list/0/profit' in fields


@pytest.mark.parametrize('source_event', [None,'2026-09-09T21:59:59-03:00'])
def test_snapshot_clock_not_replaced_by_activity_and_metadata_preserved(source_event):
    params={'chain':'sol','address':'Token','limit':20,'order_by':'profit','direction':'desc','tag':'dev'}
    meta=metadata(source_event_at=source_event,request_params=params)
    result=parse([{'address':'Wallet','last_active_timestamp':1,'created_at':2}],meta)
    row=result.holders[0]
    assert row.occurred_at == (meta.source_event_at or meta.received_at)
    assert row.time_basis == ('source' if source_event else 'received')
    assert row.occurred_at.utcoffset().total_seconds() == 0
    assert result.metadata == meta and result.metadata.source_kind == 'fixture'
    assert result.metadata.evidence_kind == 'legacy_reserialized'
    assert not hasattr(result,'available_at')
    meta.request_params['tag']='sniper'
    assert result.metadata.request_params['tag']=='dev'


def test_missing_selection_parameters_stay_absent():
    result=parse([{'address':'Wallet'}])
    assert set(result.metadata.request_params)=={'chain','address'}
    assert result.collection_scope=='partial_list' and not result.issues


@pytest.mark.parametrize('params', [
    {'limit':0},{'limit':101},{'limit':False},{'limit':'20.0'},
    {'order_by':'invented'},{'direction':'up'},{'tag':None},
])
def test_explicit_invalid_selection_is_not_replaced_by_defaults(params):
    meta=metadata(request_params={'chain':'sol','address':'Token',**params})
    result=parse([{'address':'Wallet'}],meta)
    assert not result.holders and codes(result)=={'invalid_request_context'}


def test_requested_limit_mismatch_is_not_silent_truncation():
    result=parse([{'address':'A'},{'address':'B'}],metadata(request_params={'chain':'sol','address':'Token','limit':'1'}))
    assert result.returned_rows==2 and not result.holders
    assert codes(result)=={'request_limit_exceeded'}


def test_hundred_rows_accepted_and_101_rejected_without_partial_holders():
    result=parse([{'address':f'Wallet{i}'} for i in range(100)])
    assert len(result.holders)==100 and not result.issues
    result=parse([{'address':f'Wallet{i}'} for i in range(101)])
    assert not result.holders and result.returned_rows==101
    assert codes(result)=={'holder_count_limit'} and result.fields


@pytest.mark.parametrize('raw,code', [
    (b'','invalid_json'), (b'not json','invalid_json'), (b'\xff','invalid_utf8'),
    (b'{"list":[],"list":[]}','duplicate_json_key'),
    (b'{"list":[{"address":"A","balance":1,"balance":2}]}','duplicate_json_key'),
    (b'{"list":[{"address":"A","balance":NaN}]}','non_finite_json_number'),
    (b'{"list":[{"address":"A","balance":Infinity}]}','non_finite_json_number'),
    (b'{"list":[{"address":"\\ud800"}]}','invalid_unicode'),
    (b'{"list":[{"address":"A\\u0000"}]}','invalid_unicode'),
])
def test_malformed_payloads_fail_atomically(raw,code):
    result=parse_holders(metadata(),raw)
    assert not result.holders and not result.fields and result.returned_rows is None
    assert codes(result)=={code}


@pytest.mark.parametrize('root,code', [
    ({'list':None},'unsupported_shape'), ([], 'unsupported_shape'),
    ({'data':{'list':[]}},'unsupported_shape'),
    ({'code':0,'data':{'list':[]}},'unsupported_shape'),
    ({'code':False,'list':[]},'provider_error'),
    ({'code':429,'list':[]},'provider_error'),
    ({'list':[],'data':{}},'unsupported_shape'),
    ({'list':[],'address':'MaybeToken'},'unsupported_shape'),
])
def test_unknown_envelopes_and_ambiguous_top_level_identity(root,code):
    result=parse_holders(metadata(),json.dumps(root).encode())
    assert not result.holders and codes(result)=={code}


def test_successful_empty_list_is_distinct_from_failed_empty_file():
    result=parse_holders(metadata(),b'{"list":[]}')
    assert result.returned_rows==0 and not result.holders and not result.issues
    assert {x.pointer for x in result.fields}=={'','/list'}
    failed=parse_holders(metadata(),b'')
    assert failed.returned_rows is None and codes(failed)=={'invalid_json'}


def test_byte_limit_fails_before_parsing():
    result=parse_holders(metadata(),b'x'*(common.MAX_RAW_BYTES+1))
    assert codes(result)=={'payload_too_large'} and not result.fields


def test_node_and_depth_limits_never_return_partial_catalogue(monkeypatch):
    monkeypatch.setattr(common,'MAX_JSON_NODES',3)
    assert not parse([]).issues
    result=parse([{'address':'Wallet'}])
    assert codes(result)=={'json_structure_limit'} and not result.fields and not result.holders
    monkeypatch.setattr(common,'MAX_JSON_NODES',20000)
    root=0
    for _ in range(34):root=[root]
    result=parse_holders(metadata(),json.dumps({'list':[],'unknown':root}).encode())
    assert codes(result)=={'json_structure_limit'} and not result.fields


def test_pointer_and_complete_output_budget(monkeypatch):
    result=parse([{'address':'Wallet','x'*2049:1}])
    assert codes(result)=={'pointer_size_limit'} and not result.holders
    raw=b'{"list":[{"address":"Wallet","balance":"10"}]}'
    result=parse_holders(metadata(),raw)
    size=len(result.model_dump_json().encode('utf-8'))
    monkeypatch.setattr(holders,'MAX_OUTPUT_BYTES',size)
    assert parse_holders(metadata(),raw)==result
    monkeypatch.setattr(holders,'MAX_OUTPUT_BYTES',size-1)
    rejected=parse_holders(metadata(),raw)
    assert codes(rejected)=={'output_size_limit'} and not rejected.holders and not rejected.fields


def test_offline_parser_does_not_register_holders_in_token_normalizer():
    assert 'token_top_holders' not in common.CONTRACT_VERSIONS
    raw=b'{"list":[{"address":"Wallet","balance":"1"}]}'
    assert len(parse_holders(metadata(),raw).holders)==1
    result=common.normalize_capture(metadata(),raw)
    assert not result.facts and codes(result)=={'unsupported_endpoint'}


def test_programmer_errors_are_not_silent_empty_snapshots():
    with pytest.raises(TypeError):parse_holders({},b'{"list":[]}')
    with pytest.raises(TypeError):parse_holders(metadata(),'{"list":[]}')

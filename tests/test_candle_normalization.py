"""Candle admission uses the same exact reader contract, without token facts."""
import json
import pytest
from midas_data.normalize import normalize_capture, FATAL_ISSUE_CODES, UNSUPPORTED_ISSUE_CODES
from test_candles import metadata, candle, START


def normalize(rows, meta=None):
    return normalize_capture(meta or metadata(), json.dumps({'code':0,'data':{'list':rows}}).encode())


def test_supported_candles_are_catalogued_with_a_new_version_and_no_token_facts():
    result=normalize([candle(extra={'future_metric':123})])
    assert result.normalizer_version=='gmgn-candles-normalize-v1'
    assert not result.facts
    assert not result.issues
    fields={f.pointer:f for f in result.fields}
    for key in ['time','open','high','low','close','volume']:
        assert fields['/data/list/0/'+key].quality=='valid'
        assert fields['/data/list/0/'+key].contract_id=='gmgn-candles-read-v1:'+key
    assert fields['/data/list/0/extra/future_metric'].quality=='unverified'


@pytest.mark.parametrize('bad,reason', [({'volume':None},'invalid_number'),
    ({'low':'0.03'},'inconsistent_ohlc'),({'time':START+1},'unaligned_bar')])
def test_partial_rows_remain_unavailable_without_rejecting_valid_rows(bad,reason):
    result=normalize([candle(),candle(time=START+60000,**bad)]) if 'time' not in bad else normalize([candle(),candle(**bad)])
    fields={f.pointer:f for f in result.fields}
    assert fields['/data/list/0/close'].quality=='valid'
    assert fields['/data/list/1/close'].quality=='invalid'
    assert fields['/data/list/1/close'].reason==reason
    assert any(i.code=='invalid_candle' and i.detail==reason for i in result.issues)
    assert not any(i.code in FATAL_ISSUE_CODES|UNSUPPORTED_ISSUE_CODES for i in result.issues)
    assert not result.facts


@pytest.mark.parametrize('params',[{}, {'query':{'chain':'base'}}])
def test_invalid_candle_envelope_or_request_is_still_quarantined(params):
    result=normalize([candle()],metadata(request_params=params))
    assert result.normalizer_version=='gmgn-candles-normalize-v1'
    assert any(i.code in FATAL_ISSUE_CODES for i in result.issues)
    assert not any(f.quality=='valid' for f in result.fields)
    assert not result.facts


def test_duplicates_empty_rows_and_unrecognized_endpoints_keep_honest_quality():
    duplicate=normalize([candle(),candle()])
    assert len([i for i in duplicate.issues if i.code=='invalid_candle'])==2
    assert not any(f.quality=='valid' for f in duplicate.fields)
    assert normalize([]).issues==[]
    unknown=normalize_capture(metadata(endpoint='unrecognized'),b'{"code":0,"data":{}}')
    assert unknown.normalizer_version=='gmgn-p0-v4'
    assert any(i.code=='unsupported_endpoint' for i in unknown.issues)


@pytest.mark.parametrize('raw',[b'{"code":0,"code":0}',b'{"code":1,"data":{"list":[]}}',b'not json'])
def test_bad_payload_is_never_promoted(raw):
    result=normalize_capture(metadata(),raw)
    assert result.normalizer_version=='gmgn-candles-normalize-v1'
    assert any(i.code in FATAL_ISSUE_CODES for i in result.issues)
    assert not result.facts

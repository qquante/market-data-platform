import json
from uuid import uuid4
import pytest
from midas_data.contracts import CaptureMetadata
from midas_data.candles import parse_candles,CandleContract
TOKEN='So11111111111111111111111111111111111111112'
START=1789120800000

def metadata(**changes):
    return CaptureMetadata(**dict(dict(capture_id=uuid4(),source_kind='fixture',endpoint='token_kline',chain='sol',
        requested_at='2026-09-11T10:02:00Z',received_at='2026-09-11T10:02:01Z',http_status=200,evidence_kind='original_response',
        request_params={'query':{'chain':'sol','address':TOKEN,'resolution':'1m','from':START,'to':START+120000}}),**changes))

def candle(**changes):return dict(dict(time=START,open='0.0100',high='0.02',low='0.009',close='0.011',volume='10.00100'),**changes)
def parse(rows,meta=None):return parse_candles(meta or metadata(),json.dumps({'code':0,'data':{'list':rows}}).encode(),'2026-09-11T10:02:03Z')

def test_exact_candles_separate_bar_receipt_and_available_time_without_interpolation():
    r=parse([candle()]);assert r['items'][0]['open']=='0.0100' and r['items'][0]['volume']=='10.00100'
    assert r['coverage']['eligible_rows']==1 and r['coverage']['missing_bar_times_ms']==[START+60000]
    assert r['items'][0]['closed_at']<r['received_at']<r['available_at']

@pytest.mark.parametrize('changes,reason',[({'volume':None},'invalid_number'),({'low':'0.03'},'inconsistent_ohlc'),
    ({'time':START//1000},'outside_requested_range'),({'time':START+1},'unaligned_bar'),({'time':START+120000},'outside_requested_range'),
    ({'open':'NaN'},'invalid_number'),({'close':'1e999'},'invalid_number'),({'open':True},'invalid_number')])
def test_bad_candles_count_as_unknown(changes,reason):
    r=parse([candle(**changes)]);assert r['coverage']['eligible_rows']==0 and r['items'][0]['reason']==reason

def test_duplicates_invalidate_both_and_unfinished_bar_is_not_a_close():
    r=parse([candle(),candle()]);assert r['coverage']['eligible_rows']==0
    r=parse([candle(time=START+60000)],metadata(received_at='2026-09-11T10:01:30Z',requested_at='2026-09-11T10:01:20Z',request_params={'query':{'chain':'sol','address':TOKEN,'resolution':'1m','from':START,'to':START+70000}}))
    assert r['items'][0]['reason']=='unfinished_bar_at_receipt'

@pytest.mark.parametrize('params',[{}, {'query':{'chain':'sol','address':TOKEN,'resolution':'1m','from_timestamp':START,'to_timestamp':START+120000}}])
def test_unverified_range_alias_is_not_an_accepted_range(params):
    with pytest.raises(CandleContract):parse([candle()],metadata(request_params=params))

def test_duplicate_json_provider_failure_and_limits():
    for raw in [b'{"code":0,"code":0,"data":{"list":[]}}',b'{"code":1,"data":{"list":[]}}']:
        with pytest.raises(CandleContract):parse_candles(metadata(),raw,'2026-09-11T10:02:03Z')
    with pytest.raises(CandleContract):parse([candle()]*1501)

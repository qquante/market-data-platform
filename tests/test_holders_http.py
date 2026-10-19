"""Original HTTP evidence contract; all clocks below are explicit fixtures."""
import json
import pytest
from midas_data.holders import parse_holders
from midas_data.contracts import CaptureMetadata
from uuid import uuid4


def metadata(**changes):
    return CaptureMetadata(**dict(dict(capture_id=uuid4(),source_kind='fixture',
        evidence_kind='original_response',endpoint='token_top_holders',chain='sol',
        requested_at='2026-09-09T12:00:00Z',received_at='2026-09-09T12:00:01Z',
        http_status=200,request_params={'chain':'sol','address':'Token','limit':10}),**changes))


def test_original_http_preserves_original_pointers_catalog_and_decimal():
    m=metadata()
    raw=b'{"code":0,"data":{"list":[{"address":"Wallet","balance":1.00000000000000000001}]},"message":"ok"}'
    r=parse_holders(m,raw)
    assert not r.issues and len(r.holders)==1
    assert r.holders[0].pointer=='/data/list/0'
    assert r.holders[0].values['balance'].value_text=='1.00000000000000000001'
    assert '/code' in {f.pointer for f in r.fields}
    assert r.model_dump_json()==parse_holders(m,raw).model_dump_json()


@pytest.mark.parametrize('body',[
    {'list':[]}, {'data':{'list':[]}}, {'code':False,'data':{'list':[]}},
    {'code':'0','data':{'list':[]}}, {'code':429,'data':{'list':[]}},
    {'code':0,'list':[],'data':{'list':[]}}, {'code':0,'data':[]},
    {'code':0,'chain':'base','data':{'list':[]}},
    {'code':0,'address':'Other','data':{'list':[]}},
    {'code':0,'data':{'list':[],'address':'Other'}},
])
def test_ambiguous_or_failed_envelopes_never_emit_rows(body):
    r=parse_holders(metadata(),json.dumps(body).encode())
    assert r.issues and not r.holders


def test_nested_collector_params_and_identity_conflict():
    raw=b'{"code":0,"data":{"list":[{"address":"Wallet"}]}}'
    q={'chain':'sol','address':'Token','limit':10}
    assert len(parse_holders(metadata(request_params={'query':q,'body':None}),raw).holders)==1
    r=parse_holders(metadata(request_params={'query':q,'address':'Other'}),raw)
    assert not r.holders and any(i.code=='identity_conflict' for i in r.issues)


def test_empty_original_snapshot_is_observed_empty_not_failure():
    r=parse_holders(metadata(),b'{"code":0,"data":{"list":[]}}')
    assert r.returned_rows==0 and not r.holders and not r.issues


@pytest.mark.parametrize('data,params,code,pointer',[
    ({'chain':'base','list':[]},None,'identity_conflict','/data/chain'),
    ({'list':[{'address':'Wallet'}]*11},None,'request_limit_exceeded','/data/list'),
    ({'list':[{'address':'Wallet'}]*101},None,'holder_count_limit','/data/list'),
])
def test_http_issue_pointers_reference_original_bytes(data,params,code,pointer):
    result=parse_holders(metadata(),json.dumps({'code':0,'data':data}).encode())
    assert any(i.code==code and i.pointer==pointer for i in result.issues)
    assert not result.holders

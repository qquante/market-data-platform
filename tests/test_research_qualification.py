import json,copy
from uuid import uuid4
import pytest
from midas_data.contracts import CaptureMetadata
from midas_data.normalize import normalize_capture
from midas_data.valuation import build_valuation
from midas_data.trench_economics import METHOD
from midas_data.research_sampling import sample,verify,MAX_FRAME

def inputs(**changes):
    m=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint='trenches',chain='sol',
        requested_at='2026-09-11T10:00:00Z',received_at='2026-09-11T10:00:01Z',http_status=200,evidence_kind='original_response')
    item=dict(address='FixtureToken',price='0.01',market_cap='10000000',total_supply='1000000000');item.update(changes)
    result=normalize_capture(m,json.dumps({'code':0,'data':{'new_creation':[item]}}).encode())
    rows=[dict(f.model_dump(),normalizer_version=result.normalizer_version,available_at='2026-09-11T10:00:02Z') for f in result.facts]
    return rows[0],rows

def test_explicit_empirical_method_does_not_mutate_or_promote_strict_facts():
    anchor,rows=inputs();saved=copy.deepcopy(rows)
    strict=build_valuation(anchor,'trenches','/data/new_creation/0',rows)
    empirical=build_valuation(anchor,'trenches','/data/new_creation/0',rows,method=METHOD)
    assert strict['price']['state']==strict['market_cap']['state']=='unavailable'
    assert empirical['price']['state']==empirical['market_cap']['state']=='available'
    assert empirical['market_cap']['value_text']=='10000000'
    assert 'empirical' in empirical['interpretation_basis'] and rows==saved

@pytest.mark.parametrize('changes',[{'total_supply':None},{'price':None},{'market_cap':'10001001'},
    {'price':'-1'},{'total_supply':'1e1001'},{'market_cap':'NaN'}])
def test_absent_invalid_and_inconsistent_economics_remain_unknown(changes):
    anchor,rows=inputs(**changes)
    result=build_valuation(anchor,'trenches','/data/new_creation/0',rows,method=METHOD)
    assert result['market_cap']['state']==result['price']['state']=='unavailable'

def test_snapshot_source_revision_contract_and_duplicate_isolation():
    anchor,rows=inputs()
    for field,value in [('source_kind','gmgn'),('normalizer_version','other'),('contract_version','other'),('chain','bsc')]:
        bad=copy.deepcopy(rows);next(r for r in bad if r['metric']=='price')[field]=value
        assert build_valuation(anchor,'trenches','/data/new_creation/0',bad,method=METHOD)['market_cap']['state']=='unavailable'
    bad=rows+[next(r for r in rows if r['metric']=='price')]
    assert build_valuation(anchor,'trenches','/data/new_creation/0',bad,method=METHOD)['price']['reason']=='empirical_contract_inputs_unavailable'

def test_outcome_blind_sampling_replays_and_rejects_tampering():
    rows=[{'registry_id':i,'result':i%2} for i in range(1,201)]
    members,frame=sample(rows,'predeclared-v1',50)
    assert len(members)==50 and frame['population']==200 and frame['inclusion_probability']=='50/200'
    assert sample(list(reversed(rows)),'predeclared-v1',50)==(members,frame)
    assert [r['registry_id'] for r in members]==[r['registry_id'] for r in sample([dict(r,result=100) for r in rows],'predeclared-v1',50)[0]]
    verify(frame,members,'predeclared-v1',50)
    for altered in [dict(frame,population=199),dict(frame,registry_ids=frame['registry_ids'][1:]),dict(frame,seed='other')]:
        with pytest.raises(ValueError):verify(altered,members,'predeclared-v1',50)
    with pytest.raises(ValueError):verify(frame,members[:-1],'predeclared-v1',50)
    with pytest.raises(ValueError):sample([{'registry_id':i} for i in range(MAX_FRAME+1)],'x',50)

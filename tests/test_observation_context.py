"""Exact source context, including missing/conflicting evidence and clock boundaries."""
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from midas_data.observation_context import ContextUnavailable, build_observation_context


def build(item, *, endpoint='market_rank', params=None, group='pump', pointer=None):
    item = dict(address='FixtureToken', **item)
    prefix = f'/data/{group}/0' if endpoint == 'trenches' else '/data/rank/0'
    body = {'data': {group if endpoint == 'trenches' else 'rank': [item]}}
    t = datetime(2026, 9, 9, tzinfo=timezone.utc)
    fact = dict(pointer=pointer or prefix + '/price', chain='base', token_address='FixtureToken',
                capture_id=uuid4(), source_kind='fixture', evidence_kind='original_response',
                occurred_at=t, time_basis='received', available_at=t)
    capture = dict(endpoint=endpoint, raw_sha256='0'*64, capture_recorded_at=t,
                   metadata=dict(request_params=params or {}, requested_at=t.isoformat(),
                                 received_at=t.isoformat(), source_event_at=None, http_status=200))
    return build_observation_context(fact,capture,json.dumps(body).encode())


def test_reported_rank_is_not_response_position_and_request_clocks_survive():
    c=build({'price':'1', 'rank':12}, params={'query':{'interval':'5m','order_by':'volume',
        'direction':'desc','limit':20,'filters':['verified'],'min_marketcap':20000}})
    assert c['entity']['reported_rank']['value_text']=='12'
    assert c['entity']['response_index']==0 and c['entity']['response_position']==1
    assert c['request']['window']['value']=='5m'
    assert c['request']['filter_parameters'][0]['parameters']['min_marketcap']==20000
    assert c['clocks']['source_event_at'] is None


@pytest.mark.parametrize('value,state', [(None,'null'),(False,'invalid'),(0,'invalid'),(-1,'invalid'),('3','invalid'),(2**63,'invalid')])
def test_invalid_reported_rank_never_becomes_response_position(value,state):
    rank=build({'price':'1','rank':value})['entity']['reported_rank']
    assert rank['state']==state and rank['value_text'] is None


def test_missing_request_is_unknown_not_default_or_empty_filters():
    c=build({'price':'1'})
    assert c['entity']['reported_rank']['state']=='missing'
    assert c['request']['window']['state']=='missing'
    assert c['request']['filter_parameters']==[]
    assert 'not_inferred' in c['request']['filter_basis']


def test_conflicting_windows_and_filters_stay_explicit():
    c=build({'price':'1'},params={'interval':'1m','filters':[],
                               'query':{'interval':'24h','filters':['verified']}})
    assert c['request']['window']['state']=='conflict'
    assert c['request']['window']['value'] is None
    assert len(c['request']['filter_parameters'])==2


def test_trenches_uses_matching_category_and_preserves_pump_pointer():
    c=build({'price':'1'},endpoint='trenches',params={'body':{
        'near_completion':{'limit':60,'filters':['onchain']},
        'completed':{'limit':2,'filters':['offchain']}}})
    assert c['entity']['response_category']=='pump'
    assert c['entity']['lifecycle_stage']=='near_completion'
    assert c['entity']['reported_rank']['state']=='not_applicable'
    assert c['request']['limit']['value']==60
    assert c['request']['filter_parameters'][0]['pointer']=='/request_params/body/near_completion'
    assert c['request']['window']['state']=='not_applicable'


def test_pointer_cannot_select_a_different_token_item():
    with pytest.raises(ContextUnavailable):
        build({'price':'1'},pointer='/data/rank/1/price')


def test_invalid_query_container_does_not_promote_a_flat_window():
    c=build({'price':'1'},params={'interval':'1m','query':[]})
    assert c['request']['window']['state']=='invalid'
    assert c['request']['window']['value'] is None


def test_api_unavailable_evidence_is_503_without_leaking_details(tmp_path):
    import asyncio
    from types import SimpleNamespace
    import httpx
    from midas_data.api import make_app
    from midas_data.raw_store import RawIntegrityError
    from midas_data.settings import Settings

    class Fake:
        async def observation_context_record(self,*args):
            return {}, {'raw_sha256':'fixture'}
        def read(self,*args):
            raise RawIntegrityError('private filesystem path must not escape')

    async def run():
        settings=Settings('unused','unused','fixture-key','nats://unused','http://unused',
                          'fixture','fixture',tmp_path,str(tmp_path))
        app=make_app(settings)
        fake=Fake();fake.raw=fake;app.state.store=fake
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            reply=await client.get(f'/v1/observations/{uuid4()}/context',
                params={'source_kind':'fixture','knowledge_cutoff':'2026-09-09T00:00:00Z'},
                headers={'authorization':'Bearer fixture-key'})
        assert reply.status_code==503
        assert reply.json()=={'detail':'Observation context evidence unavailable'}
    asyncio.run(run())


@pytest.mark.parametrize('value,state,seconds', [
    (None,'null',None), ('','empty',None), (0,'zero_unusable',None),
    ('0','zero_unusable',None), (-1,'negative_invalid',None),
    (False,'invalid_type',None), (1.5,'invalid_type',None),
    ('1e3','invalid_type',None), (' 100','invalid_type',None),
    (1788979500,'reported','1788979500'), ('1788979500','reported','1788979500'),
])
def test_temporal_context_preserves_scalar_without_inventing_event(value,state,seconds):
    c=build({'price':'1','open_timestamp':value},endpoint='trenches')
    t=c['temporal_attributes']['open_timestamp']
    assert t['state']==state and t['usable_seconds_text']==seconds
    assert t['pointer']=='/data/pump/0/open_timestamp'
    assert c['clocks']['source_event_at'] is None
    assert c['clocks']['time_basis']=='received'


def test_temporal_absence_negative_duration_and_zero_duration_are_distinct():
    c=build({'price':'1','complete_timestamp':0,'complete_cost_time':-1788979500},endpoint='trenches')
    attrs=c['temporal_attributes']
    assert attrs['created_timestamp']['state']=='absent'
    assert attrs['created_timestamp']['pointer'] is None
    assert attrs['complete_cost_time']['state']=='negative_invalid'
    assert attrs['complete_cost_time']['value_text']=='-1788979500'
    assert attrs['complete_cost_time']['usable_seconds_text'] is None
    assert c['temporal_consistency']['duration_matches_reported_clocks'] is None
    zero=build({'price':'1','complete_cost_time':0},endpoint='trenches')
    assert zero['temporal_attributes']['complete_cost_time']['state']=='reported'
    assert zero['temporal_attributes']['complete_cost_time']['usable_seconds_text']=='0'


@pytest.mark.parametrize('duration,consistent',[(60,True),(61,False)])
def test_temporal_consistency_does_not_rewrite_reported_duration(duration,consistent):
    c=build({'price':'1','created_timestamp':100,'complete_timestamp':160,
             'complete_cost_time':duration},endpoint='trenches',group='completed')
    assert c['temporal_consistency']['duration_matches_reported_clocks'] is consistent
    assert c['temporal_attributes']['complete_cost_time']['value_text']==str(duration)


def test_response_count_includes_invalid_items_and_never_slices_to_requested_limit():
    item={'address':'FixtureToken','price':'1'}
    raw=json.dumps({'data':{'pump':[item,17,item]}}).encode()
    t=datetime(2026,9,9,tzinfo=timezone.utc)
    fact=dict(pointer='/data/pump/2/price',chain='sol',token_address='FixtureToken',
              capture_id=uuid4(),source_kind='fixture',evidence_kind='original_response',
              occurred_at=t,time_basis='received',available_at=t)
    capture=dict(endpoint='trenches',raw_sha256='0'*64,capture_recorded_at=t,
       metadata=dict(request_params={'body':{'near_completion':{'limit':2}}},
                     requested_at=t,received_at=t,http_status=200))
    c=build_observation_context(fact,capture,raw)
    assert c['response']=={'raw_bytes':len(raw),'list_pointer':'/data/pump','list_item_count':3,
                           'requested_limit_comparison':'exceeded'}
    assert c['entity']['response_position']==3


def test_unknown_limit_does_not_claim_complete_source_coverage():
    c=build({'price':'1'})
    assert c['response']['list_item_count']==1
    assert c['response']['requested_limit_comparison']=='unknown'
    assert set(c['temporal_attributes'])=={'creation_timestamp','open_timestamp'}

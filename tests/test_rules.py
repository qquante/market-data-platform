"""Deterministic rule decisions on synthetic shared snapshots; no SQL/provider I/O."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Inexact, localcontext
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from midas_data.api import make_app
from midas_data.features import SharedFeatures, _hash, _json
from midas_data.feature_windows import FeatureWindows, WINDOW_VERSION
from midas_data.rules import ComparisonRule, evaluate
from midas_data.settings import Settings
from test_features import window
from test_trajectory import METHOD


def definition(**changes):
    return dict(window=dict(source_kind='fixture', chain='sol', token='FixtureToken',
                method=METHOD, window_seconds=180, gap_after_seconds=120),
                feature='market_cap_latest', operator='gte', threshold='120', unit='usd', **changes)


class NoSql:
    def __getattr__(self, name):
        raise AssertionError('Rule attempted SQL: ' + name)


def setup(source=None, body=None):
    rule = ComparisonRule.model_validate(body or definition())
    owner = FeatureWindows(SimpleNamespace(pool=NoSql()))
    owner.connection = SimpleNamespace(is_closed=lambda: False)
    owner.checked_monotonic = time.monotonic()
    now = datetime.now(timezone.utc)
    key = _hash(rule.window.canonical())
    series, _ = SharedFeatures().build(source if source is not None else window())
    snapshot = dict(version=WINDOW_VERSION, window_id=key, generation=1,
        publication_cutoff=series['knowledge_cutoff'], next_due_at=now+timedelta(seconds=60), result=series)
    owner.windows[key] = dict(ready=True, expires_at=now+timedelta(seconds=600),
                             next_due_at=snapshot['next_due_at'], payload=_json(snapshot))
    return rule, owner, key


@pytest.mark.parametrize('op,threshold,state', [
    ('gt','120','false'), ('gte','120','true'), ('lt','120','false'),
    ('lte','120','true'), ('eq','120.000','true'), ('ne','120','false'),
    ('gt','119.999999999999999999999999999999999999999999','true'),
    ('lt','120.000000000000000000000000000000000000000001','true')])
def test_exact_comparisons_and_frame_provenance(op, threshold, state):
    body=definition();body.update(operator=op,threshold=threshold)
    rule, owner, key=setup(body=body)
    with localcontext() as ctx:
        ctx.prec=2;ctx.traps[Inexact]=True
        answer=evaluate(rule,owner)
    assert answer['state']==state and answer['frame']['generation']==1
    assert answer['window_id']==key and answer['feature']['input_point_ids']
    assert answer==evaluate(rule,owner)
    assert len(answer['rule_id'])==len(answer['evaluation_id'])==64
    assert (owner.reads,owner.polls)==(0,0)


@pytest.mark.parametrize('feature,unit,threshold', [
    ('market_cap_min_observed','usd','100'), ('market_cap_max_observed','usd','150'),
    ('market_cap_sample_change_pct','percent','20')])
def test_other_features_use_their_declared_units(feature,unit,threshold):
    body=definition();body.update(feature=feature,unit=unit,operator='eq',threshold=threshold)
    rule, owner, _=setup(body=body)
    assert evaluate(rule,owner)['state']=='true'


@pytest.mark.parametrize('values,times,end,gap,reason', [
    ((),[],180,120,'no_observations'),
    (('100',None),[0,60],100,120,'latest_observation_unavailable'),
    (('100','120'),[0,60],181,120,'stale_at_window_end'),
    (('100','120'),[60,60],100,120,'ambiguous_latest_observation')])
def test_missing_stale_and_ambiguous_never_become_false(values,times,end,gap,reason):
    rule,owner,_=setup(window(values,times=times,end=end,gap=gap))
    answer=evaluate(rule,owner)
    assert answer['state']=='unknown' and answer['reason']=='latest:'+reason


@pytest.mark.parametrize('source,reason', [
    (lambda: window(('100',None,'120')), 'unavailable_points'),
    (lambda: window(('100','110','120'),times=[0,0,60]), 'ambiguous_timestamp_points'),
    (lambda: window(('100','120'),times=[0,160],end=180,gap=120), 'flagged_gap_count')])
def test_extrema_cannot_pass_with_incomplete_sampled_coverage(source,reason):
    series=source()
    body=definition();body.update(feature='market_cap_max_observed',threshold='1')
    rule,owner,_=setup(series,body)
    answer=evaluate(rule,owner)
    assert answer['feature']['state']=='available'
    assert answer['state']=='unknown' and answer['reason']=='quality:'+reason
    # The latest-only predicate depends on the fresh tail, not previous sample continuity.
    latest=ComparisonRule.model_validate(definition())
    assert evaluate(latest,owner)['state']=='true'


def test_zero_baseline_and_insufficient_samples_abstain():
    for source,reason in ((window(('0','100')),'zero_baseline'),
                          (window(('100',),times=[120]),'insufficient_observations')):
        body=definition();body.update(feature='market_cap_sample_change_pct',unit='percent')
        rule,owner,_=setup(source,body)
        answer=evaluate(rule,owner)
        assert answer['state']=='unknown' and answer['reason']=='feature:'+reason


def test_canonical_identity_covers_semantic_definition():
    body=definition();body['window'].update(chain='BASE',token='0x'+'A'*40)
    rule=ComparisonRule.model_validate(body)
    same=deepcopy(body);same['window'].update(chain='base',token='0x'+'a'*40);same['threshold']='120.000'
    assert rule.definition()==ComparisonRule.model_validate(same).definition()
    original=_hash(rule.definition())
    for field,value in [('threshold','121'),('operator','gt'),('feature','market_cap_min_observed')]:
        other=deepcopy(body);other[field]=value
        assert _hash(ComparisonRule.model_validate(other).definition())!=original
    for field,value in [('source_kind','gmgn'),('window_seconds',181),('gap_after_seconds',121),
                        ('token','0x'+'b'*40),('method','gmgn.market_rank.v4.market_cap')]:
        other=deepcopy(body);other['window'][field]=value
        assert _hash(ComparisonRule.model_validate(other).definition())!=original
    for threshold in ('-0','0.000','-0.000'):
        other=deepcopy(body);other['threshold']=threshold
        assert ComparisonRule.model_validate(other).threshold=='0'


@pytest.mark.parametrize('field,value', [
    ('threshold',120),('threshold',True),('threshold','NaN'),('threshold','Infinity'),
    ('threshold','1e1000000'),('threshold','9'*161),('threshold',' 120'),
    ('unit','percent'),('operator','eval'),('version','future'),('quality_policy','ignore_missing')])
def test_invalid_or_incompatible_definitions_are_rejected(field,value):
    body=definition();body[field]=value
    with pytest.raises(ValidationError): ComparisonRule.model_validate(body)


def test_unknown_fields_and_invalid_window_rejected():
    for field,value in [('unknown','ignored'),('window_seconds',True),('method','unsupported')]:
        body=definition();body['window'][field]=value
        with pytest.raises(ValidationError): ComparisonRule.model_validate(body)
    body=definition();body['cooldown_seconds']=60
    with pytest.raises(ValidationError): ComparisonRule.model_validate(body)


@pytest.mark.parametrize('failure', ['expired','deadline','poll_stale','pending','closed','disconnected','removed'])
def test_owner_readiness_is_checked_on_every_call(failure):
    rule,owner,key=setup()
    assert evaluate(rule,owner)['state']=='true'
    entry=owner.windows[key]
    if failure=='expired':entry['expires_at']=datetime.now(timezone.utc)-timedelta(seconds=1)
    elif failure=='deadline':entry['next_due_at']=datetime.now(timezone.utc)-timedelta(seconds=1)
    elif failure=='poll_stale':owner.checked_monotonic-=11
    elif failure=='pending':entry['ready']=False
    elif failure=='closed':owner.connection.is_closed=lambda:True
    elif failure=='disconnected':owner.connection=None
    else:owner.windows.clear()
    answer=evaluate(rule,owner)
    assert answer['state']=='unknown'
    assert answer['frame'] is None and answer['feature'] is None


def test_version_mismatch_or_deadline_during_decoding_abstains():
    rule,owner,key=setup()
    payload=json.loads(owner.read(key))
    payload['result']['features']['version']='future'
    owner.windows[key]['payload']=_json(payload)
    assert evaluate(rule,owner)['reason']=='incompatible_frame'
    payload['result']['features']['version']=rule.definition()['dependencies']['feature_version']
    payload['next_due_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    owner.windows[key]['payload']=_json(payload)
    assert evaluate(rule,owner)['reason']=='window_not_ready'


def test_http_contract_auth_limits_and_read_only_consumers(tmp_path):
    async def run():
        settings=Settings('unused','unused','fixture-key','nats://unused','http://unused',
                          'fixture','fixture',tmp_path,str(tmp_path))
        app=make_app(settings);app.state.store=SimpleNamespace(pool=NoSql())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',
                                    headers={'authorization':'Bearer fixture-key'}) as client:
            route='/v1/rules/evaluate'
            assert (await client.post(route,json=definition(),headers={'authorization':'bad'})).status_code==401
            absent=await client.post(route,json=definition())
            assert absent.status_code==200 and absent.json()['reason']=='window_owner_unavailable'
            assert (await client.post(route,content=b'x'*8193)).status_code==413
            assert (await client.post(route,content=b'{broken')).status_code==422
            wrong=definition();wrong['threshold']=12
            response=await client.post(route,json=wrong)
            assert response.status_code==422 and 'input' not in response.json()['detail'][0]
            rule,owner,key=setup();app.state.feature_windows=owner
            before=owner.read(key)
            responses=await asyncio.gather(*[client.post(route,json=definition()) for _ in range(20)])
            assert all(r.status_code==200 and r.json()['state']=='true' for r in responses)
            assert all(r.content==responses[0].content for r in responses)
            assert responses[0].headers['cache-control']=='no-store'
            assert len(owner.windows)==1 and owner.read(key)==before and owner.reads==owner.polls==0
            responses[0].json()['feature']['value_text']='corrupt'
            assert evaluate(rule,owner)['feature']['value_text']=='120'
    asyncio.run(run())

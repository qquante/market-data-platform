from datetime import datetime,timedelta,timezone
import pytest
from midas_data.cohorts import labels,latest_frame,summarize
from midas_data.research_contracts import ResearchSpec

T=datetime(2026,9,9,tzinfo=timezone.utc)


def point(at,value='100',available=None,**changes):
    return dict(capture_id=str(at),point_id=str(at),observed_at=T+timedelta(seconds=at),
        available_at=T+timedelta(seconds=available if available is not None else at),state='available',
        value_text=value,same_timestamp_count=1,**changes)


def label(points,cutoff=120,level='200'):
    return labels(points,start=T,end=T+timedelta(seconds=120),cutoff=T+timedelta(seconds=cutoff),
                  gap_seconds=60,levels=[level])


def test_reached_negative_missing_and_censored_remain_distinct():
    reached=label([point(0),point(60,'300'),point(120)])
    negative=label([point(0),point(60),point(120)])
    missing=label([])
    censored=label([point(0),point(120)])
    immature=label([point(0),point(60)],cutoff=60)
    assert [x['labels'][0]['state'] for x in [reached,negative,missing,censored,immature]]==[
        'reached','not_reached','unknown','censored','censored']
    assert reached['labels'][0]['witness_capture_id']=='60'
    assert not censored['coverage']['complete_under_sampling_policy']


def test_late_evidence_never_becomes_an_entry_feature_or_old_label():
    late=point(30,'500',available=200)
    series={'points':[point(0),late],'query_complete':True}
    at=T+timedelta(seconds=60)
    assert latest_frame(series,at,60)['point']['capture_id']=='0'
    assert label([point(0),late,point(60),point(120)])['labels'][0]['state']=='not_reached'
    assert label([point(0),late,point(60),point(120)],cutoff=240)['labels'][0]['state']=='reached'


def test_counts_reconcile_and_missing_does_not_become_negative():
    outcomes=[label([point(0),point(60,'300'),point(120)]),
        label([point(0),point(60),point(120)]),label([]),label([point(0),point(120)])]
    members=[{'registry_id':i,'outcome':o,'entry':{'state':'unknown'},'holder':{'state':'unknown'}} for i,o in enumerate(outcomes)]
    s=summarize(members,['200']);r=s['levels'][0]
    assert r['N']==4 and r['resolved']==2
    assert r['observed_rate_among_resolved']=='0.5'
    assert r['population_lower_bound']=='0.25' and r['population_upper_bound']=='0.75'
    assert s['entry_unknown']==4


def test_invalid_latest_or_duplicate_quote_does_not_fall_back():
    invalid=point(60);invalid.update(state='unavailable',value_text=None)
    assert latest_frame({'points':[point(0),invalid],'query_complete':True},T+timedelta(seconds=60),60)['state']=='unknown'
    invalid=point(60);invalid['same_timestamp_count']=2
    assert latest_frame({'points':[point(0),invalid],'query_complete':True},T+timedelta(seconds=60),60)['state']=='unknown'


@pytest.mark.parametrize('change',[
    {'levels_usd':['200','100']},{'levels_usd':['0']},{'levels_usd':['NaN']},
    {'levels_usd':['1','1.0']},{'max_members':301},{'horizon_seconds':True},
    {'cohort_end':T-timedelta(seconds=1)},{'knowledge_cutoff':T-timedelta(seconds=1)}
])
def test_invalid_research_contract_rejected(change):
    kwargs=dict(source_kind='fixture',cohort_start=T,cohort_end=T+timedelta(hours=1),
        knowledge_cutoff=T+timedelta(hours=2),method='gmgn.market_rank.v4.market_cap',
        entry_delay_seconds=0,horizon_seconds=120,max_gap_seconds=60,levels_usd=['200'])
    with pytest.raises(ValueError):ResearchSpec(**{**kwargs,**change})

import copy
from datetime import timedelta
import pytest
from midas_data.studies import StudySpec,transitions,matched_controls,event_study,prefix_neighbors
from midas_data.experiments import compare
from test_experiments import member,definitions,bundle,T


def config(kind,**kw):
    defaults=dict(name=kind,kind=kind,cap_bins_usd=['200'],time_block_seconds=3600,level_usd='200')
    defaults.update(kw)
    return StudySpec(**defaults)


def with_cap(m,index,value):
    m['evidence']['outcome_series']['points'][index]['value_text']=value
    return m


def test_transitions_keep_reentries_out_of_first_episode_denominator_and_do_not_invent_dwell():
    r,_=definitions();m=with_cap(member(),1,'300')
    result=transitions([m],r,config('transitions'))
    assert result['N']==1
    assert result['first_episode']==[{'origin':'0','destination':'1','N':1,'origin_N':1,'fraction':'1'}]
    assert sum(x['N'] for x in result['adjacent_pairs'])==2
    assert result['same_state_seconds_bounds']=={}


def test_transition_gap_is_a_competing_loss_of_coverage_not_permanence():
    r,_=definitions();m=member()
    m['evidence']['outcome_series']['points'][1]['state']='unknown'
    result=transitions([m],r,config('transitions'))
    assert result['first_episode'][0]['destination']=='lost_coverage'
    assert not result['adjacent_pairs']
    assert result['unassigned_internal_seconds']=='120.0'


def test_matching_keeps_unknown_and_unmatched_and_does_not_use_labels():
    r,_=definitions();members=[member(i,600+i) for i in range(1,5)]
    for m,share in zip(members,['.1','.2','.8']):
        m['holder']={'state':'available','value_text':share}
    cfg=config('matched_controls',holder_exposure_max='.3')
    first=matched_controls(members,r,cfg)
    assert first['N']==4 and len(first['pairs'])==1
    assert first['unknown_ids']==[4] and first['unmatched_ids']==[2]
    assert first['pairs'][0]['exposed_id']==1 and first['pairs'][0]['control_id']==3
    members[2]['outcome']['labels'][0]['state']='reached'
    assert matched_controls(members,r,cfg)['pairs']==first['pairs']
    assert first['confidence_interval'] is None


def test_no_matching_overlap_cannot_produce_risk_difference():
    r,_=definitions();m=member();m['holder']={'state':'available','value_text':'.1'}
    report=matched_controls([m],r,config('matched_controls',holder_exposure_max='.3'))
    assert report['status']=='not_identifiable' and report['risk_difference_bounds'] is None
    assert report['exposed']['N']==0 and report['unmatched_ids']==[1]


def test_event_study_preserves_interval_anchor_control_pretrend_and_per_lag_coverage():
    r,_=definitions();exposed=with_cap(member(1),1,'200');control=member(2)
    report=event_study([exposed,control],r,config('event_study',pre_seconds=60,post_seconds=60,resolution_seconds=60))
    assert report['N']==2 and report['event_count']==1
    assert report['events'][0]['control_id']==2
    assert report['events'][0]['observed_interval'][0]<report['events'][0]['observed_interval'][1]
    assert [row['paired_N'] for row in report['lag_summary']]==[1,1,1]
    assert [row['paired_mean_difference_usd'] for row in report['lag_summary']]==['0','100','0']
    # A quote available only after the observation cutoff cannot create an event.
    exposed['evidence']['outcome_series']['points'][1]['available_at']=r.knowledge_cutoff+timedelta(seconds=1)
    assert event_study([exposed,control],r,config('event_study',pre_seconds=60,post_seconds=60,resolution_seconds=60))['event_count']==0


def test_event_missing_lag_stays_null_and_never_becomes_zero():
    r,_=definitions();m=with_cap(member(),1,'200')
    report=event_study([m],r,config('event_study',pre_seconds=120,post_seconds=60,resolution_seconds=60))
    row=report['lag_summary'][0]
    assert row['event']['N']==0 and row['event']['mean'] is None and row['paired_N']==0


def test_prefix_neighbors_are_independent_of_future_outcomes_and_exclude_future_training():
    r,e=definitions();a,b,q=member(1),member(2,660),member(3,7800)
    cfg=config('prefix_neighbors',prefix_seconds=60,prefix_steps=2,max_distance='.1')
    first=prefix_neighbors([a,b,member(4,15000)],[q],r,cfg,e.train_end)
    assert first['training_N']==2 and first['unavailable_training_ids']==[4]
    keys=lambda report:[(n['registry_id'],n['distance']) for n in report['queries'][0]['neighbors']]
    a['evidence']['outcome_series']['points'][2]['value_text']='900'
    a['evidence']['exit_series']['points'][0]['value_text']='900'
    a['outcome']['labels'][0]['state']='reached'
    second=prefix_neighbors([a,b,member(4,15000)],[q],r,cfg,e.train_end)
    assert keys(first)==keys(second)==[(1,'0'),(2,'0')]
    assert first['index_sha256']==second['index_sha256']


def test_prefix_gap_and_outside_support_are_explicit():
    r,e=definitions();a,q=member(),with_cap(member(3,7800),1,'500')
    cfg=config('prefix_neighbors',prefix_seconds=60,prefix_steps=2,max_distance='.1')
    assert prefix_neighbors([a],[q],r,cfg,e.train_end)['queries'][0]['status']=='outside_support'
    q['evidence']['outcome_series']['points'][1]['state']='unknown'
    assert prefix_neighbors([a],[q],r,cfg,e.train_end)['queries'][0]['status']=='insufficient_prefix'


def test_studies_are_versioned_in_the_same_comparison_and_respect_split_membership():
    b=bundle([member(1),member(2,7800),member(3,15000)])
    _,e=definitions(studies=[config('transitions')])
    report=compare(b,e)
    assert report['studies']['trials']==1
    result=report['studies']['outputs'][0]['result']
    assert all(result[name]['N']==1 for name in ['train','validation','test'])
    assert compare(b,e)==report


@pytest.mark.parametrize('kind,fields',[
    ('matched_controls',{}),('event_study',{}),('prefix_neighbors',{}),
    ('event_study',dict(pre_seconds=100,post_seconds=100,resolution_seconds=1)),
    ('transitions',dict(cap_bins_usd=['300','200']))
])
def test_studies_require_explicit_bounded_methods(kind,fields):
    with pytest.raises(ValueError):config(kind,**fields)

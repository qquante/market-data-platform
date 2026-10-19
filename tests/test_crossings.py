"""Grid boundaries, observed-segment reentries and immutable context references."""
from copy import deepcopy
from datetime import timedelta
from decimal import localcontext
import pytest

from midas_data.crossings import build_crossings, LEVELS, GRID_VERSION
from midas_data.trajectory import build_series, TrajectoryLimit
from test_trajectory import record, START, METHOD


def curve(values, seconds=None, *, start=START, gap=120):
    rows=[]
    for i,value in enumerate(values):
        r=record(invalid=value is None)
        r['received_at']=start+timedelta(seconds=seconds[i] if seconds else i*30)
        if value is not None:
            r['input_rows'][0]['value_text']='1'
            r['input_rows'][1]['value_text']=str(value)
        rows.append(r)
    return build_series(rows,source_kind='fixture',chain='sol',token='FixtureToken',method=METHOD,
        start=start,end=start+timedelta(hours=1),cutoff=start+timedelta(days=1),
        gap_after_seconds=gap,include_context=True)


def test_versioned_grid_has_exact_unique_boundaries_and_no_automatic_extension():
    assert len(LEVELS)==50 and sum(n<=10000000 for n in LEVELS)==41
    assert len(LEVELS)==len(set(LEVELS)) and LEVELS[0]==20000 and LEVELS[-1]==100000000
    c=build_crossings(curve([1,19999,20000,210000000]))['crossings']
    assert c['segments'][0]['initial_levels_at_or_above']==[]
    assert len(c['events'])==50 and c['events'][-1]['level_text']=='100000000'


@pytest.mark.parametrize('left,right,expected',[
    (90000,110000,[100000]),(100000,125000,[125000]),(180000,260000,[200000,250000]),
    (400000,1200000,[450000,500000,600000,700000,800000,900000,1000000]),
    (9000000,21000000,[10000000,20000000]),(90000000,210000000,[100000000]),
    (110000,90000,[100000]),(125000,100000,[125000]),(100000,100000,[]),
])
def test_threshold_membership_is_ge_with_exact_up_and_down_boundaries(left,right,expected):
    c=build_crossings(curve([left,right]))['crossings']
    assert [int(e['level_text']) for e in c['events']]==expected
    assert all(e['direction']==('up' if right>left else 'down') for e in c['events'])


def test_equal_samples_do_not_duplicate_events_and_observed_reentry_is_local():
    s=curve([19000,20000,20000,19999,20000])
    result=build_crossings(s);c=result['crossings']
    assert [e['kind'] for e in c['events']]==['upcross','downcross','reentry']
    assert len({e['event_id'] for e in c['events']})==3
    assert build_crossings(s)==result
    starts_above=build_crossings(curve([20000,19999,20000]))['crossings']
    assert starts_above['segments'][0]['initial_levels_at_or_above']==['20000']
    assert [e['kind'] for e in starts_above['events']]==['downcross','reentry']


def test_decimal_edges_do_not_round_to_a_crossing():
    with localcontext() as ctx:
        ctx.prec=2
        below=build_crossings(curve(['19999.9999999999999999999999999991',
                                    '19999.9999999999999999999999999992']))['crossings']
        crossing=build_crossings(curve(['19999.9999999999999999999999999992','20000']))['crossings']
    assert below['events']==[] and len(crossing['events'])==1


@pytest.mark.parametrize('values,seconds',[
    ([19000,21000,19000,21000],[0,30,600,630]),
    ([19000,21000,None,19000,21000],[0,30,60,90,120]),
])
def test_gap_or_invalid_snapshot_cannot_create_a_crossing_or_keep_reentry_state(values,seconds):
    c=build_crossings(curve(values,seconds))['crossings']
    assert [e['kind'] for e in c['events']]==['upcross','upcross']
    assert len(c['segments'])==2 and c['events'][0]['segment_id']!=c['events'][1]['segment_id']


def test_tied_timestamps_are_not_ordered_by_capture_id_and_empty_has_no_zero_events():
    c=build_crossings(curve([19000,21000,18000,22000],[0,30,30,60]))['crossings']
    assert c['events']==[] and len(c['segments'])==2
    assert build_crossings(curve([]))['crossings']['events']==[]
    assert build_crossings(curve([None,None]))['crossings']['segments']==[]


def test_midnight_does_not_reset_segment_but_new_query_does_not_claim_old_history():
    start=START.replace(hour=23,minute=59,second=0)
    s=curve([39000,41000,39000,42000],start=start)
    c=build_crossings(s)['crossings']
    assert [e['kind'] for e in c['events']]==['upcross','downcross','reentry']
    assert len(c['segments'])==1
    assert c['events'][1]['interval_start'].date()!=c['events'][1]['interval_end'].date()
    window=curve([39000,42000],start=start+timedelta(seconds=60))
    assert build_crossings(window)['crossings']['events'][0]['kind']=='upcross'


def test_skipped_levels_share_two_real_contexts_and_supply_change_is_not_price_change():
    s=curve([180000,260000]);before=deepcopy(s)
    result=build_crossings(s);c=result['crossings'];events=c['events']
    assert s==before and all('valuation_context' not in p for p in result['points'])
    assert len(c['context_snapshots'])==2
    assert len({(e['left_point_id'],e['right_point_id']) for e in events})==1
    assert all(e['exact_crossing_at'] is None for e in events)
    assert events[0]['context_changes']=={'price_changed':False,'circulating_supply_changed':True}
    for snap in c['context_snapshots']:
        p=next(p for p in s['points'] if p['point_id']==snap['point_id'])
        assert snap['context']==p['valuation_context'] and snap['raw_sha256']==p['raw_sha256']
        assert snap['context']['economic_inputs']['liquidity']['state']=='unavailable'


def test_ids_change_with_interpretation_and_knowledge_covers_reentry_prefix():
    s=curve([21000,19000,22000]);s['points'][0]['available_at']=START+timedelta(hours=3)
    c=build_crossings(s)['crossings'];event=c['events'][-1]
    assert event['kind']=='reentry' and event['required_knowledge_at']==s['points'][0]['available_at']
    s['points'][0]['interpretation_sha256']='c'*64
    assert build_crossings(s)['crossings']['events'][-1]['event_id']!=event['event_id']


def test_budgets_fail_without_partial_events_and_grid_requires_approved_version():
    with pytest.raises(TrajectoryLimit):
        build_crossings(curve([1,100000000]),max_events=49)
    for changes in ({'grid_version':'future-grid'},{'max_events':0},{'max_events':2001}):
        with pytest.raises(ValueError):
            build_crossings(curve([19000,21000]),**changes)
    s=curve([]);s['query_complete']=False
    with pytest.raises(ValueError):
        build_crossings(s)

def test_context_timestamp_matches_individual_valuation_canonical_precision():
    import json
    from datetime import datetime
    from fastapi.encoders import jsonable_encoder
    from midas_data.trajectory import build_point
    from midas_data.valuation import build_valuation
    r=record()
    for f in r['input_rows']:
        f['available_at']='2026-09-09T20:41:25.91247+00:00'
    native=[dict(f,available_at=datetime.fromisoformat(f['available_at'])) for f in r['input_rows']]
    expected=build_valuation(native[0],'token_info','/data',native)
    r['input_rows']=json.dumps(r['input_rows'],default=str)
    actual=build_point(r,'token_info',METHOD,include_context=True)['valuation_context']
    assert jsonable_encoder(actual['price'])==jsonable_encoder(expected['price'])
    assert jsonable_encoder(actual['economic_inputs'])==jsonable_encoder(expected['economic_inputs']['fields'])
    assert jsonable_encoder(actual['price'])['available_at']=='2026-09-09T20:41:25.912470+00:00'

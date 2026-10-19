"""Daily summaries use observed points, UTC boundaries and exact comparisons."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import localcontext
import pytest

from midas_data.daily_trajectory import build_daily, day_bounds, DAILY_VERSION
from midas_data.trajectory import build_series
from test_trajectory import record, START, METHOD

DAY=START.date()


def sample(seconds, value='1', invalid=False):
    r=record(seconds,invalid=invalid)
    if not invalid:
        r['input_rows'][0]['value_text']='1'
        r['input_rows'][1]['value_text']=value
    return r


def daily(records, cutoff=None, day=DAY):
    cutoff=cutoff or START+timedelta(days=1)
    start,end,_=day_bounds(day,cutoff)
    series=build_series(records,source_kind='fixture',chain='sol',token='FixtureToken',method=METHOD,
        start=start,end=end,cutoff=cutoff,gap_after_seconds=600)
    return build_daily(series,day)


@pytest.mark.parametrize('day,expected_end',[(date(2024,2,29),date(2024,3,1)),
    (date(2025,12,31),date(2026,1,1))])
def test_day_is_utc_calendar_including_leap_year_and_year_boundary(day,expected_end):
    cutoff=datetime.combine(expected_end,datetime.min.time(),tzinfo=timezone.utc)
    start,end,next_day=day_bounds(day,cutoff)
    assert start.hour==end.hour==0 and end.date()==expected_end and end==next_day
    assert (end-start).total_seconds()==86400


def test_cutoff_offset_is_converted_before_clipping_current_day():
    cutoff=datetime.fromisoformat('2026-09-09T21:30:00-03:00')
    start,end,next_day=day_bounds(date(2026,9,10),cutoff)
    assert start.isoformat()=='2026-09-10T00:00:00+00:00'
    assert end.isoformat()=='2026-09-10T00:30:00+00:00'
    assert next_day.isoformat()=='2026-09-11T00:00:00+00:00'


@pytest.mark.parametrize('day,cutoff',[(DAY,START.replace(tzinfo=None)),
    (date(9999,12,31),START),(DAY,START.replace(hour=0)),
    (DAY,START-timedelta(days=1))])
def test_invalid_or_not_started_days_reject_explicitly(day,cutoff):
    with pytest.raises(ValueError):
        day_bounds(day,cutoff)


def test_extremes_numeric_exact_ties_reference_all_points_and_first_last_are_observations():
    with localcontext() as context:
        context.prec=2
        result=daily([sample(30,'10.0000000000000000000000000002'),
                      sample(0,'9'),sample(60,'10.0000000000000000000000000002'),
                      sample(90,'10.0000000000000000000000000001')])
    d=result['daily'];points=result['points']
    assert d['version']==DAILY_VERSION and d['calendar_state']=='ended'
    assert d['first_observation']['value_text']=='9'
    assert d['last_observation']['point_id']==points[-1]['point_id']
    assert d['minimum_observed']['point_id']==points[0]['point_id']
    assert d['maximum_observed']['matching_point_ids']==[points[1]['point_id'],points[2]['point_id']]
    assert d['maximum_observed']['value_text']=='10.0000000000000000000000000002'
    assert d['coverage']['leading_seconds']==43200
    assert d['coverage']['trailing_seconds']==43110
    assert d['coverage']['flagged_gap_seconds']==86310
    assert d['coverage']['largest_sample_interval_seconds']==43200


def test_tied_time_different_values_has_no_invented_open_or_close_but_max_is_known():
    d=daily([sample(0,'1'),sample(0,'2')])['daily']
    assert d['first_observation']['reason']==d['last_observation']['reason']=='ambiguous_observation_order'
    assert d['first_observation']['value_text'] is None
    assert d['maximum_observed']['value_text']=='2'
    same=daily([sample(0,'1.00'),sample(0,'1')])['daily']['first_observation']
    assert same['state']=='available' and len(same['matching_point_ids'])==2


def test_empty_or_invalid_day_is_missing_while_actual_zero_is_preserved():
    for records in ([],[sample(0,invalid=True)]):
        d=daily(records)['daily']
        assert d['state']=='unavailable' and d['maximum_observed']['value_text'] is None
        assert d['coverage']['flagged_gap_seconds']==86400
        assert d['coverage']['leading_seconds'] is None
    zero=daily([sample(0,'0')])['daily']
    assert zero['state']=='available' and zero['maximum_observed']['value_text']=='0'


def test_open_day_future_is_not_a_gap_and_last_valid_is_not_carried_to_cutoff():
    cutoff=START+timedelta(minutes=15)
    r=sample(0);r['revision_count']=2;r['previous_normalizer_version']='older'
    result=daily([r,sample(300,invalid=True)],cutoff=cutoff)
    d=result['daily']
    assert d['calendar_state']=='in_progress' and d['has_known_revisions']
    assert d['through_at']==cutoff and d['coverage']['not_elapsed_seconds']==42300
    assert d['coverage']['query_seconds']==44100 and d['coverage']['trailing_seconds']==900
    assert d['coverage']['flagged_gap_seconds']==44100
    assert d['last_observation']['observed_at']==START
    assert result['points'][-1]['state']=='unavailable'


@pytest.mark.parametrize('change',[{'query_complete':False},{'start':START},{'end':START}])
def test_summary_cannot_label_partial_or_truncated_query_as_the_whole_day(change):
    r=deepcopy(daily([sample(0)]));r.pop('daily');r.update(change)
    with pytest.raises(ValueError):
        build_daily(r,DAY)

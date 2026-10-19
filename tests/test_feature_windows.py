"""Deadlines and canonical subscription identities; no provider calls."""
from datetime import timedelta
import pytest

from midas_data.feature_windows import configuration, next_due
from test_features import window
from test_trajectory import METHOD


def test_canonical_registration_unifies_consumers():
    a=configuration('fixture','BASE','0x'+'A'*40,METHOD,900)
    b=configuration('fixture','base','0x'+'a'*40,METHOD,900,720)
    assert a==b


@pytest.mark.parametrize('source,method,duration,gap', [
    ('other',METHOD,60,120),('fixture','invalid',60,120),
    ('fixture',METHOD,0,120),('fixture',METHOD,86401,120),
    ('fixture',METHOD,60,0),('fixture',METHOD,60,86401)])
def test_invalid_window_definitions_are_rejected(source,method,duration,gap):
    with pytest.raises(ValueError):
        configuration(source,'base','0x'+'a'*40,method,duration,gap)


def test_deadline_tracks_staleness_left_boundary_and_clock_limit():
    s=window(('100','120'),times=[0,60],end=100,gap=60)
    # First sample leaves [start,end) just after +duration.
    assert next_due(s)==s['end']+timedelta(microseconds=1)
    s['start']-=timedelta(seconds=200)
    assert next_due(s)==s['points'][-1]['observed_at']+timedelta(seconds=60,microseconds=1)
    assert next_due(window((),times=[]))==window((),times=[])['end']+timedelta(seconds=60)


def test_deadline_can_resolve_a_leading_gap_without_a_new_point():
    s=window(('100','120'),times=[60,90],end=100,gap=80)
    s['start']-=timedelta(seconds=30)
    # Start reaches first observation minus tolerated gap after ten seconds.
    assert next_due(s)==s['end']+timedelta(seconds=10)

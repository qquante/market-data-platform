"""Window feature semantics and bounded reuse; no database or provider calls."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext

import pytest

from midas_data.features import SharedFeatures, _percent
from midas_data.trajectory import TrajectoryLimit
from test_trajectory import record, series


def window(values=('100', '150', '120'), *, times=None, end=180, gap=120):
    rows = []
    for i, value in enumerate(values):
        r = record((times or [i*60 for i in range(len(values))])[i], invalid=value is None)
        for field in r['input_rows']:
            if field['metric'] == 'price':
                field['value_text'] = '1'
            if field['metric'] == 'circulating_supply':
                field['value_text'] = value
        rows.append(r)
    return series(rows, end=end, gap=gap)


def frame(cache, source):
    result, reuse = cache.build(source)
    return result['features'], reuse


def test_exact_extrema_latest_and_sampled_percentage():
    source = window()
    f, reuse = frame(SharedFeatures(), source)
    values = f['features']
    assert values['market_cap_latest']['value_text'] == '120'
    assert values['market_cap_min_observed']['value_text'] == '100'
    assert values['market_cap_max_observed']['value_text'] == '150'
    assert Decimal(values['market_cap_sample_change_pct']['value_text']) == 20
    assert values['market_cap_sample_change_pct']['observed_span_seconds'] == 120
    assert f['required_knowledge_at'] == max(p['available_at'] for p in source['points'])
    assert reuse == dict(reused=0, computed=2)


def test_consumers_share_calculations_and_cannot_mutate_the_cache():
    source, cache = window(), SharedFeatures()
    original, _ = frame(cache, source)
    for _ in range(20):
        same, reuse = frame(cache, source)
        assert same == original and reuse == dict(reused=2, computed=0)
        same['features']['market_cap_latest']['value_text'] = 'corrupt'
    assert cache.computations == 2 and cache.reuses == 40
    serialized_ids = deepcopy(source)
    for point in serialized_ids['points']:
        point['input_fact_ids'] = [str(value) for value in point['input_fact_ids']]
    assert frame(cache, serialized_ids) == (original, dict(reused=2, computed=0))
    assert frame(SharedFeatures(), source)[0] == original  # cold reconstruction has the same ID.


def test_new_cutoff_reuses_but_revision_only_invalidates_affected_node():
    source, cache = window(), SharedFeatures()
    original, _ = frame(cache, source)
    later = deepcopy(source)
    later['knowledge_cutoff'] += timedelta(minutes=1)
    assert frame(cache, later) == (original, dict(reused=2, computed=0))
    revised = deepcopy(later)
    revised['points'][0].update(value_text='80', normalizer_version='fixture-revision',
        interpretation_sha256='c'*64, available_at=source['knowledge_cutoff'])
    changed, reuse = frame(cache, revised)
    assert reuse == dict(reused=1, computed=1)
    assert changed['frame_id'] != original['frame_id']
    assert changed['features']['market_cap_latest'] == original['features']['market_cap_latest']
    assert changed['features']['market_cap_min_observed']['value_text'] == '80'
    assert frame(cache, source)[0] == original


@pytest.mark.parametrize('field,value', [('source_kind','gmgn'), ('token_address','DifferentToken'),
    ('method','different.version'), ('chain','base'), ('unit','other'), ('version','trajectory.v2')])
def test_semantic_identities_never_share_nodes(field, value):
    source, cache = window(), SharedFeatures()
    original, _ = frame(cache, source)
    changed = deepcopy(source)
    changed[field] = value
    other, reuse = frame(cache, changed)
    assert other['frame_id'] != original['frame_id'] and reuse['computed'] == 2


def test_clock_expiry_and_window_membership_change_without_a_new_tick():
    source, cache = window(), SharedFeatures()
    original, _ = frame(cache, source)
    expired = deepcopy(source)
    expired['end'] += timedelta(minutes=3)
    # Rebuild the normal trajectory gap metadata, exactly as a fresh read does.
    expired['gaps'] = [dict(reason='trailing_gap')]
    stale, reuse = frame(cache, expired)
    assert stale['frame_id'] != original['frame_id'] and reuse['computed'] == 2
    assert stale['features']['market_cap_latest']['reason'] == 'stale_at_window_end'
    assert stale['features']['market_cap_sample_change_pct']['reason'] == 'window_has_gaps'
    moved = deepcopy(source)
    moved['start'] += timedelta(seconds=60)
    moved['points'] = moved['points'][1:]
    moved['coverage'].update(observed_points=2, eligible_points=2)
    narrower, _ = frame(cache, moved)
    assert narrower['features']['market_cap_min_observed']['value_text'] == '120'
    assert Decimal(narrower['features']['market_cap_sample_change_pct']['value_text']) == -20


@pytest.mark.parametrize('source,latest_reason,change_reason', [
    (lambda: window((), times=[]), 'no_observations', 'insufficient_observations'),
    (lambda: window(('100',)), 'stale_at_window_end', 'insufficient_observations'),
    (lambda: window(('100',None)), 'latest_observation_unavailable', 'insufficient_observations'),
    (lambda: window(('100',None,'120')), None, 'unavailable_observation_in_window'),
    (lambda: window(('100','120'),times=[0,0]), 'ambiguous_latest_observation', 'ambiguous_observation_order'),
    (lambda: window(('100','120'),times=[0,600],end=650), None, 'window_has_gaps'),
    (lambda: window(('0','120'),end=100), None, 'zero_baseline'),
])
def test_missing_ties_gaps_and_zero_never_become_usable_returns(source, latest_reason, change_reason):
    f, _ = frame(SharedFeatures(), source())
    latest, change = f['features']['market_cap_latest'], f['features']['market_cap_sample_change_pct']
    assert latest.get('reason') == latest_reason
    assert change['reason'] == change_reason and change['value_text'] is None
    if latest_reason:
        assert latest['state'] == 'unavailable' and latest['value_text'] is None


def test_observed_extrema_remain_auditable_across_gaps_and_equal_values():
    source = window(('100','100','120'),times=[0,60,600],end=650)
    f, _ = frame(SharedFeatures(), source)
    minimum = f['features']['market_cap_min_observed']
    assert minimum['value_text'] == '100'
    assert minimum['input_point_ids'] == [p['point_id'] for p in source['points'][:2]]
    assert f['quality']['flagged_gap_count'] > 0


def test_percentage_has_its_own_precision_rounding_and_traps():
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.rounding = ROUND_DOWN
        ctx.traps[Inexact] = True
        assert _percent('3','4') == '33.33333333333333333333333333333333'
        assert Decimal(_percent('1'+'0'*80, '1'+'0'*79+'1')) == Decimal('1e-78')
        assert Decimal(_percent('1e-2000','1e2000')) == Decimal('1e4002')


def test_cache_eviction_and_uncacheable_values_keep_results_correct():
    source = window()
    cache = SharedFeatures(max_entries=2)
    expected, _ = frame(cache, source)
    other = deepcopy(source)
    other['token_address'] = 'Other'
    frame(cache, other)
    assert len(cache.entries) == 2
    assert frame(cache, source) == (expected, dict(reused=0, computed=2))
    tiny = SharedFeatures(max_bytes=1)
    assert frame(tiny, source)[0] == expected
    assert frame(tiny, source)[1]['computed'] == 2
    assert tiny.bytes == 0 and not tiny.entries
    bounded = SharedFeatures(max_bytes=1500)
    for i in range(10):
        other['token_address'] = str(i)
        assert frame(bounded, other)[0]['features'] == expected['features']
        assert bounded.bytes <= 1500 and len(bounded.entries) <= 128


def test_partial_or_excessive_inputs_are_rejected():
    source = window()
    source['query_complete'] = False
    with pytest.raises(TrajectoryLimit):
        frame(SharedFeatures(), source)
    source['query_complete'] = True
    source['points'] *= 667
    with pytest.raises(TrajectoryLimit):
        frame(SharedFeatures(), source)

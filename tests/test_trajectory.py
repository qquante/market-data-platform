"""Temporal boundaries and honest continuity without provider calls."""
from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4

import pytest
from midas_data.trajectory import build_point, build_series, item_pattern, METHODS
from test_valuation import inputs

START=datetime(2026,9,9,12,tzinfo=timezone.utc)
METHOD=next(iter(METHODS))


def record(seconds=0, *, invalid=False):
    anchor,base,rows,_=inputs(price='0.12345678901234567890123456789',supply='1000000000')
    return dict(capture_id=anchor['capture_id'],received_at=START+timedelta(seconds=seconds),
        recorded_at=START+timedelta(hours=1),available_at=START+timedelta(hours=1,seconds=1),
        normalizer_version=anchor['normalizer_version'],revision_count=1,previous_normalizer_version=None,
        content_sha256='a'*64,raw_sha256='b'*64,evidence_kind='original_response',input_rows=[] if invalid else rows)


def series(rows, end=1800, gap=600):
    return build_series(rows,source_kind='fixture',chain='sol',token='FixtureToken',method=METHOD,
        start=START,end=START+timedelta(seconds=end),cutoff=START+timedelta(days=1),gap_after_seconds=gap)


@pytest.mark.parametrize('endpoint,base',[
    ('token_info',''),('token_info','/data'),
    ('market_rank','/0'),('market_rank','/data/0'),('market_rank','/rank/0'),
    ('market_rank','/data/rank/0'),('market_rank','/data/data/rank/0'),
    ('trenches','/new_creation/0'),('trenches','/data/completed/9'),
])
def test_approved_item_shapes_and_no_subtree_wildcards(endpoint,base):
    pattern=re.compile(item_pattern(endpoint))
    assert pattern.fullmatch(base+'/price')[1]==base
    assert not pattern.fullmatch(base+'/pool/price')


def test_exact_cap_and_provenance_no_ambient_decimal_rounding():
    from decimal import localcontext
    r=record()
    with localcontext() as context:
        context.prec=2
        p=build_point(r,'token_info',METHOD)
    assert p['state']=='available' and p['value_text']=='123456789.01234567890123456789000000000'
    assert p['raw_sha256']==r['raw_sha256']
    assert p['input_fact_ids']==[f['fact_id'] for f in r['input_rows']]
    assert p['observed_at']==START and p['available_at']>p['observed_at']


def test_missing_revision_and_ambiguous_items_do_not_reuse_or_choose_values():
    assert build_point(record(invalid=True),'token_info',METHOD)['reason']=='no_current_token_facts'
    r=record();r['input_rows'][1]['contract_version']='unreviewed.v99'
    assert build_point(r,'token_info',METHOD)['state']=='unavailable'
    anchor,base,rows,row=inputs('market_rank')
    rows=[row('market_cap','market_cap','1','usd'),
          dict(row('market_cap','market_cap','2','usd'),pointer='/data/rank/1/market_cap')]
    r['input_rows']=rows
    assert build_point(r,'market_rank','gmgn.market_rank.v4.market_cap')['reason']=='ambiguous_token_items'
    r['input_rows']=rows*8
    assert build_point(r,'market_rank','gmgn.market_rank.v4.market_cap')['reason']=='snapshot_input_budget_exceeded'


def test_gaps_invalid_points_and_tied_timestamps_break_continuity():
    r=series([record(1600),record(600),record(650,invalid=True),record(700),record(700)])
    assert [p['observed_at'] for p in r['points']]==sorted(p['observed_at'] for p in r['points'])
    assert r['points'][1]['break_before']=='unavailable_observation'
    assert all(p['break_before']=='ambiguous_observation_order' for p in r['points'][2:])
    assert r['coverage']['ambiguous_timestamp_points']==2
    assert [(g['reason'],g['duration_seconds']) for g in r['gaps']]==[('observation_gap',900)]
    assert r['coverage']['interpolation']=='none'


def test_empty_leading_trailing_gaps_and_invalid_followup():
    assert series([])['gaps'][0]['reason']=='no_eligible_observations'
    assert series([record(invalid=True)])['gaps'][0]['duration_seconds']==1800
    r=series([record(700),record(720,invalid=True),record(740)],end=1800)
    assert [g['reason'] for g in r['gaps']]==['leading_gap','trailing_gap']
    assert r['points'][2]['break_before']=='after_unavailable_observation'
    assert series([record(0),record(601)],end=602)['points'][1]['break_before']=='observation_gap'

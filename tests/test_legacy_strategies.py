"""Regression evidence for exact legacy imports and their read-only API boundary."""
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import httpx
import pytest
from midas_data import legacy_strategies as legacy
from midas_data.api import make_app
from midas_data.settings import Settings


def source(key):
    return json.loads(Path(legacy.__file__).with_name('strategy_sources').joinpath(key+'.json').read_text(encoding='utf-8-sig'))


def test_selected_sources_have_all_rows_exact_calibration_and_partial_fractions():
    data=source('selected_partials')
    rows={r['definition']['pair_id']+'|'+str(r['definition']['horizon_seconds'])+'|'+str(r['definition']['target_age_seconds']):r
          for r in legacy.catalog()['items'] if r['source_id']=='selected_partials'}
    assert len(rows)==277
    for values in data['rows']:
        original=dict(zip(data['columns'],values,strict=True))
        h,age,rule=original['entry_key_horizon_age_rule'].split('|')
        imported=rows[original['pair_id']+'|'+h+'|'+age]
        entry=dict(zip(data['entry_columns'],data['entries'][original['entry_key_horizon_age_rule']],strict=True))
        entry['conditions']=[dict(zip(data['condition_columns'],c,strict=True)) for c in entry['conditions']]
        assert imported['definition']['entry_rule']==entry
        assert imported['definition']['exit_policy']==data['exits'][original['exit_policy_id']]
        assert imported['definition']['run_id']==data['run']
        assert imported['compatibility']['calibration']=='complete'
    assert Counter(r['definition']['exit_policy']['targets_gain_percent'][0] for r in rows.values())=={
        66:25,75:19,100:34,125:27,150:74,200:35,300:63}


def test_same_pair_different_runs_never_share_calibration_or_become_executable():
    rows=[r for r in legacy.catalog()['items'] if r['definition']['pair_id']=='cdbe2fe3683c0034ee14']
    old=next(r for r in rows if r['source_id']=='fast_partials')
    newer=next(r for r in rows if r['source_id']=='partial70_shadow')
    assert old['strategy_id']!=newer['strategy_id']
    assert old['definition']['entry_rule']['score_cutoff']==0.18589948667035508
    assert newer['definition']['entry_rule']['score_cutoff']==0.15469285070471994
    assert old['compatibility']['calibration']=='missing'
    assert all('mean' not in c and 'std' not in c and 'transform' not in c for c in old['definition']['entry_rule']['conditions'])
    for r in legacy.catalog()['items']:
        assert r['runtime_status']=='catalog_only' and r['compatibility']['state']=='blocked'
        assert r['strategy_validated_in_current_base'] is False
    assert Counter(r['source_id'] for r in legacy.catalog()['items'])=={
        'selected_partials':277,'fast_partials':41,'partial70_shadow':1}


def test_frozen_profile_fingerprint_rejects_changed_cutoff():
    profile=source('partial70_shadow');profile['strategy']['entry_rule']['score_cutoff']+=.01
    with pytest.raises(ValueError,match='legacy_profile_identity_changed'):
        list(legacy.import_profile(profile,{}))


def test_source_integrity_failure_is_not_silently_imported(monkeypatch,tmp_path):
    folder=tmp_path/'strategy_sources';folder.mkdir()
    root=Path(legacy.__file__).with_name('strategy_sources')
    for p in root.glob('*.json'):(folder/p.name).write_bytes(p.read_bytes())
    (folder/'selected_partials.json').write_bytes(b'{}')
    legacy.catalog.cache_clear();monkeypatch.setattr(legacy,'files',lambda _:tmp_path)
    try:
        with pytest.raises(ValueError,match='legacy_source_hash_mismatch'):legacy.catalog()
    finally:legacy.catalog.cache_clear()


def test_pagination_filters_export_and_details_do_not_change_snapshot():
    pages=[legacy.listing(offset=i,limit=20) for i in range(0,320,20)]
    ids=[r['strategy_id'] for p in pages for r in p['items']]
    assert len(ids)==len(set(ids))==319
    filtered=legacy.listing(source='selected_partials',first_target=66)
    assert filtered['total']==25 and filtered['has_more']
    assert legacy.listing(query='NONEXISTENT')['total']==0
    assert legacy.listing(query='bundler_rate')['total']>0
    before=legacy.export();r=legacy.detail(ids[0]);r['definition']['entry_rule']['score_cutoff']=999
    assert legacy.export()==before
    assert hashlib.sha256(before[0]).hexdigest()==before[1]
    assert json.loads(before[0])['total']==319
    assert legacy.export('absent') is None


def test_legacy_api_auth_bounds_exports_and_rejects_post_without_database():
    async def run():
        settings=Settings(database_dsn='fixture',lake_catalog_dsn='fixture',api_key='legacy-fixture',
            nats_url='fixture',clickhouse_url='fixture',clickhouse_user='fixture',clickhouse_password='fixture',
            raw_path=Path('/tmp/unused-fixture'),lake_path='/tmp/unused-fixture')
        app=make_app(settings)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://fixture') as client:
            for route in ['/v1/research/strategies','/v1/research/strategies/export','/v1/research/strategies/absent']:
                assert (await client.get(route)).status_code==401
            client.headers['Authorization']='Bearer '+settings.api_key
            r=await client.get('/v1/research/strategies');assert r.status_code==200 and r.json()['catalog_total']==319
            sid=r.json()['items'][0]['strategy_id']
            assert (await client.get('/v1/research/strategies?limit=101')).status_code==422
            assert (await client.get('/v1/research/strategies?offset=-1')).status_code==422
            assert (await client.get('/v1/research/strategies/absent')).status_code==404
            r=await client.get('/v1/research/strategies/'+sid);assert r.json()['compatibility']['state']=='blocked'
            for route in ['/v1/research/strategies/export','/v1/research/strategies/'+sid+'/export']:
                r=await client.get(route);assert r.status_code==200
                assert hashlib.sha256(r.content).hexdigest()==r.headers['x-content-sha256']
            assert (await client.post('/v1/research/strategies',json={})).status_code==405
    asyncio.run(run())

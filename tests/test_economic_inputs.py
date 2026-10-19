"""Trajectory input contracts exercised through real v4 normalization."""
import json
from uuid import uuid4

import pytest

from midas_data.contracts import CaptureMetadata
from midas_data.normalize import normalize_capture
from midas_data.valuation import build_valuation


def valuation(endpoint, data, params=None):
    meta=CaptureMetadata(capture_id=uuid4(),source_kind='fixture',endpoint=endpoint,chain='sol',
        requested_at='2026-09-10T12:00:00Z',received_at='2026-09-10T12:00:01Z',
        http_status=200,evidence_kind='original_response',request_params=params or {})
    result=normalize_capture(meta,json.dumps({'data':data}).encode())
    # Publication attaches these two fields; Fact itself is the pure normalized payload.
    rows=[dict(f.model_dump(),normalizer_version=result.normalizer_version,
               available_at='2026-09-10T12:00:02Z') for f in result.facts]
    anchor=next(r for r in rows if r['metric']=='price')
    pointer=anchor['pointer'].removesuffix('/price/price' if endpoint=='token_info' else '/price')
    return build_valuation(anchor,endpoint,pointer,rows)


def token(**changes):
    return dict(address='FixtureToken',price={'price':'0.1234567890123456789','volume_5m':'100.00100'},
                circulating_supply='1000000000',total_supply='9000000000',max_supply='9900000000',
                decimals=9,liquidity='123456.7890123456789',**changes)


def test_token_info_exposes_exact_units_scope_and_only_supported_operands():
    result=valuation('token_info',token())
    context=result['economic_inputs'];fields=context['fields']
    assert context['version']=='gmgn-trajectory-inputs-v1'
    assert fields['liquidity']['value_text']=='123456.7890123456789'
    assert fields['volume_5m']['value_text']=='100.00100'
    for name in ('circulating_supply','liquidity','volume_5m','decimals'):
        assert fields[name]['state']=='available' and fields[name]['contract_supported']
        assert fields[name]['fact_id'] and fields[name]['pointer'].startswith('/data/')
    for name in ('total_supply','max_supply'):
        assert fields[name]['state']=='unavailable' and not fields[name]['contract_supported']
        assert fields[name]['reason']=='input_unverified'
    assert result['market_cap']['value_text']=='123456789.0123456789000000000'
    assert 'never rescale' in fields['decimals']['scope']
    assert 'never sum' in fields['volume_5m']['scope']


@pytest.mark.parametrize('params,available',[
    ({'query':{'interval':'5m'}},True),
    ({'interval':'5m'},True),
    ({'query':{'interval':'1h'}},False),
    ({'interval':'5m','query':{'interval':'1h'}},False),
    ({},False),
])
def test_ranking_volume_only_uses_a_confirmed_five_minute_window(params,available):
    result=valuation('market_rank',{'rank':[{'address':'FixtureToken','price':'1','market_cap':'10',
        'liquidity':'200','volume':'12.3400'}]},params)
    fields=result['economic_inputs']['fields']
    assert (fields['volume_5m']['state']=='available') is available
    assert fields['liquidity']['state']=='unavailable'
    assert not fields['liquidity']['contract_supported']
    assert result['market_cap']['state']=='available' and result['market_cap']['value_text']=='10'


def test_trenches_aliases_remain_unavailable_even_when_numeric_values_match():
    result=valuation('trenches',{'new_creation':[{'address':'FixtureToken','price':'1',
        'market_cap':'10','total_supply':'10','liquidity':'20','volume_5m':'30'}]})
    assert result['price']['state']==result['market_cap']['state']=='unavailable'
    assert all(f['state']=='unavailable' for f in result['economic_inputs']['fields'].values())


@pytest.mark.parametrize('field',['liquidity','volume_5m'])
@pytest.mark.parametrize('value',[None,'-1','1e1001'])
def test_bad_optional_context_does_not_invalidate_good_core_valuation(field,value):
    data=token()
    if field=='volume_5m':data['price'][field]=value
    else:data[field]=value
    result=valuation('token_info',data)
    assert result['economic_inputs']['fields'][field]['state']=='unavailable'
    assert result['market_cap']['state']=='available'


def test_missing_optional_context_is_not_zero_and_observed_zero_is_allowed():
    data={'address':'FixtureToken','price':{'price':'1'},'circulating_supply':'10'}
    missing=valuation('token_info',data)
    assert missing['economic_inputs']['fields']['liquidity']['state']=='unavailable'
    data.update(liquidity='0');data['price']['volume_5m']='0'
    zero=valuation('token_info',data)['economic_inputs']['fields']
    assert zero['liquidity']['state']==zero['volume_5m']['state']=='available'
    assert zero['liquidity']['value_text']==zero['volume_5m']['value_text']=='0'

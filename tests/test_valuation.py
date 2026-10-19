"""Same-response valuation, explicit economic basis and exact arithmetic."""
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from midas_data.valuation import build_valuation


def inputs(endpoint='token_info', price='2.5000', supply='100'):
    contract={'token_info':'gmgn.token_info.v3','market_rank':'gmgn.market_rank.v4','trenches':'gmgn.trenches.v1'}[endpoint]
    base='/data' if endpoint=='token_info' else '/data/rank/0' if endpoint=='market_rank' else '/data/new_creation/0'
    anchor=dict(capture_id=uuid4(),normalizer_version='gmgn-p0-v4',source_kind='fixture',evidence_kind='original_response',
                chain='sol',token_address='FixtureToken',occurred_at='2026-09-09T12:00:00Z',time_basis='received')
    def row(metric,path,text,unit,quality='valid'):
        return dict(anchor,fact_id=uuid4(),pointer=base+'/'+path,metric=metric,value_text=text,
                    value_type='decimal',unit=unit,quality=quality,contract_version=contract,
                    available_at='2026-09-09T12:00:01Z')
    rows=[row('price','price/price' if endpoint=='token_info' else 'price',price,'usd_per_token'),
          row('circulating_supply','circulating_supply',supply,'token')]
    return anchor,base,rows,row


def test_exact_product_ignores_ambient_decimal_precision_and_records_inputs():
    anchor,base,rows,_=inputs(price='0.12345678901234567890123456789',supply='1000000000')
    with localcontext() as context:
        context.prec=2
        result=build_valuation(anchor,'token_info',base,rows)
    cap=result['market_cap']
    assert Decimal(cap['value_text'])==Decimal('123456789.01234567890123456789')
    assert cap['kind']=='derived' and cap['metric']=='circulating_market_cap_estimate'
    assert cap['input_fact_ids']==[x['fact_id'] for x in rows]
    assert result['price']['value_text']==rows[0]['value_text']


@pytest.mark.parametrize('field',['capture_id','normalizer_version','chain','token_address','source_kind','occurred_at','time_basis'])
def test_different_snapshot_cannot_supply_missing_operand(field):
    anchor,base,rows,_=inputs();rows[1][field]='different'
    cap=build_valuation(anchor,'token_info',base,rows)['market_cap']
    assert cap['state']=='unavailable' and cap['value_text'] is None


@pytest.mark.parametrize('mutation',[{'quality':'unverified'},{'quality':'invalid'},{'unit':'atomic'},
    {'contract_version':'unreviewed.v99'},{'metric':'total_supply'},{'value_text':None},
    {'value_text':'NaN'},{'value_text':'-1'},{'value_text':'1e1001'},
    {'value_text':'1e9999999999999999999999'},{'value_text':'1'*4097}])
def test_bad_supply_never_turns_into_usable_cap(mutation):
    anchor,base,rows,_=inputs();rows[1].update(mutation)
    assert build_valuation(anchor,'token_info',base,rows)['market_cap']['state']=='unavailable'


def test_no_total_supply_or_other_item_fallback_and_no_decimal_rescaling():
    anchor,base,rows,row=inputs();rows.pop()
    rows += [row('total_supply','total_supply','999','token'),
             row('circulating_supply','another_item/circulating_supply','555','token')]
    assert build_valuation(anchor,'token_info',base,rows)['market_cap']['state']=='unavailable'
    rows.append(row('circulating_supply','circulating_supply','100','token'))
    rows.append(row('decimals','decimals','9','decimal_places'))
    assert Decimal(build_valuation(anchor,'token_info',base,rows)['market_cap']['value_text'])==250


def test_duplicate_operand_blocks_and_zero_supply_is_not_missing():
    anchor,base,rows,_=inputs(supply='0')
    assert Decimal(build_valuation(anchor,'token_info',base,rows)['market_cap']['value_text'])==0
    rows.append(dict(rows[1],fact_id=uuid4()))
    assert build_valuation(anchor,'token_info',base,rows)['market_cap']['state']=='unavailable'


def test_reported_rank_cap_is_not_recalculated_or_replaced_by_ath():
    anchor,base,rows,row=inputs('market_rank')
    rows += [row('market_cap','market_cap','777.00','usd'),row('history_highest_market_cap','history_highest_market_cap','999999','usd')]
    cap=build_valuation(anchor,'market_rank',base,rows)['market_cap']
    assert cap['kind']=='reported' and cap['value_text']=='777.00'
    assert cap['supply_basis']=='unspecified_by_provider' and cap['input_fact_ids']==[rows[2]['fact_id']]


def test_trenches_alias_is_not_the_documented_usd_market_cap():
    anchor,base,rows,row=inputs('trenches')
    rows += [row('market_cap','market_cap','777','usd')]
    result=build_valuation(anchor,'trenches',base,rows)
    assert result['market_cap']['state']=='unavailable' and result['price']['state']=='unavailable'
    rows += [row('usd_market_cap','usd_market_cap','888','usd')]
    assert build_valuation(anchor,'trenches',base,rows)['market_cap']['value_text']=='888'

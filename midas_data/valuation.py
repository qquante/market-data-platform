"""Versioned valuation from persisted inputs of one capture/item/interpretation."""
from __future__ import annotations

from decimal import Decimal, Inexact, InvalidOperation, localcontext
from .normalize import _NUMBER

VALUATION_VERSION = 'gmgn-valuation-v1'
PATHS = ('price', 'price/price', 'market_cap', 'usd_market_cap', 'circulating_supply',
         'total_supply', 'max_supply', 'decimals', 'liquidity', 'price/volume_5m', 'volume', 'volume_5m')


def valuation_pointers(item_pointer):
    return [item_pointer + '/' + path for path in PATHS]


def _input(anchor, rows, pointer, metric, unit, contracts, *, positive=False):
    matches = [r for r in rows if r['pointer'] == pointer]
    if len(matches) != 1:
        return {'state': 'unavailable', 'reason': 'absent' if not matches else 'ambiguous',
                'fact_id': None, 'pointer': pointer, 'value_text': None}, None
    row = matches[0]
    text = row.get('value_text')
    result = {k: row.get(k) for k in ('fact_id', 'pointer', 'metric', 'unit', 'quality',
                                    'contract_version', 'available_at')}
    result.update(state='unavailable', value_text=text if isinstance(text, str) and len(text)<=4096 else None,
                  value_omitted=isinstance(text, str) and len(text)>4096)
    identity = ('capture_id', 'normalizer_version', 'source_kind', 'evidence_kind',
                'chain', 'token_address', 'occurred_at', 'time_basis')
    if any(row.get(k) != anchor.get(k) for k in identity):
        result['reason'] = 'different_snapshot'
    elif row.get('quality') != 'valid':
        result['reason'] = 'input_' + str(row.get('quality'))
    elif row.get('contract_version') not in contracts or row.get('metric') != metric or row.get('unit') != unit:
        result['reason'] = 'unsupported_field_contract'
    elif row.get('value_type') not in ('decimal', 'integer') or not isinstance(text, str) or len(text)>4096 or not _NUMBER.fullmatch(text):
        result['reason'] = 'invalid_numeric_text'
    else:
        try:
            value = Decimal(text)
        except InvalidOperation:
            result['reason'] = 'numeric_budget_exceeded'
            return result, None
        if not value.is_finite() or len(value.as_tuple().digits)>1000 or abs(value.adjusted())>1000:
            result['reason'] = 'numeric_budget_exceeded'
        elif value < 0 or (positive and value == 0):
            result['reason'] = 'invalid_numeric_range'
        else:
            result['state'] = 'available'
            return result, value
    return result, None


ECONOMIC_INPUTS_VERSION = 'gmgn-trajectory-inputs-v1'


def build_economic_inputs(anchor, endpoint, item_pointer, rows):
    """Optional trajectory context; unavailable fields never become cap operands."""
    token_contracts = {'gmgn.token_info.v3'} if endpoint == 'token_info' else set()
    volume_contracts = token_contracts if endpoint == 'token_info' else (
        {'gmgn.market_rank.v4'} if endpoint == 'market_rank' else set())
    volume_path = 'price/volume_5m' if endpoint == 'token_info' else (
        'volume' if endpoint == 'market_rank' else 'volume_5m')
    specs = (
        ('circulating_supply', 'circulating_supply', 'token', token_contracts,
         'provider_reported_human_tokens; same-capture operand only'),
        ('total_supply', 'total_supply', 'token', set(),
         'not approved as circulating supply or cap fallback'),
        ('max_supply', 'max_supply', 'token', set(),
         'not approved as circulating supply or cap fallback'),
        ('decimals', 'decimals', 'decimal_places', token_contracts,
         'metadata only; never rescale provider human-token circulating supply'),
        ('liquidity', 'liquidity', 'usd', token_contracts,
         'provider biggest-pool liquidity; not executable depth or aggregate across pools'),
        ('volume_5m', volume_path, 'usd', volume_contracts,
         'provider rolling 5m volume; window end not measured; never sum snapshots as daily volume'),
    )
    fields = {}
    for metric, path, unit, contracts, scope in specs:
        field, _ = _input(anchor, rows, item_pointer+'/'+path, metric, unit, contracts)
        field.update(expected_unit=unit, scope=scope, contract_supported=bool(contracts))
        fields[metric] = field
    return {
        'version': ECONOMIC_INPUTS_VERSION,
        'fields': fields,
        'scope': 'optional context from this capture/item/interpretation; not a temporal series',
        'core_valuation_fields': ['price', 'market_cap'],
        'unsupported_fields_policy': 'keep raw/catalogue; unavailable for trajectory arithmetic',
        'cross_source_policy': 'keep endpoint, method and declared pool scope separate',
    }


def build_valuation(anchor, endpoint, item_pointer, rows, *, method=None):
    from .trench_economics import METHOD as OBSERVED_METHOD,QUALIFICATION,observed_inputs
    if method == OBSERVED_METHOD and endpoint == 'trenches':
        result=observed_inputs(anchor,item_pointer,rows,_input)
        if result is None:
            return {'version':VALUATION_VERSION,'normalizer_version':anchor['normalizer_version'],
                'price':{'state':'unavailable','value_text':None,'unit':'usd_per_token','reason':'unsupported_chain'},
                'market_cap':{'state':'unavailable','value_text':None,'method':method,'unit':'usd','kind':'reported','supply_basis':'provider_reported_total_supply','input_fact_ids':[],'reason':'unsupported_chain'},
                'economic_inputs':build_economic_inputs(anchor,endpoint,item_pointer,rows)}
        price,reported,supply=result
        cap={**reported,'method':method,'series_key':method,'kind':'reported','metric':'market_cap',
            'supply_basis':'provider_reported_total_supply_consistency_check',
            'value_text':reported['value_text'] if reported['state']=='available' else None,
            'input_fact_ids':[i['fact_id'] for i in result if i.get('fact_id')]}
        return {'version':QUALIFICATION,'normalizer_version':anchor['normalizer_version'],'price':price,
            'market_cap':cap,'inputs':list(result),'economic_inputs':build_economic_inputs(anchor,endpoint,item_pointer,rows),
            'interpretation_basis':'versioned empirical reading of previously published numbers; qualification learned later',
            'scope':'exploratory provider quotes; no pool equivalence, fill or independent supply verification'}
    if endpoint == 'token_info':
        price_path, price_contracts = 'price/price', {'gmgn.token_info.v3'}
    elif endpoint == 'market_rank':
        price_path, price_contracts = 'price', {'gmgn.market_rank.v4'}
    else:
        price_path, price_contracts = 'price', set()  # Trenches price alias is unverified.
    price, price_number = _input(anchor, rows, item_pointer+'/'+price_path,
        'price', 'usd_per_token', price_contracts, positive=True)
    price['kind'] = 'reported'
    inputs = [price]
    cap = {'state':'unavailable', 'value_text':None, 'unit':'usd', 'reason':'unsupported_endpoint'}
    if endpoint == 'token_info':
        supply, supply_number = _input(anchor, rows, item_pointer+'/circulating_supply',
            'circulating_supply', 'token', {'gmgn.token_info.v3'})
        inputs.append(supply)
        cap.update(kind='derived', metric='circulating_market_cap_estimate',
            supply_basis='provider_reported_circulating_supply',
            method='gmgn.token_info.price_times_circulating_supply.v1',
            formula='price.price * circulating_supply', reason='input_unavailable')
        if price_number is not None and supply_number is not None:
            with localcontext() as ctx:
                ctx.prec = max(1, len(price_number.as_tuple().digits)+len(supply_number.as_tuple().digits))
                ctx.Emax, ctx.Emin = 4096, -4096
                ctx.traps[Inexact] = True
                cap.update(state='available', value_text=str(price_number*supply_number))
                cap.pop('reason')
    elif endpoint in ('market_rank', 'trenches'):
        metric = 'market_cap' if endpoint == 'market_rank' else 'usd_market_cap'
        contract = 'gmgn.market_rank.v4' if endpoint == 'market_rank' else 'gmgn.trenches.v1'
        reported, _ = _input(anchor, rows, item_pointer+'/'+metric, metric, 'usd', {contract})
        inputs.append(reported)
        cap.update(state=reported['state'],value_text=reported['value_text'] if reported['state']=='available' else None,
            kind='reported',metric=metric,supply_basis='unspecified_by_provider',
            method=contract+'.'+metric)
        if reported['state']=='available': cap.pop('reason')
        else: cap['reason']=reported['reason']
    cap['input_fact_ids'] = [r['fact_id'] for r in (inputs if endpoint=='token_info' else inputs[1:]) if r.get('fact_id')]
    cap['series_key'] = cap.get('method')  # Different methods must never silently share a curve.
    return {'version':VALUATION_VERSION, 'normalizer_version':anchor['normalizer_version'],
            'price':price, 'market_cap':cap, 'inputs':inputs,
            'economic_inputs':build_economic_inputs(anchor,endpoint,item_pointer,rows),
            'interpretation_basis':'on_demand_calculation_from_inputs_known_at_cutoff',
            'scope':'specified capture, item and fact interpretation; not latest token state or historical ATH'}

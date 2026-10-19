"""Explicit empirical reading policy; original v4 facts and strict method stay intact."""
from decimal import Decimal,localcontext

METHOD='gmgn.trenches.observed_market_cap_usd.v1'
QUALIFICATION='gmgn-sol-trenches-economic-observation-20260911-v1'
# A read policy supported by saved raw/official documented USD sibling and cross-endpoint audit.
# This does not equate pools, supply bases or mark the old normalized facts as verified.
def observed_inputs(anchor,base,rows,read_input):
    if anchor.get('chain')!='sol':return None
    mapped=[]
    for row in rows:
        path=row.get('pointer','').removeprefix(base+'/')
        if path not in ('price','market_cap','total_supply'):continue
        if row.get('contract_version')!='gmgn.trenches.v1':continue
        if row.get('quality') not in ('valid','unverified'):continue
        mapped.append(dict(row,quality='valid',contract_version=QUALIFICATION,
            unit={'price':'usd_per_token','market_cap':'usd','total_supply':'token'}[path]))
    values=[]
    for name,unit,positive in [('price','usd_per_token',True),('market_cap','usd',False),('total_supply','token',True)]:
        item,value=read_input(anchor,mapped,base+'/'+name,name,unit,{QUALIFICATION},positive=positive)
        item.update(qualification=QUALIFICATION,qualification_basis='empirical_source_contract',
            original_field_quality='unverified',pool_scope='provider token quote; pool equivalence unverified')
        values.append((item,value))
    (price,p),(cap,m),(supply,s)=values
    reason=None
    if p is None or m is None or s is None:reason='empirical_contract_inputs_unavailable'
    else:
        with localcontext() as ctx:
            ctx.prec=4096
            delta=abs(m-p*s);tolerance=Decimal('.02')+abs(m)*Decimal('.00001')
            if delta>tolerance:reason='reported_cap_supply_price_disagree'
    if reason:
        for item,_ in values:item.update(state='unavailable',reason=reason)
    return price,cap,supply

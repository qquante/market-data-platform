"""Bounded, exact read-time interpretation of archived OHLCV; no fake historical knowledge."""
from collections import Counter
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
import hashlib,json,re
from .contracts import utc_datetime
from .normalize import _pairs,_constant,_nodes,_PayloadError
VERSION='gmgn-candles-read-v1'
MAX_ROWS=1500
RESOLUTIONS={'30s':30,'1m':60,'5m':300,'15m':900,'1h':3600,'4h':14400,'1d':86400}

class CandleContract(ValueError):pass

def number(value,positive=False):
    if not isinstance(value,(str,int,Decimal)) or isinstance(value,bool):raise CandleContract('invalid_number')
    text=str(value)
    if len(text)>100 or not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?',text):
        raise CandleContract('invalid_number')
    try:n=Decimal(text)
    except InvalidOperation:raise CandleContract('invalid_number') from None
    if not n.is_finite() or abs(n.adjusted())>30 or n<0 or (positive and n==0):raise CandleContract('invalid_number')
    return text,n

def parse_candles(meta,raw,available_at):
    if len(raw)>4*1024*1024:raise CandleContract('raw_budget')
    query=meta.request_params.get('query',meta.request_params)
    if not isinstance(query,dict) or meta.endpoint!='token_kline' or meta.chain!='sol':raise CandleContract('unsupported_capture')
    if query.get('chain')!=meta.chain or not isinstance(query.get('address'),str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}',query['address']):raise CandleContract('identity')
    resolution=query.get('resolution');step=RESOLUTIONS.get(resolution)
    start,stop=query.get('from'),query.get('to')
    if not step or type(start) is not int or type(stop) is not int or not 0<start<stop or stop-start>86400000:
        raise CandleContract('explicit_millisecond_range_required')
    if stop>int(meta.requested_at.timestamp()*1000):raise CandleContract('future_requested_range')
    try:
        root=json.loads(raw.decode('utf-8-sig'),parse_float=Decimal,parse_constant=_constant,object_pairs_hook=_pairs)
        _nodes(root)
    except (ValueError,UnicodeError,RecursionError,_PayloadError):raise CandleContract('invalid_bounded_json') from None
    if not 200<=meta.http_status<300 or not isinstance(root,dict) or type(root.get('code')) is not int or root['code']!=0:
        raise CandleContract('provider_failure')
    data=root.get('data');rows=data.get('list') if isinstance(data,dict) else None
    if not isinstance(rows,list):raise CandleContract('unsupported_envelope')
    if len(rows)>MAX_ROWS:raise CandleContract('row_budget')
    # A conflicting explicit identity always invalidates the whole response.
    for obj in (root,data):
        if ('chain' in obj and obj['chain']!=meta.chain) or ('address' in obj and obj['address']!=query['address']):raise CandleContract('identity_conflict')
    stamps=Counter(r.get('time') for r in rows if isinstance(r,dict) and type(r.get('time')) is int)
    output=[]
    for index,row in enumerate(rows):
        candle={'ordinal':index,'pointer':f'/data/list/{index}','state':'unavailable','time_ms':None}
        try:
            if not isinstance(row,dict) or type(row.get('time')) is not int:raise CandleContract('invalid_millisecond_time')
            at=row['time'];candle['time_ms']=at
            if not start<=at<stop:raise CandleContract('outside_requested_range')
            if at%(step*1000):raise CandleContract('unaligned_bar')
            if stamps[at]!=1:raise CandleContract('duplicate_bar')
            values={key:number(row.get(key),positive=key!='volume') for key in ('open','high','low','close','volume')}
            o,h,l,c=(values[k][1] for k in ('open','high','low','close'))
            if not l<=min(o,c)<=max(o,c)<=h:raise CandleContract('inconsistent_ohlc')
            if at+step*1000>int(meta.received_at.timestamp()*1000):raise CandleContract('unfinished_bar_at_receipt')
            candle.update(state='available',closed_at=datetime.fromtimestamp(at/1000,timezone.utc)+timedelta(seconds=step),
                **{k:v[0] for k,v in values.items()})
        except CandleContract as exc:candle['reason']=str(exc)
        output.append(candle)
    output.sort(key=lambda x:(x['time_ms'] is None,x['time_ms'] or 0,x['ordinal']))
    eligible={r['time_ms'] for r in output if r['state']=='available'}
    first=((start+step*1000-1)//(step*1000))*step*1000
    expected=range(first,stop,step*1000)
    missing=[at for at in expected if at not in eligible]
    return {'version':VERSION,'capture_id':str(meta.capture_id),'source_kind':meta.source_kind,'evidence_kind':meta.evidence_kind,
        'chain':meta.chain,'token_address':query['address'],'resolution':resolution,'request_from_ms':start,'request_to_ms':stop,
        'raw_sha256':hashlib.sha256(raw).hexdigest(),'received_at':meta.received_at,'available_at':utc_datetime(available_at),
        'knowledge_basis':'archived response publication; historical bar time is not historical availability',
        'price_unit':'usd_per_token','volume_unit':'usd','scope':'provider bars; pool equivalence and execution fills unverified',
        'items':output,'coverage':{'returned_rows':len(rows),'eligible_rows':len(eligible),'expected_rows':len(expected),
            'unavailable_rows':len(rows)-len(eligible),'missing_bar_times_ms':missing,'interpolation':'none'},
        'interpretation_basis':'versioned read-time parser; token Fact and holder tables unchanged'}

"""Small cached schema registry and bounded latest-capture operational audit."""
import asyncio, json
from datetime import datetime, timezone
from fastapi import HTTPException, Query, Request
from fastapi.responses import Response
from .field_dictionary import ENDPOINTS, VERSION, dictionary_page
from .explorer import field_capture, interpreted_fields, RawFieldsError
from .raw_store import RawIntegrityError
from .read_cache import ReadCache, ReaderBusy


def install_dictionary_routes(app, slots):
    cache=ReadCache(slots,max_admitted=4,max_bytes=1024*1024,max_entries=1,ttl=30)

    @app.get('/v1/dictionary')
    async def dictionary(endpoint:str|None=Query(None,max_length=64),category:str|None=Query(None,max_length=64),
                         usage:str|None=Query(None,pattern='^(metric|metadata|blocked|structure)$'),
                         query:str=Query('',max_length=150),offset:int=Query(0,ge=0,le=10000),limit:int=Query(50,ge=1,le=100)):
        if endpoint and endpoint not in ENDPOINTS:raise HTTPException(422,'Endpoint outside configured dictionary scope')
        return dictionary_page(endpoint,category,usage,query,offset,limit)

    @app.get('/v1/dictionary/coverage')
    async def coverage(request:Request):
        async def compute():
            store=request.app.state.store
            async with asyncio.timeout(10):
                cutoff=await store.pool.fetchval('SELECT clock_timestamp()')
                items=[]
                for endpoint in ENDPOINTS:
                    cap=await store.pool.fetchval("""SELECT capture_id FROM captures WHERE source_kind='gmgn'
                        AND chain='sol' AND endpoint=$1 AND available_at<=$2
                        ORDER BY recorded_at DESC,capture_id DESC LIMIT 1""",endpoint,cutoff)
                    if cap is None:
                        items.append({'endpoint':endpoint,'state':'no_capture'});continue
                    row=await field_capture(store,cap,'gmgn',cutoff)
                    try:
                        result=await asyncio.to_thread(interpreted_fields,store,row,'',0,1)
                        summary=result['quality_summary'];state='drift' if summary['uncatalogued_path_count'] or summary['type_drift_path_count'] else 'catalogued'
                        items.append({'endpoint':endpoint,'state':state,'capture_id':str(cap),
                            'received_at':row['received_at'].isoformat(),'normalizer_version':row['normalizer_version'],
                            'normalization_state':row['normalization_state'],'quality':summary})
                    except (RawFieldsError,RawIntegrityError) as exc:
                        items.append({'endpoint':endpoint,'capture_id':str(cap),'state':'unavailable','reason':type(exc).__name__})
                payload={'dictionary_version':VERSION,'sampled_at':cutoff.isoformat(),'cache_ttl_seconds':30,
                    'scope':'latest published original GMGN Solana capture per endpoint; operational sample, not all-history quality or historical knowledge',
                    'items':items}
                return json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode(),{}
        try:
            (body,_),cache_state=await cache.run(VERSION,compute)
        except ReaderBusy:raise HTTPException(429,'Evidence readers busy',headers={'Retry-After':'1'}) from None
        except TimeoutError:raise HTTPException(503,'Dictionary coverage timed out') from None
        return Response(body,media_type='application/json',headers={'X-Read-Cache':cache_state})

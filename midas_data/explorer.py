"""Bounded read-only explorer for archived captures, exact raw fields and facts."""
from datetime import datetime, timezone
from uuid import UUID
import asyncio
import json
from fastapi import HTTPException, Query, Request
from .contracts import utc_datetime
from .raw_store import RawIntegrityError
from .field_dictionary import assess_field, summarize_fields


class NumericText(str):
    """Keep the original JSON number lexeme; never round through float."""


class RawFieldsError(ValueError):
    pass


def raw_fields(raw, query='', offset=0, limit=100, *, endpoint=None, observations=None, chain=None, accepted=False):
    if len(raw)>4*1024*1024:raise RawFieldsError('raw_byte_budget')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise RawFieldsError('duplicate_json_key')
            result[k]=v
        return result
    def constant(value):raise RawFieldsError('nonfinite_json_number')
    try:root=json.loads(raw.decode('utf-8-sig'),parse_int=NumericText,parse_float=NumericText,
                        parse_constant=constant,object_pairs_hook=pairs)
    except (ValueError,UnicodeError,RecursionError) as exc:raise RawFieldsError('invalid_json_use_original_download') from exc
    rows=[];all_rows=[];nodes=0;q=query.casefold();annotations=observations or {}
    def visit(value,pointer,depth,schema_path):
        nonlocal nodes
        nodes+=1
        if depth>32 or nodes>50000:raise RawFieldsError('raw_structure_budget')
        if isinstance(value,dict) and value:
            for k,v in value.items():
                part=k.replace('~','~0').replace('/','~1');visit(v,pointer+'/'+part,depth+1,schema_path+'/'+part)
        elif isinstance(value,list) and value:
            for i,v in enumerate(value):visit(v,pointer+'/'+str(i),depth+1,schema_path+'/*')
        else:
            kind=('number' if isinstance(value,NumericText) else 'null' if value is None else
                  'boolean' if isinstance(value,bool) else 'string' if isinstance(value,str) else
                  'array' if isinstance(value,list) else 'object')
            text=(str(value) if isinstance(value,str) else json.dumps(value,separators=(',',':')))
            row={'pointer':pointer,'schema_path':schema_path,'value_type':kind,'value_text':text}
            if endpoint is not None:
                row['interpretation']=assess_field(endpoint,row,annotations.get(pointer),chain=chain,accepted=accepted)
                all_rows.append(row)
            if not q or q in pointer.casefold() or q in text.casefold():rows.append(row)
    visit(root,'',0,'')
    page=rows[offset:offset+limit]
    if len(json.dumps(page,ensure_ascii=False).encode())>1024*1024:raise RawFieldsError('field_page_byte_budget_use_original_download')
    return {'quality_summary':summarize_fields(all_rows) if endpoint is not None else None,'items':page,'total':len(rows),'offset':offset,'limit':limit,'has_more':offset+limit<len(rows),
            'numbers':'original JSON numeric lexemes','scope':'raw leaves, including null and empty containers; observed does not mean economically qualified'}



def decoded(value, default):
    return json.loads(value) if isinstance(value,str) else value if value is not None else default


async def field_capture(store,capture_id,source_kind,cutoff):
    return await store.pool.fetchrow("""SELECT c.capture_id,c.endpoint,c.chain,c.received_at,c.available_at,
        c.raw_sha256,c.normalization_state,n.normalizer_version,n.issues,f.fields
        FROM captures c LEFT JOIN LATERAL (SELECT * FROM normalization_runs n
            WHERE n.capture_id=c.capture_id AND n.available_at<=$3
            ORDER BY n.available_at DESC,n.normalizer_version DESC LIMIT 1) n ON true
        LEFT JOIN field_catalog_batches f ON f.capture_id=n.capture_id AND f.normalizer_version=n.normalizer_version
        WHERE c.capture_id=$1 AND c.source_kind=$2 AND c.available_at<=$3""",capture_id,source_kind,cutoff)


def interpreted_fields(store,row,query='',offset=0,limit=100):
    from .normalize import FATAL_ISSUE_CODES,UNSUPPORTED_ISSUE_CODES
    issues=decoded(row['issues'],[])
    accepted=bool(row['normalizer_version']) and not any(i['code'] in FATAL_ISSUE_CODES|UNSUPPORTED_ISSUE_CODES for i in issues)
    annotations={f['pointer']:f for f in decoded(row['fields'],[])}
    return raw_fields(store.raw.read(row['raw_sha256']),query,offset,limit,
        endpoint=row['endpoint'],chain=row['chain'],accepted=accepted,observations=annotations)


def install_explorer_routes(app,slots):
    def clock(value):
        try:result=utc_datetime(value)
        except ValueError:raise HTTPException(422,'UTC timestamp required') from None
        if result>datetime.now(timezone.utc):raise HTTPException(422,'Future cutoff rejected')
        return result

    @app.get('/v1/explorer/captures')
    async def captures(request:Request,knowledge_cutoff:datetime,start:datetime,end:datetime,
                       source_kind:str=Query(...,pattern='^(fixture|gmgn|legacy_gmgn)$'),
                       chain:str|None=Query(None,max_length=64),endpoint:str|None=Query(None,max_length=256),
                       state:str|None=Query(None,pattern='^(pending|ready|quarantined)$'),
                       limit:int=Query(50,ge=1,le=200),before_at:datetime|None=None,before_id:UUID|None=None):
        cutoff=clock(knowledge_cutoff)
        try:start,end=utc_datetime(start),utc_datetime(end)
        except ValueError:raise HTTPException(422,'UTC interval required') from None
        if end<=start or (end-start).total_seconds()>7*86400 or end>cutoff:
            raise HTTPException(422,'Require interval <= 7 days and end <= cutoff')
        if (before_at is None)!=(before_id is None):raise HTTPException(422,'Complete capture cursor required')
        if before_at is not None:before_at=clock(before_at)
        try:
            async with asyncio.timeout(5):
                rows=await request.app.state.store.pool.fetch('''SELECT capture_id,source_kind,evidence_kind,endpoint,chain,
                    received_at,recorded_at,available_at,normalization_state,raw_sha256
                    FROM captures WHERE source_kind=$1 AND recorded_at>=$2 AND recorded_at<$3
                    AND available_at<=$4 AND ($5::text IS NULL OR chain=$5) AND ($6::text IS NULL OR endpoint=$6)
                    AND ($7::text IS NULL OR normalization_state=$7)
                    AND ($8::timestamptz IS NULL OR (recorded_at,capture_id)<($8,$9::uuid))
                    ORDER BY recorded_at DESC,capture_id DESC LIMIT $10''',
                    source_kind,start,end,cutoff,chain,endpoint,state,before_at,before_id,limit+1)
        except TimeoutError:raise HTTPException(503,'Capture query timed out; reduce interval') from None
        items=[dict(r) for r in rows[:limit]]
        return {'items':items,'has_more':len(rows)>limit,'limit':limit,'knowledge_cutoff':cutoff,
                'next_cursor':{'before_at':items[-1]['recorded_at'],'before_id':items[-1]['capture_id']} if len(rows)>limit else None,
                'scope':'captures accepted by cutoff; normalization_state is current operational state, not historical state'}

    @app.get('/v1/explorer/captures/{capture_id}/fields')
    async def fields(request:Request,capture_id:UUID,knowledge_cutoff:datetime,
                     source_kind:str=Query(...,pattern='^(fixture|gmgn|legacy_gmgn)$'),
                     query:str=Query('',max_length=150),offset:int=Query(0,ge=0,le=50000),limit:int=Query(100,ge=1,le=200)):
        cutoff=clock(knowledge_cutoff)
        if slots.locked():raise HTTPException(429,'Evidence readers busy',headers={'Retry-After':'1'})
        async with slots:
            try:
                async with asyncio.timeout(5):
                    row=await field_capture(request.app.state.store,capture_id,source_kind,cutoff)
                    if row is None:raise HTTPException(404,'Capture unavailable for source and cutoff')
                    def read():return interpreted_fields(request.app.state.store,row,query,offset,limit)
                    result=await asyncio.to_thread(read)
            except RawFieldsError as exc:raise HTTPException(422,str(exc)) from None
            except RawIntegrityError:raise HTTPException(503,'Raw integrity verification failed') from None
            except TimeoutError:raise HTTPException(503,'Raw field query timed out') from None
        return dict(result,capture_id=str(capture_id),raw_sha256=row['raw_sha256'],knowledge_cutoff=cutoff)

    @app.get('/v1/explorer/captures/{capture_id}/facts')
    async def facts(request:Request,capture_id:UUID,knowledge_cutoff:datetime,
                    source_kind:str=Query(...,pattern='^(fixture|gmgn|legacy_gmgn)$'),
                    limit:int=Query(100,ge=1,le=200),after_seq:int=Query(0,ge=0)):
        cutoff=clock(knowledge_cutoff)
        store=request.app.state.store
        try:
            async with asyncio.timeout(5):
                rows=await store._temporal_fetch(cutoff,'''SELECT f.* FROM facts f
                    JOIN LATERAL (SELECT n.normalizer_version FROM normalization_runs n
                       WHERE n.capture_id=$1 AND n.available_at<=$3
                       ORDER BY n.available_at DESC,n.normalizer_version DESC LIMIT 1) chosen
                    ON chosen.normalizer_version=f.normalizer_version
                    WHERE f.capture_id=$1 AND f.source_kind=$2 AND f.available_at<=$3 AND f.seq>$4
                    ORDER BY f.seq LIMIT $5''',capture_id,source_kind,cutoff,after_seq,limit+1)
        except TimeoutError:raise HTTPException(503,'Fact query timed out') from None
        items=[dict(r) for r in rows[:limit]]
        for r in items:r.pop('numeric_value',None)
        return {'items':items,'has_more':len(rows)>limit,'next_after_seq':items[-1]['seq'] if len(rows)>limit else None,
                'knowledge_cutoff':cutoff,'scope':'all qualities; latest interpretation published by cutoff; values in exact text'}

"""Atomic holder publication and bounded point-in-time reads."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from .contracts import CaptureMetadata
from .holders import HolderParseResult, PARSER_VERSION, MAX_OUTPUT_BYTES
from .store import CaptureConflict, ValuationInputLimit, canonical

ROW_ISSUES = frozenset({'invalid_field','invalid_holder_item','invalid_holder_identity',
                       'duplicate_wallet','balance_alias_conflict'})


class HolderStore:
    def __init__(self, store):
        self.store = store

    async def record(self, result: HolderParseResult):
        if not isinstance(result, HolderParseResult):
            raise TypeError('holder_result_required')
        body = result.model_dump(mode='json')
        serialized = canonical(body)
        if len(serialized.encode()) > MAX_OUTPUT_BYTES or len(result.holders) > 100:
            raise ValueError('holder_output_budget')
        if any(h.capture_id != result.metadata.capture_id for h in result.holders):
            raise ValueError('holder_capture_identity')
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        meta, version = result.metadata, result.parser_version
        cid = meta.capture_id
        batch = hashlib.sha256((str(cid)+':'+version).encode()).hexdigest()
        rejected = result.returned_rows is None or any(i.code not in ROW_ISSUES for i in result.issues)
        state = 'quarantined' if rejected else 'partial' if result.issues else 'ready'
        params = meta.request_params
        query = params.get('query', params)
        token = query.get('address') if isinstance(query, dict) else None
        if not isinstance(token, str): token = None
        async with self.store.pool.acquire() as con:
            async with con.transaction():
                # One bounded capture per transaction. No ACK before all rows and
                # the availability marker commit; clock readers use the same lock.
                await con.execute('SELECT pg_advisory_xact_lock(60608003)')
                capture = await con.fetchrow('SELECT metadata,raw_sha256 FROM captures WHERE capture_id=$1', cid)
                if (capture is None or capture['raw_sha256'] != result.raw_sha256
                        or CaptureMetadata.model_validate_json(capture['metadata']) != meta):
                    raise CaptureConflict('Holder capture provenance mismatch')
                prior = await con.fetchrow("""SELECT content_sha256,available_at FROM normalization_runs
                    WHERE capture_id=$1 AND normalizer_version=$2""", cid, version)
                if prior:
                    if prior['content_sha256'] != digest:
                        raise CaptureConflict('Holder parser version changed immutable output')
                    if prior['available_at'] is None:
                        raise CaptureConflict('Incomplete holder publication')
                    return {'capture_id':cid,'parser_version':version,'duplicate':True,
                            'available_at':prior['available_at']}
                marker = await con.fetchval('SELECT clock_timestamp()')
                await con.execute("""INSERT INTO normalization_runs
                    (capture_id,normalizer_version,batch_id,content_sha256,fact_count,field_count,issues,available_at)
                    VALUES($1,$2,$3,$4,0,$5,$6::jsonb,$7)""",
                    cid,version,batch,digest,len(result.fields),canonical(body['issues']),marker)
                await con.execute("""INSERT INTO field_catalog_batches(capture_id,normalizer_version,fields)
                    VALUES($1,$2,$3::jsonb)""",cid,version,canonical(body['fields']))
                await con.execute("""INSERT INTO holder_runs(capture_id,parser_version,source_kind,chain,
                    token_address,occurred_at,returned_rows,accepted_rows,state,collection_scope)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'partial_list')""",
                    cid,version,meta.source_kind,meta.chain or '',token,meta.source_event_at or meta.received_at,
                    result.returned_rows if result.returned_rows is not None and result.returned_rows<=100 else None,
                    len(result.holders),state)
                if result.holders:
                    await con.copy_records_to_table('holder_observations', columns=(
                        'capture_id','parser_version','holder_id','ordinal','wallet_address','observation'),
                        records=[(cid,version,h.holder_id,i,h.wallet_address,canonical(body['holders'][i]))
                                 for i,h in enumerate(result.holders)])
                await con.execute("""UPDATE captures SET normalization_state=$2,last_error=NULL,
                    available_at=COALESCE(available_at,$3) WHERE capture_id=$1""",
                    cid,'quarantined' if rejected else 'ready',marker)
                await con.execute("""UPDATE outbox SET consumed_at=COALESCE(consumed_at,$2),
                    last_error=NULL WHERE capture_id=$1""",cid,marker)
        return {'capture_id':cid,'parser_version':version,'duplicate':False,'available_at':marker}

    async def listing(self, source_kind, chain, token, start, end, cutoff, limit=50, after_seq=0):
        if not 1 <= limit <= 200 or after_seq < 0 or start >= end or end-start > timedelta(days=31):
            raise ValueError('holder_query_bounds')
        rows = await self.store._temporal_fetch(cutoff, """SELECT h.*,c.raw_sha256,c.evidence_kind,
            c.received_at,c.available_at AS capture_available_at,n.available_at,n.issues
            FROM holder_runs h JOIN captures c USING(capture_id)
            JOIN normalization_runs n ON n.capture_id=h.capture_id AND n.normalizer_version=h.parser_version
            WHERE h.source_kind=$1 AND h.chain=$2 AND h.token_address=$3
                AND h.occurred_at>=$4 AND h.occurred_at<$5
                AND n.available_at<=$6 AND c.available_at<=$6 AND h.run_seq>$7
                AND h.parser_version=$8
            ORDER BY h.run_seq LIMIT $9""",source_kind,chain,token,start,end,cutoff,after_seq,PARSER_VERSION,limit+1)
        items = [{**dict(r),'issues':json.loads(r['issues'])} for r in rows[:limit]]
        return {'items':items,'has_more':len(rows)>limit,
            'next_after_seq':items[-1]['run_seq'] if len(rows)>limit else None,
            'knowledge_cutoff':cutoff,'parser_version':PARSER_VERSION,'scope':'partial_list'}

    async def snapshot(self, capture_id, source_kind, cutoff, limit=100, after_ordinal=-1):
        if not 1 <= limit <= 100 or not -1 <= after_ordinal <= 99:
            raise ValueError('holder_query_bounds')
        runs = await self.store._temporal_fetch(cutoff, """SELECT h.*,c.raw_sha256,c.evidence_kind,
            c.metadata,c.available_at AS capture_available_at,n.available_at,n.issues
            FROM holder_runs h JOIN captures c USING(capture_id)
            JOIN normalization_runs n ON n.capture_id=h.capture_id AND n.normalizer_version=h.parser_version
            WHERE h.capture_id=$1 AND h.source_kind=$2 AND h.parser_version=$3
                AND n.available_at<=$4 AND c.available_at<=$4""",capture_id,source_kind,PARSER_VERSION,cutoff)
        if not runs: return None
        rows = await self.store.pool.fetch("""SELECT ordinal,observation FROM holder_observations
            WHERE capture_id=$1 AND parser_version=$2 AND ordinal>$3 ORDER BY ordinal LIMIT $4""",
            capture_id,PARSER_VERSION,after_ordinal,limit+1)
        if sum(len(r['observation'].encode()) for r in rows[:limit]) > 2*1024*1024:
            raise ValuationInputLimit('Holder response budget; reduce page size')
        run = dict(runs[0])
        run['metadata'],run['issues'] = json.loads(run['metadata']),json.loads(run['issues'])
        return {'run':run,'items':[{'ordinal':r['ordinal'],**json.loads(r['observation'])} for r in rows[:limit]],
            'has_more':len(rows)>limit,'next_after_ordinal':rows[limit-1]['ordinal'] if len(rows)>limit else None,
            'knowledge_cutoff':cutoff}

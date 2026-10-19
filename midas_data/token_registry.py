"""Prospective identity registry, independent of token tracking and economic facts."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from .contracts import CaptureMetadata
from .normalize import (CONTRACT_VERSIONS, FATAL_ISSUE_CODES, UNSUPPORTED_ISSUE_CODES,
                        MAX_RAW_BYTES, NORMALIZER_VERSION, _pairs, _constant, canonical_identity)

REGISTRY_VERSION = 'gmgn-token-registry-v1'
CANDIDATES = """
    FROM captures c JOIN normalization_runs n USING(capture_id)
    CROSS JOIN token_registry_config config
    LEFT JOIN token_registry_runs r ON r.capture_id=n.capture_id
        AND r.normalizer_version=n.normalizer_version
    WHERE c.recorded_at>=config.started_at AND n.available_at IS NOT NULL
      AND n.normalizer_version=$1 AND c.endpoint IN ('market_rank','trenches','token_info')
      AND r.completed_at IS NULL
"""


def identities(metadata, fields, issues, raw):
    """Use only identity roles already accepted by this persisted interpretation."""
    if any(i['code'] in FATAL_ISSUE_CODES | UNSUPPORTED_ISSUE_CODES for i in issues):
        return []
    if metadata.endpoint not in CONTRACT_VERSIONS:
        raise ValueError('registry_unsupported_endpoint')
    if len(raw)>MAX_RAW_BYTES or len(fields)>40000:
        raise ValueError('registry_input_budget')
    root=json.loads(raw.decode('utf-8-sig'),parse_float=Decimal,object_pairs_hook=_pairs,parse_constant=_constant)
    expected=f'gmgn.{metadata.endpoint}.{CONTRACT_VERSIONS[metadata.endpoint]}:address'
    result=[];seen=set()
    for field in fields:
        if field['category']!='identity' or field['quality']!='valid' or field.get('contract_id')!=expected:
            continue
        pointer=field['pointer']
        if not pointer.endswith('/address'):
            raise ValueError('registry_identity_pointer')
        value=root
        for component in pointer.split('/')[1:]:
            component=component.replace('~1','/').replace('~0','~')
            value=value[int(component)] if isinstance(value,list) else value[component]
        chain,address=canonical_identity(metadata.chain,value)
        if (chain,address) not in seen:
            seen.add((chain,address))
            result.append((chain,address,pointer[:-len('/address')]))
    if len(result)>10000:
        raise ValueError('registry_identity_budget')
    return result


class TokenRegistry:
    def __init__(self,store):
        self.store=store

    async def step(self):
        async with self.store.pool.acquire() as con:
            async with con.transaction(readonly=True):
                await con.execute("SET LOCAL max_parallel_workers_per_gather=0")
                rows=await con.fetch(
                    "WITH selected AS MATERIALIZED ("
                    "SELECT c.capture_id,c.raw_sha256,c.metadata,c.recorded_at,n.normalizer_version,n.issues "
                    + CANDIDATES
                    + " AND (r.retry_at IS NULL OR r.retry_at<=clock_timestamp()) "
                      "ORDER BY c.recorded_at,c.capture_id LIMIT 2) "
                      "SELECT c.capture_id,c.raw_sha256,c.metadata,c.normalizer_version,c.issues,b.fields "
                      "FROM selected c LEFT JOIN field_catalog_batches b "
                      "ON b.capture_id=c.capture_id AND b.normalizer_version=c.normalizer_version "
                      "ORDER BY c.recorded_at,c.capture_id",NORMALIZER_VERSION)
        for row in rows:
            try:
                if row['fields'] is None:
                    raise ValueError('registry_catalog_missing')
                meta=CaptureMetadata.model_validate_json(row['metadata'])
                def extract():
                    raw=self.store.raw.read(row['raw_sha256'])
                    return identities(meta,json.loads(row['fields']),json.loads(row['issues']),raw)
                entries=await asyncio.to_thread(extract)
                await self.publish(row['capture_id'],row['normalizer_version'],meta.source_kind,entries)
            except Exception as exc:
                # Durable retry avoids one missing/corrupt raw blocking later captures.
                await self.store.pool.execute("""INSERT INTO token_registry_runs
                    (capture_id,normalizer_version,attempts,retry_at,last_error)
                    VALUES($1,$2,1,clock_timestamp()+interval '30 seconds',$3)
                    ON CONFLICT(capture_id,normalizer_version) DO UPDATE SET
                    attempts=token_registry_runs.attempts+1,retry_at=EXCLUDED.retry_at,last_error=EXCLUDED.last_error
                    WHERE token_registry_runs.completed_at IS NULL""",
                    row['capture_id'],row['normalizer_version'],type(exc).__name__)
                raise
        return len(rows)

    async def publish(self,capture_id,version,source_kind,entries):
        async with self.store.pool.acquire() as con:
            async with con.transaction():
                # Same read barrier as /v1/clock; registration never backdates knowledge.
                await con.execute("SELECT pg_advisory_xact_lock(60608003)")
                if await con.fetchval("""SELECT completed_at IS NOT NULL FROM token_registry_runs
                        WHERE capture_id=$1 AND normalizer_version=$2""",capture_id,version):
                    return
                marker=await con.fetchval("SELECT clock_timestamp()")
                inserted=await con.fetch("""INSERT INTO token_registry
                    (source_kind,chain,token_address,item_pointer,capture_id,normalizer_version,registered_at)
                    SELECT $1,chain,address,pointer,$2,$3,$4
                    FROM unnest($5::text[],$6::text[],$7::text[]) AS item(chain,address,pointer)
                    ON CONFLICT(source_kind,chain,token_address) DO NOTHING RETURNING registry_id""",
                    source_kind,capture_id,version,marker,[e[0] for e in entries],
                    [e[1] for e in entries],[e[2] for e in entries])
                await con.execute("""INSERT INTO token_registry_runs
                    (capture_id,normalizer_version,completed_at,identity_count,inserted_count)
                    VALUES($1,$2,$3,$4,$5) ON CONFLICT(capture_id,normalizer_version) DO UPDATE SET
                    completed_at=EXCLUDED.completed_at,identity_count=EXCLUDED.identity_count,
                    inserted_count=EXCLUDED.inserted_count,retry_at=NULL,last_error=NULL""",
                    capture_id,version,marker,len(entries),len(inserted))

    async def listing(self,source_kind,chain,cutoff,limit,after_id,token=None):
        rows=await self.store._temporal_fetch(cutoff,"""SELECT t.*,c.endpoint,c.evidence_kind,c.raw_sha256,
            c.received_at AS registration_capture_received_at,n.available_at AS interpretation_available_at
            FROM token_registry t JOIN captures c USING(capture_id)
            JOIN normalization_runs n ON n.capture_id=t.capture_id AND n.normalizer_version=t.normalizer_version
            WHERE t.source_kind=$1 AND t.chain=$2 AND t.registered_at<=$3 AND t.registry_id>$4
                AND ($5::text IS NULL OR t.token_address=$5)
            ORDER BY t.registry_id LIMIT $6""",source_kind,chain,cutoff,after_id,token,limit+1)
        start=await self.store.pool.fetchval("SELECT started_at FROM token_registry_config")
        items=[dict(r) for r in rows[:limit]]
        return {'items':items,'has_more':len(rows)>limit,
            'next_after_registry_id':items[-1]['registry_id'] if len(rows)>limit else None,
            'knowledge_cutoff':cutoff,'source_kind':source_kind,'chain':chain,
            'registry_version':REGISTRY_VERSION,'registry_started_at':start,
            'scope':'first registration from captures accepted since registry start; not earliest historical sighting',
            'tracking_enabled':False,
            'tracking_scope':'registry does not schedule; see /v1/status collector.tracking'}

    async def lookup(self,source_kind,chain,cutoff,addresses):
        if not 1<=len(addresses)<=100:raise ValueError('registry_lookup_budget')
        rows=await self.store._temporal_fetch(cutoff,"""SELECT t.source_kind,t.chain,t.token_address,t.registry_id,
            t.capture_id,t.registered_at,c.received_at AS registration_capture_received_at
            FROM token_registry t JOIN captures c USING(capture_id)
            WHERE t.source_kind=$1 AND t.chain=$2 AND t.registered_at<=$3 AND t.token_address=ANY($4::text[])
            ORDER BY t.registry_id""",source_kind,chain,cutoff,addresses)
        return {'items':[dict(r) for r in rows],'knowledge_cutoff':cutoff,'registry_version':REGISTRY_VERSION}

    async def status(self):
        pending=await self.store.pool.fetchrow(
            "SELECT count(*) AS pending,count(*) FILTER(WHERE r.last_error IS NOT NULL) AS errors,"
            "extract(epoch FROM clock_timestamp()-min(n.available_at)) AS oldest_seconds "+CANDIDATES,
            NORMALIZER_VERSION)
        worker=await self.store.pool.fetchrow("""SELECT state,details,
            extract(epoch FROM clock_timestamp()-heartbeat_at) AS heartbeat_age_seconds
            FROM worker_health WHERE worker_name='data-registry'""")
        counts=await self.store.pool.fetch("SELECT source_kind,chain,count(*) FROM token_registry GROUP BY 1,2")
        current=bool(worker and worker['heartbeat_age_seconds']<45 and worker['state']=='ready'
            and pending['pending']==0)
        return {'version':REGISTRY_VERSION,'current':current,'queue':dict(pending),
            'worker':dict(worker) if worker else None,'counts':[dict(r) for r in counts],
            'started_at':await self.store.pool.fetchval('SELECT started_at FROM token_registry_config'),
            'retention':'no automatic expiry','tracking_enabled':False,
            'tracking_scope':'registry does not schedule; see /v1/status collector.tracking'}

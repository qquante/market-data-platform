from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from typing import Any
from uuid import UUID

import asyncpg

from .raw_store import RawStore


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class CaptureConflict(ValueError):
    pass


class FutureCutoff(ValueError):
    pass


class Store:
    def __init__(self, dsn: str, raw: RawStore):
        self.dsn = dsn
        self.raw = raw
        self.pool = None

    async def open(self):
        self.pool = await asyncpg.create_pool(
            self.dsn, min_size=1, max_size=6, command_timeout=15,
            server_settings={"timezone": "UTC", "application_name": "midas-data",
                             "statement_timeout": "15000", "idle_in_transaction_session_timeout": "15000"},
        )
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute("SELECT pg_advisory_xact_lock(60608001)")
                await con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT clock_timestamp())")
                migrations = sorted(files("midas_data").joinpath("migrations").iterdir(), key=lambda p:p.name)
                for path in migrations:
                    if not path.name.endswith(".sql"):
                        continue
                    version = path.name.split("_",1)[0]
                    migration = path.read_text(encoding="utf-8")
                    digest = hashlib.sha256(migration.encode()).hexdigest()
                    applied = await con.fetchval("SELECT sha256 FROM schema_migrations WHERE version=$1",version)
                    if applied is not None and applied != digest:
                        raise RuntimeError("Migration checksum mismatch")
                    if applied is None:
                        await con.execute(migration)
                        await con.execute("INSERT INTO schema_migrations(version,sha256) VALUES($1,$2)",version,digest)
        return self

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def accept_capture(self, metadata, raw: bytes) -> dict:
        meta = metadata.model_dump(mode="json")
        digest = hashlib.sha256(raw).hexdigest()
        fingerprint = hashlib.sha256((canonical(meta) + ":" + digest).encode()).hexdigest()
        existing = await self.pool.fetchrow("SELECT fingerprint FROM captures WHERE capture_id=$1", metadata.capture_id)
        if existing and existing["fingerprint"] != fingerprint:
            raise CaptureConflict("capture_id already identifies another capture")
        obj = await asyncio.to_thread(self.raw.write, raw)
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute(
                    "INSERT INTO raw_objects(sha256,relative_path,size_bytes) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                    obj.sha256, obj.relative_path, obj.size_bytes,
                )
                inserted = await con.fetchval(
                    """INSERT INTO captures(capture_id,fingerprint,raw_sha256,metadata,source_kind,
                       evidence_kind,endpoint,chain,received_at)
                       VALUES($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9)
                       ON CONFLICT(capture_id) DO NOTHING RETURNING capture_id""",
                    metadata.capture_id, fingerprint, obj.sha256, canonical(meta), meta["source_kind"],
                    meta["evidence_kind"], metadata.endpoint, metadata.chain, metadata.received_at,
                )
                actual = await con.fetchval("SELECT fingerprint FROM captures WHERE capture_id=$1", metadata.capture_id)
                if actual != fingerprint:
                    raise CaptureConflict("capture_id conflict")
                await con.execute("INSERT INTO outbox(capture_id) VALUES($1) ON CONFLICT DO NOTHING", metadata.capture_id)
        # This marker is obtained AFTER the transaction's commit is acknowledged.
        # A crash before it leaves the capture pending conservative reconciliation.
        marker = utcnow()
        await self.pool.execute("UPDATE captures SET available_at=$2 WHERE capture_id=$1 AND available_at IS NULL", metadata.capture_id, marker)
        return {"capture_id": str(metadata.capture_id), "raw_sha256": obj.sha256,
                "bytes": obj.size_bytes, "duplicate": inserted is None}

    async def get_capture(self, capture_id: UUID) -> dict | None:
        row = await self.pool.fetchrow("SELECT * FROM captures WHERE capture_id=$1", capture_id)
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        result.pop("fingerprint", None)
        runs = await self.pool.fetch("SELECT * FROM normalization_runs WHERE capture_id=$1 ORDER BY recorded_at", capture_id)
        result["runs"] = [dict(r) for r in runs]
        for run in result["runs"]:
            run["issues"] = json.loads(run["issues"])
            if run["analytics_receipt"]:
                run["analytics_receipt"] = json.loads(run["analytics_receipt"])
        return result

    async def record_normalization(self, capture_id: UUID, result) -> dict:
        version = result.normalizer_version
        batch_id = hashlib.sha256((str(capture_id) + ":" + version).encode()).hexdigest()
        facts = [f.model_dump(mode="json") for f in result.facts]
        fields = [f.model_dump(mode="json") for f in result.fields]
        issues = [i.model_dump(mode="json") for i in result.issues]
        content_hash = hashlib.sha256(canonical({"facts":facts,"fields":fields,"issues":issues}).encode()).hexdigest()
        capture = await self.pool.fetchrow("SELECT source_kind,evidence_kind FROM captures WHERE capture_id=$1", capture_id)
        async with self.pool.acquire() as con:
            async with con.transaction():
                added = await con.fetchval(
                    """INSERT INTO normalization_runs(capture_id,normalizer_version,batch_id,fact_count,field_count,issues,content_sha256)
                    VALUES($1,$2,$3,$4,$5,$6::jsonb,$7) ON CONFLICT DO NOTHING RETURNING batch_id""",
                    capture_id, version, batch_id, len(facts), len(fields), canonical(issues), content_hash,
                )
                actual_hash = await con.fetchval("SELECT content_sha256 FROM normalization_runs WHERE capture_id=$1 AND normalizer_version=$2",capture_id,version)
                if actual_hash != content_hash:
                    raise CaptureConflict("Normalizer version changed immutable output")
                if added:
                    for fact in facts:
                        numeric = None
                        if fact["value_type"] in ("decimal", "integer") and fact["value_text"] is not None and fact["quality"] in ("valid", "unverified"):
                            try:
                                number = Decimal(fact["value_text"])
                                if number.is_finite() and len(number.as_tuple().digits) <= 1000 and abs(number.adjusted()) <= 1000:
                                    numeric = number
                            except InvalidOperation:
                                pass
                        await con.execute(
                            """INSERT INTO facts(fact_id,capture_id,normalizer_version,source_kind,evidence_kind,
                            chain,token_address,metric,value_text,numeric_value,value_type,unit,category,occurred_at,
                            time_basis,pointer,quality,contract_version) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)""",
                            UUID(str(fact["fact_id"])), capture_id, version, capture["source_kind"], capture["evidence_kind"],
                            fact["chain"], fact["token_address"], fact["metric"], fact["value_text"], numeric,
                            fact["value_type"], fact["unit"], fact["category"],
                            datetime.fromisoformat(fact["occurred_at"].replace("Z","+00:00")), fact["time_basis"],
                            fact["pointer"], fact["quality"], fact["contract_version"],
                        )
                    if fields:
                        await con.executemany(
                            """INSERT INTO field_observations(capture_id,normalizer_version,pointer,category,value_type,quality,reason,contract_id)
                               VALUES($1,$2,$3,$4,$5,$6,$7,$8)""",
                            [(capture_id,version,f["pointer"],f["category"],f["value_type"],f["quality"],f["reason"],f.get("contract_id")) for f in fields],
                        )
                    from .normalize import FATAL_ISSUE_CODES, UNSUPPORTED_ISSUE_CODES
                    rejected = any(i["code"] in FATAL_ISSUE_CODES | UNSUPPORTED_ISSUE_CODES for i in issues)
                    state = "quarantined" if rejected else "ready"
                    await con.execute("UPDATE captures SET normalization_state=$2,last_error=NULL WHERE capture_id=$1", capture_id, state)
        # Serialize publication against temporal reads; no reader can see an
        # in-flight availability marker. The marker comes from the same DB clock.
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute("SELECT pg_advisory_xact_lock(60608003)")
                marker = await con.fetchval("SELECT clock_timestamp()")
                await con.execute("UPDATE captures SET available_at=COALESCE(available_at,$2) WHERE capture_id=$1",capture_id,marker)
                available = await con.fetchval(
                    """UPDATE normalization_runs SET available_at=COALESCE(available_at,$3)
                    WHERE capture_id=$1 AND normalizer_version=$2 RETURNING available_at""", capture_id,version,marker)
                await con.execute(
                    "UPDATE facts SET available_at=$3 WHERE capture_id=$1 AND normalizer_version=$2 AND available_at IS NULL",
                    capture_id, version, available)
                await con.execute("UPDATE outbox SET consumed_at=COALESCE(consumed_at,$2),last_error=NULL WHERE capture_id=$1", capture_id, marker)
        return {"batch_id":batch_id,"fact_count":len(facts),"field_count":len(fields),"issues":issues}

    async def batch_rows(self, batch_id: str) -> list[dict]:
        rows = await self.pool.fetch(
            """SELECT f.* FROM facts f JOIN normalization_runs r USING(capture_id,normalizer_version)
            WHERE r.batch_id=$1 AND f.available_at IS NOT NULL ORDER BY f.fact_id""", batch_id)
        result = []
        for row in rows:
            obj = dict(row)
            obj.pop("seq",None)
            obj.pop("numeric_value",None)
            for key,value in tuple(obj.items()):
                if isinstance(value,datetime):
                    obj[key]=value.isoformat()
                elif isinstance(value,UUID):
                    obj[key]=str(value)
            result.append(obj)
        return result

    async def mark_analytics(self, batch_id: str, receipt: dict):
        if not receipt.get("ok"):
            raise RuntimeError("Analytics did not verify both destinations")
        await self.pool.execute(
            "UPDATE normalization_runs SET analytics_applied_at=$2,analytics_receipt=$3::jsonb WHERE batch_id=$1",
            batch_id,utcnow(),canonical(receipt))

    async def status(self) -> dict:
        counts = await self.pool.fetch("SELECT source_kind,normalization_state,count(*) AS count FROM captures GROUP BY 1,2")
        queue = await self.pool.fetchrow(
            """SELECT count(*) FILTER(WHERE consumed_at IS NULL) AS pending,
            count(*) FILTER(WHERE consumed_at IS NULL AND published_at IS NULL) AS unpublished FROM outbox""")
        analytics_pending = await self.pool.fetchval("SELECT count(*) FROM normalization_runs WHERE analytics_applied_at IS NULL")
        health = await self.pool.fetch("SELECT worker_name,heartbeat_at,state,details FROM worker_health")
        return {"captures":[dict(r) for r in counts],"outbox":dict(queue),
                "analytics_pending":analytics_pending,"workers":[dict(r) for r in health]}

    async def heartbeat(self, state: str, details: dict | None = None):
        await self.pool.execute(
            """INSERT INTO worker_health(worker_name,heartbeat_at,state,details) VALUES('data-worker',$1,$2,$3::jsonb)
               ON CONFLICT(worker_name) DO UPDATE SET heartbeat_at=EXCLUDED.heartbeat_at,state=EXCLUDED.state,details=EXCLUDED.details""",
            utcnow(),state,canonical(details or {}))

    async def observations(self, chain: str, token: str, metric: str, start: datetime, end: datetime,
                           cutoff: datetime, source_kind: str, limit: int, after_seq: int = 0,
                           include_unverified: bool = False) -> list[dict]:
        rows = await self._temporal_fetch(cutoff,
            """SELECT * FROM facts WHERE chain=$1 AND token_address=$2 AND metric=$3
               AND occurred_at >= $4 AND occurred_at < $5 AND available_at <= $6 AND source_kind=$7
               AND seq > $8 AND (quality='valid' OR ($9 AND quality='unverified'))
               AND NOT EXISTS (SELECT 1 FROM normalization_runs newer
                 WHERE newer.capture_id=facts.capture_id AND newer.available_at <= $6
                 AND (newer.available_at,newer.normalizer_version) > (facts.available_at,facts.normalizer_version))
               ORDER BY seq LIMIT $10""",
            chain,token,metric,start,end,cutoff,source_kind,after_seq,include_unverified,limit)
        return [self._public_fact(dict(r)) for r in rows]

    async def _temporal_fetch(self, cutoff, sql, *args):
        async with self.pool.acquire() as con:
            async with con.transaction(isolation="read_committed"):
                await con.execute("SELECT pg_advisory_xact_lock_shared(60608003)")
                if cutoff > await con.fetchval("SELECT clock_timestamp()"):
                    raise FutureCutoff("knowledge_cutoff cannot be in the future")
                return await con.fetch(sql, *args)

    @staticmethod
    def _public_fact(row: dict) -> dict:
        # Decimal is serialized as text, never through the browser's Number.
        row.pop("numeric_value", None)
        return row

    async def as_known(self, chain: str, token: str, event_at: datetime, cutoff: datetime,
                       source_kind: str, include_unverified: bool = False) -> list[dict]:
        rows = await self._temporal_fetch(cutoff,
            """SELECT DISTINCT ON(metric,unit,contract_version) * FROM facts
            WHERE chain=$1 AND token_address=$2 AND occurred_at <= $3 AND available_at <= $4
            AND source_kind=$5 AND (quality='valid' OR ($6 AND quality='unverified'))
            AND NOT EXISTS (SELECT 1 FROM normalization_runs newer
              WHERE newer.capture_id=facts.capture_id AND newer.available_at <= $4
              AND (newer.available_at,newer.normalizer_version) > (facts.available_at,facts.normalizer_version))
            ORDER BY metric,unit,contract_version,occurred_at DESC,available_at DESC,seq DESC LIMIT 200""",
            chain,token,event_at,cutoff,source_kind,include_unverified)
        return [self._public_fact(dict(r)) for r in rows]

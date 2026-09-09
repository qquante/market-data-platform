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

    @staticmethod
    def _normalization_payload(result) -> tuple[dict, int]:
        from .contracts import NormalizationResult
        if not isinstance(result, NormalizationResult):
            raise TypeError("normalization_result_required")
        payload = {"facts": [f.model_dump(mode="json") for f in result.facts],
                   "fields": [f.model_dump(mode="json") for f in result.fields]}
        # Same exact UTF-8 scope as the normalizer: facts + fields envelopes.
        # Issues are immutable/hash-checked but not part of this admission budget.
        size = len(canonical(payload).encode("utf-8"))
        payload["issues"] = [i.model_dump(mode="json") for i in result.issues]
        return payload, size

    @staticmethod
    def normalization_size(result) -> int:
        """Exact group-planning bytes; do not estimate with Python string length.

        Sum this value for at most eight captures, <=16 MiB and <=10,000 facts.
        Each per-capture {facts,fields} envelope counts, including its punctuation.
        This preserves a single capture at the normalizer's exact 16 MiB limit;
        issues, numeric projections and SQL/COPY overhead are outside this budget.
        """
        return Store._normalization_payload(result)[1]

    async def record_normalization(self, capture_id: UUID, result) -> dict:
        """Compatibility wrapper; identical result and transaction semantics."""
        return (await self.record_normalizations([(capture_id, result)]))[0]

    async def record_normalizations(self, items: list[tuple[UUID, Any]]) -> list[dict]:
        """Persist 1..8 outputs atomically, then publish once behind the read barrier.

        An immutable-output/COPY conflict rolls back all new group content. A
        publication failure leaves committed but unpublished content for replay;
        outbox consumption and availability are committed together afterwards.
        """
        if not isinstance(items, list) or not 1 <= len(items) <= 8:
            raise ValueError("normalization_group_count_limit")
        capture_ids = []
        for item in items:
            if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], UUID):
                raise TypeError("normalization_group_item_invalid")
            capture_ids.append(item[0])
        if len(set(capture_ids)) != len(capture_ids):
            raise ValueError("normalization_group_duplicate_capture")
        prepared, total_bytes, total_facts = [], 0, 0
        for capture_id, result in items:
            payload, size = self._normalization_payload(result)
            total_bytes += size
            total_facts += len(payload["facts"])
            if total_bytes > 16 * 1024 * 1024:
                raise ValueError("normalization_group_output_size_limit")
            if total_facts > 10_000:
                raise ValueError("normalization_group_fact_count_limit")
            version = result.normalizer_version
            prepared.append({"capture_id": capture_id, "version": version,
                "batch_id": hashlib.sha256((str(capture_id) + ":" + version).encode()).hexdigest(),
                "content_hash": hashlib.sha256(canonical(payload).encode()).hexdigest(), **payload})
        versions = [p["version"] for p in prepared]
        from .normalize import FATAL_ISSUE_CODES, UNSUPPORTED_ISSUE_CODES
        async with self.pool.acquire() as con:
            async with con.transaction():
                captures = {r["capture_id"]: r for r in await con.fetch(
                    "SELECT capture_id,source_kind,evidence_kind FROM captures WHERE capture_id=ANY($1::uuid[])",
                    capture_ids)}
                if len(captures) != len(capture_ids):
                    raise CaptureConflict("Normalization capture is missing")
                # One statement creates all runs; returned IDs identify only new
                # outputs. Existing outputs must still match their immutable hash.
                added_rows = await con.fetch(
                    """INSERT INTO normalization_runs(capture_id,normalizer_version,batch_id,fact_count,field_count,issues,content_sha256)
                    SELECT capture_id,version,batch_id,fact_count,field_count,issues::jsonb,content_hash
                    FROM unnest($1::uuid[],$2::text[],$3::text[],$4::integer[],$5::integer[],$6::text[],$7::text[])
                    AS input(capture_id,version,batch_id,fact_count,field_count,issues,content_hash)
                    ON CONFLICT DO NOTHING RETURNING capture_id,normalizer_version""",
                    capture_ids, versions, [p["batch_id"] for p in prepared],
                    [len(p["facts"]) for p in prepared], [len(p["fields"]) for p in prepared],
                    [canonical(p["issues"]) for p in prepared], [p["content_hash"] for p in prepared])
                added = {(r["capture_id"], r["normalizer_version"]) for r in added_rows}
                actual = {(r["capture_id"], r["normalizer_version"]): r["content_sha256"] for r in await con.fetch(
                    """SELECT r.capture_id,r.normalizer_version,r.content_sha256 FROM normalization_runs r
                    JOIN unnest($1::uuid[],$2::text[]) AS input(capture_id,version)
                    ON r.capture_id=input.capture_id AND r.normalizer_version=input.version""", capture_ids, versions)}
                if any(actual.get((p["capture_id"], p["version"])) != p["content_hash"] for p in prepared):
                    raise CaptureConflict("Normalizer version changed immutable output")
                fact_records, field_records, state_ids, states = [], [], [], []
                for p in prepared:
                    capture_id, version = p["capture_id"], p["version"]
                    if (capture_id, version) not in added:
                        continue
                    capture = captures[capture_id]
                    for fact in p["facts"]:
                        numeric = None
                        if fact["value_type"] in ("decimal", "integer") and fact["value_text"] is not None and fact["quality"] in ("valid", "unverified"):
                            try:
                                number = Decimal(fact["value_text"])
                                if number.is_finite() and len(number.as_tuple().digits) <= 1000 and abs(number.adjusted()) <= 1000:
                                    numeric = number
                            except InvalidOperation:
                                pass
                        fact_records.append((
                            UUID(str(fact["fact_id"])), capture_id, version, capture["source_kind"], capture["evidence_kind"],
                            fact["chain"], fact["token_address"], fact["metric"], fact["value_text"], numeric,
                            fact["value_type"], fact["unit"], fact["category"],
                            datetime.fromisoformat(fact["occurred_at"].replace("Z", "+00:00")), fact["time_basis"],
                            fact["pointer"], fact["quality"], fact["contract_version"]))
                    field_records.extend((capture_id, version, f["pointer"], f["category"], f["value_type"],
                        f["quality"], f["reason"], f.get("contract_id")) for f in p["fields"])
                    rejected = any(i["code"] in FATAL_ISSUE_CODES | UNSUPPORTED_ISSUE_CODES for i in p["issues"])
                    state_ids.append(capture_id)
                    states.append("quarantined" if rejected else "ready")
                if fact_records:
                    await con.copy_records_to_table("facts", records=fact_records, columns=(
                        "fact_id", "capture_id", "normalizer_version", "source_kind", "evidence_kind",
                        "chain", "token_address", "metric", "value_text", "numeric_value", "value_type",
                        "unit", "category", "occurred_at", "time_basis", "pointer", "quality", "contract_version"))
                if field_records:
                    await con.copy_records_to_table("field_observations", records=field_records, columns=(
                        "capture_id", "normalizer_version", "pointer", "category", "value_type", "quality", "reason", "contract_id"))
                if state_ids:
                    await con.execute("""UPDATE captures c SET normalization_state=input.state,last_error=NULL
                        FROM unnest($1::uuid[],$2::text[]) AS input(capture_id,state)
                        WHERE c.capture_id=input.capture_id""", state_ids, states)
            # Group content is now committed. Writers take the exclusive side of
            # the barrier used by temporal readers; a shared writer lock is unsafe.
            async with con.transaction():
                await con.execute("SELECT pg_advisory_xact_lock(60608003)")
                marker = await con.fetchval("SELECT clock_timestamp()")
                await con.execute("UPDATE captures SET available_at=COALESCE(available_at,$2) WHERE capture_id=ANY($1::uuid[])",
                                  capture_ids, marker)
                published = await con.fetch("""UPDATE normalization_runs r SET available_at=COALESCE(r.available_at,$3)
                    FROM unnest($1::uuid[],$2::text[]) AS input(capture_id,version)
                    WHERE r.capture_id=input.capture_id AND r.normalizer_version=input.version
                    RETURNING r.capture_id,r.normalizer_version,r.available_at""", capture_ids, versions, marker)
                if len(published) != len(prepared):
                    raise CaptureConflict("Normalization group publication is incomplete")
                # Preserve each run's original marker on replay, including mixed
                # old/new groups. Already-published facts keep their own marker.
                await con.execute("""UPDATE facts f SET available_at=r.available_at FROM normalization_runs r,
                    unnest($1::uuid[],$2::text[]) AS input(capture_id,version)
                    WHERE f.capture_id=input.capture_id AND f.normalizer_version=input.version
                    AND r.capture_id=f.capture_id AND r.normalizer_version=f.normalizer_version
                    AND f.available_at IS NULL""", capture_ids, versions)
                await con.execute("UPDATE outbox SET consumed_at=COALESCE(consumed_at,$2),last_error=NULL WHERE capture_id=ANY($1::uuid[])",
                                  capture_ids, marker)
        return [{"batch_id":p["batch_id"], "fact_count":len(p["facts"]),
                 "field_count":len(p["fields"]), "issues":p["issues"]} for p in prepared]

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

    @staticmethod
    def _validate_analytics_receipt(batch_id: str, receipt: dict) -> None:
        import re
        from .contracts import utc_datetime
        from .analytics import SCHEMA_VERSION
        error = "Analytics receipt identity or verification contract is invalid"
        if (not isinstance(batch_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", batch_id)
                or not isinstance(receipt, dict) or receipt.get("ok") is not True
                or receipt.get("batch_id") != batch_id):
            raise RuntimeError(error)
        count, checksum, sources = receipt.get("count"), receipt.get("checksum"), receipt.get("source_counts")
        if (type(count) is not int or count < 0 or not isinstance(checksum, str)
                or not re.fullmatch(r"[a-f0-9]{64}", checksum) or not isinstance(sources, dict)
                or any(key not in {"fixture", "gmgn", "legacy_gmgn"} or type(value) is not int or value < 0
                       for key, value in sources.items()) or sum(sources.values()) != count):
            raise RuntimeError(error)
        try:
            frontier = receipt.get("available_frontier")
            if (count == 0) != (frontier is None):
                raise ValueError("frontier")
            frontier = utc_datetime(frontier) if frontier is not None else None
            schemas = []
            for name in ("lake", "clickhouse"):
                member = receipt.get(name)
                if (not isinstance(member, dict) or member.get("ok") is not True
                        or member.get("receipt_present") is not True or type(member.get("count")) is not int
                        or member["count"] != count or member.get("checksum") != checksum
                        or canonical(member.get("source_counts")) != canonical(sources)):
                    raise ValueError("sink summary")
                current = member.get("available_frontier")
                current = utc_datetime(current) if current is not None else None
                if current != frontier:
                    raise ValueError("sink frontier")
                schemas.append(member.get("schema_version"))
            if schemas != [SCHEMA_VERSION, SCHEMA_VERSION]:
                raise ValueError("schema")
            snapshot = receipt.get("snapshot")
            if (not isinstance(snapshot, dict) or snapshot.get("scope") != "batch_commit"
                    or type(snapshot.get("snapshot_id")) is not int or snapshot["snapshot_id"] < 0):
                raise ValueError("snapshot")
            utc_datetime(snapshot.get("snapshot_time"))
        except (ValueError, TypeError, OverflowError):
            raise RuntimeError(error) from None

    async def defer_analytics(self, batch_id: str, code: str) -> dict | None:
        """Schedule a published pending run for 1..60s later, using the DB clock.

        Store only an exception-class-shaped code, never exception text or body.
        Attempts survive eventual success; unavailable/already-applied runs are
        not moved back into the pending queue.
        """
        import re
        safe_code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", code) else "AnalyticsError"
        row = await self.pool.fetchrow("""UPDATE normalization_runs
            SET analytics_retry_at=statement_timestamp()
                + LEAST(60,power(2,LEAST(analytics_attempts,6)))::integer * interval '1 second',
                analytics_attempts=CASE WHEN analytics_attempts < 9223372036854775807 THEN analytics_attempts+1 ELSE analytics_attempts END,
                analytics_last_error=$2
            WHERE batch_id=$1 AND available_at IS NOT NULL AND analytics_applied_at IS NULL
            RETURNING batch_id,analytics_retry_at,analytics_attempts,analytics_last_error""", batch_id, safe_code)
        return dict(row) if row is not None else None

    async def mark_analytics(self, batch_id: str, receipt: dict):
        await self.mark_analytics_many({batch_id: receipt})

    async def mark_analytics_many(self, receipts: dict[str, dict]):
        """Confirm at most eight complete matching sink receipts atomically."""
        if not isinstance(receipts, dict) or not 1 <= len(receipts) <= 8:
            raise ValueError("analytics_receipt_group_count_limit")
        for batch_id, receipt in receipts.items():
            self._validate_analytics_receipt(batch_id, receipt)
        batch_ids = list(receipts)
        encoded = [canonical(receipts[batch_id]) for batch_id in batch_ids]
        marker = utcnow()
        async with self.pool.acquire() as con:
            async with con.transaction():
                updated = await con.fetch("""UPDATE normalization_runs r
                    SET analytics_applied_at=$3,analytics_receipt=input.receipt::jsonb,
                        analytics_retry_at=NULL,analytics_last_error=NULL
                    FROM unnest($1::text[],$2::text[]) AS input(batch_id,receipt)
                    WHERE r.batch_id=input.batch_id AND r.available_at IS NOT NULL
                    RETURNING r.batch_id,r.fact_count""", batch_ids, encoded, marker)
                if {row["batch_id"] for row in updated} != set(batch_ids):
                    raise CaptureConflict("Analytics receipt batch is missing or unpublished")
                if any(row["fact_count"] != receipts[row["batch_id"]]["count"] for row in updated):
                    raise CaptureConflict("Analytics receipt count differs from immutable run")

    async def status(self) -> dict:
        counts = await self.pool.fetch("SELECT source_kind,normalization_state,count(*) AS count FROM captures GROUP BY 1,2")
        queue = await self.pool.fetchrow(
            """SELECT count(*) FILTER(WHERE consumed_at IS NULL) AS pending,
            count(*) FILTER(WHERE consumed_at IS NULL AND published_at IS NULL) AS unpublished FROM outbox""")
        analytics_pending = await self.pool.fetchval("SELECT count(*) FROM normalization_runs WHERE analytics_applied_at IS NULL")
        analytics_failures = dict(await self.pool.fetchrow("""SELECT count(*) AS pending_count,
            min(analytics_retry_at) AS next_retry_at FROM normalization_runs
            WHERE available_at IS NOT NULL AND analytics_applied_at IS NULL AND analytics_last_error IS NOT NULL"""))
        analytics_failures["items"] = [dict(row) for row in await self.pool.fetch("""SELECT batch_id,capture_id,
            normalizer_version,analytics_retry_at,analytics_attempts,analytics_last_error FROM normalization_runs
            WHERE available_at IS NOT NULL AND analytics_applied_at IS NULL AND analytics_last_error IS NOT NULL
            ORDER BY recorded_at,batch_id LIMIT 100""")]
        analytics_failures["limit"] = 100
        health = await self.pool.fetch("SELECT worker_name,heartbeat_at,state,details FROM worker_health")
        bridges = await self.pool.fetch("""SELECT source_name,origin_at,heartbeat_at,state,last_error,
            accepted_count,unavailable_count,rejected_count,accepted_raw_bytes,progress,details
            FROM bridge_state WHERE source_name NOT LIKE 'fixture:%' ORDER BY source_name LIMIT 50""")
        return {"captures":[dict(r) for r in counts],"outbox":dict(queue),
                "analytics_pending":analytics_pending,"analytics_failures":analytics_failures,"workers":[dict(r) for r in health],
                "bridges":[dict(r) for r in bridges]}

    async def ingestion_status(self, limit: int = 50) -> dict:
        if not 1 <= limit <= 100:
            raise ValueError("ingestion_exception_limit")
        source = "gmgn-legacy-live-v1"
        row = await self.pool.fetchrow("""SELECT source_name,origin_at,state,heartbeat_at,last_error,
            accepted_count,unavailable_count,rejected_count,accepted_raw_bytes,progress,details,
            extract(epoch FROM clock_timestamp()-heartbeat_at) AS heartbeat_age_seconds
            FROM bridge_state WHERE source_name=$1""",source)
        if row is None:
            return {"state":"not_started","source_name":source,"exceptions":[]}
        result = dict(row)
        for key in ("progress","details"):
            result[key] = json.loads(result[key])
        exceptions = await self.pool.fetch("""
            WITH missing AS MATERIALIZED (
              SELECT capture_id,source_received_at,endpoint,source_status,state,reason,attempts,last_attempt_at
              FROM bridge_delivery WHERE source_name=$1 AND state='unavailable'
              ORDER BY source_received_at DESC LIMIT $2
            ), rejected AS MATERIALIZED (
              SELECT capture_id,source_received_at,endpoint,source_status,state,reason,attempts,last_attempt_at
              FROM bridge_delivery WHERE source_name=$1 AND state='rejected'
              ORDER BY source_received_at DESC LIMIT $2
            ) SELECT * FROM (SELECT * FROM missing UNION ALL SELECT * FROM rejected) e
              ORDER BY source_received_at DESC LIMIT $2""",source,limit)
        result["exceptions"] = [dict(e) for e in exceptions]
        result["exception_limit"] = limit
        result["discovered_calls"] = sum(result[k] for k in ("accepted_count","unavailable_count","rejected_count"))
        result["coverage_basis"] = "discovered source calls; revisited received_at interval, not closed commit-time coverage"
        result["accepted_meaning"] = "raw/API receipt; normalization and analytical readiness are separate"
        return result
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

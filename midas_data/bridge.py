"""Bounded read-only polling of legacy evidence, with durable replay and reconciliation.

This is NOT CDC. Coverage is received_at >= origin while the source retains rows.
A completed audit is a revisited interval, never a closed commit-time snapshot.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import signal
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg
import httpx
from pydantic import Field, StrictStr, ValidationError

from .contracts import utc_datetime
from .importer import (LegacyRow, ImportFailure, MAX_RAW_BYTES, MAX_COMPRESSED_BYTES,
                       verify_row, local_api_url, _request_json)
from .store import canonical

UTC = timezone.utc
PAGE_SIZE = 50
WINDOW = timedelta(seconds=60)
RECENT = timedelta(minutes=10)
LOCK_ID = 60608004
# Limit metadata in SQL before transferring it. Never select headers/error text.
EXTRA_PARAMS = frozenset({"wallet_address", "wallet_addresses", "period", "from", "to", "resolution",
    "capture_policy", "horizon_seconds", "hide_abnormal", "hide_airdrop", "hide_closed", "cursor", "offset", "page"})
SAFE_PARAMS = tuple(sorted({"chain", "address", "token_address", "interval", "limit", "order_by", "direction"} | EXTRA_PARAMS))
META_COLUMNS = """
call_id, endpoint, chain, requested_at_utc, received_at_utc, status, http_status,
response_sha256::text, payload_sha256::text,
CASE WHEN jsonb_typeof(request_params)='object' AND octet_length(request_params::text)<=32768 THEN
 jsonb_build_object(
  'params',(SELECT coalesce(jsonb_object_agg(p.key,p.value),'{}'::jsonb)
     FROM jsonb_each(request_params) p WHERE p.key=ANY($5::text[])),
  'query',(SELECT coalesce(jsonb_object_agg(p.key,p.value),'{}'::jsonb)
     FROM jsonb_each(CASE WHEN jsonb_typeof(request_params->'query')='object'
                       THEN request_params->'query' ELSE '{}'::jsonb END) p
     WHERE p.key=ANY($5::text[])),
  'body',(SELECT coalesce(jsonb_object_agg(p.key,p.value),'{}'::jsonb)
     FROM jsonb_each(CASE WHEN jsonb_typeof(request_params->'body')='object'
                       THEN request_params->'body' ELSE '{}'::jsonb END) p
     WHERE p.key=ANY($5::text[])))
 ELSE NULL END AS filtered_params
"""
# Each leg can use its existing partial time index. No blob JOIN before LIMIT.
PAGE_SQL = f"""
WITH s AS MATERIALIZED (
 SELECT {META_COLUMNS} FROM gmgn_api_calls
 WHERE status='success' AND received_at_utc >= $1 AND received_at_utc < $3
 AND ($2::uuid IS NULL OR (received_at_utc,call_id)>($1,$2))
 ORDER BY received_at_utc,call_id LIMIT $4
), e AS MATERIALIZED (
 SELECT {META_COLUMNS} FROM gmgn_api_calls
 WHERE status='error' AND received_at_utc >= $1 AND received_at_utc < $3
 AND ($2::uuid IS NULL OR (received_at_utc,call_id)>($1,$2))
 ORDER BY received_at_utc,call_id LIMIT $4
)
SELECT * FROM (SELECT * FROM s UNION ALL SELECT * FROM e) calls
ORDER BY received_at_utc,call_id LIMIT $4
"""
BLOB_SQL = """
SELECT codec,raw_bytes,octet_length(payload) AS compressed_bytes,
 CASE WHEN codec='zlib' AND raw_bytes BETWEEN 0 AND $2 AND octet_length(payload)<=$3
 THEN payload ELSE NULL END AS payload
FROM gmgn_payload_blobs WHERE payload_sha256=$1
"""


class RawLegacyRow(LegacyRow):
    """Same byte/clock contract; raw receipt does not require an implemented adapter."""
    endpoint: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    chain: StrictStr | None


def iso(value: datetime) -> str:
    return utc_datetime(value).isoformat()


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def initial_progress(origin: datetime) -> dict:
    return {"turn": 0, "forward_until": iso(origin), "lanes": {
        name: {"at": iso(origin), "after": None, "end": None, "cycle_end": None,
               "cycle_started_at": None, "cycles": 0, "last_cycle_at": None}
        for name in ("forward", "recent", "audit")}}


def prepare_lane(progress: dict, origin: datetime, now: datetime) -> tuple[str, dict] | None:
    """Mutate only the caller's candidate progress; save it after delivery succeeds."""
    name = ("forward", "recent", "audit")[progress["turn"] % 3]
    lane = progress["lanes"][name]
    if lane["end"] is not None:
        return name, lane
    upper = now - timedelta(seconds=2)
    if name != "forward":
        if lane["cycle_end"] is None:
            upper = utc_datetime(progress["forward_until"])
            lower = origin if name == "audit" else max(origin, upper - RECENT)
            if upper <= lower:
                return None
            lane.update(at=iso(lower), after=None, cycle_end=iso(upper),
                        cycle_started_at=iso(now))
        upper = utc_datetime(lane["cycle_end"])
    start = utc_datetime(lane["at"])
    if upper <= start:
        return None
    lane["end"] = iso(min(start + WINDOW, upper))
    return name, lane


def finish_page(progress: dict, name: str, rows: list[dict], now: datetime) -> None:
    lane = progress["lanes"][name]
    if len(rows) == PAGE_SIZE:
        last = rows[-1]
        lane.update(at=iso(last["received_at_utc"]), after=str(last["call_id"]))
    else:
        lane.update(at=lane["end"], after=None, end=None)
        if name == "forward":
            progress["forward_until"] = lane["at"]
        elif lane["at"] == lane["cycle_end"]:
            lane.update(cycles=lane["cycles"] + 1, last_cycle_at=iso(now),
                        cycle_end=None, cycle_started_at=None)
    progress["turn"] = (progress["turn"] + 1) % 3


class PgSource:
    def __init__(self, connection):
        self.connection = connection

    async def clock(self) -> datetime:
        return await self.connection.fetchval("SELECT clock_timestamp()")

    async def page(self, at: datetime, after: UUID | None, end: datetime) -> list[dict]:
        if not at < end or end - at > WINDOW:
            raise ValueError("source_window_limit")
        async with self.connection.transaction(readonly=True):
            rows = await self.connection.fetch(PAGE_SQL, at, after, end, PAGE_SIZE, list(SAFE_PARAMS))
        result = []
        for raw in rows:
            row = dict(raw)
            filtered = row.pop("filtered_params")
            if filtered is None:
                row["request_params"] = None
            else:
                params = json.loads(filtered)
                row["request_params"] = {**params["params"], "query": params["query"], "body": params["body"]}
            result.append(row)
        return result

    async def blob(self, sha: str) -> dict | None:
        async with self.connection.transaction(readonly=True):
            row = await self.connection.fetchrow(BLOB_SQL, sha, MAX_RAW_BYTES, MAX_COMPRESSED_BYTES)
        return dict(row) if row else None


@dataclass(frozen=True)
class BridgeConfig:
    source_name: str
    origin_at: datetime
    binding: str
    source_kind: str = "legacy_gmgn"

    def __post_init__(self):
        if self.source_kind not in {"legacy_gmgn", "fixture"}:
            raise ValueError("invalid_bridge_source_kind")
        if self.source_kind == "fixture" and not self.source_name.startswith("fixture:"):
            raise ValueError("fixture_bridge_name_required")
        utc_datetime(self.origin_at)

    @property
    def fingerprint(self):
        return digest({"protocol": "gmgn-bridge-v1", "source_kind": self.source_kind,
                       "origin": iso(self.origin_at), "binding": self.binding,
                       "statuses": ["success", "error"], "params": SAFE_PARAMS})


class Bridge:
    """Public processing interface; source and ACK transport are injectable for acceptance."""
    def __init__(self, config: BridgeConfig, database, source, deliver, admission):
        self.config, self.db, self.source, self.deliver = config, database, source, deliver
        self.admission = admission
        self.locked = False

    async def open(self):
        # A session lock prevents two cursors/writers for the production source.
        # Fixtures use a per-name lock and cannot compete with the active bridge.
        key = LOCK_ID if self.config.source_kind != "fixture" else int(
            hashlib.sha256(self.config.source_name.encode()).hexdigest()[:14], 16)
        if not await self.db.fetchval("SELECT pg_try_advisory_lock($1::bigint)", key):
            raise RuntimeError("bridge_already_running")
        self.locked = True
        now = await self.source.clock()
        if self.config.origin_at > now:
            raise ValueError("bridge_origin_in_future")
        await self.db.execute("""
            INSERT INTO bridge_state(source_name,origin_at,binding_sha256,progress)
            VALUES($1,$2,$3,$4::jsonb) ON CONFLICT DO NOTHING
            """, self.config.source_name, self.config.origin_at, self.config.fingerprint,
            canonical(initial_progress(self.config.origin_at)))
        row = await self.db.fetchrow("SELECT * FROM bridge_state WHERE source_name=$1", self.config.source_name)
        if row["binding_sha256"] != self.config.fingerprint:
            raise RuntimeError("bridge_binding_changed_restore_original_configuration")
        return self

    async def pulse(self, state: str, details: dict, error: str | None = None):
        await self.db.execute("""
            UPDATE bridge_state SET heartbeat_at=clock_timestamp(),state=$2,last_error=$3,details=$4::jsonb
            WHERE source_name=$1
            """, self.config.source_name, state, error, canonical(details))

    async def _receipt(self, row: dict, fingerprint: str, state: str, reason: str | None,
                       raw_sha: str | None = None, raw_bytes: int | None = None):
        async with self.db.transaction():
            old = await self.db.fetchrow(
                "SELECT state,raw_bytes,fingerprint FROM bridge_delivery WHERE source_name=$1 AND capture_id=$2",
                self.config.source_name, row["call_id"])
            previous = old["state"] if old else None
            # Original fingerprint remains immutable, including after a mutation exception.
            original = old["fingerprint"] if old else fingerprint
            await self.db.execute("""
                INSERT INTO bridge_delivery(source_name,capture_id,source_received_at,endpoint,source_status,
                    fingerprint,state,reason,raw_sha256,raw_bytes,ack_capture_id)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                ON CONFLICT(source_name,capture_id) DO UPDATE SET state=EXCLUDED.state,reason=EXCLUDED.reason,
                    raw_sha256=coalesce(EXCLUDED.raw_sha256,bridge_delivery.raw_sha256),
                    raw_bytes=coalesce(EXCLUDED.raw_bytes,bridge_delivery.raw_bytes),
                    ack_capture_id=coalesce(EXCLUDED.ack_capture_id,bridge_delivery.ack_capture_id),
                    last_attempt_at=clock_timestamp(),attempts=bridge_delivery.attempts+1
                """, self.config.source_name,row["call_id"],row["received_at_utc"],
                row["endpoint"][:256],row["status"],original,state,reason,raw_sha,raw_bytes,
                row["call_id"] if state == "accepted" else None)
            deltas = [int(state == s) - int(previous == s) for s in ("accepted", "unavailable", "rejected")]
            # Preserve the lifetime audit counter even when an accepted source is mutated.
            byte_delta = raw_bytes if state == "accepted" and (old is None or old["raw_bytes"] is None) else 0
            await self.db.execute("""
                UPDATE bridge_state SET accepted_count=accepted_count+$2,unavailable_count=unavailable_count+$3,
                 rejected_count=rejected_count+$4,accepted_raw_bytes=accepted_raw_bytes+$5 WHERE source_name=$1
                """, self.config.source_name,*deltas,byte_delta or 0)

    async def process_row(self, row: dict) -> str:
        identity = {key: (iso(value) if isinstance(value, datetime) else str(value) if isinstance(value, UUID) else value)
                    for key,value in row.items()}
        fingerprint = digest(identity)
        old = await self.db.fetchrow(
            "SELECT state,fingerprint FROM bridge_delivery WHERE source_name=$1 AND capture_id=$2",
            self.config.source_name,row["call_id"])
        if old and old["fingerprint"] != fingerprint:
            await self._receipt(row,fingerprint,"rejected","source_identity_changed")
            return "rejected"
        if old and old["state"] == "accepted":
            return "duplicate"
        if row["status"] == "error" or row["payload_sha256"] is None:
            reason = "source_error_without_body" if row["status"] == "error" else "source_body_not_retained"
            await self._receipt(row,fingerprint,"unavailable",reason)
            return "unavailable"
        try:
            blob = await self.source.blob(row["payload_sha256"])
            if blob is None:
                raise ImportFailure("source_blob_missing")
            if blob["payload"] is None:
                raise ImportFailure("source_blob_limit_or_codec")
            record = RawLegacyRow(
                export_version="gmgn-legacy-v1",call_id=row["call_id"],endpoint=row["endpoint"],
                chain=row["chain"],requested_at_utc=row["requested_at_utc"],received_at_utc=row["received_at_utc"],
                http_status=row["http_status"],request_params=row["request_params"],
                response_sha256=row["response_sha256"],payload_sha256=row["payload_sha256"],
                codec=blob["codec"],raw_bytes=blob["raw_bytes"],
                payload_base64=base64.b64encode(blob["payload"]).decode("ascii"))
            # Existing I/R capture IDs retain the original importer metadata contract.
            extended = row["endpoint"] not in {"token_info", "market_rank"}
            capture = verify_row(record, additional_allowed=EXTRA_PARAMS if extended else frozenset(),
                                 allow_lists=extended)
            if self.config.source_kind == "fixture":
                from dataclasses import replace
                capture = replace(capture,metadata=capture.metadata.model_copy(update={"source_kind":"fixture"}))
        except (ValidationError, ImportFailure) as exc:
            reason = exc.code if isinstance(exc,ImportFailure) else "source_metadata_invalid"
            await self._receipt(row,fingerprint,"rejected",reason)
            return "rejected"
        # Transport failure, 409 or uncertain ACK stops the page. It does NOT become
        # a successful checkpoint or permanent rejection; replay keeps the same ID.
        acknowledgement = await self.deliver(capture)
        if (not isinstance(acknowledgement,dict)
            or acknowledgement.get("capture_id") != str(capture.metadata.capture_id)
            or acknowledgement.get("raw_sha256") != capture.sha256
            or type(acknowledgement.get("bytes")) is not int
            or acknowledgement["bytes"] != len(capture.raw)
            or type(acknowledgement.get("duplicate")) is not bool):
            raise ImportFailure("capture_acknowledgement_mismatch")
        await self._receipt(row,fingerprint,"accepted",None,capture.sha256,len(capture.raw))
        return "accepted"

    async def capacity(self) -> dict:
        value = await self.admission()
        if (not isinstance(value,dict) or type(value.get("accepting")) is not bool
            or any(type(value.get(k)) is not int or value[k] < 0 for k in ("free_bytes","required_free_bytes"))
            or value.get("basis") != "storage_filesystems_free_space"
            or value["accepting"] != (value["free_bytes"] >= value["required_free_bytes"])):
            raise ValueError("invalid_capture_admission")
        return value

    async def pause_for_storage(self, capacity: dict) -> dict:
        await self.pulse("paused",capacity,"storage_backpressure")
        return {"state":"paused",**capacity}

    async def step(self) -> dict:
        if not self.locked or self.db.is_closed():
            raise RuntimeError("bridge_session_lost")
        state = await self.db.fetchrow("SELECT * FROM bridge_state WHERE source_name=$1",self.config.source_name)
        capacity = await self.capacity()
        if not capacity["accepting"]:
            return await self.pause_for_storage(capacity)
        progress = json.loads(state["progress"])
        now = await self.source.clock()
        prepared = prepare_lane(progress,self.config.origin_at,now)
        counts = {}
        if prepared is not None:
            name,lane = prepared
            rows = await self.source.page(utc_datetime(lane["at"]),
                UUID(lane["after"]) if lane["after"] else None,utc_datetime(lane["end"]))
            # Source interface invariants protect the checkpoint even for a faulty adapter.
            keys = [(r["received_at_utc"],r["call_id"]) for r in rows]
            lower = (utc_datetime(lane["at"]),UUID(lane["after"])) if lane["after"] else None
            if (len(rows)>PAGE_SIZE or keys != sorted(set(keys))
                or any(t<utc_datetime(lane["at"]) or t>=utc_datetime(lane["end"]) for t,_ in keys)
                or (lower is not None and any(key<=lower for key in keys))):
                raise ValueError("source_page_order_or_bounds")
            for row in rows:
                try:
                    result = await self.process_row(row)
                except ImportFailure as exc:
                    if exc.code == "api_http_503":
                        capacity = await self.capacity()
                        if not capacity["accepting"]:
                            return await self.pause_for_storage(capacity)
                    raise
                counts[result] = counts.get(result,0) + 1
            finish_page(progress,name,rows,now)
        else:
            name = ("forward","recent","audit")[progress["turn"]]
            progress["turn"] = (progress["turn"] + 1) % 3
        # Every delivered row has already committed its ACK or visible exception.
        # A failure here repeats the page; it never skips unrecorded rows.
        await self.db.execute("UPDATE bridge_state SET progress=$2::jsonb WHERE source_name=$1",
                              self.config.source_name,canonical(progress))
        rejected = await self.db.fetchval("SELECT rejected_count FROM bridge_state WHERE source_name=$1",self.config.source_name)
        info = {"lane":name,"page":counts,"source_clock":iso(now),
                "forward_lag_seconds":max(0,(now-utc_datetime(progress["forward_until"])).total_seconds()),
                "coverage":"received_at_from_origin_revisited_not_commit_complete",
                "rejected_count":rejected,"storage":capacity}
        await self.pulse("degraded" if rejected else "ready",info)
        return info


def validate_source_dsn(dsn: str):
    try:
        value = urlsplit(dsn)
        valid = (value.scheme == "postgresql" and value.hostname == "midas-fomo-postgres-1"
                 and value.port == 5432 and value.path == "/midas_fomo"
                 and value.username == "midas_data_reader" and value.password
                 and not value.query and not value.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("dedicated_readonly_legacy_dsn_required")


async def connect_destination():
    # Configuration is controlled by Compose; never print the DSN.
    return await asyncpg.connect(os.environ["DATA_DATABASE_DSN"],timeout=5,command_timeout=10,
        server_settings={"timezone":"UTC","application_name":"midas-data-bridge",
                         "statement_timeout":"10000","idle_in_transaction_session_timeout":"10000"})


def poll_delay(failures: int, info: dict) -> float:
    # Full pages may advance quickly; storage admission gates every page.
    # Backlog is durable and never gates capture. Paused work retries at 5s.
    if failures:
        return min(15,1+failures*2)
    if info.get("state") == "paused":
        return 5.0
    return 0.1 if sum(info.get("page",{}).values()) == PAGE_SIZE else 1.0


async def run():
    source_dsn = os.environ["DATA_LEGACY_DSN"]
    validate_source_dsn(source_dsn)
    config = BridgeConfig(source_name="gmgn-legacy-live-v1",
        origin_at=utc_datetime(os.environ["DATA_BRIDGE_START_AT"]),
        binding="midas-fomo-postgres-1:5432/midas_fomo/public/"+os.environ["DATA_LEGACY_SYSTEM_ID"])
    database = source_con = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(signum,stop.set)
    try:
        database = await connect_destination()
        source_con = await asyncpg.connect(source_dsn,timeout=5,command_timeout=6,
            server_settings={"timezone":"UTC","application_name":"midas-data-reader",
              "default_transaction_read_only":"on","statement_timeout":"5000",
              "lock_timeout":"1000","idle_in_transaction_session_timeout":"5000","work_mem":"4MB"})
        if await source_con.fetchval("SHOW transaction_read_only") != "on":
            raise RuntimeError("source_must_be_readonly")
        identity = await source_con.fetchval("SELECT system_identifier::text FROM pg_control_system()")
        if identity != os.environ["DATA_LEGACY_SYSTEM_ID"]:
            raise RuntimeError("legacy_cluster_identity_changed")
        elevated = await source_con.fetchval("""
            SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
            FROM pg_roles WHERE rolname=current_user""")
        if elevated:
            raise RuntimeError("legacy_reader_must_not_be_privileged")
        key = os.environ["DATA_API_KEY"]
        api = local_api_url(os.getenv("DATA_BRIDGE_API_URL","http://api:8791"))
        with httpx.Client(timeout=httpx.Timeout(15,connect=3),trust_env=False,follow_redirects=False) as client:
            async def admission():
                _,value = await asyncio.to_thread(_request_json,client,"GET",api+"/v1/admission",
                    {"authorization":"Bearer "+key})
                return value
            async def deliver(capture):
                status,ack = await asyncio.to_thread(_request_json,client,"POST",api+"/v1/captures",
                    {"authorization":"Bearer "+key,"x-midas-metadata":capture.metadata_header(),
                     "content-type":"application/octet-stream"},body=capture.raw)
                if status != 202:
                    raise ImportFailure("capture_http_status")
                return ack
            bridge = await Bridge(config,database,PgSource(source_con),deliver,admission).open()
            failures = 0
            while not stop.is_set():
                try:
                    info=await bridge.step()
                    failures = 0
                except Exception as exc:
                    failures += 1
                    code = exc.code if isinstance(exc,ImportFailure) else type(exc).__name__
                    await bridge.pulse("degraded",{"consecutive_failures":failures},code)
                    print("bridge_retry code="+code,flush=True)
                    if failures >= 5:
                        raise RuntimeError("bridge_reconnect_required") from None
                try:
                    await asyncio.wait_for(stop.wait(),timeout=poll_delay(failures,info if failures == 0 else {}))
                except asyncio.TimeoutError:
                    pass
    finally:
        if source_con:
            await source_con.close()
        if database:
            await database.close()


def bridge_healthy(row) -> bool:
    return bool(row and row["fresh"] and (row["state"] == "ready" or
        (row["state"] == "paused" and row["last_error"] == "storage_backpressure")))


async def status(health=False):
    con = await connect_destination()
    try:
        row = await con.fetchrow("""
            SELECT source_name,origin_at,state,heartbeat_at,last_error,accepted_count,unavailable_count,
             rejected_count,accepted_raw_bytes,progress,details,
             heartbeat_at>clock_timestamp()-interval '90 seconds' AS fresh
            FROM bridge_state WHERE source_name='gmgn-legacy-live-v1'""")
        if health:
            return 0 if bridge_healthy(row) else 1
        value = dict(row) if row else {"state":"not_started"}
        value.update(healthy=bridge_healthy(row),ready_for_capture=bool(row and row["fresh"] and row["state"] == "ready"))
        print(json.dumps(value,default=str,indent=2))
        return 0
    finally:
        await con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command",choices=["run","status","healthcheck"])
    args = parser.parse_args()
    try:
        if args.command == "run":
            asyncio.run(run())
            return 0
        return asyncio.run(status(args.command == "healthcheck"))
    except Exception as exc:
        print("bridge_failed type="+type(exc).__name__,flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

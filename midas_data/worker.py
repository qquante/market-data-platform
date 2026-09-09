from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from datetime import timedelta
from uuid import UUID

import nats
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import ConsumerConfig, DeliverPolicy, AckPolicy, RetentionPolicy, StorageType, StreamConfig, DiscardPolicy
from nats.js.errors import NotFoundError

from .analytics import AnalyticsSinks, BatchConflict, batch_size, MAX_BATCH_ROWS, MAX_BATCH_BYTES
from .contracts import CaptureMetadata
from .normalize import normalize_capture, NORMALIZER_VERSION
from .raw_store import RawStore
from .settings import Settings
from .store import Store, utcnow

log=logging.getLogger("midas-data")
MAX_GROUP_CAPTURES=8


async def ensure_stream(js):
    expected=StreamConfig(
        name="MIDAS_DATA",subjects=["midas.data.capture"],retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,num_replicas=1,max_bytes=5*1024**3,
        max_age=86400,discard=DiscardPolicy.NEW,duplicate_window=600,max_msg_size=64*1024,
    )
    try:
        info=await js.stream_info("MIDAS_DATA")
        if info.config.subjects!=expected.subjects or info.config.storage!=StorageType.FILE:
            raise RuntimeError("Existing stream contract incompatible")
    except NotFoundError:
        await js.add_stream(config=expected)


def group_would_overflow(count, rows, size, next_rows, next_size):
    # A single oversized item must reach the component's validator, not loop
    # indefinitely as an empty group. The normalizer already bounds its output.
    return bool(count and (count >= MAX_GROUP_CAPTURES or
                           rows+next_rows > MAX_BATCH_ROWS or size+next_size > MAX_BATCH_BYTES))


async def serve(settings: Settings):
    store=await Store(settings.database_dsn,RawStore(settings.raw_path,settings.max_capture_bytes)).open()
    sinks=AnalyticsSinks(settings.lake_catalog_dsn,settings.lake_path,settings.clickhouse_url,
                         settings.clickhouse_user,settings.clickhouse_password)
    stopping=asyncio.Event()
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(sig,stopping.set)
    connection=owner=None
    iteration_error=None
    metrics={}
    batches_since_maintenance=0

    owner_probe_lock=asyncio.Lock()

    async def check_owner():
        # asyncpg permits only one operation per physical connection. The
        # session remains the sole distributed owner for both bounded stages.
        async with owner_probe_lock:
            await owner.fetchval("SELECT 1")

    async def acknowledge(message):
        nonlocal iteration_error
        try:
            await message.ack_sync(timeout=5)
        except Exception as exc:
            # Facts are already committed. A transport ACK failure must not
            # rewrite their data-quality status; broker replay confirms the run.
            iteration_error=type(exc).__name__
            log.error("capture_ack_failed type=%s",iteration_error)
            try:
                await message.nak(delay=5)
            except Exception:
                pass

    async def failed_capture(capture_id, messages, exc):
        nonlocal iteration_error
        code=type(exc).__name__
        iteration_error=code
        log.error("normalization_failed type=%s capture_id=%s",code,capture_id)
        if capture_id:
            await store.pool.execute("UPDATE captures SET last_error=$2 WHERE capture_id=$1",capture_id,code)
            await store.pool.execute("UPDATE outbox SET last_error=$2 WHERE capture_id=$1",capture_id,code)
        for message in messages:
            try:
                await message.nak(delay=5)
            except Exception:
                pass

    async def persist_normalizations(group):
        nonlocal iteration_error
        if not group:
            return
        items=[(entry["capture_id"],entry["result"]) for entry in group]
        persist_started=time.monotonic()
        try:
            await check_owner()
            outputs=await store.record_normalizations(items)
            if len(outputs) != len(group):
                raise RuntimeError("Normalization group reply cardinality mismatch")
            completed=group
            metrics["normalization_groups"]+=1
        except Exception as exc:
            # The grouped transaction rolls back on a bad member. Isolate it
            # using the same immutable single-capture protocol so it cannot
            # prevent valid neighbors from publishing.
            iteration_error=type(exc).__name__
            metrics["normalization_group_fallbacks"]+=1
            log.error("normalization_group_failed type=%s size=%s",iteration_error,len(group))
            completed=[]
            for entry in group:
                try:
                    await check_owner()
                    await store.record_normalization(entry["capture_id"],entry["result"])
                    completed.append(entry)
                except Exception as single_exc:
                    await failed_capture(entry["capture_id"],entry["messages"],single_exc)
        metrics["normalization_persist_ms"]+=round((time.monotonic()-persist_started)*1000,2)
        for entry in completed:
            metrics["normalized"]+=1
            metrics["facts"]+=len(entry["result"].facts)
            metrics["fields"]+=len(entry["result"].fields)
            for message in entry["messages"]:
                await acknowledge(message)

    async def persist_analytics(group):
        nonlocal iteration_error,batches_since_maintenance
        if not group:
            return
        try:
            await check_owner()
            receipts=await asyncio.to_thread(sinks.apply_batches,group)
            if set(receipts) != {batch_id for batch_id,_ in group}:
                raise RuntimeError("Analytics group reply identity mismatch")
            await store.mark_analytics_many(receipts)
            metrics["analytics_groups"]+=1
            metrics["analytics_batches"]+=len(group)
            batches_since_maintenance+=len(group)
        except Exception as exc:
            iteration_error=type(exc).__name__
            metrics["analytics_group_fallbacks"]+=1
            log.error("analytics_group_failed type=%s size=%s",iteration_error,len(group))
            for batch_id,rows in group:
                try:
                    await check_owner()
                    receipt=await asyncio.to_thread(sinks.apply_batch,batch_id,rows)
                    await store.mark_analytics(batch_id,receipt)
                    metrics["analytics_batches"]+=1
                    batches_since_maintenance+=1
                except Exception as single_exc:
                    iteration_error=type(single_exc).__name__
                    log.error("analytics_failed type=%s batch_id=%s",iteration_error,batch_id)
                    await store.defer_analytics(batch_id,iteration_error)
                    await store.heartbeat("degraded",{"error_type":iteration_error,"batch_id":batch_id})
                    # A content conflict must not block valid neighbors; an
                    # unavailable sink stops retries to avoid a request storm.
                    if not isinstance(single_exc, (ValueError, BatchConflict)):
                        await asyncio.sleep(1)
                        break

    async def normalize_messages(messages):
        normalization_started=time.monotonic()
        metrics["messages"]=len(messages)
        group=[]
        pending_ids={}
        group_rows=group_bytes=0
        for message in messages:
            capture_id=None
            try:
                capture_id=UUID(json.loads(message.data)["capture_id"])
                if capture_id in pending_ids:
                    pending_ids[capture_id]["messages"].append(message)
                    metrics["replayed"]+=1
                    continue
                read_started=time.monotonic()
                capture=await store.get_capture(capture_id)
                metrics["capture_read_ms"]+=round((time.monotonic()-read_started)*1000,2)
                if capture is None:
                    raise RuntimeError("Event has no capture manifest")
                if any(run["normalizer_version"]==NORMALIZER_VERSION and run["available_at"] is not None for run in capture["runs"]):
                    await acknowledge(message)
                    metrics["replayed"]+=1
                    continue
                metadata=CaptureMetadata.model_validate(capture["metadata"])
                raw=await asyncio.to_thread(store.raw.read,capture["raw_sha256"])
                compute_started=time.monotonic()
                result=await asyncio.to_thread(normalize_capture,metadata,raw)
                metrics["normalization_compute_ms"]+=round((time.monotonic()-compute_started)*1000,2)
                envelope_started=time.monotonic()
                size=Store.normalization_size(result)
                metrics["normalization_envelope_ms"]+=round((time.monotonic()-envelope_started)*1000,2)
                if group_would_overflow(len(group),group_rows,group_bytes,len(result.facts),size):
                    await persist_normalizations(group)
                    group=[]
                    pending_ids={}
                    group_rows=group_bytes=0
                entry={"capture_id":capture_id,"result":result,"messages":[message]}
                group.append(entry)
                pending_ids[capture_id]=entry
                group_rows+=len(result.facts)
                group_bytes+=size
            except Exception as exc:
                await failed_capture(capture_id,[message],exc)
        await persist_normalizations(group)
        # Release output objects when this bounded normalization stage finishes.
        group.clear()
        pending_ids.clear()
        metrics["normalization_ms"]=round((time.monotonic()-normalization_started)*1000,2)


    async def apply_pending_batches():
        nonlocal iteration_error
        analytics_started=time.monotonic()
        batches=await store.pool.fetch(
            """SELECT batch_id FROM normalization_runs WHERE available_at IS NOT NULL
               AND analytics_applied_at IS NULL
               AND (analytics_retry_at IS NULL OR analytics_retry_at<=statement_timestamp())
               ORDER BY analytics_retry_at NULLS FIRST,recorded_at,batch_id LIMIT 8""")
        analytical_group=[]
        group_rows=group_bytes=0
        for batch in batches:
            try:
                rows=await store.batch_rows(batch["batch_id"])
                size=batch_size(rows)
                if group_would_overflow(len(analytical_group),group_rows,group_bytes,len(rows),size):
                    await persist_analytics(analytical_group)
                    analytical_group=[]
                    group_rows=group_bytes=0
                analytical_group.append((batch["batch_id"],rows))
                group_rows+=len(rows)
                group_bytes+=size
            except Exception as exc:
                iteration_error=type(exc).__name__
                log.error("analytics_prepare_failed type=%s batch_id=%s",iteration_error,batch["batch_id"])
                await store.defer_analytics(batch["batch_id"],iteration_error)
                if not isinstance(exc, (ValueError, BatchConflict)):
                    break
        await persist_analytics(analytical_group)
        analytical_group.clear()
        metrics["analytics_ms"]=round((time.monotonic()-analytics_started)*1000,2)

    try:
        owner=await store.pool.acquire()
        if not await owner.fetchval("SELECT pg_try_advisory_lock(60608002)"):
            raise RuntimeError("Another data worker owns the pipeline")
        connection=await nats.connect(settings.nats_url,connect_timeout=5,max_reconnect_attempts=5)
        js=connection.jetstream(timeout=10)
        await ensure_stream(js)
        await asyncio.to_thread(sinks.ensure_schema)
        consumer=await js.pull_subscribe(
            "midas.data.capture",durable="normalizer-v1",stream="MIDAS_DATA",
            config=ConsumerConfig(durable_name="normalizer-v1",ack_policy=AckPolicy.EXPLICIT,
                                  deliver_policy=DeliverPolicy.ALL,ack_wait=120,max_ack_pending=32,max_deliver=-1),
        )
        await store.heartbeat("ready")
        last_heartbeat=0.0
        maintenance=None
        maintenance_error=None
        while not stopping.is_set():
            iteration_error=None
            iteration_started=time.monotonic()
            metrics={key:0 for key in (
                "messages","normalized","replayed","facts","fields","analytics_batches",
                "normalization_groups","normalization_group_fallbacks","analytics_groups","analytics_group_fallbacks",
                "normalization_compute_ms","normalization_persist_ms","normalization_envelope_ms","capture_read_ms")}
            await check_owner()
            pending=await store.pool.fetch(
                """SELECT capture_id FROM outbox WHERE consumed_at IS NULL
                AND (published_at IS NULL OR published_at<$1) ORDER BY published_at NULLS FIRST LIMIT 16""",
                utcnow()-timedelta(seconds=15))
            published=[]
            for row in pending:
                capture_id=str(row["capture_id"])
                await js.publish("midas.data.capture",json.dumps({"capture_id":capture_id}).encode(),
                                 headers={"Nats-Msg-Id":capture_id})
                published.append(row["capture_id"])
            if published:
                # If this commit is uncertain, the same message IDs are retried.
                # Only positively acknowledged publications are recorded.
                async with store.pool.acquire() as con:
                    async with con.transaction():
                        marker=utcnow()
                        await con.executemany(
                            "UPDATE outbox SET published_at=$2,publish_count=publish_count+1,last_error=NULL WHERE capture_id=$1",
                            [(capture_id,marker) for capture_id in published])
            try:
                messages=await consumer.fetch(batch=MAX_GROUP_CAPTURES,timeout=1)
            except NatsTimeoutError:
                messages=[]
            # Separate resources can make progress concurrently. Both stages are
            # bounded and joined before maintenance, heartbeat or lock release.
            async with asyncio.TaskGroup() as stages:
                stages.create_task(normalize_messages(messages))
                stages.create_task(apply_pending_batches())
            if batches_since_maintenance >= 128 and not iteration_error:
                batches_since_maintenance=0
                maintenance_started=time.monotonic()
                try:
                    await check_owner()
                    maintenance=await asyncio.to_thread(sinks.compact_files,max_output_files=2,min_input_files=64)
                    maintenance_error=None
                except Exception as exc:
                    maintenance_error=type(exc).__name__
                    log.error("maintenance_failed type=%s",maintenance_error)
                metrics["maintenance_ms"]=round((time.monotonic()-maintenance_started)*1000,2)
            metrics["iteration_ms"]=round((time.monotonic()-iteration_started)*1000,2)
            if time.monotonic()-last_heartbeat>5:
                outstanding_errors=await store.pool.fetchval("""SELECT
                    (SELECT count(*) FROM captures WHERE last_error IS NOT NULL) +
                    (SELECT count(*) FROM normalization_runs WHERE analytics_applied_at IS NULL AND analytics_last_error IS NOT NULL)""")
                await store.heartbeat("degraded" if iteration_error or maintenance_error or outstanding_errors else "ready",
                                      {"error_type":iteration_error,"captures_with_error":outstanding_errors,"last_iteration":metrics,
                                       "maintenance_error":maintenance_error,"last_maintenance":maintenance})
                last_heartbeat=time.monotonic()
        await store.heartbeat("stopped")
    finally:
        try:
            if connection:
                await connection.close()
        finally:
            # Cancellation of to_thread does not stop its synchronous operation.
            # close takes the sink lock and drains it before owner release, even
            # when broker shutdown raises. Store cleanup is guaranteed as well.
            try:
                await asyncio.to_thread(sinks.close)
            finally:
                try:
                    if owner:
                        await store.pool.release(owner)
                finally:
                    await store.close()


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(serve(Settings.from_env()))

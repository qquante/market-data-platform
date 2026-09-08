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

from .analytics import AnalyticsSinks
from .contracts import CaptureMetadata
from .normalize import normalize_capture
from .raw_store import RawStore
from .settings import Settings
from .store import Store, utcnow

log=logging.getLogger("midas-data")


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


async def serve(settings: Settings):
    store=await Store(settings.database_dsn,RawStore(settings.raw_path,settings.max_capture_bytes)).open()
    sinks=AnalyticsSinks(settings.lake_catalog_dsn,settings.lake_path,settings.clickhouse_url,
                         settings.clickhouse_user,settings.clickhouse_password)
    stopping=asyncio.Event()
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(sig,stopping.set)
    connection=None
    owner=None
    try:
        # The session lock is a single-writer fence. All work stops on session loss.
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
        iteration_error=None
        while not stopping.is_set():
            iteration_error=None
            # Retention/broker rebuild can remove published messages: retry every
            # unconsumed capture, not merely outbox rows with published_at=NULL.
            await owner.fetchval("SELECT 1")
            pending=await store.pool.fetch(
                """SELECT capture_id FROM outbox WHERE consumed_at IS NULL
                AND (published_at IS NULL OR published_at<$1) ORDER BY published_at NULLS FIRST LIMIT 16""",
                utcnow()-timedelta(seconds=15))
            for row in pending:
                capture_id=str(row["capture_id"])
                await js.publish("midas.data.capture",json.dumps({"capture_id":capture_id}).encode(),
                                 headers={"Nats-Msg-Id":capture_id})
                await store.pool.execute(
                    "UPDATE outbox SET published_at=$2,publish_count=publish_count+1,last_error=NULL WHERE capture_id=$1",
                    row["capture_id"],utcnow())
            try:
                messages=await consumer.fetch(batch=8,timeout=1)
            except NatsTimeoutError:
                messages=[]
            for message in messages:
                capture_id=None
                try:
                    event=json.loads(message.data)
                    capture_id=UUID(event["capture_id"])
                    capture=await store.get_capture(capture_id)
                    if capture is None:
                        raise RuntimeError("Event has no capture manifest")
                    metadata=CaptureMetadata.model_validate(capture["metadata"])
                    raw=await asyncio.to_thread(store.raw.read,capture["raw_sha256"])
                    result=await asyncio.to_thread(normalize_capture,metadata,raw)
                    await owner.fetchval("SELECT 1")
                    await store.record_normalization(capture_id,result)
                    await message.ack_sync(timeout=5)
                except Exception as exc:
                    code=type(exc).__name__
                    iteration_error=code
                    log.error("normalization_failed type=%s capture_id=%s",code,capture_id)
                    if capture_id:
                        await store.pool.execute("UPDATE captures SET last_error=$2 WHERE capture_id=$1",capture_id,code)
                        await store.pool.execute("UPDATE outbox SET last_error=$2 WHERE capture_id=$1",capture_id,code)
                    await message.nak(delay=5)
            batches=await store.pool.fetch(
                """SELECT batch_id FROM normalization_runs WHERE available_at IS NOT NULL
                   AND analytics_applied_at IS NULL ORDER BY recorded_at LIMIT 8""")
            for batch in batches:
                try:
                    await owner.fetchval("SELECT 1")
                    rows=await store.batch_rows(batch["batch_id"])
                    await asyncio.to_thread(sinks.apply_batch,batch["batch_id"],rows)
                    receipt=await asyncio.to_thread(sinks.verify_batch,batch["batch_id"])
                    await store.mark_analytics(batch["batch_id"],receipt)
                except Exception as exc:
                    iteration_error=type(exc).__name__
                    log.error("analytics_failed type=%s batch_id=%s",type(exc).__name__,batch["batch_id"])
                    await store.heartbeat("degraded",{"error_type":type(exc).__name__,"batch_id":batch["batch_id"]})
                    await asyncio.sleep(1)
                    break
            if time.monotonic()-last_heartbeat>5:
                outstanding_errors=await store.pool.fetchval("SELECT count(*) FROM captures WHERE last_error IS NOT NULL")
                await store.heartbeat("degraded" if iteration_error or outstanding_errors else "ready",
                                      {"error_type":iteration_error,"captures_with_error":outstanding_errors})
                last_heartbeat=time.monotonic()
        await store.heartbeat("stopped")
    finally:
        if connection:
            await connection.close()
        await asyncio.to_thread(sinks.close)
        if owner:
            await store.pool.release(owner)
        await store.close()


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(serve(Settings.from_env()))

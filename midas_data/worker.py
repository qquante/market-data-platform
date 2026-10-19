from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from datetime import datetime, timedelta
from uuid import UUID

import nats
import asyncpg
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import ConsumerConfig, DeliverPolicy, AckPolicy, RetentionPolicy, StorageType, StreamConfig, DiscardPolicy
from nats.js.errors import NotFoundError

from .analytics import BatchConflict, batch_size, MAX_BATCH_ROWS, MAX_BATCH_BYTES, MAX_GROUP_ROWS, MAX_DELIVERY_CAPTURES
from .analytics_process import ProcessAnalyticsSinks
from .contracts import CaptureMetadata
from .normalize import normalize_capture, NORMALIZER_VERSION
from .raw_store import RawStore
from .settings import Settings, bounded_int
from .store import Store, utcnow
from .token_registry import TokenRegistry
from .holders import parse_holders, PARSER_VERSION as HOLDER_PARSER_VERSION
from .holder_store import HolderStore
from .research_jobs import ResearchJobs
from .resources import resource_snapshot

log=logging.getLogger("midas-data")
MAX_GROUP_CAPTURES=8
NACK_DIAGNOSTIC_TIMEOUT_SECONDS=2.0


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


def group_would_overflow(count, rows, size, next_rows, next_size, *, row_limit=MAX_BATCH_ROWS, capture_limit=MAX_GROUP_CAPTURES):
    # A single oversized item must reach the component's validator, not loop
    # indefinitely as an empty group. The normalizer already bounds its output.
    return bool(count and (count >= capture_limit or
                           rows+next_rows > row_limit or size+next_size > MAX_BATCH_BYTES))


def destination_retry_delay(target, error, consecutive):
    diagnostic=getattr(error,'diagnostic',{})
    if target!='lake' or diagnostic.get('stage')!='lake.query':return 0
    if diagnostic.get('causeType') not in {'InterruptException','InterruptedException','OutOfMemoryException','IOException'}:return 0
    return min(3600,600*2**min(max(0,int(consecutive)-1),3))


def destination_retry_state(saved,now):
    try:
        if isinstance(saved,str):saved=json.loads(saved)
        if not isinstance(saved,dict) or not isinstance(saved.get('retry_at'),str):return {}
        deadline=datetime.fromisoformat(saved['retry_at'])
        if deadline.tzinfo is None:return {}
        remaining=(deadline-now).total_seconds()
        if not 0<remaining<=3600:return {}
        consecutive=saved.get('consecutive_destination_failures',1)
        consecutive=max(1,min(100,consecutive)) if type(consecutive) is int else 1
        restored=dict(state='degraded',reason='destination_cooldown',retry_at=saved['retry_at'],
            retry_after_seconds=int(remaining),consecutive_destination_failures=consecutive)
        previous=saved.get('last_error')
        if isinstance(previous,dict) and previous.get('stage')=='lake.query':
            restored['last_error']={'type':'AnalyticsError','stage':'lake.query'}
            if previous.get('operation') in {'identity_check','read_batches','receipts','snapshots','insert','commit','rollback','begin','schema','maintenance','query'}:
                restored['last_error']['operation']=previous['operation']
            if previous.get('causeType') in {'InterruptException','InterruptedException','OutOfMemoryException','IOException'}:
                restored['last_error']['causeType']=previous['causeType']
        return restored
    except (ValueError,TypeError,OverflowError):return {}


class Pipeline:
    """Independent bounded loops sharing only durable state and ownership."""
    def __init__(self, settings, store, stop):
        from .delivery import DeliveryStore
        self.settings,self.store,self.stop=settings,store,stop
        self.republish_seconds=bounded_int('DATA_OUTBOX_REPUBLISH_SECONDS',15,15,3600)
        self.deliveries=DeliveryStore(store)
        self.registry=TokenRegistry(store)
        self.research=ResearchJobs(store)
        self.connection=self.js=self.consumer=None
        self.broker_lock=asyncio.Lock()
        self.sinks={target:ProcessAnalyticsSinks(settings.lake_catalog_dsn,settings.lake_path,
            settings.clickhouse_url,settings.clickhouse_user,settings.clickhouse_password,
            destination=target) for target in ("lake","clickhouse")}
        self.stages={name:{"state":"starting","completed":0,"failures":0,"fallbacks":0}
                     for name in ("relay","normalizer","lake","clickhouse","maintenance","registry","research")}
        self.lake_batches=0

    async def broker(self):
        async with self.broker_lock:
            if self.connection is None or self.connection.is_closed:
                self.connection=await nats.connect(self.settings.nats_url,connect_timeout=5,
                    max_reconnect_attempts=-1)
                self.js=self.connection.jetstream(timeout=10)
                self.consumer=None
            # A successful transport connection is not yet a ready consumer.
            if self.consumer is None:
                await ensure_stream(self.js)
                self.consumer=await self.js.pull_subscribe(
                    "midas.data.capture",durable="normalizer-v1",stream="MIDAS_DATA",
                    config=ConsumerConfig(durable_name="normalizer-v1",ack_policy=AckPolicy.EXPLICIT,
                        deliver_policy=DeliverPolicy.ALL,ack_wait=120,max_ack_pending=32,max_deliver=-1))
        return self.js,self.consumer

    async def _published_message_present(self, js, capture_id, sequence):
        try:
            message = await js.get_msg("MIDAS_DATA", seq=sequence)
        except NotFoundError:
            return False
        if message.subject != "midas.data.capture" or int(message.seq) != sequence:
            raise RuntimeError("Outbox publication identity mismatch")
        try:
            payload = json.loads(message.data)
        except (ValueError, TypeError):
            raise RuntimeError("Outbox publication payload mismatch") from None
        if not isinstance(payload, dict) or payload.get("capture_id") != capture_id:
            raise RuntimeError("Outbox publication payload mismatch")
        return True

    async def _publish_capture(self, js, capture_id, missing_sequence=None):
        # An expired/deleted sequence needs a fresh, deterministic message ID.
        # Uncertain acknowledgements retry that same ID, including after restart.
        message_id = capture_id if missing_sequence is None else f"{capture_id}:recovery:{missing_sequence}"
        for _ in range(2):
            ack = await js.publish("midas.data.capture", json.dumps({"capture_id": capture_id}).encode(),
                headers={"Nats-Msg-Id": message_id})
            if ack.stream != "MIDAS_DATA" or not isinstance(ack.seq, int) or isinstance(ack.seq, bool) or ack.seq <= 0:
                raise RuntimeError("Invalid outbox publication acknowledgement")
            if not ack.duplicate or await self._published_message_present(js, capture_id, ack.seq):
                return ack
            # A duplicate PubAck can refer to a deleted message still remembered
            # by the deduplication cache. Reconcile it instead of accepting loss.
            message_id = f"{capture_id}:recovery:{ack.seq}"
        raise RuntimeError("Outbox publication disappeared during reconciliation")

    async def relay(self):
        js, _ = await self.broker()
        self.stages['relay']['operation'] = 'publication_reconcile'
        # Published capture IDs are checked by their durable stream sequence.
        # Confirmed messages are never republished simply because time elapsed.
        pending = await self.store.pool.fetch("""WITH selected AS MATERIALIZED (
                SELECT capture_id,published_at FROM outbox
                WHERE consumed_at IS NULL AND (published_at IS NULL OR published_at<$1)
                ORDER BY published_at NULLS FIRST,capture_id LIMIT 16
            )
            SELECT o.capture_id,p.stream,p.sequence FROM selected o
            LEFT JOIN outbox_publications p USING(capture_id)
            ORDER BY o.published_at NULLS FIRST,o.capture_id""",
            utcnow()-timedelta(seconds=self.republish_seconds))
        for row in pending:
            cid = str(row["capture_id"])
            sequence = row["sequence"]
            if sequence is not None:
                if row["stream"] != "MIDAS_DATA":
                    raise RuntimeError("Outbox publication stream mismatch")
                self.stages['relay']['verification_checks'] = self.stages['relay'].get('verification_checks', 0)+1
                if await self._published_message_present(js, cid, sequence):
                    async with self.store.pool.acquire() as con:
                        async with con.transaction():
                            await con.execute("""UPDATE outbox_publications SET checked_at=clock_timestamp()
                                WHERE capture_id=$1 AND sequence=$2""", row["capture_id"], sequence)
                            # Keep the existing indexed outbox timestamp as the
                            # eligibility cursor. The receipt retains actual publish time.
                            await con.execute("""UPDATE outbox SET published_at=clock_timestamp()
                                WHERE capture_id=$1""", row["capture_id"])
                    continue
            self.stages['relay']['operation'] = 'publish'
            ack = await self._publish_capture(js, cid, sequence)
            # Persist PubAck before treating a publication as confirmed. A crash
            # before this transaction safely retries the same deterministic ID.
            async with self.store.pool.acquire() as con:
                async with con.transaction():
                    await con.execute("""INSERT INTO outbox_publications(capture_id,stream,sequence)
                        VALUES($1,$2,$3) ON CONFLICT(capture_id) DO UPDATE SET stream=excluded.stream,
                        sequence=excluded.sequence,published_at=clock_timestamp(),checked_at=clock_timestamp()""",
                        row["capture_id"], ack.stream, ack.seq)
                    await con.execute("""UPDATE outbox SET published_at=clock_timestamp(),
                        publish_count=publish_count+1,last_error=NULL WHERE capture_id=$1""", row["capture_id"])
            status = self.stages['relay']
            status['publish_confirmed'] = status.get('publish_confirmed', 0)+1
            status['duplicate_acks'] = status.get('duplicate_acks', 0)+int(bool(ack.duplicate))
            status['recovered'] = status.get('recovered', 0)+int(sequence is not None)
            status['last_puback'] = {'stream': ack.stream, 'sequence': ack.seq, 'duplicate': bool(ack.duplicate)}
        return len(pending)

    async def nack(self, entries, exc):
        # Recovery is independent of diagnostic writes to a pressured database.
        # Cancellation still propagates; durable broker/outbox state survives it.
        for entry in entries:
            for message in entry["messages"]:
                try: await message.nak(delay=5)
                except asyncio.CancelledError: raise
                except Exception: pass
        capture_ids=list(dict.fromkeys(entry.get("capture_id") for entry in entries
                                      if entry.get("capture_id") is not None))
        if not capture_ids: return
        try:
            # One total budget covers pool admission and both batched markers.
            async with asyncio.timeout(NACK_DIAGNOSTIC_TIMEOUT_SECONDS):
                code=type(exc).__name__
                await self.store.pool.execute(
                    "UPDATE captures SET last_error=$2 WHERE capture_id=ANY($1::uuid[])",capture_ids,code)
                await self.store.pool.execute(
                    "UPDATE outbox SET last_error=$2 WHERE capture_id=ANY($1::uuid[])",capture_ids,code)
        except asyncio.CancelledError: raise
        except Exception as diagnostic:
            log.warning("nack_marker_failed type=%s captures=%d",type(diagnostic).__name__,len(capture_ids))

    async def ack(self, messages):
        for message in messages:
            try: await message.ack_sync(timeout=5)
            except Exception:
                # Commit is already durable. ACK uncertainty never rewrites quality.
                try: await message.nak(delay=5)
                except Exception: pass

    async def persist(self, entries):
        if not entries: return 0
        self.stages['normalizer']['operation']='persist'
        try:
            await self.store.record_normalizations([(e["capture_id"],e["result"]) for e in entries])
        except (ValueError,TypeError) as exc:
            self.stages["normalizer"]["fallbacks"]+=1
            done=0
            for entry in entries:
                try: await self.store.record_normalization(entry["capture_id"],entry["result"])
                except Exception as single:
                    await self.nack([entry],single)
                    self.stages["normalizer"]["failures"]+=1
                else:
                    await self.ack(entry["messages"]);done+=1
            return done
        except Exception as exc:
            await self.nack(entries,exc)
            raise
        for entry in entries: await self.ack(entry["messages"])
        return len(entries)

    async def normalize(self):
        self.stages['normalizer']['operation']='broker'
        _,consumer=await self.broker()
        self.stages['normalizer']['operation']='fetch'
        try: messages=await consumer.fetch(batch=MAX_GROUP_CAPTURES,timeout=1)
        except (NatsTimeoutError, asyncio.TimeoutError):
            # nats-py can raise built-in TimeoutError when its remaining fetch
            # deadline expires. This catch must not cover broker/DB/commit I/O.
            stage=self.stages['normalizer']
            stage['fetch_timeouts']=stage.get('fetch_timeouts',0)+1
            return 0
        group=[];seen={};count=rows=size=fields=0
        for message in messages:
            cid=None
            try:
                cid=UUID(json.loads(message.data)["capture_id"])
                if cid in seen:
                    seen[cid]["messages"].append(message);continue
                self.stages['normalizer']['operation']='read_manifest'
                capture=await self.store.get_capture(cid)
                if capture is None: raise ValueError("Event has no manifest")
                version = HOLDER_PARSER_VERSION if capture["metadata"]["endpoint"] == "token_top_holders" else NORMALIZER_VERSION
                if any(r["normalizer_version"]==version and r["available_at"] is not None for r in capture["runs"]):
                    await self.ack([message]);continue
                metadata=CaptureMetadata.model_validate(capture["metadata"])
                self.stages['normalizer']['operation']='read_raw'
                raw=await asyncio.to_thread(self.store.raw.read,capture["raw_sha256"])
                self.stages['normalizer']['operation']='normalize'
                if metadata.endpoint == 'token_top_holders':
                    holder_result = await asyncio.to_thread(parse_holders, metadata, raw)
                    await HolderStore(self.store).record(holder_result)
                    await self.ack([message]); count += 1
                    continue
                result=await asyncio.to_thread(normalize_capture,metadata,raw)
                nbytes=Store.normalization_size(result)
                # Keep the field-count work budget even with compressed packets;
                # an indivisible capture still uses its normalizer contract.
                if group_would_overflow(len(group),rows,size,len(result.facts),nbytes) or (group and fields+len(result.fields)>10000):
                    previous=group;group=[];seen={};rows=size=fields=0
                    count+=await self.persist(previous)
                entry={"capture_id":cid,"result":result,"messages":[message]}
                group.append(entry);seen[cid]=entry
                rows+=len(result.facts);size+=nbytes;fields+=len(result.fields)
            except Exception as exc:
                await self.nack([{"capture_id":cid,"messages":[message]}],exc)
                self.stages["normalizer"]["failures"]+=1
                self.stages["normalizer"]["last_error"]={"type":type(exc).__name__,
                    "operation":self.stages['normalizer'].get('operation')}
        count+=await self.persist(group)
        return count

    async def apply(self, target, group):
        if not group: return 0
        try:
            receipts=await asyncio.to_thread(self.sinks[target].apply_destination,group)
            if set(receipts)!={b for b,_ in group}: raise ValueError("Receipt cardinality mismatch")
            await self.deliveries.confirm(target,receipts)
            done=len(group)
        except (ValueError,TypeError,BatchConflict):
            self.stages[target]["fallbacks"]+=1
            done=0
            for batch,rows in group:
                try:
                    receipt=await asyncio.to_thread(self.sinks[target].apply_destination,[(batch,rows)])
                    await self.deliveries.confirm(target,receipt);done+=1
                except Exception as exc:
                    await self.deliveries.defer(target,batch,type(exc).__name__)
                    self.stages[target]["failures"]+=1
                    self.stages[target]["last_error"]=getattr(exc,"diagnostic",{"type":type(exc).__name__})
                    # Resource failures still reach the destination circuit breaker
                    # after a poison member caused individual isolation.
                    if destination_retry_delay(target,exc,1):raise
                    if not isinstance(exc,(ValueError,TypeError,BatchConflict)): break
        except Exception as exc:
            for batch,_ in group: await self.deliveries.defer(target,batch,type(exc).__name__)
            raise
        if target=="lake": self.lake_batches+=done
        return done

    async def deliver(self, target):
        batches=await self.deliveries.pending(target)
        group=[];rows=size=count=0
        for batch in batches:
            # Unsubmitted batches remain durable for the next owner.
            if self.stop.is_set():return count
            try:
                content=await self.store.batch_rows(batch["batch_id"])
                nbytes=batch_size(content)
            except Exception as exc:
                await self.deliveries.defer(target,batch["batch_id"],type(exc).__name__)
                self.stages[target]["failures"]+=1
                continue
            if group_would_overflow(len(group),rows,size,len(content),nbytes,row_limit=MAX_GROUP_ROWS,capture_limit=MAX_DELIVERY_CAPTURES):
                count+=await self.apply(target,group);group=[];rows=size=0
            group.append((batch["batch_id"],content));rows+=len(content);size+=nbytes
        return count if self.stop.is_set() else count+await self.apply(target,group)

    async def maintenance(self):
        if self.lake_batches<128: return 0
        # Only the Lake instance's lock is held. Other stages do not join it.
        processed=self.lake_batches
        result=await asyncio.to_thread(self.sinks["lake"].compact_files,max_output_files=2,min_input_files=64)
        self.lake_batches-=processed
        self.stages["maintenance"]["last_result"]=result
        return 1

    async def pause(self, seconds):
        try: await asyncio.wait_for(self.stop.wait(),timeout=seconds)
        except TimeoutError: pass

    async def run_stage(self, name, operation, idle=0.5):
        status=self.stages[name]
        restored=name!='lake'
        while not self.stop.is_set():
            if not restored:
                try:
                    saved=await self.store.pool.fetchval("SELECT details FROM worker_health WHERE worker_name=$1",'data-'+name)
                    status.update(destination_retry_state(saved,utcnow()))
                    restored=True
                except Exception:
                    status.update(state='degraded',reason='retry_state_unavailable')
                    await self.pause(2);continue
            retry_at=status.get('retry_at')
            if retry_at:
                remaining=(datetime.fromisoformat(retry_at)-utcnow()).total_seconds()
                if remaining>0:
                    status.update(state='degraded',reason='destination_cooldown')
                    await self.pause(min(30,remaining));continue
                status.pop('retry_at',None);status.pop('retry_after_seconds',None)
            started=time.monotonic()
            try:
                capacity=await asyncio.to_thread(self.store.raw.admission)
                if not capacity["accepting"]:
                    status.update(state="paused",reason="storage_backpressure")
                    await self.pause(1);continue
                status.pop("reason",None)
                status["inflight_since"]=utcnow().isoformat()
                failures=status["failures"]
                done=await operation()
                status["completed"]+=done
                status["last_duration_ms"]=round((time.monotonic()-started)*1000,2)
                status["last_progress_at"]=utcnow().isoformat() if done else status.get("last_progress_at")
                status["state"]="ready" if status["failures"]==failures else "degraded"
                if status["state"]=="ready":
                    status.pop("last_error",None)
                    status.pop('retry_after_seconds',None)
                    status['consecutive_destination_failures']=0
                delay=0 if done else idle
            except Exception as exc:
                status["state"]="degraded";status["failures"]+=1
                diagnostic=getattr(exc,"diagnostic",{"type":type(exc).__name__})
                status["last_error"]={**diagnostic,
                    "operation":diagnostic.get('operation') or status.get('operation'),
                    "stage_operation":status.get('operation'),"duration_ms":round((time.monotonic()-started)*1000,2)}
                log.error("stage_failed stage=%s diagnostic=%s",name,json.dumps(status["last_error"]))
                consecutive=status.get('consecutive_destination_failures',0)+1
                cooldown=destination_retry_delay(name,exc,consecutive)
                if cooldown:
                    status.update(reason='destination_cooldown',consecutive_destination_failures=consecutive,
                        retry_after_seconds=cooldown,retry_at=(utcnow()+timedelta(seconds=cooldown)).isoformat())
                delay=min(30,cooldown) if cooldown else 2
            finally:status.pop("inflight_since",None)
            await self.pause(delay)

    async def watch(self, owner):
        while not self.stop.is_set():
            # Ownership uncertainty still aborts every stage. A failed telemetry
            # write instead expires readiness and retries without dropping work.
            await owner.fetchval("SELECT 1")
            try:
                for name,details in self.stages.items():
                    await self.deliveries.heartbeat(name,details["state"],details)
                states={s["state"] for s in self.stages.values()}
                state="degraded" if "degraded" in states else "paused" if "paused" in states else "ready"
                await self.store.heartbeat(state,{"reason":"storage_backpressure" if state=="paused" else None,
                    "stages":self.stages,"liveness":True,"resources":await asyncio.to_thread(resource_snapshot)})
            except (TimeoutError, asyncpg.QueryCanceledError, asyncpg.PostgresConnectionError) as exc:
                log.error("heartbeat_write_failed type=%s", type(exc).__name__)
            await self.pause(5)

    async def run(self, owner):
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self.run_stage("relay",self.relay))
            tasks.create_task(self.run_stage("normalizer",self.normalize))
            tasks.create_task(self.run_stage("lake",lambda:self.deliver("lake")))
            tasks.create_task(self.run_stage("clickhouse",lambda:self.deliver("clickhouse")))
            tasks.create_task(self.run_stage("maintenance",self.maintenance,idle=5))
            tasks.create_task(self.run_stage("registry",self.registry.step,idle=2))
            tasks.create_task(self.run_stage("research",self.research.step,idle=5))
            tasks.create_task(self.watch(owner))

    async def close(self):
        # Join both destination processes before releasing PostgreSQL ownership.
        try:
            results=await asyncio.gather(*(asyncio.to_thread(s.close) for s in self.sinks.values()),return_exceptions=True)
            for result in results:
                if isinstance(result,BaseException):raise result
        finally:
            if self.connection is not None: await self.connection.close()


async def serve(settings: Settings):
    store=await Store(settings.database_dsn,RawStore(settings.raw_path,settings.max_capture_bytes,
        min_free_bytes=settings.raw_min_free_bytes,host_capacity_path=settings.raw_host_capacity_path)).open()
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
    pipeline=Pipeline(settings,store,stop);owner=None
    try:
        owner=await store.pool.acquire()
        if not await owner.fetchval("SELECT pg_try_advisory_lock(60608002)"):
            raise RuntimeError("Another data worker owns the pipeline")
        await pipeline.run(owner)
        await store.heartbeat("stopped")
    finally:
        stop.set()
        try: await pipeline.close()
        finally:
            try:
                if owner is not None: await store.pool.release(owner)
            finally: await store.close()


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s midas-data %(message)s")
    asyncio.run(serve(Settings.from_env()))


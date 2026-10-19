"""Durable independent destination progress. No provider or trading dependencies."""
from __future__ import annotations
import json
import re
from .store import canonical, CaptureConflict
from .analytics import MAX_DELIVERY_CAPTURES

DESTINATIONS = ("lake", "clickhouse")
SUMMARY_KEYS = ("count", "checksum", "source_counts", "available_frontier")


def combined_receipt(batch, lake, clickhouse):
    if lake.get("destination") != "lake" or clickhouse.get("destination") != "clickhouse":
        raise CaptureConflict("Wrong destination receipt")
    for member in (lake, clickhouse):
        if member.get("batch_id") != batch:
            raise CaptureConflict("Wrong batch receipt")
    if any(lake.get(key) != clickhouse.get(key) for key in SUMMARY_KEYS):
        raise CaptureConflict("Destination receipts disagree")
    return dict({k: lake[k] for k in SUMMARY_KEYS}, batch_id=batch, ok=True,
                lake=lake, clickhouse=clickhouse, snapshot=lake.get("snapshot"))


def validate_destination(batch, target, receipt):
    from .store import Store
    if target not in DESTINATIONS or not isinstance(receipt, dict) or receipt.get("destination") != target:
        raise CaptureConflict("Wrong destination receipt")
    # Reuse the exact summary/type validator; the synthetic peer is solely a
    # validation scaffold, never persisted or used as a delivery acknowledgment.
    lake = dict(receipt, destination="lake")
    if target == "clickhouse":
        lake["snapshot"] = {"scope":"batch_commit", "snapshot_id":0,
                            "snapshot_time":"2000-01-01T00:00:00Z"}
    other = dict(receipt, destination="clickhouse")
    Store._validate_analytics_receipt(batch, combined_receipt(batch, lake, other))


class DeliveryStore:
    def __init__(self, store):
        self.store = store
        self.pool = store.pool

    async def pending(self, target):
        if target not in DESTINATIONS:
            raise ValueError("invalid destination")
        # Eligibility still respects backoff; new arrivals cannot starve an older due retry.
        return await self.pool.fetch("""SELECT d.batch_id FROM analytics_deliveries d
            WHERE destination=$1 AND applied_at IS NULL
              AND (retry_at IS NULL OR retry_at<=statement_timestamp())
            ORDER BY created_at,batch_id LIMIT 32""", target)

    async def defer(self, target, batch, code):
        if target not in DESTINATIONS:
            raise ValueError("invalid destination")
        safe = code if isinstance(code,str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}",code) else "AnalyticsError"
        await self.pool.execute("""UPDATE analytics_deliveries SET
            retry_at=statement_timestamp()+least(60,power(2,least(attempts,6)))::integer*interval '1 second',
            attempts=least(attempts,9223372036854775806)+1,last_error=$3
            WHERE destination=$1 AND batch_id=$2 AND applied_at IS NULL""",target,batch,safe)

    async def confirm(self, target, receipts):
        if not isinstance(receipts,dict) or not 1<=len(receipts)<=MAX_DELIVERY_CAPTURES:
            raise ValueError("destination_receipt_group_limit")
        for batch, receipt in receipts.items():
            validate_destination(batch,target,receipt)
        ids=sorted(receipts)
        async with self.pool.acquire() as con:
            async with con.transaction():
                # Serialize completion only; never hold this lock during network I/O.
                runs=await con.fetch("""SELECT batch_id,fact_count,available_at FROM normalization_runs
                    WHERE batch_id=ANY($1::text[]) ORDER BY batch_id FOR UPDATE""",ids)
                if len(runs)!=len(ids) or any(r["available_at"] is None or r["fact_count"]!=receipts[r["batch_id"]]["count"] for r in runs):
                    raise CaptureConflict("Receipt does not match published run")
                old=await con.fetch("""SELECT batch_id,receipt FROM analytics_deliveries
                    WHERE destination=$1 AND batch_id=ANY($2::text[]) AND applied_at IS NOT NULL""",target,ids)
                for r in old:
                    if json.loads(r["receipt"]) != receipts[r["batch_id"]]:
                        raise CaptureConflict("Immutable destination receipt differs")
                updated=await con.fetch("""UPDATE analytics_deliveries d SET
                    applied_at=COALESCE(applied_at,clock_timestamp()),receipt=x.receipt::jsonb,
                    retry_at=NULL,last_error=NULL
                    FROM unnest($2::text[],$3::text[]) AS x(batch_id,receipt)
                    WHERE d.destination=$1 AND d.batch_id=x.batch_id RETURNING d.batch_id""",
                    target,ids,[canonical(receipts[b]) for b in ids])
                if len(updated)!=len(ids):
                    raise CaptureConflict("Missing destination work record")
                finished=await con.fetch("""SELECT l.batch_id,l.receipt AS lake,c.receipt AS clickhouse
                    FROM analytics_deliveries l JOIN analytics_deliveries c USING(batch_id)
                    WHERE l.destination='lake' AND c.destination='clickhouse'
                      AND l.applied_at IS NOT NULL AND c.applied_at IS NOT NULL
                      AND l.batch_id=ANY($1::text[])""",ids)
                completed_ids=[]
                completed_receipts=[]
                for r in finished:
                    joined=combined_receipt(r["batch_id"],json.loads(r["lake"]),json.loads(r["clickhouse"]))
                    self.store._validate_analytics_receipt(r["batch_id"],joined)
                    completed_ids.append(r["batch_id"])
                    completed_receipts.append(canonical(joined))
                if completed_ids:
                    # All rows are already locked in batch order. Validate every
                    # receipt first, then finish the group in the same transaction.
                    await con.execute("""UPDATE normalization_runs r SET
                        analytics_applied_at=COALESCE(r.analytics_applied_at,clock_timestamp()),
                        analytics_receipt=x.receipt::jsonb,analytics_retry_at=NULL,analytics_last_error=NULL
                        FROM unnest($1::text[],$2::text[]) AS x(batch_id,receipt)
                        WHERE r.batch_id=x.batch_id""",completed_ids,completed_receipts)

    async def heartbeat(self, name, state, details):
        await self.pool.execute("""INSERT INTO worker_health(worker_name,heartbeat_at,state,details)
            VALUES($1,clock_timestamp(),$2,$3::jsonb) ON CONFLICT(worker_name) DO UPDATE SET
            heartbeat_at=EXCLUDED.heartbeat_at,state=EXCLUDED.state,details=EXCLUDED.details""",
            "data-"+name,state,canonical(details))

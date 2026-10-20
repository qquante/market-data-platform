"""Read-only restore reconciliation, injected by the host into the pinned image.

Plans are captured before rebuild. Only previously published, undelivered PG
batches may add Lake data. Original physical rows, receipts and snapshot metadata
must remain identical. No schema creation, writes, or provider access.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID

def require(condition, reason):
    if not condition:
        raise ValueError(reason)

def require_same(expected, actual, label):
    require(expected == actual, label + "_changed")

def fingerprint_rows(rows):
    digest, count = hashlib.sha256(), 0
    for row in rows:
        digest.update(json.dumps(row, default=str, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}

def validate_added(eligible, added):
    require(len(set(eligible)) == len(eligible), "duplicate_eligible_batch")
    require(len(set(added)) == len(added), "duplicate_added_receipt")
    require(set(added) <= set(eligible), "unapproved_added_batch")
    return set(added)

def check_batch(*, expected, pg_rows, lake_rows, receipts, run, baseline_snapshot, added):
    from midas_data.analytics import _prepare, _row, _row_hash, _receipt_matches, _utc
    batch = expected["batch_id"]
    require(run is not None and run["available_at"] is not None, "missing_published_run")
    require(type(expected["fact_count"]) is int and expected["fact_count"] >= 0, "invalid_planned_count")
    require(run["fact_count"] == expected["fact_count"], "published_count_changed")
    require_same(expected["available_at"], _utc(run["available_at"]), "publication_time")
    normalized, summary = _prepare(pg_rows)
    require(summary["count"] == expected["fact_count"], "pg_fact_count_changed")
    actual = []
    for record in lake_rows:
        value = dict(record)
        digest = value.pop("row_hash")
        row = _row(value)
        require(digest == _row_hash(row), "invalid_lake_row_hash")
        actual.append(row)
    actual, actual_summary = _prepare(actual, stored=True)
    require(len(lake_rows) == len(actual), "duplicate_physical_fact")
    require_same(normalized, actual, "lake_vs_pg_content")
    require_same(summary, actual_summary, "lake_vs_pg_summary")
    require(len(receipts) == 1 and receipts[0]["batch_id"] == batch, "missing_or_duplicate_receipt")
    require(_receipt_matches(receipts[0], summary), "lake_receipt_mismatch")
    require(run["analytics_applied_at"] is not None, "delivery_not_confirmed")
    ack = run["analytics_receipt"]
    if isinstance(ack, str):
        ack = json.loads(ack)
    require(isinstance(ack, dict) and ack.get("ok") is True and ack.get("batch_id") == batch,
            "wrong_delivery_receipt")
    keys = ("count", "checksum", "source_counts", "available_frontier", "schema_version")
    for result in (ack, ack.get("lake", {}), ack.get("clickhouse", {})):
        require(result.get("ok") is True, "unconfirmed_destination")
        # Top-level receipts do not carry schema_version in older pinned images.
        compare = keys if result is not ack else keys[:-1]
        require(all(result.get(k) == summary[k] for k in compare), "delivery_summary_mismatch")
    snapshot = ack.get("snapshot", {})
    number = snapshot.get("snapshot_id")
    require(type(number) is int and number >= 0 and snapshot.get("scope") == "batch_commit",
            "invalid_batch_snapshot")
    require(number > baseline_snapshot if added else number <= baseline_snapshot, "wrong_snapshot_boundary")
    return dict(batch_id=batch, count=summary["count"], checksum=summary["checksum"], snapshot_id=number)

def validate_commit(metadata, batch, checksum):
    from midas_data.analytics import SCHEMA_VERSION
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    require(isinstance(metadata, dict) and metadata.get("schema_version") == SCHEMA_VERSION,
            "invalid_commit_metadata")
    member = {"batch_id": batch, "checksum": checksum}
    if metadata.get("group_version") == "midas-analytics-group-v1":
        require(isinstance(metadata.get("batches"), list) and metadata["batches"].count(member) == 1,
                "commit_member_mismatch")
    else:
        require(metadata.get("group_version") is None and metadata.get("batch_id") == batch
                and metadata.get("checksum") == checksum, "commit_member_mismatch")

class Reader:
    def __init__(self, lake, pg):
        self.lake, self.pg = lake, pg

    @staticmethod
    def table(name, version=None):
        require(name in ("facts_log", "batch_receipts"), "invalid_table")
        require(version is None or type(version) is int and version >= 0, "invalid_snapshot")
        return "lake." + name + (f" AT (VERSION => {version})" if version is not None else "")

    def fingerprint(self, name, version=None, exclude=()):
        table = self.table(name, version)
        order = "source_kind,fact_id,batch_id,row_hash" if name == "facts_log" else "batch_id,checksum,created_at"
        where = " WHERE batch_id NOT IN (SELECT unnest(?::VARCHAR[]))"
        cursor = self.lake.execute("SELECT * FROM " + table + where + " ORDER BY " + order, [list(exclude)])
        def rows():
            while chunk := cursor.fetchmany(256):
                yield from chunk
        return fingerprint_rows(rows())

    def snapshots(self, through):
        cursor = self.lake.execute("SELECT * FROM lake.snapshots() WHERE snapshot_id<=? ORDER BY snapshot_id", [through])
        def rows():
            while chunk := cursor.fetchmany(256):
                yield from chunk
        return fingerprint_rows(rows())

    async def pending(self):
        from midas_data.analytics import _utc
        result = []
        async with self.pg.transaction(readonly=True):
            async for r in self.pg.cursor("""SELECT batch_id,fact_count,available_at FROM normalization_runs
                WHERE available_at IS NOT NULL AND analytics_applied_at IS NULL ORDER BY batch_id""", prefetch=256):
                result.append(dict(batch_id=r["batch_id"], fact_count=r["fact_count"], available_at=_utc(r["available_at"])))
        return result

    async def batch(self, expected, snapshot, added):
        from midas_data.analytics import FACT_COLUMNS, RECEIPT_COLUMNS
        batch = expected["batch_id"]
        run = await self.pg.fetchrow("""SELECT available_at,fact_count,analytics_applied_at,analytics_receipt
            FROM normalization_runs WHERE batch_id=$1""", batch)
        pg_rows = []
        records = await self.pg.fetch("""SELECT f.* FROM facts f JOIN normalization_runs r USING(capture_id,normalizer_version)
            WHERE r.batch_id=$1 AND f.available_at IS NOT NULL ORDER BY f.fact_id LIMIT 10001""", batch)
        for r in records:
            obj = dict(r)
            obj.pop("seq", None)
            obj.pop("numeric_value", None)
            pg_rows.append({k: str(v) if isinstance(v, UUID) else v for k, v in obj.items()})
        if added:
            old = self.lake.execute("SELECT count(*) FROM " + self.table("facts_log", snapshot) + " WHERE batch_id=?", [batch]).fetchone()[0]
            require(old == 0, "orphan_facts_before_rebuild")
        columns = (*FACT_COLUMNS, "row_hash")
        lake_rows = [dict(zip(columns, row)) for row in self.lake.execute(
            "SELECT " + ",".join(columns) + " FROM lake.facts_log WHERE batch_id=? LIMIT 10001", [batch]).fetchall()]
        receipts = [dict(zip(RECEIPT_COLUMNS, row)) for row in self.lake.execute(
            "SELECT " + ",".join(RECEIPT_COLUMNS) + " FROM lake.batch_receipts WHERE batch_id=? LIMIT 2", [batch]).fetchall()]
        proof = check_batch(expected=expected, pg_rows=pg_rows, lake_rows=lake_rows, receipts=receipts,
                            run=run, baseline_snapshot=snapshot, added=added)
        commits = self.lake.execute("SELECT commit_extra_info FROM lake.snapshots() WHERE snapshot_id=?",
                                    [proof["snapshot_id"]]).fetchall()
        require(len(commits) == 1, "missing_batch_commit")
        validate_commit(commits[0][0], batch, proof["checksum"])
        return proof

async def make_plan(reader, baseline):
    snapshot = baseline["lake_snapshot"][0]
    require(type(snapshot) is int and snapshot >= 0, "invalid_baseline_snapshot")
    current = reader.lake.execute("SELECT max(snapshot_id) FROM lake.snapshots()").fetchone()[0]
    require(current == snapshot, "plan_must_precede_rebuild")
    pending = await reader.pending()
    validate_added([r["batch_id"] for r in pending], [])
    return dict(schema_version=1, baseline_snapshot=snapshot, original_facts=baseline["lake_facts"],
                original_receipts=reader.fingerprint("batch_receipts"),
                original_snapshots=reader.snapshots(snapshot), pending=pending)

async def verify(reader, plan):
    require(plan["schema_version"] == 1, "unsupported_reconciliation_plan")
    snapshot = plan["baseline_snapshot"]
    eligible = {r["batch_id"]: r for r in plan["pending"]}
    validate_added([r["batch_id"] for r in plan["pending"]], [])
    new = [r[0] for r in reader.lake.execute(
        "SELECT batch_id FROM lake.batch_receipts EXCEPT SELECT batch_id FROM " +
        reader.table("batch_receipts", snapshot) + " ORDER BY batch_id LIMIT ?",
        [len(eligible) + 1]).fetchall()]
    added = validate_added(list(eligible), new)
    historical = reader.fingerprint("facts_log", snapshot)
    preserved = reader.fingerprint("facts_log", exclude=new)
    require_same(plan["original_facts"], historical, "historical_facts")
    require_same(plan["original_facts"], preserved, "original_current_facts")
    require_same(plan["original_receipts"], reader.fingerprint("batch_receipts", snapshot), "historical_receipts")
    require_same(plan["original_receipts"], reader.fingerprint("batch_receipts", exclude=new), "original_current_receipts")
    require_same(plan["original_snapshots"], reader.snapshots(snapshot), "original_snapshot_metadata")
    batches = [await reader.batch(r, snapshot, batch in added) for batch, r in eligible.items()]
    added_rows = sum(r["count"] for r in batches if r["batch_id"] in added)
    current = reader.lake.execute("SELECT count(*) FROM lake.facts_log").fetchone()[0]
    require(current == plan["original_facts"]["rows"] + added_rows, "unexpected_physical_growth")
    return dict(schema_version=1, status="passed", baseline_snapshot=snapshot,
                historical_fingerprint=historical, preserved_original_fingerprint=preserved,
                current_lake_rows=current, added_rows=added_rows, new_receipts=len(added),
                pending_batches_verified=len(batches), batches=batches,
                original_receipts_unchanged=True, original_snapshots_unchanged=True,
                unexpected_rows_or_duplicates=0)

async def main():
    import asyncpg
    import duckdb
    require(len(sys.argv) == 2 and sys.argv[1] in ("plan", "verify"), "invalid_phase")
    directory = Path("/restore-input")
    baseline = json.loads((directory / "baseline.json").read_text())
    pg = await asyncpg.connect(os.environ["DATA_DATABASE_DSN"],
        server_settings={"timezone": "UTC", "application_name": "midas-restore-reconcile", "statement_timeout": "30000"})
    lake = duckdb.connect(":memory:")
    try:
        for sql in ("SET enable_progress_bar=false", "SET threads=2", "SET memory_limit='256MB'",
                    "SET TimeZone='UTC'", "LOAD ducklake", "LOAD postgres"):
            lake.execute(sql)
        dsn = ("ducklake:postgres:" + os.environ["DATA_LAKE_CATALOG_DSN"]).replace("'", "''")
        lake.execute("ATTACH '" + dsn + "' AS lake (READ_ONLY)")
        reader = Reader(lake, pg)
        if sys.argv[1] == "plan":
            result = await make_plan(reader, baseline)
        else:
            plan = json.loads((directory / "restore-rebuild-plan.json").read_text())
            require_same(baseline["lake_facts"], plan["original_facts"], "plan_baseline")
            require_same(baseline["lake_snapshot"][0], plan["baseline_snapshot"], "plan_snapshot")
            result = await verify(reader, plan)
        print("\nMIDAS_RESTORE_RECONCILIATION=" + json.dumps(result, sort_keys=True, default=str, separators=(",", ":")), flush=True)
    finally:
        lake.close()
        await pg.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        # Never expose raw SQL, DSNs, paths or payloads in failure output.
        print("restore_reconciliation_failed type=" + type(exc).__name__, file=sys.stderr)
        sys.exit(1)

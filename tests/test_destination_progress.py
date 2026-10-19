"""Real SQL transaction proof in a private schema of the fresh Docker base."""
import asyncio,json,os
from pathlib import Path
from uuid import uuid4
import pytest
from midas_data.contracts import CaptureMetadata
from midas_data.normalize import normalize_capture
from midas_data.store import Store,CaptureConflict
from midas_data.raw_store import RawStore
from midas_data.delivery import DeliveryStore,combined_receipt
from midas_data.analytics import _prepare

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv("DATA_INTEGRATION")!="1",reason="isolated SQL opt-in")]


def test_independent_receipts_transaction_replay_and_legacy_initialization(tmp_path):
    async def run():
        import asyncpg
        dsn=os.environ["DATA_DATABASE_DSN"];schema="delivery_fixture_"+uuid4().hex
        admin=await asyncpg.connect(dsn);pool=None
        try:
            assert await admin.fetchval("SELECT current_database()") == "midas_data"
            await admin.execute(f"CREATE SCHEMA {schema}")
            pool=await asyncpg.create_pool(dsn,min_size=1,max_size=3,server_settings={"search_path":schema,"timezone":"UTC"})
            migrations=sorted(Path("midas_data/migrations").glob("*.sql"))
            async with pool.acquire() as con:
                for m in migrations:
                    if not m.name.startswith("007_"):await con.execute(m.read_text())
            store=Store("fixture",RawStore(tmp_path,min_free_bytes=0));store.pool=pool
            async def capture():
                cid=uuid4();meta=CaptureMetadata(capture_id=cid,source_kind="fixture",endpoint="token_info",chain="base",
                    requested_at="2026-09-09T12:00:00Z",received_at="2026-09-09T12:00:01Z",http_status=200,evidence_kind="original_response")
                body=b'{"address":"0x1111111111111111111111111111111111111111","price":{"price":"1.2300"}}'
                await store.accept_capture(meta,body)
                output=await store.record_normalization(cid,normalize_capture(meta,body))
                batch=output["batch_id"];_,summary=_prepare(await store.batch_rows(batch))
                receipts={t:dict(summary,batch_id=batch,destination=t,ok=True,receipt_present=True) for t in ("lake","clickhouse")}
                receipts["lake"]["snapshot"]={"scope":"batch_commit","snapshot_id":1,"snapshot_time":"2026-09-09T12:00:02Z"}
                return cid,batch,receipts
            old_id,old_batch,old=await capture()
            await store.mark_analytics(old_batch,combined_receipt(old_batch,old["lake"],old["clickhouse"]))
            marker=await pool.fetchval("SELECT analytics_applied_at FROM normalization_runs WHERE batch_id=$1",old_batch)
            await pool.execute(next(m for m in migrations if m.name.startswith("007_")).read_text())
            assert await pool.fetchval("SELECT count(*) FROM analytics_deliveries WHERE batch_id=$1 AND applied_at=$2",old_batch,marker)==2
            cid,batch,receipts=await capture();delivery=DeliveryStore(store)
            await delivery.confirm("lake",{batch:receipts["lake"]})
            assert await pool.fetchval("SELECT analytics_applied_at FROM normalization_runs WHERE batch_id=$1",batch) is None
            assert not any(r["batch_id"]==batch for r in await delivery.pending("lake"))
            await delivery.defer("clickhouse",batch,"AnalyticsError")
            assert await pool.fetchval("SELECT attempts FROM analytics_deliveries WHERE batch_id=$1 AND destination='clickhouse'",batch)==1
            await delivery.confirm("clickhouse",{batch:receipts["clickhouse"]})
            assert await pool.fetchval("SELECT analytics_applied_at IS NOT NULL FROM normalization_runs WHERE batch_id=$1",batch)
            before=await pool.fetch("SELECT * FROM analytics_deliveries WHERE batch_id=$1 ORDER BY destination",batch)
            await delivery.confirm("lake",{batch:receipts["lake"]})
            assert before==await pool.fetch("SELECT * FROM analytics_deliveries WHERE batch_id=$1 ORDER BY destination",batch)
            bad=dict(receipts["lake"],checksum="f"*64)
            with pytest.raises(CaptureConflict):await delivery.confirm("lake",{batch:bad})
            assert before==await pool.fetch("SELECT * FROM analytics_deliveries WHERE batch_id=$1 ORDER BY destination",batch)
        finally:
            if pool:await pool.close()
            assert schema.startswith("delivery_fixture_") and len(schema)==49
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE");await admin.close()
    asyncio.run(run())

"""Real PostgreSQL/NATS proof; private schema and accelerated fixture stream TTL."""
import asyncio, json, os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from midas_data.contracts import CaptureMetadata
from midas_data.raw_store import RawStore
from midas_data.store import Store
from midas_data.worker import Pipeline

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv("DATA_INTEGRATION")!="1",reason="private broker/SQL opt-in")]


def test_expired_uncertain_publication_republishes_and_duplicate_has_no_effect(tmp_path):
    async def run():
        import asyncpg,nats
        from nats.js.api import StreamConfig,ConsumerConfig,AckPolicy,StorageType
        dsn=os.environ["DATA_DATABASE_DSN"];tag=uuid4().hex
        schema="message_fixture_"+tag;stream="EXPIRY_FIXTURE_"+tag;subject="fixture.expiry."+tag
        admin=await asyncpg.connect(dsn);pool=connection=None;created=False
        try:
            assert await admin.fetchval("SELECT current_database()") == "midas_data"
            await admin.execute(f"CREATE SCHEMA {schema}")
            pool=await asyncpg.create_pool(dsn,min_size=1,max_size=3,server_settings={"search_path":schema,"timezone":"UTC"})
            async with pool.acquire() as con:
                for m in sorted(Path("midas_data/migrations").glob("*.sql")):await con.execute(m.read_text())
            store=Store("fixture",RawStore(tmp_path,min_free_bytes=0));store.pool=pool
            connection=await nats.connect(os.environ["DATA_NATS_URL"]);js=connection.jetstream(timeout=5)
            await js.add_stream(config=StreamConfig(name=stream,subjects=[subject],storage=StorageType.MEMORY,
                max_age=2,duplicate_window=0.25,max_bytes=65536));created=True
            consumer=await js.pull_subscribe(subject,durable="fixture",stream=stream,config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT,ack_wait=2))
            lost_ack=True
            async def publish(_,body,**kw):
                nonlocal lost_ack
                reply=await js.publish(subject,body,**kw)
                if lost_ack:lost_ack=False;raise TimeoutError("fixture: server published but reply lost")
                return reply
            async def broker():return SimpleNamespace(publish=publish),consumer
            pipeline=object.__new__(Pipeline);pipeline.store=store;pipeline.broker=broker
            pipeline.stages={"normalizer":{"failures":0,"fallbacks":0}}
            cid=uuid4();meta=CaptureMetadata(capture_id=cid,source_kind="fixture",endpoint="token_info",chain="base",
                requested_at="2026-09-09T12:00:00Z",received_at="2026-09-09T12:00:01Z",http_status=200,evidence_kind="original_response")
            body=b'{"address":"0x1111111111111111111111111111111111111111","price":{"price":"1.2300"}}'
            await store.accept_capture(meta,body)
            with pytest.raises(TimeoutError):await pipeline.relay()
            assert (await js.stream_info(stream)).state.messages==1
            assert await pool.fetchval("SELECT published_at FROM outbox") is None
            # Accelerated TTL, not a claim that 24 real hours elapsed.
            for _ in range(60):
                if (await js.stream_info(stream)).state.messages==0:break
                await asyncio.sleep(0.1)
            assert (await js.stream_info(stream)).state.messages==0
            assert await pipeline.relay()==1
            assert await pipeline.normalize()==1
            before=await store.get_capture(cid)
            assert len(before["runs"])==1 and before["runs"][0]["available_at"]
            assert await pool.fetchval("SELECT consumed_at IS NOT NULL FROM outbox")
            facts=await pool.fetch("SELECT * FROM facts ORDER BY fact_id")
            assert len(facts)==1 and facts[0]["value_text"]=="1.2300"
            await js.publish(subject,json.dumps({"capture_id":str(cid)}).encode(),headers={"Nats-Msg-Id":str(cid)+":redelivery"})
            assert await pipeline.normalize()==0
            assert (await store.get_capture(cid))["runs"]==before["runs"]
            assert await pool.fetch("SELECT * FROM facts ORDER BY fact_id")==facts
            assert store.raw.read(before["raw_sha256"])==body
        finally:
            if connection:
                if created:await js.delete_stream(stream)
                await connection.close()
            if pool:await pool.close()
            assert schema.startswith("message_fixture_") and len(schema)==48
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE");await admin.close()
    asyncio.run(run())

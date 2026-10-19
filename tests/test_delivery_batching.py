"""Real isolated PostgreSQL: batching must preserve atomicity and receipts."""
import asyncio,json,os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import asyncpg,pytest
from midas_data.contracts import CaptureMetadata
from midas_data.normalize import normalize_capture
from midas_data.store import Store,CaptureConflict
from midas_data.raw_store import RawStore
from midas_data.delivery import DeliveryStore
from midas_data.analytics import _prepare

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='isolated SQL opt-in')]

class RecordedPool:
    def __init__(self,pool):self.pool=pool;self.calls=[]
    @asynccontextmanager
    async def acquire(self):
        async with self.pool.acquire() as con:
            owner=self
            class Connection:
                def transaction(self):return con.transaction()
                async def fetch(self,sql,*args):
                    owner.calls.append(sql);return await con.fetch(sql,*args)
                async def execute(self,sql,*args):
                    owner.calls.append(sql);return await con.execute(sql,*args)
            yield Connection()

@asynccontextmanager
async def isolated(tmp_path):
    # Never use deployment credentials or a non-local host in this suite.
    port=int(os.environ.get('COST_TEST_PORT','55439'))
    assert port in (5432,55439)
    dsn=f'postgresql://fixture:local-fixture@127.0.0.1:{port}/midas_data'
    admin=await asyncpg.connect(dsn);schema='cost_fixture_'+uuid4().hex;pool=None
    try:
        assert await admin.fetchval('SELECT current_database()')=='midas_data'
        await admin.execute(f'CREATE SCHEMA {schema}')
        pool=await asyncpg.create_pool(dsn,min_size=1,max_size=4,server_settings={'search_path':schema,'timezone':'UTC','statement_timeout':'10000'})
        async with pool.acquire() as con:
            for migration in sorted(Path('midas_data/migrations').glob('*.sql')):await con.execute(migration.read_text())
        store=Store('fixture',RawStore(tmp_path,min_free_bytes=0));store.pool=pool
        yield store,pool
    finally:
        if pool:await pool.close()
        assert schema.startswith('cost_fixture_') and len(schema)==45
        await admin.execute(f'DROP SCHEMA {schema} CASCADE');await admin.close()

async def group(store,size):
    result={'lake':{},'clickhouse':{}}
    for _ in range(size):
        cid=uuid4();meta=CaptureMetadata(capture_id=cid,source_kind='fixture',endpoint='token_info',chain='base',requested_at='2026-09-09T12:00:00Z',received_at='2026-09-09T12:00:01Z',http_status=200,evidence_kind='original_response')
        body=b'{"address":"0x1111111111111111111111111111111111111111","price":{"price":"1.2300"}}'
        await store.accept_capture(meta,body)
        batch=(await store.record_normalization(cid,normalize_capture(meta,body)))['batch_id']
        _,summary=_prepare(await store.batch_rows(batch))
        for dest in result:
            receipt=dict(summary,batch_id=batch,destination=dest,ok=True,receipt_present=True)
            if dest=='lake':receipt['snapshot']={'scope':'batch_commit','snapshot_id':1,'snapshot_time':'2026-09-09T12:00:02Z'}
            result[dest][batch]=receipt
    return result

async def state(pool):
    return ([dict(r) for r in await pool.fetch('SELECT batch_id,analytics_applied_at,analytics_receipt,analytics_retry_at,analytics_last_error FROM normalization_runs ORDER BY batch_id')],
            [dict(r) for r in await pool.fetch('SELECT * FROM analytics_deliveries ORDER BY batch_id,destination')])

def test_group_32_uses_one_completion_statement_and_exact_replay(tmp_path):
    async def run():
        async with isolated(tmp_path) as (store,pool):
            receipts=await group(store,32);delivery=DeliveryStore(store);record=RecordedPool(pool);delivery.pool=record
            await delivery.confirm('lake',receipts['lake'])
            assert await pool.fetchval('SELECT count(*) FROM normalization_runs WHERE analytics_applied_at IS NOT NULL')==0
            record.calls.clear();await delivery.confirm('clickhouse',receipts['clickhouse'])
            assert len(record.calls)==5
            assert sum('UPDATE normalization_runs' in s for s in record.calls)==1
            before=await state(pool)
            assert all(r['analytics_applied_at'] is not None for r in before[0])
            for r in before[0]:
                joined=json.loads(r['analytics_receipt']);b=r['batch_id']
                assert joined['lake']==receipts['lake'][b] and joined['clickhouse']==receipts['clickhouse'][b]
            for _ in range(3):
                await delivery.confirm('lake',receipts['lake']);await delivery.confirm('clickhouse',receipts['clickhouse'])
            assert await state(pool)==before
    asyncio.run(run())

def test_last_receipt_mismatch_rolls_back_entire_group(tmp_path):
    async def run():
        async with isolated(tmp_path) as (store,pool):
            receipts=await group(store,32);delivery=DeliveryStore(store)
            await delivery.confirm('lake',receipts['lake']);before=await state(pool)
            bad=dict(receipts['clickhouse']);last=sorted(bad)[-1];bad[last]=dict(bad[last],checksum='f'*64)
            with pytest.raises(CaptureConflict):await delivery.confirm('clickhouse',bad)
            assert await state(pool)==before
            await delivery.confirm('clickhouse',receipts['clickhouse'])
            assert await pool.fetchval('SELECT count(*) FROM normalization_runs WHERE analytics_applied_at IS NOT NULL')==32
    asyncio.run(run())

def test_database_error_in_last_completion_rolls_back_all(tmp_path):
    async def run():
        async with isolated(tmp_path) as (store,pool):
            receipts=await group(store,8);delivery=DeliveryStore(store)
            await delivery.confirm('lake',receipts['lake']);before=await state(pool)
            last=sorted(receipts['lake'])[-1]
            # Fixture-only trigger injects failure inside the final SQL statement.
            assert all(c.isalnum() or c in ':_-' for c in last)
            await pool.execute(f"CREATE FUNCTION reject_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.batch_id='{last}' AND NEW.analytics_applied_at IS NOT NULL THEN RAISE EXCEPTION 'fixture_failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER reject_fixture BEFORE UPDATE ON normalization_runs FOR EACH ROW EXECUTE FUNCTION reject_fixture()")
            with pytest.raises(asyncpg.RaiseError):await delivery.confirm('clickhouse',receipts['clickhouse'])
            assert await state(pool)==before
            await pool.execute('DROP TRIGGER reject_fixture ON normalization_runs')
            await delivery.confirm('clickhouse',receipts['clickhouse'])
            assert await pool.fetchval('SELECT count(*) FROM normalization_runs WHERE analytics_applied_at IS NOT NULL')==8
    asyncio.run(run())

def test_concurrent_destinations_and_lost_ack_replay(tmp_path):
    async def run():
        async with isolated(tmp_path) as (store,pool):
            receipts=await group(store,32);delivery=DeliveryStore(store)
            await asyncio.wait_for(asyncio.gather(delivery.confirm('lake',receipts['lake']),delivery.confirm('clickhouse',dict(reversed(list(receipts['clickhouse'].items()))))),timeout=10)
            before=await state(pool)
            assert await pool.fetchval('SELECT count(*) FROM normalization_runs WHERE analytics_applied_at IS NOT NULL')==32
            # Caller retries after an unobserved successful return/commit.
            await asyncio.gather(delivery.confirm('lake',receipts['lake']),delivery.confirm('clickhouse',receipts['clickhouse']))
            assert await state(pool)==before
    asyncio.run(run())

def test_mixed_complete_incomplete_and_replay(tmp_path):
    async def run():
        async with isolated(tmp_path) as (store,pool):
            receipts=await group(store,8);delivery=DeliveryStore(store);ids=sorted(receipts['lake'])
            first={b:receipts['lake'][b] for b in ids[:4]}
            await delivery.confirm('lake',first)
            await delivery.confirm('clickhouse',receipts['clickhouse'])
            before=await state(pool)
            assert sum(r['analytics_applied_at'] is not None for r in before[0])==4
            await delivery.confirm('lake',receipts['lake']);after=await state(pool)
            previous={r['batch_id']:r for r in before[0]}
            assert all(r['analytics_applied_at'] is not None for r in after[0])
            assert all(r==previous[r['batch_id']] for r in after[0] if r['batch_id'] in first)
    asyncio.run(run())

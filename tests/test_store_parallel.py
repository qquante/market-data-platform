"""Real PostgreSQL concurrency, isolated schema and fixture values only."""
import asyncio,os,re
from pathlib import Path
from uuid import uuid4
import pytest
from midas_data.store import Store
from test_store_batches import item

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('DATA_INTEGRATION')!='1',reason='isolated PostgreSQL opt-in')]

def test_concurrent_overlapping_groups_keep_one_output_and_publication_marker():
 async def run():
  import asyncpg
  dsn=os.environ['DATA_DATABASE_DSN'];schema='speed_parallel_'+uuid4().hex
  admin=await asyncpg.connect(dsn);pool=None
  try:
   assert await admin.fetchval('SELECT current_database()')=='midas_data'
   await admin.execute(f'CREATE SCHEMA {schema}')
   pool=await asyncpg.create_pool(dsn,min_size=1,max_size=2,command_timeout=30,server_settings={'search_path':schema,'timezone':'UTC'})
   async with pool.acquire() as con:
    for p in sorted(Path(__file__).parents[1].joinpath('midas_data/migrations').glob('*.sql')):await con.execute(p.read_text(encoding='utf-8'))
    await con.execute("INSERT INTO raw_objects(sha256,relative_path,size_bytes) VALUES($1,'fixture',0)",'0'*64)
    items=[item() for _ in range(3)]
    for cid,_ in items:
     await con.execute("INSERT INTO captures(capture_id,fingerprint,raw_sha256,metadata,source_kind,evidence_kind,endpoint,chain,received_at) VALUES($1,'fixture',$2,'{}','fixture','original_response','token_info','base',clock_timestamp())",cid,'0'*64)
     await con.execute('INSERT INTO outbox(capture_id) VALUES($1)',cid)
   store=Store('unused',None);store.pool=pool
   results=await asyncio.gather(store.record_normalizations(items[:2]),store.record_normalizations(items[1:]))
   assert len({r['batch_id'] for group in results for r in group})==3
   assert await pool.fetchval('SELECT count(*) FROM normalization_runs')==3
   assert await pool.fetchval('SELECT count(*) FROM facts')==sum(len(result.facts) for _,result in items)
   assert await pool.fetchval("SELECT count(*) FROM facts f JOIN normalization_runs r USING(capture_id,normalizer_version) WHERE f.available_at IS NULL OR f.available_at IS DISTINCT FROM r.available_at")==0
   before=await pool.fetch('SELECT capture_id,available_at FROM normalization_runs ORDER BY capture_id')
   await asyncio.gather(store.record_normalizations(items[:2]),store.record_normalizations(items[1:]))
   assert before==await pool.fetch('SELECT capture_id,available_at FROM normalization_runs ORDER BY capture_id')
   assert await pool.fetchval('SELECT count(*) FROM outbox WHERE consumed_at IS NULL')==0
  finally:
   if pool:await pool.close()
   assert re.fullmatch('speed_parallel_[a-f0-9]{32}',schema)
   await admin.execute(f'DROP SCHEMA {schema} CASCADE');await admin.close()
 asyncio.run(run())

import asyncio,pytest
from test_token_registry import database,integration
from midas_data.delivery import DeliveryStore


@integration
def test_due_retry_is_served_before_continuously_arriving_first_attempts(tmp_path):
 async def run():
  async with database(tmp_path) as (store,capture,settings):
   batches=[]
   for i in range(35):
    meta,_=await capture({'rank':[{'address':f'Token{i}','price':'1'}]})
    batches.append(await store.pool.fetchval('SELECT batch_id FROM normalization_runs WHERE capture_id=$1',meta.capture_id))
   await store.pool.execute("UPDATE analytics_deliveries SET created_at=clock_timestamp()-interval '10 minutes',retry_at=clock_timestamp()-interval '1 second',last_error='AnalyticsError' WHERE batch_id=$1 AND destination='clickhouse'",batches[0])
   await store.pool.execute("UPDATE analytics_deliveries SET created_at=clock_timestamp()-interval '20 minutes',retry_at=clock_timestamp()+interval '1 day' WHERE batch_id=$1 AND destination='clickhouse'",batches[1])
   selected=[r['batch_id'] for r in await DeliveryStore(store).pending('clickhouse')]
   assert selected[0]==batches[0]
   assert batches[1] not in selected and len(selected)==32
 asyncio.run(run())

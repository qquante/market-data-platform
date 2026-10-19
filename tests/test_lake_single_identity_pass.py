"""SQL proof for bounded Lake delivery, replay and global conflicts."""
import pytest
from midas_data.analytics import AnalyticsError, BatchConflict, _row_hash
from test_analytics import fact

@pytest.fixture
def lake():
 from test_analytics import GroupLakeSqlTests
 case=GroupLakeSqlTests('runTest');case.setUp()
 case.sink.destination='lake';case.sink._ready=True
 # Only catalog snapshot metadata is replaced; facts/receipts/transactions use SQL.
 case.sink._batch_snapshots=lambda checksums:{b:{'snapshot_id':1,'snapshot_time':'2026-09-11T00:00:00Z','scope':'batch_commit'} for b in checksums}
 try:yield case
 finally:case.doCleanups()

def test_full_delivery_uses_one_global_scan_and_replay_stays_exact(lake):
 rows=[fact() for _ in range(4400)]
 result=lake.sink.apply_destination([('large',rows)])
 assert result['large']['count']==4400 and result['large']['ok']
 assert sum('WHERE fact_id IN' in q for q in lake.statements)==1
 lake.statements.clear()
 assert lake.sink.apply_destination([('large',rows)])==result
 assert sum('WHERE fact_id IN' in q for q in lake.statements)==1
 assert lake.sink._lake.execute('SELECT count(*) FROM lake.facts_log').fetchone()[0]==4400

def test_replay_checks_global_conflict_even_when_receipt_already_exists(lake):
 a=fact();lake.sink.apply_destination([('original',[a])])
 # Simulate externally corrupted legacy membership, with an internally valid hash.
 altered=dict(a,value_text='999')
 columns=tuple(lake.sink._lake_types())
 record=dict(altered,row_hash=_row_hash(altered),batch_id='corrupted-other-batch')
 lake.sink._lake.execute('INSERT INTO lake.facts_log ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[record[k] for k in columns])
 with pytest.raises(BatchConflict):lake.sink.apply_destination([('original',[a])])
 assert lake.sink._lake.execute('SELECT count(*) FROM lake.facts_log').fetchone()[0]==2

def test_new_batch_conflict_does_not_create_receipt_or_modify_old(lake):
 a=fact();lake.sink.apply_destination([('original',[a])])
 with pytest.raises(BatchConflict):lake.sink.apply_destination([('wrong',[dict(a,unit='wrong')])])
 assert lake.sink._receipt('wrong',lake=True) is None
 assert lake.sink._lake.execute('SELECT count(*) FROM lake.facts_log').fetchone()[0]==1

def test_readback_still_rejects_changed_content_after_write(lake):
 a=fact();original=lake.sink._apply_lake_batches
 def tamper(prepared):
  original(prepared)
  lake.sink._lake.execute("UPDATE lake.facts_log SET value_text='999' WHERE batch_id='one'")
 lake.sink._apply_lake_batches=tamper
 with pytest.raises(BatchConflict):lake.sink.apply_destination([('one',[a])])

def test_missing_receipt_still_rolls_back_then_retries(lake):
 a=fact();lake.fail_receipt_once=True
 with pytest.raises(AnalyticsError):lake.sink.apply_destination([('one',[a])])
 assert lake.sink._lake.execute('SELECT count(*) FROM lake.facts_log').fetchone()[0]==0
 assert lake.sink.apply_destination([('one',[a])])['one']['ok']

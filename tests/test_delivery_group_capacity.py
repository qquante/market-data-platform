import pytest
from midas_data.analytics import _prepare_batches, _prepare, MAX_BATCH_BYTES, batch_size
from midas_data.worker import group_would_overflow
from test_analytics import fact

def test_three_complete_captures_share_a_bounded_delivery_group():
 batches=[('capture-'+str(i),[fact() for _ in range(4400)]) for i in range(3)]
 prepared=_prepare_batches(batches)
 assert sum(len(rows) for rows,_ in prepared.values())==13200
 assert sum(batch_size(rows) for rows,_ in prepared.values())<=MAX_BATCH_BYTES
 assert not group_would_overflow(2,8800,0,4400,1,row_limit=25000)
 assert group_would_overflow(2,8800,0,4400,1)

def test_individual_capture_limit_and_group_byte_limit_still_apply():
 with pytest.raises(ValueError):_prepare([fact() for _ in range(10001)])
 assert group_would_overflow(2,8800,MAX_BATCH_BYTES,1,1,row_limit=25000)
 assert group_would_overflow(2,24000,0,1001,1,row_limit=25000)


def test_large_group_round_trip_keeps_all_members_and_exact_hashes():
 from test_analytics import GroupLakeSqlTests
 case=GroupLakeSqlTests('runTest');case.setUp()
 try:
  batches=[('large-'+str(i),[fact() for _ in range(4400)]) for i in range(3)]
  prepared=_prepare_batches(batches)
  case.sink._apply_lake_batches(prepared)
  stored=case.sink._read_batches(list(prepared),lake=True)
  assert {b:len(rows) for b,rows in stored.items()}=={b:4400 for b in prepared}
  assert all(_prepare(stored[b],stored=True)[1]==summary for b,(_,summary) in prepared.items())
  case.sink._apply_lake_batches(prepared)
  assert case.sink._lake.execute('SELECT count(*) FROM lake.facts_log').fetchone()[0]==13200
 finally:case.doCleanups()


def test_small_enrichment_captures_share_delivery_without_tiny_commits():
 batches=[('small-'+str(i),[fact()]) for i in range(32)]
 assert len(_prepare_batches(batches))==32
 assert not group_would_overflow(20,8000,0,4400,1,row_limit=25000,capture_limit=32)
 with pytest.raises(ValueError):_prepare_batches(batches+[('over',[])])
 assert group_would_overflow(32,8000,0,1,1,row_limit=25000,capture_limit=32)

import pytest
from midas_data.analytics import AnalyticsSinks,AnalyticsError,BatchConflict,_row,_row_hash
from test_analytics import fact


def sink():
 return AnalyticsSinks('postgresql://fixture:fixture@invalid/lake','/fixture','http://invalid','fixture','fixture')


def test_read_budget_splits_without_skipping_any_id_and_late_conflicts_fail():
 s=sink();rows=[_row(fact()) for _ in range(130)];by_id={r['fact_id']:r for r in rows};visited=set();sizes=[]
 def read(query,**kw):
  ids=list(kw['params'].values());sizes.append(len(ids))
  if len(ids)>8:raise AnalyticsError('read budget',stage='clickhouse.query',code=307)
  visited.update(ids)
  return [dict(source_kind=by_id[x]['source_kind'],fact_id=x,row_hash=_row_hash(by_id[x])) for x in ids]
 s._ch=read
 try:
  s._check_identities(rows,lake=False)
  assert visited==set(by_id) and max(sizes)==130
  last=sorted(by_id)[-1];by_id[last]=dict(by_id[last],value_text='999')
  with pytest.raises(BatchConflict):s._check_identities(rows,lake=False)
 finally:s.close()


@pytest.mark.parametrize('code,ids',[(307,['one']),(241,['one','two'])])
def test_unsplittable_and_unrelated_errors_still_fail(code,ids):
 s=sink();calls=[]
 def fail(query,**kw):
  calls.append(kw);raise AnalyticsError('failed',stage='clickhouse.query',code=code)
 s._ch=fail
 try:
  with pytest.raises(AnalyticsError):s._ch_identity_rows(ids)
  assert len(calls)==1
 finally:s.close()


def test_identity_chunks_reduce_round_trips_and_check_last_conflict():
 s=sink();rows=[_row(fact()) for _ in range(512)];by_id={r['fact_id']:r for r in rows};calls=[]
 def read(query,**kw):
  ids=list(kw['params'].values());calls.append(ids)
  return [dict(source_kind=by_id[x]['source_kind'],fact_id=x,row_hash=_row_hash(by_id[x])) for x in ids]
 s._ch=read
 try:
  s._check_group_identities(rows,lake=False)
  assert len(calls)==2 and set(x for batch in calls for x in batch)==set(by_id)
  last=sorted(by_id)[-1];by_id[last]=dict(by_id[last],value_text='999')
  with pytest.raises(BatchConflict):s._check_group_identities(rows,lake=False)
 finally:s.close()

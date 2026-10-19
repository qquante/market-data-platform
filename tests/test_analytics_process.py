"""Real spawn/IPC lifecycle tests; fake durable destination, no network."""
import json,os,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from midas_data.analytics import AnalyticsError, BatchConflict
from midas_data.analytics_process import ProcessAnalyticsSinks

class FileSink:
 def __init__(self,path,*,destination):self.path=Path(path);self.destination=destination
 def apply_destination(self,batches):
  if batches=='conflict':raise BatchConflict('fixture conflict')
  if batches=='crash' and not self.path.exists():
   self.path.write_text('committed',encoding='utf-8');os._exit(7)
  if batches=='wait':
   self.path.with_suffix('.started').touch();time.sleep(.2)
  return {'pid':os.getpid(),'destination':self.destination,'exact':batches,'recovered':self.path.exists()}
 def compact_files(self,**kw):return {'pid':os.getpid(),'options':kw}
 def close(self):self.path.with_suffix('.closed').write_text('closed',encoding='utf-8')

def make(path,target='lake'):
 return ProcessAnalyticsSinks(str(path),destination=target,sink_factory=FileSink)

def test_lazy_reuse_exact_ipc_and_close(tmp_path):
 path=tmp_path/'lake';s=make(path)
 assert s._pool is None
 try:
  value=[('batch',[{'text':'漢字 \n','price':'0.00000000000000001230','null':None}])]
  first=s.apply_destination(value);second=s.apply_destination(value)
  assert first==second and first['exact']==value and first['pid']!=os.getpid()
  assert s.compact_files(max_output_files=2)['pid']==first['pid']
 finally:s.close()
 assert path.with_suffix('.closed').read_text(encoding='utf-8')=='closed'
 s.close()
 with pytest.raises(AnalyticsError):s.apply_destination(value)

def test_targets_have_separate_processes_and_calls_are_serial(tmp_path):
 a=make(tmp_path/'lake');b=make(tmp_path/'ch','clickhouse')
 try:
  with ThreadPoolExecutor(2) as p:
   results=list(p.map(a.apply_destination,['wait','next']))
  assert results[0]['pid']==results[1]['pid']
  assert b.apply_destination('other')['pid']!=results[0]['pid']
 finally:a.close();b.close()

def test_process_death_does_not_retry_hiddenly_and_next_call_recovers(tmp_path):
 path=tmp_path/'crash';s=make(path)
 try:
  with pytest.raises(AnalyticsError,match='process stopped'):s.apply_destination('crash')
  assert path.read_text(encoding='utf-8')=='committed' and s._pool is None
  result=s.apply_destination('crash')
  assert result['recovered'] and result['exact']=='crash'
 finally:s.close()

def test_conflict_type_survives_ipc_without_disabling_worker(tmp_path):
 s=make(tmp_path/'conflict')
 try:
  with pytest.raises(BatchConflict):s.apply_destination('conflict')
  assert s.apply_destination('next')['exact']=='next'
 finally:s.close()

def test_close_waits_for_inflight_result_before_returning(tmp_path):
 path=tmp_path/'inflight';s=make(path)
 with ThreadPoolExecutor(1) as p:
  future=p.submit(s.apply_destination,'wait')
  deadline=time.monotonic()+10
  while not path.with_suffix('.started').exists():
   assert time.monotonic()<deadline;time.sleep(.01)
  s.close()
  assert future.result()['exact']=='wait'
 assert path.with_suffix('.closed').exists()

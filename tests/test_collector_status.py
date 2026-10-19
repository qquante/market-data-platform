import json
import time
from midas_data.collector_status import read_collector_status


def test_live_and_stale_process_are_separate_from_source_current(tmp_path):
    path=tmp_path/'status.json'
    assert read_collector_status(path)['state']=='not_started'
    status={'protocol':'gmgn-direct-v1','heartbeat_at':time.time()-2,'state':'running','source_current':False}
    path.write_text(json.dumps(status))
    result=read_collector_status(path)
    assert result['process_alive'] and not result['source_current']
    status.update(heartbeat_at=time.time()-150,source_current=True,tracking={'current':True});path.write_text(json.dumps(status))
    result=read_collector_status(path)
    assert not result['process_alive'] and not result['source_current']
    assert not result['tracking']['current']


def test_bad_or_large_status_does_not_break_base_status(tmp_path):
    path=tmp_path/'status.json'
    for raw in ('{','[]','x'*65537,'{"protocol":"unknown"}'):
        path.write_text(raw)
        assert read_collector_status(path)['state']=='unavailable'
    assert read_collector_status(None)['state']=='not_configured'

"""Bounded read of the direct collector heartbeat; liveness is not freshness."""
import json
import time
from pathlib import Path


def read_collector_status(path: Path | None) -> dict:
    if path is None: return {'state':'not_configured','process_alive':False}
    try:
        with path.open('rb') as source: raw=source.read(65537)
        if len(raw)>65536: raise ValueError('status_size_limit')
        status=json.loads(raw)
        if status.get('protocol')!='gmgn-direct-v1': raise ValueError('unknown_status_protocol')
        heartbeat=status['heartbeat_at']
        if type(heartbeat) not in (int,float): raise ValueError('invalid_heartbeat')
        age=time.time()-heartbeat
        status['heartbeat_age_seconds']=max(0,age)
        status['process_alive']=status.get('state')=='running' and 0<=age<90
        if not status['process_alive']:
            status['source_current']=False
            if isinstance(status.get('tracking'),dict): status['tracking']['current']=False
        return status
    except FileNotFoundError: return {'state':'not_started','process_alive':False,'source_current':False}
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        return {'state':'unavailable','process_alive':False,'source_current':False}

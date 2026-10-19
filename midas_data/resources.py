"""Small cgroup-v2 measurements; missing measurements remain unavailable."""
from pathlib import Path
from datetime import datetime,timezone


def resource_snapshot(root=Path('/sys/fs/cgroup')):
    def read(name):
        try:return (root/name).read_text().strip()
        except OSError:return None
    def integer(name):
        value=read(name)
        return int(value) if value and value.isdigit() else None
    usage=read('cpu.stat');quota=read('cpu.max')
    cpu=None;limit=None
    try:
        if usage:cpu=int(dict(line.split() for line in usage.splitlines())['usage_usec'])
        if quota and quota.split()[0]!='max':
            q,p=map(int,quota.split());limit=q/p
    except (ValueError,KeyError,ZeroDivisionError):pass
    memory=integer('memory.current')
    return {'sampled_at':datetime.now(timezone.utc).isoformat(),
        'scope':'this service cgroup; not whole host','available':memory is not None,
        'memory_bytes':memory,'memory_limit_bytes':integer('memory.max'),
        'cpu_usage_usec':cpu,'cpu_limit_cores':limit}

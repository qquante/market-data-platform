"""One spawned interpreter per destination, under the existing worker owner.

Calls stay synchronous for the worker's bounded to_thread interface. Each child
owns its connections; no live DB handles are forked or transferred. A failed
process never retries a write internally: the persistent delivery queue does.
"""
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from threading import RLock
from .analytics import AnalyticsSinks, AnalyticsError

_sink = None


def _initialize(factory, args, kwargs):
    global _sink
    _sink = factory(*args, **kwargs)


def _invoke(operation, args, kwargs):
    if operation not in {'apply_destination', 'compact_files', 'close'}:
        raise ValueError('unsupported analytics operation')
    return getattr(_sink, operation)(*args, **kwargs)


class ProcessAnalyticsSinks:
    def __init__(self, *args, destination, sink_factory=AnalyticsSinks):
        if destination not in {'lake', 'clickhouse'}:
            raise ValueError('invalid analytics destination')
        self.destination = destination
        self._args = args
        self._factory = sink_factory
        self._pool = None
        self._closed = False
        self._lock = RLock()

    def _call(self, operation, *args, **kwargs):
        with self._lock:
            if self._closed:
                raise AnalyticsError('analytics sinks are closed')
            if self._pool is None:
                self._pool = ProcessPoolExecutor(max_workers=1,
                    mp_context=get_context('spawn'), initializer=_initialize,
                    initargs=(self._factory, self._args, {'destination': self.destination}))
            try:
                return self._pool.submit(_invoke, operation, args, kwargs).result()
            except BrokenProcessPool:
                pool, self._pool = self._pool, None
                pool.shutdown(wait=True, cancel_futures=True)
                raise AnalyticsError('analytics process stopped; retry the persisted batch') from None

    def apply_destination(self, batches):
        return self._call('apply_destination', batches)

    def compact_files(self, **kwargs):
        return self._call('compact_files', **kwargs)

    def close(self):
        # The parent retains its PostgreSQL owner lock until all children close.
        # The same lock also prevents maintenance/close racing with a delivery.
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pool, self._pool = self._pool, None
            if pool is not None:
                try:
                    pool.submit(_invoke, 'close', (), {}).result()
                except BrokenProcessPool:
                    pass
                finally:
                    pool.shutdown(wait=True, cancel_futures=True)

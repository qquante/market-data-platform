import asyncio
import pytest
from midas_data.read_cache import ReadCache,ReaderBusy

def test_repeated_cutoff_burst_reuses_bytes_with_two_database_slots():
    async def run():
        c=ReadCache(asyncio.Semaphore(2));active=peak=calls=0
        async def compute():
            nonlocal active,peak,calls
            active+=1;calls+=1;peak=max(peak,active)
            await asyncio.sleep(.01);active-=1
            return b'exact',{'header':'value'}
        results=await asyncio.gather(*(c.run(('fixture','method','cutoff'),compute) for _ in range(20)))
        assert all(r[0][0]==b'exact' for r in results) and peak==calls==2
        assert c.admitted==0 and c.bytes==5
        await c.run(('gmgn','method','cutoff'),compute)
        await c.run(('fixture','method','different_cutoff'),compute)
        assert calls==4
    asyncio.run(run())

def test_bounds_timeout_cancellation_and_failures_release_slots():
    async def run():
        slots=asyncio.Semaphore(1);c=ReadCache(slots,max_admitted=1,wait_seconds=.01)
        await slots.acquire()
        async def compute():return b'x',{}
        queued=asyncio.create_task(c.run('x',compute));await asyncio.sleep(0)
        with pytest.raises(ReaderBusy):await c.run('other',compute)
        with pytest.raises(ReaderBusy):await queued
        assert c.admitted==0
        queued=asyncio.create_task(c.run('x',compute));await asyncio.sleep(0);queued.cancel()
        with pytest.raises(asyncio.CancelledError):await queued
        assert c.admitted==0;slots.release()
        async def fail():raise ValueError('fixture')
        with pytest.raises(ValueError):await c.run('x',fail)
        assert c.admitted==0 and not slots.locked() and not c.cache
        assert (await c.run('x',compute))[0][0]==b'x'
    asyncio.run(run())

def test_cache_memory_entry_and_ttl_budgets():
    async def run():
        c=ReadCache(asyncio.Semaphore(2),max_bytes=4,max_entries=2,ttl=0)
        async def compute():return b'abc',{}
        await c.run('a',compute);await c.run('b',compute)
        assert c.bytes==3 and len(c.cache)==1
        assert c.get('b') is None and c.bytes==0
        async def large():return b'too-big',{}
        await c.run('c',large);assert not c.cache
    asyncio.run(run())

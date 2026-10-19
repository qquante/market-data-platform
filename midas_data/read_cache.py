"""Bounded queue and immutable response cache; shared database concurrency stays unchanged."""
import asyncio,time
from collections import OrderedDict

class ReaderBusy(Exception):pass

class ReadCache:
    def __init__(self,slots,*,max_admitted=32,max_bytes=16*1024*1024,max_entries=32,wait_seconds=2,ttl=60):
        self.slots=slots;self.max_admitted=max_admitted;self.max_bytes=max_bytes;self.max_entries=max_entries
        self.wait_seconds=wait_seconds;self.ttl=ttl;self.cache=OrderedDict();self.admitted=0;self.bytes=0
        self.hits=0;self.computed=0;self.rejected=0

    def get(self,key):
        item=self.cache.get(key)
        if item is None:return None
        at,value=item
        if time.monotonic()-at>self.ttl:
            self.bytes-=len(value[0]);del self.cache[key];return None
        self.cache.move_to_end(key);self.hits+=1;return value

    async def run(self,key,compute):
        cached=self.get(key)
        if cached is not None:return cached,'hit'
        if self.admitted>=self.max_admitted:self.rejected+=1;raise ReaderBusy()
        self.admitted+=1;acquired=False
        try:
            try:
                async with asyncio.timeout(self.wait_seconds):await self.slots.acquire()
                acquired=True
            except TimeoutError:self.rejected+=1;raise ReaderBusy() from None
            cached=self.get(key)
            if cached is not None:return cached,'hit_after_wait'
            value=await compute();self.computed+=1
            size=len(value[0])
            if size<=self.max_bytes:
                if key in self.cache:self.bytes-=len(self.cache.pop(key)[1][0])
                self.cache[key]=(time.monotonic(),value);self.bytes+=size
                while len(self.cache)>self.max_entries or self.bytes>self.max_bytes:
                    self.bytes-=len(self.cache.popitem(last=False)[1][1][0])
            return value,'computed'
        finally:
            if acquired:self.slots.release()
            self.admitted-=1

    def status(self):
        return {'admitted':self.admitted,'max_admitted':self.max_admitted,'wait_seconds':self.wait_seconds,
            'cache_bytes':self.bytes,'max_cache_bytes':self.max_bytes,'entries':len(self.cache),
            'hits':self.hits,'computed':self.computed,'rejected':self.rejected,'key_scope':'all query arguments including source, method and exact cutoff'}

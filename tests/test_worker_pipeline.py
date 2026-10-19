"""Behavioral tests for independent stage scheduling, retries and ownership."""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from midas_data import worker
from midas_data.analytics import AnalyticsError, BatchConflict
from midas_data.delivery import validate_destination, combined_receipt
from midas_data.store import Store


def receipt(batch,target):
    result=dict(batch_id=batch,destination=target,count=1,checksum="a"*64,
        source_counts={"fixture":1},available_frontier="2026-09-08T12:00:00.000000Z",
        schema_version="midas-analytics-v1",ok=True,receipt_present=True)
    if target=="lake":result["snapshot"]={"scope":"batch_commit","snapshot_id":1,"snapshot_time":"2026-09-08T12:00:01Z"}
    return result


class Sink:
    def __init__(self,*args,destination=None):
        self.destination=destination;self.calls=[];self.bad=set();self.down=False;self.closed=False
    def apply_destination(self,group):
        self.calls.append([b for b,_ in group])
        if self.down:raise AnalyticsError("unavailable")
        if any(b in self.bad for b,_ in group):raise BatchConflict("conflict")
        return {b:receipt(b,self.destination) for b,_ in group}
    def close(self):self.closed=True


class Delivery:
    def __init__(self):self.confirmed={};self.failed=[]
    async def confirm(self,target,results):
        for b,r in results.items():validate_destination(b,target,r)
        self.confirmed.update({(target,b):r for b,r in results.items()})
    async def defer(self,target,batch,code):self.failed.append((target,batch,code))
    async def heartbeat(self,*args):pass


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def make(self):
        settings=SimpleNamespace(lake_catalog_dsn="fixture",lake_path="fixture",clickhouse_url="fixture",
            clickhouse_user="fixture",clickhouse_password="fixture",nats_url="fixture")
        store=SimpleNamespace(pool=AsyncMock(),raw=SimpleNamespace(admission=lambda:{"accepting":True}),heartbeat=AsyncMock())
        with patch.object(worker,"ProcessAnalyticsSinks",Sink):p=worker.Pipeline(settings,store,asyncio.Event())
        p.deliveries=Delivery()
        p.research=SimpleNamespace(step=AsyncMock(return_value=0))
        self.addAsyncCleanup(p.close)
        return p

    async def test_builtin_fetch_deadline_is_idle_but_broker_failure_propagates(self):
        p=self.make()
        consumer=SimpleNamespace(fetch=AsyncMock(side_effect=TimeoutError()))
        p.broker=AsyncMock(return_value=(None,consumer))
        self.assertEqual(await p.normalize(),0)
        self.assertEqual(p.stages['normalizer']['fetch_timeouts'],1)
        self.assertEqual(p.stages['normalizer']['failures'],0)
        p.broker=AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError):await p.normalize()

    async def test_blocked_lake_does_not_block_normalizer_or_clickhouse(self):
        p=self.make();entered=asyncio.Event();release=asyncio.Event();normal=[];ch=[]
        async def blocked():entered.set();await release.wait();return 1
        async def fast():normal.append(1);return 1
        async def other():ch.append(1);return 1
        tasks=[asyncio.create_task(p.run_stage("lake",blocked)),asyncio.create_task(p.run_stage("normalizer",fast)),asyncio.create_task(p.run_stage("clickhouse",other))]
        try:
            await asyncio.wait_for(entered.wait(),1)
            for _ in range(100):
                if len(normal)>=3 and len(ch)>=3:break
                await asyncio.sleep(0.001)
            self.assertGreaterEqual(len(normal),3);self.assertGreaterEqual(len(ch),3)
            self.assertEqual(p.stages["lake"]["completed"],0)
        finally:p.stop.set();release.set();await asyncio.gather(*tasks)

    async def test_destination_outage_keeps_peer_receipt_and_retries_only_failed_destination(self):
        p=self.make();group=[("one",[{}])]
        await p.apply("lake",group);p.sinks["clickhouse"].down=True
        with self.assertRaises(AnalyticsError):await p.apply("clickhouse",group)
        self.assertIn(("lake","one"),p.deliveries.confirmed)
        self.assertNotIn(("clickhouse","one"),p.deliveries.confirmed)
        self.assertEqual(p.sinks["clickhouse"].calls,[["one"]])
        p.sinks["clickhouse"].down=False;await p.apply("clickhouse",group)
        self.assertEqual(p.sinks["lake"].calls,[["one"]])
        joined=combined_receipt("one",p.deliveries.confirmed["lake","one"],p.deliveries.confirmed["clickhouse","one"])
        Store._validate_analytics_receipt("one",joined)

    async def test_poison_member_isolated_without_stopping_healthy_neighbor(self):
        p=self.make();p.sinks["lake"].bad={"bad"}
        self.assertEqual(await p.apply("lake",[("bad",[{}]),("good",[{}])]),1)
        self.assertIn(("lake","good"),p.deliveries.confirmed)
        self.assertEqual(p.deliveries.failed,[("lake","bad","BatchConflict")])

    async def test_timeout_defers_group_without_amplifying_into_individual_requests(self):
        p=self.make();p.sinks["lake"].apply_destination=lambda group:(_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(TimeoutError):await p.apply("lake",[("a",[{}]),("b",[{}])])
        self.assertEqual(len(p.deliveries.failed),2);self.assertFalse(p.deliveries.confirmed)

    async def test_normalization_timeout_preserves_retry_and_does_not_single_fallback(self):
        p=self.make();p.store.record_normalizations=AsyncMock(side_effect=TimeoutError())
        p.store.record_normalization=AsyncMock();msg=SimpleNamespace(nak=AsyncMock(),ack_sync=AsyncMock())
        with self.assertRaises(TimeoutError):await p.persist([{"capture_id":"id","result":{},"messages":[msg]}])
        p.store.record_normalization.assert_not_awaited();msg.nak.assert_awaited_once();msg.ack_sync.assert_not_awaited()

    async def test_normalization_conflict_preserves_valid_neighbor_and_ack(self):
        p=self.make();p.store.record_normalizations=AsyncMock(side_effect=ValueError())
        p.store.record_normalization=AsyncMock(side_effect=[ValueError(),{}])
        msgs=[SimpleNamespace(nak=AsyncMock(),ack_sync=AsyncMock()) for _ in range(2)]
        entries=[{"capture_id":str(i),"result":{},"messages":[m]} for i,m in enumerate(msgs)]
        self.assertEqual(await p.persist(entries),1)
        msgs[0].nak.assert_awaited_once();msgs[1].ack_sync.assert_awaited_once()

    async def test_uncertain_ack_does_not_rewrite_capture_quality(self):
        p=self.make();msg=SimpleNamespace(ack_sync=AsyncMock(side_effect=TimeoutError()),nak=AsyncMock())
        await p.ack([msg]);msg.nak.assert_awaited_once();p.store.pool.execute.assert_not_awaited()

    async def test_pressure_pauses_without_work_then_resumes(self):
        p=self.make();free=False;calls=[]
        p.store.raw.admission=lambda:{"accepting":free}
        async def step():calls.append(1);p.stop.set();return 1
        async def pause(seconds):
            nonlocal free
            if seconds == 0:return
            self.assertEqual(calls,[]);self.assertEqual(p.stages["lake"]["state"],"paused")
            free=True
        with patch.object(p,"pause",pause):await p.run_stage("lake",step)
        self.assertEqual(calls,[1])

    async def test_bad_receipt_never_confirms_other_destination(self):
        p=self.make();r=receipt("a","lake");r["count"]=2
        with self.assertRaises(Exception):await p.deliveries.confirm("lake",{"a":r})
        self.assertFalse(p.deliveries.confirmed)

    async def test_lost_owner_cancels_stages(self):
        p=self.make();owner=SimpleNamespace(fetchval=AsyncMock(side_effect=ConnectionError()))
        async def idle(*args):await asyncio.Event().wait()
        with patch.object(p,"relay",idle),patch.object(p,"normalize",idle),patch.object(p,"deliver",lambda target:idle()),patch.object(p,"maintenance",idle):
            with self.assertRaises(ExceptionGroup):await asyncio.wait_for(p.run(owner),1)
        self.assertTrue(all("inflight_since" not in v for v in p.stages.values()))

    async def test_broker_retries_incomplete_consumer_initialization(self):
        p=self.make();consumer=object()
        js=SimpleNamespace(pull_subscribe=AsyncMock(side_effect=[TimeoutError(),consumer]))
        connection=SimpleNamespace(is_closed=False,jetstream=lambda **kw:js,close=AsyncMock())
        with patch.object(worker.nats,"connect",AsyncMock(return_value=connection)) as connect, patch.object(worker,"ensure_stream",AsyncMock()):
            with self.assertRaises(TimeoutError):await p.broker()
            self.assertEqual(await p.broker(),(js,consumer))
            connect.assert_awaited_once()


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    make=PipelineTests.make

    async def test_failed_close_still_joins_peer_before_owner_can_release(self):
        p=self.make();entered=threading.Event();release=threading.Event();calls=[]
        def fail_once():
            calls.append(1)
            if len(calls)==1:raise RuntimeError("fixture_close_failure")
        def slow_close():
            entered.set();release.wait(3)
        p.sinks['lake'].close=fail_once;p.sinks['clickhouse'].close=slow_close
        task=asyncio.create_task(p.close())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait,2))
            await asyncio.sleep(.02)
            self.assertFalse(task.done())
        finally:release.set()
        with self.assertRaisesRegex(RuntimeError,'fixture_close_failure'):await task


    async def test_shutdown_does_not_start_another_delivery_group(self):
        p=self.make()
        p.deliveries.pending=AsyncMock(return_value=[{'batch_id':'first'},{'batch_id':'second'}])
        p.store.batch_rows=AsyncMock(return_value=[])
        async def apply(target,group):p.stop.set();return len(group)
        p.apply=AsyncMock(side_effect=apply)
        with patch.object(worker,'group_would_overflow',side_effect=[False,True]):
            self.assertEqual(await p.deliver('lake'),1)
        self.assertEqual(p.apply.await_count,1)
        self.assertEqual(p.apply.await_args.args[1],[('first',[])])

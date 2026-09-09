"""Bounded worker orchestration tests with in-memory transport/persistence fakes.

The actual serve loop and Store receipt-validation method run. No filesystem,
database, HTTP, NATS server, Docker control, or provider request is performed.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
import inspect
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from midas_data import worker
from midas_data.analytics import AnalyticsError, BatchConflict, SCHEMA_VERSION
from midas_data.store import Store as RealStore

_REAL_SLEEP = asyncio.sleep


class Acquire:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def get():
            return self.value
        return get().__await__()

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return False


class Transaction:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        self.store.staged = []

    async def __aexit__(self, kind, *args):
        if kind is None:
            self.store.markers.extend(self.store.staged)
            for batch, _ in self.store.staged:
                self.store.deferred.pop(batch, None)
        self.store.staged = []
        return False


class Connection:
    def __init__(self, store):
        self.store = store
        self.active_probes = self.max_active_probes = 0

    async def fetchval(self, query, *args):
        self.active_probes += 1
        self.max_active_probes = max(self.max_active_probes, self.active_probes)
        try:
            await _REAL_SLEEP(0)
            return True
        finally:
            self.active_probes -= 1

    def transaction(self):
        return Transaction(self.store)

    async def fetch(self, query, batch_ids, receipts, marker):
        assert query.lstrip().startswith("UPDATE normalization_runs")
        self.store.staged.extend(zip(batch_ids, [json.loads(item) for item in receipts]))
        return [{"batch_id": batch, "fact_count": 1} for batch in batch_ids]


class Pool:
    def __init__(self, store):
        self.store = store
        self.owner = Connection(store)

    def acquire(self):
        return Acquire(self.owner)

    async def release(self, owner):
        assert owner is self.owner
        self.store.events.append("owner_released")

    async def fetch(self, query, *args):
        if "FROM outbox" in query:
            return []
        assert "FROM normalization_runs" in query and "LIMIT 8" in query
        confirmed = {batch for batch, _ in self.store.markers}
        assert "analytics_retry_at" in query and "statement_timestamp()" in query
        assert "ORDER BY analytics_retry_at NULLS FIRST,recorded_at,batch_id" in query
        eligible = [(index, batch) for index, batch in enumerate(self.store.pending)
                    if batch not in confirmed and self.store.deferred.get(batch, {}).get("retry_at", 0) <= self.store.clock]
        eligible.sort(key=lambda item: (item[1] in self.store.deferred,
                      self.store.deferred.get(item[1], {}).get("retry_at", 0), item[0], item[1]))
        return [{"batch_id": batch} for _, batch in eligible[:8]]

    async def fetchval(self, query, *args):
        return len(self.store.errors)

    async def execute(self, query, *args):
        self.store.errors.append(args)


class StoreFake:
    # Exercise the real publication guard and atomic receipt transaction.
    mark_analytics = RealStore.mark_analytics
    mark_analytics_many = RealStore.mark_analytics_many
    _validate_analytics_receipt = staticmethod(RealStore._validate_analytics_receipt)

    def __init__(self, capture_ids=(), pending=()):
        self.pool = Pool(self)
        self.raw = SimpleNamespace(read=lambda digest: b"fixture")
        self.captures = {identifier: {"metadata": {"id": identifier}, "raw_sha256": "fixture", "runs": []}
                         for identifier in capture_ids}
        self.pending = list(pending)
        self.normalized = []
        self.group_normalizations = []
        self.markers, self.staged, self.errors, self.health = [], [], [], []
        self.poison_capture = None
        self.clock = 0.0
        self.deferred = {}
        self.attempts = {}
        self.defer_calls = []
        self.prepare_poison = set()
        self.rows_override = {}
        self.events = []

    async def open(self):
        return self

    async def close(self):
        self.events.append("store_closed")

    async def get_capture(self, identifier):
        return self.captures[identifier]

    @staticmethod
    def normalization_size(result):
        return 1

    async def record_normalizations(self, items):
        self.group_normalizations.append([identifier for identifier, _ in items])
        if any(identifier == self.poison_capture for identifier, _ in items):
            raise ValueError("fixture immutable output conflict")
        self.normalized.extend(identifier for identifier, _ in items)
        return [{} for _ in items]

    async def record_normalization(self, identifier, result):
        if identifier == self.poison_capture:
            raise ValueError("fixture immutable output conflict")
        self.normalized.append(identifier)
        return {}

    async def batch_rows(self, batch):
        if batch in self.prepare_poison:
            raise ValueError("fixture malformed analytical row")
        return self.rows_override.get(batch, [{"batch": batch}])

    async def defer_analytics(self, batch, error_code):
        self.defer_calls.append((batch, error_code))
        attempts = self.attempts.get(batch, 0) + 1
        self.attempts[batch] = attempts
        retry_at = self.clock + min(60, 2 ** min(attempts - 1, 6))
        self.deferred[batch] = {"attempts": attempts, "retry_at": retry_at, "last_error": error_code}
        return self.deferred[batch]

    async def heartbeat(self, state, details=None):
        self.health.append((state, details))


class Message:
    def __init__(self, identifier):
        self.data = json.dumps({"capture_id": str(identifier)}).encode()
        self.acks = self.naks = 0

    async def ack_sync(self, timeout):
        self.acks += 1

    async def nak(self, delay):
        self.naks += 1


def receipt(batch):
    summary = {"count": 1, "checksum": "a" * 64, "source_counts": {"fixture": 1},
               "available_frontier": "2026-09-08T12:00:00.000000Z"}
    destination = dict(summary, ok=True, receipt_present=True, schema_version=SCHEMA_VERSION)
    return dict(summary, batch_id=batch, ok=True,
                snapshot={"snapshot_id": 1, "scope": "batch_commit", "snapshot_time": "2026-09-08T12:00:01.000000Z"},
                lake=dict(destination), clickhouse=dict(destination))


class SinkFake:
    def __init__(self):
        self.group_calls, self.single_calls = [], []
        self.poison = set()
        self.group_reply = None
        self.unavailable = False

    def ensure_schema(self):
        pass

    def close(self):
        pass

    def apply_batches(self, group):
        self.group_calls.append([batch for batch, _ in group])
        if self.group_reply is not None:
            return self.group_reply
        if self.unavailable:
            raise AnalyticsError("fixture destination unavailable")
        if any(batch in self.poison for batch, _ in group):
            raise BatchConflict("fixture immutable fact conflict")
        return {batch: receipt(batch) for batch, _ in group}

    def apply_batch(self, batch, rows):
        self.single_calls.append(batch)
        if self.unavailable:
            raise AnalyticsError("fixture destination unavailable")
        if batch in self.poison:
            raise BatchConflict("fixture immutable fact conflict")
        return receipt(batch)


class WorkerPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def run_worker(self, store, sink, messages=(), iterations=1, iteration_advance=0.01, real_threads=False):
        stop = asyncio.Event()
        fetched = 0

        async def fetch(**kwargs):
            nonlocal fetched
            fetched += 1
            store.clock += iteration_advance
            if fetched >= iterations:
                stop.set()
            return list(messages) if fetched == 1 else []

        async def in_thread(function, *args, **kwargs):
            value = function(*args, **kwargs)
            return await value if inspect.isawaitable(value) else value

        async def sleep(seconds):
            store.clock += seconds

        consumer = SimpleNamespace(fetch=fetch)
        js = SimpleNamespace(pull_subscribe=AsyncMock(return_value=consumer))
        transport = SimpleNamespace(jetstream=lambda **kwargs: js, close=AsyncMock())
        settings = SimpleNamespace(database_dsn="fixture", raw_path="fixture", max_capture_bytes=1024,
                                   lake_catalog_dsn="fixture", lake_path="fixture", clickhouse_url="fixture",
                                   clickhouse_user="fixture", clickhouse_password="fixture", nats_url="fixture")
        result = SimpleNamespace(facts=[SimpleNamespace()], fields=[SimpleNamespace()])
        with ExitStack() as stack:
            stack.enter_context(patch.object(asyncio.get_running_loop(), "add_signal_handler"))
            stack.enter_context(patch.object(worker.asyncio, "Event", return_value=stop))
            if not real_threads:
                stack.enter_context(patch.object(worker.asyncio, "to_thread", side_effect=in_thread))
            stack.enter_context(patch.object(worker.asyncio, "sleep", new=AsyncMock(side_effect=sleep)))
            stack.enter_context(patch.object(worker, "Store", return_value=store))
            # Store is a constructor mock, but its sizing API is part of the contract.
            worker.Store.normalization_size.side_effect = StoreFake.normalization_size
            stack.enter_context(patch.object(worker, "RawStore", return_value=None))
            stack.enter_context(patch.object(worker, "AnalyticsSinks", return_value=sink))
            stack.enter_context(patch.object(worker.nats, "connect", new=AsyncMock(return_value=transport)))
            stack.enter_context(patch.object(worker, "ensure_stream", new=AsyncMock()))
            stack.enter_context(patch.object(worker, "ConsumerConfig", side_effect=lambda **kw: SimpleNamespace(**kw)))
            stack.enter_context(patch.object(worker, "CaptureMetadata", model_validate=lambda value: value))
            stack.enter_context(patch.object(worker, "normalize_capture", return_value=result))
            stack.enter_context(patch.object(worker, "batch_size", return_value=1))
            await worker.serve(settings)
        self.assertEqual(fetched, iterations)
        transport.close.assert_awaited_once()

    async def test_poison_normalization_rolls_back_group_then_valid_neighbor_commits_and_acks(self):
        bad, good = uuid4(), uuid4()
        store = StoreFake([bad, good])
        store.poison_capture = bad
        bad_message, good_message = Message(bad), Message(good)
        await self.run_worker(store, SinkFake(), [bad_message, good_message])
        self.assertEqual(store.group_normalizations, [[bad, good]])
        self.assertEqual(store.normalized, [good])
        self.assertEqual((bad_message.acks, bad_message.naks), (0, 1))
        self.assertEqual((good_message.acks, good_message.naks), (1, 0))
        self.assertTrue(any(args[0] == bad for args in store.errors))

    async def test_analytics_content_poison_does_not_block_valid_neighbor(self):
        store = StoreFake(pending=["bad", "good"])
        sink = SinkFake()
        sink.poison = {"bad"}
        await self.run_worker(store, sink)
        self.assertEqual(sink.single_calls, ["bad", "good"])
        self.assertEqual([batch for batch, _ in store.markers], ["good"])
        self.assertEqual(store.markers[0][1], receipt("good"))
        self.assertTrue(any(state == "degraded" for state, _ in store.health))

    async def test_incomplete_or_unverified_group_never_publishes_marker(self):
        for reply in ({"good": receipt("good")},
                      {"bad": dict(receipt("bad"), ok=False), "good": receipt("good")}):
            with self.subTest(reply_keys=list(reply)):
                store = StoreFake(pending=["bad", "good"])
                sink = SinkFake()
                sink.group_reply = reply
                sink.unavailable = True
                await self.run_worker(store, sink)
                self.assertEqual(store.markers, [])
                self.assertEqual(sink.single_calls, ["bad"])

    async def test_eight_old_poison_batches_defer_and_ninth_advances(self):
        bad = ["bad-" + str(index) for index in range(8)]
        store = StoreFake(pending=[*bad, "healthy-ninth"])
        sink = SinkFake()
        sink.poison = set(bad)
        await self.run_worker(store, sink, iterations=2)
        self.assertEqual(sink.group_calls, [bad, ["healthy-ninth"]])
        self.assertEqual(sink.single_calls, bad)
        self.assertEqual([batch for batch, _ in store.markers], ["healthy-ninth"])
        self.assertEqual({batch for batch, _ in store.defer_calls}, set(bad))
        self.assertTrue(all(entry["retry_at"] > store.clock for entry in store.deferred.values()))
        self.assertTrue(all(entry["last_error"] == "BatchConflict" for entry in store.deferred.values()))



    async def test_unattempted_ninth_has_priority_even_after_poison_retry_deadlines_expire(self):
        bad = ["bad-" + str(index) for index in range(8)]
        store = StoreFake(pending=[*bad, "healthy-ninth"])
        sink = SinkFake()
        sink.poison = set(bad)
        await self.run_worker(store, sink, iterations=2, iteration_advance=2.0)
        self.assertEqual(sink.group_calls[1], ["healthy-ninth", *bad[:7]])
        self.assertEqual([batch for batch, _ in store.markers], ["healthy-ninth"])
        self.assertEqual(sink.single_calls[8], "healthy-ninth")
        self.assertEqual(store.attempts["bad-7"], 1)
        self.assertEqual(store.attempts["bad-0"], 2)

    async def test_prepare_poison_is_deferred_and_does_not_hide_healthy_ninth(self):
        bad = ["malformed-" + str(index) for index in range(8)]
        store = StoreFake(pending=[*bad, "healthy-ninth"])
        store.prepare_poison = set(bad)
        sink = SinkFake()
        await self.run_worker(store, sink, iterations=2)
        self.assertEqual(sink.group_calls, [["healthy-ninth"]])
        self.assertEqual([batch for batch, _ in store.markers], ["healthy-ninth"])
        self.assertEqual(store.defer_calls, [(batch, "ValueError") for batch in bad])

    async def test_global_outage_retains_queue_and_stops_individual_fanout(self):
        names = ["waiting-" + str(index) for index in range(8)]
        store = StoreFake(pending=names)
        sink = SinkFake()
        sink.unavailable = True
        await self.run_worker(store, sink)
        self.assertEqual(sink.group_calls, [names])
        self.assertEqual(sink.single_calls, [names[0]])
        self.assertEqual(store.markers, [])
        self.assertEqual(store.pending, names)
        self.assertEqual(store.defer_calls, [(names[0], "AnalyticsError")])
        self.assertGreaterEqual(store.clock, 1.0)



    async def test_stages_overlap_without_reading_unpublished_normalization(self):
        capture = uuid4()
        message = Message(capture)
        store = StoreFake([capture], pending=["already-published"])
        sink = SinkFake()
        normalization_started = asyncio.Event()
        analytics_started = asyncio.Event()
        published = asyncio.Event()

        async def persist(items):
            store.group_normalizations.append([identifier for identifier, _ in items])
            normalization_started.set()
            await asyncio.wait_for(analytics_started.wait(), 1)
            self.assertEqual(message.acks, 0)
            self.assertEqual(store.normalized, [])
            store.normalized.extend(identifier for identifier, _ in items)
            store.pending.append("newly-published")
            published.set()
            return [{} for _ in items]

        async def apply(group):
            sink.group_calls.append([batch for batch, _ in group])
            analytics_started.set()
            await asyncio.wait_for(normalization_started.wait(), 1)
            self.assertEqual([batch for batch, _ in group], ["already-published"])
            await asyncio.wait_for(published.wait(), 1)
            return {batch: receipt(batch) for batch, _ in group}

        store.record_normalizations = persist
        sink.apply_batches = apply
        await asyncio.wait_for(self.run_worker(store, sink, [message]), 2)
        self.assertEqual(store.normalized, [capture])
        self.assertEqual((message.acks, message.naks), (1, 0))
        self.assertEqual([batch for batch, _ in store.markers], ["already-published"])
        self.assertIn("newly-published", store.pending)
        self.assertEqual(store.pool.owner.max_active_probes, 1)
        self.assertEqual(store.events[-2:], ["owner_released", "store_closed"])

    async def test_overlapping_stages_keep_analytical_row_budget_and_single_writer(self):
        capture = uuid4()
        names = ["bounded-" + str(index) for index in range(8)]
        store = StoreFake([capture], pending=names)
        store.rows_override = {batch: [{"batch": batch}] * 4001 for batch in names}
        sink = SinkFake()
        active = maximum = 0

        async def apply(group):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                self.assertLessEqual(len(group), 8)
                self.assertLessEqual(sum(len(rows) for _, rows in group), 10000)
                await _REAL_SLEEP(0)
                return SinkFake.apply_batches(sink, group)
            finally:
                active -= 1

        sink.apply_batches = apply
        await self.run_worker(store, sink, [Message(capture)])
        self.assertEqual(sink.group_calls, [names[index:index + 2] for index in range(0, 8, 2)])
        self.assertEqual(maximum, 1)
        self.assertEqual([batch for batch, _ in store.markers], names)
        self.assertEqual(store.pool.owner.max_active_probes, 1)

    async def test_sibling_failure_drains_inflight_sink_thread_before_owner_release(self):
        capture = uuid4()
        store = StoreFake([capture], pending=["in-flight"])
        loop = asyncio.get_running_loop()
        analytics_started = asyncio.Event()
        close_started = asyncio.Event()
        release_thread = threading.Event()
        writer_lock = threading.RLock()

        class BlockingSink(SinkFake):
            def apply_batches(self, group):
                with writer_lock:
                    self.group_calls.append([batch for batch, _ in group])
                    store.events.append("sink_write_started")
                    loop.call_soon_threadsafe(analytics_started.set)
                    if not release_thread.wait(3):
                        raise AssertionError("test sink thread did not receive release")
                    store.events.append("sink_write_finished")
                    return {batch: receipt(batch) for batch, _ in group}

            def close(self):
                store.events.append("sink_close_started")
                loop.call_soon_threadsafe(close_started.set)
                with writer_lock:
                    store.events.append("sink_closed")

        sink = BlockingSink()

        async def failing_capture(identifier):
            await asyncio.wait_for(analytics_started.wait(), 1)
            raise RuntimeError("fixture capture read unavailable")

        async def failing_error_record(*args):
            # Escapes failed_capture(), forcing TaskGroup to cancel its sibling.
            raise RuntimeError("fixture error store unavailable")

        store.get_capture = failing_capture
        store.pool.execute = failing_error_record
        task = asyncio.create_task(self.run_worker(store, sink, [Message(capture)], real_threads=True))
        try:
            await asyncio.wait_for(close_started.wait(), 2)
            self.assertIn("sink_write_started", store.events)
            self.assertNotIn("sink_write_finished", store.events)
            self.assertNotIn("owner_released", store.events)
            self.assertFalse(task.done())
        finally:
            release_thread.set()
        with self.assertRaises(ExceptionGroup):
            await asyncio.wait_for(task, 2)
        self.assertLess(store.events.index("sink_write_finished"), store.events.index("sink_closed"))
        self.assertLess(store.events.index("sink_closed"), store.events.index("owner_released"))
        self.assertLess(store.events.index("owner_released"), store.events.index("store_closed"))
        self.assertEqual(store.markers, [])  # Completed sink write remains pending for replay.
        self.assertEqual(sink.group_calls, [["in-flight"]])


if __name__ == "__main__":
    unittest.main()

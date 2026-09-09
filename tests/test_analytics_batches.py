"""Opt-in real grouped-sink proofs; no Docker control or external provider calls.

Run with DATA_INTEGRATION=1 and DATA_ANALYTICS_BATCH_INTEGRATION=1 in the
isolated data Compose runtime while its worker is paused by the operator.
These tests require the same advisory writer lock and preserve fixture rows.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from midas_data.analytics import AnalyticsError, AnalyticsSinks, BatchConflict, _prepare
from midas_data.settings import Settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("DATA_INTEGRATION") != "1" or os.getenv("DATA_ANALYTICS_BATCH_INTEGRATION") != "1",
        reason="real grouped sinks require both DATA_INTEGRATION and DATA_ANALYTICS_BATCH_INTEGRATION",
    ),
]


def row(**changes):
    result = {
        "fact_id": str(uuid4()), "capture_id": str(uuid4()), "chain": "base",
        "token_address": "0x" + uuid4().hex + uuid4().hex[:8],
        "metric": "price_usd", "value_text": "0.0000000000000000001234567890123456789",
        "value_type": "decimal", "unit": "USD/token", "category": "market",
        "occurred_at": "2026-09-08T12:00:00.123456Z", "time_basis": "source",
        "pointer": "/data/price", "quality": "unverified", "contract_version": "fixture-price-v1",
        "available_at": "2026-09-08T12:00:01.000000Z", "normalizer_version": "fixture-normalizer-v1",
        "source_kind": "fixture", "evidence_kind": "original_response",
    }
    result.update(changes)
    return result


class TracedSinks(AnalyticsSinks):
    def __init__(self, *args):
        self.sql_calls = []
        self.ch_calls = []
        super().__init__(*args)

    def _sql(self, query, params=None):
        self.sql_calls.append(query)
        return super()._sql(query, params)

    def _ch(self, query, **kwargs):
        self.ch_calls.append(query)
        return super()._ch(query, **kwargs)


@pytest.fixture(scope="module")
def sink():
    import asyncpg
    settings = Settings.from_env()
    loop = asyncio.new_event_loop()
    connection = loop.run_until_complete(asyncpg.connect(
        settings.database_dsn, timeout=5, server_settings={"statement_timeout": "5000"},
    ))
    locked = False
    sinks = None
    try:
        locked = loop.run_until_complete(connection.fetchval("SELECT pg_try_advisory_lock(60608002)"))
        assert locked, "The isolated data worker must be paused before grouped-sink integration"
        sinks = TracedSinks(
            settings.lake_catalog_dsn, settings.lake_path, settings.clickhouse_url,
            settings.clickhouse_user, settings.clickhouse_password,
        )
        sinks.ensure_schema()
        yield sinks
    finally:
        try:
            if sinks is not None:
                sinks.close()
        finally:
            try:
                if locked:
                    loop.run_until_complete(connection.fetchval("SELECT pg_advisory_unlock(60608002)"))
            finally:
                loop.run_until_complete(connection.close(timeout=5))
                loop.close()


def batch_name():
    return "group-fixture-" + uuid4().hex


def test_real_group_preserves_old_snapshot_membership_exact_values_and_replay(sink):
    old_id, first_id, repeated_id, empty_id = [batch_name() for _ in range(4)]
    old_row = row()
    old = sink.apply_batch(old_id, [old_row])
    facts = [row() for _ in range(601)]
    facts[0].update(value_text=None, unit=None, quality="missing")
    facts[1].update(value_text='aspas " barra \\ linha\n token 漢字', value_type="string")
    batches = [(old_id, [old_row]), (first_id, facts), (repeated_id, [facts[0]]), (empty_id, [])]
    sink.sql_calls.clear()
    sink.ch_calls.clear()
    results = sink.apply_batches(batches)
    assert all(result["ok"] for result in results.values())
    assert results[old_id]["snapshot"] == old["snapshot"]
    snapshots = {results[name]["snapshot"]["snapshot_id"] for name in (first_id, repeated_id, empty_id)}
    assert len(snapshots) == 1
    assert old["snapshot"]["snapshot_id"] not in snapshots
    assert sum(query.startswith("INSERT INTO lake.facts_log") for query in sink.sql_calls) == 1
    assert sum(query.startswith("INSERT INTO lake.batch_receipts") for query in sink.sql_calls) == 1
    assert sum(query.startswith("INSERT INTO midas_data.facts_log") for query in sink.ch_calls) == 1
    assert sum(query.startswith("INSERT INTO midas_data.batch_receipts_log") for query in sink.ch_calls) == 1
    # More than 256 identities must not cause a per-chunk SELECT loop.
    assert sum(query.startswith("SELECT source_kind,fact_id,row_hash") for query in sink.ch_calls) == 2
    # Every batch retains its own membership, even when another batch contains
    # the identical immutable fact. Values and per-batch hashes round-trip.
    for batch, facts_in_batch in batches:
        expected = _prepare(facts_in_batch)[1]
        assert results[batch]["checksum"] == expected["checksum"]
        assert results[batch]["count"] == expected["count"]
        assert sink.verify_batch(batch)["snapshot"] == results[batch]["snapshot"]
    before = sink.snapshot_info()
    assert sink.apply_batches(batches) == results
    assert sink.snapshot_info() == before
    assert sink.verify_batch(old_id)["snapshot"] == old["snapshot"]
    # The exact old commit is still queryable after the grouped commit.
    snapshot = old["snapshot"]["snapshot_id"]
    count = sink._sql(
        f"SELECT count(*) FROM lake.facts_log AT (VERSION => {int(snapshot)}) WHERE batch_id=?", [old_id],
    )
    assert count == [(1,)]
    stored = sink._read_batches([first_id, repeated_id], lake=False)
    assert len(stored[first_id]) == 601 and len(stored[repeated_id]) == 1


@pytest.mark.parametrize("failure", ["facts_after", "receipts_before", "receipts_after"])
def test_real_partial_group_recovers_without_duplicate_effect(sink, failure):
    first_id, second_id, empty_id = [batch_name() for _ in range(3)]
    shared = row()
    batches = [(first_id, [shared, row()]), (second_id, [shared]), (empty_id, [])]
    original = sink._ch
    injected = [False]

    def fail_once(query, **kwargs):
        is_facts = query.startswith("INSERT INTO midas_data.facts_log")
        is_receipts = query.startswith("INSERT INTO midas_data.batch_receipts_log")
        trigger = not injected[0] and ((failure == "facts_after" and is_facts) or
                                       (failure.startswith("receipts_") and is_receipts))
        if trigger:
            injected[0] = True
            if failure != "receipts_before":
                original(query, **kwargs)  # The real HTTP insertion has succeeded.
            raise AnalyticsError("injected failure at grouped sink boundary")
        return original(query, **kwargs)
    sink._ch = fail_once
    try:
        with pytest.raises(AnalyticsError):
            sink.apply_batches(batches)
    finally:
        sink._ch = original
    assert injected[0]
    names = [batch for batch, _ in batches]
    assert len(sink._receipts_many(names, lake=True)) == 3
    before = sink.snapshot_info()
    recovered = sink.apply_batches(batches)
    assert sink.snapshot_info() == before
    assert all(result["ok"] for result in recovered.values())
    assert [recovered[name]["count"] for name in names] == [2, 1, 0]
    assert sink.apply_batches(batches) == recovered
    counts = sink._ch(
        "SELECT count() AS n FROM midas_data.facts_log "
        "WHERE batch_id IN (SELECT arrayJoin(JSONExtract({names:String},'Array(String)'))) FORMAT JSON",
        params={"names": __import__("json").dumps(names)}, result=True,
    )
    assert int(counts[0]["n"]) == 3
    assert len(sink._receipts_many(names, lake=False)) == 3


def test_real_cross_batch_conflict_does_not_change_prior_evidence(sink):
    old_id, new_id = batch_name(), batch_name()
    original = row()
    committed = sink.apply_batch(old_id, [original])
    before = sink.snapshot_info()
    with pytest.raises(BatchConflict):
        sink.apply_batches([(new_id, [dict(original, value_text="different")])])
    assert sink.snapshot_info() == before
    assert sink.verify_batch(old_id) == committed
    assert sink._receipt(new_id, lake=True) is None
    assert sink._receipt(new_id, lake=False) is None

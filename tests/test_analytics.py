"""Unit checks only; real DuckLake/PostgreSQL/ClickHouse integration is separate."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from midas_data.analytics import (
    AnalyticsError, AnalyticsSinks, BatchConflict, SCHEMA_VERSION,
    _batch_id, _dsn_with_limits, _identity, _prepare, _receipt_matches, _row,
)


def fact(**changes):
    result = {
        "fact_id": str(uuid4()), "capture_id": str(uuid4()),
        "chain": "sol", "token_address": "fixture-token", "metric": "price_usd",
        "value_text": "0.000000000000000000123456789012345678901234567890",
        "value_type": "decimal", "unit": "USD/token", "category": "market",
        "occurred_at": "2026-09-08T12:00:00.123456Z", "time_basis": "source",
        "pointer": "/data/price", "quality": "unverified",
        "contract_version": "fixture-price-v1",
        "available_at": "2026-09-08T12:00:01.000000Z",
        "normalizer_version": "fixture-normalizer-v1",
        "source_kind": "fixture", "evidence_kind": "original_response",
    }
    result.update(changes)
    return result


class IdentityTests(unittest.TestCase):
    def test_exact_value_and_missing_remain_distinct(self):
        a = fact()
        original = a["value_text"]
        rows, _ = _prepare([a])
        self.assertEqual(rows[0]["value_text"], original)
        null = fact(value_text=None, quality="missing")
        empty = fact(value_text="", value_type="string")
        zero = fact(value_text="0")
        rows, _ = _prepare([null, empty, zero])
        self.assertEqual({r["value_text"] for r in rows}, {None, "", "0"})

    def test_hash_is_order_independent_but_content_sensitive(self):
        a, b = fact(), fact()
        first = _prepare([a, b])[1]
        self.assertEqual(first, _prepare([b, a])[1])
        b["unit"] = "quote/token"
        self.assertNotEqual(first["checksum"], _prepare([a, b])[1]["checksum"])

    def test_duplicate_input_ids_are_not_silently_dropped(self):
        a = fact()
        with self.assertRaises(BatchConflict):
            _prepare([a, dict(a)])

    def test_identical_stored_retries_collapse_but_conflicts_fail(self):
        a = fact()
        rows, summary = _prepare([a, dict(a)], stored=True)
        self.assertEqual(summary["count"], 1)
        self.assertEqual(rows, [_row(a)])
        with self.assertRaises(BatchConflict):
            _prepare([a, dict(a, value_text="1")], stored=True)

    def test_source_kind_is_an_identity_namespace_and_count_dimension(self):
        a = fact()
        b = dict(a, source_kind="legacy_gmgn", evidence_kind="legacy_reserialized")
        rows, summary = _prepare([a, b])
        self.assertEqual(len(rows), 2)
        self.assertEqual(summary["source_counts"], {"fixture": 1, "legacy_gmgn": 1})
        with self.assertRaises(ValueError):
            _prepare([{k: v for k, v in a.items() if k != "source_kind"}])

    def test_offsets_canonicalize_without_changing_instants(self):
        a = fact()
        b = dict(a, occurred_at="2026-09-08T09:00:00.123456-03:00")
        self.assertEqual(_prepare([a])[1], _prepare([b])[1])
        with self.assertRaises(ValueError):
            _prepare([dict(a, available_at="2026-09-08T12:00:01")])

    def test_float_and_unknown_schema_are_rejected(self):
        with self.assertRaises(ValueError):
            _prepare([fact(value_text=1.2)])
        with self.assertRaises(ValueError):
            _prepare([fact(invented_column="must-not-disappear")])

    def test_limits_reject_whole_batch(self):
        with patch("midas_data.analytics.MAX_BATCH_ROWS", 1):
            with self.assertRaises(ValueError):
                _prepare([fact(), fact()])
        with patch("midas_data.analytics.MAX_BATCH_BYTES", 1):
            with self.assertRaises(ValueError):
                _prepare([fact()])

    def test_receipt_checks_count_checksum_provenance_and_version(self):
        _, summary = _prepare([fact()])
        receipt = AnalyticsSinks._make_receipt("batch", summary)
        self.assertTrue(_receipt_matches(receipt, summary))
        for key, value in (
            ("row_count", 2), ("checksum", "bad"),
            ("source_counts_json", '{"gmgn":1}'), ("schema_version", "future"),
            ("available_frontier", None),
        ):
            with self.subTest(key=key):
                self.assertFalse(_receipt_matches(dict(receipt, **{key: value}), summary))

    def test_empty_batch_has_receipt_semantics(self):
        rows, summary = _prepare([])
        self.assertEqual(rows, [])
        self.assertEqual(summary["count"], 0)
        self.assertIsNone(summary["available_frontier"])
        self.assertEqual(summary["source_counts"], {})

    def test_batch_identifier_cannot_be_sql(self):
        self.assertEqual(_batch_id("sha256:ab_12-34"), "sha256:ab_12-34")
        with self.assertRaises(ValueError):
            _batch_id("'; DROP DATABASE x;--")

    def test_catalog_dsn_has_connection_and_server_limits(self):
        value = _dsn_with_limits("postgresql://user:password@catalog/lake")
        self.assertIn("connect_timeout=5", value)
        self.assertIn("statement_timeout%3D30000", value)


class HttpBoundaryTests(unittest.TestCase):
    def make_sink(self, handler):
        sink = AnalyticsSinks("postgresql://user:do-not-leak@catalog/lake", "/lake", "http://clickhouse:8123", "user", "do-not-leak")
        sink._http.close()
        sink._http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://clickhouse:8123/")
        self.addCleanup(sink.close)
        return sink

    def test_http_failure_does_not_expose_response_or_credentials(self):
        sink = self.make_sink(lambda request: httpx.Response(500, text="query password=do-not-leak"))
        with self.assertRaises(AnalyticsError) as raised:
            sink._ch("SELECT 1")
        self.assertNotIn("do-not-leak", str(raised.exception))
        self.assertIn("500", str(raised.exception))

    def test_transport_error_is_redacted(self):
        def fail(request):
            raise httpx.ConnectError("postgresql://user:do-not-leak@host", request=request)
        sink = self.make_sink(fail)
        with self.assertRaises(AnalyticsError) as raised:
            sink._ch("SELECT 1")
        self.assertNotIn("do-not-leak", str(raised.exception))

    def test_response_budget_is_enforced(self):
        sink = self.make_sink(lambda request: httpx.Response(200, content=b"x" * 20))
        with patch("midas_data.analytics.MAX_RESPONSE_BYTES", 10):
            with self.assertRaises(AnalyticsError):
                sink._ch("SELECT 1 FORMAT JSON", result=True)

    def test_query_values_are_http_parameters(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={"data": []})
        sink = self.make_sink(respond)
        sink._ch("SELECT {value:String} FORMAT JSON", params={"value": "');x"}, result=True)
        self.assertEqual(seen[0].url.params["param_value"], "');x")
        self.assertNotIn("');x", seen[0].url.params["query"])

    def test_http_200_with_error_body_is_not_success(self):
        sink = self.make_sink(lambda request: httpx.Response(200, text="Code: 241. query failed"))
        with self.assertRaises(AnalyticsError):
            sink._ch("INSERT INTO facts FORMAT JSONEachRow")

    def test_compatible_columns_do_not_hide_incompatible_engine(self):
        sink = self.make_sink(lambda request: httpx.Response(200, json={"data": []}))
        with patch.object(sink, "_ch", return_value=[{"engine": "ReplacingMergeTree", "create_table_query": ""}]):
            with self.assertRaises(AnalyticsError):
                sink._check_ch_kind("facts_log", "MergeTree")

    def test_view_without_conflict_guard_is_rejected(self):
        sink = self.make_sink(lambda request: httpx.Response(200, json={"data": []}))
        with patch.object(sink, "_ch", return_value=[{"engine": "View", "create_table_query": "CREATE VIEW facts AS SELECT * FROM facts_log"}]):
            with self.assertRaises(AnalyticsError):
                sink._check_ch_kind("facts", "View")

    def test_close_is_idempotent_and_disables_work(self):
        sink = self.make_sink(lambda request: httpx.Response(200))
        sink.close()
        sink.close()
        with self.assertRaises(AnalyticsError):
            sink.apply_batch("after-close", [])


class MemorySinks(AnalyticsSinks):
    """Fault injection for orchestration; deliberately not a database substitute."""
    def __init__(self):
        super().__init__("postgresql://fixture:fixture@invalid/lake", "/fixture", "http://invalid", "fixture", "fixture")
        self._ready = True
        self.tables = {True: {}, False: {}}
        self.receipts = {True: {}, False: {}}
        self.lake_commits = 0
        self.fail_ch_once = True

    def _apply_lake(self, batch, rows, summary):
        prior = self.receipts[True].get(batch)
        if prior and not _receipt_matches(prior, summary):
            raise BatchConflict("batch reused")
        if not prior:
            self.tables[True][batch] = copy.deepcopy(rows)
            self.receipts[True][batch] = self._make_receipt(batch, summary)
            self.lake_commits += 1

    def _apply_ch(self, batch, rows, summary):
        if self.fail_ch_once:
            self.tables[False][batch] = copy.deepcopy(rows[:1])
            self.fail_ch_once = False
            raise AnalyticsError("injected failure after partial second sink")
        self.tables[False][batch] = copy.deepcopy(rows)
        self.receipts[False][batch] = self._make_receipt(batch, summary)

    def _receipt(self, batch, *, lake):
        return self.receipts[lake].get(batch)

    def _read_batch(self, batch, *, lake):
        return copy.deepcopy(self.tables[lake].get(batch, []))

    def _check_identities(self, rows, *, lake):
        expected = {_identity(row): row for row in rows}
        for batch in self.tables[lake].values():
            for row in batch:
                if _identity(row) in expected and expected[_identity(row)] != row:
                    raise BatchConflict("immutable identity conflict")

    def _batch_snapshot(self, batch, checksum):
        return {"snapshot_id": self.lake_commits, "scope": "unit-fixture"}


class OrchestrationTests(unittest.TestCase):
    def test_partial_sink_failure_is_not_reported_as_success_and_retry_converges(self):
        sink = MemorySinks()
        self.addCleanup(sink.close)
        rows = [fact(), fact()]
        with self.assertRaises(AnalyticsError):
            sink.apply_batch("partial-batch", rows)
        self.assertEqual(sink.lake_commits, 1)
        self.assertFalse(sink.verify_batch("partial-batch")["ok"])
        result = sink.apply_batch("partial-batch", rows)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(sink.lake_commits, 1)
        again = sink.apply_batch("partial-batch", list(reversed(rows)))
        self.assertEqual(again["checksum"], result["checksum"])
        self.assertEqual(sink.lake_commits, 1)

    def test_reusing_batch_with_different_content_fails_before_second_sink(self):
        sink = MemorySinks()
        self.addCleanup(sink.close)
        sink.fail_ch_once = False
        a = fact()
        sink.apply_batch("immutable", [a])
        with self.assertRaises(BatchConflict):
            sink.apply_batch("immutable", [dict(a, value_text="999")])
        self.assertEqual(sink.tables[False]["immutable"][0]["value_text"], a["value_text"])

    def test_cross_sink_same_count_with_different_fact_is_not_equal(self):
        sink = MemorySinks()
        self.addCleanup(sink.close)
        sink.fail_ch_once = False
        sink.apply_batch("check", [fact()])
        changed = fact()
        sink.tables[False]["check"] = [changed]
        _, changed_summary = _prepare([changed])
        sink.receipts[False]["check"] = sink._make_receipt("check", changed_summary)
        result = sink.verify_batch("check")
        self.assertTrue(result["lake"]["ok"])
        self.assertTrue(result["clickhouse"]["ok"])
        self.assertFalse(result["ok"])



class LakeSqlTests(unittest.TestCase):
    """Real in-memory DuckDB SQL; DuckLake storage commits remain integration gates."""

    def setUp(self):
        import duckdb
        self.sink = AnalyticsSinks(
            "postgresql://fixture:fixture@invalid/lake", "/fixture",
            "http://invalid", "fixture", "fixture",
        )
        self.addCleanup(self.sink.close)
        self.sink._lake = duckdb.connect(":memory:")
        self.sink._ready = True
        self.sink._lake.execute("SET TimeZone='UTC'")
        self.sink._lake.execute("CREATE SCHEMA lake")
        types = self.sink._lake_types()
        self.sink._lake.execute("CREATE TABLE lake.facts_log (" + ",".join(
            f"{name} {kind}" for name, kind in types.items()) + ")")
        self.sink._lake.execute(
            "CREATE TABLE lake.batch_receipts (batch_id VARCHAR,checksum VARCHAR,row_count BIGINT,"
            "source_counts_json VARCHAR,available_frontier VARCHAR,created_at TIMESTAMPTZ,schema_version VARCHAR)"
        )
        self.statements = []
        self.fail_receipt_once = False
        self.inline_value = None
        execute = self.sink._sql

        def observed(query, params=None):
            self.statements.append(query)
            # Only the catalog-specific metadata functions are substituted.
            if "FROM lake.options()" in query:
                return [(self.inline_value,)] if self.inline_value is not None else []
            if query.startswith("CALL lake.set_option"):
                self.inline_value = "1000"
                return []
            if query.startswith("CALL lake.set_commit_message"):
                return []
            if self.fail_receipt_once and query.startswith("INSERT INTO lake.batch_receipts"):
                self.fail_receipt_once = False
                raise AnalyticsError("injected receipt failure")
            return execute(query, params)

        self.sink._sql = observed

    def apply(self, batch, rows):
        prepared, summary = _prepare(rows)
        self.sink._apply_lake(batch, prepared, summary)
        return prepared, summary

    def test_single_insert_round_trips_exact_text_null_unicode_and_utc(self):
        rows = [fact() for _ in range(601)]
        rows[0].update(value_text=None, unit=None, quality="missing")
        rows[1].update(value_text="", value_type="string")
        rows[2].update(value_text="null", value_type="string")
        rows[3].update(value_text='aspas " barra \\ linha\n token 漢字', value_type="string")
        rows[4].update(occurred_at="2026-09-08T09:00:00.123456-03:00")
        expected, summary = self.apply("large-sql", rows)
        actual = self.sink._read_batch("large-sql", lake=True)
        self.assertEqual(_prepare(actual, stored=True), (expected, summary))
        self.assertEqual(sum(q.startswith("INSERT INTO lake.facts_log") for q in self.statements), 1)
        self.assertEqual(sum("WHERE fact_id IN" in q for q in self.statements), 1)
        self.assertTrue(_receipt_matches(self.sink._receipt("large-sql", lake=True), summary))

    def test_receipt_failure_rolls_back_all_facts_then_retry_converges(self):
        rows = [fact(), fact(value_text=None, quality="missing")]
        self.fail_receipt_once = True
        with self.assertRaises(AnalyticsError):
            self.apply("rollback", rows)
        self.assertEqual(self.sink._read_batch("rollback", lake=True), [])
        self.assertIsNone(self.sink._receipt("rollback", lake=True))
        expected, summary = self.apply("rollback", rows)
        self.apply("rollback", list(reversed(rows)))
        self.assertEqual(_prepare(self.sink._read_batch("rollback", lake=True), stored=True), (expected, summary))
        physical = self.sink._lake.execute("SELECT count(*) FROM lake.facts_log").fetchone()[0]
        self.assertEqual(physical, 2)
        self.assertEqual(self.sink._lake.execute("SELECT count(*) FROM lake.batch_receipts").fetchone()[0], 1)

    def test_global_identity_conflict_is_checked_across_batches(self):
        original = fact()
        self.apply("original", [original])
        with self.assertRaises(BatchConflict):
            self.apply("conflicting", [dict(original, value_text="999")])
        self.assertIsNone(self.sink._receipt("conflicting", lake=True))
        self.assertEqual(self.sink._lake.execute("SELECT count(*) FROM lake.facts_log").fetchone()[0], 1)

    def test_identity_namespace_remains_source_kind(self):
        original = fact()
        self.apply("fixture-source", [original])
        other = dict(original, source_kind="legacy_gmgn", evidence_kind="legacy_reserialized", value_text="999")
        _, summary = self.apply("legacy-source", [other])
        self.assertEqual(summary["source_counts"], {"legacy_gmgn": 1})
        self.assertEqual(self.sink._lake.execute("SELECT count(*) FROM lake.facts_log").fetchone()[0], 2)

    def test_empty_batch_gets_receipt_without_fact_insert(self):
        _, summary = self.apply("empty-sql", [])
        self.assertEqual(sum(q.startswith("INSERT INTO lake.facts_log") for q in self.statements), 0)
        self.assertEqual(sum("WHERE fact_id IN" in q for q in self.statements), 0)
        self.assertTrue(_receipt_matches(self.sink._receipt("empty-sql", lake=True), summary))

    def test_schema_enables_inlining_only_for_receipts(self):
        with patch.object(self.sink, "_ch", return_value=[]), patch.object(
            self.sink, "_check_ch_schema"), patch.object(self.sink, "_check_ch_kind"):
            self.sink.ensure_schema()
        options = [q for q in self.statements if q.startswith("CALL lake.set_option")]
        self.assertEqual(options, [
            "CALL lake.set_option('data_inlining_row_limit', 1000, table_name => 'batch_receipts')"
        ])



    def test_schema_reentry_does_not_rewrite_existing_inlining_option(self):
        self.inline_value = "1000"
        with patch.object(self.sink, "_ch", return_value=[]), patch.object(
            self.sink, "_check_ch_schema"), patch.object(self.sink, "_check_ch_kind"):
            self.sink.ensure_schema()
            self.sink.ensure_schema()
        self.assertFalse(any(q.startswith("CALL lake.set_option") for q in self.statements))

    def test_schema_changes_old_inlining_once(self):
        self.inline_value = "0"
        with patch.object(self.sink, "_ch", return_value=[]), patch.object(
            self.sink, "_check_ch_schema"), patch.object(self.sink, "_check_ch_kind"):
            self.sink.ensure_schema()
            self.sink.ensure_schema()
        self.assertEqual(sum(q.startswith("CALL lake.set_option") for q in self.statements), 1)



class CompactionTests(unittest.TestCase):
    """Maintenance orchestration contract; persistent compaction is a live gate."""

    def make_sink(self, counts=None, outputs=None, sizes=None):
        sink = AnalyticsSinks(
            "postgresql://fixture:fixture@invalid/lake", "/fixture",
            "http://invalid", "fixture", "fixture",
        )
        sink._ready = True
        self.addCleanup(sink.close)
        self.calls = []
        self.thread_state = {"value": 2}
        counts = counts if counts is not None else {"facts_log": 64, "batch_receipts": 64}
        outputs = outputs if outputs is not None else {
            "facts_log": [("main", "facts_log", 64, 1)],
            "batch_receipts": [("main", "batch_receipts", 64, 1)],
        }
        sizes = sizes if sizes is not None else {}

        def sql(query, params=None):
            self.calls.append((query, params))
            if query == "SELECT current_setting('threads')":
                return [(self.thread_state["value"],)]
            if query.startswith("SET threads="):
                self.thread_state["value"] = int(query.split("=")[1])
                return []
            if "ducklake_list_files" in query:
                return [(min(counts.get(params[0], 0), params[1]),)]
            if "FROM lake.options()" in query:
                value = sizes.get(params[0])
                return [(value,)] if value is not None else []
            if query.startswith("CALL lake.set_option"):
                sizes["main." + params[0]] = "8000000"
                return []
            if query.startswith("CALL ducklake_merge_adjacent_files"):
                return outputs.get(params[0], [])
            raise AssertionError("Unexpected maintenance SQL: " + query)

        sink._sql = sql
        return sink

    def test_argument_limits_fail_before_any_database_operation(self):
        sink = self.make_sink()
        for kwargs in (
            {"max_output_files": 0}, {"max_output_files": 3}, {"max_output_files": True},
            {"max_output_files": 1.5}, {"min_input_files": 1},
            {"min_input_files": 10001}, {"min_input_files": False},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                sink.compact_files(**kwargs)
        self.assertEqual(self.calls, [])

    def test_below_threshold_only_reads_bounded_file_metadata(self):
        sink = self.make_sink(counts={"facts_log": 63, "batch_receipts": 0})
        result = sink.compact_files()
        self.assertEqual(result["files_created"], 0)
        self.assertTrue(all(t["status"] == "below_threshold" for t in result["tables"].values()))
        self.assertEqual(len(self.calls), 2)
        for query, params in self.calls:
            self.assertIn("LIMIT ?", query)
            self.assertIn("data_file_size_bytes < 1048576", query)
            self.assertEqual(params[1], 64)

    def test_output_budget_is_global_and_never_runs_cleanup(self):
        sink = self.make_sink(outputs={
            "facts_log": [("main", "facts_log", 100, 1)],
            "batch_receipts": [("main", "batch_receipts", 200, 1)],
        })
        result = sink.compact_files()
        self.assertEqual((result["files_processed"], result["files_created"]), (300, 2))
        self.assertEqual(result["tables"]["batch_receipts"]["status"], "compacted")
        merges = [(q, p) for q, p in self.calls if q.startswith("CALL ducklake_merge_adjacent_files")]
        self.assertEqual(len(merges), 2)
        self.assertEqual(merges[0][1], ["facts_log", 1])
        self.assertIn("max_file_size => 1048576", merges[0][0])
        for query, _ in self.calls:
            for forbidden in ("CHECKPOINT", "expire_snapshots", "cleanup_old_files", "delete_orphaned_files", "flush_inlined_data"):
                self.assertNotIn(forbidden, query)

    def test_remaining_budget_is_passed_to_second_table(self):
        sink = self.make_sink()
        result = sink.compact_files()
        merges = [p for q, p in self.calls if q.startswith("CALL ducklake_merge_adjacent_files")]
        self.assertEqual(merges, [["facts_log", 1], ["batch_receipts", 1]])
        self.assertEqual(result["files_created"], 2)
        self.assertEqual(set(result["tables"]), {"facts_log", "batch_receipts"})

    def test_target_size_option_is_idempotent_and_table_scoped(self):
        sizes = {"main.facts_log": "512000000", "main.batch_receipts": "8000000"}
        sink = self.make_sink(sizes=sizes)
        sink.compact_files()
        sink.compact_files()
        sets = [(q, p) for q, p in self.calls if q.startswith("CALL lake.set_option")]
        self.assertEqual(len(sets), 1)
        self.assertIn("'target_file_size', '8MB', table_name => ?", sets[0][0])
        self.assertEqual(sets[0][1], ["facts_log"])

    def test_no_mergeable_group_is_explicit_and_keeps_budget_available(self):
        sink = self.make_sink(outputs={"batch_receipts": [("main", "batch_receipts", 64, 1)]})
        result = sink.compact_files()
        self.assertEqual(result["tables"]["facts_log"]["status"], "no_mergeable_group")
        self.assertEqual(result["files_created"], 1)
        merges = [p for q, p in self.calls if q.startswith("CALL ducklake_merge_adjacent_files")]
        self.assertEqual(merges[1], ["batch_receipts", 1])

    def test_unexpected_engine_result_is_not_reported_as_success(self):
        for rows in (
            [("main", "other_table", 64, 1)],
            [("main", "facts_log", 64, 3)],
            [("main", "facts_log", 64, 1)] * 2,
            [("main", "facts_log", 64, 1)] * 3,
        ):
            with self.subTest(rows=rows):
                sink = self.make_sink(outputs={"facts_log": rows})
                with self.assertRaises(AnalyticsError):
                    sink.compact_files()

    def test_closed_sink_cannot_run_maintenance(self):
        sink = self.make_sink()
        sink.close()
        with self.assertRaises(AnalyticsError):
            sink.compact_files()
        self.assertEqual(self.calls, [])




    def test_single_global_output_skips_second_table(self):
        sink = self.make_sink()
        result = sink.compact_files(max_output_files=1)
        self.assertEqual(result["files_created"], 1)
        self.assertEqual(result["tables"]["batch_receipts"]["status"], "output_budget_exhausted")
        merges = [p for q, p in self.calls if q.startswith("CALL ducklake_merge_adjacent_files")]
        self.assertEqual(merges, [["facts_log", 1]])

    def test_original_thread_setting_is_restored_between_table_merges(self):
        sink = self.make_sink()
        self.thread_state["value"] = 4
        sink.compact_files()
        changes = [q for q, _ in self.calls if q.startswith("SET threads=")]
        self.assertEqual(changes, ["SET threads=1", "SET threads=4", "SET threads=1", "SET threads=4"])
        self.assertEqual(self.thread_state["value"], 4)

    def test_thread_setting_is_restored_after_merge_failure(self):
        sink = self.make_sink()
        self.thread_state["value"] = 3
        execute = sink._sql

        def failing_merge(query, params=None):
            if query.startswith("CALL ducklake_merge_adjacent_files"):
                self.assertEqual(self.thread_state["value"], 1)
                raise AnalyticsError("injected merge failure")
            return execute(query, params)

        sink._sql = failing_merge
        with self.assertRaises(AnalyticsError):
            sink.compact_files()
        self.assertEqual(self.thread_state["value"], 3)
        self.assertEqual(self.calls[-1][0], "SET threads=3")


class SpillDirectoryTests(unittest.TestCase):
    """Real DuckDB settings and OS directories; catalog I/O is deliberately skipped."""

    def make_sink(self):
        sink = AnalyticsSinks(
            "postgresql://fixture:fixture@invalid/lake", "/fixture",
            "http://invalid", "fixture", "fixture",
        )
        self.addCleanup(sink.close)
        return sink

    def test_private_writable_spill_directory_and_caps_are_applied_then_removed(self):
        import os
        from pathlib import Path
        import tempfile
        import duckdb

        sink = self.make_sink()
        sql = sink._sql

        def no_catalog(query, params=None):
            if query.startswith(("LOAD ", "ATTACH ")):
                return []
            return sql(query, params)

        sink._sql = no_catalog
        sink._open_lake()
        owned = Path(sink._lake_temp_directory.name)
        self.assertEqual(owned.parent, Path(tempfile.gettempdir()))
        self.assertTrue(owned.is_dir())
        self.assertEqual(
            sink._lake.execute("SELECT current_setting('temp_directory')").fetchone()[0],
            str(owned),
        )
        (owned / "write-proof").write_bytes(b"fixture spill")
        if os.name != "nt":
            self.assertEqual(owned.stat().st_uid, os.getuid())
            self.assertEqual(owned.stat().st_mode & 0o077, 0)
        reference = duckdb.connect(":memory:")
        try:
            reference.execute("SET max_temp_directory_size='128MB'")
            reference.execute("SET memory_limit='256MB'")
            for name in ("max_temp_directory_size", "memory_limit"):
                expected = reference.execute("SELECT current_setting(?)", [name]).fetchone()[0]
                actual = sink._lake.execute("SELECT current_setting(?)", [name]).fetchone()[0]
                self.assertEqual(actual, expected)
        finally:
            reference.close()
        with tempfile.TemporaryDirectory(prefix="midas-unrelated-test-") as unrelated:
            sink.close()
            self.assertFalse(owned.exists())
            self.assertTrue(Path(unrelated).is_dir())
        sink.close()

    def test_initialization_failure_cleans_only_owned_temp_directory(self):
        from pathlib import Path

        sink = self.make_sink()
        sql = sink._sql
        owned = []

        def fail_extension(query, params=None):
            if query.startswith("LOAD "):
                owned.append(Path(sink._lake_temp_directory.name))
                raise AnalyticsError("injected extension failure")
            return sql(query, params)

        sink._sql = fail_extension
        with self.assertRaises(AnalyticsError):
            sink._open_lake()
        self.assertEqual(len(owned), 1)
        self.assertFalse(owned[0].exists())
        self.assertIsNone(sink._lake)
        self.assertIsNone(sink._lake_temp_directory)

    def test_close_failure_still_cleans_directory_and_closes_http(self):
        from pathlib import Path
        import tempfile

        class FailingConnection:
            def close(self):
                raise RuntimeError("sensitive fixture connection detail")

        sink = self.make_sink()
        sink._lake = FailingConnection()
        sink._lake_temp_directory = tempfile.TemporaryDirectory(prefix="midas-close-test-")
        owned = Path(sink._lake_temp_directory.name)
        with self.assertRaises(AnalyticsError) as raised:
            sink.close()
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertFalse(owned.exists())
        self.assertTrue(sink._http.is_closed)
        self.assertTrue(sink._closed)
        self.assertIsNone(sink._lake)
        sink.close()

    def test_close_handles_instances_without_temp_directory_attribute(self):
        sink = self.make_sink()
        del sink._lake_temp_directory
        sink.close()
        sink.close()
        self.assertTrue(sink._http.is_closed)




class GroupBoundaryTests(unittest.TestCase):
    def test_group_limits_and_cross_batch_conflict_precede_sink_access(self):
        from midas_data.analytics import _prepare_batches, batch_size, _json
        a = fact()
        self.assertEqual(batch_size([dict(a, occurred_at="2026-09-08T09:00:00.123456-03:00")]),
                         len(_json(_row(a)).encode("utf-8")))
        self.assertEqual(batch_size([]), 0)
        for batches in ([], [("b" + str(i), []) for i in range(33)], [["b", []]]):
            with self.assertRaises(ValueError):
                _prepare_batches(batches)
        with self.assertRaises(BatchConflict):
            _prepare_batches([("a", []), ("a", [])])
        with self.assertRaises(BatchConflict):
            _prepare_batches([("a", [a]), ("b", [dict(a, value_text="different")])])
        prepared = _prepare_batches([("a", [a]), ("b", [a])])
        self.assertEqual(sum(len(rows) for rows, _ in prepared.values()), 2)
        with patch("midas_data.analytics.MAX_GROUP_ROWS", 1):
            with self.assertRaises(ValueError):
                _prepare_batches([("a", [a]), ("b", [a])])
        with patch("midas_data.analytics.MAX_BATCH_BYTES", batch_size([a])):
            with self.assertRaises(ValueError):
                _prepare_batches([("a", [a]), ("b", [a])])
        sink = AnalyticsSinks("fixture", "/fixture", "http://invalid", "fixture", "fixture")
        self.addCleanup(sink.close)
        with patch.object(sink, "ensure_schema") as access:
            with self.assertRaises(BatchConflict):
                sink.apply_batches([("a", [a]), ("b", [dict(a, unit="different")])])
            access.assert_not_called()

    def test_ten_thousand_identities_are_all_checked_with_bounded_read_sets(self):
        rows = [fact() for _ in range(10_000)]
        observed=[]
        def response(request):
            ids=[v for k,v in request.url.params.multi_items() if k.startswith("param_id")]
            self.assertLessEqual(len(ids),256)
            self.assertLess(len(str(request.url).encode()),32768)
            self.assertEqual(request.url.params["max_bytes_to_read"],str(128*1024**2))
            observed.extend(ids)
            return httpx.Response(200,json={"data":[]})
        sink=AnalyticsSinks("fixture","/fixture","http://invalid","fixture","fixture")
        sink._http.close()
        sink._http=httpx.Client(base_url="http://invalid",transport=httpx.MockTransport(response))
        self.addCleanup(sink.close)
        sink._check_group_identities(rows,lake=False)
        self.assertEqual(sorted(observed),sorted(r["fact_id"] for r in rows))

    def test_bulk_receipts_detect_duplicate_content_conflict(self):
        _, summary = _prepare([fact()])
        good = AnalyticsSinks._make_receipt("one", summary)
        sink = AnalyticsSinks("fixture", "/fixture", "http://invalid", "fixture", "fixture")
        self.addCleanup(sink.close)
        with patch.object(sink, "_ch", return_value=[good, dict(good, checksum="different")]):
            with self.assertRaises(BatchConflict):
                sink._receipts_many(["one"], lake=False)


class GroupLakeSqlTests(unittest.TestCase):
    setUp = LakeSqlTests.setUp

    def test_single_transaction_preserves_per_batch_membership_and_empty_receipts(self):
        from midas_data.analytics import _prepare_batches
        a, b = fact(value_text=None, unit=None), fact(value_text='漢字 "\\\n', value_type="string")
        prepared = _prepare_batches([("one", [a, b]), ("two", [a]), ("empty", [])])
        self.sink._apply_lake_batches(prepared)
        stored = self.sink._read_batches(list(prepared), lake=True)
        receipts = self.sink._receipts_many(list(prepared), lake=True)
        for batch, (rows, summary) in prepared.items():
            self.assertEqual(_prepare(stored[batch], stored=True), (rows, summary))
            self.assertTrue(_receipt_matches(receipts[batch], summary))
        self.assertEqual(sum(s.startswith("INSERT INTO lake.facts_log") for s in self.statements), 1)
        self.assertEqual(sum(s.startswith("INSERT INTO lake.batch_receipts") for s in self.statements), 1)
        self.assertEqual(self.statements.count("COMMIT"), 1)
        self.statements.clear()
        self.sink._apply_lake_batches(prepared)
        self.assertFalse(any(s.startswith("INSERT") or "set_commit_message" in s for s in self.statements))
        self.assertEqual(self.sink._lake.execute("SELECT count(*) FROM lake.facts_log").fetchone()[0], 3)

    def test_old_batch_replay_is_excluded_from_new_commit_metadata(self):
        import json
        from midas_data.analytics import _prepare_batches
        a = fact()
        rows, summary = _prepare([a])
        self.sink._apply_lake("old", rows, summary)
        execute = self.sink._sql
        metadata = []

        def observe(query, params=None):
            if "set_commit_message" in query:
                metadata.append(json.loads(params[0]))
            return execute(query, params)
        self.sink._sql = observe
        self.sink._apply_lake_batches(_prepare_batches([("old", [a]), ("new", [a]), ("empty", [])]))
        self.assertEqual([x["batch_id"] for x in metadata[0]["batches"]], ["empty", "new"])
        self.assertEqual(metadata[0]["schema_version"], SCHEMA_VERSION)
        self.assertEqual(self.sink._lake.execute("SELECT count(*) FROM lake.facts_log").fetchone()[0], 2)
        with self.assertRaises(BatchConflict):
            self.sink._apply_lake_batches(_prepare_batches([("other", [dict(a, unit="different")])]))
        self.assertIsNone(self.sink._receipt("other", lake=True))

    def test_group_receipt_failure_rolls_back_all_facts_and_retry_is_exact(self):
        from midas_data.analytics import _prepare_batches
        prepared = _prepare_batches([("one", [fact()]), ("two", [fact()]), ("empty", [])])
        self.fail_receipt_once = True
        with self.assertRaises(AnalyticsError):
            self.sink._apply_lake_batches(prepared)
        self.assertEqual(self.sink._lake.execute("SELECT count(*) FROM lake.facts_log").fetchone()[0], 0)
        self.assertEqual(self.sink._lake.execute("SELECT count(*) FROM lake.batch_receipts").fetchone()[0], 0)
        self.sink._apply_lake_batches(prepared)
        self.assertEqual(len(self.sink._receipts_many(list(prepared), lake=True)), 3)

    def test_bulk_snapshot_lookup_matches_pairs_versions_and_old_exact_metadata(self):
        from midas_data.analytics import _json
        self.sink._lake.execute("CREATE TABLE lake.history(snapshot_id BIGINT,snapshot_time TIMESTAMPTZ,commit_extra_info VARCHAR)")
        self.sink._lake.execute("CREATE MACRO lake.snapshots() AS TABLE SELECT * FROM lake.history")
        old = _json({"batch_id": "old", "checksum": "a", "schema_version": SCHEMA_VERSION})
        group = {"group_version": "midas-analytics-group-v1", "schema_version": SCHEMA_VERSION,
                 "batches": [{"batch_id": "one", "checksum": "b"}, {"batch_id": "two", "checksum": "c"}]}
        split_pair = dict(group, batches=[{"batch_id": "bad", "checksum": "other"}, {"batch_id": "other", "checksum": "d"}])
        wrong_version = dict(group, group_version="future", batches=[{"batch_id": "future", "checksum": "f"}])
        for identifier, metadata in enumerate([old, _json(group), "not-json", _json(split_pair), _json(wrong_version)], 1):
            self.sink._lake.execute("INSERT INTO lake.history VALUES (?, '2026-09-08T12:00:00Z', ?)", [identifier, metadata])
        found = self.sink._batch_snapshots({"old": "a", "one": "b", "two": "c", "bad": "d", "future": "f"})
        self.assertEqual({k: v["snapshot_id"] for k, v in found.items()}, {"old": 1, "one": 2, "two": 2})
        self.assertEqual(self.sink._batch_snapshot("old", "a"), found["old"])
        self.assertEqual(self.sink._batch_snapshot("one", "b"), found["one"])
        self.assertIsNone(self.sink._batch_snapshot("one", "wrong"))

    def test_partial_clickhouse_write_retry_keeps_memberships_and_one_logical_identity(self):
        import json
        from midas_data.analytics import _prepare_batches, _row_hash
        a = fact()
        b = dict(a, source_kind="legacy_gmgn", evidence_kind="legacy_reserialized")
        batches = [("one", [a, b]), ("two", [a]), ("empty", [])]
        facts, receipts, writes = [], [], []
        fail = [True]

        def ch(query, *, params=None, data=None, result=False, param_body=False, max_rows=10000):
            if query.startswith("INSERT"):
                writes.append(query)
                target = receipts if "batch_receipts_log" in query else facts
                target.extend(copy.deepcopy(data))
                if fail[0] and "facts_log" in query:
                    fail[0] = False
                    raise AnalyticsError("injected uncertain successful facts insert")
                return []
            if "batch_receipts_log" in query:
                ids = json.loads(params["batches"])
                return copy.deepcopy([r for r in receipts if r["batch_id"] in ids])
            if query.startswith("SELECT source_kind"):
                ids = list(params.values())
                tuples = {(r["source_kind"], r["fact_id"], r["row_hash"]) for r in facts if r["fact_id"] in ids}
                return [dict(zip(("source_kind", "fact_id", "row_hash"), r)) for r in tuples]
            ids = json.loads(params["batches"])
            return copy.deepcopy([r for r in facts if r["batch_id"] in ids])
        self.sink._ch = ch
        self.sink._batch_snapshots = lambda checksums: {
            key: {"snapshot_id": 7, "snapshot_time": "2026-09-08T12:00:00.000000Z", "scope": "batch_commit"}
            for key in checksums
        }
        with self.assertRaises(AnalyticsError):
            self.sink.apply_batches(batches)
        self.assertEqual(len(self.sink._receipts_many([b[0] for b in batches], lake=True)), 3)
        self.assertEqual(len(facts), 3)
        result = self.sink.apply_batches(batches)
        self.assertTrue(all(r["ok"] for r in result.values()))
        self.assertEqual([result[b]["count"] for b in ("one", "two", "empty")], [2, 1, 0])
        self.assertEqual(len(facts), 3)
        self.assertEqual(len({_identity(r) for r in facts}), 2)
        self.assertEqual(len(receipts), 3)
        self.assertEqual(len([w for w in writes if ".facts_log " in w]), 1)
        before = copy.deepcopy(result)
        self.assertEqual(self.sink.apply_batches(batches), before)
        facts[0]["value_text"] = "tampered"
        with self.assertRaises(BatchConflict):
            self.sink.apply_batches(batches)


if __name__ == "__main__":
    unittest.main()

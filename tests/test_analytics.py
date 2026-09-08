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


if __name__ == "__main__":
    unittest.main()

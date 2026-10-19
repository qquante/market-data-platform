"""Local regressions for restore reconciliation; no Docker or provider calls."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "data_platform"))
spec = importlib.util.spec_from_file_location("restore_reconcile", ROOT / "scripts/gmgn-restore-reconcile.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
from midas_data.analytics import _prepare, _row_hash

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        row = dict(fact_id="00000000-0000-0000-0000-000000000001",
            capture_id="00000000-0000-0000-0000-000000000002",chain="base",token_address="fixture",
            metric="price_usd",value_text="0.001",value_type="decimal",unit="USD/token",category="market",
            occurred_at="2026-09-09T00:00:00.000000Z",time_basis="source",pointer="/price",quality="unverified",
            contract_version="fixture-v1",available_at="2026-09-09T00:00:01.000000Z",
            normalizer_version="fixture-v1",source_kind="fixture",evidence_kind="original_response")
        self.rows, s = _prepare([row])
        self.expected = dict(batch_id="pending",fact_count=1,available_at=row["available_at"])
        self.lake = [dict(self.rows[0],row_hash=_row_hash(self.rows[0]))]
        self.receipts = [dict(batch_id="pending",checksum=s["checksum"],row_count=1,
            source_counts_json='{"fixture":1}',available_frontier=s["available_frontier"],
            created_at="2026-09-09T01:00:00Z",schema_version=s["schema_version"])]
        child = dict(s,ok=True,receipt_present=True)
        self.run = dict(available_at=row["available_at"],fact_count=1,analytics_applied_at="2026-09-09T01:00:00Z",
            analytics_receipt=dict(s,batch_id="pending",ok=True,lake=child,clickhouse=copy.deepcopy(child),
                snapshot=dict(snapshot_id=11,scope="batch_commit")))
    def check(self, **changes):
        args=dict(expected=self.expected,pg_rows=self.rows,lake_rows=self.lake,receipts=self.receipts,
                  run=self.run,baseline_snapshot=10,added=True)
        args.update(changes)
        return gate.check_batch(**args)
    def test_exact_pending_delivery(self):
        self.assertEqual(self.check()["count"],1)
    def test_duplicate_and_missing_physical_facts(self):
        for rows in (self.lake*2,[]):
            with self.subTest(rows=rows),self.assertRaises(ValueError): self.check(lake_rows=rows)
    def test_changed_value_or_time_even_with_valid_hash(self):
        for change in (dict(value_text="999"),dict(available_at="2026-09-09T02:00:00.000000Z")):
            row=dict(self.rows[0],**change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.check(lake_rows=[dict(row,row_hash=_row_hash(row))])
    def test_invalid_row_hash(self):
        with self.assertRaises(ValueError): self.check(lake_rows=[dict(self.lake[0],row_hash="0"*64)])
    def test_missing_duplicate_wrong_receipt(self):
        for receipts in ([],self.receipts*2,[dict(self.receipts[0],checksum="0"*64)]):
            with self.subTest(receipts=receipts),self.assertRaises(ValueError): self.check(receipts=receipts)
    def test_wrong_destination_or_batch_receipt(self):
        for kind in ("destination","batch","checksum"):
            run=copy.deepcopy(self.run)
            if kind=="destination": run["analytics_receipt"]["clickhouse"]["ok"]=False
            if kind=="batch": run["analytics_receipt"]["batch_id"]="other"
            if kind=="checksum": run["analytics_receipt"]["clickhouse"]["checksum"]="0"*64
            with self.subTest(kind=kind),self.assertRaises(ValueError): self.check(run=run)
    def test_new_delivery_requires_new_snapshot(self):
        run=copy.deepcopy(self.run)
        run["analytics_receipt"]["snapshot"]["snapshot_id"]=10
        with self.assertRaises(ValueError): self.check(run=run)
    def test_existing_lake_delivery_preserves_old_snapshot(self):
        run=copy.deepcopy(self.run)
        run["analytics_receipt"]["snapshot"]["snapshot_id"]=9
        self.assertEqual(self.check(run=run,added=False)["count"],1)
    def test_original_pg_count_and_time(self):
        for change in (dict(fact_count=2),dict(available_at="2026-09-09T02:00:00Z")):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.check(expected=dict(self.expected,**change))
    def test_empty_pending_batch(self):
        _,s=_prepare([])
        receipt=dict(self.receipts[0],checksum=s["checksum"],row_count=0,source_counts_json="{}",available_frontier=None)
        child=dict(s,ok=True,receipt_present=True)
        run=dict(self.run,fact_count=0,analytics_receipt=dict(s,batch_id="pending",ok=True,lake=child,clickhouse=child,
            snapshot=dict(snapshot_id=11,scope="batch_commit")))
        self.assertEqual(self.check(expected=dict(self.expected,fact_count=0),pg_rows=[],lake_rows=[],
                                    receipts=[receipt],run=run)["count"],0)
    def test_only_previously_pending_batches_can_be_added(self):
        self.assertEqual(gate.validate_added(["a","b"],["b"]),{"b"})
        self.assertEqual(gate.validate_added([],[]),set())
        for eligible,added in ((["a"],["other"]),(["a"],["a","a"]),(["a","a"],[])):
            with self.subTest(eligible=eligible,added=added),self.assertRaises(ValueError):
                gate.validate_added(eligible,added)
    def test_original_physical_bag_rejects_loss_change_or_duplicate(self):
        original=gate.fingerprint_rows([(1,"old"),(2,"old")])
        gate.require_same(original,original,"historical")
        for rows in ([(1,"old")],[(1,"old"),(2,"changed")],[(1,"old"),(2,"old"),(2,"old")]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                gate.require_same(original,gate.fingerprint_rows(rows),"preserved")

    def test_group_snapshot_must_bind_the_exact_batch_and_checksum(self):
        from midas_data.analytics import SCHEMA_VERSION
        member=dict(batch_id="a",checksum="hash")
        metadata=dict(schema_version=SCHEMA_VERSION,group_version="midas-analytics-group-v1",batches=[member])
        gate.validate_commit(metadata,"a","hash")
        for changes in (dict(batches=[]),dict(batches=[member,member]),dict(schema_version="other"),
                        dict(batches=[dict(member,checksum="wrong")]),dict(batches=[dict(member,batch_id="other")])):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                gate.validate_commit(dict(metadata,**changes),"a","hash")
    def test_legacy_snapshot_membership_is_supported(self):
        from midas_data.analytics import SCHEMA_VERSION
        gate.validate_commit(dict(schema_version=SCHEMA_VERSION,batch_id="a",checksum="hash"),"a","hash")

if __name__=="__main__": unittest.main()

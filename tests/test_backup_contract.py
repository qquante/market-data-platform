"""Local PowerShell contract tests; never contact Docker or production data."""
from __future__ import annotations

import base64
import ast
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gmgn-data-backup.ps1"
POWERSHELL = shutil.which("pwsh")
ARTIFACTS = ("midas_data.dump", "midas_lake.dump", "raw.tar", "lake.tar", "baseline.json")



@unittest.skipUnless(SCRIPT.exists(), "Repository backup script required")
class EvidenceFormulaTests(unittest.TestCase):
    """Execute only pure formula helpers, without database or Docker imports."""

    @classmethod
    def setUpClass(cls):
        script = SCRIPT.read_text(encoding="utf-8-sig")
        probe = re.search(r"\$script:ProbeCode = @'\n(.*?)\n'@", script, re.S).group(1)
        tree = ast.parse(probe)
        names = {"delivery_fields", "row_expression", "delivery_sql"}
        helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        cls.formula = {}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), "backup-pure-helpers", "exec"), cls.formula)

    def test_rebuild_reconciles_delivery_without_hiding_attempt_or_content_changes(self):
        derived = self.formula["delivery_fields"](2)
        self.assertEqual(derived, ("analytics_applied_at", "analytics_receipt", "analytics_retry_at", "analytics_last_error"))
        before = dict(batch_id="fixture", fact_count=2, analytics_attempts=3,
                      analytics_applied_at=None, analytics_receipt=None,
                      analytics_retry_at="2026-09-09T01:00:00Z", analytics_last_error="sink_unavailable")
        after = dict(before, analytics_applied_at="2026-09-09T02:00:00Z", analytics_receipt={"ok": True},
                     analytics_retry_at=None, analytics_last_error=None)
        project = lambda row: {key: value for key, value in row.items() if key not in derived}
        delivery = lambda row: [row["batch_id"], *(row.get(key) for key in derived)]
        digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest(project(before)), digest(project(after)))
        self.assertNotEqual(digest(delivery(before)), digest(delivery(after)))
        for field, value in (("analytics_attempts", 4), ("fact_count", 1), ("batch_id", "other")):
            with self.subTest(field=field):
                self.assertNotEqual(digest(project(before)), digest(project(dict(before, **{field: value}))))
        for field in derived:
            with self.subTest(writer_changes=field):
                self.assertNotEqual(digest(delivery(before)), digest(delivery(dict(before, **{field: "changed"}))))

    def test_legacy_formula_is_unchanged_and_new_formula_preserves_other_tables(self):
        expression = self.formula["row_expression"]
        self.assertEqual(expression("normalization_runs", True, 1),
                         "to_jsonb(t) - ARRAY['analytics_applied_at','analytics_receipt']")
        self.assertEqual(self.formula["delivery_sql"](1),
                         "SELECT jsonb_build_array(batch_id,analytics_applied_at,analytics_receipt)::text AS doc FROM normalization_runs ORDER BY batch_id")
        self.assertNotIn("analytics_attempts", expression("normalization_runs", True, 2))
        self.assertEqual(expression("captures", True, 2), "to_jsonb(t)")
        self.assertEqual(expression("normalization_runs", False, 2), "to_jsonb(t)")

    def test_new_delivery_formula_tolerates_schema_before_migration_005(self):
        self.assertEqual(self.formula["delivery_sql"](2),
                         "SELECT jsonb_build_array(batch_id,analytics_applied_at,analytics_receipt,to_jsonb(r)->'analytics_retry_at',to_jsonb(r)->'analytics_last_error')::text AS doc FROM normalization_runs r ORDER BY batch_id")
        for version in (0, 3):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "unsupported_evidence_version"):
                self.formula["delivery_fields"](version)


@unittest.skipUnless(POWERSHELL and SCRIPT.exists(), "Host PowerShell 7 and repository scripts required")
class BackupContractTests(unittest.TestCase):
    def ps(self, body: str):
        path = str(SCRIPT).replace("'", "''")
        command = f". '{path}'; {body}"
        encoded = base64.b64encode(command.encode("utf-16le")).decode()
        return subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=30,
        )

    def fixture(self, directory: str, **changes):
        root = Path(directory)
        artifacts = []
        for name in ARTIFACTS:
            value = ("fixture:"+name).encode()
            (root / name).write_bytes(value)
            artifacts.append({"name":name,"bytes":len(value),"sha256":hashlib.sha256(value).hexdigest()})
        manifest = {"schema_version":1,"status":"complete","source_project":"midas-data","artifacts":artifacts}
        manifest.update(changes)
        self.manifest(root, manifest)
        return root, manifest

    @staticmethod
    def manifest(root, value):
        body = json.dumps(value).encode()
        (root / "manifest.json").write_bytes(body)
        (root / "manifest.sha256").write_text(hashlib.sha256(body).hexdigest())

    def read(self, root):
        escaped = str(root).replace("'", "''")
        return self.ps(f"$null=Read-BackupManifest '{escaped}'; 'accepted'")

    def test_valid_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _ = self.fixture(temp)
            result = self.read(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("accepted", result.stdout)

    def test_tampered_payload_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _ = self.fixture(temp)
            (root / "raw.tar").write_bytes(b"changed")
            self.assertNotEqual(self.read(root).returncode, 0)

    def test_manifest_hash_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root, _ = self.fixture(temp)
            (root / "manifest.json").write_text("{}")
            self.assertNotEqual(self.read(root).returncode, 0)

    def test_traversal_and_duplicate_artifacts_rejected(self):
        for name in ("../raw.tar", "midas_data.dump"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root, manifest = self.fixture(temp)
                manifest["artifacts"][2]["name"] = name
                self.manifest(root, manifest)
                self.assertNotEqual(self.read(root).returncode, 0)

    def test_foreign_or_incomplete_backup_rejected(self):
        for changes in ({"source_project":"midas-fomo"}, {"status":"incomplete"}, {"schema_version":2}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temp:
                root, _ = self.fixture(temp, **changes)
                self.assertNotEqual(self.read(root).returncode, 0)

    def test_project_guard_rejects_other_names(self):
        result = self.ps("Assert-Project 'midas-fomo'")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.ps("Assert-Project 'midas-data'; Assert-Project 'midas-data-restore'").returncode, 0)

    def test_existing_restore_volume_refused_without_mutation(self):
        result = self.ps(r"""
function Invoke-Docker([string[]]$Arguments) {
    if ($Arguments[0] -eq 'volume') { return 'midas-data-restore_raw' }
    if ($Arguments[0] -cnotin @('ps','network')) { throw 'Unexpected mutation' }
    return @()
}
Assert-FreshRestore
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already has resources", result.stderr)

    def test_failure_resumes_only_previously_running_new_services(self):
        with tempfile.TemporaryDirectory() as temp:
            backup = str(Path(temp) / "new-backup").replace("'", "''")
            result = self.ps(r"""
$script:BackupPath = '__BACKUP__'
$script:calls = [Collections.Generic.List[string]]::new()
function Get-ProjectVolumes { return @{} }
function Get-Images { return @{worker='image'} }
function Get-ConfigDigests { return @{} }
function Invoke-Docker { return 'image' }
function Invoke-Compose([string]$Project,[string[]]$Arguments) {
    if ($Project -cne 'midas-data') { throw 'Wrong project' }
    if ($Arguments[0] -eq 'ps') { return @('postgres','api') }
    $script:calls.Add(($Arguments -join ' '))
}
function Assert-NoOtherWriters { throw 'simulated probe refusal' }
try { Invoke-Backup } catch {
    if ($_.Exception.Message -cne 'simulated probe refusal') { throw }
}
if (($script:calls -join '|') -cne 'stop --timeout 60 api|start api') { throw 'Unsafe resume set' }
'passed'
""".replace("__BACKUP__", backup))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("passed", result.stdout)

    def test_compose_always_receives_explicit_env_and_project(self):
        result = self.ps(r"""
$script:ResolvedEnv = 'opaque-env-path'
$script:ComposePath = 'compose.data.yaml'
function Invoke-Docker([string[]]$Arguments) { return ($Arguments -join '|') }
$actual = Invoke-Compose 'midas-data-restore' @('ps')
if ($actual -cne 'compose|--env-file|opaque-env-path|--project-name|midas-data-restore|--file|compose.data.yaml|ps') {
    throw 'Compose isolation arguments missing'
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)



    def test_restore_suffix_is_strict(self):
        self.assertEqual(self.ps("Assert-Project 'midas-data-restore-v2'").returncode, 0)
        for project in ("midas-data-restore-", "midas-data-restore-v2-extra",
                        "midas-data-restore_V2", "midas-data-restore-V2", "midas-fomo"):
            with self.subTest(project=project):
                self.assertNotEqual(self.ps(f"Assert-Project '{project}'").returncode, 0)

    def test_fresh_suffix_preserves_previous_rehearsal(self):
        result = self.ps(r"""
function Invoke-Docker([string[]]$Arguments) {
    if ($Arguments[0] -eq 'volume') { return 'midas-data-restore_raw' }
    if ($Arguments[0] -cnotin @('ps','network')) { throw 'Unexpected mutation' }
    if (($Arguments -join '|') -notmatch 'project=midas-data-restore-v2') { throw 'Wrong project filter' }
    return @()
}
Assert-FreshRestore 'midas-data-restore-v2'
""")
        self.assertEqual(result.returncode, 0, result.stderr)



    def test_failure_pauses_and_resumes_running_bridge(self):
        with tempfile.TemporaryDirectory() as temp:
            backup = str(Path(temp) / "new-backup").replace("'", "''")
            result = self.ps(r"""
$script:BackupPath = '__BACKUP__'
$script:calls = [Collections.Generic.List[string]]::new()
function Get-ProjectVolumes { return @{} }
function Get-Images { return @{worker='image'} }
function Get-ConfigDigests { return @{} }
function Invoke-Docker { return 'image' }
function Invoke-Compose([string]$Project,[string[]]$Arguments) {
    if ($Project -cne 'midas-data') { throw 'Wrong project' }
    if ($Arguments[0] -eq 'ps') { return @('postgres','api','bridge','worker') }
    $script:calls.Add(($Arguments -join ' '))
}
function Assert-NoOtherWriters { throw 'simulated source refusal' }
try { Invoke-Backup } catch {
    if ($_.Exception.Message -cne 'simulated source refusal') { throw }
}
if (($script:calls -join '|') -cne 'stop --timeout 60 api bridge worker|start api bridge worker') {
    throw 'Running bridge was not preserved in the pause/resume set'
}
'passed'
""".replace("__BACKUP__", backup))
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_restore_rejects_writers_before_docker(self):
        for token in ("api", "worker", "bridge", "ingestion", "--profile=ingestion"):
            with self.subTest(token=token):
                result = self.ps(r"""
function Invoke-Docker { throw 'Docker must not be reached' }
Invoke-Compose 'midas-data-restore-v3' @('up','__TOKEN__')
""".replace("__TOKEN__", token))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Restore commands cannot target", result.stderr)
                self.assertNotIn("Docker must not be reached", result.stderr)

    def test_config_fingerprints_exclude_both_env_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            migrations = root / "data_platform" / "midas_data" / "migrations"
            migrations.mkdir(parents=True)
            (migrations / "004_bridge.sql").write_text("fixture migration")
            (migrations / "005_analytics_retry.sql").write_text("fixture retry migration")
            envs = root / ".data" / "gmgn-data"
            envs.mkdir(parents=True)
            (envs / "runtime.env").write_text("must not be read")
            (envs / "bridge.env").write_text("must not be read")
            escaped = str(root).replace("'", "''")
            result = self.ps(r"""
$script:Workspace = '__ROOT__'
function Get-FileDigest([string]$Path) {
    if ($Path.EndsWith('.env')) { throw 'Credential file was read' }
    return 'fixture-hash'
}
$config=Get-ConfigDigests
if ($config.Count -ne 5 -or -not $config.Contains('data_platform/midas_data/migrations/004_bridge.sql') -or
    -not $config.Contains('data_platform/midas_data/migrations/005_analytics_retry.sql')) {
    throw 'Migration fingerprint missing'
}
""".replace("__ROOT__", escaped))
            self.assertEqual(result.returncode, 0, result.stderr)


    def test_evidence_accepts_unique_json_after_real_duckdb_progress_prefix(self):
        result = self.ps(r"""
function Invoke-Compose {
    $progressPrefix=' 99% '+([string][char]0x2595)+(([string][char]0x2588)*65)+' '
    if ($progressPrefix.Length -ne 72) { throw 'Fixture must reproduce the observed prefix length' }
    return @('Container fixture Starting',($progressPrefix+'MIDAS_BACKUP_EVIDENCE={"raw":{"objects":2},"ready_batches":3}'))
}
$actual=Get-Evidence 'midas-data-restore-test'
if ($actual.raw.objects -ne 2 -or $actual.ready_batches -ne 3) { throw 'Evidence was not preserved' }
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_evidence_rejects_missing_and_duplicate_markers(self):
        for lines in (
            "@('progress only')",
            "@('MIDAS_BACKUP_EVIDENCE={}', 'MIDAS_BACKUP_EVIDENCE={}')",
            "@('MIDAS_BACKUP_EVIDENCE={}MIDAS_BACKUP_EVIDENCE={}')",
        ):
            with self.subTest(lines=lines):
                result = self.ps(r"""
function Invoke-Compose { return __LINES__ }
$null=Get-Evidence 'midas-data-restore-test'
""".replace("__LINES__", lines))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("no unique result", result.stderr)

    def test_evidence_invalid_json_error_does_not_expose_payload(self):
        result = self.ps(r"""
function Invoke-Compose { return 'MIDAS_BACKUP_EVIDENCE={postgresql://fixture-secret}' }
$null=Get-Evidence 'midas-data-restore-test'
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid JSON", result.stderr)
        self.assertNotIn("fixture-secret", result.stdout+result.stderr)


    def test_evidence_version_supports_legacy_and_rejects_unrecognized_baselines(self):
        result = self.ps(r"""
if ((Get-EvidenceVersion ('{}' | ConvertFrom-Json)) -ne 1) { throw 'Legacy baseline rejected' }
if ((Get-EvidenceVersion ('{"evidence_schema_version":2}' | ConvertFrom-Json)) -ne 2) { throw 'Current baseline rejected' }
foreach ($json in @('{"evidence_schema_version":1}', '{"evidence_schema_version":3}',
                    '{"evidence_schema_version":"2"}', '{"evidence_schema_version":true}',
                    '{"evidence_schema_version":null}')) {
    $rejected=$false
    try { $null=Get-EvidenceVersion ($json | ConvertFrom-Json) }
    catch { $rejected=$_.Exception.Message -eq 'Unsupported backup evidence schema version.' }
    if (-not $rejected) { throw 'Unsupported version accepted' }
}
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_probe_receives_current_default_and_explicit_legacy_version(self):
        result = self.ps(r"""
$script:versions=[Collections.Generic.List[string]]::new()
function Invoke-Compose([string]$Project,[string[]]$Arguments) {
    if ($Project -ne 'midas-data-restore-test') { throw 'Wrong target' }
    $script:versions.Add($Arguments[-1])
    return 'MIDAS_BACKUP_EVIDENCE={"ok":true}'
}
$null=Get-Evidence 'midas-data-restore-test'
$null=Get-Evidence 'midas-data-restore-test' -EvidenceVersion 1
if (($script:versions -join ',') -ne '2,1') { throw 'Probe evidence versions not forwarded' }
""")
        self.assertEqual(result.returncode, 0, result.stderr)


    def reconciliation_setup(self):
        return r"""
$baseline='{"evidence_schema_version":2,"operational":{"facts":{"rows":3,"sha256":"op"}},"raw":{"objects":1},"source_counts":[],"ready_batches":2,"pending_captures":1,"lake_snapshot":[7,"2026-09-09T00:00:00Z",2],"lake_facts":{"rows":3,"sha256":"old"},"delivery_sha256":"before"}' | ConvertFrom-Json
$after=$baseline | ConvertTo-Json -Depth 20 | ConvertFrom-Json
$after.lake_facts.rows=4; $after.lake_facts.sha256='new'; $after.delivery_sha256='after'
$plan='{"schema_version":1,"baseline_snapshot":7,"original_facts":{"rows":3,"sha256":"old"},"pending":[{"batch_id":"pending","fact_count":1}]}' | ConvertFrom-Json
$proof='{"schema_version":1,"status":"passed","baseline_snapshot":7,"unexpected_rows_or_duplicates":0,"original_receipts_unchanged":true,"original_snapshots_unchanged":true,"historical_fingerprint":{"rows":3,"sha256":"old"},"preserved_original_fingerprint":{"rows":3,"sha256":"old"},"pending_batches_verified":1,"new_receipts":1,"added_rows":1,"current_lake_rows":4}' | ConvertFrom-Json
"""
    def test_post_rebuild_gate_accepts_exact_pending_growth(self):
        result=self.ps(self.reconciliation_setup()+r"""
$oldRejected=$false
try { Assert-SameEvidence $baseline.lake_facts $after.lake_facts 'Legacy exact lake gate' } catch {$oldRejected=$true}
if(-not $oldRejected){throw 'Regression fixture did not reproduce the former false rejection'}
Assert-PostRebuildEvidence $baseline $after $plan $proof
""")
        self.assertEqual(result.returncode,0,result.stderr)

    def test_post_rebuild_gate_rejects_incomplete_proof_or_changed_other_data(self):
        cases=("missing","unverified","metadata","snapshot","historical","preserved","growth","current","pending","raw","operational")
        for case in cases:
            with self.subTest(case=case):
                result=self.ps(self.reconciliation_setup()+r"""
switch ('__CASE__') {
'missing' {$proof=$null}
'unverified' {$proof.status='failed'}
'metadata' {$proof.original_snapshots_unchanged=$false}
'snapshot' {$proof.baseline_snapshot=6}
'historical' {$proof.historical_fingerprint.sha256='changed'}
'preserved' {$proof.preserved_original_fingerprint.sha256='changed'}
'growth' {$proof.added_rows=2}
'current' {$proof.current_lake_rows=5}
'pending' {$proof.pending_batches_verified=0}
'raw' {$after.raw.objects=2}
'operational' {$after.operational.facts.sha256='changed'}
}
Assert-PostRebuildEvidence $baseline $after $plan $proof
""".replace("__CASE__",case))
                self.assertNotEqual(result.returncode,0)

    def test_reconciliation_cannot_target_source_and_mounts_inputs_read_only(self):
        root=str(SCRIPT.parents[1]).replace("'","''")
        result=self.ps(r"""
$script:Workspace='__ROOT__'
$script:BackupPath='C:\fixture backup'
function Invoke-Compose([string]$Project,[string[]]$Arguments) {
    if($Project -cne 'midas-data-restore-test'){throw 'Wrong project'}
    $v=[array]::IndexOf($Arguments,'--volume')
    if($v -lt 0 -or $Arguments[$v+1] -cne 'C:\fixture backup:/restore-input:ro'){throw 'Input mount not read-only'}
    if($Arguments[-1] -cne 'plan' -or '--no-deps' -cnotin $Arguments){throw 'Unsafe invocation'}
    return 'MIDAS_RESTORE_RECONCILIATION={"schema_version":1}'
}
$rejected=$false
try {$null=Get-RebuildReconciliation 'midas-data' -Phase plan} catch {$rejected=$true}
if(-not $rejected){throw 'Source target accepted'}
$null=Get-RebuildReconciliation 'midas-data-restore-test' -Phase plan
""".replace("__ROOT__",root))
        self.assertEqual(result.returncode,0,result.stderr)

    def test_restore_workflow_captures_plan_before_rebuild_and_requires_final_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            target=str(Path(temp)).replace("'","''")
            root=str(SCRIPT.parents[1]).replace("'","''")
            body=self.reconciliation_setup()+r"""
$script:Workspace='__ROOT__'
$script:BackupPath='__TARGET__'
Write-Json (Join-Path $script:BackupPath 'baseline.json') $baseline
$script:steps=[Collections.Generic.List[string]]::new()
$script:probes=0
function Read-BackupManifest { return @{config_sha256=@{};images=@{worker='image';postgres='image';nats='image';clickhouse='image'}} }
function Get-ConfigDigests { return @{} }
function Assert-FreshRestore {}
function Wait-Healthy {}
function Get-ProjectVolumes { return @{} }
function Get-ContainerId { return 'fixture-container' }
function Invoke-Docker { return 'image' }
function Invoke-Archive {}
function Get-Evidence {
    $script:probes++
    if($script:probes -eq 1){$script:steps.Add('before');return $baseline}
    $script:steps.Add('after');return $after
}
function Get-RebuildReconciliation([string]$Project,[string]$Phase) {
    $script:steps.Add($Phase)
    if($Phase -eq 'plan'){return $plan}
    return $proof
}
function Invoke-Compose([string]$Project,[string[]]$Arguments) {
    if($Project -cne 'midas-data-restore'){throw 'Wrong target'}
    if('rebuild-analytics' -cin $Arguments) {
        if(-not (Test-Path (Join-Path $script:BackupPath 'restore-rebuild-plan.json'))){throw 'Plan not saved before rebuild'}
        $script:steps.Add('rebuild')
        return '{"reconciled_batches":2}'
    }
    return @()
}
Invoke-RestoreCheck
if(($script:steps -join ',') -cne 'before,plan,rebuild,after,verify'){throw 'Incorrect verification sequence'}
$report=Get-Content (Get-ChildItem $script:BackupPath -Filter 'restore-check-*.json').FullName -Raw | ConvertFrom-Json
if($report.status -cne 'passed' -or $report.reconciliation.added_rows -ne 1){throw 'Reconciliation not required by full workflow'}
"""
            result=self.ps(body.replace("__ROOT__",root).replace("__TARGET__",target))
            self.assertEqual(result.returncode,0,result.stderr)

if __name__ == "__main__":
    unittest.main()

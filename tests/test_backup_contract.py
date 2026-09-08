"""Local PowerShell contract tests; never contact Docker or production data."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gmgn-data-backup.ps1"
POWERSHELL = shutil.which("pwsh")
ARTIFACTS = ("midas_data.dump", "midas_lake.dump", "raw.tar", "lake.tar", "baseline.json")


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


if __name__ == "__main__":
    unittest.main()

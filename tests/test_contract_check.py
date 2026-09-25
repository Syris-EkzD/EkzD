import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd import contract_check
from ekzd.contract import canonical_bytes, compose_contract
from ekzd.identity import runtime_identity
from ekzd.worker import run_worker_check
from test_contract import project, task


class FrozenCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        for args in (("init", "-qb", "main"), ("config", "user.name", "Test"), ("config", "user.email", "test@example.invalid")):
            self.git(*args)
        (self.root / "file").write_text("good", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.baseline = self.git("rev-parse", "HEAD")
        self.git("switch", "-qc", "feat/task")
        self.path = Path(self.temp.name) / "contract.json"

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.PIPE, text=True).strip()

    def freeze(self):
        task_data = task()
        task_data["include"] = ["*"]
        contract = compose_contract(project(), task_data, self.baseline, runtime_identity())
        self.path.write_bytes(canonical_bytes(contract))
        return contract

    def test_clean_and_dirty_modes_bind_initial_state(self):
        self.freeze()
        self.assertTrue(run_worker_check(self.root, self.path, final=True)["ready"])
        (self.root / "file").write_text("dirty", encoding="utf-8")
        self.assertTrue(run_worker_check(self.root, self.path)["ready"])
        self.assertFalse(run_worker_check(self.root, self.path, final=True)["ready"])

    def test_runtime_mismatch_blocks_structural_check(self):
        contract = self.freeze()
        contract["ekzd"]["build_sha256"] = "0" * 64
        self.path.write_bytes(canonical_bytes(contract))
        self.assertFalse(run_worker_check(self.root, self.path)["ready"])

    def test_remotes_are_not_authority(self):
        self.freeze()
        for url in ("https://example.invalid/a", "git@example.invalid:a.git"):
            self.git("config", "remote.origin.url", url)
            self.assertTrue(run_worker_check(self.root, self.path, final=True)["ready"])

    def test_missing_baseline_is_unavailable(self):
        contract = self.freeze()
        contract["baseline"] = "0" * 40
        self.path.write_bytes(canonical_bytes(contract))
        result = run_worker_check(self.root, self.path, final=True)
        self.assertEqual("UNAVAILABLE", result["overall_status"])

    def test_unavailable_runtime_fails_closed(self):
        from ekzd.identity import BuildIdentityUnavailable

        self.freeze()
        with mock.patch.object(contract_check, "runtime_identity", side_effect=BuildIdentityUnavailable("missing source")):
            result = run_worker_check(self.root, self.path, final=True)
        self.assertEqual("UNAVAILABLE", result["overall_status"])


if __name__ == "__main__":
    unittest.main()

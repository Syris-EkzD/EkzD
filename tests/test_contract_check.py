import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd import contract_check
from ekzd.contract import canonical_bytes, compose_contract
from ekzd.identity import BuildIdentityUnavailable, runtime_identity
from ekzd.worker import run_worker_check
from test_contract import project, task


class FrozenCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        for args in (("init", "-qb", "main"), ("config", "user.name", "Test"),
                     ("config", "user.email", "test@example.invalid")):
            self.git(*args)
        (self.root / "file").write_text("good")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.baseline = self.git("rev-parse", "HEAD")
        self.git("switch", "-qc", "feat/task")
        self.path = Path(self.temp.name) / "contract.json"

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root,
                                       stderr=subprocess.PIPE, text=True).strip()

    def freeze(self):
        c = compose_contract(project(), task(include=["*"]), self.baseline, runtime_identity())
        self.path.write_bytes(canonical_bytes(c))
        return c

    def test_clean_and_dirty_modes_bind_initial_state(self):
        self.freeze()
        self.assertTrue(run_worker_check(self.root, self.path, final=True)["ready"])
        (self.root / "file").write_text("dirty")
        self.assertTrue(run_worker_check(self.root, self.path)["ready"])
        self.assertFalse(run_worker_check(self.root, self.path, final=True)["ready"])

    def test_candidate_change_during_structural_check_is_not_rebound(self):
        self.freeze()
        original = contract_check.capture_candidate
        calls = 0

        def capture(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / "file").write_text("different")
            return original(root)

        with mock.patch.object(contract_check, "capture_candidate", side_effect=capture):
            result = run_worker_check(self.root, self.path, final=True)
        self.assertFalse(result["ready"])
        self.assertTrue(result["git"]["clean"])
        self.assertIn("Candidate changed during structural verification", json.dumps(result))

    def test_runtime_mismatch_stops_structural_verification(self):
        c = self.freeze()
        c["ekzd"]["build_sha256"] = "0" * 64
        self.path.write_bytes(canonical_bytes(c))
        self.assertFalse(run_worker_check(self.root, self.path)["ready"])

    def test_remotes_are_not_authority(self):
        self.freeze()
        for url in ("https://example.invalid/a", "git@example.invalid:a.git"):
            self.git("config", "remote.origin.url", url)
            self.assertTrue(run_worker_check(self.root, self.path, final=True)["ready"])

    def test_missing_baseline_is_unavailable(self):
        c = self.freeze()
        c["baseline"] = "0" * 40
        self.path.write_bytes(canonical_bytes(c))
        result = run_worker_check(self.root, self.path, final=True)
        self.assertEqual("UNAVAILABLE", result["overall_status"])

    def test_unavailable_runtime_is_unavailable(self):
        self.freeze()
        with mock.patch.object(
            contract_check, "runtime_identity",
            side_effect=BuildIdentityUnavailable("missing source"),
        ):
            result = run_worker_check(self.root, self.path, final=True)
        self.assertEqual("UNAVAILABLE", result["overall_status"])


if __name__ == "__main__":
    unittest.main()

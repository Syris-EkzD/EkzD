from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd import contract_check
from ekzd.contract import canonical_bytes, compose_contract
from ekzd.core import load_config
from ekzd.identity import runtime_identity
from ekzd.worker import run_worker_check


class WorkerCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "EkzD Test")
        self._git("config", "user.email", "ekzd@example.invalid")
        (self.root / ".ekzd").mkdir()
        (self.root / ".gitignore").write_text(".ekzd/local/\n__pycache__/\n", encoding="utf-8")
        (self.root / "allowed.txt").write_text("baseline\n", encoding="utf-8")
        (self.root / ".ekzd/project.toml").write_text(
            'schema_version = 4\nname = "Demo"\nprotected = []\nexclude = []\nguidance = []\n',
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-q", "-m", "baseline")
        self.baseline = self._git("rev-parse", "HEAD")
        self._git("checkout", "-q", "-b", "feat/task")
        self.contract_path = self.base / "contract.json"
        self._write_contract()

    def _git(self, *args: str) -> str:
        process = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True)
        return process.stdout.strip()

    def _write_contract(self, *, baseline=None, branch="feat/task", include=None, exclude=None) -> None:
        task = {
            "schema_version": 3,
            "objective": "Worker task",
            "branch": branch,
            "include": include or ["allowed.txt"],
            "exclude": exclude or [],
            "acceptance": ["ready"],
        }
        contract = compose_contract(load_config(self.root), task, baseline or self.baseline, runtime_identity())
        self.contract_path.write_bytes(canonical_bytes(contract))

    def _checks(self, result: dict) -> dict[str, dict]:
        return {item["name"]: item for item in result["checks"]}

    def test_development_check_supports_dirty_in_scope_work_without_session(self):
        (self.root / "allowed.txt").write_text("dirty", encoding="utf-8")
        local = self.root / ".ekzd/local"
        local.mkdir()
        state = local / "session.json"
        state.write_text("not valid json", encoding="utf-8")
        first = run_worker_check(self.root, self.contract_path)
        state.write_text("different local bytes", encoding="utf-8")
        second = run_worker_check(self.root, self.contract_path)
        self.assertTrue(first["ready"])
        self.assertTrue(second["ready"])
        self.assertEqual(first["git"], second["git"])
        self.assertFalse(first["git"]["clean"])
        self.assertEqual(hashlib.sha256(self.contract_path.read_bytes()).hexdigest(), first["contract_id"])

    def test_final_check_requires_clean_committed_candidate(self):
        (self.root / "allowed.txt").write_text("dirty\n", encoding="utf-8")
        result = run_worker_check(self.root, self.contract_path, final=True)
        self.assertFalse(result["ready"])
        self.assertIn("clean, fully committed", self._checks(result)["candidate-state"]["message"])
        self._git("add", "allowed.txt")
        self._git("commit", "-q", "-m", "feat: implement task")
        result = run_worker_check(self.root, self.contract_path, final=True)
        self.assertTrue(result["ready"])
        self.assertTrue(result["git"]["clean"])
        self.assertEqual(self._git("rev-parse", "HEAD"), result["git"]["head"])

    def test_branch_and_baseline_ancestry_are_enforced(self):
        self._write_contract(branch="feat/other")
        self.assertFalse(run_worker_check(self.root, self.contract_path)["ready"])

        self._git("checkout", "-q", "--orphan", "unrelated")
        self._git("rm", "-q", "-rf", ".")
        (self.root / "other.txt").write_text("other\n", encoding="utf-8")
        self._git("add", "other.txt")
        self._git("commit", "-q", "-m", "unrelated")
        unrelated = self._git("rev-parse", "HEAD")
        self._git("checkout", "-q", "feat/task")
        self._write_contract(baseline=unrelated)
        result = run_worker_check(self.root, self.contract_path)
        self.assertEqual("FAIL", self._checks(result)["contract-authority"]["status"])

    def test_fixed_commit_ceiling_is_enforced(self):
        for index in range(51):
            (self.root / "allowed.txt").write_text(f"commit-{index}\n", encoding="utf-8")
            self._git("add", "allowed.txt")
            self._git("commit", "-q", "-m", f"change {index}")
        result = run_worker_check(self.root, self.contract_path)
        check = self._checks(result)["contract-authority"]
        self.assertEqual("FAIL", check["status"])
        self.assertIn("Commit safety ceiling exceeded: 51 > 50", check["message"])

    def test_scope_covers_history_staged_unstaged_and_untracked_paths(self):
        (self.root / "committed.txt").write_text("c\n", encoding="utf-8")
        self._git("add", "committed.txt")
        self._git("commit", "-q", "-m", "outside committed")
        (self.root / "staged.txt").write_text("s\n", encoding="utf-8")
        self._git("add", "staged.txt")
        (self.root / "allowed.txt").write_text("unstaged\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("u\n", encoding="utf-8")
        result = run_worker_check(self.root, self.contract_path)
        check = self._checks(result)["contract-authority"]
        self.assertEqual("FAIL", check["status"])
        for path in ("committed.txt", "staged.txt", "untracked.txt"):
            self.assertIn(path, check["message"])

    def test_protected_project_policy_change_is_rejected(self):
        policy = self.root / ".ekzd/project.toml"
        policy.write_text(policy.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
        result = run_worker_check(self.root, self.contract_path)
        self.assertFalse(result["ready"])
        self.assertIn("scope.protected", json.dumps(result))

    def test_candidate_change_during_structural_check_is_not_rebound(self):
        identity = runtime_identity()
        calls = 0

        def mutate_on_recheck():
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / "allowed.txt").write_text("changed after scope\n", encoding="utf-8")
            return identity

        with mock.patch.object(contract_check, "runtime_identity", side_effect=mutate_on_recheck):
            result = run_worker_check(self.root, self.contract_path)

        self.assertFalse(result["ready"])
        self.assertTrue(result["git"]["clean"])
        self.assertEqual("FAIL", self._checks(result)["state-binding"]["status"])
        self.assertIn("Candidate changed during structural checking", json.dumps(result))

    def test_contract_change_during_structural_check_fails_binding(self):
        identity = runtime_identity()
        calls = 0

        def mutate_contract_on_recheck():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.contract_path.write_text("changed", encoding="utf-8")
            return identity

        with mock.patch.object(contract_check, "runtime_identity", side_effect=mutate_contract_on_recheck):
            result = run_worker_check(self.root, self.contract_path)

        self.assertFalse(result["ready"])
        self.assertIn("Frozen contract changed", json.dumps(result))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
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
from phase3_helpers import project_text


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
        (self.root / ".ekzd/project.toml").write_text(project_text())
        self._git("add", ".")
        self._git("commit", "-q", "-m", "baseline")
        self.baseline = self._git("rev-parse", "HEAD")
        self._git("checkout", "-q", "-b", "feat/task")
        self.task_path = self.base / "task.json"
        self._write_task()

    def _git(self, *args: str) -> str:
        process = subprocess.run(["git", *args], cwd=self.root, text=True,
                                 capture_output=True, check=True)
        return process.stdout.strip()

    def _write_task(self, *, baseline=None, branch="feat/task", include=None, exclude=None):
        task = dict(schema_version=3, objective="Worker task", branch=branch,
                    include=include or ["allowed.txt"], exclude=exclude or [],
                    acceptance=["ready"])
        contract = compose_contract(
            load_config(self.root), task, baseline or self.baseline, runtime_identity()
        )
        self.task_path.write_bytes(canonical_bytes(contract))

    def _checks(self, result: dict) -> dict[str, dict]:
        return {item["name"]: item for item in result["checks"]}

    def test_development_check_supports_dirty_worktree_without_session(self):
        (self.root / "allowed.txt").write_text("dirty")
        local = self.root / ".ekzd/local"
        local.mkdir()
        state = local / "session.json"
        state.write_text("not valid json")
        first = run_worker_check(self.root, self.task_path)
        state.write_text("different local bytes")
        second = run_worker_check(self.root, self.task_path)
        self.assertTrue(first["ready"])
        self.assertTrue(second["ready"])
        self.assertEqual(first["git"], second["git"])
        self.assertFalse(first["git"]["clean"])
        self.assertEqual(hashlib.sha256(self.task_path.read_bytes()).hexdigest(),
                         first["contract_id"])

    def test_final_check_rejects_dirty_worktree(self):
        (self.root / "allowed.txt").write_text("dirty\n", encoding="utf-8")
        result = run_worker_check(self.root, self.task_path, final=True)
        self.assertFalse(result["ready"])
        self.assertEqual("FAIL", self._checks(result)["contract-authority"]["status"])
        self.assertIn("clean", self._checks(result)["contract-authority"]["message"])

    def test_final_check_passes_for_clean_exact_head(self):
        result = run_worker_check(self.root, self.task_path, final=True)
        self.assertTrue(result["ready"])
        self.assertTrue(result["git"]["clean"])
        self.assertEqual(self._git("rev-parse", "HEAD"), result["git"]["head"])

    def test_branch_mismatch_is_blocking(self):
        self._write_task(branch="feat/other")
        result = run_worker_check(self.root, self.task_path)
        self.assertFalse(result["ready"])
        self.assertEqual("FAIL", self._checks(result)["contract-authority"]["status"])

    def test_non_ancestor_baseline_blocks_checks(self):
        self._git("checkout", "-q", "--orphan", "unrelated")
        self._git("rm", "-q", "-rf", ".")
        (self.root / "other.txt").write_text("other\n", encoding="utf-8")
        self._git("add", "other.txt")
        self._git("commit", "-q", "-m", "unrelated")
        unrelated = self._git("rev-parse", "HEAD")
        self._git("checkout", "-q", "feat/task")
        self._write_task(baseline=unrelated)
        result = run_worker_check(self.root, self.task_path)
        self.assertEqual("FAIL", self._checks(result)["contract-authority"]["status"])

    def test_commit_ceiling_uses_baseline_relative_commit_count(self):
        for index in range(51):
            (self.root / "allowed.txt").write_text(f"commit-{index}\n", encoding="utf-8")
            self._git("add", "allowed.txt")
            self._git("commit", "-q", "-m", f"change {index}")
        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["contract-authority"]
        self.assertEqual("FAIL", check["status"])
        self.assertIn("Commit safety ceiling exceeded: 51 > 50", check["message"])

    def test_scope_covers_committed_staged_unstaged_and_untracked_changes(self):
        (self.root / "committed.txt").write_text("c\n", encoding="utf-8")
        self._git("add", "committed.txt")
        self._git("commit", "-q", "-m", "outside committed")
        (self.root / "staged.txt").write_text("s\n", encoding="utf-8")
        self._git("add", "staged.txt")
        (self.root / "allowed.txt").write_text("unstaged\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("u\n", encoding="utf-8")
        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["contract-authority"]
        self.assertEqual("FAIL", check["status"])
        for path in ("committed.txt", "staged.txt", "untracked.txt"):
            self.assertIn(path, check["message"])

    def test_candidate_change_after_scope_checks_is_detected(self):
        original = contract_check.capture_candidate
        calls = 0

        def capture(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / "allowed.txt").write_text("changed after scope\n")
            return original(root)

        with mock.patch.object(contract_check, "capture_candidate", side_effect=capture):
            result = run_worker_check(self.root, self.task_path, final=True)
        checks = self._checks(result)
        self.assertFalse(result["ready"])
        self.assertEqual("FAIL", checks["state-binding"]["status"])
        self.assertTrue(result["git"]["clean"])


if __name__ == "__main__":
    unittest.main()

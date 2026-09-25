from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd.worker import run_worker_check
from ekzd import contract_check
from ekzd.contract import compose_contract, canonical_bytes
from ekzd.identity import runtime_identity
from ekzd.core import load_config
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
        self._write_project_steps([
            ("base", ["python3", "-c", "print('project-ok')"]),
        ])
        self._git("add", ".")
        self._git("commit", "-q", "-m", "baseline")
        self.baseline = self._git("rev-parse", "HEAD")
        self._git("checkout", "-q", "-b", "feat/task")
        self.task_path = self.base / "task.toml"
        self._write_task()

    def _git(self, *args: str) -> str:
        process = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True)
        return process.stdout.strip()

    def _toml_array(self, values: list[str]) -> str:
        return "[" + ", ".join(json.dumps(value) for value in values) + "]"

    def _write_project_steps(self, steps, *, step_cwds=None):
        lines = ['schema_version = 3', 'name = "Demo"']
        for name, command in steps:
            lines += ['[[verification]]', f'name = {json.dumps(name)}',
                      f'command = {json.dumps(command)}', f'cwd = {json.dumps((step_cwds or {}).get(name, "."))}',
                      'timeout_seconds = 30']
        (self.root / '.ekzd/project.toml').write_text('\n'.join(lines) + '\n')

    def _write_task(self, *, baseline=None, branch='feat/task', include=None, exclude=None, task_steps=None, task_step_cwds=None):
        task = dict(schema_version=2, objective='Worker task', branch=branch,
                    include=include or ['allowed.txt'], exclude=exclude or [], acceptance=['ready'],
                    verification=[dict(name=name, command=command, cwd=(task_step_cwds or {}).get(name, '.'), timeout_seconds=30)
                                  for name, command in task_steps or []])
        contract = compose_contract(load_config(self.root), task, baseline or self.baseline, runtime_identity())
        self.task_path.write_bytes(canonical_bytes(contract))

    def _checks(self, result: dict) -> dict[str, dict]:
        return {item["name"]: item for item in result["checks"]}

    def test_development_check_supports_dirty_worktree_without_session(self):
        (self.root / 'allowed.txt').write_text('dirty')
        local = self.root / '.ekzd/local'
        local.mkdir()
        state = local / 'session.json'
        state.write_text('not valid json')
        first = run_worker_check(self.root, self.task_path)
        state.write_text('different local bytes')
        second = run_worker_check(self.root, self.task_path)
        self.assertTrue(first['ready'])
        self.assertTrue(second['ready'])
        self.assertEqual(first['git'], second['git'])
        self.assertFalse(first['git']['clean'])
        self.assertEqual(hashlib.sha256(self.task_path.read_bytes()).hexdigest(), first['contract_id'])

    def test_final_check_rejects_dirty_worktree(self) -> None:
        (self.root / "allowed.txt").write_text("dirty\n", encoding="utf-8")
        result = run_worker_check(self.root, self.task_path, final=True)
        self.assertFalse(result["ready"])
        self.assertEqual("FAIL", self._checks(result)["candidate-state"]["status"])

    def test_final_check_passes_for_clean_exact_head(self) -> None:
        result = run_worker_check(self.root, self.task_path, final=True)
        self.assertTrue(result["ready"])
        self.assertTrue(result["git"]["clean"])
        self.assertEqual(self._git("rev-parse", "HEAD"), result["git"]["head"])
        self.assertEqual(64, len(result["git"]["worktree_fingerprint"]))

    def test_branch_mismatch_is_blocking(self) -> None:
        self._write_task(branch="feat/other")
        result = run_worker_check(self.root, self.task_path)
        self.assertFalse(result["ready"])
        self.assertEqual("FAIL", self._checks(result)["contract-authority"]["status"])

    def test_non_ancestor_baseline_blocks_baseline_relative_checks(self) -> None:
        self._git("checkout", "-q", "--orphan", "unrelated")
        self._git("rm", "-q", "-rf", ".")
        (self.root / "other.txt").write_text("other\n", encoding="utf-8")
        self._git("add", "other.txt")
        self._git("commit", "-q", "-m", "unrelated")
        unrelated = self._git("rev-parse", "HEAD")
        self._git("checkout", "-q", "feat/task")
        self._write_task(baseline=unrelated)
        result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)
        self.assertEqual("FAIL", checks["contract-authority"]["status"])

    def test_commit_ceiling_uses_baseline_relative_commit_count(self) -> None:
        for index in range(51):
            (self.root / "allowed.txt").write_text(f"commit-{index}\n", encoding="utf-8")
            self._git("add", "allowed.txt")
            self._git("commit", "-q", "-m", f"change {index}")
        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["contract-authority"]
        self.assertEqual("FAIL", check["status"])
        self.assertIn("Commit safety ceiling exceeded: 51 > 50", check["message"])

    def test_scope_covers_committed_staged_unstaged_and_untracked_changes(self) -> None:
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

    def test_frozen_contract_is_used_and_worktree_config_change_is_blocking(self):
        self._write_project_steps([('tampered', ['python3', '-c', 'raise SystemExit(9)'])])
        result = run_worker_check(self.root, self.task_path)
        self.assertFalse(result['ready'])
        self.assertIn('scope.protected', json.dumps(result))
        self.assertNotIn('verification:tampered', self._checks(result))

    def test_missing_required_tool_is_unavailable_and_later_check_runs(self) -> None:
        self._write_task(task_steps=[
            ("missing", ["ekzd-definitely-missing-executable"]),
            ("later", ["python3", "-c", "print('still-ran')"]),
        ])
        result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)
        self.assertEqual("UNAVAILABLE", checks["verification:missing"]["status"])
        self.assertEqual("PASS", checks["verification:later"]["status"])
        self.assertFalse(result["ready"])

    def test_ordinary_verification_failure_does_not_stop_independent_steps(self) -> None:
        self._write_task(task_steps=[
            ("fails", ["python3", "-c", "raise SystemExit(7)"]),
            ("later", ["python3", "-c", "print('still-ran')"]),
        ])
        result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)
        self.assertEqual("FAIL", checks["verification:fails"]["status"])
        self.assertEqual("PASS", checks["verification:later"]["status"])

    def test_verification_cwd_inside_repository_still_runs(self) -> None:
        work = self.root / "work"
        work.mkdir()
        self._write_task(
            include=["allowed.txt", "work"],
            task_steps=[("inside", ["python3", "-c", "from pathlib import Path; print(Path.cwd().name)"])],
            task_step_cwds={"inside": "work"},
        )

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["verification:inside"]

        self.assertEqual("PASS", check["status"])
        self.assertEqual("work\n", check["details"]["stdout"])

    def test_task_verification_cwd_symlink_escape_is_blocked_without_execution(self) -> None:
        external = self.base / "external-task"
        external.mkdir()
        (self.root / "escaped-task").symlink_to(external, target_is_directory=True)
        self._write_task(
            include=["allowed.txt", "escaped-task"],
            task_steps=[("escaped", ["python3", "-c", "open('executed.txt','w').write('ran')"])],
            task_step_cwds={"escaped": "escaped-task"},
        )

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["verification:escaped"]

        self.assertEqual("UNAVAILABLE", check["status"])
        self.assertFalse((external / "executed.txt").exists())
        self.assertNotIn(str(external), json.dumps(result))

    def test_project_verification_cwd_symlink_escape_is_blocked_without_execution(self) -> None:
        external = self.base / "external-project"
        external.mkdir()
        (self.root / "escaped-project").symlink_to(external, target_is_directory=True)
        self._write_project_steps(
            [("escaped", ["python3", "-c", "open('executed.txt','w').write('ran')"])],
            step_cwds={"escaped": "escaped-project"},
        )
        self._git("add", ".")
        self._git("commit", "-q", "-m", "project cwd fixture")
        self.baseline = self._git("rev-parse", "HEAD")
        self._write_task(baseline=self.baseline)

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["verification:escaped"]

        self.assertEqual("UNAVAILABLE", check["status"])
        self.assertFalse((external / "executed.txt").exists())
        self.assertNotIn(str(external), json.dumps(result))

    def test_verifier_mutation_is_detected_not_restored_and_stops_dependent_steps(self) -> None:
        self._write_task(task_steps=[
            ("mutate", ["python3", "-c", "open('allowed.txt','w').write('mutated\\n')"]),
            ("later", ["python3", "-c", "print('unsafe')"]),
        ])
        result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)
        self.assertEqual("FAIL", checks["verification:mutate"]["status"])
        self.assertIn("candidate_error", checks["verification:mutate"]["details"])
        self.assertNotIn("verification:later", checks)
        self.assertEqual("mutated\n", (self.root / "allowed.txt").read_text(encoding="utf-8"))

    def test_candidate_change_after_scope_checks_is_not_rebound_by_evaluator(self) -> None:
        original = contract_check.evaluate_candidate

        def mutate_before_evaluator(*args, **kwargs):
            (self.root / "allowed.txt").write_text("changed after scope\n", encoding="utf-8")
            return original(*args, **kwargs)

        with mock.patch.object(contract_check, "evaluate_candidate", side_effect=mutate_before_evaluator):
            result = run_worker_check(self.root, self.task_path)

        checks = self._checks(result)
        self.assertFalse(result["ready"])
        self.assertEqual("FAIL", checks["state-binding"]["status"])
        self.assertIn("Candidate changed between evaluation boundaries", json.dumps(result))
        self.assertTrue(result["git"]["clean"])









if __name__ == "__main__":
    unittest.main()

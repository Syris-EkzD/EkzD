from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

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
        (self.root / ".gitignore").write_text(".ekzd/session.json\n__pycache__/\n", encoding="utf-8")
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

    def _write_project_steps(
        self,
        steps: list[tuple[str, list[str]]],
        *,
        step_cwds: dict[str, str] | None = None,
    ) -> None:
        lines = [
            "schema_version = 1",
            "",
            "[project]",
            'name = "Demo"',
            "",
            "[sources]",
            'paths = ["allowed.txt"]',
            "",
            "[scope]",
            'include = ["*"]',
            "exclude = []",
            "constraints = []",
            "",
            "[authority]",
            "may = []",
            "requires_approval = []",
            "may_not = []",
            "",
            "[session]",
            "max_commits = 20",
            "",
            "[acceptance]",
            'criteria = ["verification"]',
            "",
            "[verification]",
        ]
        for name, command in steps:
            lines += [
                "",
                "[[verification.steps]]",
                f"name = {json.dumps(name)}",
                f"command = {self._toml_array(command)}",
                f"cwd = {json.dumps((step_cwds or {}).get(name, '.'))}",
                "timeout_seconds = 30",
            ]
        (self.root / ".ekzd/project.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_task(
        self,
        *,
        baseline: str | None = None,
        branch: str = "feat/task",
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        max_commits: int = 20,
        repository: str | None = None,
        task_steps: list[tuple[str, list[str]]] | None = None,
        task_step_cwds: dict[str, str] | None = None,
    ) -> None:
        lines = [
            "schema_version = 1",
            'objective = "Worker task"',
            f"baseline = {json.dumps(baseline or self.baseline)}",
            f"implementation_branch = {json.dumps(branch)}",
            f"max_commits = {max_commits}",
        ]
        if repository is not None:
            lines.append(f"repository = {json.dumps(repository)}")
        lines += [
            "",
            "[scope]",
            f"include = {self._toml_array(include or ['allowed.txt'])}",
            f"exclude = {self._toml_array(exclude or [])}",
            "",
            "[acceptance]",
            'criteria = ["ready"]',
        ]
        for name, command in task_steps or []:
            lines += [
                "",
                "[[verification.steps]]",
                f"name = {json.dumps(name)}",
                f"command = {self._toml_array(command)}",
                f"cwd = {json.dumps((task_step_cwds or {}).get(name, '.'))}",
                "timeout_seconds = 30",
            ]
        self.task_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _checks(self, result: dict) -> dict[str, dict]:
        return {item["name"]: item for item in result["checks"]}

    def test_development_check_supports_dirty_worktree_without_session(self) -> None:
        (self.root / "allowed.txt").write_text("dirty\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        session = self.root / ".ekzd/session.json"
        session.write_text("not even valid json\n", encoding="utf-8")
        self._write_task(include=["allowed.txt", ".gitignore"])

        result = run_worker_check(self.root, self.task_path)
        first_fingerprint = result["git"]["worktree_fingerprint"]
        session.write_text("different local session bytes\n", encoding="utf-8")
        repeated = run_worker_check(self.root, self.task_path)

        self.assertTrue(result["ready"])
        self.assertTrue(repeated["ready"])
        self.assertEqual("PASS", result["overall_status"])
        self.assertFalse(result["git"]["clean"])
        self.assertEqual(first_fingerprint, repeated["git"]["worktree_fingerprint"])
        self.assertEqual("WARN", self._checks(result)["worktree-mode"]["status"])
        self.assertEqual(hashlib.sha256(self.task_path.read_bytes()).hexdigest(), result["task"]["sha256"])

    def test_final_check_rejects_dirty_worktree(self) -> None:
        (self.root / "allowed.txt").write_text("dirty\n", encoding="utf-8")
        result = run_worker_check(self.root, self.task_path, final=True)
        self.assertFalse(result["ready"])
        self.assertEqual("FAIL", self._checks(result)["worktree-mode"]["status"])

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
        self.assertEqual("FAIL", self._checks(result)["implementation-branch"]["status"])

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
        self.assertEqual("FAIL", checks["baseline-ancestry"]["status"])
        self.assertEqual("UNAVAILABLE", checks["task-scope"]["status"])

    def test_commit_ceiling_uses_baseline_relative_commit_count(self) -> None:
        for index in range(2):
            (self.root / "allowed.txt").write_text(f"commit-{index}\n", encoding="utf-8")
            self._git("add", "allowed.txt")
            self._git("commit", "-q", "-m", f"change {index}")
        self._write_task(max_commits=1)
        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["commit-ceiling"]
        self.assertEqual("FAIL", check["status"])
        self.assertEqual(2, check["details"]["commit_count"])

    def test_scope_covers_committed_staged_unstaged_and_untracked_changes(self) -> None:
        (self.root / "committed.txt").write_text("c\n", encoding="utf-8")
        self._git("add", "committed.txt")
        self._git("commit", "-q", "-m", "outside committed")
        (self.root / "staged.txt").write_text("s\n", encoding="utf-8")
        self._git("add", "staged.txt")
        (self.root / "allowed.txt").write_text("unstaged\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("u\n", encoding="utf-8")
        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["task-scope"]
        self.assertEqual("FAIL", check["status"])
        paths = set(check["details"]["changed_paths"])
        self.assertTrue({"committed.txt", "staged.txt", "allowed.txt", "untracked.txt"} <= paths)

    def test_baseline_project_config_is_used_and_worktree_config_change_is_blocking(self) -> None:
        self._write_project_steps([("tampered", ["python3", "-c", "raise SystemExit(9)"])])
        result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)
        self.assertEqual("PASS", checks["project:base"]["status"])
        self.assertNotIn("project:tampered", checks)
        self.assertEqual("FAIL", checks["protected-harness"]["status"])

    def test_missing_required_tool_is_unavailable_and_later_check_runs(self) -> None:
        self._write_task(task_steps=[
            ("missing", ["ekzd-definitely-missing-executable"]),
            ("later", ["python3", "-c", "print('still-ran')"]),
        ])
        result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)
        self.assertEqual("UNAVAILABLE", checks["task:missing"]["status"])
        self.assertEqual("PASS", checks["task:later"]["status"])
        self.assertFalse(result["ready"])

    def test_ordinary_verification_failure_does_not_stop_independent_steps(self) -> None:
        self._write_task(task_steps=[
            ("fails", ["python3", "-c", "raise SystemExit(7)"]),
            ("later", ["python3", "-c", "print('still-ran')"]),
        ])
        result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)
        self.assertEqual("FAIL", checks["task:fails"]["status"])
        self.assertEqual("PASS", checks["task:later"]["status"])

    def test_verification_cwd_inside_repository_still_runs(self) -> None:
        work = self.root / "work"
        work.mkdir()
        self._write_task(
            include=["allowed.txt", "work"],
            task_steps=[("inside", ["python3", "-c", "from pathlib import Path; print(Path.cwd().name)"])],
            task_step_cwds={"inside": "work"},
        )

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["task:inside"]

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
        check = self._checks(result)["task:escaped"]

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
        check = self._checks(result)["project:escaped"]

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
        self.assertEqual("FAIL", checks["task:mutate"]["status"])
        self.assertIn("candidate_error", checks["task:mutate"]["details"])
        self.assertNotIn("task:later", checks)
        self.assertEqual("mutated\n", (self.root / "allowed.txt").read_text(encoding="utf-8"))

    def test_repository_identity_mismatch_is_safe_and_blocks(self) -> None:
        self._git("remote", "add", "origin", "https://user:supersecret@github.com/example/demo.git?token=hunter2#frag")
        self._write_task(repository="https://github.com/example/other")
        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["repository-identity"]
        self.assertEqual("FAIL", check["status"])
        serialized = json.dumps(result)
        self.assertNotIn("supersecret", serialized)
        self.assertNotIn("hunter2", serialized)

    def test_repository_identity_valid_credentialed_url_still_matches(self) -> None:
        self._git("remote", "add", "origin", "https://user:supersecret@github.com/example/demo.git?token=hunter2#frag")
        self._write_task(repository="https://github.com/example/demo")

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["repository-identity"]

        self.assertEqual("PASS", check["status"])
        serialized = json.dumps(result)
        self.assertNotIn("supersecret", serialized)
        self.assertNotIn("hunter2", serialized)

    def test_repository_identity_hostless_file_url_matches(self) -> None:
        self._git("remote", "add", "origin", "file:///tmp/demo.git")
        self._write_task(repository="file:///tmp/demo.git")

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["repository-identity"]

        self.assertEqual("PASS", check["status"])
        self.assertEqual("file:///tmp/demo", check["details"]["repository"])

    def test_repository_identity_hostless_file_url_strips_query_and_fragment(self) -> None:
        self._git("remote", "add", "origin", "file:///tmp/demo.git?token=originsecret#originfrag")
        self._write_task(repository="file:///tmp/demo.git?token=tasksecret#taskfrag")

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["repository-identity"]

        self.assertEqual("PASS", check["status"])
        self.assertEqual("file:///tmp/demo", check["details"]["repository"])
        serialized = json.dumps(result)
        for secret in ("originsecret", "originfrag", "tasksecret", "taskfrag"):
            self.assertNotIn(secret, serialized)

    def test_hostless_relaxation_does_not_allow_malformed_credentials(self) -> None:
        self._git("remote", "add", "origin", "https://github.com/example/demo.git")
        self._write_task(
            repository="https:///secretuser:secretpass@github.com/example/demo.git?token=declaredtoken"
        )

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["repository-identity"]

        self.assertEqual("UNAVAILABLE", check["status"])
        serialized = json.dumps(result)
        for secret in ("secretuser", "secretpass", "declaredtoken"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("expected", check.get("details", {}))
        self.assertNotIn("actual", check.get("details", {}))

    def test_unsafe_declared_repository_identity_is_unavailable_without_secret_leak(self) -> None:
        self._git("remote", "add", "origin", "https://github.com/example/demo.git")
        self._write_task(
            repository="https:////secretuser:secretpass@github.com/example/demo.git?token=declaredtoken"
        )

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["repository-identity"]

        self.assertEqual("UNAVAILABLE", check["status"])
        serialized = json.dumps(result)
        for secret in ("secretuser", "secretpass", "declaredtoken"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("expected", check.get("details", {}))
        self.assertNotIn("actual", check.get("details", {}))

    def test_unsafe_origin_repository_identity_is_unavailable_without_secret_leak(self) -> None:
        self._git(
            "remote",
            "add",
            "origin",
            "https:////originuser:originpass@github.com/example/demo.git?token=origintoken",
        )
        self._write_task(repository="https://github.com/example/demo")

        result = run_worker_check(self.root, self.task_path)
        check = self._checks(result)["repository-identity"]

        self.assertEqual("UNAVAILABLE", check["status"])
        serialized = json.dumps(result)
        for secret in ("originuser", "originpass", "origintoken"):
            self.assertNotIn(secret, serialized)
        self.assertNotIn("expected", check.get("details", {}))
        self.assertNotIn("actual", check.get("details", {}))


if __name__ == "__main__":
    unittest.main()

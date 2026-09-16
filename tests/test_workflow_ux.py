from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError, abort_session, finish_session, start_session, verify_session
from ekzd.workflow import (
    build_implementation_prompt,
    build_workflow_status,
    start_reproducible_session,
)


CONFIG = """schema_version = 1

[project]
name = "demo"

[sources]
paths = ["README.md"]

[scope]
include = ["src/"]
exclude = ["src/generated/"]
constraints = ["Avoid unrelated changes."]

[authority]
may = ["Edit scoped source files."]
requires_approval = ["Merge to main."]
may_not = ["Change secrets."]

[session]
max_commits = 2

[acceptance]
criteria = ["Configured verification passes."]

[[verification.steps]]
name = "syntax"
command = ["python3", "-c", "print('ok')"]
cwd = "."
timeout_seconds = 30
"""


class WorkflowUxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:example/demo.git"],
            cwd=self.root,
            check=True,
        )
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        config.write_text(CONFIG, encoding="utf-8")
        (self.root / ".gitignore").write_text(".ekzd/session.json\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.root, check=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def _start(self, objective: str = "Change app", branch: str = "feat/demo-task") -> dict[str, object]:
        return start_reproducible_session(self.root, objective, implementation_branch=branch)

    def _finish_candidate(self) -> None:
        state = json.loads((self.root / ".ekzd/session.json").read_text())
        self._git("checkout", "-b", state["workflow"]["implementation_branch"])
        if self._git("status", "--porcelain"):
            self._git("add", ".")
            self._git("commit", "-m", "finished candidate")

    def _commit_app(self, value: int, message: str) -> None:
        (self.root / "src/app.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
        self._git("add", "src/app.py")
        self._git("commit", "-m", message)

    def _replace_config(self, old: str, new: str) -> None:
        path = self.root / ".ekzd/project.toml"
        path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        self._git("add", ".ekzd/project.toml")
        self._git("commit", "-m", "test config")

    def test_start_rejects_dirty_nonreproducible_baseline(self) -> None:
        (self.root / "notes.txt").write_text("local only\n", encoding="utf-8")

        with self.assertRaisesRegex(HarnessError, "clean working tree is required"):
            self._start()

    def test_start_from_main_requires_separate_declared_implementation_branch(self) -> None:
        state = self._start(branch="feat/main-safe-task")

        self.assertEqual("main", state["start_git"]["branch"])
        self.assertEqual("feat/main-safe-task", state["workflow"]["implementation_branch"])

    def test_start_rejects_baseline_branch_as_implementation_branch(self) -> None:
        with self.assertRaisesRegex(HarnessError, "separate from the baseline branch"):
            self._start(branch="main")

    def test_detached_baseline_keeps_real_implementation_branch(self) -> None:
        self._git("checkout", "--detach")
        self._start(branch="feat/detached-task")

        prompt = build_implementation_prompt(self.root)

        self.assertIn("Baseline branch: (detached)", prompt)
        self.assertIn("Implementation branch: feat/detached-task", prompt)
        self.assertNotIn("Implementation branch: (detached)", prompt)

    def test_prompt_carries_frozen_contract_without_assuming_local_checkout(self) -> None:
        baseline = self._git("rev-parse", "HEAD")
        self._start(branch="feat/contract-task")

        prompt = build_implementation_prompt(self.root)

        self.assertIn("Repository: github.com/example/demo", prompt)
        self.assertIn("Baseline branch: main", prompt)
        self.assertIn("Implementation branch: feat/contract-task", prompt)
        self.assertIn(f"Task baseline HEAD: {baseline}", prompt)
        self.assertIn("Baseline working tree: clean", prompt)
        self.assertIn("Maximum commits: 2", prompt)
        self.assertIn("README.md", prompt)
        self.assertIn("src/generated/", prompt)
        self.assertIn("If you have a local Git checkout", prompt)
        self.assertIn("do not pretend local Git state was checked", prompt)
        self.assertIn("Authoritative EkzD verification happens in the maintainer environment", prompt)
        self.assertIn("hard safety ceiling, not a target", prompt)
        self.assertIn("Prefer small, logically isolated commits", prompt)
        self.assertIn("Do not combine unrelated changes merely to reduce the number of commits", prompt)
        self.assertIn("stop and reassess or report that the task may be exceeding its intended scope", prompt)

    def test_prompt_sanitizes_https_credentials_at_session_start(self) -> None:
        self._git(
            "remote",
            "set-url",
            "origin",
            "https://user:super-secret-token@github.com/example/demo.git?token=also-secret#fragment",
        )
        self._start()

        prompt = build_implementation_prompt(self.root)

        self.assertIn("Repository: https://github.com/example/demo", prompt)
        self.assertNotIn("super-secret-token", prompt)
        self.assertNotIn("also-secret", prompt)
        self.assertNotIn("fragment", prompt)

    def test_prompt_sanitizes_scp_style_origin(self) -> None:
        self._start()

        prompt = build_implementation_prompt(self.root)

        self.assertIn("Repository: github.com/example/demo", prompt)
        self.assertNotIn("git@", prompt)

    def test_prompt_sanitizes_query_and_fragment_from_scp_style_origin(self) -> None:
        self._git(
            "remote",
            "set-url",
            "origin",
            "git@github.com:example/demo.git?token=SUPER_SECRET#fragment",
        )
        state = self._start()

        prompt = build_implementation_prompt(self.root)
        identifier = state["workflow"]["repository"]["identifier"]

        self.assertEqual("github.com/example/demo", identifier)
        self.assertIn("Repository: github.com/example/demo", prompt)
        self.assertNotIn("SUPER_SECRET", identifier)
        self.assertNotIn("token=", identifier)
        self.assertNotIn("fragment", identifier)
        self.assertNotIn("SUPER_SECRET", prompt)
        self.assertNotIn("token=", prompt)
        self.assertNotIn("fragment", prompt)

    def test_prompt_sanitizes_query_and_fragment_from_hostless_url_origin(self) -> None:
        self._git(
            "remote",
            "set-url",
            "origin",
            "file:///tmp/demo.git?token=SUPER_SECRET#fragment",
        )
        state = self._start()

        prompt = build_implementation_prompt(self.root)
        identifier = state["workflow"]["repository"]["identifier"]

        self.assertEqual("file:///tmp/demo", identifier)
        self.assertIn("Repository: file:///tmp/demo", prompt)
        self.assertNotIn("SUPER_SECRET", identifier)
        self.assertNotIn("token=", identifier)
        self.assertNotIn("fragment", identifier)
        self.assertNotIn("SUPER_SECRET", prompt)
        self.assertNotIn("token=", prompt)
        self.assertNotIn("fragment", prompt)

    def test_repository_identity_is_frozen_when_origin_changes_or_disappears(self) -> None:
        self._start()
        first = build_implementation_prompt(self.root)
        self._git("remote", "set-url", "origin", "https://github.com/other/changed.git")
        second = build_implementation_prompt(self.root)
        self._git("remote", "remove", "origin")
        third = build_implementation_prompt(self.root)

        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertIn("Repository: github.com/example/demo", third)

    def test_no_origin_is_frozen_without_inventing_remote(self) -> None:
        self._git("remote", "remove", "origin")
        self._start()
        first = build_implementation_prompt(self.root)
        self._git("remote", "add", "origin", "https://github.com/example/added-later.git")
        second = build_implementation_prompt(self.root)

        self.assertEqual(first, second)
        self.assertIn("no origin remote recorded at session start", first)
        self.assertIn("project: demo", first)
        self.assertNotIn("added-later", second)
        self.assertIn("report that limitation instead of guessing one", first)

    def test_prompt_rejects_legacy_session_started_from_dirty_tree(self) -> None:
        (self.root / "notes.txt").write_text("local only\n", encoding="utf-8")
        start_session(self.root, "Legacy dirty session")

        with self.assertRaisesRegex(HarnessError, "cannot produce a portable implementation prompt"):
            build_implementation_prompt(self.root)

    def test_prompt_preserves_verification_argv_losslessly(self) -> None:
        command = [
            "python3",
            "-c",
            "print('hello world')",
            "argument with spaces",
            'quote"inside',
            "$HOME; echo nope",
            "a|b&&c",
        ]
        replacement = "command = " + json.dumps(command)
        self._replace_config('command = ["python3", "-c", "print(\'ok\')"]', replacement)
        self._start()

        prompt = build_implementation_prompt(self.root)

        self.assertIn(f"argv: {json.dumps(command, ensure_ascii=False)}", prompt)
        self.assertIn('cwd: "."', prompt)
        self.assertIn("argv arrays executed directly without a shell", prompt)

    def test_prompt_defines_pr_delivery_and_no_merge_contract(self) -> None:
        self._start(branch="feat/pr-delivery")

        prompt = build_implementation_prompt(self.root)

        self.assertIn("push/update the declared implementation branch `feat/pr-delivery`", prompt)
        self.assertIn("open or update the relevant pull request", prompt)
        self.assertIn("Do not merge the pull request", prompt)
        self.assertIn("cannot push/update the branch or create/update a pull request", prompt)
        self.assertIn("Self-review the final diff", prompt)
        self.assertIn("report the final branch name, commit SHA(s), PR number/link/reference", prompt)

    def test_status_guides_prompt_then_verify_then_accept(self) -> None:
        self._start()

        initial = build_workflow_status(self.root)
        self.assertIn("ekzd prompt", initial["next"])
        self.assertEqual("not run", initial["verification"])
        self.assertEqual("main", initial["baseline_branch"])
        self.assertEqual("feat/demo-task", initial["implementation_branch"])
        self.assertEqual("main", initial["current_branch"])

        (self.root / "src/app.py").write_text("VALUE = 2\n", encoding="utf-8")
        changed = build_workflow_status(self.root)
        self.assertIn("ekzd verify", changed["next"])
        self.assertFalse(changed["worktree_clean"])

        self._finish_candidate()
        verification = verify_session(self.root)
        self.assertTrue(verification["passed"])

        verified = build_workflow_status(self.root)
        self.assertEqual("passed", verified["verification"])
        self.assertIn("ekzd finish --accept", verified["next"])

    def test_finished_status_retains_inactive_status_objective_and_next_action(self) -> None:
        self._start(branch="feat/finished-task")
        (self.root / "src/app.py").write_text("VALUE = 2\n", encoding="utf-8")
        self._finish_candidate()
        self.assertTrue(verify_session(self.root)["passed"])
        finish_session(self.root, accept=True)

        status = build_workflow_status(self.root)

        self.assertEqual({"session_status", "objective", "next"}, set(status))
        self.assertEqual("finished", status["session_status"])
        self.assertEqual("Change app", status["objective"])
        self.assertIn("ekzd start", status["next"])

    def test_aborted_status_retains_inactive_status_objective_and_next_action(self) -> None:
        self._start(branch="feat/aborted-task")
        abort_session(self.root)

        status = build_workflow_status(self.root)

        self.assertEqual({"session_status", "objective", "next"}, set(status))
        self.assertEqual("aborted", status["session_status"])
        self.assertEqual("Change app", status["objective"])
        self.assertIn("ekzd start", status["next"])

    def test_status_marks_successful_verification_stale_after_change(self) -> None:
        self._start()
        (self.root / "src/app.py").write_text("VALUE = 2\n", encoding="utf-8")
        self._finish_candidate()
        self.assertTrue(verify_session(self.root)["passed"])

        (self.root / "src/app.py").write_text("VALUE = 3\n", encoding="utf-8")
        status = build_workflow_status(self.root)

        self.assertEqual("stale", status["verification"])
        self.assertIn("rerun `ekzd verify`", status["next"])

    def test_status_detects_changed_contents_after_verified_commit(self) -> None:
        self._start(objective="Add helper")
        helper = self.root / "src/helper.py"
        helper.write_text("VALUE = 1\n", encoding="utf-8")
        self._finish_candidate()
        self.assertTrue(verify_session(self.root)["passed"])

        before = build_workflow_status(self.root)
        self.assertEqual("passed", before["verification"])

        helper.write_text("VALUE = 2\n", encoding="utf-8")
        after = build_workflow_status(self.root)

        self.assertEqual("stale", after["verification"])
        self.assertIn("rerun `ekzd verify`", after["next"])

    def test_status_reports_failed_verification(self) -> None:
        self._replace_config("print('ok')", "raise SystemExit(7)")
        self._start()
        self._finish_candidate()
        verification = verify_session(self.root)
        self.assertFalse(verification["passed"])

        status = build_workflow_status(self.root)

        self.assertEqual("failed", status["verification"])
        self.assertIn("Fix the verification failure", status["next"])

    def test_status_blocks_over_budget_session(self) -> None:
        self._start()
        self._commit_app(2, "change one")
        self._commit_app(3, "change two")
        self._commit_app(4, "change three")

        status = build_workflow_status(self.root)

        self.assertEqual("blocked", status["verification"])
        self.assertEqual(3, status["commit_count"])
        self.assertIn("commit budget exceeded", status["blocked_reason"])
        self.assertIn("Abort this session", status["next"])
        self.assertNotIn("run `ekzd verify`", status["next"])

    def test_status_blocks_history_that_no_longer_descends_from_baseline(self) -> None:
        (self.root / "README.md").write_text("# Demo\nsecond baseline commit\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "baseline two")
        self._start()
        self._git("reset", "--hard", "HEAD~1")

        status = build_workflow_status(self.root)

        self.assertEqual("blocked", status["verification"])
        self.assertIsNone(status["commit_count"])
        self.assertIn("diverged from its starting commit", status["blocked_reason"])
        self.assertIn("Abort this session", status["next"])


if __name__ == "__main__":
    unittest.main()

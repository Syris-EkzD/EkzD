from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from ekzd import cli
from ekzd.core import HarnessError, start_session
from ekzd.identity import BuildIdentityUnavailable
from ekzd.task import load_task_manifest, render_task_manifest_v2, write_task_manifest_file
from ekzd.workflow import build_worker_task_manifest, build_workflow_status, start_reproducible_session


CONFIG = """schema_version = 1

[project]
name = "demo"

[sources]
paths = ["README.md"]

[scope]
include = ["src/"]
exclude = ["src/generated/"]
constraints = ["Human-only constraint; do not export this as task authority."]

[authority]
may = ["Edit scoped source files."]
requires_approval = ["Merge to main."]
may_not = ["Change secrets."]

[session]
max_commits = 3

[acceptance]
criteria = ["Configured verification passes.", "Quoted value remains exact."]

[[verification.steps]]
name = "syntax"
command = ["python3", "-c", "print('ok')"]
cwd = "."
timeout_seconds = 30
"""


class TaskRenderingTests(unittest.TestCase):
    def test_schema_v2_renderer_is_stable_valid_toml_with_exact_trailing_newline(self) -> None:
        objective = 'Quote " and slash \\ and newline\nkept'
        manifest = render_task_manifest_v2(
            objective=objective,
            baseline="1" * 40,
            implementation_branch="feat/export",
            max_commits=4,
            include=["src/", 'quoted/"path"/'],
            exclude=["src/generated/"],
            acceptance_criteria=["One", 'Two "quoted" \\ value'],
            repository="https://github.com/example/demo",
            ekzd_version="0.3.0",
            ekzd_build_sha256="a" * 64,
        )

        parsed = tomllib.loads(manifest)
        self.assertEqual(2, parsed["schema_version"])
        self.assertEqual(objective, parsed["objective"])
        self.assertEqual(["src/", 'quoted/"path"/'], parsed["scope"]["include"])
        self.assertEqual(["One", 'Two "quoted" \\ value'], parsed["acceptance"]["criteria"])
        self.assertNotIn("verification", parsed)
        self.assertNotIn("\r", manifest)
        self.assertTrue(manifest.endswith("\n"))
        self.assertFalse(manifest.endswith("\n\n"))


class TaskExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.external = base / "exports"
        self.root.mkdir()
        self.external.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "task-export@example.invalid")
        self._git("config", "user.name", "Task Export Test")
        self._git("remote", "add", "origin", "git@github.com:example/demo.git")
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / ".ekzd").mkdir()
        (self.root / ".ekzd/project.toml").write_text(CONFIG, encoding="utf-8")
        (self.root / ".gitignore").write_text(".ekzd/session.json\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "baseline")
        self.baseline = self._git("rev-parse", "HEAD")

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def _start(self) -> None:
        start_reproducible_session(
            self.root,
            "Export worker task",
            implementation_branch="feat/export-worker-task",
        )

    def _manifest(self) -> str:
        with mock.patch(
            "ekzd.workflow.runtime_identity",
            return_value={"version": "0.3.0", "build_sha256": "a" * 64},
        ):
            return build_worker_task_manifest(self.root)

    def test_export_uses_frozen_authority_round_trips_and_does_not_duplicate_project_verification(self) -> None:
        self._start()
        state_path = self.root / ".ekzd/session.json"
        state_before = state_path.read_bytes()
        git_before = self._git("status", "--porcelain=v1", "--untracked-files=all")

        manifest = self._manifest()
        parsed = tomllib.loads(manifest)
        task_path = self.external / "task.toml"
        write_task_manifest_file(self.root, task_path, manifest)
        task = load_task_manifest(task_path)

        self.assertEqual(2, task.schema_version)
        self.assertEqual("Export worker task", task.objective)
        self.assertEqual(self.baseline, task.baseline)
        self.assertEqual("feat/export-worker-task", task.implementation_branch)
        self.assertEqual(3, task.max_commits)
        self.assertEqual(("src/",), task.include)
        self.assertEqual(("src/generated/",), task.exclude)
        self.assertEqual(("Configured verification passes.", "Quoted value remains exact."), task.acceptance_criteria)
        self.assertEqual("github.com/example/demo", task.repository)
        self.assertEqual("0.3.0", task.ekzd_version)
        self.assertEqual("a" * 64, task.ekzd_build_sha256)
        self.assertEqual((), task.verification_steps)
        self.assertNotIn("verification", parsed)
        self.assertNotIn("sources", parsed)
        self.assertNotIn("authority", parsed)
        self.assertNotIn("constraints", parsed["scope"])
        self.assertEqual(state_before, state_path.read_bytes())
        self.assertEqual(git_before, self._git("status", "--porcelain=v1", "--untracked-files=all"))

    def test_export_uses_exact_runtime_identity(self) -> None:
        self._start()
        parsed = tomllib.loads(build_worker_task_manifest(self.root))
        self.assertEqual("0.3.0", parsed["ekzd"]["version"])
        self.assertRegex(parsed["ekzd"]["build_sha256"], r"^[0-9a-f]{64}$")

    def test_export_omits_repository_when_no_origin_was_frozen(self) -> None:
        self._git("remote", "remove", "origin")
        self._start()
        parsed = tomllib.loads(self._manifest())
        self.assertNotIn("repository", parsed)

    def test_manifest_is_independent_of_current_directory_destination_and_mutable_worktree(self) -> None:
        self._start()
        first = self._manifest()
        old_cwd = Path.cwd()
        try:
            os.chdir(self.root / "src")
            second = self._manifest()
        finally:
            os.chdir(old_cwd)
        (self.root / "src/app.py").write_text("VALUE = 99\n", encoding="utf-8")
        third = self._manifest()

        first_path = self.external / "one.toml"
        second_path = self.external / "two.toml"
        write_task_manifest_file(self.root, first_path, first)
        write_task_manifest_file(self.root, second_path, second)

        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
        self.assertEqual(self.baseline, tomllib.loads(third)["baseline"])
        self.assertNotIn("VALUE = 99", third)

    def test_external_output_is_idempotent_and_rejects_unsafe_existing_destinations(self) -> None:
        self._start()
        manifest = self._manifest()
        destination = self.external / "task.toml"
        write_task_manifest_file(self.root, destination, manifest)
        before = destination.stat().st_mtime_ns
        write_task_manifest_file(self.root, destination, manifest)
        self.assertEqual(before, destination.stat().st_mtime_ns)
        self.assertEqual(manifest.encode("utf-8"), destination.read_bytes())

        different = self.external / "different.toml"
        different.write_bytes(b"keep me\n")
        with self.assertRaisesRegex(HarnessError, "different content"):
            write_task_manifest_file(self.root, different, manifest)
        self.assertEqual(b"keep me\n", different.read_bytes())

        directory = self.external / "directory"
        directory.mkdir()
        with self.assertRaisesRegex(HarnessError, "not a regular file"):
            write_task_manifest_file(self.root, directory, manifest)

        target = self.external / "target.toml"
        target.write_bytes(b"target\n")
        link = self.external / "link.toml"
        link.symlink_to(target)
        with self.assertRaisesRegex(HarnessError, "symlink"):
            write_task_manifest_file(self.root, link, manifest)
        self.assertEqual(b"target\n", target.read_bytes())

    def test_output_inside_project_and_failed_publication_leave_destination_absent(self) -> None:
        self._start()
        manifest = self._manifest()
        inside = self.root / "task.toml"
        with self.assertRaisesRegex(HarnessError, "outside"):
            write_task_manifest_file(self.root, inside, manifest)
        self.assertFalse(inside.exists())

        failed = self.external / "failed.toml"
        with mock.patch("ekzd.task.os.link", side_effect=OSError("denied")):
            with self.assertRaisesRegex(HarnessError, "Unable to publish"):
                write_task_manifest_file(self.root, failed, manifest)
        self.assertFalse(failed.exists())
        self.assertFalse(any(path.name.startswith(".failed.toml.") for path in self.external.iterdir()))

    def test_export_fails_closed_when_runtime_identity_is_unavailable(self) -> None:
        self._start()
        with mock.patch(
            "ekzd.workflow.runtime_identity",
            side_effect=BuildIdentityUnavailable("source unavailable"),
        ):
            with self.assertRaisesRegex(HarnessError, "runtime build identity is unavailable"):
                build_worker_task_manifest(self.root)

    def test_export_rejects_inactive_dirty_legacy_contract_invalid_and_protected_history_sessions(self) -> None:
        with self.assertRaisesRegex(HarnessError, "No active EkzD session"):
            build_worker_task_manifest(self.root)

        (self.root / "notes.txt").write_text("dirty\n", encoding="utf-8")
        start_session(self.root, "Legacy dirty")
        with self.assertRaisesRegex(HarnessError, "dirty working tree"):
            build_worker_task_manifest(self.root)
        (self.root / ".ekzd/session.json").unlink()
        (self.root / "notes.txt").unlink()

        self._start()
        config = self.root / ".ekzd/project.toml"
        original = config.read_text(encoding="utf-8")
        config.write_text(original + "\n", encoding="utf-8")
        with self.assertRaises(HarnessError):
            build_worker_task_manifest(self.root)
        config.write_text(original, encoding="utf-8")

        config.write_text(original + "\n# temporary protected change\n", encoding="utf-8")
        self._git("add", ".ekzd/project.toml")
        self._git("commit", "-qm", "temporary protected change")
        config.write_text(original, encoding="utf-8")
        self._git("add", ".ekzd/project.toml")
        self._git("commit", "-qm", "restore protected config")
        with self.assertRaisesRegex(HarnessError, "Protected EkzD harness files"):
            build_worker_task_manifest(self.root)

    def test_new_session_guidance_surfaces_prompt_and_task(self) -> None:
        self._start()
        status = build_workflow_status(self.root)
        self.assertIn("ekzd prompt", status["next"])
        self.assertIn("ekzd task", status["next"])

    def test_cli_stdout_is_manifest_only_and_file_mode_is_silent(self) -> None:
        manifest = "schema_version = 2\n"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "find_root", return_value=self.root),
            mock.patch.object(cli, "build_worker_task_manifest", return_value=manifest),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(["task"])
        self.assertEqual(0, code)
        self.assertEqual(manifest, stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

        output = self.external / "cli.toml"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "find_root", return_value=self.root),
            mock.patch.object(cli, "build_worker_task_manifest", return_value=manifest),
            mock.patch.object(cli, "write_task_manifest_file") as writer,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(["task", "--output", str(output)])
        self.assertEqual(0, code)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())
        writer.assert_called_once_with(self.root, output, manifest)

    def test_cli_export_failure_emits_no_manifest_stdout(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "find_root", return_value=self.root),
            mock.patch.object(cli, "build_worker_task_manifest", side_effect=HarnessError("blocked export")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(["task"])
        self.assertEqual(1, code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("blocked export", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

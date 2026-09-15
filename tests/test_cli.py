from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd import cli
from ekzd.core import HarnessError
from ekzd.identity import BuildIdentityUnavailable


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class ExplodingInput(io.StringIO):
    def read(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("bare ekzd must not read interactive input")

    def readline(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("bare ekzd must not read interactive input")

    def isatty(self) -> bool:
        return True


class CliPresentationTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "find_root", return_value=Path("/repo")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_no_subcommand_prints_help_and_exits_successfully_without_repository_lookup(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "find_root") as find_root,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main([])

        self.assertEqual(0, code)
        find_root.assert_not_called()
        self.assertIn("usage: ekzd", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_no_subcommand_does_not_read_interactive_input_or_depend_on_tty(self) -> None:
        for stdin in (ExplodingInput(), io.StringIO()):
            with self.subTest(tty=stdin.isatty()):
                stdout = TtyBuffer()
                stderr = io.StringIO()
                with (
                    mock.patch.object(cli.sys, "stdin", stdin),
                    mock.patch.object(cli.sys, "stdout", stdout),
                    mock.patch.object(cli.sys, "stderr", stderr),
                    mock.patch.object(cli, "find_root") as find_root,
                ):
                    code = cli.main([])
                self.assertEqual(0, code)
                find_root.assert_not_called()
                self.assertIn("usage: ekzd", stdout.getvalue())
                self.assertEqual("", stderr.getvalue())

    def test_version_reports_exact_identity_without_repository_lookup(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "find_root") as find_root,
            mock.patch.object(cli, "runtime_identity", return_value={"version": "0.2.0", "build_sha256": "a" * 64}),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(["--version"])

        self.assertEqual(0, code)
        find_root.assert_not_called()
        self.assertEqual(f"EkzD 0.2.0 {'a' * 64}\n", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_version_fails_closed_when_exact_identity_is_unavailable(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "find_root") as find_root,
            mock.patch.object(cli, "runtime_identity", side_effect=BuildIdentityUnavailable("source unavailable")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(["--version"])

        self.assertEqual(1, code)
        find_root.assert_not_called()
        self.assertEqual("", stdout.getvalue())
        self.assertIn("exact runtime build identity unavailable", stderr.getvalue())
        self.assertNotIn("unknown", stderr.getvalue().lower())

    def test_module_version_command_works_outside_project_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            process = subprocess.run(
                [sys.executable, "-m", "ekzd.cli", "--version"],
                cwd=temp_dir,
                env=env,
                text=True,
                capture_output=True,
            )
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertRegex(process.stdout, r"^EkzD 0\.2\.0 [0-9a-f]{64}\n$")
        self.assertEqual("", process.stderr)

    def test_help_lists_retained_commands_and_omits_handoff_notes_command(self) -> None:
        argument_parser = cli.parser()
        help_text = argument_parser.format_help()
        for command in ("init", "start", "status", "prompt", "context", "verify", "check", "abort", "finish"):
            self.assertIn(command, help_text)
        self.assertIn("--version", help_text)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            argument_parser.parse_args(["handoff"])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("invalid choice", stderr.getvalue())

    def test_check_help_remains_available(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            cli.main(["check", "--help"])
        self.assertEqual(0, raised.exception.code)
        self.assertIn("--task", stdout.getvalue())
        self.assertIn("--final", stdout.getvalue())
        self.assertIn("--json", stdout.getvalue())

    def test_prompt_remains_exact_plain_contract_output(self) -> None:
        contract = "Repository: github.com/example/demo\nObjective\n\nDo the thing.\n"
        with mock.patch.object(cli, "build_implementation_prompt", return_value=contract):
            code, stdout, stderr = self._run(["prompt"])

        self.assertEqual(0, code)
        self.assertEqual(contract, stdout)
        self.assertEqual("", stderr)
        self.assertNotIn("\x1b[", stdout)
        self.assertNotIn("EkzD ·", stdout)

    def test_context_json_remains_machine_readable_and_structurally_unchanged(self) -> None:
        context = {
            "project": {"name": "Demo"},
            "session_status": "active",
            "git": {"branch": "main", "head": "abc", "status": []},
            "scope": {"include": ["src/"]},
        }
        with mock.patch.object(cli, "build_context", return_value=context):
            code, stdout, stderr = self._run(["context", "--json"])

        self.assertEqual(0, code)
        self.assertEqual(context, json.loads(stdout))
        self.assertEqual("", stderr)
        self.assertNotIn("\x1b[", stdout)

    def test_verify_failure_keeps_failure_exit_code_and_human_output_on_stdout(self) -> None:
        verification = {
            "passed": False,
            "steps": [
                {"name": "compile", "passed": True},
                {"name": "tests", "passed": False},
            ],
        }
        with mock.patch.object(cli, "verify_session", return_value=verification):
            code, stdout, stderr = self._run(["verify"])

        self.assertEqual(1, code)
        self.assertEqual("", stderr)
        self.assertIn("EkzD · verify", stdout)
        self.assertIn("✓ compile", stdout)
        self.assertIn("✗ tests", stdout)

    def test_harness_errors_remain_on_stderr(self) -> None:
        with mock.patch.object(cli, "build_workflow_status", side_effect=HarnessError("blocked")):
            code, stdout, stderr = self._run(["status"])

        self.assertEqual(1, code)
        self.assertEqual("", stdout)
        self.assertIn("✗ EkzD: blocked", stderr)
        self.assertNotIn("\x1b[", stderr)

    def test_no_color_disables_ansi_for_tty_cli_output(self) -> None:
        status = {
            "session_status": "active",
            "project": "Demo",
            "objective": "Change one thing",
            "current_branch": "main",
            "baseline_branch": "main",
            "implementation_branch": "feat/demo",
            "baseline_head": "1234567890abcdef",
            "head": "1234567890abcdef",
            "worktree_clean": True,
            "commit_count": 0,
            "max_commits": 3,
            "verification": "not run",
            "blocked_reason": None,
            "next": "Generate the implementation prompt.",
        }
        stdout = TtyBuffer()
        stderr = TtyBuffer()
        with (
            mock.patch.object(cli, "find_root", return_value=Path("/repo")),
            mock.patch.object(cli, "build_workflow_status", return_value=status),
            mock.patch.object(cli.sys, "stdout", stdout),
            mock.patch.object(cli.sys, "stderr", stderr),
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=True),
        ):
            code = cli.main(["status"])

        self.assertEqual(0, code)
        self.assertNotIn("\x1b[", stdout.getvalue())
        self.assertIn("EkzD · status", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_finished_status_recovers_persisted_project_identity(self) -> None:
        status = {
            "session_status": "finished",
            "objective": "Change one thing",
            "next": 'Start a new session with `ekzd start "<objective>" --branch <task-branch>`.',
        }
        state = {
            "status": "finished",
            "project": "Demo",
            "objective": "Change one thing",
        }
        self.assertNotIn("project", status)

        with (
            mock.patch.object(cli, "build_workflow_status", return_value=status) as build_status,
            mock.patch.object(cli, "read_state", return_value=state) as read_state,
        ):
            code, stdout, stderr = self._run(["status"])

        build_status.assert_called_once_with(Path("/repo"))
        read_state.assert_called_once_with(Path("/repo"))
        self.assertEqual(0, code)
        self.assertIn("! Session: finished", stdout)
        self.assertIn("project  Demo", stdout)
        self.assertEqual("", stderr)

    def test_check_human_output_and_success_exit_code(self) -> None:
        result = {
            "schema_version": 1,
            "overall_status": "PASS",
            "ready": True,
            "mode": "development",
            "ekzd": {"version": "0.2.0", "build_sha256": "e" * 64},
            "task": {"path": "/tmp/task.toml", "sha256": "a" * 64},
            "baseline": "b" * 40,
            "git": {"head": "c" * 40, "branch": "feat/demo", "clean": False, "worktree_fingerprint": "d" * 64},
            "checks": [{"name": "task-manifest", "status": "PASS", "required": True, "message": "valid"}],
        }
        with mock.patch.object(cli, "run_worker_check", return_value=result) as run_check:
            code, stdout, stderr = self._run(["check", "--task", "/tmp/task.toml"])
        self.assertEqual(0, code)
        self.assertIn("EkzD · check", stdout)
        self.assertIn("ekzd    0.2.0", stdout)
        self.assertIn(f"build   {'e' * 64}", stdout)
        self.assertIn("PASS", stdout)
        self.assertEqual("", stderr)
        run_check.assert_called_once_with(Path("/repo"), Path("/tmp/task.toml"), final=False)

    def test_check_final_json_preserves_failure_exit_code(self) -> None:
        result = {
            "schema_version": 1,
            "overall_status": "FAIL",
            "ready": False,
            "mode": "final",
            "ekzd": {"version": "0.2.0", "build_sha256": "e" * 64},
            "task": {"path": "/tmp/task.toml", "sha256": "a" * 64},
            "baseline": "b" * 40,
            "git": {"head": "c" * 40, "branch": "feat/demo", "clean": False, "worktree_fingerprint": "d" * 64},
            "checks": [{"name": "worktree-mode", "status": "FAIL", "required": True, "message": "dirty"}],
        }
        with mock.patch.object(cli, "run_worker_check", return_value=result):
            code, stdout, stderr = self._run(["check", "--final", "--task", "/tmp/task.toml", "--json"])
        self.assertEqual(1, code)
        self.assertEqual(result, json.loads(stdout))
        self.assertEqual("", stderr)

    def test_check_json_manifest_validation_failure_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task = Path(temp_dir) / "task.toml"
            task.write_text("schema_version = 99\n", encoding="utf-8")
            code, stdout, stderr = self._run(["check", "--task", str(task), "--json"])
        payload = json.loads(stdout)
        self.assertEqual(1, code)
        self.assertFalse(payload["ready"])
        self.assertEqual("task-manifest", payload["checks"][0]["name"])
        self.assertEqual("FAIL", payload["checks"][0]["status"])
        self.assertRegex(payload["ekzd"]["build_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual("", stderr)

    def test_check_json_root_failure_is_structured(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "find_root", side_effect=HarnessError("not a repo")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(["check", "--task", "/tmp/task.toml", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(1, code)
        self.assertFalse(payload["ready"])
        self.assertEqual("worker-check", payload["checks"][0]["name"])
        self.assertEqual("UNAVAILABLE", payload["checks"][0]["status"])
        self.assertEqual("0.2.0", payload["ekzd"]["version"])
        self.assertRegex(payload["ekzd"]["build_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual("", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

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
            mock.patch.object(cli, "runtime_identity", return_value={"version": "0.3.0", "build_sha256": "a" * 64}),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main(["--version"])

        self.assertEqual(0, code)
        find_root.assert_not_called()
        self.assertEqual(f"EkzD 0.3.0 {'a' * 64}\n", stdout.getvalue())
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
        self.assertRegex(process.stdout, r"^EkzD 0\.3\.0 [0-9a-f]{64}\n$")
        self.assertEqual("", process.stderr)

    def test_handoff_replaces_temporary_worker_commands(self):
        parser = cli.parser()
        self.assertIn('handoff', parser.format_help())
        for command in ('contract', 'prompt', 'check'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args([command])
        with mock.patch.object(cli, 'export_handoff', return_value=Path('/tmp/handoff.zip')):
            code, stdout, stderr = self._run(['handoff'])
        self.assertEqual((0, '/tmp/handoff.zip\n', ''), (code, stdout, stderr))

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
        self.assertIn("✗ Verification failed", stdout)

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
            "implementation_branch": "feat/demo",
            "baseline_head": "1234567890abcdef",
            "handoff_path": "/repo/.ekzd/local/handoffs/demo/handoff.zip",
            "head": "1234567890abcdef",
            "worktree_clean": True,
            "commit_count": 0,
            "max_commits": 3,
            "verification": "not run",
            "blocked_reason": None,
            "next": "Transfer the existing handoff archive.",
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

    def test_finished_status_includes_frozen_project_identity(self):
        status = dict(session_status='finished', project='Demo', objective='Done', next='Start next task')
        with mock.patch.object(cli, 'build_workflow_status', return_value=status):
            code, stdout, stderr = self._run(['status'])
        self.assertEqual(0, code)
        self.assertIn('project  Demo', stdout)
        self.assertEqual('', stderr)



if __name__ == "__main__":
    unittest.main()

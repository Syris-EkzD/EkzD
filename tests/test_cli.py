from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from ekzd import cli
from ekzd.core import HarnessError


class TtyBuffer(io.StringIO):
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
            "baseline_branch": "main",
            "implementation_branch": "feat/demo",
            "baseline_head": "1234567890abcdef",
            "head": "1234567890abcdef",
            "worktree_clean": True,
            "commit_count": 0,
            "max_commits": 3,
            "verification": "not run",
            "blocked_reason": None,
            "next": "Generate the handoff.",
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


if __name__ == "__main__":
    unittest.main()

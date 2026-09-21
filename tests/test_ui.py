from __future__ import annotations

import io
import os
import unittest
from unittest import mock

from ekzd.ui import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    render_command_summary,
    render_status_ui,
    render_verification_ui,
    success,
    supports_color,
)


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class UiTests(unittest.TestCase):
    def test_color_enabled_for_tty(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(supports_color(TtyBuffer()))

    def test_no_color_disables_ansi(self) -> None:
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=True):
            self.assertFalse(supports_color(TtyBuffer()))

    def test_non_tty_disables_ansi(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(supports_color(io.StringIO()))

    def test_success_uses_semantic_color_when_enabled(self) -> None:
        rendered = success("Verification passed", enabled=True)
        self.assertIn("\x1b[32m", rendered)
        self.assertIn(RESET, rendered)

    def test_command_summary_uses_consistent_sections_without_ansi(self) -> None:
        rendered = render_command_summary(
            "session",
            "Session started",
            tone="success",
            details=[("objective", "Change one thing"), ("implementation branch", "feat/demo")],
            next_action="Run `ekzd status`.",
            enabled=False,
        )
        self.assertNotIn("\x1b[", rendered)
        self.assertIn("EkzD · session", rendered)
        self.assertIn("✓ Session started", rendered)
        self.assertIn("objective  Change one thing", rendered)
        self.assertIn("implementation branch  feat/demo", rendered)
        self.assertIn("Next", rendered)
        self.assertIn("› Run `ekzd status`.", rendered)

    def test_command_summary_keeps_important_detail_labels_prominent(self) -> None:
        rendered = render_command_summary(
            "session",
            "Session started",
            tone="success",
            details=[("objective", "Change one thing"), ("implementation branch", "feat/demo")],
            enabled=True,
        )
        for label in ("objective", "implementation branch"):
            self.assertIn(BOLD + CYAN + label, rendered)
            self.assertNotIn(DIM + label, rendered)

    def test_status_plain_output_emphasizes_state_verification_and_next_action(self) -> None:
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
            "verification": "stale",
            "blocked_reason": None,
            "next": "Rerun `ekzd verify`.",
        }
        rendered = render_status_ui(status, enabled=False)
        self.assertNotIn("\x1b[", rendered)
        self.assertIn("EkzD · status", rendered)
        self.assertIn("✓ Session active", rendered)
        self.assertIn("project  Demo", rendered)
        self.assertIn("verification  stale", rendered)
        self.assertIn("Branches", rendered)
        self.assertIn("implementation  feat/demo", rendered)
        self.assertIn("Repository", rendered)
        self.assertIn("handoff  /repo/.ekzd/local/handoffs/demo/handoff.zip", rendered)
        self.assertNotIn("baseline  main", rendered)
        self.assertIn("commits  0 / 3", rendered)
        self.assertIn("Next", rendered)
        self.assertIn("› Rerun `ekzd verify`.", rendered)

    def test_finished_status_plain_output_includes_project_identity(self) -> None:
        rendered = render_status_ui(
            {
                "session_status": "finished",
                "project": "Demo",
                "objective": "Change one thing",
                "next": "Start a new task with `ekzd start task.toml`.",
            },
            enabled=False,
        )
        self.assertNotIn("\x1b[", rendered)
        self.assertIn("! Session: finished", rendered)
        self.assertIn("project  Demo", rendered)
        self.assertIn("objective  Change one thing", rendered)

    def test_status_important_labels_are_not_dim_and_not_run_is_warning_yellow(self) -> None:
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
        rendered = render_status_ui(status, enabled=True)
        for label in ("project", "objective", "verification", "implementation"):
            self.assertIn(BOLD + CYAN + label, rendered)
            self.assertNotIn(DIM + label, rendered)
        self.assertIn(YELLOW + "not run", rendered)

    def test_finished_non_active_status_uses_success_green(self) -> None:
        rendered = render_status_ui(
            {
                "session_status": "finished",
                "project": "Demo",
                "objective": "Completed task",
                "next": "Start a new session.",
            },
            enabled=True,
        )
        self.assertIn(GREEN + "Session: finished", rendered)
        self.assertNotIn(YELLOW + "Session: finished", rendered)

        plain = render_status_ui(
            {
                "session_status": "finished",
                "project": "Demo",
                "objective": "Completed task",
                "next": "Start a new session.",
            },
            enabled=False,
        )
        self.assertIn("! Session: finished", plain)
        self.assertNotIn("✓ Session: finished", plain)

    def test_aborted_non_active_status_remains_warning_yellow(self) -> None:
        rendered = render_status_ui(
            {
                "session_status": "aborted",
                "project": "Demo",
                "objective": "Cancelled task",
                "next": "Start a new session.",
            },
            enabled=True,
        )
        self.assertIn(YELLOW + "Session: aborted", rendered)
        self.assertNotIn(GREEN + "Session: aborted", rendered)

    def test_status_blocked_output_surfaces_reason(self) -> None:
        status = {
            "session_status": "active",
            "project": "Demo",
            "objective": "Change one thing",
            "current_branch": "main",
            "implementation_branch": "feat/demo",
            "baseline_head": "1234567890abcdef",
            "handoff_path": "/repo/.ekzd/local/handoffs/demo/handoff.zip",
            "head": "fedcba0987654321",
            "worktree_clean": True,
            "commit_count": 4,
            "max_commits": 3,
            "verification": "blocked",
            "blocked_reason": "Session commit budget exceeded.",
            "next": "Abort this session.",
        }
        rendered = render_status_ui(status, enabled=False)
        self.assertIn("✗ Session blocked", rendered)
        self.assertIn("verification  blocked", rendered)
        self.assertIn("Blocked", rendered)
        self.assertIn("✗ Session commit budget exceeded.", rendered)
        self.assertIn("› Abort this session.", rendered)

    def test_verification_output_shows_steps_and_final_result(self) -> None:
        verification = {
            "passed": False,
            "steps": [
                {"name": "compile", "passed": True},
                {"name": "tests", "passed": False},
            ],
        }
        rendered = render_verification_ui(verification, enabled=False)
        self.assertNotIn("\x1b[", rendered)
        self.assertIn("EkzD · verify", rendered)
        self.assertIn("Steps", rendered)
        self.assertIn("✓ compile", rendered)
        self.assertIn("✗ tests", rendered)
        self.assertIn("Result", rendered)
        self.assertIn("✗ Verification failed", rendered)
        self.assertIn("› Fix the reported failure and rerun `ekzd verify`.", rendered)

    def test_failed_verification_renders_stderr_under_its_step(self) -> None:
        verification = {"passed": False, "steps": [
            {"name": "compile", "status": "PASS", "message": "Compilation passed."},
            {"name": "tests", "status": "FAIL", "message": "Tests failed.",
             "details": {"stderr": "AssertionError: expected 1"}},
        ]}
        rendered = render_verification_ui(verification, enabled=False)
        self.assertIn("✗ tests: Tests failed.\n    stderr:\n      AssertionError: expected 1", rendered)
        self.assertNotIn("    stderr:", rendered.split("✗ tests:", 1)[0])
        self.assertIn("✗ Verification failed", rendered)

    def test_failed_verification_renders_stdout(self) -> None:
        rendered = render_verification_ui(
            {"passed": False, "steps": [{"name": "tests", "status": "FAIL",
                                         "details": {"stdout": "FAIL: case_a"}}]},
            enabled=False,
        )
        self.assertIn("✗ tests\n    stdout:\n      FAIL: case_a", rendered)

    def test_failed_verification_keeps_both_multiline_streams_and_truncation_marker(self) -> None:
        marker = "[... 1024 bytes truncated by EkzD ...]"
        rendered = render_verification_ui(
            {"passed": False, "steps": [
                {"name": "unit", "status": "FAIL", "details": {
                    "stderr": f"first error\n\n{marker}\nlast error\n",
                    "stdout": "test A ... ok\ntest B ... FAIL\n",
                    "stderr_truncated": True, "stdout_truncated": True}},
                {"name": "integration", "status": "FAIL", "details": {"stderr": "independent failure"}},
            ]},
            enabled=False,
        )
        self.assertIn(
            f"✗ unit\n    stderr (truncated):\n      first error\n      \n      {marker}\n      last error\n"
            "    stdout (truncated):\n      test A ... ok\n      test B ... FAIL\n"
            "  ✗ integration\n    stderr:\n      independent failure",
            rendered,
        )
        self.assertNotIn("\x1b[", rendered)

    def test_unavailable_verification_renders_captured_diagnostics(self) -> None:
        rendered = render_verification_ui(
            {"passed": False, "steps": [
                {"name": "readiness:compiler", "status": "UNAVAILABLE",
                 "message": "Capability unavailable.",
                 "details": {"stderr": "command not found", "stdout": "checked PATH"}},
            ]},
            enabled=False,
        )
        self.assertIn(
            "✗ readiness:compiler: Capability unavailable.\n"
            "    stderr:\n      command not found\n    stdout:\n      checked PATH",
            rendered,
        )

    def test_missing_empty_and_malformed_optional_diagnostics_are_skipped(self) -> None:
        verification = {"passed": False, "steps": [
            {"name": "missing", "status": "FAIL"},
            {"name": "none", "status": "FAIL", "details": None},
            {"name": "list", "status": "UNAVAILABLE", "details": ["not a mapping"]},
            {"name": "wrong-streams", "status": "FAIL",
             "details": {"stderr": {"error": "not a string"}, "stdout": ["not a string"],
                         "stderr_truncated": "yes"}},
            {"name": "blank", "status": "FAIL",
             "details": {"stderr": " \n\t ", "stdout": ""}},
        ]}
        rendered = render_verification_ui(verification, enabled=False)
        for name in ("missing", "none", "list", "wrong-streams", "blank"):
            self.assertIn(f"✗ {name}", rendered)
        self.assertNotIn("    stderr:", rendered)
        self.assertNotIn("    stdout:", rendered)
        self.assertIn("✗ Verification failed", rendered)

    def test_successful_verification_suppresses_diagnostics(self) -> None:
        rendered = render_verification_ui(
            {"passed": True, "steps": [
                {"name": "tests", "status": "PASS", "message": "All tests passed.",
                 "details": {"stdout": "long successful log", "stderr": "unnecessary warning",
                             "stdout_truncated": True}},
            ]},
            enabled=False,
        )
        self.assertIn("✓ tests: All tests passed.", rendered)
        self.assertIn("✓ Verification passed", rendered)
        self.assertNotIn("long successful log", rendered)
        self.assertNotIn("unnecessary warning", rendered)
        self.assertNotIn("    stdout", rendered)
        self.assertNotIn("    stderr", rendered)

    def test_failed_diagnostics_render_with_semantic_ansi_but_plain_stream_text(self) -> None:
        verification = {"passed": False, "steps": [
            {"name": "tests", "status": "FAIL", "details": {"stderr": "line one\nline two"}},
        ]}
        colored = render_verification_ui(verification, enabled=True)
        plain = render_verification_ui(verification, enabled=False)
        self.assertIn(RED + "tests", colored)
        self.assertIn("    stderr:\n      line one\n      line two", colored)
        self.assertNotIn("\x1b[", plain)
        self.assertIn("✗ tests\n    stderr:\n      line one\n      line two", plain)


if __name__ == "__main__":
    unittest.main()

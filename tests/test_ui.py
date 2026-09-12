from __future__ import annotations

import io
import os
import unittest
from unittest import mock

from ekzd.ui import (
    RED,
    RESET,
    render_command_summary,
    render_context_ui,
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

    def test_status_plain_output_emphasizes_state_verification_and_next_action(self) -> None:
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
            "verification": "stale",
            "blocked_reason": None,
            "next": "Rerun `ekzd verify`.",
        }

        rendered = render_status_ui(status, enabled=False)

        self.assertNotIn("\x1b[", rendered)
        self.assertIn("EkzD · status", rendered)
        self.assertIn("✓ Session active", rendered)
        self.assertIn("verification  stale", rendered)
        self.assertIn("Branches", rendered)
        self.assertIn("implementation  feat/demo", rendered)
        self.assertIn("Repository", rendered)
        self.assertIn("commits  0 / 3", rendered)
        self.assertIn("Next", rendered)
        self.assertIn("› Rerun `ekzd verify`.", rendered)

    def test_status_blocked_output_surfaces_reason(self) -> None:
        status = {
            "session_status": "active",
            "project": "Demo",
            "objective": "Change one thing",
            "current_branch": "main",
            "baseline_branch": "main",
            "implementation_branch": "feat/demo",
            "baseline_head": "1234567890abcdef",
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

    def test_context_plain_output_remains_readable(self) -> None:
        context = {
            "project": {"name": "Demo"},
            "objective": "Change one thing",
            "session_status": "active",
            "session": {"max_commits": 3},
            "git": {"branch": "feature/demo", "head": "1234567890abcdef", "status": []},
            "scope": {"include": ["src/"], "exclude": [], "constraints": ["Stay focused."]},
            "authority": {
                "may": ["Edit src."],
                "requires_approval": ["Merge."],
                "may_not": ["Deploy."],
            },
            "acceptance": {"criteria": ["Tests pass."]},
            "verification": {
                "steps": [
                    {
                        "name": "tests",
                        "command": ["python3", "-m", "unittest"],
                    }
                ]
            },
        }

        rendered = render_context_ui(context, enabled=False)

        self.assertNotIn("\x1b[", rendered)
        self.assertIn("EkzD · Demo", rendered)
        self.assertIn("feature/demo @ 12345678 · clean", rendered)
        self.assertIn("Scope", rendered)
        self.assertIn("Verification", rendered)
        self.assertIn("tests python3 -m unittest", rendered)

    def test_context_color_output_contains_semantic_ansi(self) -> None:
        context = {
            "project": {"name": "Demo"},
            "objective": "Change one thing",
            "session_status": "active",
            "session": {"max_commits": 3},
            "git": {"branch": "main", "head": "1234567890abcdef", "status": [" M README.md"]},
            "scope": {"include": ["README.md"], "exclude": [], "constraints": []},
            "authority": {"may": [], "requires_approval": [], "may_not": []},
            "acceptance": {"criteria": ["Review complete."]},
            "verification": {"steps": []},
        }

        rendered = render_context_ui(context, enabled=True)

        self.assertIn("\x1b[36m", rendered)
        self.assertIn("\x1b[33m", rendered)
        self.assertNotIn(RED, rendered)


if __name__ == "__main__":
    unittest.main()

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
    render_context_ui,
    render_contract_review,
    render_interactive_home,
    render_previous_task_card,
    render_status_ui,
    render_terminal_contract_review,
    render_terminal_header,
    render_terminal_idle_header,
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
        self.assertIn("project  Demo", rendered)
        self.assertIn("verification  stale", rendered)
        self.assertIn("Branches", rendered)
        self.assertIn("implementation  feat/demo", rendered)
        self.assertIn("Repository", rendered)
        self.assertIn("commits  0 / 3", rendered)
        self.assertIn("Next", rendered)
        self.assertIn("› Rerun `ekzd verify`.", rendered)

    def test_finished_status_plain_output_includes_project_identity(self) -> None:
        status = {
            "session_status": "finished",
            "project": "Demo",
            "objective": "Change one thing",
            "next": 'Start a new session with `ekzd start "<objective>" --branch <task-branch>`.',
        }

        rendered = render_status_ui(status, enabled=False)

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

    def test_idle_terminal_header_is_distinct_and_omits_active_placeholders(self) -> None:
        status = {
            "session_status": "none",
            "objective": None,
            "next": "Start a new session.",
        }

        rendered = render_terminal_idle_header("Demo", status, width=94, enabled=False)

        self.assertNotIn("\x1b[", rendered)
        self.assertIn("Ready for a new task", rendered)
        self.assertIn("Scopes development work", rendered)
        self.assertIn("Project", rendered)
        self.assertIn("Next", rendered)
        self.assertNotIn("Branch", rendered)
        self.assertNotIn("Verification", rendered)
        self.assertNotIn("Commits", rendered)
        self.assertNotIn("(unknown)", rendered)
        self.assertNotIn("not run", rendered)
        self.assertNotIn("Previous task", rendered)

    def test_idle_terminal_header_shows_finished_previous_task_as_structured_card(self) -> None:
        objective = "Previous finished objective " * 8
        status = {
            "session_status": "finished",
            "objective": objective,
            "previous_implementation_branch": "feat/previous",
            "previous_verification": "passed",
            "previous_result": "accepted",
            "next": "Start a new session.",
        }

        rendered = render_terminal_idle_header("Demo", status, width=72, enabled=False)

        self.assertIn("Ready for a new task", rendered)
        self.assertIn("┌─ Previous task", rendered)
        self.assertIn("State / Result", rendered)
        self.assertIn("finished · accepted", rendered)
        self.assertIn("Objective", rendered)
        self.assertIn("Implementation branch", rendered)
        self.assertIn("feat/previous", rendered)
        self.assertIn("Verification", rendered)
        self.assertIn("passed", rendered)
        self.assertIn("…", rendered)
        self.assertNotIn(objective, rendered)

    def test_idle_terminal_header_shows_aborted_previous_task_with_semantic_color(self) -> None:
        status = {
            "session_status": "aborted",
            "objective": "Discarded experiment",
            "previous_implementation_branch": "feat/discarded",
            "previous_verification": "failed",
            "previous_result": "aborted",
            "next": "Start a new session.",
        }

        rendered = render_terminal_idle_header("Demo", status, width=94, enabled=True)

        self.assertIn("Previous task", rendered)
        self.assertIn(YELLOW + "aborted · aborted", rendered)
        self.assertIn(RED + "failed", rendered)
        self.assertIn("Discarded experiment", rendered)

    def test_idle_fallback_home_is_semantically_idle_with_prominent_labels(self) -> None:
        status = {
            "session_status": "finished",
            "objective": "Previous task only",
            "previous_implementation_branch": "feat/previous",
            "previous_verification": "passed",
            "previous_result": "accepted",
            "next": "Start a new session.",
        }

        rendered = render_interactive_home(
            "Demo",
            status,
            ["Start task", "View task", "Exit"],
            enabled=True,
        )

        self.assertIn("Ready for a new task", rendered)
        self.assertIn("Previous task", rendered)
        self.assertIn("Previous task only", rendered)
        self.assertIn("Implementation branch", rendered)
        self.assertIn("Result", rendered)
        self.assertIn("\x1b[1m\x1b[36mProject", rendered)
        self.assertIn("\x1b[1m\x1b[36mState", rendered)
        self.assertIn("Start task", rendered)
        self.assertIn("View task", rendered)
        self.assertIn("Exit", rendered)
        self.assertNotIn("session  finished", rendered)

    def test_contract_review_summarizes_committed_contract_and_decisions(self) -> None:
        review = {
            "project": "Demo",
            "sources_count": 3,
            "scope_include_count": 2,
            "scope_exclude_count": 1,
            "constraint_count": 4,
            "verification_count": 2,
            "max_commits": 5,
        }
        rendered = render_contract_review(review, "Do the task", "feat/demo", enabled=False)
        self.assertIn("Review committed contract", rendered)
        self.assertIn("Objective  Do the task", rendered)
        self.assertIn("Implementation branch  feat/demo", rendered)
        self.assertIn("Sources  3 paths", rendered)
        self.assertIn("Scope  2 included · 1 excluded · 4 constraints", rendered)
        self.assertIn("Verification  2 steps", rendered)
        self.assertIn("Max commits  5", rendered)
        self.assertIn("Confirm and start", rendered)
        self.assertIn("Update contract first", rendered)
        self.assertIn("Cancel", rendered)

        terminal = render_terminal_contract_review(review, "Do the task", "feat/demo", width=94, enabled=False)
        self.assertTrue(terminal.startswith("┌─ EkzD · Contract review"))
        self.assertTrue(all(len(line) == 93 for line in terminal.rstrip("\n").splitlines()))

    def test_public_terminal_header_dispatches_idle_state(self) -> None:
        status = {
            "session_status": "finished",
            "objective": "Previous task",
            "next": "Start a new session.",
        }
        rendered = render_terminal_header("Demo", status, width=94, enabled=False)
        self.assertIn("Ready for a new task", rendered)
        self.assertIn("Previous task", rendered)
        self.assertNotIn("Commits", rendered)

    def test_active_terminal_header_retains_operational_rows(self) -> None:
        status = {
            "session_status": "active",
            "objective": "Current task",
            "current_branch": "feat/demo",
            "verification": "not run",
            "commit_count": 1,
            "max_commits": 3,
            "next": "Verify when ready.",
        }

        rendered = render_terminal_header("Demo", status, width=94, enabled=False)

        for label in ("Task", "Branch", "Session", "Verification", "Commits", "Next"):
            self.assertIn(label, rendered)
        self.assertNotIn("Ready for a new task", rendered)


if __name__ == "__main__":
    unittest.main()

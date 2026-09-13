from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest import mock

from ekzd import interactive
from ekzd.core import HarnessError

ROOT = Path("/repo")


def active_status(verification: str = "not run") -> dict[str, object]:
    next_action = {
        "passed": "Review the verified changes, then accept the task.",
        "blocked": "Abort this session and restart from a valid baseline.",
        "stale": "Repository state changed after verification; verify changes again.",
        "failed": "Fix the verification failure, then verify changes again.",
    }.get(verification, "Generate the implementation prompt.")
    return {
        "session_status": "active",
        "project": "Demo",
        "objective": "Change one thing",
        "verification": verification,
        "next": next_action,
        "baseline_branch": "main",
        "implementation_branch": "feat/demo",
        "current_branch": "feat/demo",
        "baseline_head": "1234567890abcdef",
        "head": "fedcba0987654321",
        "worktree_clean": True,
        "commit_count": 1,
        "max_commits": 3,
        "blocked_reason": "blocked" if verification == "blocked" else None,
    }


def fresh_status() -> dict[str, object]:
    status = active_status()
    status.update(
        current_branch="main",
        head=status["baseline_head"],
        worktree_clean=True,
        commit_count=0,
        next="Generate the frozen implementation handoff with `ekzd prompt`.",
    )
    return status


CONTEXT = {"project": {"name": "Demo"}}

class RecordingPersistentTerminal:
    persistent = True
    width = 94

    def __init__(self) -> None:
        self.history: list[str] = []
        self.frames: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def append(self, text: str) -> None:
        self.history.append(text)

    def redraw(self, header: str, actions: str) -> None:
        self.frames.append((header, actions))

    def read_key(self) -> str:
        return "return"


class InteractiveTests(unittest.TestCase):
    def _run(self, status: dict[str, object], inputs) -> tuple[int, str]:
        output = io.StringIO()
        values = iter(inputs)
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=status),
        ):
            code = interactive.run_interactive(ROOT, enabled=False, input_fn=lambda prompt: next(values), output=output)
        return code, output.getvalue()

    def test_intro_state_next_action_and_default_actions_are_clear(self) -> None:
        code, output = self._run(active_status(), ["5"])
        self.assertEqual(0, code)
        self.assertIn("guides a scoped development task", output)
        self.assertIn("project  Demo", output)
        self.assertIn("session  active", output)
        self.assertIn("verification  not run", output)
        self.assertIn("Next", output)
        self.assertIn("Generate implementation prompt", output)
        self.assertIn("Verify changes", output)
        self.assertIn("View task", output)
        self.assertIn("Abort task", output)
        self.assertIn("Exit", output)

    def test_fresh_session_does_not_offer_verification_until_implementation_ready(self) -> None:
        code, output = self._run(fresh_status(), ["4"])
        self.assertEqual(0, code)
        self.assertIn("Generate implementation prompt", output)
        self.assertNotIn("Verify changes", output)

        code, output = self._run(active_status(), ["5"])
        self.assertEqual(0, code)
        self.assertIn("Verify changes", output)

    def test_state_aware_passed_actions_offer_acceptance_not_prompt_or_verify(self) -> None:
        code, output = self._run(active_status("passed"), ["4"])
        self.assertEqual(0, code)
        self.assertIn("Accept verified task", output)
        self.assertNotIn("Generate implementation prompt", output)
        self.assertNotIn("Verify changes", output)

    def test_blocked_state_limits_actions(self) -> None:
        code, output = self._run(active_status("blocked"), ["3"])
        self.assertEqual(0, code)
        self.assertIn("View task", output)
        self.assertIn("Abort task", output)
        self.assertNotIn("Verify changes", output)
        self.assertNotIn("Accept verified task", output)

    def test_invalid_input_is_handled_without_traceback(self) -> None:
        code, output = self._run(active_status(), ["wat", "5"])
        self.assertEqual(0, code)
        self.assertIn("Invalid selection", output)
        self.assertNotIn("Traceback", output)

    def test_eof_exits_cleanly(self) -> None:
        output = io.StringIO()

        def eof(prompt: str) -> str:
            raise EOFError

        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=active_status()),
        ):
            code = interactive.run_interactive(ROOT, enabled=False, input_fn=eof, output=output)
        self.assertEqual(0, code)
        self.assertIn("Exited.", output.getvalue())

    def test_keyboard_interrupt_exits_cleanly(self) -> None:
        output = io.StringIO()

        def interrupt(prompt: str) -> str:
            raise KeyboardInterrupt

        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=active_status()),
        ):
            code = interactive.run_interactive(ROOT, enabled=False, input_fn=interrupt, output=output)
        self.assertEqual(0, code)
        self.assertIn("Exited.", output.getvalue())

    def test_harness_error_uses_error_stream_and_shell_remains_usable(self) -> None:
        output = io.StringIO()
        error_output = io.StringIO()
        values = iter(["1", "5"])
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=active_status()),
            mock.patch.object(
                interactive,
                "build_implementation_prompt",
                side_effect=HarnessError("blocked"),
            ),
        ):
            code = interactive.run_interactive(
                ROOT,
                enabled=False,
                input_fn=lambda prompt: next(values),
                output=output,
                error_output=error_output,
            )
        self.assertEqual(0, code)
        self.assertIn("EkzD: blocked", error_output.getvalue())
        self.assertNotIn("EkzD: blocked", output.getvalue())
        self.assertIn("Exited.", output.getvalue())

    def test_abort_requires_explicit_confirmation(self) -> None:
        output = io.StringIO()
        values = iter(["4", "", "5"])
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=active_status()),
            mock.patch.object(interactive, "abort_session") as abort_session,
        ):
            code = interactive.run_interactive(ROOT, enabled=False, input_fn=lambda prompt: next(values), output=output)
        self.assertEqual(0, code)
        abort_session.assert_not_called()
        self.assertIn("Abort cancelled", output.getvalue())

    def test_acceptance_requires_explicit_confirmation(self) -> None:
        output = io.StringIO()
        values = iter(["2", "", "4"])
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=active_status("passed")),
            mock.patch.object(interactive, "finish_session") as finish_session,
        ):
            code = interactive.run_interactive(ROOT, enabled=False, input_fn=lambda prompt: next(values), output=output)
        self.assertEqual(0, code)
        finish_session.assert_not_called()
        self.assertIn("Acceptance cancelled", output.getvalue())

    def test_confirmed_abort_uses_existing_abort_operation(self) -> None:
        output = io.StringIO()
        statuses = iter(
            [
                active_status(),
                {"session_status": "aborted", "objective": "Change one thing", "next": "Start a new session."},
            ]
        )
        values = iter(["4", "yes", "3"])
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", side_effect=lambda root: next(statuses)),
            mock.patch.object(interactive, "abort_session", return_value={"objective": "Change one thing"}) as abort_session,
        ):
            code = interactive.run_interactive(ROOT, enabled=False, input_fn=lambda prompt: next(values), output=output)
        self.assertEqual(0, code)
        abort_session.assert_called_once_with(ROOT)

    def test_confirmed_acceptance_uses_existing_finish_operation(self) -> None:
        output = io.StringIO()
        statuses = iter(
            [
                active_status("passed"),
                {"session_status": "finished", "objective": "Change one thing", "next": "Start a new session."},
            ]
        )
        values = iter(["2", "yes", "3"])
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", side_effect=lambda root: next(statuses)),
            mock.patch.object(interactive, "finish_session", return_value={"objective": "Change one thing"}) as finish_session,
        ):
            code = interactive.run_interactive(ROOT, enabled=False, input_fn=lambda prompt: next(values), output=output)
        self.assertEqual(0, code)
        finish_session.assert_called_once_with(ROOT, accept=True)

    def test_generate_prompt_and_verify_reuse_existing_operations(self) -> None:
        output = io.StringIO()
        values = iter(["1", "2", "5"])
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=active_status()),
            mock.patch.object(interactive, "build_implementation_prompt", return_value="plain contract\n") as build_prompt,
            mock.patch.object(interactive, "verify_session", return_value={"passed": True, "steps": []}) as verify_session,
        ):
            code = interactive.run_interactive(ROOT, enabled=False, input_fn=lambda prompt: next(values), output=output)
        self.assertEqual(0, code)
        build_prompt.assert_called_once_with(ROOT)
        verify_session.assert_called_once_with(ROOT)
        self.assertIn("plain contract", output.getvalue())

    def test_start_task_uses_existing_start_operation(self) -> None:
        output = io.StringIO()
        statuses = iter(
            [
                {"session_status": "none", "objective": None, "next": "Start a new session."},
                active_status(),
            ]
        )
        values = iter(["1", "Implement one thing", "feat/demo", "5"])
        started = {
            "objective": "Implement one thing",
            "workflow": {"implementation_branch": "feat/demo"},
        }
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", side_effect=lambda root: next(statuses)),
            mock.patch.object(interactive, "start_reproducible_session", return_value=started) as start_session,
        ):
            code = interactive.run_interactive(ROOT, enabled=False, input_fn=lambda prompt: next(values), output=output)
        self.assertEqual(0, code)
        start_session.assert_called_once_with(ROOT, "Implement one thing", implementation_branch="feat/demo")


    def test_persistent_idle_omits_active_intro_and_keeps_idle_actions(self) -> None:
        terminal = RecordingPersistentTerminal()
        status = {"session_status": "none", "objective": None, "next": "Start a new session."}
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=status),
        ):
            code = interactive.run_interactive(
                ROOT,
                enabled=False,
                input_fn=lambda prompt: "2",
                output=io.StringIO(),
                terminal=terminal,
            )

        self.assertEqual(0, code)
        self.assertNotIn(interactive.INTERACTIVE_INTRO, "\n".join(terminal.history))
        self.assertIn("Start task", terminal.frames[-1][1])
        self.assertIn("Exit", terminal.frames[-1][1])
        self.assertNotIn("View task", terminal.frames[-1][1])

    def test_persistent_active_retains_existing_intro(self) -> None:
        terminal = RecordingPersistentTerminal()
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=active_status()),
        ):
            code = interactive.run_interactive(
                ROOT,
                enabled=False,
                input_fn=lambda prompt: "4",
                output=io.StringIO(),
                terminal=terminal,
            )

        self.assertEqual(0, code)
        self.assertIn(interactive.INTERACTIVE_INTRO, "\n".join(terminal.history))

    def test_non_active_state_offers_start_task(self) -> None:
        status = {"session_status": "none", "objective": None, "next": "Start a new session."}
        code, output = self._run(status, ["2"])
        self.assertEqual(0, code)
        self.assertIn("Start task", output)
        self.assertIn("Exit", output)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import os
import unittest
from pathlib import Path
from unittest import mock

from ekzd import interactive
from ekzd.core import HarnessError
from ekzd.terminal import (
    ALT_SCREEN_ENTER,
    ALT_SCREEN_EXIT,
    CLEAR_SCREEN,
    CURSOR_HOME,
    TerminalCapabilities,
    TerminalSession,
    detect_terminal_capabilities,
)
from ekzd.ui import render_terminal_header

ROOT = Path("/repo")
CONTEXT = {"project": {"name": "Demo"}}
STATUS = {
    "session_status": "active",
    "project": "Demo",
    "objective": "Do one thing",
    "verification": "not run",
    "next": "Verify when ready.",
    "baseline_branch": "main",
    "implementation_branch": "feat/demo",
    "current_branch": "feat/demo",
    "baseline_head": "abc",
    "head": "def",
    "worktree_clean": True,
    "commit_count": 1,
    "max_commits": 3,
    "blocked_reason": None,
}
PASSED_STATUS = {**STATUS, "verification": "passed", "next": "Accept when ready."}


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class RecordingTerminal:
    persistent = True
    width = 80

    def __init__(self) -> None:
        self.entered = False
        self.exited = False
        self.frames: list[tuple[str, str, list[str]]] = []
        self.history: list[str] = []

    def __enter__(self) -> "RecordingTerminal":
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.exited = True
        return False

    def append(self, text: str) -> None:
        self.history.append(text)

    def redraw(self, header: str, actions: str) -> None:
        self.frames.append((header, actions, list(self.history)))


class TerminalTests(unittest.TestCase):
    def test_supported_posix_tty_uses_screen_control_and_alt_screen(self) -> None:
        capabilities = detect_terminal_capabilities(
            TtyBuffer(),
            env={"TERM": "xterm-256color"},
            platform_name="posix",
            size=os.terminal_size((100, 30)),
        )
        self.assertTrue(capabilities.screen_control)
        self.assertTrue(capabilities.alternate_screen)

    def test_unsuitable_terminals_fall_back_without_screen_control(self) -> None:
        cases = [
            (TtyBuffer(), {"TERM": "dumb"}, "posix", os.terminal_size((100, 30))),
            (TtyBuffer(), {"TERM": "xterm"}, "posix", os.terminal_size((30, 8))),
            (TtyBuffer(), {"TERM": "xterm"}, "nt", os.terminal_size((100, 30))),
            (io.StringIO(), {"TERM": "xterm"}, "posix", os.terminal_size((100, 30))),
        ]
        for stream, env, platform_name, size in cases:
            with self.subTest(env=env, platform_name=platform_name, size=size):
                capabilities = detect_terminal_capabilities(
                    stream,
                    env=env,
                    platform_name=platform_name,
                    size=size,
                )
                self.assertFalse(capabilities.screen_control)
                self.assertFalse(capabilities.alternate_screen)

    def test_alt_screen_is_restored_after_normal_exit(self) -> None:
        output = io.StringIO()
        capabilities = TerminalCapabilities(True, True, 80, 24)
        with TerminalSession(output, capabilities=capabilities) as session:
            session.append("did something")
            session.redraw("HEADER\n", "ACTIONS\n")
        text = output.getvalue()
        self.assertIn(ALT_SCREEN_ENTER, text)
        self.assertIn(CLEAR_SCREEN + CURSOR_HOME, text)
        self.assertTrue(text.endswith(ALT_SCREEN_EXIT))

    def test_alt_screen_is_restored_after_unexpected_failure(self) -> None:
        output = io.StringIO()
        capabilities = TerminalCapabilities(True, True, 80, 24)
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with TerminalSession(output, capabilities=capabilities):
                raise RuntimeError("boom")
        self.assertTrue(output.getvalue().endswith(ALT_SCREEN_EXIT))

    def test_redraw_keeps_header_at_top_and_recent_activity_visible(self) -> None:
        output = io.StringIO()
        capabilities = TerminalCapabilities(True, True, 80, 10)
        with TerminalSession(output, capabilities=capabilities) as session:
            for index in range(10):
                session.append(f"line {index}")
            session.redraw("EkzD · Demo\nsession active\n", "Actions\n  1. Exit\n")
        last_frame = output.getvalue().split(CLEAR_SCREEN + CURSOR_HOME)[-1]
        self.assertTrue(last_frame.startswith("EkzD · Demo"))
        self.assertIn("line 9", last_frame)
        self.assertIn("Actions", last_frame)

    def test_header_contains_useful_state_without_contract_dump(self) -> None:
        rendered = render_terminal_header("Demo", STATUS, width=100, enabled=False)
        for expected in (
            "EkzD · Demo",
            "session active",
            "verification not run",
            "commits 1/3",
            "task  Do one thing",
            "branch  feat/demo",
            "next  Verify when ready.",
        ):
            self.assertIn(expected, rendered)
        self.assertNotIn("baseline_head", rendered)

    def test_header_plain_mode_has_no_color_style_sequences(self) -> None:
        rendered = render_terminal_header("Demo", STATUS, width=100, enabled=False)
        for code in ("\x1b[31m", "\x1b[32m", "\x1b[33m", "\x1b[36m", "\x1b[1m", "\x1b[2m"):
            self.assertNotIn(code, rendered)


class TerminalInteractiveTests(unittest.TestCase):
    def test_persistent_header_refreshes_after_workflow_state_change(self) -> None:
        terminal = RecordingTerminal()
        values = iter(["2", "4"])
        statuses = iter([STATUS, PASSED_STATUS])
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", side_effect=lambda root: next(statuses)),
            mock.patch.object(interactive, "verify_session", return_value={"passed": True, "steps": []}),
        ):
            code = interactive.run_interactive(
                ROOT,
                enabled=False,
                input_fn=lambda prompt: next(values),
                output=io.StringIO(),
                terminal=terminal,
            )
        self.assertEqual(0, code)
        self.assertTrue(terminal.entered)
        self.assertTrue(terminal.exited)
        self.assertIn("verification not run", terminal.frames[0][0])
        self.assertIn("verification passed", terminal.frames[1][0])
        self.assertTrue(any("Verification" in item for item in terminal.history))

    def test_persistent_prompt_result_stays_concise(self) -> None:
        terminal = RecordingTerminal()
        values = iter(["1", "5"])
        contract = "\n".join(f"line {index}" for index in range(80)) + "\n"
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=STATUS),
            mock.patch.object(interactive, "build_implementation_prompt", return_value=contract) as build_prompt,
        ):
            code = interactive.run_interactive(
                ROOT,
                enabled=False,
                input_fn=lambda prompt: next(values),
                output=io.StringIO(),
                terminal=terminal,
            )
        self.assertEqual(0, code)
        build_prompt.assert_called_once_with(ROOT)
        rendered_history = "\n".join(terminal.history)
        self.assertIn("Implementation prompt ready", rendered_history)
        self.assertIn("Use `ekzd prompt`", rendered_history)
        self.assertNotIn("line 79", rendered_history)

    def test_eof_and_ctrl_c_restore_persistent_terminal(self) -> None:
        for error in (EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                output = io.StringIO()
                terminal = TerminalSession(output, capabilities=TerminalCapabilities(True, True, 80, 24))

                def stop(prompt: str, error=error) -> str:
                    raise error

                with (
                    mock.patch.object(interactive, "build_context", return_value=CONTEXT),
                    mock.patch.object(interactive, "build_workflow_status", return_value=STATUS),
                ):
                    code = interactive.run_interactive(
                        ROOT,
                        enabled=False,
                        input_fn=stop,
                        output=output,
                        terminal=terminal,
                    )
                self.assertEqual(0, code)
                self.assertTrue(output.getvalue().endswith(ALT_SCREEN_EXIT))

    def test_handled_harness_error_keeps_session_usable_and_restores_on_exit(self) -> None:
        output = io.StringIO()
        error_output = io.StringIO()
        terminal = TerminalSession(output, capabilities=TerminalCapabilities(True, True, 80, 24))
        values = iter(["1", "5"])
        with (
            mock.patch.object(interactive, "build_context", return_value=CONTEXT),
            mock.patch.object(interactive, "build_workflow_status", return_value=STATUS),
            mock.patch.object(interactive, "build_implementation_prompt", side_effect=HarnessError("blocked")),
        ):
            code = interactive.run_interactive(
                ROOT,
                enabled=False,
                input_fn=lambda prompt: next(values),
                output=output,
                error_output=error_output,
                terminal=terminal,
            )
        self.assertEqual(0, code)
        self.assertIn("EkzD: blocked", error_output.getvalue())
        self.assertNotIn("EkzD: blocked", output.getvalue())
        self.assertTrue(output.getvalue().endswith(ALT_SCREEN_EXIT))

    def test_unexpected_interactive_failure_restores_alt_screen(self) -> None:
        output = io.StringIO()
        terminal = TerminalSession(output, capabilities=TerminalCapabilities(True, True, 80, 24))
        with mock.patch.object(interactive, "build_context", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                interactive.run_interactive(
                    ROOT,
                    enabled=False,
                    input_fn=lambda prompt: "1",
                    output=output,
                    terminal=terminal,
                )
        self.assertTrue(output.getvalue().endswith(ALT_SCREEN_EXIT))


if __name__ == "__main__":
    unittest.main()

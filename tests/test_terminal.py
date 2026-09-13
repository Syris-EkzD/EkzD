from __future__ import annotations

import io
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from ekzd import interactive, terminal
from ekzd.core import HarnessError
from ekzd.terminal import (
    ALT_SCREEN_ENTER,
    ALT_SCREEN_EXIT,
    ALT_SCROLL_DISABLE,
    ALT_SCROLL_RESTORE,
    ALT_SCROLL_SAVE,
    CLEAR_SCREEN,
    CURSOR_HOME,
    KEY_COPY,
    KEY_DOWN,
    KEY_END,
    KEY_HOME,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    KEY_RETURN,
    KEY_UP,
    TerminalCapabilities,
    TerminalSession,
    _normalize_key,
    copy_text_to_clipboard,
    detect_terminal_capabilities,
)
from ekzd.ui import BOLD, CYAN, GREEN, render_terminal_header, supports_color

ROOT = Path('/repo')
CONTEXT = {'project': {'name': 'Demo'}}
STATUS = {
    'session_status': 'active', 'project': 'Demo', 'objective': 'Do one thing',
    'verification': 'not run', 'next': 'Verify when ready.',
    'baseline_branch': 'main', 'implementation_branch': 'feat/demo',
    'current_branch': 'feat/demo', 'baseline_head': 'abc', 'head': 'def',
    'worktree_clean': True, 'commit_count': 1, 'max_commits': 3,
    'blocked_reason': None,
}
PASSED = {**STATUS, 'verification': 'passed', 'next': 'Accept when ready.'}
BLOCKED = {**STATUS, 'verification': 'blocked', 'next': 'Resolve blocker.'}


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class RecordingTerminal:
    persistent = True
    width = 80
    height = 24

    def __init__(self) -> None:
        self.entered = False
        self.exited = False
        self.frames: list[tuple[str, str]] = []
        self.docs: list[tuple[str, str, int, str | None]] = []
        self.history: list[str] = []

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exited = True
        return False

    def append(self, text: str) -> None:
        self.history.append(text)

    def set_feedback(self, text: str) -> None:
        self.history = [text]

    def redraw(self, header: str, actions: str) -> None:
        self.frames.append((header, actions))

    def redraw_document(self, title: str, text: str, offset: int, *, feedback: str | None = None):
        self.docs.append((title, text, offset, feedback))
        lines = text.splitlines() or ['']
        page = 3
        offset = min(max(0, offset), max(0, len(lines) - page))
        return offset, page, len(lines)

    def read_key(self) -> str:
        return KEY_RETURN


class PlainRecordingTerminal(RecordingTerminal):
    persistent = False


class TerminalLifecycleTests(unittest.TestCase):
    def test_capability_threshold_and_fallback(self) -> None:
        supported = detect_terminal_capabilities(
            TtyBuffer(), env={'TERM': 'xterm-256color'}, platform_name='posix',
            size=os.terminal_size((100, 30)),
        )
        self.assertTrue(supported.alternate_screen)
        for size in (os.terminal_size((40, 10)), os.terminal_size((80, 12))):
            with self.subTest(size=size):
                caps = detect_terminal_capabilities(
                    TtyBuffer(), env={'TERM': 'xterm-256color'}, platform_name='posix', size=size,
                )
                self.assertFalse(caps.screen_control)

    def test_alt_screen_and_mouse_scroll_mode_restore_symmetrically(self) -> None:
        out = io.StringIO()
        with TerminalSession(out, capabilities=TerminalCapabilities(True, True, 94, 46)):
            pass
        text = out.getvalue()
        self.assertLess(text.index(ALT_SCROLL_SAVE), text.index(ALT_SCROLL_DISABLE))
        self.assertLess(text.index(ALT_SCROLL_DISABLE), text.index(ALT_SCREEN_ENTER))
        self.assertLess(text.index(ALT_SCROLL_RESTORE), text.index(ALT_SCREEN_EXIT))
        self.assertTrue(text.endswith(ALT_SCREEN_EXIT))

    def test_unexpected_failure_restores_terminal(self) -> None:
        out = io.StringIO()
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            with TerminalSession(out, capabilities=TerminalCapabilities(True, True, 80, 24)):
                raise RuntimeError('boom')
        self.assertIn(ALT_SCROLL_RESTORE, out.getvalue())
        self.assertTrue(out.getvalue().endswith(ALT_SCREEN_EXIT))

    def test_set_feedback_replaces_history_instead_of_accumulating_transcript(self) -> None:
        session = TerminalSession(io.StringIO(), capabilities=TerminalCapabilities(True, False, 94, 46))
        session.append('older summary\nsecond line\n')
        session.set_feedback('✓ latest result\n')
        self.assertEqual(['✓ latest result'], session._history)

    def test_prompt_document_redraw_never_leaves_alternate_screen(self) -> None:
        out = io.StringIO()
        with TerminalSession(out, capabilities=TerminalCapabilities(True, True, 72, 18)) as session:
            before = out.getvalue()
            first, page, total = session.redraw_document('Implementation Prompt', 'one\ntwo\nthree\nfour\n', 0)
            session.redraw_document('Implementation Prompt', 'one\ntwo\nthree\nfour\n', page, feedback='✓ copied')
            during = out.getvalue()[len(before):]
            self.assertEqual(0, first)
            self.assertGreater(page, 0)
            self.assertEqual(4, total)
            self.assertIn(CLEAR_SCREEN + CURSOR_HOME, during)
            self.assertIn('C copy all', during)
            self.assertIn('✓ copied', during)
            self.assertNotIn(ALT_SCREEN_EXIT, during)
            self.assertNotIn(ALT_SCROLL_RESTORE, during)
        self.assertTrue(out.getvalue().endswith(ALT_SCREEN_EXIT))

    def test_prompt_navigation_key_sequences(self) -> None:
        cases = {
            '\x1b[A': KEY_UP, '\x1b[B': KEY_DOWN,
            '\x1b[5~': KEY_PAGE_UP, '\x1b[6~': KEY_PAGE_DOWN,
            '\x1b[H': KEY_HOME, '\x1b[F': KEY_END,
            'c': KEY_COPY, 'C': KEY_COPY,
            '\r': KEY_RETURN, 'q': KEY_RETURN, '\x1b': KEY_RETURN,
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, _normalize_key(raw))


class TerminalClipboardTests(unittest.TestCase):
    def test_wayland_prefers_wl_copy_and_passes_exact_utf8_stdin_without_shell(self) -> None:
        completed = subprocess.CompletedProcess(['/usr/bin/wl-copy'], 0)
        paths = {'wl-copy': '/usr/bin/wl-copy', 'xclip': '/usr/bin/xclip', 'xsel': '/usr/bin/xsel'}
        with (
            mock.patch.object(terminal.shutil, 'which', side_effect=lambda name: paths.get(name)),
            mock.patch.object(terminal.subprocess, 'run', return_value=completed) as run,
        ):
            self.assertTrue(copy_text_to_clipboard('α\n', env={'WAYLAND_DISPLAY': 'wayland-0', 'DISPLAY': ':0'}))
        args, kwargs = run.call_args
        self.assertEqual(['/usr/bin/wl-copy'], args[0])
        self.assertEqual('α\n'.encode('utf-8'), kwargs['input'])
        self.assertIs(False, kwargs['shell'])

    def test_x11_falls_back_from_xclip_to_xsel(self) -> None:
        results = [
            subprocess.CompletedProcess(['/usr/bin/xclip'], 1),
            subprocess.CompletedProcess(['/usr/bin/xsel'], 0),
        ]
        paths = {'xclip': '/usr/bin/xclip', 'xsel': '/usr/bin/xsel'}
        with (
            mock.patch.object(terminal.shutil, 'which', side_effect=lambda name: paths.get(name)),
            mock.patch.object(terminal.subprocess, 'run', side_effect=results) as run,
        ):
            self.assertTrue(copy_text_to_clipboard('contract', env={'DISPLAY': ':0'}))
        self.assertEqual(['/usr/bin/xclip', '-selection', 'clipboard'], run.call_args_list[0].args[0])
        self.assertEqual(['/usr/bin/xsel', '--clipboard', '--input'], run.call_args_list[1].args[0])
        self.assertTrue(all(call.kwargs['shell'] is False for call in run.call_args_list))

    def test_clipboard_unavailable_when_no_backend_succeeds(self) -> None:
        with mock.patch.object(terminal.shutil, 'which', return_value=None):
            self.assertFalse(copy_text_to_clipboard('contract', env={'WAYLAND_DISPLAY': 'wayland-0', 'DISPLAY': ':0'}))


class TerminalHeaderTests(unittest.TestCase):
    def test_header_has_separate_rows_and_semantic_color(self) -> None:
        plain = render_terminal_header('Demo', STATUS, width=100, enabled=False)
        lines = plain.rstrip('\n').splitlines()
        self.assertTrue(lines[0].startswith('┌─ EkzD '))
        for label in ('Project', 'Task', 'Branch', 'Session', 'Verification', 'Commits', 'Next'):
            self.assertEqual(1, len([line for line in lines if line.startswith(f'│ {label}')]))
        colored = render_terminal_header('Demo', PASSED, width=100, enabled=True)
        self.assertIn(BOLD + CYAN + 'Project', colored)
        self.assertIn(GREEN + 'active', colored)
        self.assertIn(GREEN + 'passed', colored)

    def test_header_clips_long_values_and_no_color_is_plain(self) -> None:
        width = 94
        long_status = {**STATUS, 'objective': 'long ' * 100, 'next': 'next ' * 100}
        rendered = render_terminal_header('Demo', long_status, width=width, enabled=False)
        self.assertTrue(all(len(line) == width - 1 for line in rendered.rstrip('\n').splitlines()))
        self.assertIn('…', rendered)
        with mock.patch.dict(os.environ, {'NO_COLOR': '1'}, clear=True):
            enabled = supports_color(TtyBuffer())
        self.assertFalse(enabled)
        self.assertNotIn('\x1b[', render_terminal_header('Demo', STATUS, width=100, enabled=enabled))


class TerminalInteractiveTests(unittest.TestCase):
    def test_persistent_actions_remove_view_task_but_fallback_keeps_it(self) -> None:
        for status in (STATUS, BLOCKED, PASSED):
            persistent = interactive._interactive_actions(status, persistent=True)
            self.assertNotIn(('view', 'View task'), persistent)
        fallback = interactive._interactive_actions(STATUS, persistent=False)
        self.assertIn(('view', 'View task'), fallback)

    def test_persistent_prompt_uses_exact_contract_pages_copies_all_and_returns_home(self) -> None:
        terminal_session = RecordingTerminal()
        values = iter(['1', '4'])
        keys = iter([KEY_PAGE_DOWN, KEY_COPY, KEY_RETURN])
        contract = '\n'.join(f'line {i}' for i in range(20)) + '\n'
        copied: list[str] = []
        with (
            mock.patch.object(interactive, 'build_context', return_value=CONTEXT),
            mock.patch.object(interactive, 'build_workflow_status', return_value=STATUS),
            mock.patch.object(interactive, 'build_implementation_prompt', return_value=contract) as build_prompt,
            mock.patch.object(interactive, 'copy_text_to_clipboard', side_effect=lambda value: copied.append(value) or True),
        ):
            code = interactive.run_interactive(
                ROOT, enabled=False, input_fn=lambda _: next(values), output=io.StringIO(),
                terminal=terminal_session, prompt_key_reader=lambda: next(keys),
            )
        self.assertEqual(0, code)
        build_prompt.assert_called_once_with(ROOT)
        self.assertEqual([contract], copied)
        self.assertEqual([0, 3, 3], [frame[2] for frame in terminal_session.docs])
        self.assertEqual('✓ Implementation prompt copied to clipboard', terminal_session.docs[-1][3])
        self.assertGreaterEqual(len(terminal_session.frames), 2)

    def test_prompt_view_supports_line_page_home_end_navigation(self) -> None:
        terminal_session = RecordingTerminal()
        keys = iter([KEY_DOWN, KEY_PAGE_DOWN, KEY_END, KEY_HOME, KEY_UP, KEY_RETURN])
        interactive._show_prompt_view(terminal_session, '\n'.join(f'line {i}' for i in range(20)), lambda: next(keys))
        self.assertEqual([0, 1, 4, 17, 0, 0], [frame[2] for frame in terminal_session.docs])

    def test_copy_preserves_view_position_and_failure_is_visible_nonfatal(self) -> None:
        terminal_session = RecordingTerminal()
        prompt = '\n'.join(f'line {i}' for i in range(20)) + '\n'
        copied: list[str] = []
        keys = iter([KEY_PAGE_DOWN, KEY_COPY, KEY_DOWN, KEY_COPY, KEY_RETURN])
        results = iter([True, False])

        def copy(value: str) -> bool:
            copied.append(value)
            return next(results)

        interactive._show_prompt_view(terminal_session, prompt, lambda: next(keys), copy)
        self.assertEqual([prompt, prompt], copied)
        self.assertEqual([0, 3, 3, 4, 4], [frame[2] for frame in terminal_session.docs])
        self.assertEqual('✓ Implementation prompt copied to clipboard', terminal_session.docs[2][3])
        self.assertEqual('! Clipboard unavailable. Use `ekzd prompt` for raw output.', terminal_session.docs[4][3])

    def test_prompt_navigation_does_not_consume_menu_input(self) -> None:
        terminal_session = RecordingTerminal()
        menu_seen: list[str] = []
        values = iter(['1', '4'])
        keys = iter([KEY_DOWN, KEY_RETURN])

        def menu(prompt: str) -> str:
            value = next(values)
            menu_seen.append(value)
            return value

        with (
            mock.patch.object(interactive, 'build_context', return_value=CONTEXT),
            mock.patch.object(interactive, 'build_workflow_status', return_value=STATUS),
            mock.patch.object(interactive, 'build_implementation_prompt', return_value='a\nb\nc\nd\n'),
        ):
            self.assertEqual(0, interactive.run_interactive(
                ROOT, enabled=False, input_fn=menu, output=io.StringIO(), terminal=terminal_session,
                prompt_key_reader=lambda: next(keys),
            ))
        self.assertEqual(['1', '4'], menu_seen)

    def test_fallback_view_task_remains_normal_status_output(self) -> None:
        terminal_session = PlainRecordingTerminal()
        out = io.StringIO()
        values = iter(['3', '5'])
        with (
            mock.patch.object(interactive, 'build_context', return_value=CONTEXT),
            mock.patch.object(interactive, 'build_workflow_status', return_value=STATUS),
        ):
            self.assertEqual(0, interactive.run_interactive(
                ROOT, enabled=False, input_fn=lambda _: next(values), output=out, terminal=terminal_session,
            ))
        self.assertIn('EkzD · status', out.getvalue())
        self.assertIn('project  Demo', out.getvalue())

    def test_prompt_view_interrupt_and_unexpected_failure_cleanup(self) -> None:
        for error, expected_code in ((KeyboardInterrupt(), 0), (EOFError(), 0)):
            out = io.StringIO()
            terminal_session = TerminalSession(out, capabilities=TerminalCapabilities(True, True, 94, 46))
            with (
                mock.patch.object(interactive, 'build_context', return_value=CONTEXT),
                mock.patch.object(interactive, 'build_workflow_status', return_value=STATUS),
                mock.patch.object(interactive, 'build_implementation_prompt', return_value='contract\n'),
            ):
                code = interactive.run_interactive(
                    ROOT, enabled=False, input_fn=lambda _: '1', output=out, terminal=terminal_session,
                    prompt_key_reader=mock.Mock(side_effect=error),
                )
            self.assertEqual(expected_code, code)
            self.assertIn(ALT_SCROLL_RESTORE, out.getvalue())
            self.assertTrue(out.getvalue().endswith(ALT_SCREEN_EXIT))

        out = io.StringIO()
        terminal_session = TerminalSession(out, capabilities=TerminalCapabilities(True, True, 94, 46))
        with (
            mock.patch.object(interactive, 'build_context', return_value=CONTEXT),
            mock.patch.object(interactive, 'build_workflow_status', return_value=STATUS),
            mock.patch.object(interactive, 'build_implementation_prompt', return_value='contract\n'),
            self.assertRaisesRegex(RuntimeError, 'boom'),
        ):
            interactive.run_interactive(
                ROOT, enabled=False, input_fn=lambda _: '1', output=out, terminal=terminal_session,
                prompt_key_reader=mock.Mock(side_effect=RuntimeError('boom')),
            )
        self.assertIn(ALT_SCROLL_RESTORE, out.getvalue())
        self.assertTrue(out.getvalue().endswith(ALT_SCREEN_EXIT))

    def test_handled_harness_error_stays_visible_and_shell_usable(self) -> None:
        terminal_session = RecordingTerminal()
        err = io.StringIO()
        values = iter(['1', '4'])
        with (
            mock.patch.object(interactive, 'build_context', return_value=CONTEXT),
            mock.patch.object(interactive, 'build_workflow_status', return_value=STATUS),
            mock.patch.object(interactive, 'build_implementation_prompt', side_effect=HarnessError('blocked')),
        ):
            code = interactive.run_interactive(
                ROOT, enabled=False, input_fn=lambda _: next(values), output=io.StringIO(),
                error_output=err, terminal=terminal_session,
            )
        self.assertEqual(0, code)
        self.assertIn('EkzD: blocked', err.getvalue())
        self.assertTrue(any('EkzD: blocked' in line for line in terminal_session.history))


class TerminalDocumentationTests(unittest.TestCase):
    def test_readme_documents_copy_control(self) -> None:
        readme = (Path(__file__).resolve().parents[1] / 'README.md').read_text(encoding='utf-8')
        self.assertIn('`c` or `C` to copy the complete exact contract', readme)
        self.assertIn('explicit `ekzd prompt` command remains deterministic raw plain text', readme)

    def test_readme_documents_contract_review_and_concise_feedback(self) -> None:
        readme = (Path(__file__).resolve().parents[1] / 'README.md').read_text(encoding='utf-8')
        self.assertIn('explicit review of the currently committed `.ekzd/project.toml` contract', readme)
        self.assertIn('confirm and start, update the contract first, or cancel', readme)
        self.assertIn('bordered secondary **Previous task** card', readme)
        self.assertIn('recent-feedback area', readme)
        self.assertIn('explicit `ekzd start` keeps its existing command contract', readme)


if __name__ == '__main__':
    unittest.main()

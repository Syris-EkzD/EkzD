from __future__ import annotations

import io
import os
import unittest
from unittest import mock

from ekzd.ui import RED, RESET, render_context_ui, success, supports_color


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

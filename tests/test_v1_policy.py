from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError, abort_session, load_config, start_session, verify_session


CONFIG = """schema_version = 1

[project]
name = "demo"

[sources]
paths = ["README.md"]

[scope]
include = ["src/"]
exclude = []
constraints = ["Keep work scoped."]

[authority]
may = ["Edit scoped source files."]
requires_approval = ["Finish the session."]
may_not = ["Expand scope silently."]

[session]
max_commits = 3

[acceptance]
criteria = ["Configured verification passes."]

[[verification.steps]]
name = "syntax"
command = ["python3", "-c", "print('ok')"]
cwd = "."
timeout_seconds = 30
"""


class V1PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src/app.py").write_text("VALUE = 0\n", encoding="utf-8")
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        config.write_text(CONFIG, encoding="utf-8")
        (self.root / ".gitignore").write_text(".ekzd/session.json\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.root, check=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit_app_change(self, value: int) -> None:
        (self.root / "src/app.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/app.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", f"change app {value}"], cwd=self.root, check=True)

    def test_verify_rejects_config_changed_after_session_start(self) -> None:
        start_session(self.root, "Keep contract fixed")
        config = self.root / ".ekzd/project.toml"
        config.write_text(CONFIG.replace('include = ["src/"]', 'include = ["*"]'), encoding="utf-8")

        with self.assertRaisesRegex(HarnessError, "configuration changed after the session started"):
            verify_session(self.root)

    def test_abort_recovers_over_budget_session_without_acceptance(self) -> None:
        start_session(self.root, "Bounded task")
        for value in range(1, 5):
            self.commit_app_change(value)

        with self.assertRaisesRegex(HarnessError, "commit budget exceeded"):
            verify_session(self.root)

        aborted = abort_session(self.root)
        self.assertEqual("aborted", aborted["status"])
        self.assertIsNone(aborted["acceptance"])

        restarted = start_session(self.root, "Fresh bounded task")
        self.assertEqual("active", restarted["status"])

    def test_ready_config_requires_explicit_scope_include(self) -> None:
        config = self.root / ".ekzd/project.toml"
        config.write_text(CONFIG.replace('include = ["src/"]', "include = []"), encoding="utf-8")

        with self.assertRaisesRegex(HarnessError, "scope.include must contain at least one entry"):
            load_config(self.root)


if __name__ == "__main__":
    unittest.main()

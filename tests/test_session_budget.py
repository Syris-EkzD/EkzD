from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError, load_config, verify_session
from ekzd.workflow import start_reproducible_session


CONFIG = """schema_version = 1

[project]
name = "demo"

[sources]
paths = ["README.md"]

[scope]
include = ["src/"]
exclude = []
constraints = ["Keep the session bounded."]

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


class SessionBudgetTests(unittest.TestCase):
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

    def test_explicit_smaller_budget_blocks_fourth_session_commit(self) -> None:
        start_reproducible_session(self.root, "Bounded task", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)
        for value in range(1, 5):
            self.commit_app_change(value)

        with self.assertRaisesRegex(HarnessError, "commit budget exceeded"):
            verify_session(self.root)

    def test_explicit_budgets_from_one_through_twenty_are_valid(self) -> None:
        config = self.root / ".ekzd/project.toml"
        for value in (1, 2, 5, 20):
            with self.subTest(value=value):
                config.write_text(CONFIG.replace("max_commits = 3", f"max_commits = {value}"), encoding="utf-8")
                self.assertEqual(value, load_config(self.root)["session"]["max_commits"])

    def test_twenty_commit_budget_allows_twentieth_and_blocks_twenty_first(self) -> None:
        config = self.root / ".ekzd/project.toml"
        config.write_text(CONFIG.replace("max_commits = 3", "max_commits = 20"), encoding="utf-8")
        subprocess.run(["git", "add", ".ekzd/project.toml"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "configure session budget"], cwd=self.root, check=True)
        start_reproducible_session(self.root, "Bounded task", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)
        for value in range(1, 21):
            self.commit_app_change(value)

        verification = verify_session(self.root)
        self.assertTrue(verification["passed"])
        self.assertEqual({"commit_count": 20, "max_commits": 20}, verification["session_budget"])

        self.commit_app_change(21)
        with self.assertRaisesRegex(HarnessError, "commit budget exceeded"):
            verify_session(self.root)

    def test_invalid_budget_values_are_rejected(self) -> None:
        config = self.root / ".ekzd/project.toml"
        invalid = ("0", "-1", "true", '"5"', "21")
        for value in invalid:
            with self.subTest(value=value):
                config.write_text(CONFIG.replace("max_commits = 3", f"max_commits = {value}"), encoding="utf-8")
                with self.assertRaisesRegex(HarnessError, "integer between 1 and 20"):
                    load_config(self.root)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError, finish_session, start_session, verify_session


CONFIG = """schema_version = 1

[project]
name = "demo"

[sources]
paths = ["README.md"]

[scope]
include = ["src/", "README.md"]
exclude = ["src/generated/"]
constraints = ["Avoid unrelated changes."]

[authority]
may = ["Edit scoped files."]
requires_approval = ["Finish the session."]
may_not = ["Edit generated files."]

[acceptance]
criteria = [
  "Verification passes.",
  "No state changes occur after verification.",
]

[[verification.steps]]
name = "syntax"
command = ["python3", "-c", "print('ok')"]
cwd = "."
timeout_seconds = 30
"""


class AcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        config.write_text(CONFIG, encoding="utf-8")
        (self.root / ".gitignore").write_text(".ekzd/session.json\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.root, check=True)
        start_session(self.root, "Change app")
        (self.root / "src/app.py").write_text("VALUE = 2\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_finish_requires_explicit_acceptance(self) -> None:
        verification = verify_session(self.root)
        self.assertTrue(verification["passed"])
        with self.assertRaises(HarnessError):
            finish_session(self.root, accept=False)

    def test_finish_requires_unchanged_verified_state(self) -> None:
        verify_session(self.root)
        (self.root / "README.md").write_text("# Changed after verification\n", encoding="utf-8")
        with self.assertRaises(HarnessError):
            finish_session(self.root, accept=True)

    def test_finish_blocks_same_file_content_mutation(self) -> None:
        verify_session(self.root)
        (self.root / "src/app.py").write_text("VALUE = 3\n", encoding="utf-8")

        with self.assertRaises(HarnessError):
            finish_session(self.root, accept=True)

    def test_finish_blocks_untracked_file_content_mutation(self) -> None:
        untracked = self.root / "src/new.py"
        untracked.write_text("VALUE = 1\n", encoding="utf-8")
        verify_session(self.root)
        untracked.write_text("VALUE = 2\n", encoding="utf-8")

        with self.assertRaises(HarnessError):
            finish_session(self.root, accept=True)

    def test_finish_accepts_exact_verified_state(self) -> None:
        verify_session(self.root)
        state = finish_session(self.root, accept=True)
        self.assertEqual("finished", state["status"])
        self.assertTrue(state["acceptance"]["verification_bound"])

    def test_verification_blocks_out_of_scope_change(self) -> None:
        (self.root / "notes.txt").write_text("outside scope\n", encoding="utf-8")
        with self.assertRaises(HarnessError):
            verify_session(self.root)

    def test_verification_blocks_excluded_path(self) -> None:
        generated = self.root / "src/generated"
        generated.mkdir()
        (generated / "output.py").write_text("x = 1\n", encoding="utf-8")
        with self.assertRaises(HarnessError):
            verify_session(self.root)

    def test_verification_blocks_committed_out_of_scope_change(self) -> None:
        (self.root / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "notes.txt").write_text("outside scope\n", encoding="utf-8")
        subprocess.run(["git", "add", "notes.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "add notes"], cwd=self.root, check=True)

        with self.assertRaises(HarnessError):
            verify_session(self.root)

    def test_verification_allows_committed_in_scope_change(self) -> None:
        subprocess.run(["git", "add", "src/app.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "change app"], cwd=self.root, check=True)

        verification = verify_session(self.root)

        self.assertTrue(verification["passed"])
        self.assertIn("src/app.py", verification["changed_paths"])


if __name__ == "__main__":
    unittest.main()

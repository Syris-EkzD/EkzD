from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError, start_session, verify_session


CONFIG = """schema_version = 1

[project]
name = "demo"

[sources]
paths = ["README.md"]

[scope]
include = ["README.md"]
exclude = []
constraints = ["Keep work limited to the README."]

[authority]
may = ["Edit README.md."]
requires_approval = []
may_not = ["Edit unrelated files."]

[acceptance]
criteria = ["Configured verification passes without leaving out-of-scope changes."]

[[verification.steps]]
name = "mutating-check"
command = ["python3", "-c", "from pathlib import Path; Path('notes.txt').write_text('generated', encoding='utf-8')"]
cwd = "."
timeout_seconds = 30
"""


class VerificationSideEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        config.write_text(CONFIG, encoding="utf-8")
        (self.root / ".gitignore").write_text(".ekzd/session.json\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.root, check=True)
        start_session(self.root, "Update README")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_verification_rechecks_scope_after_commands(self) -> None:
        with self.assertRaisesRegex(HarnessError, "notes.txt: outside scope.include"):
            verify_session(self.root)


if __name__ == "__main__":
    unittest.main()

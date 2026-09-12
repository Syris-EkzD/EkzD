from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError, build_context, init_project, load_config, render_context, start_session


VALID_CONFIG = """schema_version = 1

[project]
name = \"demo\"

[sources]
paths = [\"README.md\"]

[scope]
include = [\"src/\"]
exclude = [\"vendor/\"]
constraints = [\"Avoid unrelated changes.\"]

[authority]
may = [\"Edit source and tests.\"]
requires_approval = [\"Merge to main.\"]
may_not = [\"Change secrets.\"]

[acceptance]
criteria = [\"Configured verification passes.\"]

[[verification.steps]]
name = \"syntax\"
command = [\"python3\", \"-c\", \"print('ok')\"]
cwd = \".\"
timeout_seconds = 30
"""


class EkzDCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.root, check=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_init_creates_template_and_ignores_session(self) -> None:
        init_project(self.root, "Demo")
        self.assertTrue((self.root / ".ekzd/project.toml").is_file())
        self.assertIn(".ekzd/session.json", (self.root / ".gitignore").read_text(encoding="utf-8"))

    def test_start_requires_ready_acceptance_and_verification(self) -> None:
        init_project(self.root)
        with self.assertRaises(HarnessError):
            start_session(self.root, "Do work")

    def test_start_and_context_use_valid_config(self) -> None:
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        config.write_text(VALID_CONFIG, encoding="utf-8")
        loaded = load_config(self.root)
        self.assertEqual("demo", loaded["project"]["name"])

        state = start_session(self.root, "Implement one bounded change")
        self.assertEqual("active", state["status"])

        context = build_context(self.root)
        self.assertEqual("Implement one bounded change", context["objective"])
        self.assertEqual("demo", context["project"]["name"])

        rendered = render_context(context)
        self.assertIn("## Verification", rendered)
        self.assertIn('"name": "syntax"', rendered)

    def test_rejects_source_outside_project(self) -> None:
        config = VALID_CONFIG.replace('paths = [\"README.md\"]', 'paths = [\"../secret.txt\"]')
        path = self.root / ".ekzd/project.toml"
        path.parent.mkdir()
        path.write_text(config, encoding="utf-8")
        with self.assertRaises(HarnessError):
            load_config(self.root)


if __name__ == "__main__":
    unittest.main()

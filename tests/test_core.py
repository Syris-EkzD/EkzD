from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError, build_context, init_project, load_config, read_state, render_context, start_session


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

    def _commit_valid_config(self) -> Path:
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir(exist_ok=True)
        config.write_text(VALID_CONFIG, encoding="utf-8")
        subprocess.run(["git", "add", ".ekzd/project.toml"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "add EkzD contract"], cwd=self.root, check=True)
        return config

    def test_init_creates_template_and_ignores_session(self) -> None:
        init_project(self.root, "Demo")
        self.assertTrue((self.root / ".ekzd/project.toml").is_file())
        self.assertIn("max_commits = 20", (self.root / ".ekzd/project.toml").read_text(encoding="utf-8"))
        self.assertIn(".ekzd/session.json", (self.root / ".gitignore").read_text(encoding="utf-8"))

    def test_missing_session_budget_defaults_to_twenty(self) -> None:
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        config.write_text(VALID_CONFIG, encoding="utf-8")

        loaded = load_config(self.root)

        self.assertEqual(20, loaded["session"]["max_commits"])

    def test_start_requires_ready_acceptance_and_verification(self) -> None:
        init_project(self.root)
        with self.assertRaises(HarnessError):
            start_session(self.root, "Do work")

    def test_start_requires_committed_clean_project_config(self) -> None:
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        config.write_text(VALID_CONFIG, encoding="utf-8")

        with self.assertRaisesRegex(HarnessError, "must be committed"):
            start_session(self.root, "Do work")

        subprocess.run(["git", "add", ".ekzd/project.toml"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "add EkzD contract"], cwd=self.root, check=True)
        config.write_text(VALID_CONFIG + "\n# dirty\n", encoding="utf-8")

        with self.assertRaisesRegex(HarnessError, "must remain clean"):
            start_session(self.root, "Do work")

    def test_start_rejects_symlinked_project_config(self) -> None:
        target = self.root / "local-contract.toml"
        target.write_text(VALID_CONFIG, encoding="utf-8")
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        config.symlink_to(Path("../local-contract.toml"))
        subprocess.run(["git", "add", ".ekzd/project.toml"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "add symlinked EkzD contract"], cwd=self.root, check=True)

        with self.assertRaisesRegex(HarnessError, "regular tracked file"):
            start_session(self.root, "Do work")

    def test_start_rejects_filtered_project_config(self) -> None:
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        clean_filter = self.root / "clean_filter.py"
        smudge_filter = self.root / "smudge_filter.py"
        attributes = self.root / ".gitattributes"
        clean_filter.write_text(
            "import sys\ntext = sys.stdin.read()\nsys.stdout.write(text.replace('include = [\\\"*\\\"]', 'include = [\\\"src/\\\"]'))\n",
            encoding="utf-8",
        )
        smudge_filter.write_text(
            "import sys\ntext = sys.stdin.read()\nsys.stdout.write(text.replace('include = [\\\"src/\\\"]', 'include = [\\\"*\\\"]'))\n",
            encoding="utf-8",
        )
        attributes.write_text(".ekzd/project.toml filter=ekzd-contract\n", encoding="utf-8")
        subprocess.run(["git", "config", "filter.ekzd-contract.clean", "python3 clean_filter.py"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "filter.ekzd-contract.smudge", "python3 smudge_filter.py"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "filter.ekzd-contract.required", "true"], cwd=self.root, check=True)

        config.write_text(VALID_CONFIG.replace('include = [\"src/\"]', 'include = [\"*\"]'), encoding="utf-8")
        subprocess.run(
            ["git", "add", ".gitattributes", "clean_filter.py", "smudge_filter.py", ".ekzd/project.toml"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(["git", "commit", "-qm", "add filtered EkzD contract"], cwd=self.root, check=True)

        self.assertEqual(
            "",
            subprocess.run(
                ["git", "diff", "--name-only", "--", ".ekzd/project.toml"],
                cwd=self.root,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip(),
        )
        with self.assertRaisesRegex(HarnessError, "Git content filters are unsupported"):
            start_session(self.root, "Reject filtered checkout state")

    def test_start_and_context_use_valid_config(self) -> None:
        config = self.root / ".ekzd/project.toml"
        config.parent.mkdir()
        config.write_text(VALID_CONFIG, encoding="utf-8")
        loaded = load_config(self.root)
        self.assertEqual("demo", loaded["project"]["name"])
        subprocess.run(["git", "add", ".ekzd/project.toml"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "add EkzD contract"], cwd=self.root, check=True)

        state = start_session(self.root, "Implement one bounded change")
        self.assertEqual("active", state["status"])

        context = build_context(self.root)
        self.assertEqual("Implement one bounded change", context["objective"])
        self.assertEqual("demo", context["project"]["name"])

        rendered = render_context(context)
        self.assertIn("## Verification", rendered)
        self.assertIn('"name": "syntax"', rendered)

    def test_new_session_state_does_not_initialize_handoff_notes(self) -> None:
        self._commit_valid_config()
        state = start_session(self.root, "Use explicit commands")
        self.assertNotIn("handoff", state)
        persisted = json.loads((self.root / ".ekzd/session.json").read_text(encoding="utf-8"))
        self.assertNotIn("handoff", persisted)

    def test_legacy_session_state_with_handoff_field_remains_readable(self) -> None:
        state_path = self.root / ".ekzd/session.json"
        state_path.parent.mkdir()
        legacy = {
            "schema_version": 1,
            "status": "aborted",
            "objective": "Historical task",
            "handoff": {"done": ["old note"], "next": ["old next"]},
        }
        state_path.write_text(json.dumps(legacy), encoding="utf-8")
        self.assertEqual(legacy, read_state(self.root))

    def test_rejects_source_outside_project(self) -> None:
        config = VALID_CONFIG.replace('paths = [\"README.md\"]', 'paths = [\"../secret.txt\"]')
        path = self.root / ".ekzd/project.toml"
        path.parent.mkdir()
        path.write_text(config, encoding="utf-8")
        with self.assertRaises(HarnessError):
            load_config(self.root)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError, abort_session, start_session, verify_session


def config_text() -> str:
    return '''schema_version = 1

[project]
name = "demo"

[sources]
paths = ["README.md"]

[scope]
include = ["README.md"]
exclude = []
constraints = ["Keep work scoped."]

[authority]
may = ["Edit README.md."]
requires_approval = ["Finish the session."]
may_not = ["Edit secret.txt."]

[session]
max_commits = 3

[acceptance]
criteria = ["Configured verification passes."]

[[verification.steps]]
name = "check"
command = ["python3", "-c", "print('ok')"]
cwd = "."
timeout_seconds = 30
'''


class ScopeHistoryVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / "secret.txt").write_text("original\n", encoding="utf-8")
        (self.root / ".ekzd").mkdir()
        (self.root / ".ekzd/project.toml").write_text(config_text(), encoding="utf-8")
        (self.root / ".gitignore").write_text(".ekzd/session.json\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.root, check=True)
        start_session(self.root, "Keep work inside README scope")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_assume_unchanged_path_is_rejected(self) -> None:
        subprocess.run(["git", "update-index", "--assume-unchanged", "secret.txt"], cwd=self.root, check=True)
        (self.root / "secret.txt").write_text("hidden change\n", encoding="utf-8")

        with self.assertRaisesRegex(HarnessError, "assume-unchanged/skip-worktree"):
            verify_session(self.root)

    def test_skip_worktree_path_is_rejected(self) -> None:
        subprocess.run(["git", "update-index", "--skip-worktree", "secret.txt"], cwd=self.root, check=True)
        (self.root / "secret.txt").write_text("hidden change\n", encoding="utf-8")

        with self.assertRaisesRegex(HarnessError, "assume-unchanged/skip-worktree"):
            verify_session(self.root)

    def test_reverted_out_of_scope_commit_still_counts_for_scope(self) -> None:
        (self.root / "secret.txt").write_text("forbidden\n", encoding="utf-8")
        subprocess.run(["git", "add", "secret.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "touch forbidden path"], cwd=self.root, check=True)

        (self.root / "secret.txt").write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "add", "secret.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "restore forbidden path"], cwd=self.root, check=True)

        self.assertEqual("", subprocess.run(["git", "diff", "HEAD~2", "HEAD", "--", "secret.txt"], cwd=self.root, text=True, capture_output=True, check=True).stdout)
        with self.assertRaisesRegex(HarnessError, "secret.txt: outside scope.include"):
            verify_session(self.root)

    def test_session_state_cannot_enter_session_history_even_if_untracked_again(self) -> None:
        subprocess.run(["git", "add", "-f", ".ekzd/session.json"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "temporarily track session state"], cwd=self.root, check=True)
        subprocess.run(["git", "rm", "--cached", ".ekzd/session.json"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "restore local session state"], cwd=self.root, check=True)

        self.assertTrue((self.root / ".ekzd/session.json").is_file())
        self.assertEqual("", subprocess.run(["git", "ls-files", "--", ".ekzd/session.json"], cwd=self.root, text=True, capture_output=True, check=True).stdout)
        with self.assertRaisesRegex(HarnessError, "Protected EkzD harness files were committed"):
            verify_session(self.root)

    def test_project_config_cannot_enter_session_history_even_if_restored(self) -> None:
        abort_session(self.root)
        config = self.root / ".ekzd/project.toml"
        repo_wide = config_text().replace('include = ["README.md"]', 'include = ["*"]')
        config.write_text(repo_wide, encoding="utf-8")
        subprocess.run(["git", "add", ".ekzd/project.toml"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "configure repo-wide scope"], cwd=self.root, check=True)
        start_session(self.root, "Protect the frozen harness contract")

        config.write_text(repo_wide + "\n# temporary session edit\n", encoding="utf-8")
        subprocess.run(["git", "add", ".ekzd/project.toml"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "temporarily change harness config"], cwd=self.root, check=True)
        config.write_text(repo_wide, encoding="utf-8")
        subprocess.run(["git", "add", ".ekzd/project.toml"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "restore harness config"], cwd=self.root, check=True)

        with self.assertRaisesRegex(HarnessError, "Protected EkzD harness files were committed"):
            verify_session(self.root)


if __name__ == "__main__":
    unittest.main()

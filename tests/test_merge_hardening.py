from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError, load_config, verify_session
from ekzd.workflow import start_reproducible_session


def config_text(
    *,
    include: list[str],
    exclude: list[str] | None = None,
    max_commits: int = 3,
    command: list[str] | None = None,
    schema_version: str = "1",
    timeout_seconds: str = "30",
) -> str:
    command = command or ["python3", "-c", "print('ok')"]
    exclude = exclude or []
    return f'''schema_version = {schema_version}

[project]
name = "demo"

[sources]
paths = ["README.md"]

[scope]
include = {json.dumps(include)}
exclude = {json.dumps(exclude)}
constraints = ["Keep work scoped."]

[authority]
may = ["Edit scoped files."]
requires_approval = ["Finish the session."]
may_not = ["Expand scope silently."]

[session]
max_commits = {max_commits}

[acceptance]
criteria = ["Configured verification passes."]

[[verification.steps]]
name = "check"
command = {json.dumps(command)}
cwd = "."
timeout_seconds = {timeout_seconds}
'''


class MergeHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        (self.root / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.root / ".ekzd").mkdir()
        (self.root / ".gitignore").write_text(".ekzd/session.json\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_config(self, content: str) -> None:
        (self.root / ".ekzd/project.toml").write_text(content, encoding="utf-8")

    def commit_all(self, message: str = "init") -> None:
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.root, check=True)

    def test_committed_rename_cannot_hide_out_of_scope_source_path(self) -> None:
        secret = self.root / "secret"
        secret.mkdir()
        (secret / "a.txt").write_text("secret\n", encoding="utf-8")
        self.write_config(config_text(include=["allowed/"]))
        self.commit_all()
        start_reproducible_session(self.root, "Move allowed file", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)

        (self.root / "allowed").mkdir()
        subprocess.run(["git", "mv", "secret/a.txt", "allowed/a.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "move file"], cwd=self.root, check=True)

        with self.assertRaisesRegex(HarnessError, "secret/a.txt: outside scope.include"):
            verify_session(self.root)

    def test_staged_rename_cannot_hide_out_of_scope_source_path(self) -> None:
        secret = self.root / "secret"
        secret.mkdir()
        (secret / "a.txt").write_text("secret\n", encoding="utf-8")
        self.write_config(config_text(include=["allowed/"]))
        self.commit_all()
        start_reproducible_session(self.root, "Move allowed file", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)

        (self.root / "allowed").mkdir()
        subprocess.run(["git", "mv", "secret/a.txt", "allowed/a.txt"], cwd=self.root, check=True)

        with self.assertRaisesRegex(HarnessError, "secret/a.txt: outside scope.include"):
            verify_session(self.root)

    def test_newline_filename_is_parsed_losslessly_for_excludes(self) -> None:
        secret = self.root / "secret"
        secret.mkdir()
        unusual = secret / "a\nb.txt"
        unusual.write_text("one\n", encoding="utf-8")
        self.write_config(config_text(include=["*"], exclude=["secret/"]))
        self.commit_all()
        start_reproducible_session(self.root, "Touch one file", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)
        unusual.write_text("two\n", encoding="utf-8")

        with self.assertRaisesRegex(HarnessError, "matches scope.exclude"):
            verify_session(self.root)

    def test_verifier_created_commit_rechecks_session_budget(self) -> None:
        src = self.root / "src"
        src.mkdir()
        app = src / "app.py"
        app.write_text("VALUE = 0\n", encoding="utf-8")
        command = [
            "python3",
            "-c",
            "from pathlib import Path; import subprocess; "
            "Path('src/app.py').write_text('VALUE = 2\\n', encoding='utf-8'); "
            "subprocess.run(['git','add','src/app.py'], check=True); "
            "subprocess.run(['git','commit','-qm','verifier commit'], check=True)",
        ]
        self.write_config(config_text(include=["src/"], max_commits=1, command=command))
        self.commit_all()
        start_reproducible_session(self.root, "Bounded change", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)
        app.write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/app.py"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "user commit"], cwd=self.root, check=True)

        with self.assertRaisesRegex(HarnessError, "commit budget exceeded"):
            verify_session(self.root)

    def test_verifier_config_mutation_rechecks_session_contract(self) -> None:
        command = [
            "python3",
            "-c",
            "from pathlib import Path; p=Path('.ekzd/project.toml'); "
            "p.write_text(p.read_text(encoding='utf-8') + '\\n# verifier mutation\\n', encoding='utf-8')",
        ]
        self.write_config(config_text(include=["*"], command=command))
        self.commit_all()
        start_reproducible_session(self.root, "Keep contract immutable", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)

        with self.assertRaisesRegex(HarnessError, "must remain clean"):
            verify_session(self.root)

    def test_verifier_cannot_rebase_scope_on_rewritten_session_state(self) -> None:
        command = [
            "python3",
            "-c",
            "from pathlib import Path; import json, subprocess; "
            "Path('outside.txt').write_text('forbidden\\n', encoding='utf-8'); "
            "subprocess.run(['git','add','outside.txt'], check=True); "
            "subprocess.run(['git','commit','-qm','forbidden verifier commit'], check=True); "
            "p=Path('.ekzd/session.json'); state=json.loads(p.read_text(encoding='utf-8')); "
            "state['start_git']['head']=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(); "
            "p.write_text(json.dumps(state, indent=2, sort_keys=True) + '\\n', encoding='utf-8')",
        ]
        self.write_config(config_text(include=["README.md"], command=command))
        self.commit_all()
        start_reproducible_session(self.root, "Protect local session contract", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)

        with self.assertRaisesRegex(HarnessError, "session state changed during verification"):
            verify_session(self.root)

    def test_tracked_session_state_is_rejected(self) -> None:
        self.write_config(config_text(include=["README.md"]))
        self.commit_all()
        start_reproducible_session(self.root, "Keep state local", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "-f", ".ekzd/session.json"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "track session state"], cwd=self.root, check=True)

        with self.assertRaisesRegex(HarnessError, "session state must remain local and untracked"):
            verify_session(self.root)

    def test_missing_verification_executable_is_a_failed_step(self) -> None:
        self.write_config(config_text(include=["README.md"], command=["ekzd-command-that-does-not-exist-7f42c1"]))
        self.commit_all()
        start_reproducible_session(self.root, "Run configured check", implementation_branch="feat/task")
        subprocess.run(["git", "checkout", "-qb", "feat/task"], cwd=self.root, check=True)

        verification = verify_session(self.root)

        self.assertFalse(verification["passed"])
        self.assertEqual(1, len(verification["steps"]))
        self.assertFalse(verification["steps"][0]["passed"])
        self.assertTrue(verification["steps"][0]["launch_error"])
        self.assertIsNone(verification["steps"][0]["exit_code"])

    def test_schema_version_rejects_boolean_and_float(self) -> None:
        for value in ("true", "1.0"):
            with self.subTest(value=value):
                self.write_config(config_text(include=["README.md"], schema_version=value))
                with self.assertRaisesRegex(HarnessError, "schema_version must be integer 1"):
                    load_config(self.root)

    def test_timeout_rejects_boolean(self) -> None:
        self.write_config(config_text(include=["README.md"], timeout_seconds="true"))

        with self.assertRaisesRegex(HarnessError, "timeout_seconds must be an integer"):
            load_config(self.root)


if __name__ == "__main__":
    unittest.main()

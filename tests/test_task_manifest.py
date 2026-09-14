from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from ekzd.core import HarnessError
from ekzd.task import load_task_manifest


VALID = '''schema_version = 1
objective = "Implement worker checking"
baseline = "1111111111111111111111111111111111111111"
implementation_branch = "feat/worker-check"
max_commits = 4
repository = "https://github.com/example/demo"

[scope]
include = ["src/", "tests/"]
exclude = ["src/generated/"]

[acceptance]
criteria = ["Checks pass."]

[[verification.steps]]
name = "extra"
command = ["python3", "-c", "print('ok')"]
cwd = "."
timeout_seconds = 30
'''


class TaskManifestTests(unittest.TestCase):
    def _write(self, content: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "task.toml"
        path.write_bytes(content.encode("utf-8"))
        return path

    def test_parses_required_worker_authority(self) -> None:
        path = self._write(VALID)
        task = load_task_manifest(path)
        self.assertEqual("Implement worker checking", task.objective)
        self.assertEqual("1" * 40, task.baseline)
        self.assertEqual("feat/worker-check", task.implementation_branch)
        self.assertEqual(("src/", "tests/"), task.include)
        self.assertEqual(("src/generated/",), task.exclude)
        self.assertEqual(("Checks pass.",), task.acceptance_criteria)
        self.assertEqual(4, task.max_commits)
        self.assertEqual("https://github.com/example/demo", task.repository)

    def test_identity_hashes_exact_manifest_bytes(self) -> None:
        path = self._write(VALID)
        expected = hashlib.sha256(VALID.encode("utf-8")).hexdigest()
        self.assertEqual(expected, load_task_manifest(path).sha256)
        path.write_bytes((VALID + "\n").encode("utf-8"))
        self.assertNotEqual(expected, load_task_manifest(path).sha256)

    def test_rejects_wrong_schema_or_invalid_baseline(self) -> None:
        for content, message in (
            (VALID.replace("schema_version = 1", "schema_version = 2"), "schema_version"),
            (VALID.replace('baseline = "' + "1" * 40 + '"', 'baseline = "abc"'), "baseline"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(HarnessError, message):
                    load_task_manifest(self._write(content))

    def test_requires_scope_and_acceptance_authority(self) -> None:
        for content, message in (
            (VALID.replace('include = ["src/", "tests/"]', "include = []"), "scope.include"),
            (VALID.replace('criteria = ["Checks pass."]', "criteria = []"), "acceptance.criteria"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(HarnessError, message):
                    load_task_manifest(self._write(content))

    def test_commit_ceiling_is_bounded_by_ekzd_policy(self) -> None:
        for value in ("0", "21", "true"):
            with self.subTest(value=value):
                content = VALID.replace("max_commits = 4", f"max_commits = {value}")
                with self.assertRaisesRegex(HarnessError, "max_commits"):
                    load_task_manifest(self._write(content))

    def test_task_verification_is_optional_and_argv_preserving(self) -> None:
        task = load_task_manifest(self._write(VALID))
        step = task.verification_steps[0]
        self.assertEqual("extra", step.name)
        self.assertEqual(("python3", "-c", "print('ok')"), step.command)
        self.assertEqual(".", step.cwd)
        self.assertEqual(30, step.timeout_seconds)
        without = VALID.split("\n[[verification.steps]]", 1)[0] + "\n"
        self.assertEqual((), load_task_manifest(self._write(without)).verification_steps)


if __name__ == "__main__":
    unittest.main()

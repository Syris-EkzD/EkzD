from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd import __version__
from ekzd.identity import BuildIdentityUnavailable, build_sha256, runtime_identity
from ekzd.worker import run_worker_check


class IdentityTests(unittest.TestCase):
    def _package(self, root: Path, *, line_endings: bytes = b"\n") -> Path:
        package = root / "ekzd"
        (package / "nested").mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"VALUE = 1" + line_endings)
        (package / "nested" / "module.py").write_bytes(b"SECOND = 2" + line_endings)
        return package

    def test_runtime_identity_uses_package_version_and_full_lowercase_sha256(self) -> None:
        identity = runtime_identity()
        self.assertEqual("0.3.0", __version__)
        self.assertEqual(__version__, identity["version"])
        self.assertRegex(identity["build_sha256"], r"^[0-9a-f]{64}$")

    def test_build_sha_is_location_independent_for_identical_sources(self) -> None:
        with tempfile.TemporaryDirectory() as first_temp, tempfile.TemporaryDirectory() as second_temp:
            first = self._package(Path(first_temp))
            second = self._package(Path(second_temp))
            self.assertEqual(build_sha256(first), build_sha256(second))

    def test_build_sha_normalizes_line_endings_and_ignores_cache_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as first_temp, tempfile.TemporaryDirectory() as second_temp:
            first = self._package(Path(first_temp), line_endings=b"\n")
            second = self._package(Path(second_temp), line_endings=b"\r\n")
            (second / "nested" / "module.py").write_bytes(b"SECOND = 2\r")
            (first / "nested" / "module.py").write_bytes(b"SECOND = 2\n")
            cache = second / "__pycache__"
            cache.mkdir()
            (cache / "junk.pyc").write_bytes(b"installer/cache specific bytes")
            self.assertEqual(build_sha256(first), build_sha256(second))

    def test_build_sha_changes_when_source_path_or_normalized_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = self._package(root)
            original = build_sha256(package)

            module = package / "nested" / "module.py"
            renamed = package / "nested" / "renamed.py"
            module.rename(renamed)
            renamed_digest = build_sha256(package)
            self.assertNotEqual(original, renamed_digest)

            renamed.write_text("SECOND = 3\n", encoding="utf-8")
            self.assertNotEqual(renamed_digest, build_sha256(package))

    def test_symlinked_python_source_makes_identity_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = self._package(root)
            target = root / "target.txt"
            target.write_text("VALUE = 3\n", encoding="utf-8")
            (package / "linked.py").symlink_to(target)

            with self.assertRaises(BuildIdentityUnavailable):
                build_sha256(package)

    def test_unreadable_python_source_makes_identity_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = self._package(Path(temp_dir))
            with mock.patch.object(Path, "read_bytes", side_effect=OSError("denied")):
                with self.assertRaises(BuildIdentityUnavailable):
                    build_sha256(package)

    def test_symlinked_source_directory_makes_source_set_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = self._package(root)
            external = root / "external"
            external.mkdir()
            (external / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
            (package / "linked-dir").symlink_to(external, target_is_directory=True)

            with self.assertRaises(BuildIdentityUnavailable):
                build_sha256(package)


class WorkerIdentityPinningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "EkzD Identity Test")
        self._git("config", "user.email", "identity@example.invalid")
        (self.root / ".ekzd").mkdir()
        (self.root / ".gitignore").write_text(".ekzd/session.json\n__pycache__/\n", encoding="utf-8")
        (self.root / "allowed.txt").write_text("baseline\n", encoding="utf-8")
        self._write_project_command(["python3", "-c", "print('project-ok')"])
        self._git("add", ".")
        self._git("commit", "-q", "-m", "baseline")
        self.baseline = self._git("rev-parse", "HEAD")
        self._git("checkout", "-q", "-b", "feat/task")
        self.task_path = Path(self.temp.name) / "task.toml"

    def _git(self, *args: str) -> str:
        process = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True)
        return process.stdout.strip()

    def _array(self, values: list[str]) -> str:
        return "[" + ", ".join(json.dumps(value) for value in values) + "]"

    def _write_project_command(self, command: list[str]) -> None:
        config = f'''schema_version = 1

[project]
name = "Demo"

[sources]
paths = ["allowed.txt"]

[scope]
include = ["*"]
exclude = []
constraints = []

[authority]
may = []
requires_approval = []
may_not = []

[session]
max_commits = 20

[acceptance]
criteria = ["verification"]

[[verification.steps]]
name = "base"
command = {self._array(command)}
cwd = "."
timeout_seconds = 30
'''
        (self.root / ".ekzd/project.toml").write_text(config, encoding="utf-8")

    def _write_task(
        self,
        *,
        schema_version: int = 1,
        version: str | None = None,
        build_sha256_value: str | None = None,
        task_command: list[str] | None = None,
    ) -> None:
        lines = [
            f"schema_version = {schema_version}",
            'objective = "Identity pinned worker task"',
            f'baseline = "{self.baseline}"',
            'implementation_branch = "feat/task"',
            "max_commits = 20",
        ]
        if schema_version == 2:
            identity = runtime_identity()
            lines += [
                "",
                "[ekzd]",
                f"version = {json.dumps(version if version is not None else identity['version'])}",
                f"build_sha256 = {json.dumps(build_sha256_value if build_sha256_value is not None else identity['build_sha256'])}",
            ]
        lines += [
            "",
            "[scope]",
            'include = ["*"]',
            "exclude = []",
            "",
            "[acceptance]",
            'criteria = ["ready"]',
        ]
        if task_command is not None:
            lines += [
                "",
                "[[verification.steps]]",
                'name = "task"',
                f"command = {self._array(task_command)}",
                'cwd = "."',
                "timeout_seconds = 30",
            ]
        self.task_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _checks(self, result: dict) -> dict[str, dict]:
        return {item["name"]: item for item in result["checks"]}

    def test_schema_v1_remains_ready_with_nonblocking_unpinned_warning(self) -> None:
        self._write_task(schema_version=1)
        result = run_worker_check(self.root, self.task_path)
        identity = runtime_identity()

        self.assertTrue(result["ready"])
        self.assertEqual(identity, result["ekzd"])
        check = self._checks(result)["ekzd-identity"]
        self.assertEqual("WARN", check["status"])
        self.assertFalse(check["required"])
        self.assertIn("does not pin", check["message"])

    def test_schema_v2_matching_identity_proceeds_through_normal_verification(self) -> None:
        self._write_task(schema_version=2)
        result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)

        self.assertTrue(result["ready"])
        self.assertEqual("PASS", checks["ekzd-identity"]["status"])
        self.assertEqual("PASS", checks["project:base"]["status"])

    def test_schema_v2_mismatch_blocks_before_project_or_task_commands_execute(self) -> None:
        project_marker = self.root / "project-ran.txt"
        task_marker = self.root / "task-ran.txt"
        self._git("checkout", "-q", "main")
        self._write_project_command(
            ["python3", "-c", "from pathlib import Path; Path('project-ran.txt').write_text('ran')"]
        )
        self._git("add", ".ekzd/project.toml")
        self._git("commit", "-q", "-m", "marker baseline")
        self.baseline = self._git("rev-parse", "HEAD")
        self._git("checkout", "-q", "feat/task")
        self._git("reset", "-q", "--hard", self.baseline)
        self._write_task(
            schema_version=2,
            build_sha256_value="0" * 64,
            task_command=["python3", "-c", "from pathlib import Path; Path('task-ran.txt').write_text('ran')"],
        )

        result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)

        self.assertFalse(result["ready"])
        self.assertEqual("FAIL", checks["ekzd-identity"]["status"])
        self.assertFalse(project_marker.exists())
        self.assertFalse(task_marker.exists())
        self.assertFalse(any(name.startswith("project:") or name.startswith("task:") for name in checks))

    def test_runtime_identity_unavailable_fails_closed_before_verification(self) -> None:
        self._write_task(schema_version=1)
        with mock.patch(
            "ekzd.worker.runtime_identity",
            side_effect=BuildIdentityUnavailable("source enumeration failed"),
        ):
            result = run_worker_check(self.root, self.task_path)
        checks = self._checks(result)

        self.assertFalse(result["ready"])
        self.assertEqual("UNAVAILABLE", checks["ekzd-identity"]["status"])
        self.assertIsNone(result["ekzd"]["build_sha256"])
        self.assertFalse(any(name.startswith("project:") or name.startswith("task:") for name in checks))


if __name__ == "__main__":
    unittest.main()

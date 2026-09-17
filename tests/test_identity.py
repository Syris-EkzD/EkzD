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




if __name__ == "__main__":
    unittest.main()

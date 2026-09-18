import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd import identity
from ekzd.core import HarnessError
from ekzd.runtime import capture_runtime


class RuntimeCaptureTests(unittest.TestCase):
    def test_capture_uses_executing_package_and_copied_identity(self):
        expected = identity.runtime_identity()
        files = capture_runtime(expected)
        self.assertIn('__init__.py', files)
        self.assertTrue(all(name.endswith('.py') and not name.startswith('/') and '..' not in Path(name).parts for name in files))
        with tempfile.TemporaryDirectory() as directory:
            for name, content in files.items():
                path = Path(directory) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            self.assertEqual(expected['build_sha256'], identity.build_sha256(Path(directory)))

    def test_capture_rejects_mismatch_and_unexpected_symlinks(self):
        expected = identity.runtime_identity()
        with self.assertRaises(HarnessError):
            capture_runtime({**expected, 'build_sha256': '0'*64})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '__init__.py').write_text('')
            (root / 'unrelated').symlink_to('/etc/passwd')
            with mock.patch.object(identity, '__file__', str(root / 'identity.py')), mock.patch.object(identity, 'runtime_identity', return_value=expected):
                with self.assertRaisesRegex(HarnessError, 'symlinks'):
                    capture_runtime(expected)

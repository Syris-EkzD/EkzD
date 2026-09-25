import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

import test_contract_check as fixtures
from ekzd import handoff


class PortableTests(unittest.TestCase):
    setUp = fixtures.FrozenCheckTests.setUp
    git = fixtures.FrozenCheckTests.git
    freeze = fixtures.FrozenCheckTests.freeze

    def bundle(self, contract):
        metadata, _ = handoff.retain_handoff(self.root, contract)
        location = Path(self.temp.name) / 'extracted'
        location.mkdir()
        with zipfile.ZipFile(self.root / metadata['path']) as archive:
            archive.extractall(location)
        # This fixture has no local ignore rule; keep retained material external.
        shutil.rmtree(self.root / '.ekzd')
        return location

    def run_portable(self, location, *args, root=None, extra_env=None):
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.update(extra_env or {})
        return subprocess.run([sys.executable, '-S', str(location / 'run.py'), *args, '--json'],
                              cwd=root or self.root, env=env, capture_output=True, text=True, timeout=20)

    def test_relocated_runtime_works_without_installed_package(self):
        location = self.bundle(self.freeze())
        for args in [('prepare',), ('check',), ('check', '--final')]:
            result = self.run_portable(location, *args)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)['ready'])

    def test_conflicting_pythonpath_and_candidate_source_cannot_shadow_bundle(self):
        c = self.freeze()
        location = self.bundle(c)
        fake = self.root / 'src/ekzd'
        fake.mkdir(parents=True)
        (fake / '__init__.py').write_text("raise RuntimeError('wrong candidate package imported')\n")
        # Pretend a competing installed package is visible through normal resolution.
        other = Path(self.temp.name) / 'installed/ekzd'
        other.mkdir(parents=True)
        (other / '__init__.py').write_text("raise RuntimeError('wrong installed package imported')\n")
        result = self.run_portable(location, 'check', extra_env={'PYTHONPATH': os.pathsep.join([str(other.parent), str(fake.parent)])})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(c['ekzd'], json.loads(result.stdout)['ekzd'])

    def test_changed_runtime_and_contract_block_prepare_and_check(self):
        location = self.bundle(self.freeze())
        for name in ['runtime/ekzd/core.py', 'contract.json', 'run.py']:
            path = location / name
            original = path.read_bytes()
            # A comment preserves executable run.py behavior while changing its hash.
            path.write_bytes(original + b'\n# tampered\n')
            for command in ('prepare', 'check'):
                result = self.run_portable(location, command)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('Handoff', result.stderr)
            path.write_bytes(original)

    def test_runtime_build_is_checked_even_with_consistent_file_hashes(self):
        import hashlib
        from ekzd.contract import canonical_bytes
        location = self.bundle(self.freeze())
        source = location / 'runtime/ekzd/core.py'
        source.write_bytes(source.read_bytes() + b'\n# different build\n')
        manifest_path = location / 'handoff.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['files']['runtime/ekzd/core.py'] = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest_path.write_bytes(canonical_bytes(manifest))
        result = self.run_portable(location, 'prepare')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('runtime identity mismatch', result.stderr)

    def test_malformed_payload_and_symlinks_are_rejected(self):
        location = self.bundle(self.freeze())
        unexpected = location / 'unexpected'
        unexpected.write_text('not in manifest')
        self.assertNotEqual(0, self.run_portable(location, 'prepare').returncode)
        unexpected.unlink()
        unexpected.symlink_to('/etc/passwd')
        result = self.run_portable(location, 'check')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('symlinks', result.stderr)

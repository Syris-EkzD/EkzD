"""Regressions for the incident's alleged read-only-to-promotion path.

Use synthetic, owned temporary trees only. The child interpreter denies every
filesystem mutation and process launch before importing or validating with wm.
"""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_wm
import wm


class ReadOnlyValidationTests(unittest.TestCase):
    setUp = test_wm.WorkflowTests.setUp
    cli = test_wm.WorkflowTests.cli
    prepared = test_wm.WorkflowTests.prepared

    def test_recorded_validation_pipeline_cannot_write_or_call_promotion(self):
        self.prepared()
        self.assertEqual(self.cli('finish'), 0)
        before = wm.snapshot(self.root)
        # This reproduces the recorded real-candidate validation, but all paths
        # belong to the disposable fixture. The guard is independent of wm code.
        script = r'''
import os
import sys

def no_mutation(event, args):
    if event == 'open':
        flags = args[2]
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError('Read-only validation attempted a file write')
    if event in {
        'os.mkdir', 'os.remove', 'os.rmdir', 'os.rename', 'os.link', 'os.symlink',
        'os.truncate', 'os.chmod', 'os.chown', 'os.utime', 'os.system',
        'os.exec', 'os.posix_spawn', 'subprocess.Popen', 'socket.connect',
    }:
        raise AssertionError('Read-only validation attempted a mutation or execution')

sys.addaudithook(no_mutation)
import json
from pathlib import Path
import wm

def forbidden(*args, **kwargs):
    raise AssertionError('Read-only validation reached a write-capable workflow')

for name in ('create_profile', 'promote', 'promotion_main', 'write_promotion_reports',
             'exclusive_write', 'main', 'prepare', 'finish', 'git_checks'):
    setattr(wm, name, forbidden)
root, run = map(Path, sys.argv[1:])
source = json.loads((run / 'report.json').read_text())
candidate = wm.exact_text(root / wm.CANDIDATE)
assert wm.digest(candidate) == source['candidate_sha256']
response = json.loads((run / 'response.json').read_text())
wm.validate_response(response, source['prompt_sha256'])
assert wm.render(response) == candidate
canonical, edits = wm.canonicalize(candidate)
assert canonical.startswith('# Horus — Agent Profile\n')
assert edits
assert not (root / wm.PROFILE).exists()
'''
        result = subprocess.run(
            [sys.executable, '-B', '-c', script, str(self.root), str(self.run)],
            cwd=Path(wm.__file__).parent, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(wm.changes(before, wm.snapshot(self.root)), [])

    def test_fixture_root_and_repo_are_owned_temporary_paths(self):
        self.assertEqual(self.root.resolve(), Path(self.tmp.name).resolve())
        self.assertEqual(wm.REPO.resolve(), self.repo.resolve())
        self.assertTrue(self.repo.resolve().is_relative_to(self.root.resolve()))
        self.assertNotEqual(self.root.resolve(), (Path.home() / 'WhiteTree').resolve())

    def test_cli_approval_cannot_turn_finish_into_promotion(self):
        with patch.object(wm, 'promotion_main') as promote:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                wm.main(['finish', '--root', str(self.root), '--run', 'test', '--approve'])
            promote.assert_not_called()
        self.assertFalse((self.root / wm.PROFILE).exists())

    def test_promotion_requires_approval_before_any_profile_write(self):
        self.prepared()
        self.assertEqual(self.cli('finish'), 0)
        with patch.object(wm, 'create_profile') as writer:
            with contextlib.redirect_stdout(io.StringIO()):
                result = wm.main(['promote', '--root', str(self.root), '--run', 'test'])
            self.assertEqual(result, 1)
            writer.assert_not_called()
        self.assertFalse((self.root / wm.PROFILE).exists())


if __name__ == '__main__':
    unittest.main()

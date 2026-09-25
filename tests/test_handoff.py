import copy
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from ekzd import handoff
from ekzd.core import HarnessError
from ekzd.contract import compose_contract
from ekzd.identity import runtime_identity
from test_contract import project, task


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = compose_contract(project(), task(), 'a'*40, runtime_identity())

    def test_instructions_render_complete_worker_authority_deterministically(self):
        task_data = task()
        task_data.update(
            include=['src/**', 'tests/**'],
            exclude=['vendor/**'],
            sources=['README.md'],
            acceptance=['Feature works.', 'Regression is covered.'],
            guidance=['Keep the task rule.'],
            readiness=[dict(name='git', command=['git', '--version'])],
            verification=[dict(name='focused', command=['python3', '-m', 'unittest', 'tests.test_feature'], cwd='.', timeout_seconds=45)],
        )
        contract = compose_contract(
            project(
                protected=['secrets/'],
                exclude=['generated/**'],
                guidance=['Keep the project rule.'],
                readiness=[dict(
                    name='python',
                    command=['python3', '--version'],
                    cwd='tools',
                    timeout_seconds=30,
                )],
            ),
            task_data,
            'a' * 40,
            runtime_identity(),
        )

        rendered = handoff.instructions(contract).decode('utf-8')
        self.assertEqual(rendered, handoff.instructions(copy.deepcopy(contract)).decode('utf-8'))
        for expected in (
                '# EkzD worker task', 'contract.json', contract['objective'],
                contract['baseline'], contract['branch'], '## Allowed scope',
                'src/**', 'tests/**', '## Excluded paths', 'generated/**',
                'vendor/**', '## Protected paths', 'secrets/',
                '.ekzd/project.toml', '## Acceptance criteria', 'Feature works.',
                'Regression is covered.', '## Declared source/reference files',
                'README.md', '## Project/task guidance', 'Keep the project rule.',
                'Keep the task rule.', f"Effective commit ceiling: {contract['max_commits']} commits",
                '## Readiness checks', 'python3', '--version', 'tools', '30 seconds',
                'git', '## Verification checks', 'focused', 'tests.test_feature',
                '45 seconds', 'run.py prepare', 'run.py check', 'run.py check --final',
                'meaningful implementation milestones', 'does not judge commit meaning',
                'Do not widen the frozen task authority', 'Do not install missing capabilities',
                'requirements are ambiguous', 'unauthorized scope', 'Do not merge',
                'Exact branch', 'Exact commit', 'Contract ID', 'Verification result',
                'Changed files', 'Unresolved issues and review notes'):
            self.assertIn(expected, rendered)

    def test_archive_is_deterministic_and_has_only_controlled_members(self):
        files = handoff.build_payload(self.contract)
        a = handoff.archive_bytes(files)
        self.assertEqual(a, handoff.archive_bytes(dict(reversed(list(files.items())))))
        with zipfile.ZipFile(io.BytesIO(a)) as archive:
            self.assertEqual(sorted(files), archive.namelist())
            for info in archive.infolist():
                self.assertEqual((1980, 1, 1, 0, 0, 0), info.date_time)
                self.assertEqual(0o100644, info.external_attr >> 16)
                self.assertFalse(info.filename.startswith('/'))
                self.assertNotIn('..', Path(info.filename).parts)
                self.assertTrue(info.filename in {'run.py', 'instructions.md', 'contract.json', 'handoff.json'} or info.filename.startswith('runtime/ekzd/') and info.filename.endswith('.py'))
        for name in ('/etc/passwd', '../outside', 'a/../outside'):
            with self.assertRaises(HarnessError):
                handoff.archive_bytes({name: b'bad'})

    def test_reexport_uses_retained_payload_even_without_archive_or_current_runtime(self):
        metadata, created = handoff.retain_handoff(self.root, self.contract)
        self.assertTrue(created)
        path = self.root / metadata['path']
        original = path.read_bytes()
        path.unlink()
        with mock.patch.object(handoff, 'capture_runtime', side_effect=AssertionError('must not recapture')):
            self.assertEqual(original, handoff.reexport_bytes(self.root, self.contract, metadata))

    def test_retained_tampering_fails(self):
        metadata, _ = handoff.retain_handoff(self.root, self.contract)
        path = (self.root / metadata['path']).parent / 'payload/contract.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(HarnessError):
            handoff.reexport_bytes(self.root, self.contract, metadata)

    def test_failed_archive_build_publishes_nothing(self):
        with mock.patch.object(handoff, 'archive_bytes', side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):
                handoff.retain_handoff(self.root, self.contract)
        self.assertEqual([], list((self.root / '.ekzd/local/handoffs').iterdir()))

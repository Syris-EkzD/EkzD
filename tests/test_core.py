"""One-time policy setup and baseline preconditions."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd import core, session
from phase3_helpers import project_text, freeze


class ProjectSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-qb', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.root / 'file').write_text('baseline')
        self.git('add', '.')
        self.git('commit', '-qm', 'baseline')
        core.init_project(self.root, 'Demo')
        self.policy = self.root / '.ekzd/project.toml'

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE, text=True).strip()

    def ready(self):
        self.policy.write_text(project_text())
        self.git('add', '.')
        self.git('commit', '-qm', 'policy')

    def test_init_creates_durable_template_and_local_ignore(self):
        self.assertIn('/.ekzd/local/', (self.root / '.gitignore').read_text())
        self.assertEqual('Demo', core.load_config(self.root, ready=False)['name'])
        self.assertNotIn('include', self.policy.read_text())
        with self.assertRaises(core.HarnessError):
            core.load_config(self.root)

    def test_start_requires_clean_committed_policy(self):
        with self.assertRaises(core.HarnessError):
            freeze(self.root)
        self.ready()
        self.policy.write_text(self.policy.read_text() + '# dirty\n')
        with self.assertRaisesRegex(core.HarnessError, 'clean'):
            freeze(self.root)

    def test_required_project_verification_and_task_acceptance(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'empty policy')
        with self.assertRaisesRegex(core.HarnessError, 'verification'):
            freeze(self.root)
        self.ready()
        with self.assertRaisesRegex(core.HarnessError, 'acceptance'):
            freeze(self.root, acceptance=[])

    def test_symlinked_project_policy_is_rejected(self):
        self.policy.unlink()
        (self.root / 'policy.toml').write_text(project_text())
        self.policy.symlink_to('../policy.toml')
        self.git('add', '.')
        self.git('commit', '-qm', 'symlink policy')
        with self.assertRaisesRegex(core.HarnessError, 'regular tracked file'):
            freeze(self.root)

    def test_dirty_baseline_cannot_start(self):
        self.ready()
        (self.root / 'file').write_text('dirty')
        with self.assertRaisesRegex(core.HarnessError, 'clean'):
            freeze(self.root)

    def test_branch_must_be_literal_valid_and_separate(self):
        self.ready()
        for branch in ['main', '-bad', 'bad branch', '@{-1}']:
            with self.subTest(branch=branch), self.assertRaises(core.HarnessError):
                freeze(self.root, branch=branch)
        self.git('checkout', '--detach')
        self.assertEqual('feat/task', freeze(self.root)['contract']['branch'])

    def test_finish_requires_explicit_acceptance(self):
        self.ready()
        freeze(self.root)
        self.git('switch', '-qc', 'feat/task')
        self.assertTrue(session.verify_session(self.root)['passed'])
        with self.assertRaisesRegex(core.HarnessError, 'explicit'):
            session.finish_session(self.root, accept=False)

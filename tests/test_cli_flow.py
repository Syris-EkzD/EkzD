"""Real CLI flow: one policy commit supports two independent tasks."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from phase3_helpers import project_text, task_text


class CliFlowTests(unittest.TestCase):
    def test_two_tasks_against_one_committed_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.PIPE).strip()
            def cli(*args):
                result = subprocess.run([sys.executable, '-m', 'ekzd.cli', *args], cwd=root, env=env, text=True, capture_output=True)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                return result.stdout
            git('init', '-qb', 'main')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            cli('init')
            policy = root / '.ekzd/project.toml'
            policy.write_text(project_text())
            (root / 'app.py').write_text('VALUE = 1\n')
            git('add', '.')
            git('commit', '-qm', 'configure durable policy once')
            policy_commit = git('rev-parse', 'HEAD')
            local = root / '.ekzd/local'
            local.mkdir()
            draft = local / 'task-a.toml'
            draft.write_text(task_text(include=['app.py'], sources=['app.py']))
            started = cli('start', str(draft))
            self.assertIn('contract ID', started)
            self.assertIn('handoff.zip', started)
            self.assertIn('run.py prepare', started)
            state = json.loads((local / 'session.json').read_text())
            frozen = state['contract_id']
            with tempfile.TemporaryDirectory() as extracted:
                with zipfile.ZipFile(root / state['handoff']['path']) as archive:
                    archive.extractall(extracted)
                # Retain an external launcher for the complete candidate lifecycle.
                worker_dir = self.enterContext(tempfile.TemporaryDirectory())
                import shutil
                shutil.copytree(extracted, worker_dir, dirs_exist_ok=True)
            def worker(*args):
                result = subprocess.run([sys.executable, str(Path(worker_dir) / 'run.py'), *args, '--json'], cwd=root, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                return result.stdout
            status = cli('status')
            self.assertIn(str(root / state['handoff']['path']), status)
            self.assertNotIn('ekzd handoff', status)
            git('switch', '-qc', 'feat/task')
            self.assertTrue(json.loads(worker('prepare'))['ready'])
            (root / 'app.py').write_text('VALUE = 2\n')
            self.assertTrue(json.loads(worker('check'))['ready'])
            git('add', 'app.py')
            git('commit', '-qm', 'implement task A')
            self.assertTrue(json.loads(worker('check', '--final'))['ready'])
            cli('verify')
            self.assertIn('passed', cli('status'))
            cli('finish', '--accept')
            self.assertIn('finished', cli('status'))
            second = local / 'task-b.toml'
            second.write_text(task_text(branch='feat/second', objective='Task B', include=['app.py']))
            cli('start', str(second))
            self.assertNotEqual(frozen, json.loads((local / 'session.json').read_text())['contract_id'])
            self.assertEqual(policy_commit, git('log', '-1', '--format=%H', '--', '.ekzd/project.toml'))
            self.assertEqual('', git('status', '--porcelain'))

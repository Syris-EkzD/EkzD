"""Real CLI flow: one policy commit supports two independent tasks."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
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
            cli('start', str(draft))
            frozen = cli('contract')
            contract = local / 'contract.json'
            contract.write_text(frozen)
            self.assertIn('Contract ID:', cli('prompt'))
            cli('status')
            git('switch', '-qc', 'feat/task')
            (root / 'app.py').write_text('VALUE = 2\n')
            self.assertTrue(json.loads(cli('check', '--contract', str(contract), '--json'))['ready'])
            git('add', 'app.py')
            git('commit', '-qm', 'implement task A')
            self.assertTrue(json.loads(cli('check', '--contract', str(contract), '--final', '--json'))['ready'])
            cli('verify')
            self.assertIn('passed', cli('status'))
            cli('finish', '--accept')
            self.assertIn('finished', cli('status'))
            second = local / 'task-b.toml'
            second.write_text(task_text(branch='feat/second', objective='Task B', include=['app.py']))
            cli('start', str(second))
            self.assertNotEqual(frozen, cli('contract'))
            self.assertEqual(policy_commit, git('log', '-1', '--format=%H', '--', '.ekzd/project.toml'))
            self.assertEqual('', git('status', '--porcelain'))

"""Real start/archive-only transfer; worker has no maintainer installation."""
import json
import os
import shutil
import subprocess
import sys
import unittest
import venv
import zipfile
from pathlib import Path

import test_session_contract as fixtures
from ekzd.identity import runtime_identity
from phase3_helpers import freeze


class OfflineHandoffTests(unittest.TestCase):
    setUp = fixtures.SessionContractTests.setUp
    git = fixtures.SessionContractTests.git
    commit = fixtures.SessionContractTests.commit

    def extract(self, state):
        # Copy just the portable archive, independently of target Git transfer.
        transferred = Path(self.temp.name) / 'transfer.zip'
        shutil.copyfile(self.root / state['handoff']['path'], transferred)
        location = Path(self.temp.name) / 'extracted'
        with zipfile.ZipFile(transferred) as archive:
            archive.extractall(location)
        return location

    def run_worker(self, location, command='check', *args, root=None, python=None, env=None):
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        environment.update(env or {})
        return subprocess.run([str(python or sys.executable), str(location / 'run.py'), command, *args, '--json'],
                              cwd=root or self.root, env=environment, text=True, capture_output=True, timeout=30)

    def assert_ready(self, result):
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['ready'])

    def test_offline_archive_only_separate_checkout_and_pinned_self_hosting(self):
        # The target itself is EkzD source, but the executor must remain build A.
        package = Path(__import__('ekzd').__file__).parent
        shutil.copytree(package, self.root / 'src/ekzd', ignore=shutil.ignore_patterns('__pycache__'))
        self.commit('target contains runtime A')
        state = freeze(self.root, include=['src/ekzd/'], sources=['src/ekzd/__init__.py'])
        location = self.extract(state)
        worker = Path(self.temp.name) / 'worker'
        subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(self.root), str(worker)], check=True)
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=worker, text=True, stderr=subprocess.PIPE).strip()
        git('config', 'user.name', 'Worker'); git('config', 'user.email', 'worker@example.invalid')
        git('switch', '-qc', state['contract']['branch'], state['contract']['baseline'])
        # Maintainer files cannot be consulted again by this worker.
        shutil.rmtree(self.root)
        # The launcher isolates site packages; PYTHONPATH points at candidate B.
        env = {'PYTHONPATH': str(worker / 'src')}
        for command in ('prepare', 'check'):
            self.assert_ready(self.run_worker(location, command, root=worker, env=env))
        candidate = worker / 'src/ekzd/__init__.py'
        candidate.write_text("raise RuntimeError('candidate B must not be imported')\n")
        self.assert_ready(self.run_worker(location, root=worker, env=env))
        git('add', 'src/ekzd/__init__.py'); git('commit', '-qm', 'implement build B')
        result = self.run_worker(location, 'check', '--final', root=worker, env=env)
        self.assert_ready(result)
        self.assertEqual(state['contract']['ekzd'], json.loads(result.stdout)['ekzd'])
        self.assertEqual(runtime_identity(), state['contract']['ekzd'])

    def test_real_conflicting_installed_package_is_not_imported(self):
        state = freeze(self.root)
        location = self.extract(state)
        self.git('switch', '-qc', 'feat/task')
        environment = Path(self.temp.name) / 'venv'
        venv.EnvBuilder(with_pip=False).create(environment)
        python = environment / 'bin/python'
        site = subprocess.check_output([str(python), '-c', 'import site; print(site.getsitepackages()[0])'], text=True).strip()
        conflicting = Path(site) / 'ekzd'
        conflicting.mkdir()
        (conflicting / '__init__.py').write_text("MARKER = 'wrong installed build'\n")
        control = subprocess.check_output([str(python), '-c', 'import ekzd; print(ekzd.MARKER)'], cwd=self.root, env={k:v for k,v in os.environ.items() if k != 'PYTHONPATH'}, text=True)
        self.assertEqual('wrong installed build\n', control)
        self.assert_ready(self.run_worker(location, 'prepare', python=python))
        self.assert_ready(self.run_worker(location, python=python))

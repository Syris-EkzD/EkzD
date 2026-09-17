import unittest
from unittest import mock

from ekzd.contract import compose_contract, parse_project
from ekzd.core import HarnessError
from ekzd.contract_check import check_contract
from ekzd.identity import runtime_identity
from test_contract import project, task
import test_contract_check as fixtures


class ReadinessSchemaTests(unittest.TestCase):
    def test_readiness_is_additive_and_closed(self):
        p = project(readiness=[dict(name='project', command=['true'])])
        t = task(readiness=[dict(name='task', command=['true'])])
        c = compose_contract(p, t, 'a'*40, runtime_identity())
        self.assertEqual(['project', 'task'], [s['name'] for s in c['readiness']])
        p['readiness'][0]['timeouts'] = 4
        with self.assertRaisesRegex(HarnessError, 'readiness.*timeouts'):
            parse_project(p)


class PreparationTests(unittest.TestCase):
    setUp = fixtures.FrozenCheckTests.setUp
    git = fixtures.FrozenCheckTests.git
    freeze = fixtures.FrozenCheckTests.freeze

    def test_prepare_does_not_run_future_tests(self):
        c = self.freeze([dict(name='future', command=['python3', 'future_test.py'])])
        self.assertTrue(check_contract(self.root, c, final=True, prepare=True)['ready'])
        self.assertFalse(check_contract(self.root, c, final=True)['ready'])


    def test_missing_capabilities_block_and_are_repeated(self):
        c = self.freeze()
        from ekzd.contract import verification_steps
        for command in (['ekzd-nonexistent-tool-xyz'], ['python3', '-c', 'import ekzd_nonexistent_module_xyz']):
            c['readiness'] = verification_steps([dict(name='capability', command=command)])
            self.assertEqual('UNAVAILABLE', check_contract(self.root, c, final=True, prepare=True)['overall_status'])
        c['readiness'] = verification_steps([dict(name='capability', command=['python3', '-c', "assert not __import__('pathlib').Path('gone').exists()"] )])
        self.assertTrue(check_contract(self.root, c, final=True, prepare=True)['ready'])
        (self.root / 'gone').write_text('capability lost')
        self.assertFalse(check_contract(self.root, c, final=False)['ready'])


    def test_prepare_requires_clean_baseline(self):
        c = self.freeze()
        (self.root / 'file').write_text('dirty')
        self.assertFalse(check_contract(self.root, c, final=True, prepare=True)['ready'])
        self.git('add', '.'); self.git('commit', '-qm', 'candidate')
        self.assertFalse(check_contract(self.root, c, final=True, prepare=True)['ready'])
        self.assertTrue(check_contract(self.root, c, final=True)['ready'])



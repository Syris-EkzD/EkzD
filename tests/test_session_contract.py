import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd import core, session
from ekzd.contract import canonical_bytes, contract_id, load_contract
from ekzd.worker import run_worker_check
from phase3_helpers import export_contract
from ekzd.handoff import instructions
from phase3_helpers import project_text, task_text, freeze, export


class SessionContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.git('init', '-qb', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        core.init_project(self.root)
        self.policy = self.root / '.ekzd/project.toml'
        self.policy.write_text(project_text(protected=['secret'], exclude=['excluded']))
        (self.root / 'file').write_text('baseline')
        self.commit('one-time policy')
        self.contract_file = Path(self.temp.name) / 'contract.json'

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE, text=True).strip()

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-qm', message)

    def start(self, **kwargs):
        state = freeze(self.root, **kwargs)
        export(self.root, self.contract_file)
        self.git('switch', '-qc', state['contract']['branch'])
        return state

    def test_two_tasks_without_policy_admin_commit_complete_flow(self):
        original = self.policy.read_bytes()
        policy_head = self.git('log', '-1', '--format=%H', '--', '.ekzd/project.toml')
        self.start(sources=['file'])
        (self.root / 'file').write_text('implemented')
        self.assertTrue(run_worker_check(self.root, self.contract_file)['ready'])
        self.commit('task A')
        self.assertTrue(run_worker_check(self.root, self.contract_file, final=True)['ready'])
        self.assertTrue(session.verify_session(self.root)['passed'])
        session.finish_session(self.root, accept=True)
        second = freeze(self.root, branch='feat/second', objective='Second task')
        self.assertIsNone(second['verification'])
        self.assertEqual(original, self.policy.read_bytes())
        self.assertEqual(policy_head, self.git('log', '-1', '--format=%H', '--', '.ekzd/project.toml'))
        self.assertEqual('active', second['status'])

    def test_original_task_and_project_edits_never_change_frozen_authority(self):
        state = self.start()
        frozen = export_contract(self.root)
        (self.root / '.ekzd/local/draft.toml').write_text('invalid draft now')
        self.policy.write_text('invalid project now')
        self.assertEqual(frozen, export_contract(self.root))
        self.assertIn(state['contract_id'], instructions(state['contract']).decode())
        self.assertFalse(session.verify_session(self.root)['passed'])
        self.git('restore', '.ekzd/project.toml')
        self.assertTrue(session.verify_session(self.root)['passed'])

    def test_abort_refreeze_clears_evidence_and_changes_authority(self):
        first = self.start()
        self.assertTrue(session.verify_session(self.root)['passed'])
        with self.assertRaisesRegex(core.HarnessError, 'active'):
            freeze(self.root, branch='feat/second')
        session.abort_session(self.root)
        second = freeze(self.root, branch='feat/second', objective='Changed')
        self.assertNotEqual(first['contract_id'], second['contract_id'])
        self.assertIsNone(second['verification'])
        self.assertIsNone(second['acceptance'])

    def test_source_deletion_is_allowed_in_both_paths(self):
        self.start(sources=['file'])
        self.git('rm', 'file')
        self.git('commit', '-qm', 'delete source')
        self.assertTrue(run_worker_check(self.root, self.contract_file, final=True)['ready'])
        self.assertTrue(session.verify_session(self.root)['passed'])
        session.finish_session(self.root, accept=True)

    def test_missing_baseline_source_cannot_start(self):
        with self.assertRaisesRegex(core.HarnessError, 'frozen baseline'):
            freeze(self.root, sources=['missing'])
        self.assertIsNone(session.read_state(self.root))

    def test_project_protected_excluded_and_required_checks_remain(self):
        self.start()
        for path in ['secret', 'excluded', '.ekzd/project.toml']:
            with self.subTest(path=path):
                target = self.root / path
                old = target.read_bytes() if target.exists() else None
                target.write_text('changed')
                checked = run_worker_check(self.root, self.contract_file)
                self.assertFalse(checked['ready'])
                if old is None: target.unlink()
                else: target.write_bytes(old)
        c = load_contract(self.contract_file)
        self.assertEqual(['step-0'], [s['name'] for s in c['verification']])

    def test_acceptance_binds_contract_id_even_for_same_candidate(self):
        self.start()
        session.verify_session(self.root)
        state = session.read_state(self.root)
        state['contract']['objective'] = 'different authority'
        state['contract_id'] = contract_id(state['contract'])
        state['handoff']['path'] = f".ekzd/local/handoffs/{state['contract_id']}/handoff.zip"
        session.write_state(self.root, state)
        with self.assertRaisesRegex(core.HarnessError, 'stale'):
            session.finish_session(self.root, accept=True)

    def test_contract_tampering_is_detected_before_use(self):
        self.start()
        state = session.read_state(self.root)
        state['contract']['max_commits'] = 49
        session.write_state(self.root, state)
        with self.assertRaisesRegex(core.HarnessError, 'ID mismatch'):
            session.verify_session(self.root)

    def test_legacy_state_rejected_actionably(self):
        old = self.root / '.ekzd/session.json'
        old.write_text('{}')
        with self.assertRaisesRegex(core.HarnessError, 'Unsupported legacy'):
            freeze(self.root)

    def test_verifier_mutating_local_authority_stops_later_commands(self):
        self.policy.write_text(project_text(commands=[
            ['python3', '-c', "open('.ekzd/local/session.json','w').write('{}')"],
            ['python3', '-c', "open('later','w').write('bad')"],
        ]))
        self.commit('checks')
        self.start()
        with self.assertRaises(core.HarnessError):
            session.verify_session(self.root)
        self.assertFalse((self.root / 'later').exists())

    def test_atomic_state_write_failure_preserves_previous_record(self):
        self.start()
        previous = (self.root / session.STATE).read_bytes()
        state = session.read_state(self.root)
        state['status'] = 'aborted'
        with mock.patch.object(session.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                session.write_state(self.root, state)
        self.assertEqual(previous, (self.root / session.STATE).read_bytes())
        self.assertEqual([], list((self.root / session.LOCAL).glob('.session-*')))

    def test_lifecycle_lock_rejects_concurrent_mutation(self):
        with session.lifecycle_lock(self.root):
            with self.assertRaisesRegex(core.HarnessError, 'Another'):
                freeze(self.root)

    def test_local_state_symlinks_and_tracking_are_rejected(self):
        local = self.root / session.LOCAL
        local.symlink_to(Path(self.temp.name))
        with self.assertRaisesRegex(core.HarnessError, 'symlink'):
            freeze(self.root)
        local.unlink()
        local.mkdir()
        (local / 'bad').write_text('tracked')
        self.git('add', '-f', '.ekzd/local/bad')
        with self.assertRaisesRegex(core.HarnessError, 'untracked'):
            freeze(self.root)

    def test_session_mutation_during_last_candidate_capture_cannot_be_overwritten(self):
        self.start()
        original = session.capture_candidate
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            path = self.root / session.STATE
            path.write_bytes(path.read_bytes() + b'\n')
            return result
        with mock.patch.object(session, 'capture_candidate', side_effect=mutate):
            with self.assertRaisesRegex(core.HarnessError, 'Session changed'):
                session.verify_session(self.root)
        self.assertIsNone(session.read_state(self.root)['verification'])

    def test_task_passing_check_cannot_replace_failing_project_check(self):
        self.policy.write_text(project_text(commands=[['python3', '-c', 'raise SystemExit(7)']]))
        self.commit('required failing project check')
        draft = self.root / '.ekzd/local/draft.toml'
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(task_text() + '\n[[verification]]\nname="task-pass"\ncommand=["true"]\n')
        session.start_session(self.root, draft)
        export(self.root, self.contract_file)
        self.git('switch', '-qc', 'feat/task')
        worker = run_worker_check(self.root, self.contract_file, final=True)
        maintainer = session.verify_session(self.root)
        self.assertFalse(worker['ready'])
        self.assertFalse(maintainer['passed'])
        for steps in (worker['checks'], maintainer['steps']):
            checks = {step['name']: step for step in steps}
            self.assertEqual('FAIL', checks['verification:step-0']['status'])
            self.assertEqual('PASS', checks['verification:task-pass']['status'])

    def test_malformed_success_flag_is_not_accepted(self):
        self.start()
        session.verify_session(self.root)
        state = session.read_state(self.root)
        state['verification']['passed'] = 'false'
        session.write_state(self.root, state)
        with self.assertRaisesRegex(core.HarnessError, 'has not passed'):
            session.finish_session(self.root, accept=True)

    def test_worker_export_is_independent_of_filesystem_location(self):
        self.start()
        first = export_contract(self.root).encode()
        copied = Path(self.temp.name) / 'elsewhere.json'
        copied.write_bytes(first)
        self.assertEqual(load_contract(self.contract_file), load_contract(copied))
        self.assertEqual(contract_id(load_contract(copied)), session.active_state(self.root)['contract_id'])

    def test_status_does_not_claim_pass_with_mismatching_runtime(self):
        from ekzd import workflow
        self.start()
        session.verify_session(self.root)
        with mock.patch.object(workflow, 'runtime_identity', return_value={'version': 'wrong', 'build_sha256': '0' * 64}):
            status = workflow.build_workflow_status(self.root)
        self.assertEqual('blocked', status['verification'])
        self.assertIn('runtime', status['blocked_reason'])

"""History/scope regressions retained across the contract-model break."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ekzd import core, session
from ekzd.worker import run_worker_check
from phase3_helpers import project_text, freeze, export


class AuthorityHistoryTests(unittest.TestCase):
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
        self.policy.write_text(project_text())
        (self.root / 'allowed').write_text('one')
        (self.root / 'secret').write_text('original')
        self.commit('baseline')
        self.path = Path(self.temp.name) / 'contract.json'

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE, text=True).strip()

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-qm', message)

    def start(self, **kwargs):
        freeze(self.root, **kwargs)
        export(self.root, self.path)
        self.git('switch', '-qc', 'feat/task')

    def both_reject(self, text):
        result = run_worker_check(self.root, self.path)
        self.assertFalse(result['ready'])
        self.assertIn(text, str(result))
        verification = session.verify_session(self.root)
        self.assertFalse(verification['passed'])
        self.assertIn(text, str(verification))

    def test_rename_cannot_hide_source_in_staged_or_committed_state(self):
        self.start(include=['allowed*'])
        self.git('mv', 'secret', 'allowed-new')
        self.both_reject('secret: outside scope.include')
        self.git('commit', '-qm', 'rename')
        self.both_reject('secret: outside scope.include')

    def test_newline_paths_remain_lossless_in_exclusions(self):
        self.start(exclude=['secret*'])
        (self.root / 'secret\nname').write_text('changed')
        result = run_worker_check(self.root, self.path)
        self.assertFalse(result['ready'])
        self.assertIn('secret\nname: matches scope.exclude', result['checks'][0]['message'])

    def test_reverted_out_of_scope_history_still_fails(self):
        self.start(include=['allowed'])
        (self.root / 'secret').write_text('changed')
        self.commit('touch forbidden')
        self.git('revert', '--no-edit', 'HEAD')
        self.assertEqual('', self.git('diff', 'HEAD~2', 'HEAD'))
        self.both_reject('secret: outside scope.include')

    def test_merge_side_history_cannot_hide_scope_violation(self):
        self.start(include=['allowed'])
        self.git('switch', '-qc', 'side')
        (self.root / 'secret').write_text('changed')
        self.commit('forbidden side commit')
        self.git('revert', '--no-edit', 'HEAD')
        self.git('switch', '-q', 'feat/task')
        (self.root / 'allowed').write_text('implementation')
        self.commit('candidate')
        self.git('merge', '--no-ff', '-m', 'merge side', 'side')
        self.both_reject('secret: outside scope.include')

    def test_protected_project_history_cannot_be_restored_away(self):
        self.start()
        old = self.policy.read_text()
        self.policy.write_text(old + '\n# changed\n')
        self.commit('temporary policy edit')
        self.policy.write_text(old)
        self.commit('restore policy')
        self.both_reject('.ekzd/project.toml: matches scope.protected')

    def test_local_state_history_cannot_be_restored_away(self):
        self.start()
        local = self.root / '.ekzd/local/history'
        local.write_text('local')
        self.git('add', '-f', '.ekzd/local/history')
        self.git('commit', '-qm', 'track local artifact')
        self.git('rm', '--cached', '.ekzd/local/history')
        self.git('commit', '-qm', 'untrack artifact')
        self.both_reject('.ekzd/local/history: matches scope.protected')

    def test_staged_policy_change_with_original_worktree_still_fails(self):
        self.start()
        old = self.policy.read_text()
        self.policy.write_text(old + '\n# staged change\n')
        self.git('add', '.ekzd/project.toml')
        self.policy.write_text(old)
        self.both_reject('.ekzd/project.toml: matches scope.protected')

    def test_hidden_index_flags_are_rejected(self):
        self.start()
        for flag in ('assume-unchanged', 'skip-worktree'):
            self.git('update-index', '--' + flag, 'secret')
            self.both_reject('assume-unchanged/skip-worktree')
            self.git('update-index', '--no-' + flag, 'secret')

    def test_content_filter_cannot_hide_candidate_difference(self):
        self.start()
        (self.root / '.git/info/attributes').write_text('secret filter=hide\n')
        self.git('config', 'filter.hide.clean', "sed 's/changed/original/'")
        self.git('config', 'filter.hide.smudge', 'cat')
        (self.root / 'secret').write_text('changed')
        self.assertEqual('', self.git('diff', '--name-only', '--', 'secret'))
        self.both_reject('Git content filters are unsupported')

    def test_budget_counts_history_with_no_universal_twenty_ceiling(self):
        self.start(max_commits=21)
        for i in range(21):
            self.git('commit', '--allow-empty', '-qm', f'candidate {i}')
        self.assertTrue(run_worker_check(self.root, self.path, final=True)['ready'])
        self.assertTrue(session.verify_session(self.root)['passed'])
        self.git('commit', '--allow-empty', '-qm', 'one too many')
        self.both_reject('Commit budget exceeded: 22 > 21')
        session.abort_session(self.root)
        self.assertEqual('aborted', session.read_state(self.root)['status'])

    def test_verifier_created_commit_invalidates_original_candidate(self):
        self.policy.write_text(project_text(commands=[['git', 'commit', '--allow-empty', '-m', 'verifier mutation']]))
        self.commit('mutating check')
        self.start()
        before = self.git('rev-parse', 'HEAD')
        verification = session.verify_session(self.root)
        self.assertFalse(verification['passed'])
        self.assertEqual(before, verification['candidate']['head'])
        self.assertNotEqual(before, self.git('rev-parse', 'HEAD'))
        with self.assertRaises(core.HarnessError):
            session.finish_session(self.root, accept=True)

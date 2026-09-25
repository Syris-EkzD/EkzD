from __future__ import annotations

import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd import core, worker, session, contract_check
from ekzd.candidate import capture_candidate
from phase3_helpers import export_contract
from ekzd.workflow import build_workflow_status
from phase3_helpers import project_text, task_text, freeze


class CandidateStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'repo'
        self.root.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.root / '.ekzd').mkdir()
        (self.root / '.gitignore').write_text('.ekzd/local/\n')
        (self.root / 'README.md').write_text('good\n')
        self.config = self.root / '.ekzd/project.toml'
        self.config.write_text(project_text())
        self.commit('baseline')
        self.task = self.base / 'task.toml'

    def git(self, *args: str) -> str:
        return subprocess.run(['git', *args], cwd=self.root, check=True, text=True, capture_output=True).stdout.strip()

    def commit(self, message: str = 'candidate') -> None:
        self.git('add', '.')
        self.git('commit', '-qm', message)

    def start(self) -> None:
        freeze(self.root, sources=['README.md'])
        self.task.write_text(export_contract(self.root))
        self.git('checkout', '-qb', 'feat/task')

    def assert_both_reject(self, message=None):
        verification = session.verify_session(self.root)
        self.assertFalse(verification['passed'], verification)
        result = worker.run_worker_check(self.root, self.task, final=True)
        self.assertFalse(result['ready'], result)
        if message:
            self.assertIn(message, json.dumps(verification))
            self.assertIn(message, json.dumps(result))

    def test_clean_final_candidate_passes_and_accepts_exact_head(self) -> None:
        self.start()
        (self.root / 'README.md').write_text('implemented\n')
        self.commit()
        self.assertTrue(worker.run_worker_check(self.root, self.task, final=True)['ready'])
        self.assertTrue(session.verify_session(self.root)['passed'])
        accepted = session.finish_session(self.root, accept=True)
        self.assertEqual(self.git('rev-parse', 'HEAD'), accepted['acceptance']['head'])

    def test_cli_worker_and_maintainer_accept_same_committed_candidate(self) -> None:
        env = dict(os.environ, PYTHONPATH=str(Path(core.__file__).resolve().parent.parent))
        def cli(*args):
            return subprocess.run([sys.executable, '-m', 'ekzd.cli', *args], cwd=self.root,
                                  env=env, text=True, capture_output=True)
        draft = self.base / 'draft.toml'
        draft.write_text(task_text())
        self.assertEqual(0, cli('start', str(draft)).returncode)
        import zipfile
        state = session.read_state(self.root)
        location = self.base / 'handoff'
        with zipfile.ZipFile(self.root / state['handoff']['path']) as archive:
            archive.extractall(location)
        self.assertNotEqual(0, cli('verify').returncode)  # Still on main.
        self.git('checkout', '-qb', 'feat/task')
        (self.root / 'README.md').write_text('implemented')
        self.commit()
        checked = subprocess.run([sys.executable, str(location / 'run.py'), 'check', '--final', '--json'], cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(0, checked.returncode, checked.stdout + checked.stderr)
        self.assertTrue(json.loads(checked.stdout)['ready'])
        verified = cli('verify')
        self.assertEqual(0, verified.returncode, verified.stdout + verified.stderr)
        accepted = cli('finish', '--accept')
        self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD'), session.read_state(self.root)['acceptance']['head'])

    def test_worker_mutation_between_structural_boundaries_cannot_be_rebound(self) -> None:
        self.start()
        original = contract_check.capture_candidate
        calls = 0
        def capture(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / 'README.md').write_text('late change\n')
            return original(root)
        with mock.patch.object(contract_check, 'capture_candidate', side_effect=capture):
            result = worker.run_worker_check(self.root, self.task, final=True)
        self.assertFalse(result['ready'])
        self.assertIn('Candidate changed during structural verification', json.dumps(result))
        self.assertTrue(result['git']['clean'])

    def test_failed_retry_clears_prior_success(self):
        self.start()
        self.assertTrue(session.verify_session(self.root)['passed'])
        (self.root / 'README.md').write_text('dirty after success')
        self.assertFalse(session.verify_session(self.root)['passed'])
        self.git('restore', 'README.md')
        with self.assertRaisesRegex(core.HarnessError, 'verification has not passed'):
            session.finish_session(self.root, accept=True)

    def test_maintainer_mutation_at_completion_cannot_be_rebound(self):
        self.start()
        original = contract_check.check_contract
        def late_change(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root / 'README.md').write_text('late change')
            return result
        with mock.patch.object(contract_check, 'check_contract', side_effect=late_change):
            with self.assertRaisesRegex(core.HarnessError, 'Candidate changed'):
                session.verify_session(self.root)
        self.assertIsNone(session.read_state(self.root)['verification'])

    def test_final_rejects_wrong_branch_and_detached_head(self) -> None:
        self.start()
        self.git('checkout', '-q', 'main')
        self.assert_both_reject()
        self.git('checkout', '-q', '--detach')
        self.assert_both_reject()

    def test_final_rejects_staged_unstaged_and_untracked_work(self) -> None:
        self.start()
        (self.root / 'README.md').write_text('dirty\n')
        self.assert_both_reject('clean')
        self.git('add', 'README.md')
        self.assert_both_reject('clean')
        self.git('reset', '--hard', 'HEAD')
        (self.root / 'new file').write_text('untracked')
        self.assert_both_reject('clean')

    def test_conflicted_index_is_explicitly_rejected(self) -> None:
        self.start()
        oid = self.git('rev-parse', 'HEAD:README.md')
        subprocess.run(['git', 'update-index', '--index-info'], cwd=self.root, check=True,
                       input=f'0 {"0" * 40}\tREADME.md\n100644 {oid} 1\tREADME.md\n100644 {oid} 2\tREADME.md\n100644 {oid} 3\tREADME.md\n', text=True)
        self.assert_both_reject('Unresolved index conflicts')

    def test_repository_operation_markers_are_rejected(self) -> None:
        self.start()
        for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer', 'BISECT_START', 'REBASE_HEAD', 'MERGE_AUTOSTASH', 'index.lock'):
            with self.subTest(operation=name):
                marker = self.root / self.git('rev-parse', '--git-path', name)
                marker.write_text('in progress')
                try:
                    self.assert_both_reject('Repository operation in progress')
                finally:
                    marker.unlink()

    def test_textconv_and_external_diff_cannot_hide_changes(self) -> None:
        self.start()
        (self.root / '.git/info/attributes').write_text('README.md diff=hide\n')
        self.git('config', 'diff.hide.textconv', "sh -c 'printf constant'")
        self.git('config', 'diff.external', "sh -c 'exit 0'")
        before = capture_candidate(self.root)
        (self.root / 'README.md').write_text('hidden\n')
        after = capture_candidate(self.root)
        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertIn('README.md', after.changed_paths)
        self.assert_both_reject('clean')

    def test_modes_symlinks_deletions_and_whitespace_are_bound(self) -> None:
        unusual = self.root / ' leading\tname\ntrailing '
        unusual.write_text('one')
        external = self.base / 'external'
        external.write_text('outside')
        link = self.root / 'link'
        link.symlink_to(external)
        self.commit('paths')
        self.start()
        first = capture_candidate(self.root)
        external.write_text('external target contents are not candidate bytes')
        self.assertEqual(first, capture_candidate(self.root))
        self.git('config', 'core.filemode', 'false')
        unusual.chmod(0o755)
        mode = capture_candidate(self.root)
        self.assertNotEqual(first.fingerprint, mode.fingerprint)
        self.assertIn(unusual.name, mode.changed_paths)
        self.assert_both_reject('clean')
        unusual.chmod(0o644)
        link.unlink(); link.symlink_to(self.base / 'missing')
        self.assertNotEqual(first.fingerprint, capture_candidate(self.root).fingerprint)
        link.unlink(); link.symlink_to(external)
        unusual.unlink()
        deleted = capture_candidate(self.root)
        self.assertNotEqual(first.fingerprint, deleted.fingerprint)
        self.assertIn(unusual.name, deleted.changed_paths)

    def test_ignored_artifacts_are_excluded_but_ignored_tracked_files_are_bound(self) -> None:
        (self.root / '.gitignore').write_text('.ekzd/local/\ncache/\nREADME.md\n')
        self.commit('ignore rules')
        cache = self.root / 'cache'
        cache.mkdir()
        before = capture_candidate(self.root)
        (cache / 'artifact').write_text('ignored')
        self.assertEqual(before, capture_candidate(self.root))
        (self.root / 'README.md').write_text('tracked despite ignore rule')
        self.assertNotEqual(before.fingerprint, capture_candidate(self.root).fingerprint)

    def test_legacy_session_without_declared_branch_cannot_verify(self):
        (self.root / '.ekzd/session.json').write_text('{}')
        with self.assertRaisesRegex(core.HarnessError, 'Unsupported legacy'):
            session.verify_session(self.root)

    def test_staging_is_bound_even_when_working_bytes_are_restored(self) -> None:
        first = capture_candidate(self.root)
        path = self.root / 'README.md'
        path.write_text('staged\n'); self.git('add', 'README.md'); path.write_text('good\n')
        self.assertNotEqual(first.fingerprint, capture_candidate(self.root).fingerprint)

    def test_untracked_content_changes_are_bound_with_identical_git_status(self) -> None:
        path = self.root / 'new file'
        path.write_text('first')
        before_status = self.git('status', '--porcelain')
        before = capture_candidate(self.root)
        path.write_text('second')
        self.assertEqual(before_status, self.git('status', '--porcelain'))
        self.assertNotEqual(before.fingerprint, capture_candidate(self.root).fingerprint)

    def test_raw_line_ending_changes_cannot_be_claimed_fully_committed(self) -> None:
        (self.root / '.gitattributes').write_text('README.md text\n')
        self.commit('attributes')
        self.start()
        (self.root / 'README.md').write_bytes(b'good\r\n')
        self.assert_both_reject('clean')

    def test_tracked_parent_symlink_never_reads_external_target(self) -> None:
        directory = self.root / 'directory'
        directory.mkdir()
        (directory / 'file').write_text('tracked')
        self.commit('nested file')
        external = self.base / 'external-dir'
        external.mkdir()
        (external / 'file').write_text('external')
        (directory / 'file').unlink()
        directory.rmdir()
        directory.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(core.HarnessError, 'traverses a symlink'):
            capture_candidate(self.root)

    def test_special_untracked_file_is_rejected_without_blocking_read(self) -> None:
        os.mkfifo(self.root / 'pipe')
        with self.assertRaisesRegex(core.HarnessError, 'Unsupported candidate file type'):
            capture_candidate(self.root)

    def test_linked_worktree_uses_its_own_operation_state(self) -> None:
        self.start()
        linked = self.base / 'linked'
        self.git('worktree', 'add', '-q', '-b', 'linked-task', str(linked))
        self.assertTrue(capture_candidate(linked).clean)
        git_path = subprocess.check_output(['git', 'rev-parse', '--git-path', 'CHERRY_PICK_HEAD'], cwd=linked, text=True).strip()
        Path(git_path).write_text('in progress')
        with self.assertRaisesRegex(core.HarnessError, 'CHERRY_PICK_HEAD'):
            capture_candidate(linked)
        self.assertTrue(capture_candidate(self.root).clean)

    def test_clean_and_dirty_submodules_are_unsupported(self) -> None:
        sub = self.base / 'sub'
        sub.mkdir()
        for args in (('init', '-q'), ('config', 'user.name', 'Test'), ('config', 'user.email', 'test@example.invalid')):
            subprocess.run(['git', *args], cwd=sub, check=True)
        (sub / 'file').write_text('one')
        subprocess.run(['git', 'add', '.'], cwd=sub, check=True)
        subprocess.run(['git', 'commit', '-qm', 'baseline'], cwd=sub, check=True)
        self.start()
        self.git('-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(sub), 'lib')
        self.commit('submodule')
        self.assert_both_reject('Submodules are unsupported')
        (self.root / 'lib/file').write_text('dirty')
        self.assert_both_reject('Submodules are unsupported')

    def test_history_replacement_and_nested_repository_are_rejected(self) -> None:
        self.start()
        head = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/replace/' + head, head)
        self.assert_both_reject('replacement refs/grafts')
        self.git('update-ref', '-d', 'refs/replace/' + head)
        grafts = self.root / self.git('rev-parse', '--git-path', 'info/grafts')
        grafts.write_text(head + '\n')
        self.assert_both_reject('replacement refs/grafts')
        grafts.unlink()
        nested = self.root / 'nested'
        nested.mkdir()
        subprocess.run(['git', 'init', '-q'], cwd=nested, check=True)
        self.assert_both_reject('Nested repositories are unsupported')

    def test_sparse_configuration_and_shallow_history_are_rejected(self) -> None:
        self.start()
        self.git('config', 'core.sparseCheckout', 'true')
        self.assert_both_reject('Sparse checkout')
        self.git('config', 'core.sparseCheckout', 'false')
        clone = self.base / 'shallow'
        subprocess.run(['git', 'clone', '-q', '--depth=1', self.root.as_uri(), str(clone)], check=True)
        with self.assertRaisesRegex(core.HarnessError, 'Shallow repositories'):
            capture_candidate(clone)
        self.assertFalse(worker.run_worker_check(clone, self.task, final=True)['ready'])

    def test_declared_source_deletion_agrees_in_both_paths(self) -> None:
        self.start()
        self.git('rm', '-q', 'README.md'); self.git('commit', '-qm', 'delete source')
        self.assertTrue(worker.run_worker_check(self.root, self.task, final=True)['ready'])
        self.assertTrue(session.verify_session(self.root)['passed'])
        self.assertIn('README.md', session.active_state(self.root)['contract']['sources'])
        self.assertEqual(self.task.read_text(), export_contract(self.root))
        session.finish_session(self.root, accept=True)

    def test_missing_baseline_source_is_rejected(self) -> None:
        self.git('rm', '-q', 'README.md'); self.git('commit', '-qm', 'missing source')
        with self.assertRaisesRegex(core.HarnessError, 'frozen baseline'):
            self.start()

    def test_acceptance_rejects_changed_head_branch_dirty_state_and_contract(self) -> None:
        self.start()
        session.verify_session(self.root)
        original_head = self.git('rev-parse', 'HEAD')
        self.git('commit', '--allow-empty', '-qm', 'different commit')
        with self.assertRaises(core.HarnessError): session.finish_session(self.root, accept=True)
        self.git('reset', '--hard', original_head)
        self.git('checkout', '-q', 'main')
        with self.assertRaises(core.HarnessError): session.finish_session(self.root, accept=True)
        self.git('checkout', '-q', 'feat/task')
        (self.root / 'README.md').write_text('dirty')
        self.assertEqual('stale', build_workflow_status(self.root)['verification'])
        with self.assertRaises(core.HarnessError): session.finish_session(self.root, accept=True)
        self.git('restore', 'README.md')
        self.config.write_text(self.config.read_text() + '\n# changed contract\n')
        with self.assertRaises(core.HarnessError): session.finish_session(self.root, accept=True)

    def test_stale_verified_contract_digest_blocks_acceptance(self) -> None:
        self.start()
        session.verify_session(self.root)
        state = session.read_state(self.root)
        state['verification']['contract_id'] = '0' * 64
        session.write_state(self.root, state)
        with self.assertRaisesRegex(core.HarnessError, 'stale candidate/contract'):
            session.finish_session(self.root, accept=True)

    def test_old_or_modified_evidence_cannot_be_accepted(self) -> None:
        self.start()
        session.verify_session(self.root)
        state = session.read_state(self.root)
        state['verification'].pop('candidate')
        session.write_state(self.root, state)
        with self.assertRaisesRegex(core.HarnessError, 'stale candidate/contract'):
            session.finish_session(self.root, accept=True)


if __name__ == '__main__':
    unittest.main()

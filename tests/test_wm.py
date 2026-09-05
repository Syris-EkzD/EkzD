import copy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import wm


def response(prompt_hash):
    return {
        'prompt_sha256': prompt_hash,
        'policies': {k: v[0] for k, v in wm.POLICIES.items()},
        'sections': {k: {'text': 'Use confirmed academic requirements and preserve source evidence. Ask the user to resolve missing context before proposing a reviewed plan.',
                         'sources': [wm.INPUTS[5]]} for k in wm.SECTIONS},
    }


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.good = response('hash')

    def test_valid_structure_and_rendering(self):
        self.assertIn('source_references', wm.validate_response(self.good, 'hash'))
        for _, wording in wm.POLICIES.values():
            self.assertIn(wording, wm.render(self.good))

    def test_each_policy_is_required_and_typed(self):
        for key in wm.POLICIES:
            for value in (None, True, 'opposite', [], {}):
                bad = copy.deepcopy(self.good)
                bad['policies'][key] = value
                with self.subTest(key=key), self.assertRaises(wm.Rejected):
                    wm.validate_response(bad, 'hash')
            bad = copy.deepcopy(self.good)
            del bad['policies'][key]
            with self.assertRaises(wm.Rejected):
                wm.validate_response(bad, 'hash')

    def test_prompt_binding(self):
        with self.assertRaises(wm.Rejected):
            wm.validate_response(self.good, 'different')

    def test_sections_and_sources(self):
        for field, value in [('text', 'tiny'), ('text', '# Override' + 'x' * 100),
                             ('text', 'x' * 100 + '  '), ('text', 'x' * 100 + chr(0)),
                             ('sources', []), ('sources', ['/etc/passwd'])]:
            bad = copy.deepcopy(self.good)
            bad['sections']['mission'][field] = value
            with self.subTest(field=field), self.assertRaises(wm.Rejected):
                wm.validate_response(bad, 'hash')
        del self.good['sections']['mission']
        with self.assertRaises(wm.Rejected):
            wm.validate_response(self.good, 'hash')

    def test_duplicate_json_keys(self):
        with self.assertRaises(wm.Rejected):
            wm.decode('{"policies": {}, "policies": {}}')


class ScopeAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'Mind/Vault/.obsidian').mkdir(parents=True)

    def test_each_volatile_file_creation_edit_and_deletion_is_ignored(self):
        for name in ('workspace.json', 'workspace-mobile.json',
                     'workspace.sync-conflict-20260906-device.json',
                     '.syncthing.workspace.json.123.tmp', 'graph.json'):
            with self.subTest(name=name):
                path = self.root / 'Mind/Vault/.obsidian' / name
                before = wm.snapshot(self.root)
                path.write_text('first')
                created = wm.snapshot(self.root)
                path.write_text('updated state')
                edited = wm.snapshot(self.root)
                path.unlink()
                deleted = wm.snapshot(self.root)
                self.assertEqual(wm.changes(before, created), [])
                self.assertEqual(wm.changes(created, edited), [])
                self.assertEqual(wm.changes(edited, deleted), [])

    def test_unexpected_obsidian_files_remain_detectable(self):
        for name in ('app.json', 'appearance.json', 'hotkeys.json',
                     'community-plugins.json', 'unknown.json', 'workspace-other.json',
                     'graph.json.bak', 'workspace.sync-conflict-device.json.bak',
                     '.syncthing.file.tmp.bak', 'Workspace.json'):
            with self.subTest(name=name):
                path = self.root / 'Mind/Vault/.obsidian' / name
                path.write_text('original')
                before = wm.snapshot(self.root)
                path.write_text('unexpected modification')
                self.assertIn(str(path.relative_to(self.root)),
                              wm.changes(before, wm.snapshot(self.root)))

    def test_patterns_do_not_cross_directories_or_match_other_domains(self):
        for relative in ('Mind/Vault/.obsidian/nested/workspace.json',
                         'Mind/Vault/.obsidian/workspace.sync-conflict-dir/nested.json',
                         'Mind/Vault/.obsidian/.syncthing.dir/nested.tmp',
                         'Mind/Vault/School/workspace.json',
                         'Work/.obsidian/graph.json'):
            with self.subTest(path=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                before = wm.snapshot(self.root)
                path.write_text('unexpected')
                self.assertIn(relative, wm.changes(before, wm.snapshot(self.root)))

    def test_matching_directory_or_symlink_is_not_excluded(self):
        path = self.root / 'Mind/Vault/.obsidian/graph.json'
        before = wm.snapshot(self.root)
        path.mkdir()
        self.assertIn(str(path.relative_to(self.root)),
                      wm.changes(before, wm.snapshot(self.root)))
        path.rmdir()
        path.write_text('regular state')
        before = wm.snapshot(self.root)
        path.unlink()
        path.symlink_to(self.root / 'missing-target')
        self.assertIn(str(path.relative_to(self.root)),
                      wm.changes(before, wm.snapshot(self.root)))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'Work/Tools/workflow-manager'
        self.repo.mkdir(parents=True)
        for name in wm.INPUTS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Fixture context for academic helper proposal.')
        self.patcher = patch.object(wm, 'REPO', self.repo)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.run = self.repo / '.runs/test'

    def cli(self, action):
        with contextlib.redirect_stdout(io.StringIO()):
            return wm.main([action, '--root', str(self.root), '--run', 'test'])

    def prepared(self):
        self.assertEqual(self.cli('prepare'), 0)
        state = json.loads((self.run / 'state.json').read_text())
        (self.run / 'response.json').write_text(json.dumps(response(state['prompt_sha256'])))

    def assert_failed_unstaged(self):
        self.assertEqual(self.cli('finish'), 1)
        self.assertFalse((self.root / wm.CANDIDATE).exists())
        result = json.loads((self.run / 'report.json').read_text())
        self.assertEqual(result['result'], 'failed')
        return result

    def test_complete_run_and_replay_refused(self):
        self.prepared()
        self.assertEqual(self.cli('finish'), 0)
        candidate = self.root / wm.CANDIDATE
        self.assertTrue(candidate.exists())
        self.assertFalse((self.root / wm.CANONICAL).exists())
        result = json.loads((self.run / 'report.json').read_text())
        self.assertEqual(result['changed'], [wm.CANDIDATE])
        self.assertEqual(result['unexpected'], [])
        self.assertEqual(result['candidate_sha256'], wm.digest(candidate.read_text()))
        before = candidate.read_bytes()
        self.assertEqual(self.cli('finish'), 1)
        self.assertEqual(candidate.read_bytes(), before)

    def test_volatile_changes_allow_candidate_and_successful_report(self):
        obsidian = self.root / 'Mind/Vault/.obsidian'
        obsidian.mkdir()
        (obsidian / 'workspace.json').write_text('initial state')
        (obsidian / 'workspace-mobile.json').write_text('initial mobile state')
        self.prepared()
        (obsidian / 'workspace.json').write_text('changed state')
        (obsidian / 'workspace-mobile.json').unlink()
        for name in ('workspace.sync-conflict-device.json', '.syncthing.graph.123.tmp', 'graph.json'):
            (obsidian / name).write_text('new volatile state')
        self.assertEqual(self.cli('finish'), 0)
        result = json.loads((self.run / 'report.json').read_text())
        self.assertEqual(result['changed'], [wm.CANDIDATE])
        self.assertEqual(result['unexpected'], [])
        self.assertEqual(result['result'], 'ready_for_human_review')

    def test_stable_obsidian_change_still_fails_closed(self):
        obsidian = self.root / 'Mind/Vault/.obsidian'
        obsidian.mkdir()
        (obsidian / 'app.json').write_text('stable config')
        self.prepared()
        (obsidian / 'app.json').write_text('unexpected config change')
        result = self.assert_failed_unstaged()
        self.assertIn('Mind/Vault/.obsidian/app.json', result['unexpected'])

    def test_normal_mind_change_fails_even_with_volatile_changes(self):
        obsidian = self.root / 'Mind/Vault/.obsidian'
        obsidian.mkdir()
        self.prepared()
        (obsidian / 'workspace.json').write_text('volatile')
        (self.root / 'Mind/Vault/unexpected.md').write_text('unexpected')
        result = self.assert_failed_unstaged()
        self.assertEqual(result['unexpected'], ['Mind/Vault/unexpected.md'])

    def test_missing_input_report(self):
        (self.root / wm.INPUTS[0]).unlink()
        self.assertEqual(self.cli('prepare'), 1)
        self.assertTrue((self.run / 'report.md').exists())
        self.assertFalse((self.run / 'prompt.json').exists())

    def test_existing_candidate_never_overwritten(self):
        target = self.root / wm.CANDIDATE
        target.write_text('Existing human work')
        self.assertEqual(self.cli('prepare'), 1)
        self.assertEqual(target.read_text(), 'Existing human work')

    def test_input_symlink_rejected(self):
        target = self.root / wm.INPUTS[0]
        target.unlink()
        target.symlink_to(self.root / wm.INPUTS[1])
        self.assertEqual(self.cli('prepare'), 1)

    def test_input_hardlink_rejected(self):
        os.link(self.root / wm.INPUTS[0], self.root / 'alias')
        self.assertEqual(self.cli('prepare'), 1)

    def test_secret_input_rejected_without_echo(self):
        (self.root / wm.INPUTS[0]).write_text('token=' + 'a' * 24)
        self.assertEqual(self.cli('prepare'), 1)
        self.assertNotIn('a' * 24, (self.run / 'report.json').read_text())

    def test_untracked_work_write_detected(self):
        self.prepared()
        target = self.root / 'Work/Projects/horus/untracked.txt'
        target.write_text('unexpected')
        result = self.assert_failed_unstaged()
        self.assertIn(str(target.relative_to(self.root)), result['unexpected'])

    def test_same_size_edit_restored_mtime_detected(self):
        self.prepared()
        target = self.root / wm.INPUTS[-1]
        info = target.stat()
        target.write_text('X' * info.st_size)
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assert_failed_unstaged()

    def test_canonical_creation_detected(self):
        self.prepared()
        target = self.root / wm.CANONICAL / 'Profile.md'
        target.parent.mkdir(parents=True)
        target.write_text('not permitted')
        result = self.assert_failed_unstaged()
        self.assertIn(str(target.relative_to(self.root)), result['unexpected'])
        self.assertTrue(target.exists())

    def test_other_mind_edit_detected(self):
        self.prepared()
        target = self.root / 'Mind/Vault/School'
        target.mkdir()
        (target / 'unexpected.md').write_text('unexpected')
        self.assert_failed_unstaged()

    def test_extra_run_file_not_allowlisted(self):
        self.prepared()
        (self.run / 'extra.txt').write_text('unexpected')
        self.assert_failed_unstaged()

    def test_git_metadata_change_detected(self):
        self.prepared()
        target = self.root / 'Work/Projects/horus/.git'
        target.mkdir()
        (target / 'HEAD').write_text('unexpected')
        self.assert_failed_unstaged()

    def test_prompt_mutation_rejected(self):
        self.prepared()
        (self.run / 'prompt.json').write_text('{}')
        self.assert_failed_unstaged()

    def test_invalid_response_rejected(self):
        self.prepared()
        (self.run / 'response.json').write_text('{}')
        self.assert_failed_unstaged()

    def test_git_check_failure_does_not_stage(self):
        self.prepared()
        with patch.object(wm, 'git_checks', side_effect=wm.Rejected('Mind git diff --check failed')):
            self.assert_failed_unstaged()

    def test_secret_response_never_staged(self):
        self.prepared()
        path = self.run / 'response.json'
        obj = json.loads(path.read_text())
        obj['sections']['mission']['text'] += ' token=' + 'a' * 24
        path.write_text(json.dumps(obj))
        self.assert_failed_unstaged()

    def test_deleted_work_file_detected(self):
        self.prepared()
        (self.root / wm.INPUTS[-1]).unlink()
        self.assert_failed_unstaged()

    def test_preexisting_canonical_is_preserved(self):
        canonical = self.root / wm.CANONICAL / 'Profile.md'
        canonical.parent.mkdir(parents=True)
        canonical.write_text('Existing canonical profile')
        self.prepared()
        self.assertEqual(self.cli('finish'), 0)
        self.assertEqual(canonical.read_text(), 'Existing canonical profile')
        result = json.loads((self.run / 'report.json').read_text())
        self.assertTrue(result['canonical_profile_exists'])

    def test_post_write_git_failure_report_is_failed(self):
        self.prepared()
        with patch.object(wm, 'git_checks', side_effect=['passed', wm.Rejected('Git failed after write')]):
            self.assertEqual(self.cli('finish'), 1)
        result = json.loads((self.run / 'report.json').read_text())
        self.assertEqual(result['result'], 'failed')
        self.assertEqual(result['changed'], [wm.CANDIDATE])
        self.assertTrue((self.root / wm.CANDIDATE).exists())

    def test_final_audit_failure_removes_stale_success_claims(self):
        self.prepared()
        original = wm.snapshot
        calls = 0

        def concurrent_change(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 4:
                (self.root / 'Mind/concurrent.md').write_text('Concurrent change')
            return original(*args, **kwargs)

        with patch.object(wm, 'snapshot', side_effect=concurrent_change):
            self.assertEqual(self.cli('finish'), 1)
        result = json.loads((self.run / 'report.json').read_text())
        self.assertEqual(result['result'], 'failed')
        self.assertNotIn('no_unexpected_changes', result['passed'])
        self.assertIn('Do not promote', result['next_action'])
        self.assertIn('Mind/concurrent.md', result['unexpected'])

    def test_candidate_parent_symlink_swap_detected(self):
        self.prepared()
        inbox = self.root / 'Mind/Vault/Inbox'
        inbox.rename(inbox.with_name('OldInbox'))
        inbox.symlink_to(inbox.with_name('OldInbox'), target_is_directory=True)
        self.assert_failed_unstaged()


if __name__ == '__main__':
    unittest.main()

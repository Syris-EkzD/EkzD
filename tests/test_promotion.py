import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import wm
import test_wm


class PromotionTests(unittest.TestCase):
    setUp = test_wm.WorkflowTests.setUp
    cli = test_wm.WorkflowTests.cli
    prepared = test_wm.WorkflowTests.prepared

    def ready(self):
        self.prepared()
        self.assertEqual(self.cli('finish'), 0)
        self.candidate = self.root / wm.CANDIDATE
        self.target = self.root / wm.PROFILE
        self.original = self.candidate.read_bytes()
        self.proposal = (self.root / wm.INPUTS[5]).read_bytes()
        self.artifacts = {p.name: p.read_bytes() for p in self.run.iterdir()}

    def promotion(self, approve=True, run='test'):
        args = ['promote', '--root', str(self.root), '--run', run]
        if approve:
            args.append('--approve')
        with contextlib.redirect_stdout(io.StringIO()):
            return wm.main(args)

    def report(self):
        paths = list((self.repo / '.promotions').glob('*.json'))
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].with_suffix('.md').is_file())
        return json.loads(paths[0].read_text())

    def rejected(self, approve=True, run='test'):
        self.assertEqual(self.promotion(approve, run), 1)
        self.assertFalse(self.target.exists())
        result = self.report()
        self.assertEqual(result['result'], 'failed')
        self.assertTrue(result['failed'])
        return result

    def test_approved_promotion_preserves_evidence_and_scope(self):
        self.ready()
        before = wm.snapshot(self.root)
        self.assertEqual(self.promotion(), 0)
        result = self.report()
        self.assertEqual(result['result'], 'promoted')
        expected, edits = wm.canonicalize(self.original.decode())
        self.assertEqual(self.target.read_bytes(), expected.encode())
        self.assertEqual(result['transformations'], edits)
        self.assertEqual(result['canonical_sha256'], wm.digest(expected))
        self.assertEqual(self.candidate.read_bytes(), self.original)
        self.assertEqual((self.root / wm.INPUTS[5]).read_bytes(), self.proposal)
        self.assertEqual({p.name: p.read_bytes() for p in self.run.iterdir()}, self.artifacts)
        allowed = {wm.PROFILE, wm.CANONICAL, 'Work/Tools/workflow-manager/.promotions'}
        allowed.update(str(p.relative_to(self.root)) for p in (self.repo / '.promotions').iterdir())
        self.assertTrue(set(wm.changes(before, wm.snapshot(self.root))) <= allowed)
        self.assertEqual(result['unexpected'], [])
        for key, (_, wording) in wm.POLICIES.items():
            if key != 'promotion':
                self.assertIn(wording, expected)

    def test_missing_approval(self):
        self.ready()
        self.rejected(approve=False)

    def test_missing_run(self):
        self.ready()
        self.rejected(run='absent')

    def test_nonreviewable_run(self):
        self.ready()
        path = self.run / 'report.json'
        data = json.loads(path.read_text())
        data['result'] = 'failed'
        path.write_text(json.dumps(data))
        self.rejected()

    def test_failed_verification(self):
        self.ready()
        path = self.run / 'report.json'
        data = json.loads(path.read_text())
        data['failed'] = ['failed verification']
        path.write_text(json.dumps(data))
        self.rejected()

    def test_missing_success_check(self):
        self.ready()
        path = self.run / 'report.json'
        data = json.loads(path.read_text())
        data['passed'].remove('privacy')
        path.write_text(json.dumps(data))
        self.rejected()

    def test_candidate_hash_mismatch(self):
        self.ready()
        self.candidate.write_bytes(self.original + b'Changed\n')
        self.rejected()

    def test_missing_candidate(self):
        self.ready()
        self.candidate.unlink()
        self.rejected()

    def test_symlink_candidate(self):
        self.ready()
        other = self.root / 'same-content.md'
        other.write_bytes(self.original)
        self.candidate.unlink()
        self.candidate.symlink_to(other)
        self.rejected()

    def test_hardlink_candidate(self):
        self.ready()
        os.link(self.candidate, self.root / 'alias.md')
        self.rejected()

    def test_directory_candidate(self):
        self.ready()
        self.candidate.unlink()
        self.candidate.mkdir()
        self.rejected()

    def test_existing_canonical(self):
        self.ready()
        self.target.parent.mkdir()
        self.target.write_text('Existing human profile')
        self.assertEqual(self.promotion(), 1)
        self.assertEqual(self.target.read_text(), 'Existing human profile')
        self.assertEqual(self.report()['result'], 'failed')

    def test_symlink_canonical_parent_cannot_write_outside_scope(self):
        self.ready()
        outside = self.root / 'outside'
        outside.mkdir()
        self.target.parent.symlink_to(outside, target_is_directory=True)
        self.rejected()
        self.assertEqual(list(outside.iterdir()), [])

    def test_dangling_canonical_symlink(self):
        self.ready()
        self.target.parent.mkdir()
        self.target.symlink_to(self.root / 'missing')
        self.rejected()
        self.assertTrue(self.target.is_symlink())

    def test_unexpected_mind_change_since_generation(self):
        self.ready()
        (self.root / 'Mind/unexpected.md').write_text('Changed')
        self.assertIn('Mind/unexpected.md', self.rejected()['unexpected'])

    def test_unexpected_horus_work_change_since_generation(self):
        self.ready()
        (self.root / 'Work/Projects/horus/new.txt').write_text('Changed')
        self.rejected()

    def test_proposal_change(self):
        self.ready()
        (self.root / wm.INPUTS[5]).write_text('Changed proposal')
        self.rejected()

    def test_tool_updates_between_workflows_are_allowed(self):
        self.ready()
        (self.repo / 'wm.py').write_text('Implementation updated before promotion')
        self.assertEqual(self.promotion(), 0)

    def test_tool_update_during_promotion_blocks_write(self):
        self.ready()
        original = wm.git_checks
        def change(root):
            (self.repo / 'unexpected.py').write_text('Concurrent change')
            return original(root)
        with patch.object(wm, 'git_checks', side_effect=change):
            self.rejected()

    def test_policy_failure_even_with_updated_candidate_hash(self):
        self.ready()
        self.candidate.write_text(self.original.decode().replace(wm.POLICIES['privacy'][1], 'Removed'))
        path = self.run / 'report.json'
        data = json.loads(path.read_text())
        data['candidate_sha256'] = wm.digest(self.candidate.read_text())
        path.write_text(json.dumps(data))
        self.rejected()

    def test_invalid_response_policy(self):
        self.ready()
        path = self.run / 'response.json'
        data = json.loads(path.read_text())
        data['policies']['privacy'] = 'unsafe'
        path.write_text(json.dumps(data))
        self.rejected()

    def test_unknown_candidate_metadata_fails_closed(self):
        self.ready()
        with self.assertRaises(wm.Rejected):
            wm.canonicalize(self.original.decode() + '\nThis candidate awaits promotion.\n')

    def test_all_known_metadata_transformations_preserve_other_text(self):
        self.ready()
        candidate = self.original.decode()
        for old, _ in wm.PROMOTION_REPLACEMENTS[3:]:
            candidate += '\n' + old + '\n'
        canonical, edits = wm.canonicalize(candidate)
        base, _ = wm.canonicalize(self.original.decode())
        expected_tail = ''.join('\n' + new + '\n' for _, new in wm.PROMOTION_REPLACEMENTS[3:])
        self.assertEqual(canonical, base + expected_tail)
        self.assertEqual(len(edits), len(wm.PROMOTION_REPLACEMENTS))
        self.assertNotIn('non-canonical', canonical)
        self.assertNotIn('candidate', canonical.lower())

    def test_git_failure_before_write(self):
        self.ready()
        with patch.object(wm, 'git_checks', side_effect=wm.Rejected('Git failed')):
            self.rejected()

    def test_git_failure_after_write_reports_failure_without_deletion(self):
        self.ready()
        with patch.object(wm, 'git_checks', side_effect=['passed', wm.Rejected('Git failed')]):
            self.assertEqual(self.promotion(), 1)
        self.assertTrue(self.target.exists())
        self.assertEqual(self.report()['result'], 'failed')
        self.assertEqual(self.candidate.read_bytes(), self.original)

    def test_unexpected_change_after_write(self):
        self.ready()
        original = wm.create_profile
        def changed(root, text):
            original(root, text)
            (self.root / 'Mind/unexpected.md').write_text('Concurrent change')
        with patch.object(wm, 'create_profile', side_effect=changed):
            self.assertEqual(self.promotion(), 1)
        result = self.report()
        self.assertEqual(result['result'], 'failed')
        self.assertIn('Mind/unexpected.md', result['unexpected'])
        self.assertNotIn('no_unexpected_changes', result['passed'])

    def test_readback_mismatch(self):
        self.ready()
        original = wm.create_profile
        def changed(root, text):
            original(root, text + 'Unexpected\n')
        with patch.object(wm, 'create_profile', side_effect=changed):
            self.assertEqual(self.promotion(), 1)
        self.assertEqual(self.report()['result'], 'failed')

    def test_replay_preserves_canonical_and_creates_new_attempt_report(self):
        self.ready()
        self.assertEqual(self.promotion(), 0)
        original = self.target.read_bytes()
        self.assertEqual(self.promotion(), 1)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(len(list((self.repo / '.promotions').glob('*.json'))), 2)

    def test_late_change_during_reporting_corrects_both_reports(self):
        self.ready()
        original = wm.snapshot
        def changed(root, ignored=()):
            if any(str(p).endswith('.json') and '.promotions/' in str(p) for p in ignored):
                (self.root / 'Mind/late.md').write_text('Late change')
            return original(root, ignored)
        with patch.object(wm, 'snapshot', side_effect=changed):
            self.assertEqual(self.promotion(), 1)
        result = self.report()
        self.assertEqual(result['result'], 'failed')
        self.assertIn('Mind/late.md', result['unexpected'])
        human = next((self.repo / '.promotions').glob('*.md')).read_text()
        self.assertIn('promotion: failed', human)
        self.assertNotIn('no_unexpected_changes', result['passed'])

    def test_late_canonical_mutation_cannot_report_success(self):
        self.ready()
        original = wm.snapshot
        def changed(root, ignored=()):
            if any('.promotions/' in str(p) for p in ignored):
                self.target.write_text('Changed after initial readback')
            return original(root, ignored)
        with patch.object(wm, 'snapshot', side_effect=changed):
            self.assertEqual(self.promotion(), 1)
        self.assertEqual(self.report()['result'], 'failed')

    def test_candidate_change_after_write_is_reported(self):
        self.ready()
        original = wm.create_profile
        def changed(root, text):
            original(root, text)
            self.candidate.write_bytes(self.original + b'Changed\n')
        with patch.object(wm, 'create_profile', side_effect=changed):
            self.assertEqual(self.promotion(), 1)
        result = self.report()
        self.assertEqual(result['result'], 'failed')
        self.assertIn(wm.CANDIDATE, result['unexpected'])

    def test_non_lf_candidate_hash_is_not_normalized(self):
        self.ready()
        self.candidate.write_bytes(self.original.replace(b'\n', b'\r\n'))
        self.rejected()

    def test_canonical_created_at_write_time_is_not_overwritten(self):
        self.ready()
        original = wm.create_profile
        def occupied(root, text):
            self.target.parent.mkdir()
            self.target.write_text('Concurrent human profile')
            original(root, text)
        with patch.object(wm, 'create_profile', side_effect=occupied):
            self.assertEqual(self.promotion(), 1)
        self.assertEqual(self.target.read_text(), 'Concurrent human profile')
        self.assertEqual(self.report()['result'], 'failed')

    def test_report_write_failure_returns_nonzero(self):
        self.ready()
        with patch.object(wm, 'write_promotion_reports', side_effect=OSError('Unavailable')):
            self.assertEqual(self.promotion(), 1)
        self.assertEqual(self.candidate.read_bytes(), self.original)

    def test_actual_git_diff_check_and_index_preserved(self):
        subprocess.run(['git', 'init', '-q', str(self.root / 'Mind')], check=True)
        self.ready()
        before = wm.snapshot(self.root / 'Mind/.git')
        self.assertEqual(self.promotion(), 0)
        self.assertEqual(wm.changes(before, wm.snapshot(self.root / 'Mind/.git')), [])
        self.assertTrue(self.report()['mind_diff_check'].startswith('passed:'))


if __name__ == '__main__':
    unittest.main()

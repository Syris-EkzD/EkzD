import json
import unittest
from unittest import mock

import test_session_contract as fixtures
from ekzd import handoff, session
from ekzd.core import HarnessError
from phase3_helpers import freeze


class HandoffStartTests(unittest.TestCase):
    setUp = fixtures.SessionContractTests.setUp
    git = fixtures.SessionContractTests.git
    commit = fixtures.SessionContractTests.commit

    def test_start_publishes_complete_bound_archive_and_recovers_deleted_archive(self):
        state = freeze(self.root)
        archive = self.root / state['handoff']['path']
        original = archive.read_bytes()
        archive.unlink()
        self.assertEqual(archive, session.export_handoff(self.root))
        self.assertEqual(original, archive.read_bytes())
        # Neither mutable inputs nor currently installed runtime are consulted.
        (self.root / '.ekzd/local/draft.toml').write_text('invalid task')
        self.policy.write_text('invalid project')
        with mock.patch.object(handoff, 'capture_runtime', side_effect=AssertionError('new runtime')):
            self.assertEqual(original, session.export_handoff(self.root).read_bytes())

    def test_failed_runtime_or_archive_or_state_publication_leaves_no_active_session(self):
        for target in ('build_payload', 'archive_bytes'):
            with mock.patch.object(handoff, target, side_effect=OSError('simulated failure')):
                with self.assertRaises(OSError):
                    freeze(self.root)
            self.assertIsNone(session.read_state(self.root))
            self.assertEqual([], list((self.root / '.ekzd/local/handoffs').iterdir()))
        with mock.patch.object(session.os, 'replace', side_effect=OSError('state publication failed')):
            with self.assertRaises(OSError):
                freeze(self.root)
        self.assertIsNone(session.read_state(self.root))
        self.assertEqual([], list((self.root / '.ekzd/local/handoffs').iterdir()))

    def test_failed_new_start_preserves_previous_finished_or_aborted_record(self):
        freeze(self.root)
        session.abort_session(self.root)
        before = (self.root / session.STATE).read_bytes()
        with mock.patch.object(session, 'write_state', side_effect=OSError('state failure')):
            with self.assertRaises(OSError):
                freeze(self.root, objective='Different task')
        self.assertEqual(before, (self.root / session.STATE).read_bytes())
        self.assertEqual('aborted', session.read_state(self.root)['status'])

    def test_foreign_handoff_identity_and_output_overwrite_are_rejected(self):
        state = freeze(self.root)
        state['handoff']['path'] = '.ekzd/local/foreign.zip'
        session.write_state(self.root, state)
        with self.assertRaisesRegex(HarnessError, 'handoff identity'):
            session.export_handoff(self.root)

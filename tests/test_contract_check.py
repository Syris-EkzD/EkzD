import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ekzd import contract_check
from ekzd.contract import canonical_bytes, compose_contract
from ekzd.core import HarnessError
from ekzd.identity import runtime_identity
from ekzd.worker import run_worker_check
from test_contract import project, task


class FrozenCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        for args in (("init", "-qb", "main"), ("config", "user.name", "Test"), ("config", "user.email", "test@example.invalid")):
            self.git(*args)
        (self.root / "file").write_text("good")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.baseline = self.git("rev-parse", "HEAD")
        self.git("switch", "-qc", "feat/task")
        self.path = Path(self.temp.name) / "contract.json"

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.PIPE, text=True).strip()

    def freeze(self, steps=None):
        p = project()
        if steps:
            p["verification"] = steps
        t = task()
        t["include"] = ["*"]
        c = compose_contract(p, t, self.baseline, runtime_identity())
        self.path.write_bytes(canonical_bytes(c))
        return c

    def test_clean_and_dirty_modes_bind_initial_state(self):
        self.freeze()
        self.assertTrue(run_worker_check(self.root, self.path, final=True)["ready"])
        (self.root / "file").write_text("dirty")
        self.assertTrue(run_worker_check(self.root, self.path)["ready"])
        self.assertFalse(run_worker_check(self.root, self.path, final=True)["ready"])

    def test_precheck_to_evaluator_mutation_is_not_rebound(self):
        self.freeze()
        original = contract_check.evaluate_candidate
        def mutate(*args, **kwargs):
            (self.root / "file").write_text("different")
            return original(*args, **kwargs)
        with mock.patch.object(contract_check, "evaluate_candidate", side_effect=mutate):
            result = run_worker_check(self.root, self.path, final=True)
        self.assertFalse(result["ready"])
        self.assertTrue(result["git"]["clean"])
        self.assertIn("between evaluation boundaries", json.dumps(result))

    def test_authority_mutation_stops_subsequent_commands(self):
        self.freeze([
            dict(name="change contract", command=["python3", "-c", f"open({str(self.path)!r}, 'w').write('changed')"]),
            dict(name="must not run", command=["python3", "-c", "open('later', 'w').write('bad')"]),
        ])
        result = run_worker_check(self.root, self.path, final=True)
        self.assertFalse(result["ready"])
        self.assertFalse((self.root / "later").exists())
        self.assertIn("Frozen contract changed", json.dumps(result))

    def test_runtime_mismatch_stops_before_commands(self):
        c = self.freeze([dict(name="never", command=["python3", "-c", "open('later', 'w').write('bad')"])])
        c["ekzd"]["build_sha256"] = "0" * 64
        self.path.write_bytes(canonical_bytes(c))
        self.assertFalse(run_worker_check(self.root, self.path)["ready"])
        self.assertFalse((self.root / "later").exists())

    def test_remotes_are_not_authority(self):
        self.freeze()
        for url in ("https://example.invalid/a", "git@example.invalid:a.git"):
            self.git("config", "remote.origin.url", url)
            self.assertTrue(run_worker_check(self.root, self.path, final=True)["ready"])

    def test_missing_baseline_is_unavailable_before_verifiers(self):
        c = self.freeze([dict(name='never', command=['python3', '-c', "open('later','w').write('bad')"])])
        c['baseline'] = '0' * 40
        self.path.write_bytes(canonical_bytes(c))
        result = run_worker_check(self.root, self.path, final=True)
        self.assertEqual('UNAVAILABLE', result['overall_status'])
        self.assertFalse((self.root / 'later').exists())

    def test_unavailable_runtime_blocks_before_verifiers(self):
        from ekzd.identity import BuildIdentityUnavailable
        self.freeze([dict(name='never', command=['python3', '-c', "open('later','w').write('bad')"])])
        with mock.patch.object(contract_check, 'runtime_identity', side_effect=BuildIdentityUnavailable('missing source')):
            result = run_worker_check(self.root, self.path, final=True)
        self.assertEqual('UNAVAILABLE', result['overall_status'])
        self.assertFalse((self.root / 'later').exists())

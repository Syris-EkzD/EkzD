from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ekzd import core, worker, session
import ekzd.evaluation as evaluation
from ekzd.evaluation import OUTPUT_LIMIT, VerificationStep, evaluate_candidate, run_verification_step
from ekzd.workflow import export_contract
from phase3_helpers import freeze


class EvaluationPhase2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "EkzD Test")
        self.git("config", "user.email", "ekzd@example.invalid")
        (self.root / ".ekzd").mkdir()
        (self.root / ".gitignore").write_text(".ekzd/local/\n", encoding="utf-8")
        (self.root / "allowed.txt").write_text("baseline\n", encoding="utf-8")
        self.config = self.root / ".ekzd/project.toml"
        self.task = self.base / "task.toml"

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True).stdout.strip()

    def commit(self, message: str) -> None:
        self.git("add", ".")
        self.git("commit", "-q", "-m", message)

    def toml_array(self, values: list[str]) -> str:
        return "[" + ", ".join(json.dumps(value) for value in values) + "]"

    def write_config(self, steps: list[tuple[str, list[str], str, int]]) -> None:
        lines = ['schema_version = 2', 'name = "Demo"']
        for name, command, cwd, timeout in steps:
            lines += [
                "",
                "[[verification]]",
                f"name = {json.dumps(name)}",
                f"command = {self.toml_array(command)}",
                f"cwd = {json.dumps(cwd)}",
                f"timeout_seconds = {timeout}",
            ]
        self.config.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def freeze(self, steps: list[tuple[str, list[str], str, int]]) -> None:
        if not steps:
            steps = [("baseline", [sys.executable, "-c", "pass"], ".", 30)]
        self.write_config(steps)
        self.commit("baseline")
        freeze(self.root, sources=["allowed.txt"])
        self.task.write_text(export_contract(self.root))
        self.git("checkout", "-q", "-b", "feat/task")

    def classifications(self) -> tuple[list[str], list[str], dict]:
        verification = session.verify_session(self.root)
        maintainer = [s['status'] for s in verification['steps'] if s['name'].startswith('verification:') or s['name'] == 'state-binding']
        checked = worker.run_worker_check(self.root, self.task, final=True)
        worker_steps = [s['status'] for s in checked['checks'] if s['name'].startswith('verification:') or s['name'] == 'state-binding']
        return maintainer, worker_steps, checked

    def test_worker_and_maintainer_share_success_nonzero_missing_timeout_and_cwd_classifications(self) -> None:
        bad_cwd = "missing-dir"
        self.freeze(
            [
                ("ok", [sys.executable, "-c", "print('ok')"], ".", 30),
                ("fail", [sys.executable, "-c", "raise SystemExit(3)"], ".", 30),
                ("missing", ["ekzd-definitely-missing-executable"], ".", 30),
                ("bad-cwd", [sys.executable, "-c", "print('nope')"], bad_cwd, 30),
                ("timeout", [sys.executable, "-c", "import time; time.sleep(5)"], ".", 1),
            ]
        )

        maintainer, worker_steps, checked = self.classifications()

        self.assertEqual(["PASS", "FAIL", "UNAVAILABLE", "UNAVAILABLE", "FAIL"], maintainer[:5])
        self.assertEqual(maintainer, worker_steps)
        self.assertFalse(checked["ready"])

    def test_shared_clean_final_candidate_passes(self) -> None:
        self.freeze([("ok", [sys.executable, "-c", "print('ok')"], ".", 30)])

        maintainer, worker_steps, checked = self.classifications()

        self.assertTrue(session.read_state(self.root)["verification"]["passed"])
        self.assertTrue(checked["ready"])
        self.assertEqual(["PASS"], maintainer[:1])
        self.assertEqual(["PASS"], worker_steps[:1])

    def test_shared_unsupported_repository_state_is_unavailable(self) -> None:
        self.freeze([("ok", [sys.executable, "-c", "print('ok')"], ".", 30)])
        self.git("config", "core.sparseCheckout", "true")

        self.assertFalse(session.verify_session(self.root)['passed'])
        checked = worker.run_worker_check(self.root, self.task, final=True)
        self.assertFalse(checked["ready"])
        self.assertIn("Sparse checkout", json.dumps(checked))

    def test_mutation_stops_later_commands_in_shared_evaluator(self) -> None:
        later = self.root / "later-ran"
        self.freeze(
            [
                ("mutate", [sys.executable, "-c", "open('allowed.txt','w').write('changed\\n')"], ".", 30),
                ("later", [sys.executable, "-c", "open('later-ran','w').write('bad')"], ".", 30),
            ]
        )

        result = evaluate_candidate(
            self.root,
            (
                VerificationStep("mutate", (sys.executable, "-c", "open('allowed.txt','w').write('changed\\n')")),
                VerificationStep("later", (sys.executable, "-c", "open('later-ran','w').write('bad')")),
            ),
            mode="development",
        )

        self.assertEqual("FAIL", result.status)
        self.assertEqual(["mutate", "state-binding"], [step.name for step in result.steps])
        self.assertFalse(later.exists())

    def test_ordinary_failure_continues_when_candidate_remains_trusted(self) -> None:
        self.freeze([])
        result = evaluate_candidate(
            self.root,
            (
                VerificationStep("fail", (sys.executable, "-c", "raise SystemExit(4)")),
                VerificationStep("later", (sys.executable, "-c", "print('ran')")),
            ),
            mode="development",
        )

        self.assertEqual(["FAIL", "PASS", "PASS"], [step.status for step in result.steps])
        self.assertEqual(["fail", "later", "state-binding"], [step.name for step in result.steps])

    def test_timeout_kills_child_before_it_can_mutate_candidate(self) -> None:
        marker = self.root / "allowed.txt"
        child = "import time; time.sleep(1.5); open('allowed.txt','w').write('late mutation\\n')"
        parent = (
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "time.sleep(5)"
        )
        self.freeze([])

        result = evaluate_candidate(
            self.root,
            (VerificationStep("timeout", (sys.executable, "-c", parent), timeout_seconds=1),),
            mode="development",
        )
        time.sleep(1.0)

        self.assertEqual("FAIL", result.status)
        self.assertTrue(result.steps[0].timed_out)
        self.assertEqual("baseline\n", marker.read_text(encoding="utf-8"))

    def test_timeout_escalates_sigterm_resistant_child_before_late_mutation(self) -> None:
        marker = self.root / "allowed.txt"
        child = (
            "import signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(2.0); "
            "open('allowed.txt','w').write('late mutation\\n')"
        )
        parent = (
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "time.sleep(5)"
        )
        self.freeze([])

        result = evaluate_candidate(
            self.root,
            (VerificationStep("timeout", (sys.executable, "-c", parent), timeout_seconds=1),),
            mode="development",
        )
        time.sleep(1.5)

        self.assertEqual("FAIL", result.status)
        self.assertTrue(result.steps[0].timed_out)
        self.assertIsNone(result.steps[0].cleanup_error)
        self.assertEqual("baseline\n", marker.read_text(encoding="utf-8"))

    def test_parent_exit_before_descendant_still_obeys_timeout_deadline(self) -> None:
        child = "import time; time.sleep(3)"
        parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}])"
        self.freeze([])

        started = time.monotonic()
        result = run_verification_step(
            self.root,
            VerificationStep("orphaned-pipe", (sys.executable, "-c", parent), timeout_seconds=1),
        )
        elapsed = time.monotonic() - started

        self.assertEqual("FAIL", result.status)
        self.assertTrue(result.timed_out)
        self.assertLess(elapsed, 2.5)

    def test_parent_completion_with_live_descendant_is_not_trustworthy_pass(self) -> None:
        marker = self.root / "allowed.txt"
        child = "import time; time.sleep(1.5); open('allowed.txt','w').write('late mutation\\n')"
        parent = (
            "import subprocess, sys; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}], "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
        )
        self.freeze([])

        result = run_verification_step(
            self.root,
            VerificationStep("live-descendant", (sys.executable, "-c", parent), timeout_seconds=5),
        )
        time.sleep(1.8)

        self.assertEqual("FAIL", result.status)
        self.assertFalse(result.timed_out)
        self.assertIn("process-group members", result.message)
        self.assertEqual("baseline\n", marker.read_text(encoding="utf-8"))

    def test_owned_process_group_cleanup_kills_descendant_after_parent_exit(self) -> None:
        marker = self.root / "allowed.txt"
        child = "import time; time.sleep(1.5); open('allowed.txt','w').write('late mutation\\n')"
        parent = (
            "import subprocess, sys; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}], "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
        )
        self.freeze([])
        process = subprocess.Popen(
            [sys.executable, "-c", parent],
            cwd=self.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        pgid = process.pid
        process.wait(timeout=2)
        self.assertTrue(evaluation._process_group_active(pgid))

        evaluation._cleanup_owned_process_group(process, pgid)
        time.sleep(1.8)

        self.assertFalse(evaluation._process_group_active(pgid))
        self.assertEqual("baseline\n", marker.read_text(encoding="utf-8"))

    def test_keyboard_interrupt_always_invokes_owned_group_cleanup(self) -> None:
        self.freeze([])
        process = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        process.wait(timeout=2)
        with (
            mock.patch.object(evaluation.subprocess, "Popen", return_value=process),
            mock.patch.object(evaluation.os, "getpgid", return_value=process.pid),
            mock.patch.object(evaluation.os, "set_blocking"),
            mock.patch.object(evaluation.selectors, "DefaultSelector") as selector_factory,
            mock.patch.object(evaluation, "_cleanup_owned_process_group") as cleanup,
        ):
            selector = selector_factory.return_value
            selector.get_map.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                run_verification_step(
                    self.root,
                    VerificationStep("interrupted", (sys.executable, "-c", "pass"), timeout_seconds=5),
                )

        cleanup.assert_called_once_with(process, process.pid)

    def test_closed_pipes_waits_for_process_without_timeout(self) -> None:
        self.freeze([])
        code = (
            "import os, time; "
            "os.close(1); os.close(2); "
            "time.sleep(0.2)"
        )
        started = time.monotonic()

        result = run_verification_step(
            self.root,
            VerificationStep("closed-pipes", (sys.executable, "-c", code), timeout_seconds=2),
        )
        elapsed = time.monotonic() - started

        self.assertEqual("PASS", result.status)
        self.assertFalse(result.timed_out)
        self.assertGreaterEqual(elapsed, 0.15)
        self.assertLess(elapsed, 1.5)

    def test_large_output_is_bounded_and_marked_truncated(self) -> None:
        self.freeze([])
        code = "import sys; sys.stdout.buffer.write(b'a'*20000); sys.stderr.buffer.write(b'b'*20000)"

        result = run_verification_step(self.root, VerificationStep("loud", (sys.executable, "-c", code)))

        self.assertEqual("PASS", result.status)
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), OUTPUT_LIMIT + 128)
        self.assertIn("truncated by EkzD", result.stdout)

    def test_invalid_utf8_output_uses_replacement(self) -> None:
        self.freeze([])
        code = "import sys; sys.stdout.buffer.write(b'valid\\xfftail')"

        result = run_verification_step(self.root, VerificationStep("bytes", (sys.executable, "-c", code)))

        self.assertEqual("PASS", result.status)
        self.assertIn("valid\ufffdtail", result.stdout)




if __name__ == "__main__":
    unittest.main()

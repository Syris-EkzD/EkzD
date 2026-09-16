from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ekzd import core, worker
from ekzd.evaluation import OUTPUT_LIMIT, VerificationStep, evaluate_candidate, run_verification_step
from ekzd.workflow import build_worker_task_manifest, start_reproducible_session


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
        (self.root / ".gitignore").write_text(".ekzd/session.json\n", encoding="utf-8")
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
        lines = [
            "schema_version = 1",
            "",
            "[project]",
            'name = "Demo"',
            "",
            "[sources]",
            'paths = ["allowed.txt"]',
            "",
            "[scope]",
            'include = ["*"]',
            "exclude = []",
            "constraints = []",
            "",
            "[authority]",
            "may = []",
            "requires_approval = []",
            "may_not = []",
            "",
            "[session]",
            "max_commits = 20",
            "",
            "[acceptance]",
            'criteria = ["verification"]',
            "",
            "[verification]",
        ]
        for name, command, cwd, timeout in steps:
            lines += [
                "",
                "[[verification.steps]]",
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
        start_reproducible_session(self.root, "phase 2", implementation_branch="feat/task")
        self.task.write_text(build_worker_task_manifest(self.root))
        self.git("checkout", "-q", "-b", "feat/task")

    def classifications(self) -> tuple[list[str], list[str], dict]:
        verification = core.verify_session(self.root)
        maintainer = [step.get("status", "PASS" if step["passed"] else "FAIL") for step in verification["steps"]]
        checked = worker.run_worker_check(self.root, self.task, final=True)
        worker_steps = [
            item["status"]
            for item in checked["checks"]
            if item["name"].startswith("project:") or item["name"] == "state-binding"
        ]
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
        self.assertEqual(maintainer, worker_steps[:5])
        self.assertFalse(checked["ready"])

    def test_shared_clean_final_candidate_passes(self) -> None:
        self.freeze([("ok", [sys.executable, "-c", "print('ok')"], ".", 30)])

        maintainer, worker_steps, checked = self.classifications()

        self.assertTrue(core.read_state(self.root)["verification"]["passed"])
        self.assertTrue(checked["ready"])
        self.assertEqual(["PASS"], maintainer[:1])
        self.assertEqual(["PASS"], worker_steps[:1])

    def test_shared_unsupported_repository_state_is_unavailable(self) -> None:
        self.freeze([("ok", [sys.executable, "-c", "print('ok')"], ".", 30)])
        self.git("config", "core.sparseCheckout", "true")

        with self.assertRaisesRegex(core.HarnessError, "Sparse checkout"):
            core.verify_session(self.root)
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

    def test_project_config_rejects_unknown_keys(self) -> None:
        self.write_config([])
        data = self.config.read_text(encoding="utf-8")
        cases = [
            (data.replace("\n[project]\n", "\nunknown = true\n\n[project]\n"), "project configuration contains unknown key: unknown"),
            (data.replace("exclude = []", "excludes = []"), "scope contains unknown key: excludes"),
            (data + '\n[[verification.steps]]\nname = "bad"\ncommand = ["true"]\ntimeout_secondz = 1\n', "verification.steps[0] contains unknown key"),
        ]
        for text, message in cases:
            with self.subTest(message=message):
                self.config.write_text(text, encoding="utf-8")
                with self.assertRaises(core.HarnessError) as raised:
                    core.load_config(self.root)
                self.assertIn(message, str(raised.exception))

    def test_task_manifest_rejects_unknown_keys(self) -> None:
        self.write_config([("baseline", [sys.executable, "-c", "pass"], ".", 30)])
        self.commit("baseline")
        start_reproducible_session(self.root, "phase 2", implementation_branch="feat/task")
        manifest = build_worker_task_manifest(self.root)
        cases = [
            (manifest.replace("\nobjective = ", "\nunknown = true\nobjective = ", 1), "task manifest contains unknown key"),
            (manifest.replace("exclude = []", "excludes = []"), "task scope contains unknown key: excludes"),
            (manifest + '\n[[verification.steps]]\nname = "bad"\ncommand = ["true"]\ntimeout_secondz = 1\n', "verification.steps[0] contains unknown key"),
        ]
        for text, message in cases:
            with self.subTest(message=message):
                self.task.write_text(text, encoding="utf-8")
                checked = worker.run_worker_check(self.root, self.task, final=True)
                self.assertFalse(checked["ready"])
                self.assertIn(message, json.dumps(checked))


if __name__ == "__main__":
    unittest.main()

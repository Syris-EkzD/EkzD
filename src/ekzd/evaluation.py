from __future__ import annotations

import errno
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from .candidate import Candidate, capture_candidate, require_final
from .core import HarnessError

OUTPUT_LIMIT = 8192
_OUTPUT_EDGE = OUTPUT_LIMIT // 2
_TERMINATE_GRACE_SECONDS = 0.5

EvaluationMode = Literal["development", "final"]
StepStatus = Literal["PASS", "FAIL", "UNAVAILABLE"]


@dataclass(frozen=True)
class VerificationStep:
    name: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: int = 600
    prefix: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.prefix}:{self.name}" if self.prefix else self.name


@dataclass(frozen=True)
class StepEvidence:
    name: str
    command: tuple[str, ...]
    cwd: str
    status: StepStatus
    message: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    launch_error: str | None = None
    mutation_error: str | None = None
    cleanup_error: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    @property
    def trust_lost(self) -> bool:
        return self.mutation_error is not None or self.cleanup_error is not None

    def maintainer_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "command": list(self.command),
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "passed": self.passed,
            "status": self.status,
        }
        if self.stdout_truncated:
            result["stdout_truncated"] = True
        if self.stderr_truncated:
            result["stderr_truncated"] = True
        if self.timed_out:
            result["timed_out"] = True
        if self.launch_error is not None:
            result["launch_error"] = self.launch_error
        if self.mutation_error is not None:
            result["candidate_error"] = self.mutation_error
        if self.cleanup_error is not None:
            result["cleanup_error"] = self.cleanup_error
        return result

    def details(self) -> dict[str, Any]:
        details: dict[str, Any] = {"command": list(self.command), "cwd": self.cwd}
        if self.exit_code is not None:
            details["returncode"] = self.exit_code
        if self.stdout:
            details["stdout"] = self.stdout
        if self.stderr:
            details["stderr"] = self.stderr
        if self.stdout_truncated:
            details["stdout_truncated"] = True
        if self.stderr_truncated:
            details["stderr_truncated"] = True
        if self.timed_out:
            details["timed_out"] = True
        if self.launch_error is not None:
            details["launch_error"] = self.launch_error
        if self.mutation_error is not None:
            details["candidate_error"] = self.mutation_error
        if self.cleanup_error is not None:
            details["cleanup_error"] = self.cleanup_error
        return details


@dataclass(frozen=True)
class EvaluationResult:
    mode: EvaluationMode
    status: StepStatus
    candidate: Candidate | None
    steps: tuple[StepEvidence, ...]
    final_binding_error: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


class _RetainedOutput:
    def __init__(self) -> None:
        self.total = 0
        self.head = bytearray()
        self.tail = bytearray()

    @property
    def truncated(self) -> bool:
        return self.total > OUTPUT_LIMIT

    def append(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if len(self.head) < _OUTPUT_EDGE:
            space = _OUTPUT_EDGE - len(self.head)
            self.head.extend(chunk[:space])
            chunk = chunk[space:]
        if chunk:
            self.tail.extend(chunk)
            if len(self.tail) > _OUTPUT_EDGE:
                del self.tail[: len(self.tail) - _OUTPUT_EDGE]

    def text(self) -> str:
        if not self.truncated:
            payload = bytes(self.head + self.tail)
        else:
            marker = f"\n[... {self.total - len(self.head) - len(self.tail)} bytes truncated by EkzD ...]\n".encode()
            payload = bytes(self.head) + marker + bytes(self.tail)
        return payload.decode("utf-8", errors="replace")


def _resolve_cwd(root: Path, cwd: str) -> Path:
    try:
        resolved_root = root.resolve()
        command_cwd = (root / cwd).resolve()
    except OSError as exc:
        raise HarnessError(f"Configured working directory is unusable: {cwd}: {exc}") from exc
    if resolved_root not in (command_cwd, *command_cwd.parents) or not command_cwd.is_dir():
        raise HarnessError(f"Configured working directory must resolve inside the project: {cwd}")
    return command_cwd


def _terminate_process_group(process: subprocess.Popen[bytes]) -> str | None:
    try:
        pgid = os.getpgid(process.pid)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return None
        return f"Unable to inspect verifier process group: {exc}"
    for sig, deadline in ((signal.SIGTERM, time.monotonic() + _TERMINATE_GRACE_SECONDS), (signal.SIGKILL, None)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return None
        except OSError as exc:
            return f"Unable to signal verifier process group: {exc}"
        if deadline is None:
            try:
                process.wait(timeout=_TERMINATE_GRACE_SECONDS)
                return None
            except subprocess.TimeoutExpired:
                return "Verifier process group did not exit after SIGKILL."
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return None
            time.sleep(0.01)
    return None


def run_verification_step(root: Path, step: VerificationStep) -> StepEvidence:
    try:
        command_cwd = _resolve_cwd(root, step.cwd)
    except HarnessError as exc:
        return StepEvidence(
            name=step.display_name,
            command=step.command,
            cwd=step.cwd,
            status="UNAVAILABLE",
            message=str(exc),
            launch_error=str(exc),
        )

    stdout = _RetainedOutput()
    stderr = _RetainedOutput()
    selector = selectors.DefaultSelector()
    process: subprocess.Popen[bytes] | None = None
    timed_out = False
    cleanup_error: str | None = None
    try:
        try:
            process = subprocess.Popen(
                list(step.command),
                cwd=command_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return StepEvidence(
                name=step.display_name,
                command=step.command,
                cwd=step.cwd,
                status="UNAVAILABLE",
                message=f"Required executable is unavailable: {step.command[0]}",
                launch_error=str(exc),
            )
        except OSError as exc:
            return StepEvidence(
                name=step.display_name,
                command=step.command,
                cwd=step.cwd,
                status="UNAVAILABLE",
                message=f"Unable to execute verification command: {exc}",
                launch_error=str(exc),
            )

        assert process.stdout is not None
        assert process.stderr is not None
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ, stdout)
        selector.register(process.stderr, selectors.EVENT_READ, stderr)
        deadline = time.monotonic() + step.timeout_seconds
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0 and process.poll() is None:
                timed_out = True
                cleanup_error = _terminate_process_group(process)
            events = selector.select(max(0.0, min(0.05, remaining)) if not timed_out else 0.05)
            if not events and timed_out and process.poll() is not None:
                # Keep draining briefly after the process exits; EOF unregisters pipes.
                pass
            for key, _ in events:
                stream = key.fileobj
                chunk = stream.read(65536)
                if chunk:
                    key.data.append(chunk)
                else:
                    selector.unregister(stream)
            if timed_out and process.poll() is not None and not events:
                for key in list(selector.get_map().values()):
                    stream = key.fileobj
                    chunk = stream.read()
                    if chunk:
                        key.data.append(chunk)
                    selector.unregister(stream)
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            _terminate_process_group(process)
        raise
    finally:
        selector.close()
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()

    exit_code = None if process is None else process.poll()
    if process is not None and exit_code is None:
        exit_code = process.wait()
    if timed_out:
        return StepEvidence(
            name=step.display_name,
            command=step.command,
            cwd=step.cwd,
            status="FAIL",
            message=f"Verification command timed out after {step.timeout_seconds} seconds.",
            exit_code=exit_code,
            stdout=stdout.text(),
            stderr=stderr.text(),
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
            timed_out=True,
            cleanup_error=cleanup_error,
        )
    status: StepStatus = "PASS" if exit_code == 0 else "FAIL"
    message = "Verification command passed." if exit_code == 0 else f"Verification command exited with status {exit_code}."
    return StepEvidence(
        name=step.display_name,
        command=step.command,
        cwd=step.cwd,
        status=status,
        message=message,
        exit_code=exit_code,
        stdout=stdout.text(),
        stderr=stderr.text(),
        stdout_truncated=stdout.truncated,
        stderr_truncated=stderr.truncated,
    )


def evaluate_candidate(
    root: Path,
    steps: Iterable[VerificationStep],
    *,
    mode: EvaluationMode,
    implementation_branch: str | None = None,
) -> EvaluationResult:
    collected: list[StepEvidence] = []
    try:
        candidate = capture_candidate(root)
    except HarnessError as exc:
        return EvaluationResult(
            mode=mode,
            status="UNAVAILABLE",
            candidate=None,
            steps=(
                StepEvidence(
                    name="candidate-state",
                    command=(),
                    cwd=".",
                    status="UNAVAILABLE",
                    message=str(exc),
                    launch_error=str(exc),
                ),
            ),
            final_binding_error=str(exc),
        )
    if mode == "final":
        try:
            if implementation_branch is None:
                raise HarnessError("Final evaluation requires a declared implementation branch.")
            require_final(candidate, implementation_branch)
        except HarnessError as exc:
            return EvaluationResult(
                mode=mode,
                status="FAIL",
                candidate=candidate,
                steps=(
                    StepEvidence(
                        name="candidate-state",
                        command=(),
                        cwd=".",
                        status="FAIL",
                        message=str(exc),
                        mutation_error=str(exc),
                    ),
                ),
                final_binding_error=str(exc),
            )

    for step in steps:
        try:
            if capture_candidate(root) != candidate:
                message = "Candidate changed between evaluation boundaries."
                collected.append(
                    StepEvidence(
                        name="state-binding",
                        command=(),
                        cwd=".",
                        status="FAIL",
                        message=message,
                        mutation_error=message,
                    )
                )
                break
        except HarnessError as exc:
            message = f"Candidate trust lost before verification command: {exc}"
            collected.append(
                StepEvidence(
                    name="state-binding",
                    command=(),
                    cwd=".",
                    status="UNAVAILABLE",
                    message=message,
                    mutation_error=message,
                )
            )
            break

        evidence = run_verification_step(root, step)
        try:
            if capture_candidate(root) != candidate:
                message = "Verification command changed candidate state; mutation left untouched."
                evidence = StepEvidence(
                    name=evidence.name,
                    command=evidence.command,
                    cwd=evidence.cwd,
                    status="FAIL",
                    message=evidence.message,
                    exit_code=evidence.exit_code,
                    stdout=evidence.stdout,
                    stderr=evidence.stderr,
                    stdout_truncated=evidence.stdout_truncated,
                    stderr_truncated=evidence.stderr_truncated,
                    timed_out=evidence.timed_out,
                    launch_error=evidence.launch_error,
                    mutation_error=message,
                    cleanup_error=evidence.cleanup_error,
                )
        except HarnessError as exc:
            message = f"Candidate trust lost after verification command: {exc}"
            evidence = StepEvidence(
                name=evidence.name,
                command=evidence.command,
                cwd=evidence.cwd,
                status="UNAVAILABLE",
                message=evidence.message,
                exit_code=evidence.exit_code,
                stdout=evidence.stdout,
                stderr=evidence.stderr,
                stdout_truncated=evidence.stdout_truncated,
                stderr_truncated=evidence.stderr_truncated,
                timed_out=evidence.timed_out,
                launch_error=evidence.launch_error,
                mutation_error=message,
                cleanup_error=evidence.cleanup_error,
            )
        collected.append(evidence)
        if evidence.trust_lost:
            break

    final_binding_error: str | None = None
    try:
        if capture_candidate(root) != candidate:
            final_binding_error = "Candidate changed during verification; result remains bound to the initial state."
            collected.append(
                StepEvidence(
                    name="state-binding",
                    command=(),
                    cwd=".",
                    status="FAIL",
                    message=final_binding_error,
                    mutation_error=final_binding_error,
                )
            )
    except HarnessError as exc:
        final_binding_error = f"Unable to bind final Git/worktree state: {exc}"
        collected.append(
            StepEvidence(
                name="state-binding",
                command=(),
                cwd=".",
                status="UNAVAILABLE",
                message=final_binding_error,
                mutation_error=final_binding_error,
            )
        )
    if final_binding_error is None and not any(step.trust_lost for step in collected):
        collected.append(
            StepEvidence(
                name="state-binding",
                command=(),
                cwd=".",
                status="PASS",
                message="Verification preserved the captured candidate state.",
            )
        )

    statuses = [step.status for step in collected if step.name != "state-binding" or step.status != "PASS"]
    if any(status == "FAIL" for status in statuses):
        status: StepStatus = "FAIL"
    elif any(status == "UNAVAILABLE" for status in statuses):
        status = "UNAVAILABLE"
    else:
        status = "PASS"
    return EvaluationResult(mode=mode, status=status, candidate=candidate, steps=tuple(collected), final_binding_error=final_binding_error)

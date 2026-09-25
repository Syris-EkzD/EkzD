"""Deterministic handoff construction and retained-payload re-export."""
import hashlib
import io
import json
import os
import shlex
import shutil
import tempfile
import zipfile
from pathlib import Path

from .contract import canonical_bytes, contract_id, validate_contract
from .core import HarnessError
from .launcher import validate_payload
from .runtime import capture_runtime
from .identity import BuildIdentityUnavailable


def _instruction_list(items: list[str]) -> str:
    if not items:
        return "- None declared."
    return "\n".join(f"- {json.dumps(item, ensure_ascii=False)}" for item in items)


def instructions(contract: dict) -> bytes:
    sources = ""
    if contract['sources']:
        sources = f'''\n## Declared source/reference files

{_instruction_list(contract['sources'])}
'''
    guidance = ""
    if contract['guidance']:
        guidance = f'''\n## Project/task guidance

{_instruction_list(contract['guidance'])}
'''
    text = f'''# EkzD worker task

`contract.json` is the canonical machine-readable authority. This document is its deterministic
worker-facing rendering.

## Task identity

- Contract ID: `{contract_id(contract)}`
- Objective: {json.dumps(contract['objective'], ensure_ascii=False)}
- Exact baseline: `{contract['baseline']}`
- Implementation branch: {json.dumps(contract['branch'], ensure_ascii=False)}
- Effective commit ceiling: {contract['max_commits']} commits

## Allowed scope

{_instruction_list(contract['scope']['include'])}

## Excluded paths

{_instruction_list(contract['scope']['exclude'])}

## Protected paths

{_instruction_list(contract['scope']['protected'])}

## Acceptance criteria

{_instruction_list(contract['acceptance'])}
{sources}{guidance}
## Verification boundary

EkzD performs structural verification only: frozen baseline, declared branch, scope, protected and
excluded paths, history, commit ceiling, candidate cleanliness, and exact candidate binding.
Project-specific formatting, linting, static analysis, tests, builds, dependency checks, and similar
correctness checks belong to CI or another external verification system and are not executed by EkzD.

## Worker flow

Obtain the target repository checkout separately; this archive contains no repository or tools.
Extract this handoff outside tracked candidate files. Create the declared implementation branch at
the exact baseline, then run:

    git switch -c {shlex.quote(contract['branch'])} {contract['baseline']}
    python3 /path/to/handoff/run.py prepare
    python3 /path/to/handoff/run.py check

Implement and self-review. Run iterative `check` commands to catch scope, history, branch, and
authority violations while working. Commit at small, meaningful implementation milestones. Keep
each commit logically focused; avoid batching unrelated or excessive work into one large commit and
avoid noisy WIP, checkpoint, or fixup commits. Prefer Conventional Commit messages such as
`feat(scope): description` when a meaningful scope exists; omit the scope when it adds no useful
context. Use as many coherent commits as the task reasonably requires, up to the effective commit
ceiling. EkzD mechanically enforces commit-count and history rules; it does not judge commit meaning.

When the candidate is complete, clean, and committed, run:

    python3 /path/to/handoff/run.py check --final

After the final EkzD PASS, publish/update the implementation branch and pull request using the
available external workflow. Do not treat the implementation session as complete until all required
CI checks for the exact published candidate have completed successfully. If CI fails, inspect the
CI evidence, fix the candidate within the frozen authority, rerun EkzD checks, publish the new exact
candidate, and wait for CI again. If CI cannot be observed or required checks cannot run, report the
blocker instead of claiming completion.

## Escalation rules

- Do not widen the frozen task authority.
- Do not install missing capabilities through EkzD. Stop and report the exact blocker.
- Stop and report when requirements are ambiguous or the task requires unauthorized scope.
- Do not merge. The maintainer independently verifies and retains final merge authority.

## Required worker return

- Exact branch.
- Exact commit.
- Contract ID.
- EkzD final structural check result.
- Required CI status/checks for the exact returned commit.
- Changed files.
- Unresolved issues and review notes.

Worker success is not acceptance; the maintainer independently verifies and explicitly accepts the
candidate.
'''
    return text.encode('utf-8')


def build_payload(contract: dict) -> dict[str, bytes]:
    validate_contract(contract)
    try:
        runtime = capture_runtime(contract['ekzd'])
    except BuildIdentityUnavailable as exc:
        raise HarnessError(f"Runtime capture unavailable: {exc}") from exc
    files = {'contract.json': canonical_bytes(contract), 'instructions.md': instructions(contract),
             'run.py': runtime['launcher.py']}
    files.update({'runtime/ekzd/' + name: content for name, content in runtime.items()})
    manifest = {'handoff_version': 1, 'contract_id': contract_id(contract), 'ekzd': contract['ekzd'],
                'files': {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}}
    files['handoff.json'] = canonical_bytes(manifest)
    return dict(sorted(files.items()))


def archive_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            parts = Path(name).parts
            if not name or name.startswith('/') or '..' in parts or '\\' in name or Path(name).as_posix() != name:
                raise HarnessError('Unsafe archive member path.')
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, content)
    return buffer.getvalue()


def retain_handoff(root: Path, contract: dict) -> tuple[dict, bool]:
    """Publish a complete directory before session publication; caller rolls back."""
    parent = root / '.ekzd/local/handoffs'
    if parent.is_symlink():
        raise HarnessError('Handoff directory must not be a symlink.')
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / contract_id(contract)
    files = build_payload(contract)
    archive = archive_bytes(files)
    metadata = {'path': f'.ekzd/local/handoffs/{contract_id(contract)}/handoff.zip',
                'sha256': hashlib.sha256(archive).hexdigest(),
                'payload_sha256': hashlib.sha256(files['handoff.json']).hexdigest()}
    if destination.exists() or destination.is_symlink():
        if reexport_bytes(root, contract, metadata) != archive:
            raise HarnessError('Existing retained handoff differs; remove it before refreezing.')
        write_archive(destination / 'handoff.zip', archive)
        return metadata, False
    temporary = Path(tempfile.mkdtemp(prefix='.handoff-', dir=parent))
    try:
        payload = temporary / 'payload'
        for name, content in files.items():
            path = payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        try:
            validate_payload(payload)
        except (ValueError, KeyError, TypeError) as exc:
            raise HarnessError(f'Constructed handoff failed validation: {exc}') from exc
        (temporary / 'handoff.zip').write_bytes(archive)
        for path in temporary.rglob('*'):
            if path.is_file():
                with path.open('rb') as handle:
                    os.fsync(handle.fileno())
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return metadata, True


def reexport_bytes(root: Path, contract: dict, metadata: dict) -> bytes:
    expected_path = f'.ekzd/local/handoffs/{contract_id(contract)}/handoff.zip'
    if metadata.get('path') != expected_path:
        raise HarnessError('Handoff does not belong to the active contract.')
    directory = root / Path(expected_path).parent
    for path in (root / '.ekzd', root / '.ekzd/local', directory.parent, directory, directory / 'payload'):
        if path.is_symlink():
            raise HarnessError('Retained handoff must not traverse symlinks.')
    payload = directory / 'payload'
    try:
        manifest, raw = validate_payload(payload)
        if (hashlib.sha256(raw).hexdigest() != metadata['payload_sha256'] or manifest['ekzd'] != contract['ekzd']
                or manifest['contract_id'] != contract_id(contract)
                or (payload / 'contract.json').read_bytes() != canonical_bytes(contract)):
            raise HarnessError('Retained payload differs from the active frozen contract.')
        files = {name: (payload / name).read_bytes() for name in manifest['files']}
        files['handoff.json'] = raw
        archive = archive_bytes(files)
        if hashlib.sha256(archive).hexdigest() != metadata['sha256']:
            raise HarnessError('Re-export differs from the frozen handoff archive identity.')
        return archive
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HarnessError(f'Unable to validate retained handoff: {exc}') from exc


def write_archive(path: Path, content: bytes) -> None:
    """Atomic publication; never overwrite different user output or follow links."""
    if path.is_symlink():
        raise HarnessError('Archive output must not be a symlink.')
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise HarnessError('Archive output already exists with different content.')
        return
    fd, name = tempfile.mkstemp(prefix='.handoff-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(name, path)
    finally:
        os.unlink(name)

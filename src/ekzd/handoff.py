"""Deterministic handoff construction and retained-payload re-export."""
import hashlib
import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from .contract import canonical_bytes, contract_id, validate_contract
from .core import HarnessError
from .launcher import validate_payload
from .runtime import capture_runtime
from .identity import BuildIdentityUnavailable


def instructions(contract: dict) -> bytes:
    text = f'''# EkzD worker task

Contract ID: {contract_id(contract)}
Objective: {contract['objective']}
Baseline: {contract['baseline']}
Implementation branch: {contract['branch']}
Allowed scope: {', '.join(contract['scope']['include'])}
Excluded/protected: {', '.join(contract['scope']['exclude'] + contract['scope']['protected'])}

Obtain the target repository checkout separately; this archive contains no repository or tools.
Externally create/check out the declared branch at the exact baseline. Extract this handoff outside
tracked candidate files. From the target checkout, run:

    python3 /path/to/handoff/run.py prepare
    python3 /path/to/handoff/run.py check
    # Implement, repeatedly check, fix and self-review; commit the candidate.
    python3 /path/to/handoff/run.py check --final

Read contract.json for complete authority and consult sources at the baseline. Preparation probes
capabilities only; correctness verification may depend on files the task will create. Missing
capabilities block work: report the exact blocker, do not install tools through EkzD or widen authority.
Use logical commits within the frozen budget. Do not merge. Return the exact branch/commit,
contract ID, check results, changed files and remaining review notes. The maintainer independently
verifies and explicitly accepts; worker success is not acceptance.
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

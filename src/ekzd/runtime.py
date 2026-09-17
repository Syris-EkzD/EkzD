"""Capture the executing Python package, never the target Git checkout."""
import os
import stat
import tempfile
from pathlib import Path

from . import identity
from .core import HarnessError


def capture_runtime(expected: dict[str, str]) -> dict[str, bytes]:
    root = Path(identity.__file__).parent
    if root.is_symlink() or identity.runtime_identity() != expected:
        raise HarnessError("Executing runtime differs from the frozen identity.")
    captured: dict[str, bytes] = {}
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise HarnessError("Runtime capture rejects symlinks.")
                if entry.is_dir(follow_symlinks=False):
                    if entry.name != '__pycache__':
                        stack.append(Path(entry.path))
                elif not entry.is_file(follow_symlinks=False):
                    raise HarnessError("Runtime capture rejects special files.")
                elif entry.name.endswith('.py'):
                    path = Path(entry.path)
                    before = path.stat(follow_symlinks=False)
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, 'rb') as handle:
                        current = os.fstat(handle.fileno())
                        if not stat.S_ISREG(current.st_mode) or current != before:
                            raise HarnessError("Runtime source changed during capture.")
                        content = handle.read()
                    # atime may change on read; source identity fields must not.
                    after = path.stat(follow_symlinks=False)
                    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise HarnessError("Runtime source changed during capture.")
                    captured[path.relative_to(root).as_posix()] = content
    # Use the existing fingerprint implementation on the copied bytes as well.
    with tempfile.TemporaryDirectory(prefix='ekzd-runtime-') as directory:
        copied = Path(directory)
        for name, content in captured.items():
            path = copied / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        if identity.build_sha256(copied) != expected['build_sha256'] or identity.runtime_identity() != expected:
            raise HarnessError("Copied runtime does not match the frozen identity.")
    return dict(sorted(captured.items()))

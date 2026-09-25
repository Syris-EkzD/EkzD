"""Copied verbatim to handoff/run.py. Bootstrap before trusting bundled imports."""
import sys


def bootstrap():
    # Re-exec before importing anything from site-packages or PYTHONPATH. -S
    # also prevents sitecustomize/.pth execution in the isolated interpreter.
    if not sys.flags.isolated or not sys.flags.no_site:
        import os
        os.execv(sys.executable, [sys.executable, '-I', '-S', __file__, *sys.argv[1:]])
    if sys.platform != 'linux' or sys.version_info < (3, 11):
        raise ValueError('Worker requires Linux and Python 3.11 or newer.')
    sys.dont_write_bytecode = True
    from pathlib import Path
    root = Path(__file__).absolute().parent
    manifest, raw = validate_payload(root)
    runtime = root / 'runtime'
    sys.path.insert(0, str(runtime))
    import ekzd
    if Path(ekzd.__file__).resolve() != (runtime / 'ekzd/__init__.py').resolve():
        raise ValueError('Imported EkzD is not the bundled runtime.')
    from ekzd.identity import runtime_identity
    if runtime_identity() != manifest['ekzd']:
        raise ValueError('Bundled runtime identity mismatch.')
    from ekzd.portable import main

    def guard():
        _, current = validate_payload(root)
        if current != raw:
            raise ValueError('Handoff manifest changed during structural checking.')
    return main(root, manifest, guard)


def validate_payload(root):
    """Pre-import integrity validation. No EkzD or third-party imports."""
    import hashlib
    import json
    import os
    import re
    from pathlib import PurePosixPath
    if root.is_symlink():
        raise ValueError('Handoff root must not be a symlink.')
    raw = (root / 'handoff.json').read_bytes()
    manifest = json.loads(raw)
    canonical = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    if raw != canonical or not isinstance(manifest, dict) or set(manifest) != {'handoff_version', 'contract_id', 'ekzd', 'files'}:
        raise ValueError('Malformed handoff manifest.')
    if type(manifest['handoff_version']) is not int or manifest['handoff_version'] != 1:
        raise ValueError('Unsupported handoff format.')
    files = manifest['files']
    if not isinstance(files, dict) or not {'contract.json', 'instructions.md', 'run.py', 'runtime/ekzd/__init__.py'} <= set(files):
        raise ValueError('Missing handoff members.')
    for name, digest in files.items():
        parts = PurePosixPath(name).parts
        if (not name or name.startswith('/') or '..' in parts or str(PurePosixPath(name)) != name
                or not (name in {'contract.json', 'instructions.md', 'run.py'} or name.startswith('runtime/ekzd/') and name.endswith('.py'))
                or not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None):
            raise ValueError('Unsafe or unsupported handoff member.')
    actual = set()
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise ValueError('Handoff symlinks are unsupported.')
                if entry.is_dir(follow_symlinks=False):
                    stack.append(root / os.path.relpath(entry.path, root))
                elif entry.is_file(follow_symlinks=False):
                    name = os.path.relpath(entry.path, root).replace(os.sep, '/')
                    actual.add(name)
                    if name != 'handoff.json' and (name not in files or hashlib.sha256((root / name).read_bytes()).hexdigest() != files[name]):
                        raise ValueError('Handoff member changed: ' + name)
                else:
                    raise ValueError('Handoff special files are unsupported.')
    if actual != set(files) | {'handoff.json'}:
        raise ValueError('Handoff member set mismatch.')
    if hashlib.sha256((root / 'contract.json').read_bytes()).hexdigest() != manifest['contract_id']:
        raise ValueError('Handoff contract identity mismatch.')
    return manifest, raw


if __name__ == '__main__':
    try:
        raise SystemExit(bootstrap())
    except (OSError, ValueError, ImportError, KeyError, TypeError) as exc:
        print('EkzD handoff: FAIL: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)

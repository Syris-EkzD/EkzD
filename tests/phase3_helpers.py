import json
import subprocess
from pathlib import Path

from ekzd import session
from ekzd.contract import canonical_bytes


def project_text(*, exclude=(), protected=(), guidance=()):
    lines = ['schema_version = 4', 'name = "Demo"',
             f'exclude = {json.dumps(list(exclude))}', f'protected = {json.dumps(list(protected))}',
             f'guidance = {json.dumps(list(guidance))}']
    return '\n'.join(lines) + '\n'


def task_text(*, branch='feat/task', include=('*',), exclude=(), sources=(), acceptance=('Works',), guidance=(), objective='Implement'):
    lines = ['schema_version = 3', f'objective = {json.dumps(objective)}', f'branch = {json.dumps(branch)}']
    for name, value in [('include', include), ('exclude', exclude), ('sources', sources), ('acceptance', acceptance), ('guidance', guidance)]:
        lines.append(f'{name} = {json.dumps(list(value))}')
    return '\n'.join(lines) + '\n'


def freeze(root: Path, *, branch='feat/task', **kwargs):
    draft = root / '.ekzd/local/draft.toml'
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(task_text(branch=branch, **kwargs))
    return session.start_session(root, draft)


def export(root, path):
    path.write_bytes(canonical_bytes(session.active_state(root)['contract']))


def export_contract(root):
    from ekzd.contract import canonical_bytes
    from ekzd.session import active_state
    return canonical_bytes(active_state(root)['contract']).decode('utf-8')

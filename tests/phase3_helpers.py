import json
import subprocess
from pathlib import Path

from ekzd import session
from ekzd.contract import canonical_bytes


def project_text(*, max_commits=20, exclude=(), protected=(), guidance=(), commands=None):
    lines = ['schema_version = 2', 'name = "Demo"', f'max_commits = {max_commits}',
             f'exclude = {json.dumps(list(exclude))}', f'protected = {json.dumps(list(protected))}',
             f'guidance = {json.dumps(list(guidance))}']
    for index, command in enumerate(commands or [['python3', '-c', 'pass']]):
        lines += ['[[verification]]', f'name = "step-{index}"', f'command = {json.dumps(command)}']
    return '\n'.join(lines) + '\n'


def task_text(*, branch='feat/task', include=('*',), exclude=(), sources=(), acceptance=('Works',), guidance=(), max_commits=None, objective='Implement'):
    lines = ['schema_version = 1', f'objective = {json.dumps(objective)}', f'branch = {json.dumps(branch)}']
    for name, value in [('include', include), ('exclude', exclude), ('sources', sources), ('acceptance', acceptance), ('guidance', guidance)]:
        lines.append(f'{name} = {json.dumps(list(value))}')
    if max_commits is not None:
        lines.append(f'max_commits = {max_commits}')
    return '\n'.join(lines) + '\n'


def freeze(root: Path, *, branch='feat/task', **kwargs):
    draft = root / '.ekzd/local/draft.toml'
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(task_text(branch=branch, **kwargs))
    return session.start_session(root, draft)


def export(root, path):
    path.write_bytes(canonical_bytes(session.active_state(root)['contract']))

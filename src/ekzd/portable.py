"""Worker adapter, imported only after the launcher's payload validation."""
import argparse
import json
from pathlib import Path

from .check import check_result, empty_worker_result, finalize_worker_result, render_worker_check
from .contract import contract_id, load_contract
from .contract_check import check_contract
from .core import HarnessError, find_root


def main(handoff: Path, manifest: dict, payload_guard, argv=None) -> int:
    parser = argparse.ArgumentParser(prog='run.py', description='Pinned EkzD worker runtime.')
    parser.add_argument('command', choices=['prepare', 'check'])
    parser.add_argument('--final', action='store_true')
    parser.add_argument('--json', action='store_true', dest='as_json')
    args = parser.parse_args(argv)
    if args.command == 'prepare' and args.final:
        parser.error('--final applies to check only')

    def guard():
        try:
            payload_guard()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HarnessError(f'Handoff trust lost: {exc}') from exc

    try:
        contract = load_contract(handoff / 'contract.json')
        if contract_id(contract) != manifest['contract_id'] or contract['ekzd'] != manifest['ekzd']:
            raise HarnessError('Handoff and contract identity differ.')
        result = check_contract(find_root(), contract, final=args.final, prepare=args.command == 'prepare', authority_guard=guard)
        result['mode'] = 'prepare' if args.command == 'prepare' else result['mode']
    except (HarnessError, OSError) as exc:
        result = empty_worker_result(final=args.final)
        result['checks'].append(check_result('preparation', 'UNAVAILABLE', str(exc)))
        finalize_worker_result(result)
    if args.as_json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(render_worker_check(result), end='')
    return 0 if result['ready'] else 1

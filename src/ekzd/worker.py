"""Stateless adapter for a canonical frozen contract supplied by the maintainer."""
from pathlib import Path

from .check import check_result, empty_worker_result, finalize_worker_result
from .contract import canonical_bytes, load_contract
from .contract_check import check_contract
from .core import HarnessError


def run_worker_check(root: Path, contract_path: Path, *, final: bool = False, prepare: bool = False, authority_guard=None) -> dict:
    try:
        contract = load_contract(contract_path)
        payload = canonical_bytes(contract)

        def guard():
            if authority_guard is not None:
                authority_guard()
            try:
                current = contract_path.read_bytes()
            except OSError as exc:
                raise HarnessError(f"Frozen contract became unavailable: {exc}") from exc
            if current != payload:
                raise HarnessError("Frozen contract changed during structural checking.")

        result = check_contract(root, contract, final=final, prepare=prepare, authority_guard=guard)
        result["contract_path"] = str(contract_path.resolve())
        return result
    except (HarnessError, OSError) as exc:
        result = empty_worker_result(contract_path, final=final)
        result["checks"].append(check_result("contract", "FAIL", str(exc)))
        return finalize_worker_result(result)

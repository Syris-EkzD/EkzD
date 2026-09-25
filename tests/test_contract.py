import copy
import unittest

from ekzd.contract import canonical_bytes, compose_contract, contract_id, parse_project, parse_task, validate_contract
from ekzd.core import HarnessError


def project(**overrides):
    return dict(schema_version=4, name="Example", **overrides)


def task(**overrides):
    return dict(schema_version=3, objective="Implement", branch="feat/task", include=["src/**"], acceptance=["Works"], **overrides)


class ContractTests(unittest.TestCase):
    def compose(self, p=None, t=None):
        return compose_contract(p or project(), t or task(), "a" * 40, dict(version="0.3.0", build_sha256="b" * 64))

    def test_composition_is_additive_and_cannot_weaken_project(self):
        c = self.compose(project(protected=["secret/"], exclude=["private/**"], guidance=["permanent"]),
                         task(exclude=["generated/**"], guidance=["task"]))
        self.assertIn("secret/", c["scope"]["protected"])
        self.assertEqual(["generated/**", "private/**"], c["scope"]["exclude"])
        self.assertEqual(["permanent", "task"], c["guidance"])
        self.assertNotIn("verification", c)
        self.assertNotIn("readiness", c)

    def test_determinism_normalizes_defaults_and_scope_order(self):
        a = self.compose(project(exclude=["b", "a", "b"]))
        b = self.compose(project(exclude=["a", "b"]))
        self.assertEqual(canonical_bytes(a), canonical_bytes(b))
        self.assertEqual(contract_id(a), contract_id(b))
        self.assertTrue(canonical_bytes(a).endswith(b"}\n"))
        b["objective"] = "Different"
        self.assertNotEqual(contract_id(a), contract_id(b))

    def test_commit_safety_ceiling_is_fixed_and_not_author_configurable(self):
        self.assertEqual(50, self.compose()["max_commits"])
        for parser, authored in (
                (parse_project, project(max_commits=5)),
                (parse_task, task(max_commits=8))):
            with self.subTest(parser=parser.__name__), self.assertRaisesRegex(HarnessError, "max_commits"):
                parser(authored)
        for value in (49, 51):
            contract = self.compose()
            contract["max_commits"] = value
            with self.subTest(value=value), self.assertRaisesRegex(
                    HarnessError, "fixed 50-commit safety ceiling"):
                validate_contract(contract)

    def test_closed_schemas_and_versions(self):
        for parser, data, key in ((parse_project, project(), "excludes"), (parse_task, task(), "protected"),
                                  (parse_task, task(), "ekzd"), (validate_contract, self.compose(), "repository")):
            data[key] = []
            with self.assertRaisesRegex(HarnessError, key):
                parser(data)
        for parser, data in ((parse_project, project()), (parse_task, task())):
            data["schema_version"] -= 1
            with self.assertRaisesRegex(HarnessError, "Unsupported"):
                parser(data)
        for value in (0, 1, 2, 4, True, 3.0):
            c = self.compose()
            c["contract_version"] = value
            with self.assertRaisesRegex(HarnessError, "Unsupported"):
                validate_contract(c)

    def test_project_and_task_reject_removed_command_fields(self):
        for parser, authored in (
                (parse_project, project(verification=[])),
                (parse_project, project(readiness=[])),
                (parse_task, task(verification=[])),
                (parse_task, task(readiness=[]))):
            with self.subTest(parser=parser.__name__, field=next(reversed(authored))), self.assertRaisesRegex(
                    HarnessError, "verification|readiness"):
                parser(authored)

    def test_invalid_paths_fail(self):
        with self.assertRaises(HarnessError):
            parse_task(task(sources=["../secret"]))

    def test_contract_has_no_command_plan_and_requires_protection(self):
        c = self.compose()
        self.assertNotIn("verification", c)
        self.assertNotIn("readiness", c)
        for field in ("verification", "readiness"):
            changed = copy.deepcopy(c)
            changed[field] = []
            with self.subTest(field=field), self.assertRaisesRegex(HarnessError, field):
                validate_contract(changed)
        c = self.compose()
        c["scope"]["protected"] = []
        with self.assertRaises(HarnessError):
            validate_contract(c)

    def test_canonical_json_rejects_formatting_duplicate_keys_and_legacy_inputs(self):
        import json
        import tempfile
        from pathlib import Path
        from ekzd.contract import load_contract
        c = self.compose()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'contract.json'
            for raw in (json.dumps(c).encode(), canonical_bytes(c).rstrip(b'\n'),
                        b'{"contract_version":2,' + canonical_bytes(c)[1:], b'schema_version = 3\n'):
                path.write_bytes(raw)
                with self.assertRaises(HarnessError):
                    load_contract(path)
            c['objective'] = 'Validación — 注册'
            path.write_bytes(canonical_bytes(c))
            self.assertEqual(c, load_contract(path))
            self.assertIn('注册'.encode(), path.read_bytes())

    def test_unknown_fields_in_each_nested_contract_object(self):
        for key in ('project', 'scope', 'ekzd'):
            c = self.compose()
            c[key]['typo'] = []
            with self.assertRaisesRegex(HarnessError, 'typo'):
                validate_contract(c)

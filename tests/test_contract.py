import copy
import unittest

from ekzd.contract import canonical_bytes, compose_contract, contract_id, parse_project, parse_task, validate_contract
from ekzd.core import HarnessError


def project(**overrides):
    return dict(schema_version=2, name="Example", verification=[dict(name="test", command=["python3", "-c", "pass"])], **overrides)


def task(**overrides):
    return dict(schema_version=1, objective="Implement", branch="feat/task", include=["src/**"], acceptance=["Works"], **overrides)


class ContractTests(unittest.TestCase):
    def compose(self, p=None, t=None):
        return compose_contract(p or project(), t or task(), "a" * 40, dict(version="0.3.0", build_sha256="b" * 64))

    def test_composition_is_additive_and_cannot_weaken_project(self):
        c = self.compose(project(protected=["secret/"], exclude=["private/**"], guidance=["permanent"]),
                         task(exclude=["generated/**"], guidance=["task"], verification=[dict(name="extra", command=["true"])]))
        self.assertIn("secret/", c["scope"]["protected"])
        self.assertEqual(["generated/**", "private/**"], c["scope"]["exclude"])
        self.assertEqual(["test", "extra"], [s["name"] for s in c["verification"]])
        self.assertEqual(["permanent", "task"], c["guidance"])

    def test_determinism_normalizes_defaults_and_scope_order(self):
        a = self.compose(project(exclude=["b", "a", "b"]))
        b = self.compose(project(exclude=["a", "b"], max_commits=20))
        self.assertEqual(canonical_bytes(a), canonical_bytes(b))
        self.assertEqual(contract_id(a), contract_id(b))
        self.assertTrue(canonical_bytes(a).endswith(b"}\n"))
        b["objective"] = "Different"
        self.assertNotEqual(contract_id(a), contract_id(b))

    def test_budget_default_override_and_no_global_ceiling(self):
        self.assertEqual(42, self.compose(project(max_commits=42))["max_commits"])
        self.assertEqual(80, self.compose(project(max_commits=42), task(max_commits=80))["max_commits"])
        for value in (0, -1, True, 1.5):
            with self.assertRaises(HarnessError):
                parse_task(task(max_commits=value))

    def test_closed_schemas_and_versions(self):
        for parser, data, key in ((parse_project, project(), "excludes"), (parse_task, task(), "protected"),
                                  (parse_task, task(), "ekzd"), (validate_contract, self.compose(), "repository")):
            data[key] = []
            with self.assertRaisesRegex(HarnessError, key):
                parser(data)
        for value in (0, 1, 3, True, 2.0):
            c = self.compose()
            c["contract_version"] = value
            with self.assertRaisesRegex(HarnessError, "Unsupported"):
                validate_contract(c)

    def test_verification_typos_and_invalid_paths_fail(self):
        p = project()
        p["verification"][0]["timeouts"] = 1
        with self.assertRaisesRegex(HarnessError, "timeouts"):
            parse_project(p)
        with self.assertRaises(HarnessError):
            parse_task(task(sources=["../secret"]))

    def test_contract_requires_resolved_steps_and_protection(self):
        c = self.compose()
        del c["verification"][0]["cwd"]
        with self.assertRaises(HarnessError):
            validate_contract(c)
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
                        b'{"contract_version":1,' + canonical_bytes(c)[1:], b'schema_version = 2\n'):
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
        c = self.compose()
        c['verification'][0]['timeout'] = 5
        with self.assertRaisesRegex(HarnessError, 'timeout'):
            validate_contract(c)

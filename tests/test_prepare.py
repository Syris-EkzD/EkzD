import unittest
from unittest import mock

from ekzd import contract_check
from ekzd.contract_check import check_contract
import test_contract_check as fixtures


class PreparationTests(unittest.TestCase):
    setUp = fixtures.FrozenCheckTests.setUp
    git = fixtures.FrozenCheckTests.git
    freeze = fixtures.FrozenCheckTests.freeze

    def test_prepare_requires_exact_clean_baseline(self):
        contract = self.freeze()
        self.assertTrue(check_contract(self.root, contract, final=False, prepare=True)["ready"])
        (self.root / "file").write_text("dirty", encoding="utf-8")
        self.assertFalse(check_contract(self.root, contract, final=False, prepare=True)["ready"])
        self.git("add", ".")
        self.git("commit", "-qm", "candidate")
        self.assertFalse(check_contract(self.root, contract, final=False, prepare=True)["ready"])
        self.assertTrue(check_contract(self.root, contract, final=True)["ready"])

    def test_prepare_requires_supported_linux_python_environment(self):
        contract = self.freeze()
        with mock.patch.object(contract_check.sys, "platform", "darwin"):
            result = check_contract(self.root, contract, final=False, prepare=True)
        self.assertEqual("UNAVAILABLE", result["overall_status"])
        self.assertIn("Linux and Python 3.11", str(result))

    def test_prepare_requires_git(self):
        contract = self.freeze()
        with mock.patch.object(contract_check.shutil, "which", return_value=None):
            result = check_contract(self.root, contract, final=False, prepare=True)
        self.assertEqual("UNAVAILABLE", result["overall_status"])
        self.assertIn("Required executable is unavailable: git", str(result))


if __name__ == "__main__":
    unittest.main()

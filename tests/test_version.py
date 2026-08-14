import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "bjut_auth.py"
spec = importlib.util.spec_from_file_location("bjut_auth_version", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class VersionTests(unittest.TestCase):
    def test_release_version(self):
        self.assertEqual(mod.VERSION, "0.3.3")


if __name__ == "__main__":
    unittest.main()

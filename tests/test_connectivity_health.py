import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "bjut_auth.py"
spec = importlib.util.spec_from_file_location("bjut_auth_connectivity", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ConnectivityHealthTests(unittest.TestCase):
    def test_pinned_health_endpoint_uses_curl_resolve(self):
        result = SimpleNamespace(returncode=0, stdout="204")
        with mock.patch.object(mod, "run", return_value=result) as runner:
            self.assertTrue(mod.internet_online(
                "enp4s0", "https://check.example.com/204", "203.0.113.7"
            ))
        cmd = runner.call_args.args[0]
        self.assertIn("--resolve", cmd)
        self.assertIn("check.example.com:443:203.0.113.7", cmd)
        self.assertIn("--noproxy", cmd)

    def test_invalid_pinned_address_is_rejected(self):
        with self.assertRaises(mod.AuthError):
            mod.validate_connectivity_resolve_ip("not-an-ip")

    def test_empty_pinned_address_keeps_normal_dns(self):
        result = SimpleNamespace(returncode=0, stdout="204")
        with mock.patch.object(mod, "run", return_value=result) as runner:
            self.assertTrue(mod.internet_online(
                "enp4s0", "https://check.example.com/204"
            ))
        self.assertNotIn("--resolve", runner.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "bjut_auth.py"
spec = importlib.util.spec_from_file_location("bjut_auth", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ProtocolTests(unittest.TestCase):
    def test_eportal_encrypt_matches_reference(self):
        self.assertEqual(mod.eportal_encrypt("dr1005"), "726427262623")
        self.assertEqual(mod.eportal_encrypt(mod.LGN_JS_VERSION), "2238243824")

    def test_parse_jsonp_success_and_failure(self):
        self.assertEqual(mod.parse_login_response('dr1003({"result":1,"msg":"ok"});'), (True, "ok"))
        self.assertEqual(mod.parse_login_response('dr1003({"result":0,"msga":"bad"});'), (False, "bad"))
        with self.assertRaises(mod.AuthError):
            mod.parse_login_response('dr1003({"result":2});')

    def test_virtual_interfaces_are_rejected(self):
        self.assertTrue(mod.is_virtual_interface("wg0"))
        self.assertTrue(mod.is_virtual_interface("docker0"))
        self.assertFalse(mod.is_virtual_interface("eno1"))

    def test_config_password_percent_is_literal(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.ini"
            path.write_text("[BJUT]\nusername = 1\npassword = a%b%25\n", encoding="utf-8")
            config, loaded = mod.load_config(str(path))
            self.assertEqual(loaded, path)
            self.assertEqual(config["password"], "a%b%25")

    def test_curl_config_escape(self):
        self.assertEqual(mod.curl_config_escape('a"b\\c\n'), 'a\\"b\\\\c')


if __name__ == "__main__":
    unittest.main()

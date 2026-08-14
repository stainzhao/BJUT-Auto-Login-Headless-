import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl

MODULE_PATH = Path(__file__).resolve().parents[1] / "bjut_auth.py"
spec = importlib.util.spec_from_file_location("bjut_auth", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def args(**kwargs):
    defaults = {
        "username": None,
        "password": None,
        "interface": None,
        "login_type": None,
        "allow_http_fallback": False,
        "config": None,
        "command": "doctor",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class ProtocolTests(unittest.TestCase):
    def test_eportal_encrypt_matches_reference(self):
        self.assertEqual(mod.eportal_encrypt("dr1005"), "726427262623")
        self.assertEqual(mod.eportal_encrypt(mod.LGN_JS_VERSION), "2238243824")

    def test_eportal_encrypt_unicode_uses_utf16_units(self):
        value = "中"
        expected = "{:02x}".format(ord(value) ^ mod.EPORTAL_XOR_KEY)
        self.assertEqual(mod.eportal_encrypt(value), expected)

    def test_parse_jsonp_success_and_failure(self):
        self.assertEqual(
            mod.parse_login_response('dr1003({"result":1,"msg":"ok"});'),
            (True, "ok"),
        )
        self.assertEqual(
            mod.parse_login_response('dr1003({"result":"0","msga":"bad"});'),
            (False, "bad"),
        )
        with self.assertRaises(mod.AuthError):
            mod.parse_login_response('dr1003({"result":2});')

    def test_default_success_message_matches_portal_wording(self):
        self.assertEqual(
            mod.parse_login_response('dr1003({"result":1});'),
            (True, "Portal协议认证成功！"),
        )

    def test_type1_query_matches_duplicate_lang_reference(self):
        pairs = parse_qsl(
            mod.type1_query("123", "pw", "172.19.26.49", request_id="0123"),
            keep_blank_values=True,
        )
        self.assertEqual(
            [value for key, value in pairs if key == "lang"],
            ["zh-cn", "zh"],
        )
        self.assertIn(("user_account", "123@campus"), pairs)
        self.assertIn(("wlan_user_ip", "172.19.26.49"), pairs)

    def test_type2_query_matches_duplicate_lang_reference(self):
        pairs = parse_qsl(
            mod.type2_query("123", "pw", request_id="0123"),
            keep_blank_values=True,
        )
        self.assertEqual(
            [value for key, value in pairs if key == "lang"],
            ["zh-cn", "zh"],
        )
        self.assertIn(("DDDDD", "123"), pairs)
        self.assertIn(("0MKKey", "123456"), pairs)

    def test_wired_detection_retries_transient_probe_by_default(self):
        with mock.patch.object(
            mod, "interface_is_wireless", return_value=False
        ), mock.patch.object(
            mod, "get_observed_ipv6", return_value="2001:db8::1"
        ) as observed:
            self.assertEqual(mod.detect_login_type("enp7s0", False), "3")
            observed.assert_called_once_with("enp7s0", timeout=3, retries=1)


class NetworkSelectionTests(unittest.TestCase):
    def test_virtual_interfaces_are_rejected(self):
        self.assertTrue(mod.is_virtual_interface("wg0"))
        self.assertTrue(mod.is_virtual_interface("docker0"))
        self.assertTrue(mod.is_virtual_interface("lo"))
        self.assertFalse(mod.is_virtual_interface("eno1"))

    def test_fake_ip_is_not_usable(self):
        self.assertFalse(mod.usable_ipv4("198.18.1.10"))
        self.assertFalse(mod.usable_ipv4("169.254.1.1"))
        self.assertTrue(mod.usable_ipv4("172.19.26.49"))

    def test_likely_bjut_ipv4_fallback(self):
        self.assertTrue(mod.is_likely_bjut_ipv4("172.19.26.49"))
        self.assertTrue(mod.is_likely_bjut_ipv4("10.126.63.8"))
        self.assertFalse(mod.is_likely_bjut_ipv4("192.168.1.20"))
        self.assertFalse(mod.is_likely_bjut_ipv4("10.0.0.2"))

    def test_parse_route_identity(self):
        output = "172.30.201.2 via 172.19.26.1 dev enp7s0 src 172.19.26.49 uid 0\n"
        self.assertEqual(
            mod.parse_route_identity(output),
            ("enp7s0", "172.19.26.49"),
        )

    def test_route_source_ipv4_has_priority_over_first_interface_address(self):
        with mock.patch.object(
            mod, "route_identity", return_value=("enp7s0", "172.19.26.49")
        ), mock.patch.object(
            mod,
            "global_interface_addresses",
            return_value={"enp7s0": ["10.0.0.2", "172.19.26.49"]},
        ):
            self.assertEqual(
                mod.interface_ipv4("enp7s0", mod.TYPE3_ROUTE_DEST),
                "172.19.26.49",
            )

    def test_auto_select_single_interface_without_default_route(self):
        with mock.patch.object(
            mod,
            "global_interface_addresses",
            return_value={"enp7s0": ["172.19.26.49"]},
        ), mock.patch.object(mod, "route_candidate_interfaces", return_value=[]), mock.patch.object(
            mod, "default_route_interfaces", return_value=[]
        ):
            self.assertEqual(mod.auto_select_interface(), "enp7s0")

    def test_auto_select_dual_nic_prefers_portal_route(self):
        candidates = {
            "enp7s0": ["172.19.26.49"],
            "enp8s0": ["192.168.10.20"],
        }
        with mock.patch.object(
            mod, "global_interface_addresses", return_value=candidates
        ), mock.patch.object(
            mod, "route_candidate_interfaces", return_value=["enp7s0"]
        ):
            self.assertEqual(mod.auto_select_interface(), "enp7s0")

    def test_auto_select_dual_nic_uses_campus_ipv4_when_routes_are_missing(self):
        candidates = {
            "enp7s0": ["172.19.26.49"],
            "enp8s0": ["192.168.10.20"],
        }
        with mock.patch.object(
            mod, "global_interface_addresses", return_value=candidates
        ), mock.patch.object(
            mod, "strong_route_candidate_interfaces", return_value=[]
        ), mock.patch.object(
            mod, "route_candidate_interfaces", return_value=[]
        ), mock.patch.object(
            mod, "default_route_interfaces", return_value=[]
        ):
            self.assertEqual(mod.auto_select_interface(), "enp7s0")

    def test_auto_select_ambiguous_interfaces_fails_safe(self):
        candidates = {
            "enp7s0": ["10.0.0.2"],
            "enp8s0": ["192.168.10.20"],
        }
        with mock.patch.object(
            mod, "global_interface_addresses", return_value=candidates
        ), mock.patch.object(
            mod, "route_candidate_interfaces", return_value=[]
        ), mock.patch.object(
            mod, "default_route_interfaces", return_value=[]
        ), mock.patch.object(
            mod, "_probe_candidate_interfaces", return_value=[]
        ):
            with self.assertRaisesRegex(mod.AuthError, "多个候选网卡"):
                mod.auto_select_interface()

    def test_explicit_interface_always_wins(self):
        config = {"interface": "enp8s0"}
        with mock.patch.object(mod, "interface_ipv4", return_value="192.168.1.2"), mock.patch.object(
            mod, "auto_select_interface"
        ) as auto:
            self.assertEqual(mod.resolve_interface(args(), config), "enp8s0")
            auto.assert_not_called()


class CurlTests(unittest.TestCase):
    def test_curl_config_escape(self):
        self.assertEqual(mod.curl_config_escape('a"b\\c\n'), 'a\\"b\\\\c')

    def test_split_curl_response(self):
        body, status = mod.split_curl_response(
            'dr1003({"result":1});\n__BJUT_HTTP_STATUS__:200'
        )
        self.assertEqual(status, 200)
        self.assertIn('"result":1', body)

    def test_curl_get_retries_transient_http_500(self):
        first = mock.Mock(
            returncode=0,
            stdout="oops\n__BJUT_HTTP_STATUS__:500",
            stderr="",
        )
        second = mock.Mock(
            returncode=0,
            stdout="ok\n__BJUT_HTTP_STATUS__:200",
            stderr="",
        )
        with mock.patch.object(mod, "run", side_effect=[first, second]), mock.patch.object(
            mod.time, "sleep"
        ):
            self.assertEqual(mod.curl_get("https://example.invalid", retries=1), "ok")

    def test_curl_get_does_not_retry_non_transient_404(self):
        result = mock.Mock(
            returncode=0,
            stdout="not found\n__BJUT_HTTP_STATUS__:404",
            stderr="",
        )
        with mock.patch.object(mod, "run", return_value=result) as runner:
            with self.assertRaises(mod.HttpStatusError) as ctx:
                mod.curl_get("https://example.invalid", retries=2)
            self.assertEqual(ctx.exception.status, 404)
            self.assertEqual(runner.call_count, 1)


class ConfigTests(unittest.TestCase):
    def test_config_password_percent_is_literal(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.ini"
            path.write_text(
                "[BJUT]\nusername = 1\npassword = a%b%25\n",
                encoding="utf-8",
            )
            config, loaded = mod.load_config(str(path))
            self.assertEqual(loaded, path)
            self.assertEqual(config["password"], "a%b%25")

    def test_explicit_missing_config_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing.ini"
            with self.assertRaisesRegex(mod.AuthError, "不存在"):
                mod.load_config(str(path))

    def test_unknown_config_key_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.ini"
            path.write_text(
                "[BJUT]\nusername=1\npassword=x\ninterfaec=eno1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(mod.AuthError, "未知字段"):
                mod.load_config(str(path))

    def test_invalid_boolean_is_error(self):
        with self.assertRaises(mod.AuthError):
            mod.cfg_bool({"allow_http_fallback": "flase"}, "allow_http_fallback")

    def test_invalid_connectivity_url_is_error(self):
        with self.assertRaises(mod.AuthError):
            mod.validate_connectivity_url("file:///etc/passwd")
        with self.assertRaises(mod.AuthError):
            mod.validate_connectivity_url("https://user:pass@example.com/")
        self.assertEqual(
            mod.validate_connectivity_url("https://example.com/"),
            "https://example.com/",
        )

    def test_placeholder_password_is_reported(self):
        issue = mod.credential_issue(
            args(),
            {"username": "123", "password": "change_me"},
        )
        self.assertIn("占位符", issue)

    def test_doctor_config_validation_is_not_masked_by_environment(self):
        config = {"username": "123", "password": "change_me"}
        with mock.patch.dict(
            mod.os.environ,
            {"BJUT_USERNAME": "runtime-user", "BJUT_PASSWORD": "runtime-secret"},
            clear=False,
        ):
            issue = mod.credential_issue(args(), config, config_only=True)
        self.assertIn("占位符", issue)

    def test_doctor_portal_failure_returns_nonzero(self):
        config = {
            "username": "123",
            "password": "real-secret",
            "type": "auto",
            "connectivity_url": "https://example.com/",
        }
        with mock.patch.object(mod.shutil, "which", return_value="/bin/tool"), mock.patch.object(
            mod, "global_interface_addresses", return_value={"enp7s0": ["172.19.26.49"]}
        ), mock.patch.object(
            mod, "resolve_interface", return_value="enp7s0"
        ), mock.patch.object(
            mod, "interface_is_wireless", return_value=False
        ), mock.patch.object(
            mod, "interface_ipv4", return_value="172.19.26.49"
        ), mock.patch.object(
            mod, "internet_online", return_value=False
        ), mock.patch.object(
            mod, "detect_login_type", side_effect=mod.AuthError("portal unavailable")
        ):
            self.assertEqual(mod.do_doctor(args(), config, None), 1)


if __name__ == "__main__":
    unittest.main()

import argparse
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "bjut_auth.py"
spec = importlib.util.spec_from_file_location("bjut_auth_type3_doctor", MODULE_PATH)
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


class Type3OnlineDoctorTests(unittest.TestCase):
    def test_portal_only_probe_accepts_result_zero(self):
        body = 'dr1004({"result":0,"msg":"already online"});'
        with mock.patch.object(mod, "curl_get", return_value=body):
            self.assertEqual(
                mod.get_observed_ipv6("enp7s0", allow_portal_only=True), ""
            )

    def test_login_probe_still_rejects_result_zero(self):
        body = 'dr1004({"result":0,"msg":"already online"});'
        with mock.patch.object(mod, "curl_get", return_value=body):
            with self.assertRaisesRegex(mod.AuthError, "未返回成功结果"):
                mod.get_observed_ipv6("enp7s0")

    def test_wired_detect_can_identify_portal_without_login_ready_ipv6(self):
        with mock.patch.object(
            mod, "interface_is_wireless", return_value=False
        ), mock.patch.object(
            mod, "get_observed_ipv6", return_value=""
        ) as observed:
            self.assertEqual(
                mod.detect_login_type(
                    "enp7s0", False, require_login_ready=False
                ),
                "3",
            )
            observed.assert_called_once_with(
                "enp7s0", timeout=3, retries=1, allow_portal_only=True
            )

    def test_doctor_online_does_not_require_login_ready_ipv6(self):
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
            mod, "internet_online", return_value=True
        ), mock.patch.object(
            mod, "detect_login_type", return_value="3"
        ) as detect:
            self.assertEqual(mod.do_doctor(args(), config, None), 0)
            detect.assert_called_once_with(
                "enp7s0", False, require_login_ready=False
            )

    def test_doctor_offline_still_requires_login_ready_ipv6(self):
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
            mod, "detect_login_type", side_effect=mod.AuthError("IPv6 not ready")
        ) as detect:
            self.assertEqual(mod.do_doctor(args(), config, None), 1)
            detect.assert_called_once_with(
                "enp7s0", False, require_login_ready=True
            )


if __name__ == "__main__":
    unittest.main()

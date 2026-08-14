import importlib.util
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "bjut_auth.py"
spec = importlib.util.spec_from_file_location("bjut_auth_getipv6", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Type3GetIpv6RecoveryTests(unittest.TestCase):
    def test_normal_getipv6_attempt_does_not_force_ipv4(self):
        command = mod._curl_command("enp7s0", force_ipv4=False)
        self.assertNotIn("--ipv4", command)
        self.assertIn("--interface", command)

    def test_other_portal_requests_still_force_ipv4_by_default(self):
        command = mod._curl_command("enp7s0")
        self.assertIn("--ipv4", command)

    def test_result_zero_falls_through_to_second_fixed_address(self):
        result_zero = 'dr1004({"result":0,"msg":"not ready"});'
        result_one = 'dr1004({"result":1,"ip":"2001:db8::1234"});'
        with mock.patch.object(
            mod, "_curl_get_once", side_effect=[result_zero, result_zero, result_one]
        ) as request, mock.patch.object(mod.time, "sleep"):
            self.assertEqual(
                mod.get_observed_ipv6("enp7s0", retries=0),
                "2001:db8::1234",
            )
        self.assertEqual(request.call_count, 3)
        self.assertFalse(request.call_args_list[0].kwargs["force_ipv4"])
        self.assertTrue(request.call_args_list[1].kwargs["force_ipv4"])
        self.assertTrue(request.call_args_list[2].kwargs["force_ipv4"])

    def test_portal_only_accepts_result_zero_without_extra_requests(self):
        result_zero = 'dr1004({"result":0,"msg":"already online"});'
        with mock.patch.object(
            mod, "_curl_get_once", return_value=result_zero
        ) as request:
            self.assertEqual(
                mod.get_observed_ipv6(
                    "enp7s0", retries=0, allow_portal_only=True
                ),
                "",
            )
        request.assert_called_once()

    def test_all_result_zero_errors_include_safe_gateway_message(self):
        result_zero = 'dr1004({"result":0,"msg":"ipv6 not ready"});'
        with mock.patch.object(
            mod, "_curl_get_once", return_value=result_zero
        ), mock.patch.object(mod.time, "sleep"):
            with self.assertRaises(mod.AuthError) as ctx:
                mod.get_observed_ipv6("enp7s0", retries=0)
        message = str(ctx.exception)
        self.assertIn("result=0", message)
        self.assertIn("ipv6 not ready", message)
        self.assertIn("校园固定地址", message)

    def test_dns_failure_can_recover_on_fixed_address(self):
        result_one = 'dr1004({"result":1,"ip":"2001:db8::55"});'
        with mock.patch.object(
            mod,
            "_curl_get_once",
            side_effect=[mod.AuthError("Could not resolve host"), result_one],
        ), mock.patch.object(mod.time, "sleep"):
            self.assertEqual(
                mod.get_observed_ipv6("enp7s0", retries=0),
                "2001:db8::55",
            )


if __name__ == "__main__":
    unittest.main()

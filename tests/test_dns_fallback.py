import importlib.util
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "bjut_auth.py"
spec = importlib.util.spec_from_file_location("bjut_auth", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class DnsFallbackTests(unittest.TestCase):
    def test_fixed_resolve_candidates_preserve_expected_ports(self):
        self.assertEqual(
            mod.fixed_resolve_candidates(mod.LGN_IPV6_URL),
            [
                ("lgn6.bjut.edu.cn", 443, "172.30.201.2"),
                ("lgn6.bjut.edu.cn", 443, "172.30.201.10"),
            ],
        )
        self.assertEqual(
            mod.fixed_resolve_candidates(mod.LGN_LOGIN_URL),
            [
                ("lgn.bjut.edu.cn", 802, "172.30.201.2"),
                ("lgn.bjut.edu.cn", 802, "172.30.201.10"),
            ],
        )
        self.assertEqual(
            mod.fixed_resolve_candidates(mod.WIFI_HTTPS_LOGIN),
            [("wlgn.bjut.edu.cn", 443, "10.21.251.3")],
        )

    def test_dns_failure_falls_back_to_first_fixed_ip(self):
        dns_error = mock.Mock(
            returncode=6,
            stdout="",
            stderr="curl: (6) Could not resolve host: lgn6.bjut.edu.cn",
        )
        success = mock.Mock(
            returncode=0,
            stdout="ok\n__BJUT_HTTP_STATUS__:200",
            stderr="",
        )
        with mock.patch.object(mod, "run", side_effect=[dns_error, success]) as runner:
            body = mod.curl_get(mod.LGN_IPV6_URL, interface="enp4s0", retries=0)
        self.assertEqual(body, "ok")
        second_cmd = runner.call_args_list[1].args[0]
        self.assertIn("--resolve", second_cmd)
        self.assertIn("lgn6.bjut.edu.cn:443:172.30.201.2", second_cmd)
        self.assertIn("--interface", second_cmd)
        self.assertIn("enp4s0", second_cmd)

    def test_fixed_fallback_rotates_to_secondary_ip(self):
        dns_error = mock.Mock(returncode=6, stdout="", stderr="dns failed")
        primary_error = mock.Mock(returncode=7, stdout="", stderr="connect failed")
        secondary_success = mock.Mock(
            returncode=0,
            stdout="ok\n__BJUT_HTTP_STATUS__:200",
            stderr="",
        )
        with mock.patch.object(
            mod, "run", side_effect=[dns_error, primary_error, secondary_success]
        ) as runner, mock.patch.object(mod.time, "sleep"):
            self.assertEqual(
                mod.curl_get(mod.LGN_LOGIN_URL, interface="enp4s0", retries=0),
                "ok",
            )
        second_cmd = runner.call_args_list[1].args[0]
        third_cmd = runner.call_args_list[2].args[0]
        self.assertIn("lgn.bjut.edu.cn:802:172.30.201.2", second_cmd)
        self.assertIn("lgn.bjut.edu.cn:802:172.30.201.10", third_cmd)

    def test_untrusted_host_never_gets_fixed_resolve(self):
        dns_error = mock.Mock(returncode=6, stdout="", stderr="dns failed")
        with mock.patch.object(mod, "run", return_value=dns_error) as runner:
            with self.assertRaises(mod.AuthError):
                mod.curl_get("https://example.invalid/test", retries=0)
        self.assertEqual(runner.call_count, 1)
        self.assertNotIn("--resolve", runner.call_args.args[0])

    def test_non_transient_http_error_does_not_fallback(self):
        not_found = mock.Mock(
            returncode=0,
            stdout="nope\n__BJUT_HTTP_STATUS__:404",
            stderr="",
        )
        with mock.patch.object(mod, "run", return_value=not_found) as runner:
            with self.assertRaises(mod.HttpStatusError):
                mod.curl_get(mod.LGN_IPV6_URL, retries=0)
        self.assertEqual(runner.call_count, 1)


if __name__ == "__main__":
    unittest.main()

import types
import unittest
from unittest import mock
from urllib.parse import parse_qs

import bjut_relogin as mod


class ReloginQueryTests(unittest.TestCase):
    def test_type1_logout_query_contains_account_and_client_ip(self):
        query = parse_qs(
            mod.type1_logout_query("user", "pw", "10.0.0.2", "1234")
        )
        self.assertEqual(query["callback"], ["dr1004"])
        self.assertEqual(query["user_account"], ["user@campus"])
        self.assertEqual(query["user_password"], ["pw"])
        self.assertEqual(query["wlan_user_ip"], ["10.0.0.2"])
        self.assertEqual(query["register_mode"], ["0"])

    def test_type2_logout_query_uses_drcom_logout_callback(self):
        query = parse_qs(mod.type2_logout_query("5678"))
        self.assertEqual(query["callback"], ["dr1004"])
        self.assertEqual(query["jsVersion"], ["4.1"])
        self.assertEqual(query["v"], ["5678"])

    def test_type3_logout_query_encrypts_protocol_fields(self):
        core = types.SimpleNamespace(
            LGN_JS_VERSION="4.2.2",
            LGN_PROGRAM_INDEX="program",
            LGN_PAGE_INDEX="page",
            eportal_encrypt=lambda value: f"enc:{value}",
        )
        query = parse_qs(
            mod.type3_logout_query(core, "10.0.0.2", "2001:db8::1", "9999")
        )
        self.assertEqual(query["callback"], ["enc:dr1008"])
        self.assertEqual(query["user_account"], ["enc:drcom"])
        self.assertEqual(query["wlan_user_ip"], ["enc:10.0.0.2"])
        self.assertEqual(query["wlan_user_ipv6"], ["enc:2001:db8::1"])
        self.assertEqual(query["encrypt"], ["1"])

    def test_relogin_delay_defaults_to_two_seconds(self):
        args = mod.build_parser().parse_args(["relogin"])
        self.assertEqual(args.delay, 2.0)


class ReloginFlowTests(unittest.TestCase):
    def make_core(self, online):
        return types.SimpleNamespace(
            credential_issue=mock.Mock(return_value=None),
            cfg_bool=mock.Mock(return_value=False),
            resolve_interface=mock.Mock(return_value="eth0"),
            connectivity_url=mock.Mock(return_value="http://check.invalid/204"),
            connectivity_resolve_ip=mock.Mock(return_value=""),
            internet_online=mock.Mock(side_effect=online),
            do_login=mock.Mock(return_value=True),
            confirm_online=mock.Mock(return_value=True),
        )

    def test_relogin_when_already_offline_skips_logout_and_logs_in(self):
        args = mod.build_parser().parse_args(["--type", "3", "relogin", "--delay", "0"])
        core = self.make_core([False])

        with mock.patch.object(mod, "do_logout") as logout:
            rc = mod.do_relogin(core, args, {})

        self.assertEqual(rc, 0)
        logout.assert_not_called()
        core.do_login.assert_called_once()
        core.confirm_online.assert_called_once()

    def test_relogin_when_online_still_logs_out_before_login(self):
        args = mod.build_parser().parse_args(["--type", "3", "relogin", "--delay", "0"])
        core = self.make_core([True, False])

        with mock.patch.object(
            mod, "do_logout", return_value=(True, "eth0", "3", False)
        ) as logout:
            rc = mod.do_relogin(core, args, {})

        self.assertEqual(rc, 0)
        logout.assert_called_once()
        core.do_login.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import importlib.util
import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "bjut_auth.py"
spec = importlib.util.spec_from_file_location("bjut_auth_ensure", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class EnsureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace()
        self.config = {}
        self.interface = "enp4s0"
        self.url = "https://www.baidu.com/"

    def test_portal_error_but_connectivity_recovers_is_success(self):
        stderr = io.StringIO()
        with mock.patch.object(
            mod, "do_login", side_effect=mod.AuthError("HTTP 500")
        ), mock.patch.object(
            mod, "internet_online", side_effect=[False, True]
        ) as online, mock.patch.object(mod.time, "sleep") as sleep, redirect_stderr(stderr):
            rc = mod.do_ensure_login(
                self.args, self.config, self.interface, self.url
            )
        self.assertEqual(rc, 0)
        self.assertEqual(online.call_count, 2)
        sleep.assert_called_once()
        self.assertIn("HTTP 500", stderr.getvalue())
        self.assertIn("公网已恢复", stderr.getvalue())

    def test_portal_error_and_still_offline_preserves_failure(self):
        error = mod.AuthError("HTTP 500")
        with mock.patch.object(mod, "do_login", side_effect=error), mock.patch.object(
            mod, "internet_online", return_value=False
        ) as online, mock.patch.object(mod.time, "sleep") as sleep:
            with self.assertRaises(mod.AuthError) as ctx:
                mod.do_ensure_login(
                    self.args, self.config, self.interface, self.url
                )
        self.assertIs(ctx.exception, error)
        self.assertEqual(online.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_success_response_waits_for_connectivity_propagation(self):
        with mock.patch.object(mod, "do_login", return_value=True), mock.patch.object(
            mod, "internet_online", side_effect=[False, True]
        ), mock.patch.object(mod.time, "sleep") as sleep:
            rc = mod.do_ensure_login(
                self.args, self.config, self.interface, self.url
            )
        self.assertEqual(rc, 0)
        sleep.assert_called_once()

    def test_explicit_portal_rejection_remains_failure(self):
        with mock.patch.object(mod, "do_login", return_value=False), mock.patch.object(
            mod, "internet_online"
        ) as online:
            rc = mod.do_ensure_login(
                self.args, self.config, self.interface, self.url
            )
        self.assertEqual(rc, 2)
        online.assert_not_called()


if __name__ == "__main__":
    unittest.main()

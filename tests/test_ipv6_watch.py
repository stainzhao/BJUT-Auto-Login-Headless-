import importlib.util
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "bjut_ipv6_watch.py"
spec = importlib.util.spec_from_file_location("bjut_ipv6_watch", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class IPv6WatchTests(unittest.TestCase):
    def test_parse_bjut_ipv6_addresses_filters_prefix(self):
        text = (
            "2: enp7s0 inet6 2001:da8:216:191a::1234/64 scope global dynamic\n"
            "2: enp7s0 inet6 2001:db8::1/64 scope global\n"
            "2: enp7s0 inet6 fe80::1/64 scope link\n"
        )
        self.assertEqual(
            mod.parse_bjut_ipv6_addresses(text),
            ["2001:da8:216:191a::1234"],
        )

    def test_watch_config_defaults_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.ini"
            path.write_text("[BJUT]\nusername=x\npassword=y\n", encoding="utf-8")
            cfg = mod.load_watch_config(path)
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["failures"], 2)
            self.assertEqual(cfg["cooldown_seconds"], 300)

    def test_watch_config_can_enable(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.ini"
            path.write_text(
                "[BJUT]\nusername=x\npassword=y\n\n"
                "[IPv6Watch]\nenabled=true\nfailures=3\ncooldown_seconds=60\n",
                encoding="utf-8",
            )
            cfg = mod.load_watch_config(path)
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["failures"], 3)
            self.assertEqual(cfg["cooldown_seconds"], 60)

    def test_disabled_watch_does_not_probe_or_relogin(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            core = types.SimpleNamespace()
            with mock.patch.object(mod, "ipv6_health") as health, mock.patch.object(
                mod.subprocess, "run"
            ) as runner:
                self.assertEqual(
                    mod.run_watch(
                        core,
                        {},
                        None,
                        {"enabled": False, "failures": 2, "cooldown_seconds": 300},
                        state_path=state,
                    ),
                    0,
                )
                health.assert_not_called()
                runner.assert_not_called()

    def test_ipv6_health_requires_local_portal_match(self):
        core = types.SimpleNamespace(
            AuthError=RuntimeError,
            get_observed_ipv6=lambda *a, **k: "2001:da8:216:191a::2",
        )
        with mock.patch.object(
            mod,
            "interface_bjut_ipv6_addresses",
            return_value=["2001:da8:216:191a::2"],
        ):
            self.assertEqual(
                mod.ipv6_health(core, "enp7s0"),
                (True, "2001:da8:216:191a::2"),
            )

    def test_two_failures_trigger_relogin(self):
        core = types.SimpleNamespace(
            cfg_bool=lambda *a, **k: False,
            resolve_interface=lambda *a, **k: "enp7s0",
            login_type_value=lambda config: "3",
            interface_is_wireless=lambda interface: False,
            connectivity_url=lambda config: "http://example.test/204",
            connectivity_resolve_ip=lambda config: "",
            internet_online=lambda *a, **k: True,
        )
        watch = {"enabled": True, "failures": 2, "cooldown_seconds": 300}
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            with mock.patch.object(mod, "auth_lock_busy", return_value=False), mock.patch.object(
                mod, "ipv6_health", return_value=(False, "no ipv6")
            ), mock.patch.object(
                mod, "relogin_command", return_value=["true"]
            ), mock.patch.object(
                mod.subprocess,
                "run",
                return_value=types.SimpleNamespace(returncode=0),
            ) as runner:
                self.assertEqual(
                    mod.run_watch(core, {}, None, watch, state_path=state, now=1000),
                    0,
                )
                runner.assert_not_called()
                self.assertEqual(mod.load_state(state)["failures"], 1)
                self.assertEqual(
                    mod.run_watch(core, {}, None, watch, state_path=state, now=1060),
                    0,
                )
                runner.assert_called_once()
                self.assertEqual(mod.load_state(state)["failures"], 0)

    def test_cooldown_prevents_relogin_loop(self):
        core = types.SimpleNamespace(
            cfg_bool=lambda *a, **k: False,
            resolve_interface=lambda *a, **k: "enp7s0",
            login_type_value=lambda config: "3",
            interface_is_wireless=lambda interface: False,
            connectivity_url=lambda config: "http://example.test/204",
            connectivity_resolve_ip=lambda config: "",
            internet_online=lambda *a, **k: True,
        )
        watch = {"enabled": True, "failures": 2, "cooldown_seconds": 300}
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            mod.save_state({"failures": 2, "last_attempt": 1000}, state)
            with mock.patch.object(mod, "auth_lock_busy", return_value=False), mock.patch.object(
                mod, "ipv6_health", return_value=(False, "no ipv6")
            ), mock.patch.object(mod.subprocess, "run") as runner:
                self.assertEqual(
                    mod.run_watch(core, {}, None, watch, state_path=state, now=1100),
                    0,
                )
                runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()

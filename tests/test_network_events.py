import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NetworkEventTests(unittest.TestCase):
    def test_dispatcher_uses_supported_networkmanager_events(self):
        text = (ROOT / "NetworkManager/dispatcher.d/90-bjut-auto-login").read_text(encoding="utf-8")
        for action in ("up", "dhcp4-change", "dhcp6-change", "connectivity-change", "reapply"):
            self.assertIn(action, text)
        self.assertIn("systemctl is-enabled --quiet bjut-auto-login.timer", text)
        self.assertIn("restart --no-block bjut-auto-login-event.timer", text)

    def test_event_timer_debounces_into_main_service(self):
        text = (ROOT / "systemd/bjut-auto-login-event.timer").read_text(encoding="utf-8")
        self.assertIn("OnActiveSec=2s", text)
        self.assertIn("RemainAfterElapse=no", text)
        self.assertIn("Unit=bjut-auto-login.service", text)


if __name__ == "__main__":
    unittest.main()

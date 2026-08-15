import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMER = ROOT / "systemd" / "bjut-auto-login.timer"

class SystemdTimerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = TIMER.read_text(encoding="utf-8")

    def test_timer_has_restart_safe_monotonic_schedule(self):
        self.assertIn("OnActiveSec=20s", self.text)
        self.assertIn("OnUnitInactiveSec=60s", self.text)

    def test_timer_does_not_use_service_last_activation_as_only_repeat_anchor(self):
        self.assertNotIn("OnUnitActiveSec=", self.text)
        self.assertNotIn("OnBootSec=", self.text)

    def test_persistent_not_used_for_monotonic_timer(self):
        self.assertNotIn("Persistent=", self.text)

    def test_timer_targets_expected_service(self):
        self.assertIn("Unit=bjut-auto-login.service", self.text)
        self.assertIn("WantedBy=timers.target", self.text)

if __name__ == "__main__":
    unittest.main()

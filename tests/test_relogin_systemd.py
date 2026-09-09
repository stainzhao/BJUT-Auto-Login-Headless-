from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReloginSystemdTests(unittest.TestCase):
    def test_periodic_ensure_uses_same_lock_as_relogin(self):
        text = (ROOT / "systemd" / "bjut-auto-login.service").read_text()
        self.assertIn("/run/lock/bjut-auto-login.lock", text)
        self.assertIn("/usr/bin/flock", text)

    def test_relogin_timer_is_installed_but_not_enabled_by_script(self):
        timer = (ROOT / "systemd" / "bjut-auto-relogin.timer").read_text()
        install = (ROOT / "install.sh").read_text()
        self.assertIn("OnCalendar=*-*-* 04:00:00", timer)
        executable_lines = [
            line.strip() for line in install.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertNotIn(
            "systemctl enable --now bjut-auto-relogin.timer", executable_lines
        )

    def test_manual_systemd_trigger_is_available(self):
        service = (ROOT / "systemd" / "bjut-auto-relogin.service").read_text()
        self.assertIn("bjut-relogin", service)
        self.assertIn("relogin", service)


if __name__ == "__main__":
    unittest.main()

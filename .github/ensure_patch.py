from pathlib import Path

path = Path('bjut_auth.py')
text = path.read_text(encoding='utf-8')


def replace_once(old, new):
    global text
    if old not in text:
        raise SystemExit('expected source block not found:\n' + old[:500])
    text = text.replace(old, new, 1)


replace_once('VERSION = "0.3.2"', 'VERSION = "0.3.3"')

marker = '\n\ndef do_doctor(args, config, config_path):\n'
helper = r'''

def confirm_online(interface, url, attempts=3, delay=1.5):
    """Briefly confirm final Internet state after a portal login attempt."""
    for attempt in range(attempts):
        if internet_online(interface, url):
            return True
        if attempt + 1 < attempts:
            time.sleep(delay)
    return False


def do_ensure_login(args, config, interface, check_url):
    """Run one login attempt and use final connectivity as ensure's truth."""
    try:
        ok = do_login(args, config, interface)
    except AuthError as exc:
        # Some BJUT portal nodes can apply authentication and then return an
        # HTTP 5xx/timeout. For unattended recovery, final connectivity is
        # authoritative; retain the portal error as a warning.
        if confirm_online(interface, check_url):
            print(
                f"warning: Portal 认证过程异常（{exc}），但公网已恢复，视为认证成功",
                file=sys.stderr,
            )
            return 0
        raise

    if not ok:
        return 2
    if confirm_online(interface, check_url):
        return 0
    print("error: Portal 返回认证成功，但公网连通性仍为 offline", file=sys.stderr)
    return 3
'''
if marker not in text:
    raise SystemExit('doctor marker not found')
text = text.replace(marker, helper + marker, 1)

old_main = '''        if not do_login(args, config, interface):
            return 2
        if args.command == "ensure":
            if internet_online(interface, check_url):
                return 0
            print("error: Portal 返回认证成功，但公网连通性仍为 offline", file=sys.stderr)
            return 3
        return 0
'''
new_main = '''        if args.command == "ensure":
            return do_ensure_login(args, config, interface, check_url)

        return 0 if do_login(args, config, interface) else 2
'''
replace_once(old_main, new_main)
path.write_text(text, encoding='utf-8')

test = Path('tests/test_ensure_recovery.py')
test.write_text(r'''import importlib.util
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
''', encoding='utf-8')

readme = Path('README.md')
doc = readme.read_text(encoding='utf-8').replace('0.3.2', '0.3.3')
anchor = '- `ensure`：在线跳过，离线登录\n'
extra = '- `ensure` 以最终公网状态为成功判据；Portal 5xx/超时但认证已生效时避免 systemd 假失败\n'
if extra not in doc:
    if anchor not in doc:
        raise SystemExit('README ensure anchor not found')
    doc = doc.replace(anchor, anchor + extra, 1)
readme.write_text(doc, encoding='utf-8')

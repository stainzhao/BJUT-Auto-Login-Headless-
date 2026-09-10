#!/usr/bin/env python3
"""Manual and scheduled BJUT campus-network logout/relogin helper."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

VERSION = "0.1.1"
LOCK_PATH = Path("/run/lock/bjut-auto-login.lock")
DEFAULT_RELOGIN_DELAY = 2.0
DEFAULT_LOCK_TIMEOUT = 60.0


class ReloginError(RuntimeError):
    pass


def load_core():
    candidates = []
    override = os.getenv("BJUT_AUTH_CORE", "").strip()
    if override:
        candidates.append(Path(override))
    candidates.append(Path(__file__).resolve().with_name("bjut_auth.py"))
    installed = shutil.which("bjut-auth")
    if installed:
        candidates.append(Path(installed))
    candidates.append(Path("/usr/local/bin/bjut-auth"))

    seen = set()
    for path in candidates:
        path = path.expanduser()
        rendered = str(path)
        if rendered in seen or not path.is_file():
            continue
        seen.add(rendered)
        loader = importlib.machinery.SourceFileLoader("bjut_auth_core", rendered)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        if spec is None:
            continue
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    raise ReloginError(
        "找不到 bjut-auth 核心程序；请先运行 install.sh，或设置 BJUT_AUTH_CORE"
    )


@contextmanager
def process_lock(path=LOCK_PATH, timeout=DEFAULT_LOCK_TIMEOUT):
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except PermissionError as exc:
        raise ReloginError(f"无法创建锁文件 {path}；请使用 sudo 运行") from exc
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ReloginError("等待校园网认证互斥锁超时")
                time.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def type1_logout_query(username, password, local_ipv4, request_id):
    account = username if username.lower().endswith("@campus") else f"{username}@campus"
    return urlencode([
        ("callback", "dr1004"), ("login_method", "1"),
        ("user_account", account), ("user_password", password),
        ("ac_logout", "0"), ("register_mode", "0"),
        ("wlan_user_ip", local_ipv4), ("wlan_user_ipv6", ""),
        ("wlan_vlan_id", "0"), ("wlan_user_mac", "000000000000"),
        ("wlan_ac_ip", ""), ("wlan_ac_name", ""),
        ("jsVersion", "4.2.1"), ("v", request_id), ("lang", "zh"),
    ])


def type2_logout_query(request_id):
    return urlencode([
        ("callback", "dr1004"), ("jsVersion", "4.1"),
        ("v", request_id), ("lang", "zh"),
    ])


def type3_logout_query(core, local_ipv4, observed_ipv6, request_id):
    fields = {
        "callback": "dr1008", "login_method": "1",
        "user_account": "drcom", "user_password": "123",
        "ac_logout": "0", "register_mode": "1",
        "wlan_user_ip": local_ipv4, "wlan_user_ipv6": observed_ipv6,
        "wlan_vlan_id": "0", "wlan_user_mac": "000000000000",
        "wlan_ac_ip": "", "wlan_ac_name": "",
        "jsVersion": core.LGN_JS_VERSION,
        "program_index": core.LGN_PROGRAM_INDEX,
        "page_index": core.LGN_PAGE_INDEX,
    }
    encrypted = {key: core.eportal_encrypt(value) for key, value in fields.items()}
    encrypted.update({"encrypt": "1", "v": request_id, "lang": "zh"})
    return urlencode(encrypted)


def resolve_context(core, args, config):
    allow_http = args.allow_http_fallback or core.cfg_bool(
        config, "allow_http_fallback", False
    )
    interface = core.resolve_interface(args, config, allow_http)
    login_type = args.login_type or core.login_type_value(config)
    if login_type == "auto":
        login_type = (
            core.detect_login_type(
                interface, allow_http, require_login_ready=False
            )
            if core.interface_is_wireless(interface) else "3"
        )
    return interface, login_type, allow_http


def logout_type1(core, args, config, interface, allow_http):
    username, password = core.resolve_credentials(args, config)
    query = type1_logout_query(
        username,
        password,
        core.interface_ipv4(interface, core.TYPE1_ROUTE_DEST),
        core.random_request_id(),
    )
    https_url = core.DORM_HTTPS_LOGIN.replace("/portal/login", "/portal/logout")
    http_url = core.DORM_HTTP_LOGIN.replace("/portal/login", "/portal/logout")
    try:
        body = core.curl_get(
            f"{https_url}?{query}", interface, core.DORM_HTTPS_REFERER
        )
    except core.AuthError:
        if not allow_http:
            raise
        body = core.curl_get(
            f"{http_url}?{query}", interface, core.DORM_HTTP_REFERER
        )
    return core.parse_login_response(body)


def logout_type2(core, interface, allow_http):
    query = type2_logout_query(core.random_request_id())
    https_url = core.WIFI_HTTPS_LOGIN.replace("/login", "/logout")
    http_url = core.WIFI_HTTP_LOGIN.replace("/login", "/logout")
    try:
        body = core.curl_get(
            f"{https_url}?{query}", interface, core.WIFI_HTTPS_REFERER
        )
    except core.AuthError:
        if not allow_http:
            raise
        body = core.curl_get(
            f"{http_url}?{query}", interface, core.WIFI_HTTP_REFERER
        )
    return core.parse_login_response(body)


def logout_type3(core, interface):
    local_ipv4 = core.interface_ipv4(interface, core.TYPE3_ROUTE_DEST)
    diagnostic = "IPv4+IPv6 联动注销"
    try:
        observed_ipv6 = core.get_observed_ipv6(interface)
    except core.AuthError as exc:
        observed_ipv6 = ""
        diagnostic = f"IPv6 地址发现失败，使用单 IPv4 注销：{exc}"
    query = type3_logout_query(
        core, local_ipv4, observed_ipv6, core.random_request_id()
    )
    logout_url = core.LGN_LOGIN_URL.replace("/portal/login", "/portal/logout")
    body = core.curl_get(
        f"{logout_url}?{query}", interface, core.LGN_REFERER
    )
    ok, message = core.parse_login_response(body)
    return ok, f"{message}；{diagnostic}"


def do_logout(core, args, config, interface=None, login_type=None, allow_http=None):
    if interface is None or login_type is None or allow_http is None:
        interface, login_type, allow_http = resolve_context(core, args, config)
    if login_type == "1":
        ok, message = logout_type1(core, args, config, interface, allow_http)
    elif login_type == "2":
        ok, message = logout_type2(core, interface, allow_http)
    elif login_type == "3":
        ok, message = logout_type3(core, interface)
    else:
        raise ReloginError("认证类型必须为 auto、1、2 或 3")
    print(f"logout type={login_type} interface={interface}: {message}")
    return ok, interface, login_type, allow_http


def wait_until_offline(core, interface, check_url, resolve_ip, attempts=6, delay=1.0):
    for attempt in range(attempts):
        if not core.internet_online(interface, check_url, resolve_ip):
            return True
        if attempt + 1 < attempts:
            time.sleep(delay)
    return False


def login_and_confirm(
    core, args, config, interface, login_type, check_url, check_resolve_ip
):
    login_args = argparse.Namespace(**vars(args))
    login_args.login_type = login_type
    if not core.do_login(login_args, config, interface):
        return 2
    if core.confirm_online(interface, check_url, check_resolve_ip):
        print(f"relogin complete: type={login_type} interface={interface}")
        return 0
    print("error: 重新认证后公网仍为 offline", file=sys.stderr)
    return 3


def do_relogin(core, args, config):
    issue = core.credential_issue(args, config)
    if issue:
        raise ReloginError(f"{issue}；relogin 可能执行注销，因此已在操作前终止")

    interface, login_type, allow_http = resolve_context(core, args, config)
    check_url = core.connectivity_url(config)
    check_resolve_ip = core.connectivity_resolve_ip(config)

    if core.internet_online(interface, check_url, check_resolve_ip):
        ok, _, _, _ = do_logout(
            core, args, config, interface, login_type, allow_http
        )
        if not ok:
            return 2

        if not wait_until_offline(
            core, interface, check_url, check_resolve_ip
        ):
            print(
                "error: Portal 返回注销成功，但公网仍在线；为避免重复认证，已停止 relogin",
                file=sys.stderr,
            )
            return 3

        time.sleep(max(0.0, args.delay))
    else:
        print(
            f"offline: interface={interface}, skip logout and attempt login"
        )

    return login_and_confirm(
        core,
        args,
        config,
        interface,
        login_type,
        check_url,
        check_resolve_ip,
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description="BJUT campus network manual/scheduled logout and relogin"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("-u", "--username")
    parser.add_argument("-p", "--password", help="不建议在多用户机器命令行中直接传密码")
    parser.add_argument("-i", "--interface", help="校园网物理网卡")
    parser.add_argument("--type", dest="login_type", choices=["auto", "1", "2", "3"])
    parser.add_argument("--allow-http-fallback", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("logout", help="手动注销当前校园网 Portal 会话")
    relogin = sub.add_parser("relogin", help="注销后等待并重新认证；若已离线则直接认证")
    relogin.add_argument(
        "--delay", type=float, default=DEFAULT_RELOGIN_DELAY,
        help=f"确认离线后等待秒数（默认 {DEFAULT_RELOGIN_DELAY:g} 秒）",
    )
    return parser


def main():
    args = build_parser().parse_args()
    try:
        core = load_core()
        config, config_path = core.load_config(args.config)
        warning = core.config_permission_warning(config_path, config)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)

        with process_lock():
            if args.command == "logout":
                ok, _, _, _ = do_logout(core, args, config)
                return 0 if ok else 2
            return do_relogin(core, args, config)
    except (ReloginError, configparser.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Preserve the core AuthError message without importing its type here.
        if exc.__class__.__name__ in {"AuthError", "HttpStatusError"}:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Headless BJUT campus network login helper for Linux."""

from __future__ import annotations

import argparse
import configparser
import getpass
import ipaddress
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

VERSION = "0.2.0"

DORM_HTTP_LOGIN = "http://10.21.221.98:801/eportal/portal/login"
DORM_HTTPS_LOGIN = "https://10.21.221.98:802/eportal/portal/login"
DORM_HTTP_REFERER = "http://10.21.221.98/"
DORM_HTTPS_REFERER = "https://10.21.221.98/"
WIFI_HTTP_LOGIN = "http://10.21.251.3/drcom/login"
WIFI_HTTPS_LOGIN = "https://wlgn.bjut.edu.cn/drcom/login"
WIFI_HTTP_REFERER = "http://10.21.251.3/"
WIFI_HTTPS_REFERER = "https://wlgn.bjut.edu.cn/"
LGN_REFERER = "https://lgn.bjut.edu.cn/"
LGN_IPV6_URL = "https://lgn6.bjut.edu.cn/drcom/getipv6"
LGN_LOGIN_URL = "https://lgn.bjut.edu.cn:802/eportal/portal/login"
LGN_PROGRAM_INDEX = "o4OBee1755497815"
LGN_PAGE_INDEX = "cHAmjX1755497856"
LGN_JS_VERSION = "4.2.2"
EPORTAL_XOR_KEY = 0x16
DEFAULT_CONNECTIVITY_URL = "https://www.baidu.com/"
VIRTUAL_PREFIXES = (
    "wg", "tun", "tap", "tailscale", "zt", "docker", "br-", "veth", "virbr", "lo"
)
SENSITIVE_QUERY_KEYS = ("user_password", "upass", "DDDDD")


class AuthError(RuntimeError):
    pass


def run(cmd: List[str], timeout: int = 10, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AuthError("缺少命令：{}".format(cmd[0])) from exc
    except subprocess.TimeoutExpired as exc:
        raise AuthError("命令超时：{}".format(cmd[0])) from exc


def random_request_id() -> str:
    return "{:04d}".format(secrets.randbelow(10_000))


def is_virtual_interface(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(prefix) for prefix in VIRTUAL_PREFIXES)


def default_physical_interface() -> str:
    result = run(["ip", "route", "show", "table", "main", "default"], timeout=3)
    for line in result.stdout.splitlines():
        match = re.search(r"\bdev\s+(\S+)", line)
        if match and not is_virtual_interface(match.group(1)):
            return match.group(1)
    raise AuthError("无法自动确定物理网卡，请使用 --interface 指定，例如 eno1 或 wlan0")


def interface_ipv4(interface: str) -> str:
    result = run(["ip", "-4", "-o", "addr", "show", "dev", interface, "scope", "global"], timeout=3)
    for line in result.stdout.splitlines():
        match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/", line)
        if match:
            ipaddress.IPv4Address(match.group(1))
            return match.group(1)
    raise AuthError("网卡 {} 没有可用 IPv4 地址".format(interface))


def interface_is_wireless(interface: str) -> bool:
    return Path("/sys/class/net/{}/wireless".format(interface)).exists()


def redact_error_text(text: str) -> str:
    redacted = text
    for key in SENSITIVE_QUERY_KEYS:
        redacted = re.sub(r"({}=)[^&\s]+".format(re.escape(key)), r"\1***", redacted, flags=re.I)
    return redacted


def curl_config_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")


def curl_get(
    url: str,
    interface: Optional[str] = None,
    referer: Optional[str] = None,
    insecure_tls: bool = False,
    timeout: int = 6,
) -> str:
    """GET with curl while keeping the credential-bearing URL out of process argv."""
    cmd = [
        "curl", "--silent", "--show-error", "--fail", "--noproxy", "*",
        "--connect-timeout", "3", "--max-time", str(timeout),
        "--header", "Accept: */*", "--header", "Cache-Control: no-cache, no-store",
        "--config", "-",
    ]
    if interface:
        cmd += ["--interface", interface]
    if referer:
        cmd += ["--referer", referer]
    if insecure_tls:
        cmd.append("--insecure")
    curl_config = 'url = "{}"\n'.format(curl_config_escape(url))
    result = run(cmd, timeout=timeout + 2, input_text=curl_config)
    if result.returncode != 0:
        detail = redact_error_text((result.stderr or result.stdout).strip())
        raise AuthError(detail or "curl 请求失败（exit={}）".format(result.returncode))
    return result.stdout


def jsonp_object(text: str) -> Dict[str, Any]:
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        raise AuthError("认证网关未返回预期 JSONP")
    try:
        value = json.loads(text[start + 1:end])
    except json.JSONDecodeError as exc:
        raise AuthError("认证网关返回的 JSONP 无法解析") from exc
    if not isinstance(value, dict):
        raise AuthError("认证网关返回内容格式异常")
    return value


def parse_login_response(text: str) -> Tuple[bool, str]:
    data = jsonp_object(text)
    try:
        result_int = int(data.get("result"))
    except (TypeError, ValueError) as exc:
        raise AuthError("认证网关未返回有效 result 字段") from exc
    if result_int not in (0, 1):
        raise AuthError("认证网关返回未知 result={}".format(result_int))
    message = data.get("msga", data.get("msg", ""))
    if not isinstance(message, str):
        message = json.dumps(message, ensure_ascii=False)
    if not message:
        message = "认证成功" if result_int == 1 else "认证失败"
    return result_int == 1, message


def eportal_encrypt(value: str) -> str:
    """Match portal JS: each UTF-16 code unit XOR 0x16, then hex."""
    raw = value.encode("utf-16-le")
    units = (raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2))
    return "".join("{:02x}".format(unit ^ EPORTAL_XOR_KEY) for unit in units)


def get_observed_ipv6(interface: str) -> str:
    params = {
        "callback": "dr1004",
        "program_index": LGN_PROGRAM_INDEX,
        "page_index": LGN_PAGE_INDEX,
        "jsVersion": LGN_JS_VERSION,
        "v": random_request_id(),
        "lang": "zh",
    }
    body = curl_get("{}?{}".format(LGN_IPV6_URL, urlencode(params)), interface, LGN_REFERER)
    data = jsonp_object(body)
    try:
        result = int(data.get("result", 0))
    except (TypeError, ValueError):
        result = 0
    if result != 1:
        raise AuthError("IPv6 地址发现接口未返回成功结果")
    value = str(data.get("ip", "")).strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise AuthError("IPv6 地址发现接口返回了无效地址") from exc
    if address.version != 6:
        raise AuthError("IPv6 地址发现接口没有返回 IPv6")
    return value


def login_type3(username: str, password: str, interface: str) -> Tuple[bool, str]:
    local_ipv4 = interface_ipv4(interface)
    observed_ipv6 = get_observed_ipv6(interface)
    account = username if username.startswith(",0,") else ",0,{}".format(username)
    fields = {
        "callback": "dr1005",
        "login_method": "1",
        "user_account": account,
        "user_password": password,
        "wlan_user_ip": local_ipv4,
        "wlan_user_ipv6": observed_ipv6,
        "wlan_user_mac": "000000000000",
        "wlan_vlan_id": "0",
        "wlan_ac_ip": "",
        "wlan_ac_name": "",
        "authex_enable": "",
        "jsVersion": LGN_JS_VERSION,
        "login_ip_type": "0",
        "terminal_type": "3",
        "lang": "zh-cn",
        "program_index": LGN_PROGRAM_INDEX,
        "page_index": LGN_PAGE_INDEX,
    }
    encrypted = {key: eportal_encrypt(value) for key, value in fields.items()}
    encrypted.update({"encrypt": "1", "v": random_request_id(), "lang": "zh"})
    body = curl_get("{}?{}".format(LGN_LOGIN_URL, urlencode(encrypted)), interface, LGN_REFERER)
    return parse_login_response(body)


def login_type1(username: str, password: str, interface: str, allow_http_fallback: bool) -> Tuple[bool, str]:
    local_ipv4 = interface_ipv4(interface)
    account = username if username.lower().endswith("@campus") else "{}@campus".format(username)
    params = {
        "callback": "dr1003",
        "login_method": "1",
        "user_account": account,
        "user_password": password,
        "wlan_user_ip": local_ipv4,
        "wlan_user_ipv6": "",
        "wlan_user_mac": "000000000000",
        "wlan_ac_ip": "",
        "wlan_ac_name": "",
        "jsVersion": "4.2.1",
        "terminal_type": "3",
        "lang": "zh-cn",
        "v": random_request_id(),
    }
    query = urlencode(params)
    try:
        body = curl_get("{}?{}".format(DORM_HTTPS_LOGIN, query), interface, DORM_HTTPS_REFERER, insecure_tls=True)
    except AuthError:
        if not allow_http_fallback:
            raise
        body = curl_get("{}?{}".format(DORM_HTTP_LOGIN, query), interface, DORM_HTTP_REFERER)
    return parse_login_response(body)


def login_type2(username: str, password: str, interface: str, allow_http_fallback: bool) -> Tuple[bool, str]:
    params = {
        "callback": "dr1003",
        "DDDDD": username,
        "upass": password,
        "0MKKey": "123456",
        "R1": "0",
        "R2": "",
        "R3": "0",
        "R6": "0",
        "para": "00",
        "v6ip": "",
        "terminal_type": "1",
        "lang": "zh-cn",
        "jsVersion": "4.1",
        "v": random_request_id(),
    }
    query = urlencode(params)
    try:
        body = curl_get("{}?{}".format(WIFI_HTTPS_LOGIN, query), interface, WIFI_HTTPS_REFERER)
    except AuthError:
        if not allow_http_fallback:
            raise
        body = curl_get("{}?{}".format(WIFI_HTTP_LOGIN, query), interface, WIFI_HTTP_REFERER)
    return parse_login_response(body)


def detect_login_type(interface: str, allow_http_fallback: bool) -> str:
    if not interface_is_wireless(interface):
        get_observed_ipv6(interface)
        return "3"
    probes = [
        ("2", WIFI_HTTPS_LOGIN, WIFI_HTTPS_REFERER, False),
        ("1", DORM_HTTPS_LOGIN, DORM_HTTPS_REFERER, True),
    ]
    if allow_http_fallback:
        probes += [
            ("2", WIFI_HTTP_LOGIN, WIFI_HTTP_REFERER, False),
            ("1", DORM_HTTP_LOGIN, DORM_HTTP_REFERER, False),
        ]
    for login_type, url, referer, insecure in probes:
        try:
            body = curl_get("{}?callback=dr1003".format(url), interface, referer, insecure_tls=insecure, timeout=3)
        except AuthError:
            continue
        lowered = body.lower()
        if any(token in lowered for token in ("dr1003", "result", "eportal", "drcom")):
            return login_type
    raise AuthError("无法自动识别校园网认证类型，请显式指定 --type 1、2 或 3")


def internet_online(interface: Optional[str], url: str = DEFAULT_CONNECTIVITY_URL) -> bool:
    cmd = [
        "curl", "--noproxy", "*", "--silent", "--output", "/dev/null",
        "--connect-timeout", "3", "--max-time", "5", "--write-out", "%{http_code}",
    ]
    if interface:
        cmd += ["--interface", interface]
    cmd += ["--config", "-"]
    result = run(cmd, timeout=7, input_text='url = "{}"\n'.format(curl_config_escape(url)))
    if result.returncode != 0:
        return False
    try:
        code = int(result.stdout.strip())
    except ValueError:
        return False
    return 200 <= code < 300


def load_config(path: Optional[str]) -> Tuple[Dict[str, str], Optional[Path]]:
    candidates = [Path(path)] if path else [
        Path("/etc/bjut-auto-login.conf"),
        Path.home() / ".config" / "bjut-auto-login" / "config.ini",
    ]
    target = next((p for p in candidates if p.exists()), None)
    if target is None:
        return {}, None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with target.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        raise AuthError("配置文件无法解析：{}".format(target)) from exc
    if "BJUT" not in parser:
        raise AuthError("配置文件缺少 [BJUT]：{}".format(target))
    return dict(parser["BJUT"]), target


def config_permission_warning(path: Optional[Path], config: Dict[str, str]) -> Optional[str]:
    if path is None or not config.get("password"):
        return None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return "配置文件 {} 权限为 {:o}，包含密码时建议 chmod 600".format(path, mode)
    return None


def cfg_bool(config: Dict[str, str], key: str, default: bool = False) -> bool:
    value = config.get(key)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def connectivity_url(config: Dict[str, str]) -> str:
    return config.get("connectivity_url", DEFAULT_CONNECTIVITY_URL).strip() or DEFAULT_CONNECTIVITY_URL


def resolve_credentials(args: argparse.Namespace, config: Dict[str, str]) -> Tuple[str, str]:
    username = args.username or os.getenv("BJUT_USERNAME") or config.get("username", "")
    password = args.password or os.getenv("BJUT_PASSWORD") or config.get("password", "")
    if not username:
        raise AuthError("缺少用户名：使用 --username、BJUT_USERNAME 或配置文件")
    if not password:
        if sys.stdin.isatty():
            password = getpass.getpass("BJUT password: ")
        else:
            raise AuthError("缺少密码：无人值守模式请写入权限为 600 的配置文件")
    return username.strip(), password


def resolve_interface(args: argparse.Namespace, config: Dict[str, str]) -> str:
    interface = args.interface or config.get("interface") or default_physical_interface()
    interface = interface.strip()
    if is_virtual_interface(interface):
        raise AuthError("拒绝通过疑似 VPN/TUN 网卡认证：{}".format(interface))
    return interface


def do_login(args: argparse.Namespace, config: Dict[str, str], interface: Optional[str] = None) -> bool:
    username, password = resolve_credentials(args, config)
    interface = interface or resolve_interface(args, config)
    allow_http = args.allow_http_fallback or cfg_bool(config, "allow_http_fallback", False)
    login_type = args.login_type or config.get("type", "auto")
    if login_type == "auto":
        login_type = detect_login_type(interface, allow_http)
    if login_type == "1":
        ok, message = login_type1(username, password, interface, allow_http)
    elif login_type == "2":
        ok, message = login_type2(username, password, interface, allow_http)
    elif login_type == "3":
        ok, message = login_type3(username, password, interface)
    else:
        raise AuthError("认证类型必须为 auto、1、2 或 3")
    print("type={} interface={}: {}".format(login_type, interface, message))
    return ok


def do_doctor(args: argparse.Namespace, config: Dict[str, str], config_path: Optional[Path]) -> int:
    failures = 0
    print("BJUT Auto Login {}".format(VERSION))
    print("python: {}.{}.{}".format(*sys.version_info[:3]))
    for command in ("curl", "ip"):
        path = shutil.which(command)
        if path:
            print("{}: OK ({})".format(command, path))
        else:
            print("{}: MISSING".format(command))
            failures += 1
    if config_path:
        print("config: {}".format(config_path))
        warning = config_permission_warning(config_path, config)
        if warning:
            print("warning: {}".format(warning))
    else:
        print("config: not found (CLI/environment credentials can still be used)")
    try:
        interface = resolve_interface(args, config)
        print("interface: {} ({})".format(interface, "Wi-Fi" if interface_is_wireless(interface) else "wired"))
        print("ipv4: {}".format(interface_ipv4(interface)))
        online = internet_online(interface, connectivity_url(config))
        print("internet: {}".format("online" if online else "offline"))
        try:
            detected = detect_login_type(interface, args.allow_http_fallback or cfg_bool(config, "allow_http_fallback", False))
            print("portal: type {}".format(detected))
        except AuthError as exc:
            print("portal: not confirmed ({})".format(exc))
    except AuthError as exc:
        print("network: ERROR ({})".format(exc))
        failures += 1
    return 0 if failures == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BJUT campus network headless auto-login")
    parser.add_argument("--version", action="version", version="%(prog)s {}".format(VERSION))
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("-u", "--username")
    parser.add_argument("-p", "--password", help="不建议在多用户机器命令行中直接传密码")
    parser.add_argument("-i", "--interface", help="物理网卡，例如 eno1 / enp3s0 / wlan0")
    parser.add_argument("--type", dest="login_type", choices=["auto", "1", "2", "3"])
    parser.add_argument(
        "--allow-http-fallback",
        action="store_true",
        help="允许宿舍/Wi-Fi HTTPS 失败后回退到明文 HTTP，仅可信校园网使用",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("detect", help="检测认证类型")
    sub.add_parser("login", help="执行一次认证")
    sub.add_parser("status", help="检查外网连通性")
    sub.add_parser("ensure", help="已联网则退出，否则认证一次；适合 systemd timer")
    sub.add_parser("doctor", help="检查依赖、配置、网卡和 Portal 可达性")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config, config_path = load_config(args.config)
        warning = config_permission_warning(config_path, config)
        if warning and args.command not in ("doctor", "status", "detect"):
            print("warning: {}".format(warning), file=sys.stderr)

        if args.command == "doctor":
            return do_doctor(args, config, config_path)

        interface = resolve_interface(args, config)
        allow_http = args.allow_http_fallback or cfg_bool(config, "allow_http_fallback", False)
        check_url = connectivity_url(config)

        if args.command == "status":
            online = internet_online(interface, check_url)
            print("online" if online else "offline")
            return 0 if online else 1

        if args.command == "detect":
            login_type = args.login_type or config.get("type", "auto")
            if login_type == "auto":
                login_type = detect_login_type(interface, allow_http)
            print("type={} interface={}".format(login_type, interface))
            return 0

        if args.command == "ensure" and internet_online(interface, check_url):
            print("online: interface={}, skip login".format(interface))
            return 0

        ok = do_login(args, config, interface=interface)
        if not ok:
            return 2
        if args.command == "ensure":
            return 0 if internet_online(interface, check_url) else 3
        return 0
    except AuthError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

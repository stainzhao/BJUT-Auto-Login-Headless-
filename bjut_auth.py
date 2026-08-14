#!/usr/bin/env python3
"""Headless BJUT campus network login helper.

Implements the currently observed BJUT portal flows without a GUI. Network
requests are delegated to curl so the process can bind to a physical interface
and avoid VPN/TUN routes.
"""

from __future__ import annotations

import argparse
import configparser
import getpass
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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
VIRTUAL_PREFIXES = ("wg", "tun", "tap", "tailscale", "zt", "docker", "br-", "veth", "virbr", "lo")


class AuthError(RuntimeError):
    pass


def run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise AuthError(f"缺少命令：{cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AuthError(f"命令超时：{cmd[0]}") from exc


def random_request_id() -> str:
    return f"{secrets.randbelow(10_000):04d}"


def is_virtual_interface(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(prefix) for prefix in VIRTUAL_PREFIXES)


def default_physical_interface() -> str:
    result = run(["ip", "route", "show", "default"], timeout=3)
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
    raise AuthError(f"网卡 {interface} 没有可用 IPv4 地址")


def interface_is_wireless(interface: str) -> bool:
    return Path(f"/sys/class/net/{interface}/wireless").exists()


def curl_get(url: str, interface: str | None = None, referer: str | None = None,
             insecure_tls: bool = False, timeout: int = 6) -> str:
    cmd = ["curl", "--noproxy", "*", "--silent", "--show-error", "--fail-with-body",
           "--connect-timeout", "3", "--max-time", str(timeout),
           "--header", "Accept: */*", "--header", "Cache-Control: no-cache, no-store"]
    if interface:
        cmd += ["--interface", interface]
    if referer:
        cmd += ["--referer", referer]
    if insecure_tls:
        cmd.append("--insecure")
    cmd.append(url)
    result = run(cmd, timeout=timeout + 2)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise AuthError(detail or f"curl 请求失败（exit={result.returncode}）")
    return result.stdout


def jsonp_object(text: str) -> dict[str, Any]:
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


def parse_login_response(text: str) -> tuple[bool, str]:
    data = jsonp_object(text)
    try:
        result_int = int(data.get("result"))
    except (TypeError, ValueError) as exc:
        raise AuthError("认证网关未返回有效 result 字段") from exc
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
    return "".join(f"{unit ^ EPORTAL_XOR_KEY:02x}" for unit in units)


def get_observed_ipv6(interface: str) -> str:
    params = {"callback": "dr1004", "program_index": LGN_PROGRAM_INDEX,
              "page_index": LGN_PAGE_INDEX, "jsVersion": LGN_JS_VERSION,
              "v": random_request_id(), "lang": "zh"}
    body = curl_get(f"{LGN_IPV6_URL}?{urlencode(params)}", interface, LGN_REFERER)
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


def login_type3(username: str, password: str, interface: str) -> tuple[bool, str]:
    local_ipv4 = interface_ipv4(interface)
    observed_ipv6 = get_observed_ipv6(interface)
    account = username if username.startswith(",0,") else f",0,{username}"
    fields = {
        "callback": "dr1005", "login_method": "1", "user_account": account,
        "user_password": password, "wlan_user_ip": local_ipv4,
        "wlan_user_ipv6": observed_ipv6, "wlan_user_mac": "000000000000",
        "wlan_vlan_id": "0", "wlan_ac_ip": "", "wlan_ac_name": "",
        "authex_enable": "", "jsVersion": LGN_JS_VERSION, "login_ip_type": "0",
        "terminal_type": "3", "lang": "zh-cn", "program_index": LGN_PROGRAM_INDEX,
        "page_index": LGN_PAGE_INDEX,
    }
    encrypted = {key: eportal_encrypt(value) for key, value in fields.items()}
    encrypted.update({"encrypt": "1", "v": random_request_id(), "lang": "zh"})
    body = curl_get(f"{LGN_LOGIN_URL}?{urlencode(encrypted)}", interface, LGN_REFERER)
    return parse_login_response(body)


def login_type1(username: str, password: str, interface: str,
                allow_http_fallback: bool) -> tuple[bool, str]:
    local_ipv4 = interface_ipv4(interface)
    account = username if username.lower().endswith("@campus") else f"{username}@campus"
    params = {"callback": "dr1003", "login_method": "1", "user_account": account,
              "user_password": password, "wlan_user_ip": local_ipv4, "wlan_user_ipv6": "",
              "wlan_user_mac": "000000000000", "wlan_ac_ip": "", "wlan_ac_name": "",
              "jsVersion": "4.2.1", "terminal_type": "3", "lang": "zh-cn",
              "v": random_request_id()}
    query = urlencode(params)
    try:
        body = curl_get(f"{DORM_HTTPS_LOGIN}?{query}", interface, DORM_HTTPS_REFERER,
                        insecure_tls=True)
    except AuthError:
        if not allow_http_fallback:
            raise
        body = curl_get(f"{DORM_HTTP_LOGIN}?{query}", interface, DORM_HTTP_REFERER)
    return parse_login_response(body)


def login_type2(username: str, password: str, interface: str,
                allow_http_fallback: bool) -> tuple[bool, str]:
    params = {"callback": "dr1003", "DDDDD": username, "upass": password,
              "0MKKey": "123456", "R1": "0", "R2": "", "R3": "0", "R6": "0",
              "para": "00", "v6ip": "", "terminal_type": "1", "lang": "zh-cn",
              "jsVersion": "4.1", "v": random_request_id()}
    query = urlencode(params)
    try:
        body = curl_get(f"{WIFI_HTTPS_LOGIN}?{query}", interface, WIFI_HTTPS_REFERER)
    except AuthError:
        if not allow_http_fallback:
            raise
        body = curl_get(f"{WIFI_HTTP_LOGIN}?{query}", interface, WIFI_HTTP_REFERER)
    return parse_login_response(body)


def detect_login_type(interface: str, allow_http_fallback: bool) -> str:
    if not interface_is_wireless(interface):
        get_observed_ipv6(interface)
        return "3"
    probes = [("2", WIFI_HTTPS_LOGIN, WIFI_HTTPS_REFERER, False),
              ("1", DORM_HTTPS_LOGIN, DORM_HTTPS_REFERER, True)]
    if allow_http_fallback:
        probes += [("2", WIFI_HTTP_LOGIN, WIFI_HTTP_REFERER, False),
                   ("1", DORM_HTTP_LOGIN, DORM_HTTP_REFERER, False)]
    for login_type, url, referer, insecure in probes:
        try:
            body = curl_get(f"{url}?callback=dr1003", interface, referer,
                            insecure_tls=insecure, timeout=3)
        except AuthError:
            continue
        lowered = body.lower()
        if any(token in lowered for token in ("dr1003", "result", "eportal", "drcom")):
            return login_type
    raise AuthError("无法自动识别校园网认证类型，请显式指定 --type 1、2 或 3")


def internet_online(interface: str | None) -> bool:
    cmd = ["curl", "--noproxy", "*", "--silent", "--output", "/dev/null",
           "--connect-timeout", "3", "--max-time", "5", "--write-out", "%{http_code}"]
    if interface:
        cmd += ["--interface", interface]
    cmd += ["https://www.baidu.com/"]
    result = run(cmd, timeout=7)
    if result.returncode != 0:
        return False
    try:
        code = int(result.stdout.strip())
    except ValueError:
        return False
    return 200 <= code < 300


def load_config(path: str | None) -> dict[str, str]:
    candidates = [Path(path)] if path else [Path("/etc/bjut-auto-login.conf"),
        Path.home() / ".config" / "bjut-auto-login" / "config.ini"]
    target = next((p for p in candidates if p.exists()), None)
    if target is None:
        return {}
    parser = configparser.ConfigParser()
    try:
        parser.read(target, encoding="utf-8")
    except configparser.Error as exc:
        raise AuthError(f"配置文件无法解析：{target}") from exc
    if "BJUT" not in parser:
        raise AuthError(f"配置文件缺少 [BJUT]：{target}")
    return dict(parser["BJUT"])


def cfg_bool(config: dict[str, str], key: str, default: bool = False) -> bool:
    value = config.get(key)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_credentials(args: argparse.Namespace, config: dict[str, str]) -> tuple[str, str]:
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


def do_login(args: argparse.Namespace, config: dict[str, str]) -> bool:
    username, password = resolve_credentials(args, config)
    interface = args.interface or config.get("interface") or default_physical_interface()
    if is_virtual_interface(interface):
        raise AuthError(f"拒绝通过疑似 VPN/TUN 网卡认证：{interface}")
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
    print(f"type={login_type} interface={interface}: {message}")
    return ok


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BJUT campus network headless auto-login")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("-u", "--username")
    parser.add_argument("-p", "--password", help="不建议在多用户机器命令行中直接传密码")
    parser.add_argument("-i", "--interface", help="物理网卡，例如 eno1 / enp3s0 / wlan0")
    parser.add_argument("--type", dest="login_type", choices=["auto", "1", "2", "3"])
    parser.add_argument("--allow-http-fallback", action="store_true",
                        help="允许宿舍/Wi-Fi HTTPS 失败后回退到明文 HTTP，仅可信校园网使用")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("detect", help="检测认证类型")
    sub.add_parser("login", help="执行一次认证")
    sub.add_parser("status", help="检查外网连通性")
    sub.add_parser("ensure", help="已联网则退出，否则认证一次；适合 systemd timer")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config = load_config(args.config)
        interface = args.interface or config.get("interface") or default_physical_interface()
        allow_http = args.allow_http_fallback or cfg_bool(config, "allow_http_fallback", False)
        if args.command == "status":
            online = internet_online(interface)
            print("online" if online else "offline")
            return 0 if online else 1
        if args.command == "detect":
            login_type = args.login_type or config.get("type", "auto")
            if login_type == "auto":
                login_type = detect_login_type(interface, allow_http)
            print(f"type={login_type} interface={interface}")
            return 0
        if args.command == "ensure" and internet_online(interface):
            print(f"online: interface={interface}, skip login")
            return 0
        ok = do_login(args, config)
        if not ok:
            return 2
        if args.command == "ensure":
            return 0 if internet_online(interface) else 3
        return 0
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""BJUT campus network headless auto-login for Linux."""

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
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

VERSION = "0.3.0"

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
TYPE1_ROUTE_DEST = "10.21.221.98"
TYPE2_ROUTE_DEST = "10.21.251.3"
TYPE3_ROUTE_DEST = "172.30.201.2"
PORTAL_ROUTE_DESTINATIONS = (TYPE3_ROUTE_DEST, TYPE2_ROUTE_DEST, TYPE1_ROUTE_DEST)

LGN_PROGRAM_INDEX = "o4OBee1755497815"
LGN_PAGE_INDEX = "cHAmjX1755497856"
LGN_JS_VERSION = "4.2.2"
EPORTAL_XOR_KEY = 0x16

DEFAULT_CONNECTIVITY_URL = "https://www.baidu.com/"
VIRTUAL_PREFIXES = ("wg", "tun", "tap", "tailscale", "zt", "docker", "br-", "veth", "virbr")
SENSITIVE_QUERY_KEYS = ("user_password", "upass", "DDDDD", "user_account")
TRANSIENT_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
ALLOWED_CONFIG_KEYS = {
    "username", "password", "type", "interface",
    "allow_http_fallback", "connectivity_url",
}
PLACEHOLDER_PASSWORDS = {"change_me", "<password>"}
PLACEHOLDER_USERNAMES = {"<username>"}
CURL_STATUS_MARKER = "__BJUT_HTTP_STATUS__:"


class AuthError(RuntimeError):
    pass


class HttpStatusError(AuthError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"HTTP {status}")


def run(cmd, timeout=10, input_text=None):
    try:
        return subprocess.run(
            cmd, input=input_text, text=True, capture_output=True,
            timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise AuthError(f"缺少命令：{cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AuthError(f"命令超时：{cmd[0]}") from exc


def random_request_id():
    return f"{secrets.randbelow(10_000):04d}"


def normalize_interface_name(name):
    return name.strip().split("@", 1)[0]


def is_virtual_interface(name):
    name = normalize_interface_name(name).lower()
    return name == "lo" or any(name.startswith(prefix) for prefix in VIRTUAL_PREFIXES)


def usable_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value.strip())
    except ipaddress.AddressValueError:
        return False
    return not (
        address.is_unspecified or address.is_loopback or address.is_link_local
        or address.is_multicast
        or address in ipaddress.IPv4Network("198.18.0.0/15")
    )


def is_likely_bjut_ipv4(value):
    """Fallback only; explicit config and route evidence remain authoritative."""
    if not usable_ipv4(value):
        return False
    a, b, _, _ = (int(part) for part in value.split("."))
    if a == 172 and 17 <= b <= 27:
        return True
    return a == 10 and b in {
        17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 121, 126, 226
    }


def parse_route_identity(text):
    for line in text.splitlines():
        fields, interface, source = line.split(), "", ""
        for i, field in enumerate(fields[:-1]):
            if field == "dev":
                interface = normalize_interface_name(fields[i + 1])
            elif field == "src":
                source = fields[i + 1]
        if interface and not is_virtual_interface(interface):
            return interface, source if usable_ipv4(source) else ""
    return None


def route_identity(destination, interface=None):
    cmd = ["ip", "-4", "route", "get", destination]
    if interface:
        cmd += ["oif", interface]
    result = run(cmd, timeout=3)
    if result.returncode != 0:
        return None
    identity = parse_route_identity(result.stdout)
    if identity and (not interface or identity[0] == normalize_interface_name(interface)):
        return identity
    return None


def parse_global_interface_addresses(text):
    interfaces = {}
    for line in text.splitlines():
        match = re.search(r"^\d+:\s+(\S+)\s+inet\s+([0-9.]+)/", line)
        if not match:
            continue
        interface = normalize_interface_name(match.group(1))
        address = match.group(2)
        if is_virtual_interface(interface) or not usable_ipv4(address):
            continue
        interfaces.setdefault(interface, [])
        if address not in interfaces[interface]:
            interfaces[interface].append(address)
    return interfaces


def global_interface_addresses(interface=None):
    cmd = ["ip", "-4", "-o", "addr", "show"]
    if interface:
        cmd += ["dev", interface]
    cmd += ["scope", "global"]
    result = run(cmd, timeout=3)
    if result.returncode != 0:
        label = f"网卡 {interface} 的" if interface else "本机"
        raise AuthError(f"无法读取{label} IPv4 网卡信息")
    return parse_global_interface_addresses(result.stdout)


def interface_ipv4(interface, destination=None):
    interface = normalize_interface_name(interface)
    identity = route_identity(destination, interface) if destination else None
    if identity and identity[1]:
        return identity[1]
    addresses = global_interface_addresses(interface).get(interface, [])
    if not addresses:
        raise AuthError(f"网卡 {interface} 没有可用 IPv4 地址")
    return addresses[0]


def interface_is_wireless(interface):
    return Path(f"/sys/class/net/{interface}/wireless").exists()


def parse_route_interfaces(text):
    interfaces = []
    for line in text.splitlines():
        match = re.search(r"\bdev\s+(\S+)", line)
        if not match:
            continue
        interface = normalize_interface_name(match.group(1))
        if not is_virtual_interface(interface) and interface not in interfaces:
            interfaces.append(interface)
    return interfaces


def default_route_interfaces():
    result = run(["ip", "-4", "route", "show", "table", "main", "default"], timeout=3)
    return parse_route_interfaces(result.stdout) if result.returncode == 0 else []


def route_candidate_interfaces(candidates):
    found = []
    for destination in PORTAL_ROUTE_DESTINATIONS:
        identity = route_identity(destination)
        if identity and identity[0] in candidates and identity[0] not in found:
            found.append(identity[0])
    return found


def likely_bjut_interfaces(candidates):
    return [
        name for name, addresses in candidates.items()
        if any(is_likely_bjut_ipv4(address) for address in addresses)
    ]


def strong_route_candidate_interfaces(candidates):
    found = []
    for destination in PORTAL_ROUTE_DESTINATIONS:
        identity = route_identity(destination)
        if (
            identity and identity[0] in candidates and identity[1]
            and is_likely_bjut_ipv4(identity[1]) and identity[0] not in found
        ):
            found.append(identity[0])
    return found


def _probe_candidate_interfaces(candidates, allow_http_fallback):
    detected = []
    for interface in candidates:
        try:
            detect_login_type(interface, allow_http_fallback, probe_timeout=2, retries=0)
        except AuthError:
            continue
        detected.append(interface)
    return detected


def auto_select_interface(allow_http_fallback=False):
    candidates = global_interface_addresses()
    if not candidates:
        raise AuthError("未发现具有可用 IPv4 的非虚拟网卡；网络可能尚未就绪")
    names = list(candidates)
    if len(names) == 1:
        return names[0]

    strong = strong_route_candidate_interfaces(candidates)
    if len(strong) == 1:
        return strong[0]

    likely = likely_bjut_interfaces(candidates)
    if len(likely) == 1:
        return likely[0]

    routed = route_candidate_interfaces(candidates)
    if len(routed) == 1:
        return routed[0]
    if routed:
        ordered = routed + [name for name in names if name not in routed]
        detected = _probe_candidate_interfaces(ordered, allow_http_fallback)
        if len(detected) == 1:
            return detected[0]
        if len(detected) > 1:
            raise AuthError(
                f"多个网卡均可访问校园 Portal：{', '.join(detected)}；"
                "请在配置中显式设置 interface"
            )

    defaults = list(dict.fromkeys(
        name for name in default_route_interfaces() if name in candidates
    ))
    if len(defaults) == 1:
        return defaults[0]

    detected = _probe_candidate_interfaces(names, allow_http_fallback)
    if len(detected) == 1:
        return detected[0]
    if len(detected) > 1:
        raise AuthError(
            f"多个网卡均可访问校园 Portal：{', '.join(detected)}；"
            "请在配置中显式设置 interface"
        )

    summary = ", ".join(
        f"{name}={'/'.join(addresses)}" for name, addresses in candidates.items()
    )
    raise AuthError(
        f"无法在多个候选网卡中确定校园网接口（{summary}）；"
        "请在配置中设置 interface"
    )


def redact_error_text(text):
    for key in SENSITIVE_QUERY_KEYS:
        text = re.sub(
            rf"({re.escape(key)}=)[^&\s]+", r"\1***", text, flags=re.I
        )
    return text


def curl_config_escape(value):
    return (
        value.replace("\\", "\\\\").replace('"', '\\"')
        .replace("\r", "").replace("\n", "")
    )


def split_curl_response(text):
    marker = f"\n{CURL_STATUS_MARKER}"
    position = text.rfind(marker)
    if position < 0:
        raise AuthError("curl 未返回 HTTP 状态码")
    try:
        status = int(text[position + len(marker):].strip())
    except ValueError as exc:
        raise AuthError("curl 返回了无效 HTTP 状态码") from exc
    return text[:position], status


def curl_get(url, interface=None, referer=None, timeout=6, retries=1):
    """Credential-bearing URL is supplied on stdin, never in curl argv."""
    cmd = [
        "curl", "--ipv4", "--silent", "--show-error", "--noproxy", "*",
        "--connect-timeout", "3", "--max-time", str(timeout),
        "--header", "Accept: */*",
        "--header", "Cache-Control: no-cache, no-store",
        "--write-out", f"\n{CURL_STATUS_MARKER}%{{http_code}}",
        "--config", "-",
    ]
    if interface:
        cmd += ["--interface", interface]
    if referer:
        cmd += ["--referer", referer]
    config = f'url = "{curl_config_escape(url)}"\n'

    last_error = None
    for attempt in range(retries + 1):
        result = run(cmd, timeout=timeout + 2, input_text=config)
        if result.returncode != 0:
            detail = redact_error_text((result.stderr or result.stdout).strip())
            last_error = AuthError(detail or f"curl 请求失败（exit={result.returncode}）")
        else:
            try:
                body, status = split_curl_response(result.stdout)
            except AuthError as exc:
                last_error = exc
            else:
                if 200 <= status < 300:
                    return body
                last_error = HttpStatusError(status)
                if status not in TRANSIENT_HTTP_STATUS:
                    raise last_error
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))
    if last_error:
        raise last_error
    raise AuthError("未知 curl 请求错误")


def jsonp_object(text):
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        raise AuthError("认证网关未返回预期 JSONP")
    try:
        value = json.loads(text[start + 1:end])
    except json.JSONDecodeError as exc:
        raise AuthError("认证网关返回的 JSONP 无法解析") from exc
    if not isinstance(value, dict):
        raise AuthError("认证网关返回内容格式异常")
    return value


def parse_login_response(text):
    data = jsonp_object(text)
    try:
        result = int(data.get("result"))
    except (TypeError, ValueError) as exc:
        raise AuthError("认证网关未返回有效 result 字段") from exc
    if result not in (0, 1):
        raise AuthError(f"认证网关返回未知 result={result}")
    message = data.get("msga", data.get("msg", ""))
    if not isinstance(message, str):
        message = json.dumps(message, ensure_ascii=False)
    if not message:
        message = "Portal协议认证成功！" if result == 1 else "登录被认证网关拒绝"
    return result == 1, message


def eportal_encrypt(value):
    raw = value.encode("utf-16-le")
    units = (raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2))
    return "".join(f"{unit ^ EPORTAL_XOR_KEY:02x}" for unit in units)


def get_observed_ipv6(interface, timeout=6, retries=1):
    query = urlencode([
        ("callback", "dr1004"),
        ("program_index", LGN_PROGRAM_INDEX),
        ("page_index", LGN_PAGE_INDEX),
        ("jsVersion", LGN_JS_VERSION),
        ("v", random_request_id()),
        ("lang", "zh"),
    ])
    body = curl_get(
        f"{LGN_IPV6_URL}?{query}", interface, LGN_REFERER,
        timeout=timeout, retries=retries,
    )
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
    return str(address)


def type1_query(username, password, local_ipv4, request_id=None):
    account = username if username.lower().endswith("@campus") else f"{username}@campus"
    return urlencode([
        ("callback", "dr1003"), ("login_method", "1"),
        ("user_account", account), ("user_password", password),
        ("wlan_user_ip", local_ipv4), ("wlan_user_ipv6", ""),
        ("wlan_user_mac", "000000000000"), ("wlan_ac_ip", ""),
        ("wlan_ac_name", ""), ("jsVersion", "4.2.1"),
        ("terminal_type", "3"), ("lang", "zh-cn"),
        ("v", request_id or random_request_id()), ("lang", "zh"),
    ])


def type2_query(username, password, request_id=None):
    return urlencode([
        ("callback", "dr1003"), ("DDDDD", username), ("upass", password),
        ("0MKKey", "123456"), ("R1", "0"), ("R2", ""), ("R3", "0"),
        ("R6", "0"), ("para", "00"), ("v6ip", ""),
        ("terminal_type", "1"), ("lang", "zh-cn"), ("jsVersion", "4.1"),
        ("v", request_id or random_request_id()), ("lang", "zh"),
    ])


def login_type3(username, password, interface):
    local_ipv4 = interface_ipv4(interface, TYPE3_ROUTE_DEST)
    observed_ipv6 = get_observed_ipv6(interface)
    account = username if username.startswith(",0,") else f",0,{username}"
    fields = {
        "callback": "dr1005", "login_method": "1",
        "user_account": account, "user_password": password,
        "wlan_user_ip": local_ipv4, "wlan_user_ipv6": observed_ipv6,
        "wlan_user_mac": "000000000000", "wlan_vlan_id": "0",
        "wlan_ac_ip": "", "wlan_ac_name": "", "authex_enable": "",
        "jsVersion": LGN_JS_VERSION, "login_ip_type": "0",
        "terminal_type": "3", "lang": "zh-cn",
        "program_index": LGN_PROGRAM_INDEX, "page_index": LGN_PAGE_INDEX,
    }
    encrypted = {key: eportal_encrypt(value) for key, value in fields.items()}
    encrypted.update({"encrypt": "1", "v": random_request_id(), "lang": "zh"})
    body = curl_get(
        f"{LGN_LOGIN_URL}?{urlencode(encrypted)}", interface, LGN_REFERER
    )
    return parse_login_response(body)


def login_type1(username, password, interface, allow_http_fallback):
    query = type1_query(
        username, password, interface_ipv4(interface, TYPE1_ROUTE_DEST)
    )
    try:
        body = curl_get(
            f"{DORM_HTTPS_LOGIN}?{query}", interface, DORM_HTTPS_REFERER
        )
    except AuthError:
        if not allow_http_fallback:
            raise
        body = curl_get(
            f"{DORM_HTTP_LOGIN}?{query}", interface, DORM_HTTP_REFERER
        )
    return parse_login_response(body)


def login_type2(username, password, interface, allow_http_fallback):
    query = type2_query(username, password)
    try:
        body = curl_get(
            f"{WIFI_HTTPS_LOGIN}?{query}", interface, WIFI_HTTPS_REFERER
        )
    except AuthError:
        if not allow_http_fallback:
            raise
        body = curl_get(
            f"{WIFI_HTTP_LOGIN}?{query}", interface, WIFI_HTTP_REFERER
        )
    return parse_login_response(body)


def probe_body_matches(login_type, body):
    if not body.strip():
        return False
    normalized = body.lower()
    if login_type == "1" and ("eportal" in normalized or "user_account" in normalized):
        return True
    if login_type == "2" and any(x in normalized for x in ("drcom", "ddddd", "0mkkey")):
        return True
    try:
        return "result" in jsonp_object(body)
    except AuthError:
        return False


def detect_login_type(interface, allow_http_fallback, probe_timeout=3, retries=1):
    if not interface_is_wireless(interface):
        get_observed_ipv6(interface, timeout=probe_timeout, retries=retries)
        return "3"

    probes = [
        ("2", WIFI_HTTPS_LOGIN, WIFI_HTTPS_REFERER),
        ("1", DORM_HTTPS_LOGIN, DORM_HTTPS_REFERER),
    ]
    if allow_http_fallback:
        probes += [
            ("2", WIFI_HTTP_LOGIN, WIFI_HTTP_REFERER),
            ("1", DORM_HTTP_LOGIN, DORM_HTTP_REFERER),
        ]
    for login_type, url, referer in probes:
        try:
            body = curl_get(
                url, interface, referer, timeout=probe_timeout, retries=retries
            )
        except AuthError:
            continue
        if probe_body_matches(login_type, body):
            return login_type
    raise AuthError("无法自动识别校园网认证类型，请显式指定 --type 1、2 或 3")


def validate_connectivity_url(value):
    if any(c in value for c in ("\r", "\n", "\x00")):
        raise AuthError("connectivity_url 不允许包含控制字符")
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AuthError("connectivity_url 必须是有效的 http:// 或 https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise AuthError("connectivity_url 不允许包含用户名或密码")
    return value


def internet_online(interface, url=DEFAULT_CONNECTIVITY_URL):
    url = validate_connectivity_url(url)
    cmd = [
        "curl", "--noproxy", "*", "--silent", "--output", "/dev/null",
        "--connect-timeout", "3", "--max-time", "5",
        "--write-out", "%{http_code}",
    ]
    if interface:
        cmd += ["--interface", interface]
    cmd += ["--config", "-"]
    result = run(
        cmd, timeout=7, input_text=f'url = "{curl_config_escape(url)}"\n'
    )
    try:
        code = int(result.stdout.strip())
    except ValueError:
        return False
    return result.returncode == 0 and 200 <= code < 300


def cfg_bool(config, key, default=False):
    value = config.get(key)
    if value is None or not value.strip():
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise AuthError(f"配置项 {key} 必须为 true/false、yes/no、on/off 或 1/0")


def connectivity_url(config):
    value = config.get("connectivity_url", DEFAULT_CONNECTIVITY_URL).strip()
    return validate_connectivity_url(value or DEFAULT_CONNECTIVITY_URL)


def login_type_value(config):
    value = config.get("type", "auto").strip().lower() or "auto"
    if value not in {"auto", "1", "2", "3"}:
        raise AuthError("配置项 type 必须为 auto、1、2 或 3")
    return value


def load_config(path):
    if path:
        target = Path(path)
        if not target.exists():
            raise AuthError(f"指定的配置文件不存在：{target}")
    else:
        candidates = [
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
        raise AuthError(f"配置文件无法解析：{target}") from exc
    if "BJUT" not in parser:
        raise AuthError(f"配置文件缺少 [BJUT]：{target}")

    config = dict(parser["BJUT"])
    unknown = sorted(set(config) - ALLOWED_CONFIG_KEYS)
    if unknown:
        raise AuthError(f"配置文件包含未知字段：{', '.join(unknown)}")
    cfg_bool(config, "allow_http_fallback", False)
    login_type_value(config)
    connectivity_url(config)
    return config, target


def config_permission_warning(path, config):
    if path is None or not config.get("password"):
        return None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return f"配置文件 {path} 权限为 {mode:o}，包含密码时必须限制为 600"
    return None


def credential_values(args, config):
    username = args.username or os.getenv("BJUT_USERNAME") or config.get("username", "")
    password = args.password or os.getenv("BJUT_PASSWORD") or config.get("password", "")
    return username.strip(), password


def credential_issue(args, config, config_only=False):
    if config_only:
        username, password = config.get("username", "").strip(), config.get("password", "")
    else:
        username, password = credential_values(args, config)
    if not username:
        return "缺少用户名"
    if username.lower() in PLACEHOLDER_USERNAMES:
        return "用户名仍是占位符"
    if not password:
        return "缺少密码"
    if password.strip().lower() in PLACEHOLDER_PASSWORDS:
        return "密码仍是示例占位符"
    return None


def resolve_credentials(args, config):
    username, password = credential_values(args, config)
    if not username:
        raise AuthError("缺少用户名：使用 --username、BJUT_USERNAME 或配置文件")
    if username.lower() in PLACEHOLDER_USERNAMES:
        raise AuthError("用户名仍是示例占位符，请填写真实校园网账号")
    if not password:
        if sys.stdin.isatty():
            password = getpass.getpass("BJUT password: ")
        else:
            raise AuthError("缺少密码：无人值守模式请写入权限为 600 的配置文件")
    if password.strip().lower() in PLACEHOLDER_PASSWORDS:
        raise AuthError("密码仍是示例占位符，请填写真实校园网密码")
    return username, password


def resolve_interface(args, config, allow_http_fallback=False):
    explicit = (args.interface or config.get("interface", "")).strip()
    if explicit:
        interface = normalize_interface_name(explicit)
        if is_virtual_interface(interface):
            raise AuthError(f"拒绝通过疑似 VPN/TUN 网卡认证：{interface}")
        interface_ipv4(interface)
        return interface
    return auto_select_interface(allow_http_fallback)


def do_login(args, config, interface=None):
    username, password = resolve_credentials(args, config)
    allow_http = args.allow_http_fallback or cfg_bool(
        config, "allow_http_fallback", False
    )
    interface = interface or resolve_interface(args, config, allow_http)
    login_type = args.login_type or login_type_value(config)

    if login_type == "auto":
        login_type = (
            detect_login_type(interface, allow_http)
            if interface_is_wireless(interface) else "3"
        )
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


def do_doctor(args, config, config_path):
    failures = 0
    print(f"BJUT Auto Login {VERSION}")
    print(f"python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    missing = False
    for command in ("curl", "ip"):
        path = shutil.which(command)
        print(f"{command}: {'OK (' + path + ')' if path else 'MISSING'}")
        if not path:
            failures += 1
            missing = True

    if config_path:
        print(f"config: {config_path}")
        warning = config_permission_warning(config_path, config)
        if warning:
            print(f"config-security: ERROR ({warning})")
            failures += 1
        else:
            print("config-security: OK")
    else:
        print("config: not found")

    issue = credential_issue(args, config, config_only=config_path is not None)
    if issue:
        print(f"credentials: ERROR ({issue})")
        failures += 1
    else:
        source = "config" if config_path is not None else "runtime"
        print(f"credentials: configured ({source})")
    if missing:
        return 1

    allow_http = args.allow_http_fallback or cfg_bool(
        config, "allow_http_fallback", False
    )
    try:
        candidates = global_interface_addresses()
        if candidates:
            summary = ", ".join(
                f"{name}={'/'.join(addresses)}"
                for name, addresses in candidates.items()
            )
            print(f"interfaces: {summary}")
        interface = resolve_interface(args, config, allow_http)
        wireless = interface_is_wireless(interface)
        print(f"interface: {interface} ({'Wi-Fi' if wireless else 'wired'})")
        print(
            f"ipv4: {interface_ipv4(interface, None if wireless else TYPE3_ROUTE_DEST)}"
        )
        print(
            "internet: "
            + ("online" if internet_online(interface, connectivity_url(config)) else "offline")
        )
        try:
            print(f"portal: type {detect_login_type(interface, allow_http)}")
        except AuthError as exc:
            print(f"portal: ERROR ({exc})")
            failures += 1
    except AuthError as exc:
        print(f"network: ERROR ({exc})")
        failures += 1
    return 0 if failures == 0 else 1


def build_parser():
    parser = argparse.ArgumentParser(
        description="BJUT campus network headless auto-login"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("-u", "--username")
    parser.add_argument("-p", "--password", help="不建议在多用户机器命令行中直接传密码")
    parser.add_argument("-i", "--interface", help="校园网物理网卡，例如 eno1 / enp7s0 / wlan0")
    parser.add_argument("--type", dest="login_type", choices=["auto", "1", "2", "3"])
    parser.add_argument(
        "--allow-http-fallback", action="store_true",
        help="允许宿舍/Wi-Fi HTTPS 失败后回退到明文 HTTP，仅可信校园网使用",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("detect", help="检测认证类型")
    sub.add_parser("login", help="执行一次认证")
    sub.add_parser("status", help="检查外网连通性")
    sub.add_parser("ensure", help="在线跳过，否则认证一次；适合 systemd timer")
    sub.add_parser("doctor", help="检查依赖、配置、网卡和 Portal 可达性")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        config, config_path = load_config(args.config)
        warning = config_permission_warning(config_path, config)
        if warning and args.command not in ("doctor", "status", "detect"):
            print(f"warning: {warning}", file=sys.stderr)
        if args.command == "doctor":
            return do_doctor(args, config, config_path)

        allow_http = args.allow_http_fallback or cfg_bool(
            config, "allow_http_fallback", False
        )
        interface = resolve_interface(args, config, allow_http)
        check_url = connectivity_url(config)

        if args.command == "status":
            online = internet_online(interface, check_url)
            print("online" if online else "offline")
            return 0 if online else 1

        if args.command == "detect":
            login_type = args.login_type or login_type_value(config)
            if login_type == "auto":
                login_type = detect_login_type(interface, allow_http)
            print(f"type={login_type} interface={interface}")
            return 0

        if args.command == "ensure":
            issue = credential_issue(args, config)
            if issue:
                raise AuthError(f"{issue}；ensure 无法保证后续自动重连")
            if internet_online(interface, check_url):
                print(f"online: interface={interface}, skip login")
                return 0

        if not do_login(args, config, interface):
            return 2
        if args.command == "ensure":
            if internet_online(interface, check_url):
                return 0
            print("error: Portal 返回认证成功，但公网连通性仍为 offline", file=sys.stderr)
            return 3
        return 0
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Type 3 IPv6 health monitor for BJUT Auto Login."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import importlib.machinery
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

VERSION = "0.1.0"
BJUT_IPV6_PREFIX = ipaddress.IPv6Network("2001:da8:216::/48")
AUTH_LOCK_PATH = Path("/run/lock/bjut-auto-login.lock")
STATE_PATH = Path("/run/lock/bjut-auto-login-ipv6-watch.json")
DEFAULT_FAILURE_THRESHOLD = 2
DEFAULT_COOLDOWN_SECONDS = 300


class WatchError(RuntimeError):
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
    raise WatchError(
        "找不到 bjut-auth 核心程序；请先运行 install.sh，或设置 BJUT_AUTH_CORE"
    )


def parse_bool(value, default=True):
    if value is None or not str(value).strip():
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise WatchError("IPv6Watch.enabled 必须为 true/false、yes/no、on/off 或 1/0")


def parse_int(value, default, minimum, maximum, label):
    if value is None or not str(value).strip():
        return default
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise WatchError(f"{label} 必须为整数") from exc
    if not minimum <= number <= maximum:
        raise WatchError(f"{label} 必须在 {minimum}–{maximum} 范围内")
    return number


def load_watch_config(path):
    settings = {
        "enabled": True,
        "failures": DEFAULT_FAILURE_THRESHOLD,
        "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
    }
    if path is None:
        return settings

    parser = configparser.ConfigParser(interpolation=None)
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        raise WatchError(f"IPv6 监控配置无法解析：{path}") from exc

    if "IPv6Watch" not in parser:
        return settings
    section = parser["IPv6Watch"]
    unknown = sorted(set(section) - {"enabled", "failures", "cooldown_seconds"})
    if unknown:
        raise WatchError(f"IPv6Watch 包含未知字段：{', '.join(unknown)}")
    settings["enabled"] = parse_bool(section.get("enabled"), True)
    settings["failures"] = parse_int(
        section.get("failures"), DEFAULT_FAILURE_THRESHOLD, 1, 10,
        "IPv6Watch.failures",
    )
    settings["cooldown_seconds"] = parse_int(
        section.get("cooldown_seconds"), DEFAULT_COOLDOWN_SECONDS, 0, 86400,
        "IPv6Watch.cooldown_seconds",
    )
    return settings


def parse_bjut_ipv6_addresses(text):
    addresses = []
    for line in text.splitlines():
        match = re.search(r"\binet6\s+([0-9a-fA-F:]+)/\d+", line)
        if not match:
            continue
        try:
            address = ipaddress.IPv6Address(match.group(1))
        except ipaddress.AddressValueError:
            continue
        if address in BJUT_IPV6_PREFIX and str(address) not in addresses:
            addresses.append(str(address))
    return addresses


def interface_bjut_ipv6_addresses(core, interface):
    result = core.run(
        ["ip", "-6", "-o", "addr", "show", "dev", interface, "scope", "global"],
        timeout=3,
    )
    if result.returncode != 0:
        raise WatchError(f"无法读取网卡 {interface} 的 IPv6 地址")
    return parse_bjut_ipv6_addresses(result.stdout)


def ipv6_health(core, interface):
    local_addresses = interface_bjut_ipv6_addresses(core, interface)
    if not local_addresses:
        return False, "物理网卡没有 BJUT 全局 IPv6 地址"

    try:
        observed = core.get_observed_ipv6(interface, timeout=3, retries=0)
    except core.AuthError as exc:
        return False, f"Portal IPv6 地址发现失败：{exc}"

    try:
        observed_ip = ipaddress.IPv6Address(observed)
    except ipaddress.AddressValueError:
        return False, "Portal 返回了无效 IPv6 地址"
    if observed_ip not in BJUT_IPV6_PREFIX:
        return False, f"Portal 返回的 IPv6 不属于 BJUT 地址段：{observed}"
    normalized = str(observed_ip)
    if normalized not in local_addresses:
        return False, f"Portal IPv6 与本机地址不一致：portal={normalized}"
    return True, normalized


def load_state(path=STATE_PATH):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"failures": 0, "last_attempt": 0.0}
    try:
        failures = max(0, int(raw.get("failures", 0)))
        last_attempt = max(0.0, float(raw.get("last_attempt", 0.0)))
    except (TypeError, ValueError):
        return {"failures": 0, "last_attempt": 0.0}
    return {"failures": failures, "last_attempt": last_attempt}


def save_state(state, path=STATE_PATH):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except OSError as exc:
        raise WatchError(f"无法写入 IPv6 监控状态：{path}") from exc


def auth_lock_busy(path=AUTH_LOCK_PATH):
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def resolve_login_type(core, config, interface, allow_http):
    login_type = core.login_type_value(config)
    if login_type != "auto":
        return login_type
    if not core.interface_is_wireless(interface):
        return "3"
    return core.detect_login_type(
        interface, allow_http, require_login_ready=False
    )


def relogin_command(config_path):
    executable = shutil.which("bjut-relogin")
    if not executable:
        installed = Path("/usr/local/bin/bjut-relogin")
        if installed.is_file():
            executable = str(installed)
    if not executable:
        raise WatchError("找不到 bjut-relogin；请重新运行 install.sh")
    cmd = [executable]
    if config_path is not None:
        cmd += ["--config", str(config_path)]
    cmd += ["--type", "3", "relogin"]
    return cmd


def run_watch(core, config, config_path, watch_config, state_path=STATE_PATH, now=None):
    if not watch_config["enabled"]:
        save_state({"failures": 0, "last_attempt": 0.0}, state_path)
        return 0

    allow_http = core.cfg_bool(config, "allow_http_fallback", False)
    runtime_args = argparse.Namespace(interface=None)
    interface = core.resolve_interface(runtime_args, config, allow_http)
    login_type = resolve_login_type(core, config, interface, allow_http)
    if login_type != "3":
        save_state({"failures": 0, "last_attempt": 0.0}, state_path)
        return 0

    if auth_lock_busy():
        print("ipv6-watch: 认证锁忙，跳过本次检查")
        return 0

    check_url = core.connectivity_url(config)
    resolve_ip = core.connectivity_resolve_ip(config)
    if not core.internet_online(interface, check_url, resolve_ip):
        print("ipv6-watch: IPv4 公网离线，交由 ensure 恢复")
        return 0

    healthy, detail = ipv6_health(core, interface)
    state = load_state(state_path)
    if healthy:
        if state["failures"]:
            save_state({"failures": 0, "last_attempt": state["last_attempt"]}, state_path)
        print(f"ipv6-watch: healthy: interface={interface}, ipv6={detail}")
        return 0

    threshold = watch_config["failures"]
    state["failures"] = min(state["failures"] + 1, threshold)
    save_state(state, state_path)
    print(
        f"warning: IPv6 健康检查异常（{state['failures']}/{threshold}）：{detail}",
        file=sys.stderr,
    )
    if state["failures"] < threshold:
        return 0

    current = time.time() if now is None else float(now)
    cooldown = watch_config["cooldown_seconds"]
    elapsed = current - state["last_attempt"]
    if state["last_attempt"] > 0 and elapsed < cooldown:
        remaining = max(0, int(cooldown - elapsed))
        print(f"ipv6-watch: 重新认证冷却中，约 {remaining}s 后可再次尝试")
        return 0

    state["last_attempt"] = current
    save_state(state, state_path)
    print("ipv6-watch: 连续异常达到阈值，触发 Type 3 重新认证")
    try:
        result = subprocess.run(relogin_command(config_path), timeout=220, check=False)
    except subprocess.TimeoutExpired:
        print("warning: IPv6 自动重新认证超时，将在冷却后重试", file=sys.stderr)
        return 0

    if result.returncode == 0:
        save_state({"failures": 0, "last_attempt": current}, state_path)
        print("ipv6-watch: IPv6 自动恢复完成")
    else:
        print(
            f"warning: IPv6 自动重新认证失败（exit={result.returncode}），将在冷却后重试",
            file=sys.stderr,
        )
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="BJUT Type 3 IPv6 health monitor")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--config", help="配置文件路径")
    return parser


def main():
    args = build_parser().parse_args()
    try:
        core = load_core()
        config, config_path = core.load_config(args.config)
        watch_config = load_watch_config(config_path)
        return run_watch(core, config, config_path, watch_config)
    except Exception as exc:
        # IPv6 monitoring is auxiliary and must never break IPv4 auto-login.
        print(f"warning: ipv6-watch 检查失败：{exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

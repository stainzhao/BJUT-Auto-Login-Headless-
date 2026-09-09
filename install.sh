#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WITH_SYSTEMD=1

case "${1:-}" in
  "")
    ;;
  --no-systemd)
    WITH_SYSTEMD=0
    ;;
  -h|--help)
    echo "Usage: sudo ./install.sh [--no-systemd]"
    exit 0
    ;;
  *)
    echo "未知参数：${1}" >&2
    exit 2
    ;;
esac

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "请使用 root 权限运行：sudo ./install.sh" >&2
  exit 1
fi

for cmd in python3 curl ip; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "缺少依赖：$cmd" >&2
    exit 1
  fi
done

if (( WITH_SYSTEMD )); then
  if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    echo "当前系统未运行 systemd；如仅安装 CLI，可使用 --no-systemd" >&2
    exit 1
  fi
  if ! command -v flock >/dev/null 2>&1; then
    echo "缺少依赖：flock（通常由 util-linux 提供）" >&2
    exit 1
  fi
fi

python3 - "$SCRIPT_DIR/bjut_auth.py" "$SCRIPT_DIR/bjut_relogin.py" <<'PY'
from pathlib import Path
import sys

if sys.version_info < (3, 8):
    raise SystemExit("需要 Python >= 3.8")

for value in sys.argv[1:]:
    path = Path(value)
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
PY

install -m 0755 "$SCRIPT_DIR/bjut_auth.py" /usr/local/bin/bjut-auth
install -m 0755 "$SCRIPT_DIR/bjut_relogin.py" /usr/local/bin/bjut-relogin

if [[ ! -e /etc/bjut-auto-login.conf ]]; then
  install -m 0600 "$SCRIPT_DIR/config.example.ini" /etc/bjut-auto-login.conf
  echo "已创建 /etc/bjut-auto-login.conf"
else
  chown root:root /etc/bjut-auto-login.conf
  chmod 0600 /etc/bjut-auto-login.conf
fi

if (( WITH_SYSTEMD )); then
  install -m 0644 "$SCRIPT_DIR/systemd/bjut-auto-login.service" /etc/systemd/system/bjut-auto-login.service
  install -m 0644 "$SCRIPT_DIR/systemd/bjut-auto-login.timer" /etc/systemd/system/bjut-auto-login.timer
  install -m 0644 "$SCRIPT_DIR/systemd/bjut-auto-login-event.timer" /etc/systemd/system/bjut-auto-login-event.timer
  install -m 0644 "$SCRIPT_DIR/systemd/bjut-auto-relogin.service" /etc/systemd/system/bjut-auto-relogin.service
  install -m 0644 "$SCRIPT_DIR/systemd/bjut-auto-relogin.timer" /etc/systemd/system/bjut-auto-relogin.timer

  NETWORK_EVENT_BACKEND=0
  if systemctl cat NetworkManager.service >/dev/null 2>&1; then
    install -d -m 0755 /etc/NetworkManager/dispatcher.d
    install -m 0755 "$SCRIPT_DIR/NetworkManager/dispatcher.d/90-bjut-auto-login" /etc/NetworkManager/dispatcher.d/90-bjut-auto-login
    NETWORK_EVENT_BACKEND=1
  fi

  systemctl daemon-reload

  # During upgrades, reload already-running timers so new unit definitions take
  # effect immediately. First-time installs remain disabled until verified.
  if systemctl is-active --quiet bjut-auto-login.timer; then
    systemctl try-restart bjut-auto-login.timer
  fi
  if systemctl is-active --quiet bjut-auto-relogin.timer; then
    systemctl try-restart bjut-auto-relogin.timer
  fi
fi

echo "安装完成。"
echo "下一步："
echo "  sudo nano /etc/bjut-auto-login.conf"
echo "  sudo bjut-auth --config /etc/bjut-auto-login.conf doctor"
echo "  sudo bjut-auth --config /etc/bjut-auto-login.conf ensure"
echo "  sudo bjut-relogin --config /etc/bjut-auto-login.conf relogin"
if (( WITH_SYSTEMD )); then
  echo "  sudo systemctl enable --now bjut-auto-login.timer"
  echo "  定时强制重新认证：已安装但未自动启用"
  echo "  手动触发：sudo systemctl start bjut-auto-relogin.service"
  echo "  如需启用定时器：sudo systemctl enable --now bjut-auto-relogin.timer"
  if (( NETWORK_EVENT_BACKEND )); then
    echo "  NetworkManager 网络变化事件触发：已安装（2 秒防抖）"
  else
    echo "  NetworkManager 未检测到：保留 60 秒 systemd timer 兜底"
  fi
else
  echo "已跳过 systemd 单元安装。"
fi

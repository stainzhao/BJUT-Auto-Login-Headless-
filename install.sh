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
fi

python3 - "$SCRIPT_DIR/bjut_auth.py" <<'PY'
from pathlib import Path
import sys

if sys.version_info < (3, 8):
    raise SystemExit("需要 Python >= 3.8")

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
compile(source, str(path), "exec")
PY

install -m 0755 "$SCRIPT_DIR/bjut_auth.py" /usr/local/bin/bjut-auth

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
  systemctl daemon-reload

  # During upgrades, reload an already-running timer so the new unit definition
  # takes effect immediately. First-time installs remain disabled until verified.
  if systemctl is-active --quiet bjut-auto-login.timer; then
    systemctl try-restart bjut-auto-login.timer
  fi
fi

echo "安装完成。"
echo "下一步："
echo "  sudo nano /etc/bjut-auto-login.conf"
echo "  sudo bjut-auth --config /etc/bjut-auto-login.conf doctor"
echo "  sudo bjut-auth --config /etc/bjut-auto-login.conf ensure"
if (( WITH_SYSTEMD )); then
  echo "  sudo systemctl enable --now bjut-auto-login.timer"
else
  echo "已跳过 systemd 单元安装。"
fi

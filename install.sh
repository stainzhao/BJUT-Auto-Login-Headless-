#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "请使用 root 权限运行：sudo bash install.sh" >&2
  exit 1
fi

for cmd in python3 curl ip systemctl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "缺少依赖：$cmd" >&2
    exit 1
  fi
done

python3 - <<'PY'
import sys
if sys.version_info < (3, 8):
    raise SystemExit("需要 Python >= 3.8")
PY

install -m 0755 "$SCRIPT_DIR/bjut_auth.py" /usr/local/bin/bjut-auth
install -m 0644 "$SCRIPT_DIR/systemd/bjut-auto-login.service" /etc/systemd/system/bjut-auto-login.service
install -m 0644 "$SCRIPT_DIR/systemd/bjut-auto-login.timer" /etc/systemd/system/bjut-auto-login.timer

if [[ ! -e /etc/bjut-auto-login.conf ]]; then
  install -m 0600 "$SCRIPT_DIR/config.example.ini" /etc/bjut-auto-login.conf
  echo "已创建 /etc/bjut-auto-login.conf"
else
  chown root:root /etc/bjut-auto-login.conf
  chmod 0600 /etc/bjut-auto-login.conf
fi

systemctl daemon-reload

echo "安装完成。下一步："
echo "  sudo nano /etc/bjut-auto-login.conf"
echo "  sudo bjut-auth --config /etc/bjut-auto-login.conf doctor"
echo "  sudo bjut-auth --config /etc/bjut-auto-login.conf login"
echo "  sudo systemctl enable --now bjut-auto-login.timer"

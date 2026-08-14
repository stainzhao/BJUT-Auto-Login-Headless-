#!/usr/bin/env bash
set -euo pipefail

PURGE=0
case "${1:-}" in
  "")
    ;;
  --purge)
    PURGE=1
    ;;
  -h|--help)
    echo "Usage: sudo ./uninstall.sh [--purge]"
    exit 0
    ;;
  *)
    echo "未知参数：${1}" >&2
    exit 2
    ;;
esac

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "请使用 root 权限运行：sudo ./uninstall.sh" >&2
  exit 1
fi

HAS_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
  HAS_SYSTEMD=1
  systemctl disable --now bjut-auto-login.timer >/dev/null 2>&1 || true
fi

rm -f /etc/systemd/system/bjut-auto-login.timer
rm -f /etc/systemd/system/bjut-auto-login.service
rm -f /usr/local/bin/bjut-auth

if (( HAS_SYSTEMD )); then
  systemctl daemon-reload
  systemctl reset-failed bjut-auto-login.service >/dev/null 2>&1 || true
fi

if (( PURGE )); then
  rm -f /etc/bjut-auto-login.conf
  echo "已同时删除 /etc/bjut-auto-login.conf"
else
  echo "已保留 /etc/bjut-auto-login.conf；如需删除请使用 sudo ./uninstall.sh --purge"
fi

echo "卸载完成。"

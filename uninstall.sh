#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "请使用 root 权限运行：sudo bash uninstall.sh" >&2
  exit 1
fi

systemctl disable --now bjut-auto-login.timer >/dev/null 2>&1 || true
rm -f /etc/systemd/system/bjut-auto-login.timer /etc/systemd/system/bjut-auto-login.service
rm -f /usr/local/bin/bjut-auth
systemctl daemon-reload

if [[ ${1:-} == "--purge" ]]; then
  rm -f /etc/bjut-auto-login.conf
  echo "已同时删除 /etc/bjut-auto-login.conf"
else
  echo "已保留 /etc/bjut-auto-login.conf；如需删除请使用 sudo bash uninstall.sh --purge"
fi

echo "卸载完成。"

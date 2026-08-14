#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Please run: sudo ./install.sh" >&2
  exit 1
fi

install -m 0755 bjut_auth.py /usr/local/bin/bjut-auth
install -m 0644 systemd/bjut-auto-login.service /etc/systemd/system/bjut-auto-login.service
install -m 0644 systemd/bjut-auto-login.timer /etc/systemd/system/bjut-auto-login.timer

if [[ ! -e /etc/bjut-auto-login.conf ]]; then
  install -m 0600 config.example.ini /etc/bjut-auto-login.conf
  echo "Created /etc/bjut-auto-login.conf"
  echo "Edit username/password before enabling the timer."
else
  chmod 0600 /etc/bjut-auto-login.conf
fi

systemctl daemon-reload

echo "Installed. Next:"
echo "  sudo nano /etc/bjut-auto-login.conf"
echo "  sudo bjut-auth --config /etc/bjut-auto-login.conf login"
echo "  sudo systemctl enable --now bjut-auto-login.timer"

#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-kalshi-bot}"

echo "[HEALTH] systemd_active=$(systemctl is-active "$SERVICE_NAME" || true)"
echo "[HEALTH] process_list"
pgrep -af "python3 .*kalshi_web.py|run_bot.sh" || true
echo "[HEALTH] recent_logs"
journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true

#!/usr/bin/env bash
set -euo pipefail

TARGET_USER="${SUDO_USER:-kevinadmin}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | awk -F: 'NR==1 {print $6}')"
SYSLOG_PATH="/var/log/syslog"
SYSLOG_MAX_BYTES=$((300 * 1024 * 1024))

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

if [[ -z "${TARGET_HOME:-}" || ! -d "$TARGET_HOME" ]]; then
  TARGET_HOME="/home/kevinadmin"
fi

print_df() {
  echo "[MAINTENANCE-CLEANUP] df -h"
  df -h
}

cleanup_path() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    rm -rf "$path"
  fi
}

print_df

journalctl --vacuum-size=300M || true
journalctl --vacuum-time=3d || true

python3 -m pip cache purge || true

cleanup_path "$TARGET_HOME/.cache/pip"
cleanup_path "$TARGET_HOME/.cache/pypoetry"
cleanup_path "$TARGET_HOME/.npm/_cacache"
cleanup_path "$TARGET_HOME/.npm/_logs"
cleanup_path "$TARGET_HOME/.npm/_npx"

cleanup_path "$TARGET_HOME/.codex/log"
cleanup_path "$TARGET_HOME/.codex/logs_2.sqlite"
cleanup_path "$TARGET_HOME/.codex/logs_2.sqlite-wal"
cleanup_path "$TARGET_HOME/.codex/logs_2.sqlite-shm"
cleanup_path "$TARGET_HOME/.codex/history.jsonl"
cleanup_path "$TARGET_HOME/.codex/sessions"
cleanup_path "$TARGET_HOME/.codex/cache"
cleanup_path "$TARGET_HOME/.codex/tmp"
cleanup_path "$TARGET_HOME/.codex/shell_snapshots"
cleanup_path "$TARGET_HOME/.codex/state_5.sqlite-wal"
cleanup_path "$TARGET_HOME/.codex/state_5.sqlite-shm"

rm -rf /tmp/* || true
apt-get clean || true
apt-get autoremove -y || true

if [[ -f "$SYSLOG_PATH" ]]; then
  syslog_size=$(stat -c '%s' "$SYSLOG_PATH" 2>/dev/null || echo 0)
  if [[ "$syslog_size" -gt "$SYSLOG_MAX_BYTES" ]]; then
    echo "[MAINTENANCE-CLEANUP] truncating runaway syslog size=${syslog_size}"
    truncate -s 0 "$SYSLOG_PATH"
  fi
fi

print_df

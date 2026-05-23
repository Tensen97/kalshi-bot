#!/usr/bin/env bash
set -euo pipefail

warn_high_usage=0

echo "== Disk =="
df -h /

echo
echo "== Journal =="
if journal_output=$(journalctl --disk-usage 2>/dev/null); then
  echo "$journal_output"
else
  echo "journalctl --disk-usage unavailable (insufficient permissions)"
  warn_high_usage=1
fi

echo
echo "== Largest /var/log Files =="
find /var/log -xdev -type f -printf '%s %p\n' 2>/dev/null | sort -n | tail -n 20 | awk '
  function human(n) {
    split("B KB MB GB TB", units, " ")
    u = 1
    while (n >= 1024 && u < 5) {
      n /= 1024
      u++
    }
    printf "%.1f%s", n, units[u]
  }
  { printf "%-12s %s\n", human($1), $2 }
'

echo
echo "== Journal Files =="
ls -lh /var/log/journal 2>/dev/null || echo "no journal directory visible"

disk_used_percent=$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')
if [[ -n "${disk_used_percent:-}" && "$disk_used_percent" -ge 85 ]]; then
  warn_high_usage=1
fi

echo
if [[ "$warn_high_usage" -eq 1 ]]; then
  echo "WARNING: log or disk usage could not be fully verified or is high."
else
  echo "Log health looks within expected bounds."
fi

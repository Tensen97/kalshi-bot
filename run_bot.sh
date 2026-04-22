#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f ".env" ]]; then
  echo "[RUN-BOT] missing .env in $SCRIPT_DIR" >&2
  exit 1
fi

set -a
source .env
set +a

source .venv/bin/activate
exec python3 kalshi_web.py --live --arm-live

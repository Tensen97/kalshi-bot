"""Small risk helpers moved out of kalshi_web.py."""

import os


def compute_effective_live_dry_run(
    live_mode=False,
    live_armed=False,
    live_env_confirmed=False,
    configured_dry_run=False,
    allow_live_raw=None,
):
    if allow_live_raw is None:
        allow_live_raw = os.environ.get("KALSHI_ALLOW_LIVE", "")
    allow_live_raw = (allow_live_raw or "").strip().lower()
    allow_live_enabled = allow_live_raw in {"1", "true", "yes", "on"}
    if live_armed and allow_live_enabled:
        return False
    return bool(configured_dry_run or not (live_mode and live_armed and live_env_confirmed))

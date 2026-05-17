from __future__ import annotations

import builtins
import json

from risk.helpers import safe_float


def compact_float(value, precision=4, default=0.0):
    try:
        return round(safe_float(value, default), int(precision))
    except Exception:
        return round(safe_float(default, 0.0), int(precision))


def safe_tag(value, fallback="unknown"):
    text = str(value or "").strip()
    return text if text else fallback


def render_kv(prefix, **fields):
    payload = {key: value for key, value in fields.items() if value is not None}
    builtins.print(prefix + " " + json.dumps(payload, sort_keys=True, default=str))
    return payload


builtins.print("[AION-MODULE-LOAD] " + json.dumps({"module": "strategy.formatting", "helpers": ["compact_float", "safe_tag", "render_kv"]}, sort_keys=True))


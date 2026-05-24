from __future__ import annotations

import builtins
import json


def safe_float(value, default=0.0):
    try:
        if value is None:
            if default is None:
                return None
            return float(default)
        return float(value)
    except Exception:
        if default is None:
            return None
        return float(default)


def clamp(value, low=0.0, high=1.0):
    value = safe_float(value, low)
    if low > high:
        low, high = high, low
    return max(low, min(high, value))


def safe_ratio(numerator, denominator, default=0.0):
    denominator = safe_float(denominator, 0.0)
    if denominator == 0.0:
        return safe_float(default, 0.0)
    return safe_float(numerator, 0.0) / denominator


def bounded_delta(value, limit=0.08):
    limit = abs(safe_float(limit, 0.08))
    return max(-limit, min(limit, safe_float(value, 0.0)))


def log_module_load(module_name, **fields):
    payload = {"module": module_name}
    payload.update({key: value for key, value in fields.items() if value is not None})
    builtins.print("[AION-MODULE-LOAD] " + json.dumps(payload, sort_keys=True, default=str))


log_module_load("risk.helpers", helpers=["safe_float", "clamp", "safe_ratio", "bounded_delta"])

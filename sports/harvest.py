from __future__ import annotations

import builtins
import json
import time
from copy import deepcopy

from risk.helpers import safe_float


def get_or_create_bucket(container, key, defaults=None):
    if not isinstance(container, dict):
        return {}
    if key not in container or not isinstance(container.get(key), dict):
        container[key] = deepcopy(defaults or {})
    return container[key]


def build_harvest_dashboard_summary(watchlist, live_arb_enabled=False):
    buckets = [bucket for bucket in (watchlist or {}).values() if isinstance(bucket, dict)]
    if not buckets:
        return {
            "watchlist_count": 0,
            "improving_count": 0,
            "near_harvest_count": 0,
            "best_ticker": None,
            "best_family": None,
            "best_edge_after_cost": 0.0,
            "best_score": 0.0,
            "best_spread_bucket": None,
            "live_arb_enabled": bool(live_arb_enabled),
        }
    best_bucket = max(
        buckets,
        key=lambda bucket: (
            safe_float(bucket.get("best_edge_after_cost"), -1.0),
            safe_float(bucket.get("best_score"), 0.0),
        ),
    )
    return {
        "watchlist_count": int(len(buckets)),
        "improving_count": int(sum(int(bucket.get("improving_count", 0) or 0) for bucket in buckets)),
        "near_harvest_count": int(sum(int(bucket.get("near_harvest_count", 0) or 0) for bucket in buckets)),
        "best_ticker": str(best_bucket.get("ticker") or "").strip() or None,
        "best_family": str(best_bucket.get("family") or "").strip().upper() or None,
        "best_edge_after_cost": round(safe_float(best_bucket.get("best_edge_after_cost"), 0.0), 4),
        "best_score": round(safe_float(best_bucket.get("best_score"), 0.0), 4),
        "best_spread_bucket": str(best_bucket.get("best_spread_bucket") or "unknown"),
        "live_arb_enabled": bool(live_arb_enabled),
    }


def should_emit(now, last_emit, min_interval_seconds=45.0, force=False):
    now = safe_float(now, time.time())
    last_emit = safe_float(last_emit, 0.0)
    if force:
        return True
    return (now - last_emit) >= safe_float(min_interval_seconds, 45.0)


builtins.print("[AION-MODULE-LOAD] " + json.dumps({"module": "sports.harvest", "helpers": ["get_or_create_bucket", "build_harvest_dashboard_summary", "should_emit"]}, sort_keys=True))


from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def build_personality_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "families": {},
        "regime_history": {},
        "memory": {
            "events": [],
            "family_stats": {},
            "regime_counts": {},
        },
    }


def normalize_personality_profile(profile: Dict[str, Any] | None, family: str = "UNKNOWN", category: str = "unknown") -> Dict[str, Any]:
    base = {
        "family": family,
        "category": category,
        "personality_type": "balanced",
        "avg_spread": 0.0,
        "avg_liquidity": 0.0,
        "volatility": 0.0,
        "momentum_persistence": 0.0,
        "fake_breakout_frequency": 0.0,
        "mean_reversion_tendency": 0.0,
        "entry_success_rate": 0.0,
        "time_to_resolution": 0.0,
        "spread_compression_behavior": 0.0,
        "signal_reliability": 0.0,
        "sample_count": 0,
        "last_seen_at": 0.0,
        "updated_at": 0.0,
        "regime_counts": {},
        "performance_score": 0.5,
    }
    if not isinstance(profile, dict):
        return deepcopy(base)
    normalized = deepcopy(base)
    for key in base:
        if key in {"regime_counts"}:
            continue
        if key in profile:
            normalized[key] = profile.get(key)
    normalized["family"] = str(profile.get("family") or family or "UNKNOWN").strip() or "UNKNOWN"
    normalized["category"] = str(profile.get("category") or category or "unknown").strip().lower() or "unknown"
    normalized["personality_type"] = str(profile.get("personality_type") or base["personality_type"]).strip() or base["personality_type"]
    normalized["avg_spread"] = round(_safe_float(profile.get("avg_spread"), 0.0), 4)
    normalized["avg_liquidity"] = round(_safe_float(profile.get("avg_liquidity"), 0.0), 4)
    normalized["volatility"] = round(_safe_float(profile.get("volatility"), 0.0), 4)
    normalized["momentum_persistence"] = round(_safe_float(profile.get("momentum_persistence"), 0.0), 4)
    normalized["fake_breakout_frequency"] = round(_safe_float(profile.get("fake_breakout_frequency"), 0.0), 4)
    normalized["mean_reversion_tendency"] = round(_safe_float(profile.get("mean_reversion_tendency"), 0.0), 4)
    normalized["entry_success_rate"] = round(_safe_float(profile.get("entry_success_rate"), 0.0), 4)
    normalized["time_to_resolution"] = round(_safe_float(profile.get("time_to_resolution"), 0.0), 4)
    normalized["spread_compression_behavior"] = round(_safe_float(profile.get("spread_compression_behavior"), 0.0), 4)
    normalized["signal_reliability"] = round(_safe_float(profile.get("signal_reliability"), 0.0), 4)
    normalized["sample_count"] = _safe_int(profile.get("sample_count"), 0)
    normalized["last_seen_at"] = _safe_float(profile.get("last_seen_at"), 0.0)
    normalized["updated_at"] = _safe_float(profile.get("updated_at"), 0.0)
    normalized["regime_counts"] = dict(profile.get("regime_counts") or {})
    normalized["performance_score"] = round(_safe_float(profile.get("performance_score"), 0.5), 4)
    return normalized


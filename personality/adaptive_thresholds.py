from __future__ import annotations

from typing import Any, Dict


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def compute_adaptive_thresholds(
    regime: Dict[str, Any] | None,
    profile: Dict[str, Any] | None = None,
    base_spread_limit: float = 0.06,
    min_liquidity: float = 1.0,
    required_history_points: int = 6,
    cooldown_seconds: int = 120,
    exit_patience_seconds: int = 180,
    max_shift: float = 0.08,
) -> Dict[str, Any]:
    regime = regime if isinstance(regime, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    regime_name = str(regime.get("regime") or "CHOPPY").strip().upper()
    personality_type = str(profile.get("personality_type") or "balanced").strip().lower()
    momentum_bias = _safe_float(profile.get("momentum_persistence"), 0.0)
    reversion_bias = _safe_float(profile.get("mean_reversion_tendency"), 0.0)
    fake_breakout = _safe_float(profile.get("fake_breakout_frequency"), 0.0)
    reliability = _safe_float(profile.get("signal_reliability"), 0.5)
    spread_shift = 0.0
    liquidity_shift = 0.0
    history_shift = 0
    cooldown_shift = 0
    exit_shift = 0
    pullback_strictness = 1.0
    priority_boost = 0.5

    if regime_name == "TRENDING":
        spread_shift += 0.03
        liquidity_shift -= 0.15
        history_shift -= 1
        cooldown_shift -= 20
        exit_shift -= 15
        pullback_strictness = 0.85
        priority_boost = 0.78
    elif regime_name == "MOMENTUM_CONTINUATION":
        spread_shift += 0.015
        liquidity_shift -= 0.05
        history_shift -= 1
        cooldown_shift -= 10
        exit_shift -= 10
        pullback_strictness = 0.92
        priority_boost = 0.70
    elif regime_name == "REVERSAL":
        spread_shift -= 0.02
        liquidity_shift += 0.10
        history_shift += 1
        cooldown_shift += 25
        exit_shift += 20
        pullback_strictness = 1.15
        priority_boost = 0.48
    elif regime_name == "SPIKE_EVENT":
        spread_shift -= 0.03
        liquidity_shift += 0.20
        history_shift += 2
        cooldown_shift += 35
        exit_shift += 25
        pullback_strictness = 1.25
        priority_boost = 0.35
    elif regime_name == "DEAD":
        spread_shift -= 0.05
        liquidity_shift += 0.35
        history_shift += 3
        cooldown_shift += 45
        exit_shift += 40
        pullback_strictness = 1.35
        priority_boost = 0.20
    else:
        spread_shift -= 0.01
        liquidity_shift += 0.05
        history_shift += 1
        cooldown_shift += 10
        exit_shift += 10
        pullback_strictness = 1.05
        priority_boost = 0.42

    if personality_type == "momentum_heavy":
        spread_shift += 0.01
        history_shift -= 1
        priority_boost += 0.06
    elif personality_type == "mean_reversion":
        spread_shift -= 0.01
        pullback_strictness += 0.10
        priority_boost -= 0.03
    elif personality_type in {"unstable", "fake_breakout_prone"}:
        spread_shift -= 0.02
        liquidity_shift += 0.10
        cooldown_shift += 20
        exit_shift += 20
        pullback_strictness += 0.15
        priority_boost -= 0.10

    if fake_breakout >= 0.70:
        spread_shift -= 0.02
        liquidity_shift += 0.10
        pullback_strictness += 0.10
        priority_boost -= 0.10
    if reliability < 0.40:
        spread_shift -= 0.015
        liquidity_shift += 0.05
        history_shift += 1
        priority_boost -= 0.08
    if momentum_bias >= 0.60 and regime_name in {"TRENDING", "MOMENTUM_CONTINUATION"}:
        spread_shift += 0.01
        priority_boost += 0.04
    if reversion_bias >= 0.60 and regime_name == "REVERSAL":
        pullback_strictness += 0.05
        cooldown_shift += 10

    spread_shift = max(-max_shift, min(max_shift, spread_shift))
    spread_limit = base_spread_limit * (1.0 + spread_shift)
    min_liquidity = max(0.0, min_liquidity * (1.0 + liquidity_shift))
    required_history_points = max(3, required_history_points + history_shift)
    cooldown_seconds = max(30, cooldown_seconds + cooldown_shift)
    exit_patience_seconds = max(30, exit_patience_seconds + exit_shift)
    priority_boost = _clamp(priority_boost, 0.0, 1.0)

    return {
        "regime": regime_name,
        "spread_limit": round(max(0.0, spread_limit), 4),
        "min_liquidity": round(max(0.0, min_liquidity), 4),
        "required_history_points": int(required_history_points),
        "cooldown_seconds": int(cooldown_seconds),
        "exit_patience_seconds": int(exit_patience_seconds),
        "pullback_strictness": round(max(0.5, min(1.5, pullback_strictness)), 4),
        "priority_boost": round(priority_boost, 4),
        "threshold_shift": round(spread_shift, 4),
        "liquidity_shift": round(liquidity_shift, 4),
        "confidence_threshold": round(_clamp(0.50 + (0.10 if regime_name in {"DEAD", "SPIKE_EVENT"} else -0.03 if regime_name == "TRENDING" else 0.0), 0.30, 0.85), 4),
        "adaptive_reason": "conservative" if regime_name in {"DEAD", "SPIKE_EVENT"} or reliability < 0.40 else "balanced",
    }


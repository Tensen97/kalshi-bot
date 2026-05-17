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


def estimate_fair_value(implied: Dict[str, Any] | None, personality_context: Dict[str, Any] | None = None, live_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    implied = implied if isinstance(implied, dict) else {}
    personality_context = personality_context if isinstance(personality_context, dict) else {}
    live_context = live_context if isinstance(live_context, dict) else {}
    yes_probability = _safe_float(implied.get("spread_adjusted_probability"), _safe_float(implied.get("yes_probability"), 0.5))
    liquidity = _safe_float(implied.get("liquidity"), 0.0)
    spread = _safe_float(implied.get("spread"), 0.0)
    drift = _safe_float(implied.get("probability_drift"), 0.0)
    volatility = _safe_float(personality_context.get("regime", {}).get("volatility"), _safe_float(live_context.get("volatility"), 0.0))
    regime_name = str(personality_context.get("regime", {}).get("regime") or "CHOPPY").strip().upper()
    personality_type = str(personality_context.get("profile", {}).get("personality_type") or "balanced").strip().lower()
    orderbook_imbalance = _safe_float(live_context.get("orderbook_imbalance"), _safe_float(implied.get("orderbook_bias"), 0.0))
    momentum_persistence = _safe_float(personality_context.get("profile", {}).get("momentum_persistence"), 0.0)
    reversion_tendency = _safe_float(personality_context.get("profile", {}).get("mean_reversion_tendency"), 0.0)
    reliability = _safe_float(personality_context.get("profile", {}).get("signal_reliability"), 0.5)
    confidence = 0.50
    if liquidity >= 8.0:
        confidence += 0.12
    elif liquidity >= 4.0:
        confidence += 0.08
    elif liquidity <= 1.0:
        confidence -= 0.16
    if spread <= 0.02:
        confidence += 0.10
    elif spread >= 0.08:
        confidence -= 0.10
    if regime_name in {"TRENDING", "MOMENTUM_CONTINUATION"}:
        confidence += 0.08
        fair_probability = yes_probability + (momentum_persistence * 0.06) + (orderbook_imbalance * 0.03)
    elif regime_name == "REVERSAL":
        fair_probability = yes_probability + (reversion_tendency * -0.05) + (-orderbook_imbalance * 0.04)
    elif regime_name == "SPIKE_EVENT":
        fair_probability = yes_probability - (drift * 0.20)
        confidence -= 0.08
    elif regime_name == "DEAD":
        fair_probability = yes_probability
        confidence -= 0.20
    else:
        fair_probability = yes_probability + (momentum_persistence * 0.03) - (reversion_tendency * 0.02)
    if personality_type == "momentum_heavy":
        fair_probability += 0.025
    elif personality_type == "mean_reversion":
        fair_probability -= 0.025
    elif personality_type in {"unstable", "fake_breakout_prone"}:
        confidence -= 0.12
        fair_probability += (-0.03 if drift > 0 else 0.03)
    if reliability < 0.40:
        confidence -= 0.10
    if abs(drift) > 0.06:
        confidence -= 0.08
    if abs(orderbook_imbalance) > 0.30:
        confidence += 0.05
    fair_probability = _clamp(fair_probability, 0.05, 0.95)
    confidence = _clamp(confidence, 0.10, 0.95)
    uncertainty = _clamp(1.0 - confidence, 0.05, 0.90)
    interval = round(max(0.02, uncertainty * 0.25), 4)
    return {
        "fair_probability": round(fair_probability, 4),
        "confidence_interval": [round(max(0.0, fair_probability - interval), 4), round(min(1.0, fair_probability + interval), 4)],
        "uncertainty_score": round(uncertainty, 4),
        "model_confidence": round(confidence, 4),
        "regime": regime_name,
        "personality_type": personality_type,
        "volatility": round(volatility, 4),
    }


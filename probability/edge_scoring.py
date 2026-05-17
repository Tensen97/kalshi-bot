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


def score_probability_edge(implied: Dict[str, Any] | None, fair_value: Dict[str, Any] | None, personality_context: Dict[str, Any] | None = None, live_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    implied = implied if isinstance(implied, dict) else {}
    fair_value = fair_value if isinstance(fair_value, dict) else {}
    personality_context = personality_context if isinstance(personality_context, dict) else {}
    live_context = live_context if isinstance(live_context, dict) else {}
    market_probability = _safe_float(implied.get("spread_adjusted_probability"), _safe_float(implied.get("yes_probability"), 0.5))
    fair_probability = _safe_float(fair_value.get("fair_probability"), 0.5)
    divergence = fair_probability - market_probability
    liquidity = _safe_float(implied.get("liquidity"), 0.0)
    spread = _safe_float(implied.get("spread"), 0.0)
    uncertainty = _safe_float(fair_value.get("uncertainty_score"), 0.5)
    regime = str(personality_context.get("regime", {}).get("regime") or fair_value.get("regime") or "CHOPPY").strip().upper()
    personality_type = str(personality_context.get("profile", {}).get("personality_type") or fair_value.get("personality_type") or "balanced").strip().lower()
    regime_confidence = _safe_float(personality_context.get("regime", {}).get("regime_confidence"), 0.5)
    signal_reliability = _safe_float(personality_context.get("profile", {}).get("signal_reliability"), 0.5)
    volatility = _safe_float(fair_value.get("volatility"), 0.0)
    orderbook_bias = _safe_float(implied.get("orderbook_bias"), 0.0)
    edge_strength = abs(divergence)
    if liquidity <= 1.0 or spread > 0.12:
        edge_strength *= 0.55
    elif liquidity >= 8.0 and spread <= 0.03:
        edge_strength *= 1.10
    if regime in {"DEAD", "SPIKE_EVENT"}:
        edge_strength *= 0.65
    elif regime in {"TRENDING", "MOMENTUM_CONTINUATION"}:
        edge_strength *= 1.08
    if personality_type == "momentum_heavy" and divergence > 0:
        edge_strength *= 1.10
    elif personality_type == "mean_reversion" and divergence < 0:
        edge_strength *= 1.10
    elif personality_type in {"unstable", "fake_breakout_prone"}:
        edge_strength *= 0.70
    liquidity_quality = _clamp(min(1.0, liquidity / 12.0), 0.0, 1.0)
    spread_health = _clamp(1.0 - min(1.0, spread / 0.20), 0.0, 1.0)
    regime_alignment = 1.0 if regime in {"TRENDING", "MOMENTUM_CONTINUATION"} and divergence > 0 else 1.0 if regime == "REVERSAL" and divergence < 0 else 0.65 if regime == "DEAD" else 0.80
    conviction = (
        edge_strength * 3.0
        + liquidity_quality * 0.20
        + spread_health * 0.15
        + regime_alignment * 0.20
        + regime_confidence * 0.10
        + signal_reliability * 0.15
        + max(0.0, 0.10 - uncertainty * 0.10)
        + abs(orderbook_bias) * 0.05
    )
    if volatility >= 0.75:
        conviction *= 0.82
    elif volatility <= 0.20:
        conviction *= 1.05
    conviction = _clamp(conviction, 0.0, 1.0)
    edge_category = "weak"
    if conviction >= 0.72 and edge_strength >= 0.04:
        edge_category = "high"
    elif conviction >= 0.48 and edge_strength >= 0.02:
        edge_category = "moderate"
    mispricing = "underpriced" if divergence > 0.01 else "overpriced" if divergence < -0.01 else "fair"
    return {
        "market_probability": round(market_probability, 4),
        "fair_probability": round(fair_probability, 4),
        "divergence": round(divergence, 4),
        "edge_strength": round(edge_strength, 4),
        "conviction_score": round(conviction, 4),
        "edge_category": edge_category,
        "mispricing_type": mispricing,
        "liquidity_quality": round(liquidity_quality, 4),
        "spread_health": round(spread_health, 4),
        "regime_alignment": round(regime_alignment, 4),
        "personality_type": personality_type,
        "regime": regime,
    }


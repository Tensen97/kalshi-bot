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


def detect_market_regime(observation: Dict[str, Any] | None, profile: Dict[str, Any] | None = None, ws_snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    observation = observation if isinstance(observation, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    ws_snapshot = ws_snapshot if isinstance(ws_snapshot, dict) else {}
    spread = _safe_float(observation.get("spread"), _safe_float(profile.get("avg_spread"), 0.0))
    liquidity = _safe_float(observation.get("liquidity"), _safe_float(profile.get("avg_liquidity"), 0.0))
    momentum = _safe_float(observation.get("momentum"), _safe_float(profile.get("momentum_persistence"), 0.0))
    volatility = _safe_float(observation.get("volatility"), _safe_float(profile.get("volatility"), 0.0))
    price_velocity = _safe_float(observation.get("price_velocity"), 0.0)
    spread_delta = _safe_float(observation.get("spread_delta"), 0.0)
    liquidity_delta = _safe_float(observation.get("liquidity_delta"), 0.0)
    imbalance = _safe_float(observation.get("orderbook_imbalance"), _safe_float(ws_snapshot.get("orderbook_imbalance"), 0.0))
    persistence = _safe_float(observation.get("directional_persistence"), momentum)
    fake_breakout = _safe_float(profile.get("fake_breakout_frequency"), 0.0)
    reversion = _safe_float(profile.get("mean_reversion_tendency"), 0.0)
    signal_reliability = _safe_float(profile.get("signal_reliability"), 0.5)

    liquidity_state = "healthy"
    if liquidity <= 0.0:
        liquidity_state = "dead"
    elif liquidity < 3.0 or spread > 0.12:
        liquidity_state = "thin"
    elif liquidity < 8.0:
        liquidity_state = "mixed"

    volatility_state = "calm"
    if volatility >= 0.75 or abs(spread_delta) >= 0.03 or abs(price_velocity) >= 0.35:
        volatility_state = "spiky"
    elif volatility >= 0.45:
        volatility_state = "moderate"

    if liquidity_state == "dead" or (liquidity <= 1.0 and spread > 0.10):
        regime = "DEAD"
        confidence = 0.96
        recommended_bias = "avoid"
    elif volatility_state == "spiky" and abs(price_velocity) >= 0.35 and abs(spread_delta) >= 0.015:
        regime = "SPIKE_EVENT"
        confidence = 0.82
        recommended_bias = "defensive"
    elif persistence >= 0.65 and price_velocity >= 0.18 and spread <= 0.06 and liquidity >= 4.0:
        regime = "TRENDING"
        confidence = 0.84
        recommended_bias = "momentum"
    elif persistence >= 0.55 and price_velocity >= 0.10 and liquidity >= 3.0:
        regime = "MOMENTUM_CONTINUATION"
        confidence = 0.74
        recommended_bias = "balanced_momentum"
    elif abs(price_velocity) <= 0.12 and spread >= 0.04 and abs(imbalance) <= 0.20:
        regime = "CHOPPY"
        confidence = 0.76
        recommended_bias = "defensive"
    elif reversion >= 0.60 and price_velocity < -0.10:
        regime = "REVERSAL"
        confidence = 0.73
        recommended_bias = "mean_reversion"
    else:
        regime = "MOMENTUM_CONTINUATION" if momentum >= 0.50 else "CHOPPY"
        confidence = 0.60 if regime == "MOMENTUM_CONTINUATION" else 0.56
        recommended_bias = "normal" if regime == "MOMENTUM_CONTINUATION" else "cautious"

    momentum_state = "positive" if momentum >= 0.55 else "negative" if momentum <= 0.35 else "mixed"
    if fake_breakout >= 0.70:
        recommended_bias = "defensive"
        confidence = min(confidence, 0.72)
    if signal_reliability < 0.40:
        confidence = min(confidence, 0.62)
        if regime not in {"DEAD", "SPIKE_EVENT"}:
            recommended_bias = "cautious"

    return {
        "regime": regime,
        "regime_confidence": round(_clamp(confidence), 4),
        "volatility_state": volatility_state,
        "liquidity_state": liquidity_state,
        "momentum_state": momentum_state,
        "recommended_strategy_bias": recommended_bias,
        "price_velocity": round(price_velocity, 4),
        "spread_delta": round(spread_delta, 4),
        "liquidity_delta": round(liquidity_delta, 4),
        "orderbook_imbalance": round(imbalance, 4),
        "signal_reliability": round(signal_reliability, 4),
        "volatile_market": bool(volatility_state == "spiky"),
    }


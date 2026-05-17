from __future__ import annotations

from typing import Any, Dict


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def scale_aggression(capital_state: Dict[str, Any] | None, drawdown_state: Dict[str, Any] | None, exposure_state: Dict[str, Any] | None, probability_context: Dict[str, Any] | None, personality_context: Dict[str, Any] | None, external_context: Dict[str, Any] | None = None, max_aggression: float = 0.85) -> Dict[str, Any]:
    capital_state = capital_state if isinstance(capital_state, dict) else {}
    drawdown_state = drawdown_state if isinstance(drawdown_state, dict) else {}
    exposure_state = exposure_state if isinstance(exposure_state, dict) else {}
    probability_context = probability_context if isinstance(probability_context, dict) else {}
    personality_context = personality_context if isinstance(personality_context, dict) else {}
    external_context = external_context if isinstance(external_context, dict) else {}
    base = 0.35
    if capital_state.get("capital_state") == "AGGRESSIVE":
        base += 0.18
    elif capital_state.get("capital_state") == "DEFENSIVE":
        base -= 0.12
    elif capital_state.get("capital_state") == "CAPITAL_PRESERVATION":
        base -= 0.22
    conviction = _safe_float(probability_context.get("final_conviction_score"), 0.0)
    if conviction >= 0.75:
        base += 0.16
    elif conviction >= 0.50:
        base += 0.06
    else:
        base -= 0.06
    regime = str(personality_context.get("regime", {}).get("regime") or "CHOPPY").upper()
    if regime in {"DEAD", "SPIKE_EVENT"}:
        base -= 0.18
    elif regime in {"TRENDING", "MOMENTUM_CONTINUATION"}:
        base += 0.06
    if drawdown_state.get("soft_circuit_active"):
        base -= 0.12
    if exposure_state.get("exposure_warning"):
        base -= 0.08
    if exposure_state.get("exposure_block"):
        base -= 0.15
    if external_context.get("event_risk", {}).get("risk_level") == "HIGH":
        base -= 0.07
    elif external_context.get("event_risk", {}).get("risk_level") == "EXTREME":
        base -= 0.16
    personality_type = str(personality_context.get("profile", {}).get("personality_type") or "balanced").lower()
    if personality_type == "momentum_heavy" and regime in {"TRENDING", "MOMENTUM_CONTINUATION"}:
        base += 0.05
    elif personality_type in {"unstable", "fake_breakout_prone"}:
        base -= 0.10
    if drawdown_state.get("drawdown_pct", 0.0) >= 0.10:
        base -= 0.18
    elif drawdown_state.get("drawdown_pct", 0.0) >= 0.06:
        base -= 0.10
    aggression_level = max(0.05, min(max_aggression, base))
    aggression_mode = "normal"
    if aggression_level >= 0.58:
        aggression_mode = "aggressive"
    elif aggression_level <= 0.25:
        aggression_mode = "capital_preservation"
    elif aggression_level <= 0.35:
        aggression_mode = "defensive"
    reason = "stable"
    if aggression_mode == "aggressive":
        reason = "strong_edge_or_healthy_capital"
    elif aggression_mode == "defensive":
        reason = "risk_control"
    elif aggression_mode == "capital_preservation":
        reason = "drawdown_or_exposure_protection"
    risk_multiplier = 0.75 + aggression_level * 0.50
    return {
        "aggression_level": round(aggression_level, 4),
        "aggression_mode": aggression_mode,
        "aggression_reason": reason,
        "risk_multiplier": round(risk_multiplier, 4),
    }

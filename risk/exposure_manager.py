from __future__ import annotations

from typing import Any, Dict, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def manage_exposure(bot=None, probability_context: Dict[str, Any] | None = None, personality_context: Dict[str, Any] | None = None, external_context: Dict[str, Any] | None = None, live_select_summary: Dict[str, Any] | None = None, state_summary: Dict[str, Any] | None = None) -> Dict[str, Any]:
    probability_context = probability_context if isinstance(probability_context, dict) else {}
    personality_context = personality_context if isinstance(personality_context, dict) else {}
    external_context = external_context if isinstance(external_context, dict) else {}
    live_select_summary = live_select_summary if isinstance(live_select_summary, dict) else {}
    state_summary = state_summary if isinstance(state_summary, dict) else {}
    portfolio = getattr(bot, "last_realm_xviii_summary", {}) if bot is not None else {}
    family_exposure = dict((portfolio or {}).get("exposure_by_family", {}) or {})
    personality_exposure = dict((portfolio or {}).get("exposure_by_personality", {}) or {})
    regime_exposure = dict((portfolio or {}).get("exposure_by_regime", {}) or {})
    total_exposure = _safe_float((portfolio or {}).get("total_exposure_dollars"), 0.0)
    exposure_warning = False
    exposure_warning_reason = None
    exposure_block = False
    exposure_block_reason = None
    concentration_warnings: List[str] = []
    if total_exposure >= 25.0:
        exposure_warning = True
        exposure_warning_reason = "total_exposure_high"
        concentration_warnings.append("total_exposure_high")
    if any(_safe_float(v, 0.0) >= 10.0 for v in family_exposure.values()):
        exposure_warning = True
        exposure_warning_reason = exposure_warning_reason or "family_concentration_high"
        concentration_warnings.append("family_concentration_high")
    if len([value for value in family_exposure.values() if _safe_float(value, 0.0) > 0]) >= 4:
        exposure_warning = True
        concentration_warnings.append("broad_exposure")
    if len([key for key in family_exposure if "BTC" in str(key).upper()]) >= 2:
        concentration_warnings.append("correlated_crypto_exposure")
    if personality_context.get("regime", {}).get("regime") in {"DEAD", "SPIKE_EVENT"}:
        exposure_warning = True
        exposure_warning_reason = exposure_warning_reason or "unstable_personality_regime"
    if probability_context.get("final_signal") == "weak" and _safe_float(probability_context.get("final_conviction_score"), 0.0) < 0.35:
        concentration_warnings.append("weak_edge_cluster")
    if external_context.get("event_risk", {}).get("risk_level") in {"HIGH", "EXTREME"}:
        exposure_warning = True
        exposure_warning_reason = exposure_warning_reason or "external_event_risk"
        concentration_warnings.append("external_event_risk")
    if concentration_warnings and (total_exposure >= 30.0 or any("correlated" in warning for warning in concentration_warnings)):
        exposure_block = True
        exposure_block_reason = exposure_warning_reason or "correlated_exposure"
    exposure_state = "stable"
    if exposure_block:
        exposure_state = "blocked"
    elif exposure_warning:
        exposure_state = "warning"
    return {
        "total_exposure_dollars": round(total_exposure, 4),
        "family_exposure": family_exposure,
        "personality_exposure": personality_exposure,
        "regime_exposure": regime_exposure,
        "exposure_state": exposure_state,
        "exposure_warning": exposure_warning,
        "exposure_warning_reason": exposure_warning_reason,
        "exposure_block": exposure_block,
        "exposure_block_reason": exposure_block_reason,
        "concentration_warnings": concentration_warnings,
    }

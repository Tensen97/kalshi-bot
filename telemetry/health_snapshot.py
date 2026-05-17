from __future__ import annotations

from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _state_from_score(score: float) -> str:
    if score >= 85:
        return "OPTIMAL"
    if score >= 70:
        return "HEALTHY"
    if score >= 55:
        return "WATCH"
    if score >= 35:
        return "DEGRADED"
    return "CRITICAL"


def compute_global_health_snapshot(state: dict | None = None) -> dict:
    state = state if isinstance(state, dict) else {}
    reasons = []
    score = 100.0
    service_status = state.get("service_status", {}) or {}
    live_mode = state.get("live_mode", {}) or {}
    websocket = state.get("websocket_status", {}) or {}
    risk = state.get("latest_risk_state", {}) or {}
    external = state.get("latest_external_summary", {}) or {}
    evolution = state.get("latest_evolution_summary", {}) or {}
    command_center = state.get("command_center", {}) or {}

    if not service_status.get("service_active"):
        score -= 22
        reasons.append("service_inactive")
    if not bool(live_mode.get("active")):
        score -= 8
        reasons.append("live_mode_off")
    if str(websocket.get("health") or websocket.get("connection_state") or "").lower() not in {"healthy", "ok", "good", "online"}:
        score -= 10
        reasons.append("websocket_unhealthy")
    if bool(websocket.get("stale_feed")):
        score -= 12
        reasons.append("stale_feed")
    if str(risk.get("capital_state") or risk.get("risk_state") or "").lower() in {"defensive", "capital_preservation", "risk_off"}:
        score -= 10
        reasons.append("defensive_capital_state")
    if _safe_float(risk.get("drawdown_pct"), 0.0) >= 0.10:
        score -= 10
        reasons.append("drawdown_pressure")
    if str(external.get("event_risk", {}).get("risk_level") or "").upper() in {"HIGH", "EXTREME"}:
        score -= 8
        reasons.append("external_event_risk")
    if str((evolution.get("health") or {}).get("health_state") or "").upper() in {"DEGRADED", "UNSTABLE"}:
        score -= 8
        reasons.append("evolution_degraded")
    if str((command_center.get("health") or command_center.get("global_health") or {}).get("global_health_state") or "").upper() in {"DEGRADED", "CRITICAL"}:
        score -= 6
        reasons.append("command_center_degraded")
    if state.get("latest_execution_block_reason"):
        score -= 4
        reasons.append("execution_block_active")

    score = max(0.0, min(100.0, score))
    state_name = _state_from_score(score)
    print(
        "[GLOBAL-HEALTH-COMPUTED] "
        f"state={state_name} score={round(score, 2)} reasons={len(reasons)}"
    )
    if reasons:
        print(f"[GLOBAL-HEALTH-REASON] reasons={','.join(reasons[:10])}")
    return {
        "global_health_state": state_name,
        "global_health_score": round(score, 2),
        "reasons": reasons,
        "notes": reasons[:],
    }

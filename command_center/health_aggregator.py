def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class HealthAggregator:
    def compute(self, state):
        state = state or {}
        websocket = state.get("websocket", {}) or {}
        risk = state.get("risk", {}) or {}
        external = state.get("external", {}) or {}
        evolution = state.get("evolution", {}) or {}
        command_center = state.get("command_center", {}) or {}
        health = 0.82
        notes = []
        if not state.get("service_status", {}).get("service_active", False):
            health -= 0.08
            notes.append("service_inactive")
        if str(websocket.get("health") or "").lower() not in {"healthy", "ok", "good"}:
            health -= 0.10
            notes.append("websocket_unhealthy")
        if websocket.get("stale_feed"):
            health -= 0.12
            notes.append("stale_feed")
        if state.get("warnings"):
            health -= min(0.10, 0.02 * len(state.get("warnings", [])))
            notes.extend(state.get("warnings", [])[:3])
        capital_state = str((risk.get("last_global_state") or {}).get("capital_state") or "").lower()
        if capital_state in {"defensive", "capital_preservation"}:
            health -= 0.06
            notes.append(f"capital:{capital_state}")
        if str((external.get("event_risk") or {}).get("risk_level") or "NONE").upper() in {"HIGH", "EXTREME"}:
            health -= 0.06
            notes.append("external_event_risk")
        evo_health = str((evolution.get("health") or {}).get("health_state") or "").upper()
        if evo_health in {"DEGRADED", "UNSTABLE"}:
            health -= 0.06
            notes.append(f"evolution:{evo_health.lower()}")
        if command_center.get("guardian_veto"):
            health -= 0.10
            notes.append("guardian_veto")
        health = max(0.0, min(1.0, health))
        if health >= 0.88:
            state_name = "OPTIMAL"
        elif health >= 0.72:
            state_name = "HEALTHY"
        elif health >= 0.56:
            state_name = "WATCH"
        elif health >= 0.38:
            state_name = "DEGRADED"
        else:
            state_name = "CRITICAL"
        return {
            "global_health_score": round(health, 4),
            "global_health_state": state_name,
            "notes": notes[:10],
        }

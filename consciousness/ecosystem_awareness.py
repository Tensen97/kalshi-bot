from __future__ import annotations


class EcosystemAwareness:
    def build(self, state, interpretations=None, summary_text=""):
        state = state or {}
        interpretations = interpretations or {}
        health = state.get("health", {}) or {}
        command_center = interpretations.get("command_center") or {}
        overview = interpretations.get("overview") or {}
        warnings = command_center.get("warnings") or state.get("warnings") or []
        active_blocks = command_center.get("active_blocks") or state.get("active_blocks") or []
        readiness = "ready" if str(overview.get("status") or "").lower() == "live" else "monitoring"
        if str(health.get("global_health_state") or "").upper() in {"DEGRADED", "CRITICAL"}:
            readiness = "caution"
        return {
            "readiness": readiness,
            "summary": summary_text or overview.get("summary") or "AION state is available.",
            "health_state": health.get("global_health_state", "unknown"),
            "health_score": health.get("global_health_score"),
            "warnings": warnings[:10],
            "active_blocks": active_blocks[:10],
            "active_realms": state.get("active_realms", []),
            "status": "live" if str(overview.get("status") or state.get("status") or "").lower() == "live" else "waiting",
        }


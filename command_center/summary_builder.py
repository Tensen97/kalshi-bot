def _safe_str(value, default="unknown"):
    text = str(value) if value is not None else ""
    text = text.strip()
    return text or default


class SummaryBuilder:
    def build(self, state):
        state = state or {}
        consciousness = state.get("consciousness", {}) or {}
        websocket = state.get("websocket", {}) or {}
        risk = state.get("risk", {}) or {}
        external = state.get("external", {}) or {}
        evolution = state.get("evolution", {}) or {}
        command_center_health = state.get("command_center_health", {}) or {}
        lines = [
            "SERAPH COMMAND CENTER",
            "",
            f"AION status: {'online' if state.get('service_status', {}).get('service_active') else 'offline'}",
            f"WebSocket: {_safe_str(websocket.get('health'))} with {websocket.get('active_subscriptions', 0)} subscriptions",
            f"Risk: {_safe_str((risk.get('last_global_state') or {}).get('capital_state'), 'unknown')} with aggression {_safe_str((risk.get('last_global_state') or {}).get('aggression_mode'), 'unknown')}",
            f"External: {_safe_str((external.get('event_risk') or {}).get('risk_level'), 'NONE')} event risk",
            f"Evolution: {len((evolution.get('recommendations') or []))} recommendations, health {_safe_str((evolution.get('health') or {}).get('health_state'), 'unknown')}",
            f"Command center health: {_safe_str(command_center_health.get('state'), 'watch')}",
        ]
        if consciousness.get("summary"):
            lines.insert(2, f"Operational awareness: {consciousness.get('summary')}")
        if state.get("warnings"):
            lines.append(f"Warnings: {', '.join(map(str, state.get('warnings', [])[:5]))}")
        if state.get("suggested_next_actions"):
            lines.append(f"Next actions: {', '.join(map(str, state.get('suggested_next_actions', [])[:5]))}")
        return "\n".join(lines)

from __future__ import annotations


class OperationalSummaryBuilder:
    def build(self, state, interpretations=None):
        state = state or {}
        interpretations = interpretations or {}
        overview = (interpretations.get("overview") or {}).get("summary") or "AION status is available."
        execution = (interpretations.get("execution") or {}).get("summary") or "Execution posture is available."
        websocket = (interpretations.get("websocket") or {}).get("summary") or "WebSocket status is available."
        risk = (interpretations.get("risk") or {}).get("summary") or "Risk posture is available."
        probability = (interpretations.get("probability") or {}).get("summary") or "Probability state is available."
        external = (interpretations.get("command_center") or {}).get("summary") or "Command center state is available."
        return " ".join(
            part.strip()
            for part in [
                overview,
                websocket,
                execution,
                probability,
                risk,
                external,
            ]
            if part
        ).strip()


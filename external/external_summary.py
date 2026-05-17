from __future__ import annotations

from typing import Any, Dict


def build_external_summary(state: Dict[str, Any] | None = None) -> Dict[str, Any]:
    state = state if isinstance(state, dict) else {}
    return {
        "active_catalysts": state.get("active_catalysts", []),
        "top_external_risks": state.get("top_external_risks", []),
        "affected_families": state.get("affected_families", []),
        "confidence_levels": state.get("confidence_levels", {}),
        "external_awareness_state": state.get("external_awareness_state", "inactive"),
        "summary": state.get("summary", "no_external_context"),
    }


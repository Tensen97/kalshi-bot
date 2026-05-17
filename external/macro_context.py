from __future__ import annotations

from typing import Any, Dict, Iterable, List


def build_macro_context(context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    context = context if isinstance(context, dict) else {}
    events = [item for item in (context.get("events") or []) if isinstance(item, dict)]
    active = []
    for item in events:
        category = str(item.get("category") or "").upper()
        name = str(item.get("name") or item.get("summary") or "").upper()
        if any(key in category or key in name for key in ("CPI", "FOMC", "JOBS", "FED", "INFLATION", "RATE")):
            active.append(item)
    risk_window = bool(active)
    return {
        "macro_events": active,
        "macro_risk_window": risk_window,
        "summary": "macro_risk_window" if risk_window else "macro_context_clear",
    }


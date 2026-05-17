from __future__ import annotations

from typing import Any, Dict


def build_weather_context(context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    context = context if isinstance(context, dict) else {}
    text = str(context.get("text") or context.get("summary") or "").strip()
    family = str(context.get("family") or "").strip().upper()
    relevant = bool(text and any(key in family for key in ("WEATHER", "RAIN", "SNOW", "TEMP", "STORM", "HURRICANE")))
    missing = (not bool(text)) or ("context placeholder" in text.lower()) or ("live quote context" in text.lower())
    return {
        "weather_relevant": relevant,
        "weather_context_missing": bool(relevant and missing),
        "risk_window": bool(relevant and text),
        "summary": "weather_context_ready" if relevant and not missing else ("weather_context_missing" if relevant else "not_weather_related"),
    }

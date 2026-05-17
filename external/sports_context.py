from __future__ import annotations

from typing import Any, Dict


def build_sports_context(context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    context = context if isinstance(context, dict) else {}
    family = str(context.get("family") or "").strip().upper()
    text = str(context.get("text") or context.get("summary") or "").strip()
    has_match = bool(family and any(key in family for key in ("NBA", "MLB", "NFL", "NHL", "SOCCER", "SPORT")))
    missing = (not bool(text)) or ("context placeholder" in text.lower()) or ("live quote context" in text.lower())
    return {
        "sports_relevant": has_match,
        "sports_context_missing": missing and has_match,
        "match_relevance": 1.0 if has_match else 0.0,
        "weak_signal_boost": 0.05 if has_match and not missing else 0.0,
        "block_recommendation": bool(has_match and missing),
        "summary": "sports_context_ready" if has_match and not missing else ("sports_context_missing" if has_match else "not_sports_related"),
    }

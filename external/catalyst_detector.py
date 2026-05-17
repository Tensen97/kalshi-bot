from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    text = text.lower()
    return any(needle.lower() in text for needle in needles)


def detect_catalyst(context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    context = context if isinstance(context, dict) else {}
    text = " ".join(
        _safe_text(context.get(key))
        for key in ("text", "headline", "summary", "description", "title", "event_name")
    ).strip()
    category = str(context.get("category") or context.get("source") or "UNKNOWN_EVENT").upper()
    family = str(context.get("family") or context.get("related_family") or "").strip().upper()
    ticker = str(context.get("ticker") or context.get("related_ticker") or "").strip().upper()
    catalyst_type = "UNKNOWN_EVENT"
    if _contains_any(text, ["injury", "lineup", "questionable", "out", "doubtful"]):
        catalyst_type = "SPORTS_INJURY" if _contains_any(category, ["SPORT", "NBA", "MLB", "NFL", "NHL"]) else "SPORTS_LINEUP"
    elif _contains_any(text, ["weather", "storm", "rain", "wind", "snow", "hurricane", "tornado"]):
        catalyst_type = "SPORTS_WEATHER" if _contains_any(category, ["SPORT", "GAME"]) else "WEATHER_ALERT"
    elif _contains_any(text, ["cpi", "inflation"]):
        catalyst_type = "MACRO_CPI"
    elif _contains_any(text, ["fomc", "fed", "powell", "rates"]):
        catalyst_type = "MACRO_FOMC"
    elif _contains_any(text, ["jobs", "employment", "payrolls", "unemployment"]):
        catalyst_type = "MACRO_JOBS"
    elif _contains_any(text, ["bitcoin", "btc", "ethereum", "eth", "crypto", "volatility"]):
        catalyst_type = "CRYPTO_VOLATILITY"
    elif _contains_any(text, ["breaking", "urgent", "alert", "headline"]):
        catalyst_type = "BREAKING_NEWS"
    elif _contains_any(text, ["rumor", "unverified", "maybe", "allegedly"]):
        catalyst_type = "LOW_CONFIDENCE_RUMOR"
    confidence = float(context.get("confidence", 0.5) or 0.5)
    relevance = float(context.get("relevance", 0.5) or 0.5)
    urgency = float(context.get("urgency", 0.5) or 0.5)
    possible_impact = "neutral"
    if catalyst_type in {"MACRO_CPI", "MACRO_FOMC", "MACRO_JOBS", "CRYPTO_VOLATILITY"}:
        possible_impact = "high_volatility"
    elif catalyst_type in {"SPORTS_INJURY", "SPORTS_LINEUP", "WEATHER_ALERT"}:
        possible_impact = "market_specific_shift"
    elif catalyst_type in {"BREAKING_NEWS", "LOW_CONFIDENCE_RUMOR"}:
        possible_impact = "uncertain_shift"
    return {
        "catalyst_type": catalyst_type,
        "category": category,
        "affected_family": family or context.get("family") or "UNKNOWN",
        "affected_ticker": ticker or context.get("ticker") or "UNKNOWN",
        "relevance": round(max(0.0, min(1.0, relevance)), 4),
        "urgency": round(max(0.0, min(1.0, urgency)), 4),
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "possible_market_impact": possible_impact,
        "summary": _safe_text(context.get("summary") or text or catalyst_type),
    }


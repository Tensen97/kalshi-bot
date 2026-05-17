from __future__ import annotations

from typing import Any, Dict, List


def assess_event_risk(catalysts: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    catalysts = [item for item in (catalysts or []) if isinstance(item, dict)]
    if not catalysts:
        return {
            "risk_level": "NONE",
            "risk_score": 0.0,
            "top_risks": [],
            "summary": "no_external_risk",
        }
    score = 0.0
    for item in catalysts:
        relevance = float(item.get("relevance", 0.0) or 0.0)
        urgency = float(item.get("urgency", 0.0) or 0.0)
        confidence = float(item.get("confidence", 0.0) or 0.0)
        impact_weight = 0.2
        if item.get("possible_market_impact") == "high_volatility":
            impact_weight = 0.35
        elif item.get("possible_market_impact") == "uncertain_shift":
            impact_weight = 0.18
        score += (relevance * 0.45) + (urgency * 0.30) + (confidence * impact_weight)
    score = min(1.0, score / max(1.0, len(catalysts)))
    if score >= 0.85:
        level = "EXTREME"
    elif score >= 0.65:
        level = "HIGH"
    elif score >= 0.40:
        level = "MEDIUM"
    elif score >= 0.15:
        level = "LOW"
    else:
        level = "NONE"
    top_risks = sorted(catalysts, key=lambda item: (float(item.get("relevance", 0.0) or 0.0), float(item.get("urgency", 0.0) or 0.0)), reverse=True)[:5]
    return {
        "risk_level": level,
        "risk_score": round(score, 4),
        "top_risks": top_risks,
        "summary": f"{level.lower()}_external_event_risk",
    }


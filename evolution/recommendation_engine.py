def _clamp(value, minimum=0.0, maximum=1.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


class RecommendationEngine:
    def __init__(self, recommendation_cap=10):
        self.recommendation_cap = max(3, int(recommendation_cap))

    def generate(self, analysis_summary=None, pattern_state=None, parameter_state=None, realm_context=None):
        analysis_summary = analysis_summary or {}
        pattern_state = pattern_state or {}
        parameter_state = parameter_state or {}
        realm_context = realm_context or {}
        recommendations = []
        win_rate = float(analysis_summary.get("win_rate", 0.5) or 0.5)
        average_pnl = float(analysis_summary.get("average_pnl", 0.0) or 0.0)
        risk_adjusted = float(analysis_summary.get("average_risk_adjusted_outcome", 0.5) or 0.5)
        weak_patterns = list(pattern_state.get("failure_patterns", []) or [])
        strong_patterns = list(pattern_state.get("success_patterns", []) or [])
        laggards = list(parameter_state.get("parameter_laggards", []) or [])
        leaders = list(parameter_state.get("parameter_leaders", []) or [])
        if weak_patterns:
            pattern = weak_patterns[0]
            recommendations.append(
                {
                    "type": "tighten_filtering",
                    "title": "Tighten weak-edge approvals",
                    "recommendation": f"Treat {pattern.get('pattern_key')} as a guarded structure.",
                    "confidence": _clamp(0.60 + abs(float(pattern.get("score", 0.0) or 0.0)) / 20.0),
                    "evidence_count": int(pattern.get("count", 0) or 0),
                    "historical_support": round(abs(float(pattern.get("average_pnl", 0.0) or 0.0)), 4),
                    "expected_impact": "reduce false positives",
                    "risk_level": "low",
                    "reason": "historically weak pattern",
                }
            )
        if laggards:
            param = laggards[0]
            recommendations.append(
                {
                    "type": "parameter_guard",
                    "title": "Reduce unstable parameter influence",
                    "recommendation": f"Keep {param.get('parameter_key')} conservative until support improves.",
                    "confidence": _clamp(0.58 + (0.5 - float(param.get("score", 0.5) or 0.5))),
                    "evidence_count": int(param.get("count", 0) or 0),
                    "historical_support": round(float(param.get("support", 0.0) or 0.0), 4),
                    "expected_impact": "improve stability",
                    "risk_level": "low",
                    "reason": "parameter underperforming",
                }
            )
        if win_rate < 0.45 or average_pnl < 0.0:
            recommendations.append(
                {
                    "type": "aggression_reduction",
                    "title": "Reduce aggression in weaker environments",
                    "recommendation": "Prefer stricter pullback confirmation and lower aggression until recent outcomes improve.",
                    "confidence": _clamp(0.62 + (0.45 - win_rate) + abs(min(0.0, average_pnl)) * 4.0),
                    "evidence_count": max(1, int(analysis_summary.get("total_trades", 0) or 0)),
                    "historical_support": round(max(0.0, 1.0 - risk_adjusted), 4),
                    "expected_impact": "lower drawdown risk",
                    "risk_level": "medium",
                    "reason": "recent performance weakened",
                }
            )
        if strong_patterns:
            pattern = strong_patterns[0]
            recommendations.append(
                {
                    "type": "specialize_strength",
                    "title": "Favor strong confirmed structures",
                    "recommendation": f"Prioritize {pattern.get('pattern_key')} when regime alignment is present.",
                    "confidence": _clamp(0.65 + float(pattern.get("score", 0.0) or 0.0) / 40.0),
                    "evidence_count": int(pattern.get("count", 0) or 0),
                    "historical_support": round(float(pattern.get("average_pnl", 0.0) or 0.0), 4),
                    "expected_impact": "improve selective edge capture",
                    "risk_level": "low",
                    "reason": "historically strong pattern",
                }
            )
        if leaders and len(recommendations) < self.recommendation_cap:
            leader = leaders[0]
            recommendations.append(
                {
                    "type": "parameter_strength",
                    "title": "Lean into reliable parameters",
                    "recommendation": f"Parameter {leader.get('parameter_key')} is working well and should remain a priority.",
                    "confidence": _clamp(0.70 + float(leader.get("score", 0.0) or 0.0) / 5.0),
                    "evidence_count": int(leader.get("count", 0) or 0),
                    "historical_support": round(float(leader.get("support", 0.0) or 0.0), 4),
                    "expected_impact": "preserve strong behavior",
                    "risk_level": "low",
                    "reason": "parameter leader",
                }
            )
        if realm_context.get("external_risk_level") in {"HIGH", "EXTREME"}:
            recommendations.append(
                {
                    "type": "risk_preservation",
                    "title": "Preserve capital during external risk",
                    "recommendation": "Treat uncertain or externally stressed environments conservatively.",
                    "confidence": 0.92 if realm_context.get("external_risk_level") == "EXTREME" else 0.82,
                    "evidence_count": int(realm_context.get("external_context_count", 0) or 0),
                    "historical_support": 0.0,
                    "expected_impact": "reduce overreaction risk",
                    "risk_level": "medium",
                    "reason": "external risk elevated",
                }
            )
        return recommendations[: self.recommendation_cap]


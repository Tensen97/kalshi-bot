from .strategy_analysis import _safe_float


def _fmt(value, digits=4, default="unknown"):
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return default


def build_summary_text(state):
    state = state or {}
    health = state.get("health", {}) or {}
    analysis = state.get("analysis_summary", {}) or {}
    recommendations = list(state.get("recommendations", []) or [])
    patterns = state.get("pattern_summary", {}) or {}
    parameter_scores = state.get("parameter_scores", {}) or {}
    top_patterns = patterns.get("success_patterns", []) or []
    weak_patterns = patterns.get("failure_patterns", []) or []
    top_recommendation = recommendations[0] if recommendations else {}
    lines = [
        "REALM XXVI - STRATEGY EVOLUTION REPORT",
        "",
        "Ecosystem Health:",
        f"- Score: {_fmt(health.get('health_score'), 4, 'unknown')}",
        f"- State: {health.get('health_state', 'unknown')}",
        f"- Stability: {_fmt(health.get('stability_score'), 4, 'unknown')}",
        "",
        "Outcome Analysis:",
        f"- Trades: {analysis.get('total_trades', 0)}",
        f"- Win Rate: {_fmt(analysis.get('win_rate'), 4, 'unknown')}",
        f"- Average PnL: {_fmt(analysis.get('average_pnl'), 4, 'unknown')}",
        f"- Risk Adjusted: {_fmt(analysis.get('average_risk_adjusted_outcome'), 4, 'unknown')}",
        "",
        "Patterns:",
        f"- Strong: {len(top_patterns)}",
        f"- Weak: {len(weak_patterns)}",
        "",
        "Parameter Leaders:",
        f"- Count: {len(parameter_scores.get('parameter_leaders', []) or [])}",
        "",
        "Recommendation:",
        f"- {top_recommendation.get('title', 'none')}",
        f"- {top_recommendation.get('recommendation', 'No recommendation available.')}",
    ]
    return "\n".join(lines)


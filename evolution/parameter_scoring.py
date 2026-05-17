from collections import defaultdict


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value, minimum=0.0, maximum=1.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


class ParameterScorer:
    def __init__(self):
        self.parameters = (
            "realm_x_adaptive_mode",
            "realm_x_take_profit_mode",
            "realm_x_stop_behavior_mode",
            "realm_xi_breakout_state",
            "realm_xi_momentum_direction",
            "realm_xi_momentum_strength",
            "realm_xii_conviction_level",
            "timing_bucket",
        )

    def score(self, analyses):
        analyses = list(analyses or [])
        if not analyses:
            return {"parameters": {}, "parameter_leaders": [], "parameter_laggards": []}
        grouped = defaultdict(list)
        for analysis in analyses:
            environment = analysis.get("environment_bucket", {}) or {}
            grouped["realm_x_adaptive_mode:" + str(analysis.get("realm_x_adaptive_mode") or "unknown")].append(analysis)
            grouped["realm_x_take_profit_mode:" + str(analysis.get("realm_x_take_profit_mode") or "unknown")].append(analysis)
            grouped["realm_x_stop_behavior_mode:" + str(analysis.get("realm_x_stop_behavior_mode") or "unknown")].append(analysis)
            grouped["realm_xi_breakout_state:" + str(analysis.get("realm_xi_breakout_state") or "unknown")].append(analysis)
            grouped["realm_xi_momentum_direction:" + str(analysis.get("realm_xi_momentum_direction") or "unknown")].append(analysis)
            grouped["realm_xi_momentum_strength:" + str(analysis.get("realm_xi_momentum_strength") or "unknown")].append(analysis)
            grouped["realm_xii_conviction_level:" + str(analysis.get("realm_xii_conviction_level") or "unknown")].append(analysis)
            grouped["timing_bucket:" + str(environment.get("timing_bucket") or "unknown")].append(analysis)
        parameters = {}
        leaders = []
        laggards = []
        for key, records in grouped.items():
            count = len(records)
            average_pnl = sum(_safe_float(row.get("realized_pnl_delta"), 0.0) for row in records) / max(1, count)
            win_rate = sum(1 for row in records if row.get("outcome") == "win") / max(1, count)
            stable_support = sum(_safe_float(row.get("risk_adjusted_outcome"), 0.0) for row in records) / max(1, count)
            raw_score = 0.40 + (average_pnl * 4.0) + (win_rate * 0.35) + ((stable_support - 0.5) * 0.30)
            score = _clamp(raw_score)
            label = "improving" if score >= 0.65 else "highly_reliable" if score >= 0.80 else "degrading" if score <= 0.40 else "unstable"
            parameter = {
                "parameter_key": key,
                "count": count,
                "average_pnl": round(average_pnl, 4),
                "win_rate": round(win_rate, 4),
                "support": round(stable_support, 4),
                "score": round(score, 4),
                "label": label,
            }
            parameters[key] = parameter
            if score >= 0.65:
                leaders.append(parameter)
            if score <= 0.45:
                laggards.append(parameter)
        leaders = sorted(leaders, key=lambda item: (item["score"], item["count"]), reverse=True)[:10]
        laggards = sorted(laggards, key=lambda item: (item["score"], item["count"]))[:10]
        return {"parameters": parameters, "parameter_leaders": leaders, "parameter_laggards": laggards}


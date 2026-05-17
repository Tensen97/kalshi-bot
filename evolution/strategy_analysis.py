import math
from collections import Counter, defaultdict


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


def _derive_environment_bucket(row):
    category = str(
        row.get("category")
        or row.get("market_category")
        or row.get("event_category")
        or "unknown"
    ).strip().lower()
    liquidity = _safe_float(row.get("liquidity"), 0.0)
    spread = _safe_float(row.get("spread"), None)
    hold_seconds = _safe_float(row.get("hold_seconds"), 0.0)
    if liquidity <= 15.0:
        liquidity_bucket = "low"
    elif liquidity <= 60.0:
        liquidity_bucket = "medium"
    else:
        liquidity_bucket = "high"
    if spread is None:
        spread_bucket = "unknown"
    elif spread <= 0.03:
        spread_bucket = "tight"
    elif spread <= 0.06:
        spread_bucket = "normal"
    else:
        spread_bucket = "wide"
    if hold_seconds < 180:
        timing_bucket = "fast"
    elif hold_seconds < 600:
        timing_bucket = "normal"
    else:
        timing_bucket = "slow"
    return {
        "category": category or "unknown",
        "liquidity_bucket": liquidity_bucket,
        "spread_bucket": spread_bucket,
        "timing_bucket": timing_bucket,
    }


def analyze_trade_row(row):
    row = dict(row or {})
    realized_pnl = _safe_float(row.get("realized_pnl_delta"), 0.0)
    outcome = str(row.get("outcome") or "").strip().lower()
    if not outcome:
        outcome = "win" if realized_pnl > 0 else "loss" if realized_pnl < 0 else "breakeven"
    entry_price = _safe_float(row.get("entry_price"), 0.0)
    exit_price = _safe_float(row.get("exit_price"), 0.0)
    hold_seconds = _safe_float(row.get("hold_seconds"), 0.0)
    setup_signature = str(row.get("setup_signature") or "unknown").strip() or "unknown"
    realm_xi_breakout_state = str(row.get("realm_xi_breakout_state") or "unknown").strip().lower()
    realm_x_adaptive_mode = str(row.get("realm_x_adaptive_mode") or "unknown").strip().lower()
    realm_x_take_profit_mode = str(row.get("realm_x_take_profit_mode") or "unknown").strip().lower()
    realm_x_stop_behavior_mode = str(row.get("realm_x_stop_behavior_mode") or "unknown").strip().lower()
    realm_xi_momentum_direction = str(row.get("realm_xi_momentum_direction") or "unknown").strip().lower()
    realm_xi_momentum_strength = str(row.get("realm_xi_momentum_strength") or "unknown").strip().lower()
    realm_xii_conviction_level = str(row.get("realm_xii_conviction_level") or "unknown").strip().lower()
    environment = _derive_environment_bucket(row)
    pnl_ratio = realized_pnl / max(abs(entry_price or 0.0), 0.05) if realized_pnl else 0.0
    hold_factor = 1.0 - min(0.85, hold_seconds / 1800.0)
    if outcome == "win":
        entry_quality = _clamp(0.62 + min(0.28, max(0.0, pnl_ratio) * 0.8) + (0.05 if hold_seconds <= 600 else 0.0))
        exit_quality = _clamp(0.60 + min(0.25, max(0.0, realized_pnl) * 4.0) + (0.05 if hold_seconds <= 900 else 0.0))
    elif outcome == "loss":
        entry_quality = _clamp(0.38 - min(0.18, abs(pnl_ratio) * 0.5))
        exit_quality = _clamp(0.40 - min(0.18, abs(realized_pnl) * 2.0))
    else:
        entry_quality = 0.5
        exit_quality = 0.5
    risk_adjusted_outcome = _clamp(0.5 + (realized_pnl * 4.0) + (hold_factor - 0.5) * 0.15)
    conviction_map = {
        "high": 0.9,
        "medium": 0.7,
        "low": 0.4,
        "unknown": 0.5,
    }
    strength_map = {
        "strong": 0.9,
        "moderate": 0.7,
        "weak": 0.35,
        "unknown": 0.5,
    }
    breakout_map = {
        "confirmed_breakout": 0.9,
        "pre_breakout": 0.68,
        "failed_breakout": 0.25,
        "none": 0.5,
        "unknown": 0.5,
    }
    adaptive_map = {
        "aggressive": 0.82,
        "normal": 0.65,
        "defensive": 0.38,
        "conservative": 0.34,
        "unknown": 0.5,
    }
    personality_alignment_score = _clamp(
        0.30
        + breakout_map.get(realm_xi_breakout_state, 0.5) * 0.20
        + strength_map.get(realm_xi_momentum_strength, 0.5) * 0.18
        + conviction_map.get(realm_xii_conviction_level, 0.5) * 0.18
        + adaptive_map.get(realm_x_adaptive_mode, 0.5) * 0.14
        + (0.08 if realm_xi_momentum_direction in {"up", "down", "neutral"} else 0.0)
    )
    edge_accuracy_score = _clamp(0.45 + (1.0 if outcome == "win" else -0.35 if outcome == "loss" else 0.0) * 0.25 + entry_quality * 0.25)
    conviction_accuracy_score = _clamp(0.45 + conviction_map.get(realm_xii_conviction_level, 0.5) * (0.30 if outcome == "win" else -0.18 if outcome == "loss" else 0.0))
    catalyst_relevance_score = _clamp(
        0.40
        + (0.08 if "catalyst" in setup_signature else 0.0)
        + (0.05 if str(environment.get("category") or "unknown").strip().lower() in {"sports", "weather", "macro", "crypto"} else 0.0)
        + (0.04 if "event" in setup_signature else 0.0)
    )
    volatility_environment_score = _clamp(
        0.35
        + (0.15 if environment["spread_bucket"] == "tight" else -0.05 if environment["spread_bucket"] == "wide" else 0.0)
        + (0.12 if realm_xi_breakout_state == "confirmed_breakout" else -0.08 if realm_xi_breakout_state == "failed_breakout" else 0.0)
    )
    liquidity_environment_score = _clamp(
        0.35
        + (0.15 if environment["liquidity_bucket"] == "high" else 0.05 if environment["liquidity_bucket"] == "medium" else -0.08)
        + (0.05 if realm_x_adaptive_mode in {"normal", "aggressive"} else -0.03 if realm_x_adaptive_mode in {"defensive", "conservative"} else 0.0)
    )
    timing_effectiveness_score = _clamp(
        0.40
        + (0.12 if environment["timing_bucket"] == "normal" else 0.06 if environment["timing_bucket"] == "fast" else -0.04)
        + (0.06 if hold_seconds <= 600 else -0.02)
        + (0.08 if outcome == "win" and hold_seconds <= 600 else 0.0)
    )
    return {
        "ticker": row.get("ticker"),
        "family": row.get("family"),
        "category": row.get("category"),
        "setup_signature": setup_signature,
        "outcome": outcome,
        "realized_pnl_delta": realized_pnl,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "hold_seconds": hold_seconds,
        "pnl_ratio": round(pnl_ratio, 4),
        "entry_quality": round(entry_quality, 4),
        "exit_quality": round(exit_quality, 4),
        "risk_adjusted_outcome": round(risk_adjusted_outcome, 4),
        "personality_alignment_score": round(personality_alignment_score, 4),
        "edge_accuracy_score": round(edge_accuracy_score, 4),
        "conviction_accuracy_score": round(conviction_accuracy_score, 4),
        "catalyst_relevance_score": round(catalyst_relevance_score, 4),
        "volatility_environment_score": round(volatility_environment_score, 4),
        "liquidity_environment_score": round(liquidity_environment_score, 4),
        "timing_effectiveness_score": round(timing_effectiveness_score, 4),
        "realm_x_adaptive_mode": realm_x_adaptive_mode,
        "realm_x_take_profit_mode": realm_x_take_profit_mode,
        "realm_x_stop_behavior_mode": realm_x_stop_behavior_mode,
        "realm_xi_breakout_state": realm_xi_breakout_state,
        "realm_xi_momentum_direction": realm_xi_momentum_direction,
        "realm_xi_momentum_strength": realm_xi_momentum_strength,
        "realm_xii_conviction_level": realm_xii_conviction_level,
        "environment_bucket": environment,
    }


def aggregate_outcomes(analyses):
    analyses = list(analyses or [])
    if not analyses:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "loss_rate": 0.0,
            "breakeven_rate": 0.0,
            "average_pnl": 0.0,
            "average_risk_adjusted_outcome": 0.0,
            "entry_quality": 0.5,
            "exit_quality": 0.5,
            "personality_alignment": 0.5,
            "edge_accuracy": 0.5,
            "conviction_accuracy": 0.5,
            "catalyst_relevance": 0.5,
            "volatility_environment": 0.5,
            "liquidity_environment": 0.5,
            "timing_effectiveness": 0.5,
            "top_patterns": [],
            "weak_patterns": [],
        }
    total = len(analyses)
    outcomes = Counter(item.get("outcome") for item in analyses)
    total_pnl = sum(_safe_float(item.get("realized_pnl_delta"), 0.0) for item in analyses)
    metric_keys = [
        "entry_quality",
        "exit_quality",
        "risk_adjusted_outcome",
        "personality_alignment_score",
        "edge_accuracy_score",
        "conviction_accuracy_score",
        "catalyst_relevance_score",
        "volatility_environment_score",
        "liquidity_environment_score",
        "timing_effectiveness_score",
    ]
    averages = {key: sum(_safe_float(item.get(key), 0.0) for item in analyses) / total for key in metric_keys}
    grouped = defaultdict(list)
    for item in analyses:
        grouped[item.get("setup_signature") or "unknown"].append(item)
    top_patterns = []
    weak_patterns = []
    for signature, records in grouped.items():
        average_pnl = sum(_safe_float(row.get("realized_pnl_delta"), 0.0) for row in records) / len(records)
        win_count = sum(1 for row in records if row.get("outcome") == "win")
        score = (average_pnl * 100.0) + (win_count / max(1, len(records))) * 25.0
        payload = {
            "setup_signature": signature,
            "count": len(records),
            "average_pnl": round(average_pnl, 4),
            "win_rate": round(win_count / len(records), 4),
            "score": round(score, 4),
        }
        if score >= 5.0:
            top_patterns.append(payload)
        else:
            weak_patterns.append(payload)
    top_patterns = sorted(top_patterns, key=lambda item: (item["score"], item["count"]), reverse=True)[:10]
    weak_patterns = sorted(weak_patterns, key=lambda item: (item["score"], item["count"]))[:10]
    return {
        "total_trades": total,
        "win_rate": round(outcomes.get("win", 0) / total, 4),
        "loss_rate": round(outcomes.get("loss", 0) / total, 4),
        "breakeven_rate": round(outcomes.get("breakeven", 0) / total, 4),
        "average_pnl": round(total_pnl / total, 4),
        "average_risk_adjusted_outcome": round(averages["risk_adjusted_outcome"], 4),
        "entry_quality": round(averages["entry_quality"], 4),
        "exit_quality": round(averages["exit_quality"], 4),
        "personality_alignment": round(averages["personality_alignment_score"], 4),
        "edge_accuracy": round(averages["edge_accuracy_score"], 4),
        "conviction_accuracy": round(averages["conviction_accuracy_score"], 4),
        "catalyst_relevance": round(averages["catalyst_relevance_score"], 4),
        "volatility_environment": round(averages["volatility_environment_score"], 4),
        "liquidity_environment": round(averages["liquidity_environment_score"], 4),
        "timing_effectiveness": round(averages["timing_effectiveness_score"], 4),
        "top_patterns": top_patterns,
        "weak_patterns": weak_patterns,
    }

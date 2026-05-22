"""Candidate quality and ranking helpers extracted from the live pipeline."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default
            return float(stripped)
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    numeric = to_float(value, 0.0) or 0.0
    return max(minimum, min(maximum, numeric))


def _pick(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _safe_round(value: Any, ndigits: int = 4, default: Any = None) -> Any:
    numeric = to_float(value, None)
    if numeric is None:
        return default
    try:
        return round(float(numeric), int(ndigits))
    except (TypeError, ValueError):
        return default


def rank_final_candidates(candidates):
    def _candidate_rank_key(item):
        if isinstance(item, dict):
            quality_score = to_float(item.get("quality_score"), to_float(item.get("candidate_score"), 0.0))
            candidate_score = to_float(item.get("candidate_score"), 0.0)
            adjusted_score = to_float(item.get("adjusted_score"), candidate_score)
            spread = to_float(item.get("spread"), 0.0)
            liquidity = to_float(item.get("liquidity"), 0.0)
            volume = to_float(item.get("volume"), 0.0)
            return (
                quality_score,
                candidate_score,
                adjusted_score,
                -spread,
                liquidity,
                volume,
            )
        if isinstance(item, (list, tuple)) and item:
            candidate_score = to_float(item[0], 0.0)
            spread = to_float(item[2], 0.0) if len(item) > 2 else 0.0
            liquidity = to_float(getattr(item[1], "liquidity", 0.0), 0.0) if len(item) > 1 else 0.0
            volume = to_float(getattr(item[1], "volume", 0.0), 0.0) if len(item) > 1 else 0.0
            return (
                candidate_score,
                -spread,
                liquidity,
                volume,
            )
        return (0.0, 0.0, 0.0, 0.0)

    return sorted(candidates, key=_candidate_rank_key, reverse=True)


def compute_family_weight(self, family):
    family = str(family or "UNKNOWN").strip() or "UNKNOWN"
    if not getattr(self, "KALSHI_FAMILY_PERF_ENABLED", True):
        print(f"[LIVE-FAMILY-PERF-SKIP] family={family} reason=feature_disabled")
        return 0.0
    stats = dict(getattr(self, "live_family_perf_stats", {}).get(family, {}) or {})
    trades = int(stats.get("trades", 0) or 0)
    pnl = float(getattr(self, "live_realized_pnl_by_family_today", {}).get(family, stats.get("pnl", 0.0)) or 0.0)
    loss_streak = int(stats.get("loss_streak", 0) or 0)
    min_trades = getattr(self, "KALSHI_FAMILY_MIN_TRADES_FOR_PENALTY", 0)
    negative_penalty = getattr(self, "KALSHI_FAMILY_NEGATIVE_PNL_PENALTY", 0.0)
    positive_boost = getattr(self, "KALSHI_FAMILY_POSITIVE_PNL_BOOST", 0.0)
    loss_streak_penalty = getattr(self, "KALSHI_FAMILY_LOSS_STREAK_PENALTY", 0.0)
    if trades < min_trades:
        print(
            f"[LIVE-FAMILY-PERF-SKIP] family={family} reason=insufficient_trades "
            f"trades={trades} min_trades={min_trades}"
        )
        return 0.0
    adjustment = 0.0
    if pnl < 0:
        adjustment -= negative_penalty
        print(
            f"[LIVE-FAMILY-RANKING-PENALTY] family={family} type=negative_pnl "
            f"pnl={pnl} penalty={negative_penalty}"
        )
    elif pnl > 0:
        adjustment += positive_boost
        print(
            f"[LIVE-FAMILY-RANKING-BOOST] family={family} type=positive_pnl "
            f"pnl={pnl} boost={positive_boost}"
        )
    if loss_streak >= 2:
        adjustment -= loss_streak_penalty
        print(
            f"[LIVE-FAMILY-RANKING-PENALTY] family={family} type=loss_streak "
            f"loss_streak={loss_streak} penalty={loss_streak_penalty}"
        )
    print(
        "[AION-FAMILY-WEIGHT] "
        + json.dumps(
            {
                "family": family,
                "trades": trades,
                "pnl": round(pnl, 4),
                "loss_streak": loss_streak,
                "adjustment": round(adjustment, 4),
                "reason": "family_performance_weight",
            },
            sort_keys=True,
            default=str,
        )
    )
    print(
        f"[LIVE-FAMILY-PERF-WEIGHT] family={family} trades={trades} pnl={pnl} "
        f"loss_streak={loss_streak} adjustment={round(adjustment, 4)}"
    )
    return float(adjustment)


def compute_fill_quality_score(self, *, microstructure_summary=None, persistence_summary=None):
    microstructure_summary = microstructure_summary if isinstance(microstructure_summary, dict) else {}
    persistence_summary = persistence_summary if isinstance(persistence_summary, dict) else {}
    execution_survivability = _clamp(to_float(microstructure_summary.get("execution_survivability_score"), 0.5))
    quote_stability = _clamp(
        to_float(microstructure_summary.get("quote_stability_score"), 1.0 - to_float(microstructure_summary.get("quote_churn_frequency"), 0.0))
    )
    edge_durability = _clamp(
        to_float(microstructure_summary.get("edge_durability_score"), to_float(persistence_summary.get("adaptive_persistence_score"), 0.5))
    )
    liquidity_reliability = _clamp(to_float(microstructure_summary.get("liquidity_reliability"), 0.5))
    fill_quality_score = _clamp(
        execution_survivability * 0.36
        + quote_stability * 0.28
        + edge_durability * 0.20
        + liquidity_reliability * 0.16
    )
    fill_quality_bucket = "strong" if fill_quality_score >= 0.7 else "usable" if fill_quality_score >= 0.5 else "fragile"
    print(
        "[AION-FILL-QUALITY] "
        + json.dumps(
            {
                "ticker": microstructure_summary.get("ticker") or None,
                "family": microstructure_summary.get("family") or None,
                "fill_quality_score": round(fill_quality_score, 4),
                "fill_quality_bucket": fill_quality_bucket,
                "quote_stability": round(quote_stability, 4),
                "edge_durability": round(edge_durability, 4),
                "execution_survivability": round(execution_survivability, 4),
                "reason": "expectancy_fill_quality",
            },
            sort_keys=True,
            default=str,
        )
    )
    return {
        "fill_quality_score": round(fill_quality_score, 4),
        "fill_quality_bucket": fill_quality_bucket,
        "quote_stability": round(quote_stability, 4),
        "edge_durability": round(edge_durability, 4),
        "execution_survivability": round(execution_survivability, 4),
        "liquidity_reliability": round(liquidity_reliability, 4),
    }


def build_expectancy_score(
    self,
    *,
    market=None,
    q=None,
    family=None,
    spread=None,
    candidate_score=0.0,
    score_breakdown=None,
    entry_timing_observation=None,
    predictive_regime_summary=None,
    adaptive_confidence_summary=None,
    family_rotation_summary=None,
    queue_summary=None,
    microstructure_summary=None,
    learning_summary=None,
    family_learning_summary=None,
    persistence_summary=None,
    sports_momentum=None,
    sports_edge=None,
    sports_context=None,
    near_miss_count=0,
    phase55_status=None,
):
    market = market if isinstance(market, dict) else {}
    score_breakdown = score_breakdown if isinstance(score_breakdown, dict) else {}
    entry_timing_observation = entry_timing_observation if isinstance(entry_timing_observation, dict) else {}
    predictive_regime_summary = predictive_regime_summary if isinstance(predictive_regime_summary, dict) else {}
    adaptive_confidence_summary = adaptive_confidence_summary if isinstance(adaptive_confidence_summary, dict) else {}
    family_rotation_summary = family_rotation_summary if isinstance(family_rotation_summary, dict) else {}
    queue_summary = queue_summary if isinstance(queue_summary, dict) else {}
    microstructure_summary = microstructure_summary if isinstance(microstructure_summary, dict) else {}
    learning_summary = learning_summary if isinstance(learning_summary, dict) else {}
    family_learning_summary = family_learning_summary if isinstance(family_learning_summary, dict) else {}
    persistence_summary = persistence_summary if isinstance(persistence_summary, dict) else {}
    sports_momentum = sports_momentum if isinstance(sports_momentum, dict) else {}
    sports_edge = sports_edge if isinstance(sports_edge, dict) else {}
    sports_context = sports_context if isinstance(sports_context, dict) else {}
    quote_readiness_diagnostic = self._diagnose_quote_readiness_context(
        market=market,
        q=q,
        family=family,
        sports_context=sports_context,
        source="expectancy_score",
    )
    sports_context = dict(sports_context)
    sports_context["quote_ready"] = bool(quote_readiness_diagnostic.get("quote_ready"))
    sports_context["quote_missing"] = bool(quote_readiness_diagnostic.get("quote_missing"))
    sports_context["quote_readiness_diagnostic"] = {k: v for k, v in quote_readiness_diagnostic.items() if k != "fallback_quote"}
    ticker = str(getattr(q, "ticker", "") or market.get("ticker", "") or "").strip()
    family = str(family or self.get_market_family(market) or getattr(q, "family", "") or "").strip().upper() or "UNKNOWN"
    spread = self.to_float(spread, None)
    if spread is None:
        yes_bid = self.to_float(getattr(q, "yes_bid", None), self.to_float(market.get("yes_bid"), 0.0))
        yes_ask = self.to_float(getattr(q, "yes_ask", None), self.to_float(market.get("yes_ask"), 0.0))
        spread = max(0.0, yes_ask - yes_bid) if yes_bid > 0.0 and yes_ask > 0.0 else 0.0
    spread = max(0.0, self.to_float(spread, 0.0) or 0.0)
    candidate_score = self.to_float(candidate_score, 0.0) or 0.0
    base_score = self._clamp(candidate_score / max(getattr(self, "PHASE5_BUY_SCORE_THRESHOLD", 1.0), 1.0))
    spread_score = self._clamp(max(0.0, (getattr(self, "MAX_ENTRY_SPREAD", 0.0) - spread) / max(getattr(self, "MAX_ENTRY_SPREAD", 0.0001), 0.0001)))
    liquidity = self.to_float(score_breakdown.get("liquidity"), 0.0) or 0.0
    spread_component = self._clamp(self.to_float(score_breakdown.get("spread"), 0.0) / 25.0)
    timing_component = self._clamp(self.to_float(score_breakdown.get("timing"), 0.0) / 15.0)
    confidence_component = self._clamp(self.to_float(score_breakdown.get("confidence"), 0.0) / 15.0)
    family_component = self._clamp(
        self.to_float(family_learning_summary.get("family_learning_score"), self.to_float(family_rotation_summary.get("family_rotation_score"), 0.5))
    )
    regime_component = self._clamp(
        self.to_float(predictive_regime_summary.get("predictive_regime_score"), self.to_float(predictive_regime_summary.get("top_predictive_score"), 0.5))
    )
    confidence_modifier = self._clamp(0.5 + self.to_float(adaptive_confidence_summary.get("adaptive_confidence_modifier"), 0.0))
    quote_stability = self._clamp(
        self.to_float(microstructure_summary.get("quote_stability_score"), 1.0 - self.to_float(microstructure_summary.get("quote_churn_frequency"), 0.0))
    )
    edge_durability = self._clamp(
        self.to_float(microstructure_summary.get("edge_durability_score"), self.to_float(persistence_summary.get("adaptive_persistence_score"), 0.5))
    )
    execution_survivability = self._clamp(self.to_float(microstructure_summary.get("execution_survivability_score"), 0.5))
    fill_quality_result = compute_fill_quality_score(
        self,
        microstructure_summary=microstructure_summary,
        persistence_summary=persistence_summary,
    )
    fill_quality_score = fill_quality_result["fill_quality_score"]
    fill_quality_bucket = fill_quality_result["fill_quality_bucket"]
    quality_score = 0.0
    reasons: List[str] = []
    queue_state = str(queue_summary.get("queue_state") or queue_summary.get("latest_queue_state") or "unknown").strip().lower()
    queue_component = {
        "strong_watch": 0.86,
        "near_ready": 0.80,
        "improving_watch": 0.74,
        "weak_watch": 0.60,
        "blocked": 0.40,
        "expired": 0.22,
        "unknown": 0.50,
    }.get(queue_state, 0.50)
    near_miss_count = max(0, int(near_miss_count or 0))
    near_miss_component = _clamp(min(1.0, near_miss_count / 5.0))
    opportunity_component = _clamp(self.to_float(persistence_summary.get("adaptive_persistence_score"), 0.0))
    sports_edge_after_cost = self.to_float(sports_context.get("sports_edge_after_cost"), None)
    sports_edge_component = 0.0
    if sports_edge_after_cost is not None:
        sports_edge_component = _clamp(max(0.0, sports_edge_after_cost + 0.02) / 0.06)
    else:
        sports_edge_component = _clamp(max(0.0, self.to_float(sports_edge.get("score"), 0.0) / 100.0))
    time_to_event = self.get_minutes_to_event(market)
    time_component = 0.5
    if time_to_event is not None:
        if time_to_event <= 30.0:
            time_component = 0.74
        elif time_to_event <= 120.0:
            time_component = 0.62
        elif time_to_event <= 360.0:
            time_component = 0.54
        else:
            time_component = 0.42
    volatility_state = str(
        sports_momentum.get("volatility_state")
        or predictive_regime_summary.get("volatility_state")
        or microstructure_summary.get("volatility_state")
        or "unknown"
    ).strip().lower()
    volatility_component = {
        "low": 0.72,
        "normal": 0.60,
        "stable": 0.68,
        "high": 0.40,
        "volatile": 0.28,
        "decaying": 0.22,
        "unknown": 0.50,
    }.get(volatility_state, 0.50)
    expectancy_score = self._clamp(
        base_score * 0.16
        + sports_edge_component * 0.14
        + spread_score * 0.10
        + self._clamp(liquidity / max(getattr(self, "MIN_ENTRY_LIQUIDITY", 1.0), 1.0)) * 0.08
        + confidence_component * 0.08
        + timing_component * 0.08
        + regime_component * 0.10
        + confidence_modifier * 0.08
        + family_component * 0.08
        + quote_stability * 0.08
        + edge_durability * 0.08
        + execution_survivability * 0.08
        + queue_component * 0.03
        + near_miss_component * 0.03
        + opportunity_component * 0.03
        + time_component * 0.02
        + volatility_component * 0.01
    )
    strong_expectancy = bool(
        expectancy_score >= 0.68
        and sports_edge_component >= 0.48
        and quote_stability >= 0.45
        and edge_durability >= 0.42
        and execution_survivability >= 0.40
    )
    positive_expectancy = bool(expectancy_score >= 0.55 and sports_edge_component > 0.0)
    spread_bucket = str(
        sports_context.get("sports_spread_bucket")
        or sports_context.get("spread_bucket")
        or ("tight" if spread <= 0.03 else "acceptable" if spread <= 0.06 else "wide_but_possible" if spread <= getattr(self, "AION_SPORTS_MAX_SPREAD_SOFT", 0.0) else "extreme_wide")
    ).strip().lower()
    liquidity_strength = self._clamp(
        self.to_float(microstructure_summary.get("liquidity_quality"), self.to_float(family_learning_summary.get("liquidity_reliability"), 0.5))
    )
    is_sports = self.is_sports_family(family, ticker=ticker) or self._is_sports_market_or_family(family)
    expect_positive_edge = bool(sports_edge_after_cost is None or sports_edge_after_cost > 0.0)
    spread_liquidity_adaptive_pass = bool(
        expect_positive_edge
        and spread_bucket in {"ultra_tight", "tight", "acceptable", "wide_but_possible"}
        and liquidity_strength >= 0.40
        and quote_stability >= 0.40
    )
    repeated_near_miss = bool(near_miss_count >= 2 or queue_state in {"near_ready", "improving_watch", "strong_watch"})
    controlled_loosening_enabled = bool(getattr(self, "AION_CONTROLLED_LOOSENING_ENABLED", False) and getattr(self, "AION_EXPECTANCY_SCORE_ENABLED", False))
    aggression_settings = self._aion_aggression_profile_settings(getattr(self, "AION_AGGRESSION_PROFILE", "safe"), family=family, spread_bucket=spread_bucket, context="phase55_fastpath")
    phase55_relaxation = self.to_float(aggression_settings.get("phase55_relaxation"), 0.0) or 0.0
    phase55_quote_stability_threshold = max(0.40, 0.48 - (phase55_relaxation * 0.50))
    phase55_edge_durability_threshold = max(0.35, 0.42 - (phase55_relaxation * 0.60))
    phase55_execution_survivability_threshold = max(0.35, 0.40 - (phase55_relaxation * 0.60))
    phase55_regime_component_threshold = max(0.35, 0.40 - (phase55_relaxation * 0.60))
    micro_pullback_quote_threshold = max(0.42, 0.45 - (phase55_relaxation * 0.45))
    micro_pullback_edge_threshold = max(0.35, 0.40 - (phase55_relaxation * 0.50))
    micro_pullback_execution_threshold = max(0.34, 0.38 - (phase55_relaxation * 0.50))
    micro_pullback_expectancy_threshold = max(0.50, 0.60 - (phase55_relaxation * 0.80))
    phase55_fastpath_allowed = bool(
        controlled_loosening_enabled
        and expect_positive_edge
        and spread_bucket in {"ultra_tight", "tight", "acceptable", "wide_but_possible"}
        and quote_stability >= phase55_quote_stability_threshold
        and edge_durability >= phase55_edge_durability_threshold
        and execution_survivability >= phase55_execution_survivability_threshold
        and regime_component >= phase55_regime_component_threshold
        and strong_expectancy
        and repeated_near_miss
        and (not is_sports or sports_edge_component >= 0.40)
    )
    micro_pullback_relax_allowed = bool(
        controlled_loosening_enabled
        and expect_positive_edge
        and spread_bucket in {"ultra_tight", "tight", "acceptable", "wide_but_possible"}
        and quote_stability >= micro_pullback_quote_threshold
        and edge_durability >= micro_pullback_edge_threshold
        and execution_survivability >= micro_pullback_execution_threshold
        and repeated_near_miss
        and expectancy_score >= micro_pullback_expectancy_threshold
    )
    controlled_cooldown_bypass_allowed = bool(
        controlled_loosening_enabled
        and expect_positive_edge
        and strong_expectancy
        and spread_bucket in {"ultra_tight", "tight", "acceptable", "wide_but_possible"}
        and liquidity_strength >= 0.45
    )
    relaxation_reason = "expectancy_support"
    if phase55_fastpath_allowed:
        relaxation_reason = "phase55_fastpath"
    elif micro_pullback_relax_allowed:
        relaxation_reason = "micro_pullback_relaxation"
    elif controlled_cooldown_bypass_allowed:
        relaxation_reason = "cooldown_soft_bypass"
    soft_priority_adjustment = max(-0.015, min(0.015, (expectancy_score - 0.5) * 0.03))
    self._emit_aion_aggression_trace(
        "AION-AGGRESSION-REACHABILITY",
        {
            "active_profile": aggression_settings.get("profile"),
            "reason": relaxation_reason,
            "original_threshold": round(0.60, 4),
            "adjusted_threshold": round(micro_pullback_expectancy_threshold, 4),
            "candidate_count_before": aggression_settings.get("candidate_count_before"),
            "candidate_count_after": aggression_settings.get("candidate_count_after"),
            "payload_ready_count": aggression_settings.get("payload_ready_count"),
            "phase55_ready_count": aggression_settings.get("phase55_ready_count"),
            "safety_gates_preserved": bool(getattr(self, "AION_CONTROLLED_LOOSENING_ENABLED", False) and getattr(self, "AION_EXPECTANCY_SCORE_ENABLED", False)),
            "ticker": ticker,
            "family": family,
            "spread_bucket": spread_bucket,
            "quote_ready": bool(sports_context.get("quote_ready") or quote_readiness_diagnostic.get("quote_ready")),
            "phase55_ready": bool(phase55_fastpath_allowed),
            "payload_ready": bool(sports_context.get("payload_ready") or quote_readiness_diagnostic.get("payload_ready")),
        },
    )
    self._emit_aion_aggression_trace(
        "AION-AGGRESSION-SAFETY-CHECK",
        {
            "active_profile": aggression_settings.get("profile"),
            "reason": aggression_settings.get("reason"),
            "candidate_count_before": aggression_settings.get("candidate_count_before"),
            "candidate_count_after": aggression_settings.get("candidate_count_after"),
            "payload_ready_count": aggression_settings.get("payload_ready_count"),
            "phase55_ready_count": aggression_settings.get("phase55_ready_count"),
            "safety_gates_preserved": bool(getattr(self, "AION_CONTROLLED_LOOSENING_ENABLED", False) and getattr(self, "AION_EXPECTANCY_SCORE_ENABLED", False)),
            "quote_stability_threshold": round(phase55_quote_stability_threshold, 4),
            "edge_durability_threshold": round(phase55_edge_durability_threshold, 4),
            "execution_survivability_threshold": round(phase55_execution_survivability_threshold, 4),
            "regime_component_threshold": round(phase55_regime_component_threshold, 4),
            "ticker": ticker,
            "family": family,
        },
    )
    profit_target_guidance = self._build_profit_target_guidance(
        candidate_expectancy=expectancy_score,
        candidate_score=candidate_score,
        spread_bucket=spread_bucket,
        quote_stability=quote_stability,
        edge_durability=edge_durability,
        execution_survivability=execution_survivability,
        near_miss_count=near_miss_count,
        family=family,
        ticker=ticker,
    )
    profit_target_priority_adjustment = self._clamp(
        self.to_float(profit_target_guidance.get("profit_target_priority_adjustment"), 0.0)
    )
    if not controlled_loosening_enabled and any((phase55_fastpath_allowed, micro_pullback_relax_allowed, controlled_cooldown_bypass_allowed)):
        self._log_relaxation_blocked(
            ticker=ticker,
            family=family,
            reason="controlled_loosening_disabled",
            expectancy_score=expectancy_score,
            spread_bucket=spread_bucket,
        )
    return {
        "expectancy_score": round(expectancy_score, 4),
        "soft_priority_adjustment": round(soft_priority_adjustment, 4),
        "spread_bucket": spread_bucket,
        "sports_edge_after_cost": round(self.to_float(sports_edge_after_cost, 0.0), 4) if sports_edge_after_cost is not None else None,
        "quote_ready": bool(sports_context.get("quote_ready") or quote_readiness_diagnostic.get("quote_ready")),
        "quote_missing": bool(sports_context.get("quote_missing") or quote_readiness_diagnostic.get("quote_missing")),
        "quote_readiness_diagnostic": sports_context.get("quote_readiness_diagnostic"),
        "expect_positive_edge": bool(expect_positive_edge),
        "positive_expectancy": bool(positive_expectancy),
        "strong_expectancy": bool(strong_expectancy),
        "spread_liquidity_adaptive_pass": bool(spread_liquidity_adaptive_pass),
        "phase55_fastpath_allowed": bool(phase55_fastpath_allowed),
        "micro_pullback_relax_allowed": bool(micro_pullback_relax_allowed),
        "controlled_cooldown_bypass_allowed": bool(controlled_cooldown_bypass_allowed),
        "relaxation_reason": relaxation_reason,
        "near_miss_count": near_miss_count,
        "queue_state": queue_state,
        "profit_target_mode": profit_target_guidance.get("profit_target_mode", getattr(self, "AION_PROFIT_TARGET_MODE", "unknown")),
        "profit_target_priority_adjustment": round(profit_target_priority_adjustment, 4),
        "profit_target_expansion_active": bool(profit_target_guidance.get("opportunity_expansion_active")),
        "profit_target_expansion_level": self.to_float(profit_target_guidance.get("current_expansion_level"), 0.0),
        "profit_target_expectancy_quality": self.to_float(profit_target_guidance.get("expectancy_quality"), 0.0),
        "profit_target_opportunity_density": self.to_float(profit_target_guidance.get("opportunity_density"), 0.0),
        "aggression_profile": aggression_settings.get("profile"),
        "aggression_reason": aggression_settings.get("reason"),
        "aggression_phase55_relaxation": round(phase55_relaxation, 4),
        "aggression_flat_market_relaxation": round(self.to_float(aggression_settings.get("flat_market_relaxation"), 0.0), 4),
        "aggression_effective_thresholds": {
            "quote_stability": round(phase55_quote_stability_threshold, 4),
            "edge_durability": round(phase55_edge_durability_threshold, 4),
            "execution_survivability": round(phase55_execution_survivability_threshold, 4),
            "regime_component": round(phase55_regime_component_threshold, 4),
            "expectancy": round(micro_pullback_expectancy_threshold, 4),
        },
        "quote_stability": round(quote_stability, 4),
        "edge_durability": round(edge_durability, 4),
        "execution_survivability": round(execution_survivability, 4),
        "fill_quality_score": round(fill_quality_score, 4),
        "fill_quality_bucket": fill_quality_bucket,
        "family_component": round(family_component, 4),
        "regime_component": round(regime_component, 4),
        "confidence_modifier": round(confidence_modifier, 4),
        "volatility_state": volatility_state,
        "time_to_event": time_to_event,
    }


def compute_candidate_quality(self, market=None, q=None, expectancy_summary=None, tradeability=None):
    market = market if isinstance(market, dict) else {}
    expectancy_summary = expectancy_summary if isinstance(expectancy_summary, dict) else {}
    tradeability = tradeability if isinstance(tradeability, dict) else {}

    def _pick(*values):
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return None

    def _to_cents(value):
        value = to_float(value, None)
        if value is None:
            return None
        try:
            return int(round(float(value) * 100.0))
        except (TypeError, ValueError):
            return None

    ticker = str(getattr(q, "ticker", "") or market.get("ticker", "") or "").strip()
    family = str(
        expectancy_summary.get("family")
        or tradeability.get("family")
        or self.get_market_family(market)
        or getattr(q, "family", "")
        or "UNKNOWN"
    ).strip().upper() or "UNKNOWN"
    selected_side = str(
        _pick(
            getattr(q, "selected_side", None),
            getattr(q, "side", None),
            expectancy_summary.get("selected_side"),
            tradeability.get("selected_side"),
            tradeability.get("side"),
        )
        or ""
    ).strip().lower() or None
    yes_bid = self.to_float(_pick(
        getattr(q, "yes_bid", None),
        getattr(q, "yes_bid_dollars", None),
        market.get("yes_bid"),
        market.get("yes_bid_dollars"),
        expectancy_summary.get("yes_bid"),
        tradeability.get("yes_bid"),
        tradeability.get("yes_bid_dollars"),
    ), None)
    yes_ask = self.to_float(_pick(
        getattr(q, "yes_ask", None),
        getattr(q, "yes_ask_dollars", None),
        market.get("yes_ask"),
        market.get("yes_ask_dollars"),
        expectancy_summary.get("yes_ask"),
        tradeability.get("yes_ask"),
        tradeability.get("yes_ask_dollars"),
    ), None)
    no_bid = self.to_float(_pick(
        getattr(q, "no_bid", None),
        getattr(q, "no_bid_dollars", None),
        market.get("no_bid"),
        market.get("no_bid_dollars"),
        expectancy_summary.get("no_bid"),
        tradeability.get("no_bid"),
        tradeability.get("no_bid_dollars"),
    ), None)
    no_ask = self.to_float(_pick(
        getattr(q, "no_ask", None),
        getattr(q, "no_ask_dollars", None),
        market.get("no_ask"),
        market.get("no_ask_dollars"),
        expectancy_summary.get("no_ask"),
        tradeability.get("no_ask"),
        tradeability.get("no_ask_dollars"),
    ), None)

    if selected_side == "yes":
        selected_price = _pick(
            getattr(q, "selected_price", None),
            getattr(q, "selected_price_dollars", None),
            expectancy_summary.get("selected_price"),
            expectancy_summary.get("selected_price_dollars"),
            tradeability.get("selected_price"),
            tradeability.get("selected_price_dollars"),
            yes_ask if yes_ask is not None else yes_bid,
            no_ask if no_ask is not None else no_bid,
        )
    elif selected_side == "no":
        selected_price = _pick(
            getattr(q, "selected_price", None),
            getattr(q, "selected_price_dollars", None),
            expectancy_summary.get("selected_price"),
            expectancy_summary.get("selected_price_dollars"),
            tradeability.get("selected_price"),
            tradeability.get("selected_price_dollars"),
            no_ask if no_ask is not None else no_bid,
            yes_ask if yes_ask is not None else yes_bid,
        )
    else:
        selected_price = _pick(
            getattr(q, "selected_price", None),
            getattr(q, "selected_price_dollars", None),
            expectancy_summary.get("selected_price"),
            expectancy_summary.get("selected_price_dollars"),
            tradeability.get("selected_price"),
            tradeability.get("selected_price_dollars"),
            yes_ask if yes_ask is not None else yes_bid,
            no_ask if no_ask is not None else no_bid,
        )
    selected_price = self.to_float(selected_price, None)
    selected_price_cents = _to_cents(
        _pick(
            getattr(q, "selected_price_cents", None),
            expectancy_summary.get("selected_price_cents"),
            tradeability.get("selected_price_cents"),
        )
    )
    if selected_price_cents is None and selected_price is not None:
        selected_price_cents = _to_cents(selected_price)
    if selected_price is None and selected_price_cents is not None:
        selected_price = round(int(selected_price_cents) / 100.0, 4)

    yes_pair_valid = bool(yes_bid is not None and yes_ask is not None and yes_bid > 0.0 and yes_ask > 0.0)
    no_pair_valid = bool(no_bid is not None and no_ask is not None and no_bid > 0.0 and no_ask > 0.0)
    selected_side_executable = bool(
        selected_side == "yes" and yes_pair_valid and selected_price is not None and 0.0 < selected_price < 1.0
    ) or bool(
        selected_side == "no" and no_pair_valid and selected_price is not None and 0.0 < selected_price < 1.0
    )
    executable_side_present = bool(selected_side_executable or yes_pair_valid or no_pair_valid)
    quote_ready = bool(
        expectancy_summary.get("quote_ready")
        if "quote_ready" in expectancy_summary
        else executable_side_present
    )
    orderbook_usable = bool(
        tradeability.get("orderbook_ready")
        if "orderbook_ready" in tradeability
        else executable_side_present
    )
    payload_ready = bool(
        expectancy_summary.get("payload_ready")
        if "payload_ready" in expectancy_summary
        else (
            quote_ready
            and orderbook_usable
            and selected_price_cents is not None
            and 1 <= int(selected_price_cents) <= 99
        )
    )
    selected_price_in_bounds = bool(selected_price_cents is not None and 1 <= int(selected_price_cents) <= 99)
    boundary_price_candidate = bool(selected_price_cents in {0, 100} or selected_price in {0.0, 1.0})
    synthetic_yes_book = bool(yes_bid is not None and yes_ask is not None and yes_bid <= 0.0 and yes_ask <= 0.0)
    synthetic_no_book = bool(no_bid is not None and no_ask is not None and no_bid >= 0.99 and no_ask >= 0.99)
    spread_classification = str(
        tradeability.get("spread_classification")
        or expectancy_summary.get("spread_classification")
        or expectancy_summary.get("spread_bucket")
        or "unknown"
    ).strip().upper() or "UNKNOWN"
    orderbook_age_seconds = self.to_float(
        _pick(
            tradeability.get("orderbook_age_seconds"),
            expectancy_summary.get("orderbook_age_seconds"),
            expectancy_summary.get("cache_age_seconds"),
            market.get("staleness_seconds"),
        ),
        None,
    )
    fill_quality_score = self.to_float(expectancy_summary.get("fill_quality_score"), None)
    family_weight = self.to_float(expectancy_summary.get("family_weight"), None)
    if family_weight is None:
        try:
            family_weight = compute_family_weight(self, family)
        except Exception:
            family_weight = 0.0
    market_activity_score = 0.0
    recent_activity = bool(
        (self.to_float(getattr(q, "volume", None), self.to_float(market.get("volume"), 0.0)) or 0.0) > 0.0
        or (self.to_float(getattr(q, "liquidity", None), self.to_float(market.get("liquidity"), 0.0)) or 0.0) > 0.0
        or (orderbook_age_seconds is not None and orderbook_age_seconds <= 120.0)
    )
    reasons: List[str] = []
    if recent_activity:
        market_activity_score += 8.0
        reasons.append("recent_activity")
    if orderbook_age_seconds is not None:
        if orderbook_age_seconds <= 30.0:
            market_activity_score += 6.0
            reasons.append("fresh_orderbook")
        elif orderbook_age_seconds <= 120.0:
            market_activity_score += 4.0
            reasons.append("recent_orderbook")
        elif orderbook_age_seconds <= 300.0:
            market_activity_score += 1.0
            reasons.append("warm_orderbook")
        else:
            market_activity_score -= 6.0
            reasons.append("stale_orderbook")
    else:
        market_activity_score -= 2.0
        reasons.append("missing_orderbook_age")
    quality_score = market_activity_score
    print(
        "[AION-MARKET-ACTIVITY] "
        + json.dumps(
            {
                "ticker": ticker,
                "family": family,
                "recent_activity": bool(recent_activity),
                "orderbook_age_seconds": orderbook_age_seconds,
                "market_activity_score": round(market_activity_score, 4),
                "fill_quality_score": None if fill_quality_score is None else round(fill_quality_score, 4),
                "reason": "selection_activity_ranking",
            },
            sort_keys=True,
            default=str,
        )
    )
    if family_weight:
        family_weight_effect = max(-8.0, min(8.0, float(family_weight)))
        quality_score += family_weight_effect
        reasons.append("family_weight")
    else:
        family_weight_effect = 0.0
    if fill_quality_score is not None:
        fill_quality_effect = (_clamp(fill_quality_score) - 0.5) * 16.0
        quality_score += fill_quality_effect
        reasons.append("fill_quality")
    else:
        fill_quality_effect = 0.0

    if executable_side_present:
        quality_score += 18.0
        reasons.append("executable_side_present")
    else:
        quality_score -= 28.0
        reasons.append("no_executable_side")

    if selected_side_executable:
        quality_score += 14.0
        reasons.append("selected_side_executable")
    else:
        quality_score -= 14.0
        reasons.append("selected_side_not_executable")

    if selected_price_in_bounds:
        quality_score += 18.0
        reasons.append("selected_price_in_bounds")
    else:
        quality_score -= 24.0
        reasons.append("selected_price_out_of_range")

    if boundary_price_candidate:
        quality_score -= 32.0
        reasons.append("boundary_or_synthetic_price")

    if synthetic_yes_book:
        quality_score -= 45.0
        reasons.append("yes_book_synthetic")

    if synthetic_no_book:
        quality_score -= 45.0
        reasons.append("no_book_synthetic")

    if quote_ready:
        quality_score += 10.0
        reasons.append("quote_ready")
    else:
        quality_score -= 8.0
        reasons.append("quote_not_ready")

    if payload_ready:
        quality_score += 8.0
        reasons.append("payload_ready")
    else:
        quality_score -= 8.0
        reasons.append("payload_not_ready")

    if orderbook_usable:
        quality_score += 14.0
        reasons.append("orderbook_usable")
    else:
        quality_score -= 22.0
        reasons.append("orderbook_not_usable")

    if spread_classification in {"SAFE", "AGGRESSIVE_BUT_TRADEABLE"}:
        quality_score += 8.0
        reasons.append("spread_classification_safe")
    elif spread_classification == "CAUTION_WAIT":
        quality_score += 2.0
        reasons.append("spread_classification_caution")
    elif spread_classification in {"UNSAFE", "UNSAFE_SPREAD"}:
        quality_score -= 10.0
        reasons.append("spread_classification_unsafe")

    if orderbook_age_seconds is not None:
        if orderbook_age_seconds <= 30.0:
            quality_score += 5.0
            reasons.append("fresh_orderbook")
        elif orderbook_age_seconds <= 120.0:
            quality_score += 4.0
            reasons.append("recent_orderbook")
        elif orderbook_age_seconds <= 300.0:
            quality_score += 1.0
            reasons.append("stale_but_usable")
        elif orderbook_age_seconds > 900.0:
            quality_score -= 6.0
            reasons.append("stale_orderbook")

    if yes_pair_valid:
        quality_score += 4.0
        reasons.append("yes_pair_valid")
    if no_pair_valid:
        quality_score += 4.0
        reasons.append("no_pair_valid")
    if yes_pair_valid and no_pair_valid:
        quality_score += 3.0
        reasons.append("both_sides_valid")

    if synthetic_yes_book or synthetic_no_book or boundary_price_candidate:
        preferred_reason = "synthetic_or_boundary_book"
    elif not orderbook_usable:
        preferred_reason = "orderbook_not_usable"
    elif not selected_price_in_bounds:
        preferred_reason = "selected_price_out_of_range"
    elif not executable_side_present:
        preferred_reason = "no_executable_side"
    elif not payload_ready:
        preferred_reason = "payload_not_ready"
    elif not quote_ready:
        preferred_reason = "quote_not_ready"
    elif selected_side_executable and yes_pair_valid and no_pair_valid:
        preferred_reason = "dual_side_executable_book"
    elif selected_side_executable:
        preferred_reason = "selected_side_executable"
    else:
        preferred_reason = "clean_executable_book"

    quality_score = round(quality_score, 4)
    quality_adjustment = round(max(-20.0, min(20.0, quality_score / 5.0)), 4)
    if quality_score >= 40.0:
        quality_bucket = "excellent"
    elif quality_score >= 15.0:
        quality_bucket = "good"
    elif quality_score >= 0.0:
        quality_bucket = "mixed"
    elif quality_score >= -20.0:
        quality_bucket = "boundary"
    else:
        quality_bucket = "garbage"

    return {
        "ticker": ticker or None,
        "family": family,
        "selected_side": selected_side,
        "selected_price": round(selected_price, 4) if selected_price is not None else None,
        "selected_price_cents": selected_price_cents,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "quote_ready": bool(quote_ready),
        "payload_ready": bool(payload_ready),
        "orderbook_usable": bool(orderbook_usable),
        "executable_side_present": bool(executable_side_present),
        "selected_side_executable": bool(selected_side_executable),
        "selected_price_in_bounds": bool(selected_price_in_bounds),
        "boundary_price_candidate": bool(boundary_price_candidate),
        "synthetic_yes_book": bool(synthetic_yes_book),
        "synthetic_no_book": bool(synthetic_no_book),
        "spread_classification": spread_classification,
        "orderbook_age_seconds": orderbook_age_seconds,
        "recent_activity": bool(recent_activity),
        "market_activity_score": round(market_activity_score, 4),
        "family_weight": round(family_weight_effect, 4),
        "fill_quality_score": round(fill_quality_score, 4) if fill_quality_score is not None else None,
        "fill_quality_effect": round(fill_quality_effect, 4),
        "quality_score": quality_score,
        "quality_adjustment": quality_adjustment,
        "quality_bucket": quality_bucket,
        "quality_bucket_rank": {
            "excellent": 4,
            "good": 3,
            "mixed": 2,
            "boundary": 1,
            "garbage": 0,
        }.get(quality_bucket, 0),
        "preferred_reason": preferred_reason,
        "reason_tags": reasons[:12],
    }


def build_candidate_quality_summary(selected_quality_summary=None, selected_quality_snapshot=None):
    selected_quality_summary = selected_quality_summary if isinstance(selected_quality_summary, dict) else {}
    selected_quality_snapshot = selected_quality_snapshot if isinstance(selected_quality_snapshot, dict) else {}
    return {
        "total_candidates": selected_quality_summary.get("total_candidates"),
        "executable_candidates": selected_quality_summary.get("executable_candidates"),
        "boundary_price_candidates": selected_quality_summary.get("boundary_price_candidates"),
        "unusable_orderbook_candidates": selected_quality_summary.get("unusable_orderbook_candidates"),
        "selected_quality_bucket": selected_quality_summary.get("selected_quality_bucket") or selected_quality_snapshot.get("quality_bucket"),
        "selected_quality_score": selected_quality_summary.get("selected_quality_score") or selected_quality_snapshot.get("quality_score"),
        "selected_quality_adjustment": selected_quality_summary.get("selected_quality_adjustment") or selected_quality_snapshot.get("quality_adjustment"),
        "selected_quality_reason": selected_quality_summary.get("selected_quality_reason") or selected_quality_snapshot.get("preferred_reason"),
        "quality_ranking_changed": bool(selected_quality_summary.get("quality_ranking_changed")),
    }


__all__ = [
    "build_candidate_quality_summary",
    "build_expectancy_score",
    "compute_candidate_quality",
    "compute_family_weight",
    "compute_fill_quality_score",
    "rank_final_candidates",
    "to_float",
]

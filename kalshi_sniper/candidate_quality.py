"""Candidate quality and ranking helpers extracted from the live pipeline."""

from __future__ import annotations

import json
import os
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


def compute_probability_estimate_signal(
    self,
    *,
    side=None,
    selected_price=None,
    selected_price_cents=None,
    yes_bid=None,
    yes_ask=None,
    no_bid=None,
    no_ask=None,
    calibrated_probability=None,
    probability_estimate=None,
    fair_probability=None,
    no_vig_probability=None,
    implied_probability=None,
    repricing_velocity=None,
    spread_delta=None,
    liquidity=None,
    liquidity_depth=None,
    yes_top_size=None,
    no_top_size=None,
    volume=None,
    orderbook_age_seconds=None,
    family_weight=None,
    edge_source=None,
    estimated_cost=None,
):
    def _bounded_delta(value, cap=0.02):
        numeric = to_float(value, 0.0) or 0.0
        return max(-cap, min(cap, numeric))

    side_value = str(side or "").strip().lower() or "yes"
    selected_price_value = to_float(selected_price, None)
    if selected_price_value is None and selected_price_cents is not None:
        selected_price_value = max(0.0, min(1.0, (to_float(selected_price_cents, 0.0) or 0.0) / 100.0))
    selected_price_value = None if selected_price_value is None else max(0.01, min(0.99, float(selected_price_value)))

    explicit_sources = [
        ("calibrated_probability", calibrated_probability),
        ("probability_estimate", probability_estimate),
        ("fair_probability", fair_probability),
        ("no_vig_probability", no_vig_probability),
        ("implied_probability", implied_probability),
    ]
    explicit_probability = None
    explicit_source_name = None
    for candidate_name, candidate_value in explicit_sources:
        candidate_probability = to_float(candidate_value, None)
        if candidate_probability is None:
            continue
        explicit_probability = max(0.01, min(0.99, candidate_probability))
        explicit_source_name = candidate_name
        break

    yes_mid = None
    if yes_bid is not None or yes_ask is not None:
        yes_bid_value = to_float(yes_bid, None)
        yes_ask_value = to_float(yes_ask, None)
        if yes_bid_value is not None and yes_ask_value is not None and yes_bid_value > 0.0 and yes_ask_value > 0.0:
            yes_mid = max(0.01, min(0.99, (yes_bid_value + yes_ask_value) / 2.0))
        elif yes_ask_value is not None and yes_ask_value > 0.0:
            yes_mid = max(0.01, min(0.99, yes_ask_value))
        elif yes_bid_value is not None and yes_bid_value > 0.0:
            yes_mid = max(0.01, min(0.99, yes_bid_value))

    no_mid = None
    if no_bid is not None or no_ask is not None:
        no_bid_value = to_float(no_bid, None)
        no_ask_value = to_float(no_ask, None)
        if no_bid_value is not None and no_ask_value is not None and no_bid_value > 0.0 and no_ask_value > 0.0:
            no_mid = max(0.01, min(0.99, (no_bid_value + no_ask_value) / 2.0))
        elif no_ask_value is not None and no_ask_value > 0.0:
            no_mid = max(0.01, min(0.99, no_ask_value))
        elif no_bid_value is not None and no_bid_value > 0.0:
            no_mid = max(0.01, min(0.99, no_bid_value))

    selected_side_probability = None
    implied_probability_value = None
    market_implied_source_name = None
    if side_value == "no":
        if no_mid is not None:
            selected_side_probability = no_mid
            implied_probability_value = no_mid
            market_implied_source_name = "market_implied_probability"
        elif explicit_probability is not None:
            selected_side_probability = max(0.01, min(0.99, 1.0 - explicit_probability))
            implied_probability_value = selected_side_probability
        elif selected_price_value is not None:
            selected_side_probability = selected_price_value
            implied_probability_value = selected_price_value
    else:
        if yes_mid is not None:
            selected_side_probability = yes_mid
            implied_probability_value = yes_mid
            market_implied_source_name = "market_implied_probability"
        elif explicit_probability is not None:
            selected_side_probability = explicit_probability
            implied_probability_value = explicit_probability
        elif selected_price_value is not None:
            selected_side_probability = selected_price_value
            implied_probability_value = selected_price_value

    if selected_side_probability is None:
        selected_side_probability = selected_price_value if selected_price_value is not None else 0.5
    if implied_probability_value is None:
        if side_value == "no" and no_mid is not None:
            implied_probability_value = no_mid
        elif side_value != "no" and yes_mid is not None:
            implied_probability_value = yes_mid
        elif explicit_probability is not None:
            implied_probability_value = explicit_probability if side_value != "no" else max(0.01, min(0.99, 1.0 - explicit_probability))
        else:
            implied_probability_value = selected_side_probability

    selected_side_probability = max(0.01, min(0.99, to_float(selected_side_probability, 0.5) or 0.5))
    implied_probability_value = max(0.01, min(0.99, to_float(implied_probability_value, selected_side_probability) or selected_side_probability))

    momentum_component = 0.0
    repricing_velocity_value = to_float(repricing_velocity, 0.0) or 0.0
    spread_delta_value = to_float(spread_delta, 0.0) or 0.0
    momentum_component += _bounded_delta(repricing_velocity_value * 18.0, 0.025)
    momentum_component += _bounded_delta(spread_delta_value * 1.6, 0.020)

    liquidity_component = 0.0
    best_liquidity = max(
        to_float(liquidity, 0.0) or 0.0,
        to_float(liquidity_depth, 0.0) or 0.0,
    )
    if best_liquidity > 0.0:
        liquidity_component += min(0.020, (best_liquidity / 25.0) * 0.020)
    selected_top_size = to_float(yes_top_size if side_value != "no" else no_top_size, None)
    opposite_top_size = to_float(no_top_size if side_value != "no" else yes_top_size, None)
    if selected_top_size is not None and opposite_top_size is not None and (selected_top_size > 0.0 or opposite_top_size > 0.0):
        imbalance = (selected_top_size - opposite_top_size) / max(selected_top_size + opposite_top_size, 1.0)
        liquidity_component += _bounded_delta(imbalance * 0.02, 0.015)

    activity_component = 0.0
    volume_value = to_float(volume, 0.0) or 0.0
    if volume_value > 0.0:
        activity_component += min(0.012, (volume_value / max(volume_value + 80.0, 1.0)) * 0.012)
    orderbook_age_value = to_float(orderbook_age_seconds, None)
    if orderbook_age_value is not None:
        if orderbook_age_value <= 30.0:
            activity_component += 0.010
        elif orderbook_age_value <= 120.0:
            activity_component += 0.006
        elif orderbook_age_value <= 300.0:
            activity_component += 0.002
        elif orderbook_age_value > 900.0:
            activity_component -= 0.010

    family_component = _bounded_delta(to_float(family_weight, 0.0) * 0.010, 0.020)

    estimate_components = {
        "momentum": momentum_component,
        "liquidity": liquidity_component,
        "activity": activity_component,
        "family": family_component,
    }
    probability_base = selected_side_probability
    if explicit_probability is not None:
        probability_base = explicit_probability if side_value != "no" else max(0.01, min(0.99, 1.0 - explicit_probability))
    probability_estimate_value = max(
        0.01,
        min(
            0.99,
            probability_base + momentum_component + liquidity_component + activity_component + family_component,
        ),
    )
    edge_before_cost_value = probability_estimate_value - selected_price_value if selected_price_value is not None else None
    estimated_cost_value = to_float(estimated_cost, 0.0) or 0.0
    edge_after_cost_value = None if edge_before_cost_value is None else round(edge_before_cost_value - estimated_cost_value, 6)
    if edge_source is not None:
        edge_source_value = str(edge_source or "").strip().lower() or "unknown"
    elif explicit_source_name is not None:
        edge_source_value = str(explicit_source_name or "explicit_probability_model").strip().lower()
    elif market_implied_source_name is not None:
        edge_source_value = market_implied_source_name
    else:
        edge_source_value = "selected_price_baseline"
    if explicit_source_name is not None:
        reason = f"explicit:{explicit_source_name}"
        fallback_used = False
    elif any(component != 0.0 for component in (momentum_component, liquidity_component, activity_component, family_component)):
        reason = "signal_adjusted_probability"
        fallback_used = False
    else:
        reason = "selected_price_baseline"
        fallback_used = True
    return {
        "probability_estimate": round(probability_estimate_value, 6),
        "implied_probability": round(implied_probability_value, 6),
        "edge_before_cost": None if edge_before_cost_value is None else round(edge_before_cost_value, 6),
        "edge_after_cost": edge_after_cost_value,
        "edge_source": edge_source_value,
        "fallback_used": bool(fallback_used),
        "momentum_component": round(momentum_component, 6),
        "liquidity_component": round(liquidity_component, 6),
        "activity_component": round(activity_component, 6),
        "family_component": round(family_component, 6),
        "reason": reason,
        "selected_side_probability": round(selected_side_probability, 6),
        "probability_components": {
            "momentum": round(momentum_component, 6),
            "liquidity": round(liquidity_component, 6),
            "activity": round(activity_component, 6),
            "family": round(family_component, 6),
        },
        "explicit_probability": round(explicit_probability, 6) if explicit_probability is not None else None,
    }


def _ev_first(mapping: Dict[str, Any], *keys: str) -> Any:
    mapping = mapping if isinstance(mapping, dict) else {}
    nested = (
        mapping,
        mapping.get("expectancy_summary") if isinstance(mapping.get("expectancy_summary"), dict) else {},
        mapping.get("quality_snapshot") if isinstance(mapping.get("quality_snapshot"), dict) else {},
        mapping.get("tradeability") if isinstance(mapping.get("tradeability"), dict) else {},
        mapping.get("canonical_tradeability") if isinstance(mapping.get("canonical_tradeability"), dict) else {},
        mapping.get("quote_diag") if isinstance(mapping.get("quote_diag"), dict) else {},
        mapping.get("memory_profile") if isinstance(mapping.get("memory_profile"), dict) else {},
        mapping.get("adaptive_confidence_summary") if isinstance(mapping.get("adaptive_confidence_summary"), dict) else {},
    )
    for source in nested:
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    q = mapping.get("q") or mapping.get("quote")
    if q is not None:
        for key in keys:
            value = getattr(q, key, None)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return None


def _ev_probability(value: Any, default: Optional[float] = None) -> Optional[float]:
    numeric = to_float(value, None)
    if numeric is None:
        return default
    if numeric > 1.0 and numeric <= 100.0:
        numeric = numeric / 100.0
    return max(0.01, min(0.99, float(numeric)))


def _ev_price(value: Any, default: Optional[float] = None) -> Optional[float]:
    numeric = to_float(value, None)
    if numeric is None:
        return default
    if numeric > 1.0 and numeric <= 100.0:
        numeric = numeric / 100.0
    return max(0.01, min(0.99, float(numeric)))


def _probability_confidence_bucket(probability: Any) -> str:
    probability_value = _ev_probability(probability, 0.50) or 0.50
    if probability_value < 0.45:
        return "low"
    if probability_value < 0.55:
        return "neutral"
    if probability_value < 0.65:
        return "lean"
    if probability_value < 0.75:
        return "high"
    return "very_high"


def _calibration_reliability_weight(sample_size: int) -> float:
    sample_size = max(0, int(sample_size or 0))
    if sample_size <= 0:
        return 0.0
    if sample_size < 10:
        return min(0.10, sample_size / 100.0)
    if sample_size < 30:
        return min(0.20, 0.10 + ((sample_size - 10) / 20.0) * 0.10)
    if sample_size < 100:
        return min(0.35, 0.20 + ((sample_size - 30) / 70.0) * 0.15)
    return min(0.50, 0.35 + (min(sample_size, 300) - 100) / 200.0 * 0.15)


def _calibration_max_delta(sample_size: int) -> float:
    sample_size = max(0, int(sample_size or 0))
    if sample_size < 10:
        return 0.035
    if sample_size < 30:
        return 0.070
    if sample_size < 100:
        return 0.120
    return 0.180


def _outcome_memory_probability(outcome_memory: Any) -> Optional[float]:
    if not isinstance(outcome_memory, dict):
        return None
    positive_keys = {
        "win",
        "wins",
        "profit",
        "profitable",
        "positive",
        "success",
        "green",
        "accepted_profitable",
    }
    negative_keys = {
        "loss",
        "losses",
        "negative",
        "red",
        "failed",
        "unprofitable",
        "stopped_loss",
    }
    wins = 0
    losses = 0
    for key, value in outcome_memory.items():
        key_text = str(key or "").strip().lower()
        count = int(to_float(value, 0) or 0)
        if count <= 0:
            continue
        if key_text in positive_keys:
            wins += count
        elif key_text in negative_keys:
            losses += count
    total = wins + losses
    if total <= 0:
        return None
    return wins / total


def _outcome_memory_counts(outcome_memory: Any) -> tuple[int, int]:
    if not isinstance(outcome_memory, dict):
        return 0, 0
    positive_keys = {
        "win",
        "wins",
        "profit",
        "profitable",
        "positive",
        "success",
        "green",
        "accepted_profitable",
    }
    negative_keys = {
        "loss",
        "losses",
        "negative",
        "red",
        "failed",
        "unprofitable",
        "stopped_loss",
    }
    wins = 0
    losses = 0
    for key, value in outcome_memory.items():
        key_text = str(key or "").strip().lower()
        count = int(to_float(value, 0) or 0)
        if count <= 0:
            continue
        if key_text in positive_keys:
            wins += count
        elif key_text in negative_keys:
            losses += count
    return wins, losses


def _bayes_weight(sample_size: int) -> float:
    sample_size = max(0, int(sample_size or 0))
    if sample_size <= 0:
        return 0.0
    if sample_size < 10:
        return min(0.05, sample_size / 200.0)
    if sample_size < 30:
        return min(0.12, 0.05 + ((sample_size - 10) / 20.0) * 0.07)
    if sample_size < 100:
        return min(0.22, 0.12 + ((sample_size - 30) / 70.0) * 0.10)
    return min(0.35, 0.22 + ((min(sample_size, 300) - 100) / 200.0) * 0.13)


def apply_bayesian_candidate_adjustment(candidate: Dict[str, Any], raw_probability: Any) -> Dict[str, Any]:
    candidate = candidate if isinstance(candidate, dict) else {}
    raw_probability_value = _ev_probability(raw_probability, None)
    if raw_probability_value is None:
        raw_probability_value = _ev_probability(
            _ev_first(
                candidate,
                "raw_probability",
                "probability_estimate",
                "model_confidence",
                "base_confidence",
                "confidence_probability",
                "confidence_score",
                "confidence",
            ),
            0.50,
        )
    raw_probability_value = max(0.03, min(0.97, raw_probability_value if raw_probability_value is not None else 0.50))
    family_key = str(_ev_first(candidate, "family", "series_ticker") or "").strip().upper() or "UNKNOWN"
    signal_bucket = _probability_confidence_bucket(raw_probability_value)
    wins = 0.0
    losses = 0.0
    source_labels = []

    def _add_counts(label: str, source: Any, source_weight: float = 1.0):
        nonlocal wins, losses
        if not isinstance(source, dict):
            return
        source_weight_value = max(0.10, min(1.25, float(source_weight or 1.0)))
        source_wins = to_float(source.get("wins"), None)
        source_losses = to_float(source.get("losses"), None)
        if source_wins is None and source_losses is None:
            source_wins = to_float(source.get("win_count"), None)
            source_losses = to_float(source.get("loss_count"), None)
        if source_wins is None and source_losses is None:
            observed_probability = _ev_probability(
                source.get("win_rate") or source.get("hit_rate") or source.get("observed_probability"),
                None,
            )
            sample_size = int(to_float(source.get("trade_count") or source.get("trades") or source.get("sample_size") or source.get("samples"), 0) or 0)
            if observed_probability is not None and sample_size > 0:
                source_wins = observed_probability * sample_size
                source_losses = max(0.0, sample_size - source_wins)
        source_wins = max(0.0, to_float(source_wins, 0.0) or 0.0)
        source_losses = max(0.0, to_float(source_losses, 0.0) or 0.0)
        if source_wins <= 0.0 and source_losses <= 0.0:
            return
        wins += source_wins * source_weight_value
        losses += source_losses * source_weight_value
        source_labels.append(label)

    family_stats = candidate.get("family_profit_memory_stats") if isinstance(candidate.get("family_profit_memory_stats"), dict) else {}
    _add_counts("family_profit_memory", family_stats, 1.0)

    memory_profile = candidate.get("memory_profile") if isinstance(candidate.get("memory_profile"), dict) else {}
    _add_counts(
        "dynamic_memory",
        {
            "win_rate": memory_profile.get("win_rate"),
            "sample_size": (to_float(memory_profile.get("sample_count"), 0) or 0) + (to_float(memory_profile.get("outcome_samples"), 0) or 0),
            "wins": memory_profile.get("wins"),
            "losses": memory_profile.get("losses"),
        },
        0.85,
    )
    outcome_wins, outcome_losses = _outcome_memory_counts(memory_profile.get("execution_outcome_memory"))
    _add_counts("dynamic_outcome_memory", {"wins": outcome_wins, "losses": outcome_losses}, 0.75)

    adaptive_summary = candidate.get("adaptive_confidence_summary") if isinstance(candidate.get("adaptive_confidence_summary"), dict) else {}
    _add_counts("adaptive_confidence_history", adaptive_summary, 0.65)

    confidence_performance = candidate.get("confidence_bucket_performance") if isinstance(candidate.get("confidence_bucket_performance"), dict) else {}
    bucket_stats = confidence_performance.get(signal_bucket) or confidence_performance.get(f"bucket:{signal_bucket}") or {}
    if not isinstance(bucket_stats, dict):
        bucket_stats = confidence_performance if confidence_performance else {}
    _add_counts("confidence_bucket_history", bucket_stats, 0.80)

    ev_tier = str(candidate.get("ev_tier") or candidate.get("prior_ev_tier") or "").strip().upper()
    ev_tier_history = candidate.get("ev_tier_history") if isinstance(candidate.get("ev_tier_history"), dict) else {}
    if ev_tier and isinstance(ev_tier_history.get(ev_tier), dict):
        _add_counts("ev_tier_history", ev_tier_history.get(ev_tier), 0.70)
    elif isinstance(candidate.get("ev_tier_performance"), dict):
        _add_counts("ev_tier_history", candidate.get("ev_tier_performance"), 0.70)

    sample_size = int(round(wins + losses))
    if sample_size <= 0:
        result = {
            "raw_probability": round(raw_probability_value, 6),
            "bayesian_probability": round(raw_probability_value, 6),
            "bayesian_delta": 0.0,
            "alpha": 1.0,
            "beta": 1.0,
            "posterior_mean": 0.5,
            "sample_size": 0,
            "family_key": family_key,
            "signal_bucket": signal_bucket,
            "reason": "no_history_fallback",
        }
        candidate.update(
            {
                "raw_probability": result["raw_probability"],
                "bayesian_probability": result["bayesian_probability"],
                "bayesian_delta": result["bayesian_delta"],
                "bayesian_sample_size": result["sample_size"],
                "bayesian_posterior_mean": result["posterior_mean"],
                "bayesian_reason": result["reason"],
                "bayesian_alpha": result["alpha"],
                "bayesian_beta": result["beta"],
                "bayesian_signal_bucket": result["signal_bucket"],
                "bayesian_family_key": result["family_key"],
            }
        )
        return result

    alpha = 1.0 + wins
    beta = 1.0 + losses
    posterior_mean = alpha / max(alpha + beta, 1.0)
    weight = _bayes_weight(sample_size)
    bayesian_probability = (raw_probability_value * (1.0 - weight)) + (posterior_mean * weight)
    bayesian_probability = max(0.03, min(0.97, bayesian_probability))
    result = {
        "raw_probability": round(raw_probability_value, 6),
        "bayesian_probability": round(bayesian_probability, 6),
        "bayesian_delta": round(bayesian_probability - raw_probability_value, 6),
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        "posterior_mean": round(posterior_mean, 6),
        "sample_size": sample_size,
        "family_key": family_key,
        "signal_bucket": signal_bucket,
        "reason": "beta_bernoulli:" + "+".join(dict.fromkeys(source_labels)),
    }
    candidate.update(
        {
            "raw_probability": result["raw_probability"],
            "bayesian_probability": result["bayesian_probability"],
            "bayesian_delta": result["bayesian_delta"],
            "bayesian_sample_size": result["sample_size"],
            "bayesian_posterior_mean": result["posterior_mean"],
            "bayesian_reason": result["reason"],
            "bayesian_alpha": result["alpha"],
            "bayesian_beta": result["beta"],
            "bayesian_signal_bucket": result["signal_bucket"],
            "bayesian_family_key": result["family_key"],
        }
    )
    return result


def calibrate_candidate_probability(candidate: Dict[str, Any], raw_probability: Any) -> Dict[str, Any]:
    candidate = candidate if isinstance(candidate, dict) else {}
    raw_probability_value = _ev_probability(raw_probability, None)
    if raw_probability_value is None:
        raw_probability_value = _ev_probability(
            _ev_first(
                candidate,
                "raw_probability",
                "estimated_win_probability",
                "probability_estimate",
                "model_confidence",
                "base_confidence",
                "confidence_probability",
                "confidence_score",
                "confidence",
            ),
            0.50,
        )
    raw_probability_value = max(0.03, min(0.97, raw_probability_value if raw_probability_value is not None else 0.50))
    candidate_raw_probability = _ev_probability(candidate.get("raw_probability"), raw_probability_value)
    confidence_bucket = _probability_confidence_bucket(raw_probability_value)
    family_key = str(_ev_first(candidate, "family", "series_ticker") or "").strip().upper() or "UNKNOWN"

    evidence = []

    def _add_evidence(label: str, observed_probability: Any, sample_size: Any, source_weight: float = 1.0):
        observed_value = _ev_probability(observed_probability, None)
        sample_value = int(to_float(sample_size, 0) or 0)
        if observed_value is None or sample_value <= 0:
            return
        source_weight_value = max(0.10, min(1.25, float(source_weight or 1.0)))
        evidence.append(
            {
                "label": label,
                "observed_probability": observed_value,
                "sample_size": sample_value,
                "source_weight": source_weight_value,
            }
        )

    family_stats = candidate.get("family_profit_memory_stats") if isinstance(candidate.get("family_profit_memory_stats"), dict) else {}
    _add_evidence(
        "family_profit_memory",
        family_stats.get("win_rate"),
        family_stats.get("trade_count") or family_stats.get("trades") or family_stats.get("sample_size"),
        1.0,
    )

    memory_profile = candidate.get("memory_profile") if isinstance(candidate.get("memory_profile"), dict) else {}
    _add_evidence(
        "dynamic_memory",
        memory_profile.get("win_rate"),
        (to_float(memory_profile.get("sample_count"), 0) or 0) + (to_float(memory_profile.get("outcome_samples"), 0) or 0),
        0.85,
    )
    outcome_probability = _outcome_memory_probability(memory_profile.get("execution_outcome_memory"))
    outcome_memory = memory_profile.get("execution_outcome_memory") if isinstance(memory_profile.get("execution_outcome_memory"), dict) else {}
    _add_evidence(
        "dynamic_outcome_memory",
        outcome_probability,
        sum(int(to_float(value, 0) or 0) for value in outcome_memory.values()),
        0.75,
    )

    adaptive_summary = candidate.get("adaptive_confidence_summary") if isinstance(candidate.get("adaptive_confidence_summary"), dict) else {}
    _add_evidence(
        "adaptive_confidence_history",
        adaptive_summary.get("win_rate") or adaptive_summary.get("hit_rate") or adaptive_summary.get("observed_probability"),
        adaptive_summary.get("sample_size") or adaptive_summary.get("samples") or adaptive_summary.get("trade_count"),
        0.65,
    )

    confidence_performance = candidate.get("confidence_bucket_performance") if isinstance(candidate.get("confidence_bucket_performance"), dict) else {}
    bucket_stats = confidence_performance.get(confidence_bucket) or confidence_performance.get(f"bucket:{confidence_bucket}") or {}
    if not isinstance(bucket_stats, dict):
        bucket_stats = confidence_performance if confidence_performance else {}
    _add_evidence(
        "confidence_bucket_history",
        bucket_stats.get("win_rate") or bucket_stats.get("hit_rate") or bucket_stats.get("observed_probability"),
        bucket_stats.get("sample_size") or bucket_stats.get("samples") or bucket_stats.get("trade_count"),
        0.80,
    )

    if not evidence:
        result = {
            "raw_probability": round(raw_probability_value, 6),
            "calibrated_probability": round(raw_probability_value, 6),
            "calibration_delta": 0.0,
            "sample_size": 0,
            "confidence_bucket": confidence_bucket,
            "family_key": family_key,
            "reliability_weight": 0.0,
            "reason": "no_history_fallback",
        }
        candidate.update(
            {
                "raw_probability": result["raw_probability"],
                "calibration_input_probability": result["raw_probability"],
                "calibrated_probability": result["calibrated_probability"],
                "calibration_delta": result["calibration_delta"],
                "calibration_sample_size": result["sample_size"],
                "calibration_reason": result["reason"],
                "calibration_confidence_bucket": result["confidence_bucket"],
                "calibration_family_key": result["family_key"],
                "calibration_reliability_weight": result["reliability_weight"],
            }
        )
        if candidate_raw_probability is not None:
            candidate["raw_probability"] = round(candidate_raw_probability, 6)
        return result

    weighted_numerator = 0.0
    weighted_denominator = 0.0
    total_sample_size = 0
    source_labels = []
    for item in evidence:
        sample_size = int(item.get("sample_size", 0) or 0)
        source_weight = float(item.get("source_weight", 1.0) or 1.0)
        weighted_sample = sample_size * source_weight
        weighted_numerator += float(item.get("observed_probability", raw_probability_value)) * weighted_sample
        weighted_denominator += weighted_sample
        total_sample_size += sample_size
        source_labels.append(str(item.get("label") or "history"))

    observed_probability = raw_probability_value if weighted_denominator <= 0.0 else weighted_numerator / weighted_denominator
    reliability_weight = _calibration_reliability_weight(total_sample_size)
    reliability_weight *= min(1.0, weighted_denominator / max(float(total_sample_size), 1.0))
    reliability_weight = max(0.0, min(0.50, reliability_weight))
    blended_probability = (raw_probability_value * (1.0 - reliability_weight)) + (observed_probability * reliability_weight)
    max_delta = _calibration_max_delta(total_sample_size)
    calibration_delta = max(-max_delta, min(max_delta, blended_probability - raw_probability_value))
    calibrated_probability = max(0.03, min(0.97, raw_probability_value + calibration_delta))
    result = {
        "raw_probability": round(raw_probability_value, 6),
        "calibrated_probability": round(calibrated_probability, 6),
        "calibration_delta": round(calibrated_probability - raw_probability_value, 6),
        "sample_size": int(total_sample_size),
        "confidence_bucket": confidence_bucket,
        "family_key": family_key,
        "reliability_weight": round(reliability_weight, 6),
        "reason": "history_blend:" + "+".join(dict.fromkeys(source_labels)),
    }
    candidate.update(
        {
            "raw_probability": result["raw_probability"],
            "calibration_input_probability": result["raw_probability"],
            "calibrated_probability": result["calibrated_probability"],
            "calibration_delta": result["calibration_delta"],
            "calibration_sample_size": result["sample_size"],
            "calibration_reason": result["reason"],
            "calibration_confidence_bucket": result["confidence_bucket"],
            "calibration_family_key": result["family_key"],
            "calibration_reliability_weight": result["reliability_weight"],
            "calibration_observed_probability": round(observed_probability, 6),
        }
    )
    if candidate_raw_probability is not None:
        candidate["raw_probability"] = round(candidate_raw_probability, 6)
    return result


def compute_candidate_ev(candidate: Dict[str, Any]) -> Dict[str, Any]:
    candidate = candidate if isinstance(candidate, dict) else {}
    side = str(_ev_first(candidate, "selected_side", "side") or "yes").strip().lower()
    if side not in {"yes", "no"}:
        side = "yes"
    selected_price = _ev_price(
        _ev_first(candidate, "selected_price", "selected_price_dollars", "price"),
        None,
    )
    if selected_price is None:
        if side == "no":
            selected_price = _ev_price(_ev_first(candidate, "no_ask", "no_price", "no_price_dollars"), None)
        else:
            selected_price = _ev_price(_ev_first(candidate, "yes_ask", "yes_price", "yes_price_dollars"), None)
    if selected_price is None:
        selected_price = _ev_price(_ev_first(candidate, "selected_price_cents", "price_cents"), 0.50)

    market_implied_probability = _ev_probability(
        _ev_first(candidate, "market_implied_probability", "implied_probability"),
        selected_price,
    )
    base_probability = _ev_probability(
        _ev_first(
            candidate,
            "raw_probability",
            "estimated_win_probability",
            "probability_estimate",
            "model_confidence",
            "base_confidence",
            "probability_confidence",
            "confidence_probability",
            "confidence_score",
            "confidence",
            "fair_probability",
            "no_vig_probability",
        ),
        market_implied_probability,
    )

    family_adjustment = max(
        -0.035,
        min(0.035, (to_float(candidate.get("family_profit_memory_adjustment"), 0.0) or 0.0) * 0.018),
    )
    memory_profile = candidate.get("memory_profile") if isinstance(candidate.get("memory_profile"), dict) else {}
    memory_score = to_float(memory_profile.get("memory_score"), None)
    sample_quality = str(memory_profile.get("sample_size_quality") or "none").strip().lower()
    sample_scale = {"none": 0.0, "tiny": 0.20, "small": 0.45, "medium": 0.75, "strong": 1.0}.get(sample_quality, 0.50)
    dynamic_adjustment = (to_float(candidate.get("dynamic_memory_adjustment"), 0.0) or 0.0) * 0.018
    if memory_score is not None:
        dynamic_adjustment += (max(0.0, min(1.0, memory_score)) - 0.5) * 0.10 * sample_scale
    adaptive_summary = candidate.get("adaptive_confidence_summary") if isinstance(candidate.get("adaptive_confidence_summary"), dict) else {}
    dynamic_adjustment += (to_float(adaptive_summary.get("adaptive_confidence_modifier"), to_float(adaptive_summary.get("avg_confidence_modifier"), 0.0)) or 0.0) * 0.75
    dynamic_adjustment = max(-0.045, min(0.045, dynamic_adjustment))

    imbalance_context = candidate.get("orderbook_imbalance") if isinstance(candidate.get("orderbook_imbalance"), dict) else {}
    orderbook_adjustment = (to_float(candidate.get("orderbook_imbalance_adjustment"), 0.0) or 0.0) * 0.018
    imbalance = to_float(imbalance_context.get("imbalance"), None)
    imbalance_side = str(imbalance_context.get("side") or side).strip().lower()
    if imbalance is not None:
        side_support = imbalance if imbalance_side == "yes" else -imbalance if imbalance_side == "no" else 0.0
        orderbook_adjustment += max(-0.035, min(0.035, side_support * 0.035))
    orderbook_adjustment = max(-0.040, min(0.040, orderbook_adjustment))

    structural_context = candidate.get("structural_edge_context") if isinstance(candidate.get("structural_edge_context"), dict) else {}
    structural_score = to_float(
        _pick(
            candidate.get("STRUCTURAL_EDGE_SCORE"),
            candidate.get("structural_edge_score"),
            structural_context.get("STRUCTURAL_EDGE_SCORE"),
            structural_context.get("structural_edge_score"),
        ),
        0.0,
    ) or 0.0
    structural_adjustment = to_float(
        _pick(
            candidate.get("structural_edge_probability_adjustment"),
            structural_context.get("probability_adjustment"),
        ),
        None,
    )
    if structural_adjustment is None:
        if structural_score >= 50.0:
            structural_adjustment = 0.012 + ((min(100.0, structural_score) - 50.0) / 50.0) * 0.040
        elif structural_score < 28.0:
            structural_adjustment = max(-0.012, (structural_score - 28.0) / 28.0 * 0.012)
        else:
            structural_adjustment = 0.0
    structural_adjustment = max(-0.030, min(0.060, structural_adjustment))
    family_profile = candidate.get("family_profile") if isinstance(candidate.get("family_profile"), dict) else {}
    live_state_context = candidate.get("live_state_context") if isinstance(candidate.get("live_state_context"), dict) else {}
    live_state_score = to_float(live_state_context.get("live_state_score"), None)
    family_profile_bias = max(
        -0.020,
        min(0.025, (to_float(family_profile.get("structural_edge_weight"), 1.0) - 1.0) * 0.035),
    )
    if live_state_score is not None and structural_score >= 45.0:
        structural_adjustment += max(-0.012, min(0.018, (live_state_score - 0.50) * 0.035))
    structural_adjustment += family_profile_bias
    structural_adjustment = max(-0.035, min(0.065, structural_adjustment))

    raw_estimated_probability = max(
        0.03,
        min(
            0.97,
            (base_probability if base_probability is not None else market_implied_probability or 0.50)
            + family_adjustment
            + dynamic_adjustment
            + orderbook_adjustment
            + structural_adjustment,
        ),
    )
    bayes_result = apply_bayesian_candidate_adjustment(candidate, raw_estimated_probability)
    bayesian_probability = max(
        0.03,
        min(0.97, to_float(bayes_result.get("bayesian_probability"), raw_estimated_probability) or raw_estimated_probability),
    )
    calibration_result = calibrate_candidate_probability(candidate, bayesian_probability)
    estimated_win_probability = max(
        0.03,
        min(0.97, to_float(calibration_result.get("calibrated_probability"), bayesian_probability) or bayesian_probability),
    )
    market_implied_probability = max(0.03, min(0.97, market_implied_probability if market_implied_probability is not None else selected_price or 0.50))
    cost = max(0.01, min(0.99, selected_price if selected_price is not None else market_implied_probability))
    payout = max(0.01, 1.0 - cost)
    expected_value = (estimated_win_probability * payout) - ((1.0 - estimated_win_probability) * cost)
    expected_roi = expected_value / cost if cost > 0.0 else 0.0
    ev_edge = estimated_win_probability - market_implied_probability
    structural_elite = bool(structural_score >= 78.0 and ev_edge > 0.0)
    structural_strong = bool(structural_score >= 60.0 and (ev_edge > 0.0 or expected_value > 0.0))
    if ev_edge >= 0.08 or expected_roi >= 0.18 or (structural_elite and expected_roi >= 0.08):
        ev_tier = "S"
        sizing_multiplier = 1.40
        action = "prioritize_size_up"
    elif ev_edge >= 0.035 or expected_roi >= 0.08 or structural_strong:
        ev_tier = "A"
        sizing_multiplier = 1.00
        action = "prioritize_normal"
    elif ev_edge >= 0.005 or expected_value > 0.0:
        ev_tier = "B"
        sizing_multiplier = 0.65
        action = "trade_small"
    elif ev_edge >= -0.020 or expected_roi >= -0.05:
        ev_tier = "C"
        sizing_multiplier = 0.35
        action = "explore_micro"
    else:
        ev_tier = "D"
        sizing_multiplier = 0.0
        action = "deprioritize"

    structural_size_multiplier = to_float(
        _pick(candidate.get("structural_edge_size_multiplier"), structural_context.get("size_multiplier")),
        1.0,
    ) or 1.0
    structural_size_multiplier = max(0.55, min(1.45, structural_size_multiplier))
    family_size_bias = max(0.85, min(1.22, to_float(family_profile.get("sizing_bias"), 1.0) or 1.0))
    conviction_size_multiplier = max(0.20, min(1.80, structural_size_multiplier * family_size_bias))
    if ev_tier in {"S", "A"}:
        sizing_multiplier = min(1.80, max(sizing_multiplier, sizing_multiplier * conviction_size_multiplier))
    elif ev_tier in {"B", "C"}:
        sizing_multiplier = max(0.10, min(0.95, sizing_multiplier * conviction_size_multiplier))
    else:
        sizing_multiplier = max(0.0, min(0.25, sizing_multiplier * min(1.0, conviction_size_multiplier)))
    if structural_score < 24.0 and ev_tier in {"B", "C"}:
        action = "probe_structural_weak"
    elif structural_score >= 78.0 and ev_tier in {"S", "A"}:
        action = "conviction_structural_size_up"

    candidate.update(
        {
            "market_implied_probability": round(market_implied_probability, 6),
            "estimated_win_probability": round(estimated_win_probability, 6),
            "expected_value": round(expected_value, 6),
            "expected_roi": round(expected_roi, 6),
            "ev_edge": round(ev_edge, 6),
            "ev_score": round((ev_edge * 100.0) + (expected_roi * 2.0), 4),
            "ev_selected_price": round(cost, 6),
            "ev_tier": ev_tier,
            "ev_reason": (
                f"base={round(base_probability if base_probability is not None else 0.5, 4)} "
                f"family={round(family_adjustment, 4)} dynamic={round(dynamic_adjustment, 4)} "
                f"orderbook={round(orderbook_adjustment, 4)} structural={round(structural_adjustment, 4)} "
                f"bayes={bayes_result.get('bayesian_delta')} "
                f"calibration={calibration_result.get('calibration_delta')}"
            ),
            "ev_sizing_multiplier": round(sizing_multiplier, 4),
            "ev_action": action,
            "structural_edge_score": round(structural_score, 4),
            "STRUCTURAL_EDGE_SCORE": round(structural_score, 4),
            "structural_edge_probability_adjustment": round(structural_adjustment, 6),
            "structural_edge_size_multiplier": round(structural_size_multiplier, 4),
            "conviction_size_multiplier": round(conviction_size_multiplier, 4),
            "family_profile": family_profile,
            "live_state_context": live_state_context,
        }
    )
    return candidate


def _kelly_env_float(name: str, default: float, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    numeric = to_float(os.environ.get(name), default)
    if numeric is None:
        numeric = default
    numeric = float(numeric)
    if minimum is not None:
        numeric = max(float(minimum), numeric)
    if maximum is not None:
        numeric = min(float(maximum), numeric)
    return numeric


def _kelly_env_int(name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    numeric = to_float(os.environ.get(name), default)
    if numeric is None:
        numeric = default
    value = int(float(numeric))
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def calculate_fractional_kelly_size(candidate: Dict[str, Any], bankroll: Any, base_size: Any, max_size: Any) -> Dict[str, Any]:
    candidate = candidate if isinstance(candidate, dict) else {}
    base_contracts = max(0, int(to_float(base_size, 0) or 0))
    hard_max_contracts = max(base_contracts, int(to_float(max_size, base_contracts or 1) or (base_contracts or 1)))
    fallback_size = max(0, min(hard_max_contracts, base_contracts))

    kelly_fraction_config = to_float(candidate.get("kelly_fraction_config"), None)
    if kelly_fraction_config is None:
        kelly_fraction_config = _kelly_env_float("AION_KELLY_FRACTION", 0.25, 0.0, 1.0)
    else:
        kelly_fraction_config = max(0.0, min(1.0, float(kelly_fraction_config)))

    max_multiplier = to_float(candidate.get("kelly_max_multiplier"), None)
    if max_multiplier is None:
        max_multiplier = _kelly_env_float("AION_KELLY_MAX_MULTIPLIER", 1.75, 1.0, 10.0)
    else:
        max_multiplier = max(1.0, min(10.0, float(max_multiplier)))

    min_exploration_size = to_float(candidate.get("kelly_min_exploration_size"), None)
    if min_exploration_size is None:
        min_exploration_size = _kelly_env_int("AION_KELLY_MIN_EXPLORATION_SIZE", 1, 0, hard_max_contracts)
    else:
        min_exploration_size = max(0, min(hard_max_contracts, int(float(min_exploration_size))))

    probability = _ev_probability(
        _ev_first(
            candidate,
            "calibrated_probability",
            "estimated_win_probability",
            "probability_estimate",
            "model_confidence",
            "base_confidence",
            "confidence_probability",
            "confidence_score",
            "confidence",
        ),
        None,
    )
    side = str(_ev_first(candidate, "selected_side", "side") or "yes").strip().lower()
    price = _ev_price(
        _ev_first(candidate, "ev_selected_price", "selected_price", "selected_price_dollars", "price", "cost"),
        None,
    )
    if price is None:
        if side == "no":
            price = _ev_price(_ev_first(candidate, "no_ask", "no_price", "no_price_dollars"), None)
        else:
            price = _ev_price(_ev_first(candidate, "yes_ask", "yes_price", "yes_price_dollars"), None)
    if price is None:
        price = _ev_price(_ev_first(candidate, "selected_price_cents", "price_cents"), None)

    raw_payout = to_float(_ev_first(candidate, "payout", "profit_payout", "profit_per_contract", "ev_payout"), None)
    if raw_payout is None or raw_payout >= 1.0:
        payout = None if price is None else max(0.01, 1.0 - price)
    else:
        payout = _ev_price(raw_payout, None)
    bankroll_value = to_float(bankroll, None)

    result = {
        "estimated_win_probability": None if probability is None else round(probability, 6),
        "price": None if price is None else round(price, 6),
        "payout": None if payout is None else round(payout, 6),
        "raw_kelly_fraction": None,
        "fractional_kelly_fraction": None,
        "kelly_multiplier": 1.0,
        "final_contract_size": int(fallback_size),
        "reason": "fallback_existing_size",
    }

    if base_contracts <= 0 or hard_max_contracts <= 0:
        result["reason"] = "fallback_invalid_size_bounds"
        candidate.update(
            {
                "kelly_fraction": result["fractional_kelly_fraction"],
                "kelly_multiplier": result["kelly_multiplier"],
                "kelly_contract_size": result["final_contract_size"],
                "kelly_reason": result["reason"],
            }
        )
        return result

    if probability is None or price is None or payout is None or price <= 0.0 or payout <= 0.0:
        missing = []
        if probability is None:
            missing.append("probability")
        if price is None or price <= 0.0:
            missing.append("price")
        if payout is None or payout <= 0.0:
            missing.append("payout")
        result["reason"] = "fallback_missing_" + "_".join(missing or ["kelly_input"])
        candidate.update(
            {
                "kelly_fraction": result["fractional_kelly_fraction"],
                "kelly_multiplier": result["kelly_multiplier"],
                "kelly_contract_size": result["final_contract_size"],
                "kelly_reason": result["reason"],
            }
        )
        return result

    b = payout / price
    if b <= 0.0:
        result["reason"] = "fallback_invalid_profit_per_dollar"
        candidate.update(
            {
                "kelly_fraction": result["fractional_kelly_fraction"],
                "kelly_multiplier": result["kelly_multiplier"],
                "kelly_contract_size": result["final_contract_size"],
                "kelly_reason": result["reason"],
            }
        )
        return result

    q_probability = 1.0 - probability
    raw_kelly_fraction = ((b * probability) - q_probability) / b
    positive_kelly_fraction = max(0.0, raw_kelly_fraction)
    fractional_kelly_fraction = positive_kelly_fraction * kelly_fraction_config
    result["raw_kelly_fraction"] = round(raw_kelly_fraction, 6)
    result["fractional_kelly_fraction"] = round(fractional_kelly_fraction, 6)

    ev_tier = str(_ev_first(candidate, "ev_tier") or "C").strip().upper() or "C"
    selected_for_dispatch = bool(candidate.get("selected_for_dispatch") or candidate.get("kelly_selected_for_dispatch"))
    if raw_kelly_fraction <= 0.0:
        if ev_tier in {"B", "C"}:
            final_size = max(min_exploration_size, 1)
            reason = "exploration_micro_nonpositive_kelly"
        elif selected_for_dispatch and min_exploration_size > 0:
            final_size = min_exploration_size
            reason = "selected_micro_nonpositive_kelly"
        else:
            final_size = 0
            reason = "zero_nonpositive_kelly"
        final_size = max(0, min(hard_max_contracts, int(final_size)))
        result["kelly_multiplier"] = round((final_size / base_contracts) if base_contracts > 0 else 0.0, 4)
        result["final_contract_size"] = int(final_size)
        result["reason"] = reason
        candidate.update(
            {
                "kelly_fraction": result["fractional_kelly_fraction"],
                "kelly_multiplier": result["kelly_multiplier"],
                "kelly_contract_size": result["final_contract_size"],
                "kelly_reason": result["reason"],
            }
        )
        return result

    if bankroll_value is None or bankroll_value <= 0.0:
        result["reason"] = "fallback_missing_bankroll"
        candidate.update(
            {
                "kelly_fraction": result["fractional_kelly_fraction"],
                "kelly_multiplier": result["kelly_multiplier"],
                "kelly_contract_size": result["final_contract_size"],
                "kelly_reason": result["reason"],
            }
        )
        return result

    kelly_contracts = int((bankroll_value * fractional_kelly_fraction) / price)
    raw_multiplier = (kelly_contracts / base_contracts) if base_contracts > 0 else 0.0
    if ev_tier == "S":
        tier_cap = max_multiplier
        bounded_multiplier = max(1.0, min(tier_cap, raw_multiplier))
        min_size = max(1, min_exploration_size)
    elif ev_tier == "A":
        tier_cap = max_multiplier
        bounded_multiplier = max(0.50, min(tier_cap, raw_multiplier))
        min_size = max(1, min_exploration_size)
    elif ev_tier == "B":
        tier_cap = min(max_multiplier, 0.75)
        bounded_multiplier = max(0.10, min(tier_cap, raw_multiplier))
        min_size = max(1, min_exploration_size)
    elif ev_tier == "C":
        tier_cap = min(max_multiplier, 0.35)
        bounded_multiplier = max(0.05, min(tier_cap, raw_multiplier))
        min_size = max(1, min_exploration_size)
    else:
        tier_cap = min(max_multiplier, 0.20)
        if not selected_for_dispatch:
            bounded_multiplier = 0.0
            min_size = 0
        else:
            bounded_multiplier = max(0.0, min(tier_cap, raw_multiplier))
            min_size = min_exploration_size

    requested_size = int(base_contracts * bounded_multiplier)
    if bounded_multiplier > 0.0 and requested_size <= 0:
        requested_size = min_size
    if ev_tier in {"B", "C"} and requested_size <= 0:
        requested_size = min_size
    final_size = max(0, min(hard_max_contracts, requested_size))
    if final_size <= 0 and selected_for_dispatch and min_exploration_size > 0 and ev_tier != "D":
        final_size = min(hard_max_contracts, min_exploration_size)

    result["kelly_multiplier"] = round((final_size / base_contracts) if base_contracts > 0 else 0.0, 4)
    result["final_contract_size"] = int(final_size)
    result["reason"] = f"fractional_kelly_{ev_tier.lower()}"
    candidate.update(
        {
            "kelly_fraction": result["fractional_kelly_fraction"],
            "kelly_multiplier": result["kelly_multiplier"],
            "kelly_contract_size": result["final_contract_size"],
            "kelly_reason": result["reason"],
        }
    )
    return result


def compute_profitability_signal(
    self,
    *,
    edge_after_cost=None,
    edge_before_cost=None,
    edge_source=None,
    spread=None,
    spread_classification=None,
    quote_ready=False,
    payload_ready=False,
    orderbook_usable=False,
    liquidity=None,
    liquidity_depth=None,
    fill_quality_score=None,
    family_weight=None,
    market_activity_score=None,
    selected_side_executable=False,
    executable_side_present=False,
    selected_price_in_bounds=False,
    orderbook_age_seconds=None,
):
    edge_after_cost_value = to_float(edge_after_cost, None)
    edge_before_cost_value = to_float(edge_before_cost, None)
    edge_source_value = str(edge_source or "").strip().lower() or "unknown"
    spread_value = to_float(spread, None)
    spread_classification = str(spread_classification or "unknown").strip().upper() or "UNKNOWN"
    liquidity_value = to_float(liquidity, None)
    liquidity_depth_value = to_float(liquidity_depth, None)
    fill_quality_value = to_float(fill_quality_score, None)
    family_weight_value = to_float(family_weight, None)
    market_activity_value = to_float(market_activity_score, None)
    orderbook_age_value = to_float(orderbook_age_seconds, None)

    profitability_score = 50.0
    edge_quality = 0.5
    edge_source_quality = 0.5
    reasons: List[str] = []

    if edge_after_cost_value is None:
        profitability_score -= 7.0
        edge_quality = 0.28
        reasons.append("edge_missing")
    elif edge_after_cost_value > 0.0:
        profitability_score += min(30.0, edge_after_cost_value * 1200.0)
        edge_quality = _clamp(0.58 + min(0.36, edge_after_cost_value * 25.0))
        reasons.append("positive_edge")
    elif edge_after_cost_value == 0.0:
        profitability_score -= 12.0
        edge_quality = 0.35
        reasons.append("flat_edge")
    else:
        profitability_score -= min(40.0, abs(edge_after_cost_value) * 1500.0 + 10.0)
        edge_quality = _clamp(0.30 - min(0.18, abs(edge_after_cost_value) * 20.0))
        reasons.append("negative_edge")

    if edge_before_cost_value is not None:
        if edge_before_cost_value > 0.0:
            profitability_score += min(6.0, edge_before_cost_value * 400.0)
            reasons.append("positive_raw_edge")
        elif edge_before_cost_value < 0.0:
            profitability_score -= min(8.0, abs(edge_before_cost_value) * 500.0)
            reasons.append("negative_raw_edge")

    if edge_source_value == "fallback_zero":
        profitability_score -= 22.0
        edge_source_quality = 0.10
        reasons.append("fallback_zero")
    elif edge_source_value in {
        "context.final_edge",
        "final_edge",
        "live",
        "computed",
        "market_edge",
        "selected_price_baseline",
        "signal_adjusted_probability",
        "quote_diag.edge_after_cost",
        "explicit_probability_model",
        "market_implied_probability",
        "recent_momentum_edge",
        "spread_adjusted_executable_edge",
    }:
        profitability_score += 6.0
        edge_source_quality = 0.82
        reasons.append("direct_edge_source")
    elif edge_source_value not in {"unknown", ""}:
        profitability_score += 2.0
        edge_source_quality = 0.62
        reasons.append("alternate_edge_source")
    else:
        profitability_score -= 4.0
        edge_source_quality = 0.35
        reasons.append("missing_edge_source")

    if spread_classification in {"SAFE", "AGGRESSIVE_BUT_TRADEABLE"}:
        profitability_score += 8.0
        reasons.append("safe_spread")
    elif spread_classification == "CAUTION_WAIT":
        profitability_score += 3.0
        reasons.append("caution_spread")
    elif spread_classification in {"UNSAFE", "UNSAFE_SPREAD"}:
        profitability_score -= 14.0
        reasons.append("unsafe_spread")

    if spread_value is not None:
        if spread_value <= 0.02:
            profitability_score += 7.0
            reasons.append("tight_spread")
        elif spread_value <= 0.05:
            profitability_score += 3.5
            reasons.append("usable_spread")
        elif spread_value >= 0.15:
            profitability_score -= 6.0
            reasons.append("wide_spread")

    if quote_ready:
        profitability_score += 4.0
        reasons.append("quote_ready")
    else:
        profitability_score -= 4.0
        reasons.append("quote_not_ready")

    if payload_ready:
        profitability_score += 4.0
        reasons.append("payload_ready")
    else:
        profitability_score -= 4.0
        reasons.append("payload_not_ready")

    if orderbook_usable:
        profitability_score += 6.0
        reasons.append("orderbook_usable")
    else:
        profitability_score -= 8.0
        reasons.append("orderbook_not_usable")

    if selected_side_executable:
        profitability_score += 6.0
        reasons.append("selected_side_executable")
    else:
        profitability_score -= 6.0
        reasons.append("selected_side_not_executable")

    if executable_side_present:
        profitability_score += 4.0
        reasons.append("executable_side_present")
    else:
        profitability_score -= 10.0
        reasons.append("no_executable_side")

    if selected_price_in_bounds:
        profitability_score += 4.0
        reasons.append("selected_price_in_bounds")
    else:
        profitability_score -= 10.0
        reasons.append("selected_price_out_of_range")

    if liquidity_value is not None or liquidity_depth_value is not None:
        best_liquidity = max(
            to_float(liquidity_value, 0.0) or 0.0,
            to_float(liquidity_depth_value, 0.0) or 0.0,
        )
        if best_liquidity <= 0.0:
            profitability_score -= 6.0
            reasons.append("no_liquidity")
        elif best_liquidity >= 10.0:
            profitability_score += 7.0
            reasons.append("deep_liquidity")
        elif best_liquidity >= 5.0:
            profitability_score += 4.0
            reasons.append("usable_liquidity")
        else:
            profitability_score += 1.5
            reasons.append("thin_liquidity")

    if fill_quality_value is not None:
        fill_quality_component = (_clamp(fill_quality_value) - 0.5) * 14.0
        profitability_score += fill_quality_component
        reasons.append("fill_quality")
    else:
        fill_quality_component = 0.0

    if family_weight_value is not None:
        family_weight_component = max(-8.0, min(8.0, family_weight_value)) * 0.45
        profitability_score += family_weight_component
        reasons.append("family_weight")
    else:
        family_weight_component = 0.0

    if market_activity_value is not None:
        market_activity_component = max(-6.0, min(12.0, market_activity_value)) * 0.35
        profitability_score += market_activity_component
        reasons.append("market_activity")
    else:
        market_activity_component = 0.0

    if orderbook_age_value is not None:
        if orderbook_age_value <= 30.0:
            profitability_score += 4.0
            reasons.append("fresh_orderbook")
        elif orderbook_age_value <= 120.0:
            profitability_score += 2.0
            reasons.append("recent_orderbook")
        elif orderbook_age_value > 900.0:
            profitability_score -= 5.0
            reasons.append("stale_orderbook")

    profitability_score = round(max(0.0, min(100.0, profitability_score)), 4)
    if profitability_score >= 75.0:
        edge_quality_bucket = "strong"
    elif profitability_score >= 60.0:
        edge_quality_bucket = "positive"
    elif profitability_score >= 45.0:
        edge_quality_bucket = "mixed"
    elif profitability_score >= 30.0:
        edge_quality_bucket = "weak"
    else:
        edge_quality_bucket = "poor"

    if edge_after_cost_value is None:
        reason = "edge_missing"
    elif edge_after_cost_value > 0.0:
        reason = "positive_edge"
    elif edge_after_cost_value == 0.0:
        reason = "flat_or_fallback_edge"
    else:
        reason = "negative_edge"

    return {
        "profitability_score": profitability_score,
        "edge_quality": round(_clamp(edge_quality), 4),
        "edge_source_quality": round(_clamp(edge_source_quality), 4),
        "edge_quality_bucket": edge_quality_bucket,
        "reason": reason,
        "reason_tags": reasons[:10],
        "fill_quality_component": round(fill_quality_component, 4),
        "family_weight_component": round(family_weight_component, 4),
        "market_activity_component": round(market_activity_component, 4),
    }


def rank_final_candidates(candidates):
    tier_rank = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}

    def _candidate_rank_key(item):
        if isinstance(item, dict):
            ev_tier_rank = tier_rank.get(str(item.get("ev_tier") or "").strip().upper(), 1)
            ev_score = to_float(item.get("ev_score"), 0.0)
            profitability_score = to_float(item.get("profitability_score"), to_float(item.get("quality_score"), to_float(item.get("candidate_score"), 0.0)))
            quality_score = to_float(item.get("quality_score"), to_float(item.get("candidate_score"), 0.0))
            candidate_score = to_float(item.get("candidate_score"), 0.0)
            adjusted_score = to_float(item.get("adjusted_score"), candidate_score)
            spread = to_float(item.get("spread"), 0.0)
            liquidity = to_float(item.get("liquidity"), 0.0)
            volume = to_float(item.get("volume"), 0.0)
            return (
                ev_tier_rank,
                ev_score,
                profitability_score,
                quality_score,
                candidate_score,
                adjusted_score,
                -spread,
                liquidity,
                volume,
            )
        if isinstance(item, (list, tuple)) and item:
            q = item[1] if len(item) > 1 else None
            ev_tier_rank = tier_rank.get(str(getattr(q, "ev_tier", "") or "").strip().upper(), 1)
            ev_score = to_float(getattr(q, "ev_score", 0.0), 0.0)
            candidate_score = to_float(item[0], 0.0)
            spread = to_float(item[2], 0.0) if len(item) > 2 else 0.0
            liquidity = to_float(getattr(q, "liquidity", 0.0), 0.0) if q is not None else 0.0
            volume = to_float(getattr(q, "volume", 0.0), 0.0) if q is not None else 0.0
            return (
                ev_tier_rank,
                ev_score,
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
    controlled_loosening_enabled = bool(getattr(self, "AION_CONTROLLED_LOOSENING_ENABLED", True) and getattr(self, "AION_EXPECTANCY_SCORE_ENABLED", True))
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
    edge_after_cost = self.to_float(
        _pick(
            expectancy_summary.get("edge_after_cost"),
            expectancy_summary.get("final_edge"),
            expectancy_summary.get("sports_edge_after_cost"),
            tradeability.get("edge_after_cost"),
            tradeability.get("final_edge"),
            tradeability.get("sports_edge_after_cost"),
            market.get("edge_after_cost"),
            market.get("final_edge"),
        ),
        None,
    )
    edge_before_cost = self.to_float(
        _pick(
            expectancy_summary.get("edge_before_cost"),
            expectancy_summary.get("sports_edge_before_cost"),
            tradeability.get("edge_before_cost"),
            market.get("edge_before_cost"),
        ),
        None,
    )
    probability_estimate = self.to_float(
        _pick(
            expectancy_summary.get("probability_estimate"),
            tradeability.get("probability_estimate"),
            market.get("probability_estimate"),
        ),
        None,
    )
    implied_probability = self.to_float(
        _pick(
            expectancy_summary.get("implied_probability"),
            tradeability.get("implied_probability"),
            market.get("implied_probability"),
        ),
        None,
    )
    spread_value = self.to_float(_pick(
        expectancy_summary.get("spread"),
        tradeability.get("spread"),
        market.get("spread"),
    ), None)
    liquidity_value = self.to_float(_pick(
        expectancy_summary.get("liquidity"),
        tradeability.get("liquidity"),
        market.get("liquidity"),
        getattr(q, "liquidity", None),
    ), None)
    liquidity_depth_value = self.to_float(_pick(
        expectancy_summary.get("liquidity_depth"),
        tradeability.get("liquidity_depth"),
        market.get("liquidity_depth"),
        getattr(q, "liquidity_depth", None),
    ), None)
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

    profitability_signal = compute_profitability_signal(
        self,
        edge_after_cost=edge_after_cost,
        edge_before_cost=edge_before_cost,
        edge_source=_pick(
            expectancy_summary.get("edge_source"),
            tradeability.get("edge_source"),
            market.get("edge_source"),
        ),
        spread=spread_value,
        spread_classification=spread_classification,
        quote_ready=quote_ready,
        payload_ready=payload_ready,
        orderbook_usable=orderbook_usable,
        liquidity=liquidity_value,
        liquidity_depth=liquidity_depth_value,
        fill_quality_score=fill_quality_score,
        family_weight=family_weight,
        market_activity_score=market_activity_score,
        selected_side_executable=selected_side_executable,
        executable_side_present=executable_side_present,
        selected_price_in_bounds=selected_price_in_bounds,
        orderbook_age_seconds=orderbook_age_seconds,
    )
    profitability_score = profitability_signal["profitability_score"]
    profitability_adjustment = max(-12.0, min(12.0, (profitability_score - 50.0) / 4.0))
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

    quality_score += profitability_adjustment
    reasons.append("profitability_signal")

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
        "spread": round(spread_value, 4) if spread_value is not None else None,
        "spread_cents": int(round(spread_value * 100.0)) if spread_value is not None else None,
        "liquidity": round(liquidity_value, 4) if liquidity_value is not None else None,
        "liquidity_depth": round(liquidity_depth_value, 4) if liquidity_depth_value is not None else None,
        "recent_activity": bool(recent_activity),
        "market_activity_score": round(market_activity_score, 4),
        "family_weight": round(family_weight_effect, 4),
        "fill_quality_score": round(fill_quality_score, 4) if fill_quality_score is not None else None,
        "fill_quality_effect": round(fill_quality_effect, 4),
        "edge_after_cost": round(edge_after_cost, 4) if edge_after_cost is not None else None,
        "edge_before_cost": round(edge_before_cost, 4) if edge_before_cost is not None else None,
        "edge_source": str(_pick(
            expectancy_summary.get("edge_source"),
            tradeability.get("edge_source"),
            market.get("edge_source"),
        ) or "unknown").strip().lower() or "unknown",
        "probability_estimate": round(probability_estimate, 4) if probability_estimate is not None else None,
        "implied_probability": round(implied_probability, 4) if implied_probability is not None else None,
        "probability_signal_reason": str(_pick(
            expectancy_summary.get("probability_signal_reason"),
            tradeability.get("probability_signal_reason"),
            market.get("probability_signal_reason"),
            "unknown",
        )).strip().lower() or "unknown",
        "profitability_score": profitability_score,
        "profitability_adjustment": round(profitability_adjustment, 4),
        "edge_quality": profitability_signal.get("edge_quality"),
        "edge_source_quality": profitability_signal.get("edge_source_quality"),
        "edge_quality_bucket": profitability_signal.get("edge_quality_bucket"),
        "profitability_reason": profitability_signal.get("reason"),
        "profitability_reason_tags": profitability_signal.get("reason_tags"),
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
        "selected_profitability_score": selected_quality_summary.get("selected_profitability_score") or selected_quality_snapshot.get("profitability_score"),
        "selected_edge_quality": selected_quality_summary.get("selected_edge_quality") or selected_quality_snapshot.get("edge_quality"),
        "selected_edge_source_quality": selected_quality_summary.get("selected_edge_source_quality") or selected_quality_snapshot.get("edge_source_quality"),
        "selected_profitability_reason": selected_quality_summary.get("selected_profitability_reason") or selected_quality_snapshot.get("profitability_reason"),
        "quality_ranking_changed": bool(selected_quality_summary.get("quality_ranking_changed")),
    }


__all__ = [
    "build_candidate_quality_summary",
    "build_expectancy_score",
    "apply_bayesian_candidate_adjustment",
    "calibrate_candidate_probability",
    "calculate_fractional_kelly_size",
    "compute_candidate_ev",
    "compute_candidate_quality",
    "compute_profitability_signal",
    "compute_probability_estimate_signal",
    "compute_family_weight",
    "compute_fill_quality_score",
    "rank_final_candidates",
    "to_float",
]

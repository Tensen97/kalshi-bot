from __future__ import annotations

import builtins
import json
from math import exp

from risk.helpers import bounded_delta, clamp, safe_float, safe_ratio


SPORTS_PREFIXES = ("KXNBA", "KXMLB", "KXNFL", "KXNHL", "KXNCAAB", "KXNCAAF")
SPORTS_FAMILIES = {
    "KXNBA",
    "KXNBA1H",
    "KXNBASPREAD",
    "KXNBA1HSPREAD",
    "KXNBA1HTOTAL",
    "KXNBATOTAL",
    "KXMLB",
    "KXMLBSPREAD",
    "KXMLBTOTAL",
    "KXMLBHRR",
    "KXNFL",
    "KXNFLSPREAD",
    "KXNFLTOTAL",
}
STRONG_SPORTS_FAMILY_PREFIXES = ("KXNBA", "KXMLB", "KXNFL")


def _sports_family_positive_bias(
    family,
    *,
    ticker=None,
    market_lifecycle_state=None,
    spread_bucket=None,
    calibrated_probability=None,
    no_vig_probability=None,
    raw_edge=None,
):
    family = str(family or "").strip().upper()
    ticker = str(ticker or "").strip().upper()
    market_lifecycle_state = str(market_lifecycle_state or "").strip().lower()
    spread_bucket = str(spread_bucket or "").strip().lower()
    calibrated_probability = safe_float(calibrated_probability, 0.5)
    no_vig_probability = safe_float(no_vig_probability, 0.5)
    raw_edge = safe_float(raw_edge, 0.0)
    if not is_sports_family(family, ticker=ticker):
        return 0.0, None
    if market_lifecycle_state not in {"pregame_far", "pregame_near", "live_early"}:
        return 0.0, None
    bias = 0.0
    reasons = []
    is_pregame_far = market_lifecycle_state == "pregame_far"
    if family.startswith(STRONG_SPORTS_FAMILY_PREFIXES):
        bias += 0.002 if is_pregame_far else 0.003
        reasons.append("strong_family")
    if spread_bucket in {"ultra_tight", "tight"}:
        bias += 0.0015 if is_pregame_far else 0.002
        reasons.append("tight_spread")
    if raw_edge >= -0.005:
        bias += 0.0015 if is_pregame_far else 0.002
        reasons.append("near_positive_edge")
    if calibrated_probability >= no_vig_probability - 0.015:
        bias += 0.002 if is_pregame_far else 0.003
        reasons.append("calibrated_prob_close_to_no_vig")
    elif calibrated_probability >= no_vig_probability:
        bias += 0.0015 if is_pregame_far else 0.002
        reasons.append("calibrated_prob_above_no_vig")
    bias = min(bias, 0.008 if is_pregame_far else 0.012)
    return bias, "+".join(reasons) if reasons else None


def is_sports_family(family, ticker=None):
    family = str(family or "").strip().upper()
    ticker = str(ticker or "").strip().upper()
    if family == "KXBTC15M" or ticker == "KXBTC15M":
        return False
    if family in SPORTS_FAMILIES:
        return True
    return any(family.startswith(prefix) for prefix in SPORTS_PREFIXES)


def get_sports_market_lifecycle_state(bot=None, market=None, q=None, family=None, event_urgency=None):
    market = market if isinstance(market, dict) else {}
    family = str(family or (getattr(bot, "get_market_family", lambda _market: None)(market) if bot is not None else None) or getattr(q, "family", "") or "").strip().upper()
    ticker = getattr(q, "ticker", None)
    if family == "KXBTC15M" or not is_sports_family(family, ticker=ticker):
        return "unknown"
    get_minutes_to_event = getattr(bot, "get_minutes_to_event", None)
    minutes_to_event = None
    if callable(get_minutes_to_event):
        try:
            minutes_to_event = get_minutes_to_event(market)
        except Exception:
            minutes_to_event = None
    urgency = str(event_urgency or market.get("event_urgency") or market.get("urgency") or "").strip().lower()
    live_flag = bool(
        market.get("is_live")
        or market.get("live")
        or str(market.get("status") or market.get("market_status") or "").strip().lower() in {"live", "in_play", "open"}
    )
    if minutes_to_event is None:
        if urgency in {"immediate", "near", "near_term", "urgent"}:
            return "live_early" if live_flag else "pregame_near"
        if urgency in {"far", "distant", "long_term"}:
            return "pregame_far"
        return "unknown"
    minutes_to_event = safe_float(minutes_to_event, 0.0)
    if live_flag:
        if minutes_to_event <= 15.0:
            return "live_late"
        if minutes_to_event <= 60.0:
            return "live_mid"
        return "live_early"
    if minutes_to_event > 240.0:
        return "pregame_far"
    if minutes_to_event > 45.0:
        return "pregame_near"
    if minutes_to_event >= 0.0:
        return "pregame_near"
    return "unknown"


def _bot_float(bot, value, default=0.0):
    if bot is not None and hasattr(bot, "to_float") and callable(bot.to_float):
        try:
            return bot.to_float(value, default)
        except Exception:
            pass
    return safe_float(value, default)


def _bot_clamp(bot, value, low=0.0, high=1.0):
    if bot is not None and hasattr(bot, "_clamp") and callable(bot._clamp):
        try:
            return bot._clamp(value, low, high)
        except Exception:
            pass
    return clamp(value, low, high)


def compute_no_vig_probability(bot=None, *, ticker=None, family=None, yes_bid=None, yes_ask=None, no_bid=None, no_ask=None, spread=None, market_lifecycle_state=None, event_urgency=None, liquidity_quality=None, quote_stability_trend=None, directional_consistency=None, orderbook_pressure=None, context="sports", market=None, q=None):
    market = market if isinstance(market, dict) else {}
    ticker = str(ticker or market.get("ticker") or getattr(q, "ticker", "") or "").strip()
    get_market_family = getattr(bot, "get_market_family", None)
    family = str(family or (get_market_family(market) if callable(get_market_family) else None) or getattr(q, "family", "") or "").strip().upper() or "UNKNOWN"
    yes_bid = _bot_float(bot, yes_bid, _bot_float(bot, getattr(q, "yes_bid", None), _bot_float(bot, market.get("yes_bid"), None)))
    yes_ask = _bot_float(bot, yes_ask, _bot_float(bot, getattr(q, "yes_ask", None), _bot_float(bot, market.get("yes_ask"), None)))
    no_bid = _bot_float(bot, no_bid, _bot_float(bot, getattr(q, "no_bid", None), _bot_float(bot, market.get("no_bid"), None)))
    no_ask = _bot_float(bot, no_ask, _bot_float(bot, getattr(q, "no_ask", None), _bot_float(bot, market.get("no_ask"), None)))
    spread = max(0.0, _bot_float(bot, spread, 0.0) or 0.0)
    yes_mid = None if yes_bid is None or yes_ask is None else max(0.0, min(1.0, (float(yes_bid) + float(yes_ask)) / 2.0))
    no_mid = None if no_bid is None or no_ask is None else max(0.0, min(1.0, (float(no_bid) + float(no_ask)) / 2.0))
    candidates = [value for value in (yes_mid, no_mid) if value is not None]
    if candidates:
        base_probability = sum(candidates) / len(candidates)
    elif yes_ask is not None:
        base_probability = float(yes_ask)
    elif no_ask is not None:
        base_probability = 1.0 - float(no_ask)
    else:
        base_probability = 0.5
    liquidity_quality = _bot_clamp(bot, liquidity_quality if liquidity_quality is not None else market.get("liquidity_quality", 0.5), 0.0, 1.0)
    quote_stability_trend = _bot_clamp(bot, quote_stability_trend if quote_stability_trend is not None else market.get("quote_stability_trend", 0.5), 0.0, 1.0)
    directional_consistency = _bot_clamp(bot, directional_consistency if directional_consistency is not None else market.get("directional_consistency", 0.5), 0.0, 1.0)
    orderbook_pressure = str(orderbook_pressure or market.get("orderbook_pressure") or "balanced").strip().lower()
    lifecycle_state = str(market_lifecycle_state or get_sports_market_lifecycle_state(bot=bot, market=market, q=q, family=family, event_urgency=event_urgency) or "unknown").strip().lower()
    urgency = str(event_urgency or market.get("event_urgency") or market.get("urgency") or "").strip().lower()
    adjustment = 0.0
    if orderbook_pressure in {"bullish", "yes", "buy_yes", "bid_imbalance"}:
        adjustment += 0.055
    elif orderbook_pressure in {"bearish", "no", "buy_no", "ask_imbalance"}:
        adjustment -= 0.055
    if directional_consistency >= 0.7:
        adjustment += 0.03
    elif directional_consistency <= 0.35:
        adjustment -= 0.03
    if quote_stability_trend >= 0.7:
        adjustment += 0.035
    elif quote_stability_trend <= 0.35:
        adjustment -= 0.035
    if liquidity_quality >= 0.7:
        adjustment += 0.025
    elif liquidity_quality <= 0.4:
        adjustment -= 0.025
    if spread >= 0.15:
        adjustment -= 0.02
    if lifecycle_state.startswith("live_"):
        adjustment += 0.015
    elif lifecycle_state == "pregame_far":
        adjustment -= 0.01
    if urgency in {"near", "near_term", "urgent", "immediate"}:
        adjustment += 0.015
    probability = _bot_clamp(bot, base_probability + adjustment, 0.01, 0.99)
    return {
        "ticker": ticker or None,
        "family": family,
        "no_vig_probability": round(probability, 6),
        "no_vig_odds": round(1.0 / max(probability, 0.01), 6),
        "no_vig_confidence": round(_bot_clamp(bot, 0.55 + (liquidity_quality - 0.5) * 0.25 + (quote_stability_trend - 0.5) * 0.2, 0.0, 0.99), 6),
        "yes_mid": round(yes_mid, 6) if yes_mid is not None else None,
        "no_mid": round(no_mid, 6) if no_mid is not None else None,
        "spread": round(spread, 6),
        "liquidity_quality": round(liquidity_quality, 6),
        "quote_stability_trend": round(quote_stability_trend, 6),
        "directional_consistency": round(directional_consistency, 6),
        "orderbook_pressure": orderbook_pressure,
        "market_lifecycle_state": lifecycle_state,
        "event_urgency": urgency or None,
        "context": context,
    }


def compute_calibrated_sports_probability(bot=None, *, ticker=None, family=None, no_vig_probability=None, momentum_score=None, directional_consistency=None, orderbook_pressure=None, repricing_velocity=None, spread_delta=None, quote_stability_trend=None, edge_durability_trend=None, family_learning_trend=None, predictive_regime_score=None, market_lifecycle_state=None, event_urgency=None, liquidity_quality=None, directional_bias=None, spread_bucket=None, context="sports", market=None, q=None):
    market = market if isinstance(market, dict) else {}
    ticker = str(ticker or market.get("ticker") or getattr(q, "ticker", "") or "").strip()
    get_market_family = getattr(bot, "get_market_family", None)
    family = str(family or (get_market_family(market) if callable(get_market_family) else None) or getattr(q, "family", "") or "").strip().upper() or "UNKNOWN"
    base_probability = _bot_clamp(bot, _bot_float(bot, no_vig_probability, 0.5), 0.01, 0.99)
    yes_bid = _bot_float(bot, getattr(q, "yes_bid", None), _bot_float(bot, market.get("yes_bid"), None))
    yes_ask = _bot_float(bot, getattr(q, "yes_ask", None), _bot_float(bot, market.get("yes_ask"), None))
    no_bid = _bot_float(bot, getattr(q, "no_bid", None), _bot_float(bot, market.get("no_bid"), None))
    no_ask = _bot_float(bot, getattr(q, "no_ask", None), _bot_float(bot, market.get("no_ask"), None))
    spread = None
    if yes_bid is not None and yes_ask is not None:
        try:
            spread = max(0.0, float(yes_ask) - float(yes_bid))
        except (TypeError, ValueError):
            spread = None
    if spread is None and no_bid is not None and no_ask is not None:
        try:
            spread = max(0.0, float(no_ask) - float(no_bid))
        except (TypeError, ValueError):
            spread = None
    if spread is None and yes_ask is not None and no_bid is not None:
        try:
            spread = max(0.0, float(yes_ask) - (1.0 - float(no_bid)))
        except (TypeError, ValueError):
            spread = None
    if spread is None and yes_bid is not None and no_ask is not None:
        try:
            spread = max(0.0, (1.0 - float(no_ask)) - float(yes_bid))
        except (TypeError, ValueError):
            spread = None
    momentum_score = _bot_float(bot, momentum_score, 0.0) or 0.0
    directional_consistency = _bot_clamp(bot, _bot_float(bot, directional_consistency, 0.5), 0.0, 1.0)
    orderbook_pressure = str(orderbook_pressure or "balanced").strip().lower()
    repricing_velocity = _bot_float(bot, repricing_velocity, 0.0) or 0.0
    spread_delta = _bot_float(bot, spread_delta, 0.0) or 0.0
    quote_stability_trend = _bot_clamp(bot, _bot_float(bot, quote_stability_trend, 0.5), 0.0, 1.0)
    edge_durability_trend = _bot_clamp(bot, _bot_float(bot, edge_durability_trend, 0.5), 0.0, 1.0)
    family_learning_trend = _bot_clamp(bot, _bot_float(bot, family_learning_trend, 0.5), 0.0, 1.0)
    predictive_regime_score = _bot_clamp(bot, _bot_float(bot, predictive_regime_score, 0.5), 0.0, 1.0)
    liquidity_quality = _bot_clamp(bot, _bot_float(bot, liquidity_quality, 0.5), 0.0, 1.0)
    directional_bias = str(directional_bias or market.get("directional_bias") or "flat").strip().lower()
    spread_bucket = str(spread_bucket or market.get("sports_spread_bucket") or "unknown").strip().lower()
    market_lifecycle_state = str(market_lifecycle_state or get_sports_market_lifecycle_state(bot=bot, market=market, q=q, family=family, event_urgency=event_urgency) or "unknown").strip().lower()
    score = base_probability
    if momentum_score >= 0.18:
        score += 0.05 + min(0.03, momentum_score * 0.08)
    elif momentum_score <= -0.05:
        score -= 0.03
    if directional_consistency >= 0.68:
        score += 0.03
    elif directional_consistency <= 0.38:
        score -= 0.025
    if orderbook_pressure in {"bullish", "yes", "buy_yes", "bid_imbalance"}:
        score += 0.04
    elif orderbook_pressure in {"bearish", "no", "buy_no", "ask_imbalance"}:
        score -= 0.04
    if repricing_velocity > 0.0:
        score += min(0.02, repricing_velocity * 0.15)
    elif repricing_velocity < 0.0:
        score += max(-0.02, repricing_velocity * 0.15)
    if spread_delta < 0.0:
        score += min(0.02, abs(spread_delta) * 0.25)
    elif spread_delta > 0.0:
        score -= min(0.02, spread_delta * 0.25)
    if quote_stability_trend >= 0.7:
        score += 0.03
    elif quote_stability_trend <= 0.35:
        score -= 0.03
    if edge_durability_trend >= 0.68:
        score += 0.035
    elif edge_durability_trend <= 0.35:
        score -= 0.03
    if family_learning_trend >= 0.66:
        score += 0.02
    elif family_learning_trend <= 0.35:
        score -= 0.02
    if predictive_regime_score >= 0.7:
        score += 0.04
    elif predictive_regime_score <= 0.35:
        score -= 0.04
    if market_lifecycle_state in {"pregame_near", "live_early"}:
        strong_pregame_structure = bool(
            quote_stability_trend >= 0.72
            and directional_consistency >= 0.66
            and liquidity_quality >= 0.65
            and predictive_regime_score >= 0.62
        )
        if strong_pregame_structure:
            score += 0.018
        if strong_pregame_structure and spread is not None and spread <= 0.028:
            score += 0.008
        if momentum_score >= 0.10 and spread_bucket in {"ultra_tight", "tight"} and orderbook_pressure in {"balanced", "bullish", "yes", "buy_yes", "bid_imbalance"}:
            score += 0.010
        if family_learning_trend >= 0.65 and edge_durability_trend >= 0.62 and spread_bucket in {"ultra_tight", "tight"}:
            score += 0.014
        if family_learning_trend >= 0.66 and edge_durability_trend >= 0.64 and spread is not None and spread <= 0.028:
            score += 0.006
    if directional_bias == "flat":
        score -= 0.015
    elif directional_bias in {"yes", "bullish", "long"}:
        score += 0.015
    elif directional_bias in {"no", "bearish", "short"}:
        score -= 0.015
    if spread_bucket in {"wide_but_possible", "extreme_wide"}:
        score -= 0.02
    if market_lifecycle_state.startswith("live_"):
        score += 0.015
    elif market_lifecycle_state == "pregame_far":
        score -= 0.01
    if market_lifecycle_state == "live_late":
        score -= 0.01
    if liquidity_quality >= 0.7:
        score += 0.02
    elif liquidity_quality <= 0.4:
        score -= 0.02
    if event_urgency and str(event_urgency).strip().lower() in {"near", "near_term", "urgent", "immediate"}:
        score += 0.01
    calibration_before_family_bias = score
    family_positive_bias, family_bias_reason = _sports_family_positive_bias(
        family,
        ticker=ticker,
        market_lifecycle_state=market_lifecycle_state,
        spread_bucket=spread_bucket,
        calibrated_probability=calibration_before_family_bias,
        no_vig_probability=base_probability,
        raw_edge=calibration_before_family_bias - base_probability,
    )
    if family_positive_bias > 0.0:
        score += family_positive_bias
    calibrated = _bot_clamp(bot, score, 0.01, 0.99)
    return {
        "ticker": ticker or None,
        "family": family,
        "calibrated_probability": round(calibrated, 6),
        "calibrated_probability_before_family_bias": round(calibration_before_family_bias, 6),
        "calibrated_probability_after_family_bias": round(calibrated, 6),
        "base_probability": round(base_probability, 6),
        "calibration_adjustment": round(calibrated - base_probability, 6),
        "family_positive_bias": round(family_positive_bias, 6),
        "family_bias_reason": family_bias_reason,
        "momentum_score": round(momentum_score, 6),
        "directional_consistency": round(directional_consistency, 6),
        "orderbook_pressure": orderbook_pressure,
        "repricing_velocity": round(repricing_velocity, 6),
        "spread_delta": round(spread_delta, 6),
        "quote_stability_trend": round(quote_stability_trend, 6),
        "edge_durability_trend": round(edge_durability_trend, 6),
        "family_learning_trend": round(family_learning_trend, 6),
        "predictive_regime_score": round(predictive_regime_score, 6),
        "market_lifecycle_state": market_lifecycle_state,
        "event_urgency": str(event_urgency or market.get("event_urgency") or market.get("urgency") or "").strip().lower() or None,
        "liquidity_quality": round(liquidity_quality, 6),
        "directional_bias": directional_bias,
        "spread_bucket": spread_bucket,
        "context": context,
    }


def compute_sports_dynamic_buffer(bot=None, *, ticker=None, family=None, spread=None, spread_bucket=None, calibrated_probability=None, no_vig_probability=None, momentum_score=None, directional_consistency=None, orderbook_pressure=None, repricing_velocity=None, spread_delta=None, quote_stability_trend=None, edge_durability_trend=None, family_learning_trend=None, predictive_regime_score=None, liquidity_quality=None, market_lifecycle_state=None, event_urgency=None, estimated_cost=None, context="sports", market=None, q=None):
    market = market if isinstance(market, dict) else {}
    ticker = str(ticker or market.get("ticker") or getattr(q, "ticker", "") or "").strip()
    get_market_family = getattr(bot, "get_market_family", None)
    family = str(family or (get_market_family(market) if callable(get_market_family) else None) or getattr(q, "family", "") or "").strip().upper() or "UNKNOWN"
    spread = max(0.0, _bot_float(bot, spread, 0.0) or 0.0)
    spread_bucket = str(spread_bucket or market.get("sports_spread_bucket") or "unknown").strip().lower()
    calibrated_probability = _bot_clamp(bot, _bot_float(bot, calibrated_probability, 0.5), 0.01, 0.99)
    no_vig_probability = _bot_clamp(bot, _bot_float(bot, no_vig_probability, 0.5), 0.01, 0.99)
    momentum_score = _bot_float(bot, momentum_score, 0.0) or 0.0
    directional_consistency = _bot_clamp(bot, _bot_float(bot, directional_consistency, 0.5), 0.0, 1.0)
    orderbook_pressure = str(orderbook_pressure or "balanced").strip().lower()
    repricing_velocity = _bot_float(bot, repricing_velocity, 0.0) or 0.0
    spread_delta = _bot_float(bot, spread_delta, 0.0) or 0.0
    quote_stability_trend = _bot_clamp(bot, _bot_float(bot, quote_stability_trend, 0.5), 0.0, 1.0)
    edge_durability_trend = _bot_clamp(bot, _bot_float(bot, edge_durability_trend, 0.5), 0.0, 1.0)
    family_learning_trend = _bot_clamp(bot, _bot_float(bot, family_learning_trend, 0.5), 0.0, 1.0)
    predictive_regime_score = _bot_clamp(bot, _bot_float(bot, predictive_regime_score, 0.5), 0.0, 1.0)
    liquidity_quality = _bot_clamp(bot, _bot_float(bot, liquidity_quality, 0.5), 0.0, 1.0)
    market_lifecycle_state = str(market_lifecycle_state or get_sports_market_lifecycle_state(bot=bot, market=market, q=q, family=family, event_urgency=event_urgency) or "unknown").strip().lower()
    estimated_cost = _bot_clamp(bot, _bot_float(bot, estimated_cost, 0.0), 0.0, 0.25)
    buffer = 0.006 + min(0.04, spread * 0.12)
    if spread_bucket in {"wide_but_possible", "extreme_wide"}:
        buffer += 0.01
    if momentum_score < 0.1:
        buffer += 0.007
    if directional_consistency < 0.55:
        buffer += 0.005
    if orderbook_pressure in {"bearish", "no", "buy_no", "ask_imbalance"}:
        buffer += 0.006
    if repricing_velocity < 0.0:
        buffer += min(0.01, abs(repricing_velocity) * 0.12)
    if spread_delta > 0.0:
        buffer += min(0.01, spread_delta * 0.15)
    if quote_stability_trend < 0.5:
        buffer += 0.008
    if edge_durability_trend < 0.5:
        buffer += 0.008
    if family_learning_trend < 0.5:
        buffer += 0.004
    if predictive_regime_score < 0.5:
        buffer += 0.008
    if liquidity_quality < 0.5:
        buffer += 0.008
    if market_lifecycle_state in {"pregame_far", "live_late"}:
        buffer += 0.006
    if market_lifecycle_state in {"pregame_near", "live_early"} and quote_stability_trend >= 0.68 and directional_consistency >= 0.62 and liquidity_quality >= 0.60 and predictive_regime_score >= 0.58:
        buffer -= 0.008
    if market_lifecycle_state in {"pregame_near", "live_early"} and family_learning_trend >= 0.62 and edge_durability_trend >= 0.60:
        buffer -= 0.005
    if market_lifecycle_state in {"pregame_near", "live_early"} and spread_bucket in {"ultra_tight", "tight"} and spread is not None and spread <= 0.028 and quote_stability_trend >= 0.72 and directional_consistency >= 0.66 and predictive_regime_score >= 0.62:
        buffer -= 0.006
    if spread_bucket == "ultra_tight" and spread is not None and spread <= 0.028 and family_learning_trend >= 0.66 and edge_durability_trend >= 0.64:
        buffer -= 0.004
    if event_urgency and str(event_urgency).strip().lower() in {"near", "near_term", "urgent", "immediate"}:
        buffer -= 0.004
    if spread_bucket == "ultra_tight":
        buffer -= 0.004
    if spread_bucket == "tight":
        buffer -= 0.002
    if spread_bucket == "medium":
        buffer += 0.0
    buffer += min(0.008, max(0.0, estimated_cost) * 0.4)
    if market_lifecycle_state in {"pregame_near", "live_early", "pregame_far"} and spread_bucket in {"ultra_tight", "tight"} and family.startswith(STRONG_SPORTS_FAMILY_PREFIXES):
        positive_calibration_gap = max(0.0, calibrated_probability - no_vig_probability)
        if positive_calibration_gap >= 0.001:
            buffer -= min(0.005, 0.002 + positive_calibration_gap * 0.4)
    buffer = clamp(buffer, 0.002, 0.20)
    buffer_value = round(buffer, 6)
    return {
        "ticker": ticker or None,
        "family": family,
        "sports_dynamic_buffer": buffer_value,
        "dynamic_buffer": buffer_value,
        "spread_bucket": spread_bucket,
        "calibrated_probability": round(calibrated_probability, 6),
        "no_vig_probability": round(no_vig_probability, 6),
        "momentum_score": round(momentum_score, 6),
        "directional_consistency": round(directional_consistency, 6),
        "orderbook_pressure": orderbook_pressure,
        "repricing_velocity": round(repricing_velocity, 6),
        "spread_delta": round(spread_delta, 6),
        "quote_stability_trend": round(quote_stability_trend, 6),
        "edge_durability_trend": round(edge_durability_trend, 6),
        "family_learning_trend": round(family_learning_trend, 6),
        "predictive_regime_score": round(predictive_regime_score, 6),
        "liquidity_quality": round(liquidity_quality, 6),
        "market_lifecycle_state": market_lifecycle_state,
        "event_urgency": str(event_urgency or market.get("event_urgency") or market.get("urgency") or "").strip().lower() or None,
        "estimated_cost": round(estimated_cost, 6),
        "context": context,
    }


def compute_sports_post_cost_edge(bot=None, *, ticker=None, family=None, calibrated_probability=None, no_vig_probability=None, no_vig_odds=None, dynamic_buffer=None, estimated_cost=None, spread_bucket=None, market_lifecycle_state=None, context="sports", market=None, q=None):
    market = market if isinstance(market, dict) else {}
    ticker = str(ticker or market.get("ticker") or getattr(q, "ticker", "") or "").strip()
    get_market_family = getattr(bot, "get_market_family", None)
    family = str(family or (get_market_family(market) if callable(get_market_family) else None) or getattr(q, "family", "") or "").strip().upper() or "UNKNOWN"
    calibrated_probability = _bot_clamp(bot, _bot_float(bot, calibrated_probability, 0.5), 0.01, 0.99)
    no_vig_probability = _bot_clamp(bot, _bot_float(bot, no_vig_probability, 0.5), 0.01, 0.99)
    no_vig_odds = _bot_float(bot, no_vig_odds, None)
    if no_vig_odds is None or no_vig_odds <= 0.0:
        no_vig_odds = 1.0 / max(no_vig_probability, 0.01)
    yes_bid = _bot_float(bot, getattr(q, "yes_bid", None), _bot_float(bot, market.get("yes_bid"), None))
    yes_ask = _bot_float(bot, getattr(q, "yes_ask", None), _bot_float(bot, market.get("yes_ask"), None))
    no_bid = _bot_float(bot, getattr(q, "no_bid", None), _bot_float(bot, market.get("no_bid"), None))
    no_ask = _bot_float(bot, getattr(q, "no_ask", None), _bot_float(bot, market.get("no_ask"), None))
    market_price = None
    for value in (yes_ask, yes_bid, 1.0 - no_bid if no_bid is not None else None, 1.0 - no_ask if no_ask is not None else None):
        if value is not None and value > 0.0:
            market_price = clamp(value, 0.0, 1.0)
            break
    if market_price is None:
        market_price = 0.5
    spread_cost = clamp(_bot_float(bot, dynamic_buffer, 0.01), 0.0, 0.20)
    estimated_cost = clamp(_bot_float(bot, estimated_cost, 0.0), 0.0, 0.25)
    raw_edge = calibrated_probability - market_price
    post_cost_edge_before_family_bias = clamp(raw_edge - spread_cost - estimated_cost, -0.25, 0.25)
    family_positive_bias, family_bias_reason = _sports_family_positive_bias(
        family,
        ticker=ticker,
        market_lifecycle_state=market_lifecycle_state,
        spread_bucket=spread_bucket,
        calibrated_probability=calibrated_probability,
        no_vig_probability=no_vig_probability,
        raw_edge=post_cost_edge_before_family_bias,
    )
    final_edge_before_family_bias = max(0.0, post_cost_edge_before_family_bias)
    positive_edge_relief = 0.0
    if family.startswith(STRONG_SPORTS_FAMILY_PREFIXES) and spread_bucket in {"ultra_tight", "tight"} and final_edge_before_family_bias > 0.001:
        positive_edge_relief = min(0.003, final_edge_before_family_bias * 0.20)
    final_edge = clamp(final_edge_before_family_bias + family_positive_bias + positive_edge_relief, 0.0, 0.25)
    raw_edge_value = round(raw_edge, 6)
    post_cost_edge_value = round(final_edge, 6)
    post_cost_edge_before_family_bias_value = round(post_cost_edge_before_family_bias, 6)
    final_edge_before_family_bias_value = round(final_edge_before_family_bias, 6)
    final_edge_value = round(final_edge, 6)
    buffer_value = round(spread_cost, 6)
    return {
        "ticker": ticker or None,
        "family": family,
        "calibrated_probability_before_family_bias": round(max(0.0, calibrated_probability - family_positive_bias), 6),
        "calibrated_probability_after_family_bias": round(calibrated_probability, 6),
        "calibrated_probability": round(calibrated_probability, 6),
        "no_vig_probability": round(no_vig_probability, 6),
        "no_vig_odds": round(no_vig_odds, 6),
        "estimated_cost": round(estimated_cost, 6),
        "sports_dynamic_buffer": buffer_value,
        "dynamic_buffer": buffer_value,
        "raw_price": round(market_price, 6),
        "market_price": round(market_price, 6),
        "spread_cost": buffer_value,
        "fees": round(estimated_cost, 6),
        "sports_edge_after_cost_before_family_bias": post_cost_edge_before_family_bias_value,
        "sports_edge_after_cost": post_cost_edge_value,
        "sports_edge_after_cost_after_family_bias": post_cost_edge_value,
        "final_edge_before_family_bias": final_edge_before_family_bias_value,
        "final_edge": final_edge_value,
        "sports_edge_raw": raw_edge_value,
        "raw_edge": raw_edge_value,
        "family_positive_bias": round(family_positive_bias, 6),
        "family_bias_reason": family_bias_reason,
        "sports_post_cost_edge": post_cost_edge_value,
        "spread_bucket": str(spread_bucket or market.get("sports_spread_bucket") or "unknown").strip().lower(),
        "market_lifecycle_state": str(market_lifecycle_state or get_sports_market_lifecycle_state(bot=bot, market=market, q=q, family=family) or "unknown").strip().lower(),
        "context": context,
    }


builtins.print("[AION-MODULE-LOAD] " + json.dumps({"module": "sports.calibration", "helpers": ["is_sports_family", "get_sports_market_lifecycle_state", "compute_no_vig_probability", "compute_calibrated_sports_probability", "compute_sports_dynamic_buffer", "compute_sports_post_cost_edge"]}, sort_keys=True))

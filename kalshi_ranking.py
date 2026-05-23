"""Ranking, EV, Kelly, and probability-quality helpers for kalshi_web."""

import json
import time


def ev_rank_adjustment(ev_context, to_float):
    ev_context = ev_context if isinstance(ev_context, dict) else {}
    ev_score = to_float(ev_context.get("ev_score"), 0.0) or 0.0
    ev_tier = str(ev_context.get("ev_tier") or "").strip().upper()
    if ev_tier == "S":
        return max(1.50, min(4.00, ev_score * 0.35))
    if ev_tier == "A":
        return max(0.75, min(2.50, ev_score * 0.30))
    if ev_tier == "B":
        return max(0.10, min(1.25, ev_score * 0.20))
    if ev_tier == "C":
        return max(-0.25, min(0.25, ev_score * 0.10))
    if ev_tier == "D":
        return min(-0.50, max(-3.50, ev_score * 0.25))
    return max(-0.25, min(0.25, ev_score * 0.10))


def emit_aion_bayes_log(bot, *, ticker=None, family=None, ev_context=None):
    ticker = str(ticker or "").strip()
    if not ticker:
        return
    already_logged = bool(
        getattr(bot, "last_aion_bayes_log_cycle", None) == bot.cycle_count
        and str(getattr(bot, "last_aion_bayes_log_ticker", "") or "").strip() == ticker
    )
    if already_logged:
        return
    ev_context = ev_context if isinstance(ev_context, dict) else {}
    print(
        "[AION-BAYES] "
        + json.dumps(
            {
                "ticker": ticker,
                "family": family,
                "raw_probability": ev_context.get("raw_probability"),
                "bayesian_probability": ev_context.get("bayesian_probability"),
                "bayesian_delta": ev_context.get("bayesian_delta"),
                "posterior_mean": ev_context.get("bayesian_posterior_mean"),
                "sample_size": ev_context.get("bayesian_sample_size"),
                "reason": ev_context.get("bayesian_reason") or "unknown",
            },
            sort_keys=True,
            default=str,
        )
    )
    bot.last_aion_bayes_log_cycle = bot.cycle_count
    bot.last_aion_bayes_log_ticker = ticker


def emit_aion_calibration_log(bot, *, ticker=None, family=None, ev_context=None):
    ticker = str(ticker or "").strip()
    if not ticker:
        return
    already_logged = bool(
        getattr(bot, "last_aion_calibration_log_cycle", None) == bot.cycle_count
        and str(getattr(bot, "last_aion_calibration_log_ticker", "") or "").strip() == ticker
    )
    if already_logged:
        return
    ev_context = ev_context if isinstance(ev_context, dict) else {}
    print(
        "[AION-CALIBRATION] "
        + json.dumps(
            {
                "ticker": ticker,
                "family": family,
                "raw_probability": ev_context.get(
                    "calibration_input_probability",
                    ev_context.get("bayesian_probability", ev_context.get("raw_probability")),
                ),
                "calibrated_probability": ev_context.get("calibrated_probability"),
                "calibration_delta": ev_context.get("calibration_delta"),
                "sample_size": ev_context.get("calibration_sample_size"),
                "reliability_weight": ev_context.get("calibration_reliability_weight"),
                "reason": ev_context.get("calibration_reason") or "unknown",
            },
            sort_keys=True,
            default=str,
        )
    )
    bot.last_aion_calibration_log_cycle = bot.cycle_count
    bot.last_aion_calibration_log_ticker = ticker


def get_fractional_kelly_bankroll(bot):
    def _normalized_bankroll(value, *, cents=False):
        numeric = bot.to_float(value, None)
        if numeric is None or numeric <= 0.0:
            return None
        return round(float(numeric) / 100.0, 4) if cents else round(float(numeric), 4)

    def _from_mapping(mapping):
        mapping = mapping if isinstance(mapping, dict) else {}
        for field in (
            "bankroll",
            "kelly_bankroll",
            "available_bankroll",
            "portfolio_value",
            "account_value",
            "account_balance",
            "cash_balance",
            "available_cash",
            "available_balance_dollars",
            "balance_dollars",
        ):
            value = _normalized_bankroll(mapping.get(field), cents=False)
            if value is not None:
                return value
        for field in ("balance_cents", "available_balance_cents", "cash_balance_cents"):
            value = _normalized_bankroll(mapping.get(field), cents=True)
            if value is not None:
                return value
        return None

    for snapshot in (
        bot._get_capital_snapshot() if hasattr(bot, "_get_capital_snapshot") else {},
        getattr(bot, "last_realm_xviii_summary", {}) or {},
    ):
        bankroll = _from_mapping(snapshot)
        if bankroll is not None:
            return bankroll

    now = time.time()
    cached = getattr(bot, "last_fractional_kelly_bankroll_snapshot", {}) or {}
    if cached and (now - bot.to_float(cached.get("ts"), 0.0)) < 60.0:
        return bot.to_float(cached.get("bankroll"), None)

    bankroll = None
    try:
        result = bot._live_request_json("GET", "/trade-api/v2/portfolio/balance", timeout=5)
        body = result.get("body") if isinstance(result, dict) else {}
        if isinstance(body, dict):
            bankroll = _normalized_bankroll(body.get("balance"), cents=True)
            if bankroll is None:
                bankroll = _normalized_bankroll(body.get("available_balance"), cents=True)
            if bankroll is None:
                bankroll = _from_mapping(body)
    except Exception:
        bankroll = None
    bot.last_fractional_kelly_bankroll_snapshot = {
        "ts": now,
        "bankroll": bankroll,
    }
    return bankroll


def emit_aion_kelly_log(bot, *, ticker=None, family=None, ev_context=None, price=None, action=None):
    ticker = str(ticker or "").strip()
    if not ticker:
        return
    already_logged = bool(
        getattr(bot, "last_aion_kelly_log_cycle", None) == bot.cycle_count
        and str(getattr(bot, "last_aion_kelly_log_ticker", "") or "").strip() == ticker
    )
    if already_logged:
        return
    ev_context = ev_context if isinstance(ev_context, dict) else {}
    kelly_result = dict(getattr(bot, "last_fractional_kelly_result", {}) or {})
    print(
        "[AION-KELLY] "
        + json.dumps(
            {
                "ticker": ticker,
                "family": family,
                "ev_tier": ev_context.get("ev_tier"),
                "estimated_win_probability": kelly_result.get(
                    "estimated_win_probability",
                    ev_context.get("estimated_win_probability"),
                ),
                "price": bot.to_float(price, kelly_result.get("price")),
                "expected_roi": ev_context.get("expected_roi"),
                "raw_kelly_fraction": kelly_result.get("raw_kelly_fraction"),
                "fractional_kelly_fraction": kelly_result.get("fractional_kelly_fraction"),
                "kelly_multiplier": kelly_result.get("kelly_multiplier", ev_context.get("kelly_multiplier")),
                "final_contract_size": kelly_result.get(
                    "final_contract_size",
                    ev_context.get("kelly_contract_size"),
                ),
                "action": action or kelly_result.get("reason") or ev_context.get("kelly_reason") or "selected",
            },
            sort_keys=True,
            default=str,
        )
    )
    bot.last_aion_kelly_log_cycle = bot.cycle_count
    bot.last_aion_kelly_log_ticker = ticker


def resolve_ev_contract_size(
    bot,
    ev_context=None,
    q=None,
    *,
    aggressive_max_contracts,
    max_live_order_size,
    kelly_fraction,
    kelly_max_multiplier,
    kelly_min_exploration_size,
    calculate_fractional_kelly_size,
):
    ev_context = ev_context if isinstance(ev_context, dict) else {}
    base_size = max(1, int(aggressive_max_contracts or 1))
    max_size = max(1, int(max_live_order_size or base_size))
    multiplier = bot.to_float(ev_context.get("ev_sizing_multiplier"), None)
    if multiplier is None and q is not None:
        multiplier = bot.to_float(getattr(q, "ev_sizing_multiplier", None), None)
    if multiplier is None:
        multiplier = 1.0
    multiplier = max(0.0, min(1.60, float(multiplier)))
    ev_tier = str(ev_context.get("ev_tier") or getattr(q, "ev_tier", None) or "C").strip().upper() or "C"
    requested_size = int(round(base_size * multiplier))
    if ev_tier == "S" and multiplier > 1.0 and max_size > base_size:
        requested_size = max(requested_size, base_size + 1)
    if requested_size <= 0:
        requested_size = 1
    ev_adjusted_size = max(1, min(max_size, requested_size))

    if calculate_fractional_kelly_size is None:
        bot.last_fractional_kelly_result = {
            "estimated_win_probability": ev_context.get("estimated_win_probability"),
            "price": ev_context.get("ev_selected_price"),
            "payout": None,
            "raw_kelly_fraction": None,
            "fractional_kelly_fraction": None,
            "kelly_multiplier": 1.0,
            "final_contract_size": int(ev_adjusted_size),
            "reason": "fallback_kelly_helper_unavailable",
        }
        return ev_adjusted_size

    kelly_candidate = dict(ev_context)
    kelly_candidate.update(
        {
            "kelly_fraction_config": kelly_fraction,
            "kelly_max_multiplier": kelly_max_multiplier,
            "kelly_min_exploration_size": kelly_min_exploration_size,
            "kelly_selected_for_dispatch": True,
            "q": q,
        }
    )
    if q is not None:
        for field in (
            "ticker",
            "selected_side",
            "side",
            "selected_price",
            "selected_price_dollars",
            "yes_bid",
            "yes_ask",
            "no_bid",
            "no_ask",
            "probability_estimate",
            "confidence_score",
        ):
            if kelly_candidate.get(field) is None:
                try:
                    kelly_candidate[field] = getattr(q, field, None)
                except Exception:
                    pass
    bankroll = bot._get_fractional_kelly_bankroll()
    kelly_result = calculate_fractional_kelly_size(
        kelly_candidate,
        bankroll,
        ev_adjusted_size,
        max_size,
    )
    bot.last_fractional_kelly_result = dict(kelly_result or {})
    kelly_fields = {
        "kelly_fraction": kelly_candidate.get("kelly_fraction"),
        "kelly_multiplier": kelly_candidate.get("kelly_multiplier"),
        "kelly_contract_size": kelly_candidate.get("kelly_contract_size"),
        "kelly_reason": kelly_candidate.get("kelly_reason"),
    }
    ev_context.update(kelly_fields)
    if q is not None:
        for field, value in kelly_fields.items():
            try:
                setattr(q, field, value)
            except Exception:
                pass
    final_size = int(bot.to_float((kelly_result or {}).get("final_contract_size"), ev_adjusted_size) or ev_adjusted_size)
    if final_size <= 0:
        final_size = max(1, min(max_size, kelly_min_exploration_size or ev_adjusted_size or base_size))
        bot.last_fractional_kelly_result["final_contract_size"] = int(final_size)
        bot.last_fractional_kelly_result["kelly_multiplier"] = round(final_size / max(1, ev_adjusted_size), 4)
        bot.last_fractional_kelly_result["reason"] = str(
            bot.last_fractional_kelly_result.get("reason") or "kelly_zero"
        ) + "_selected_flow_micro_fallback"
        ev_context["kelly_contract_size"] = int(final_size)
        ev_context["kelly_multiplier"] = bot.last_fractional_kelly_result["kelly_multiplier"]
        ev_context["kelly_reason"] = bot.last_fractional_kelly_result["reason"]
    return max(1, min(max_size, final_size))


def apply_rank_only_memory_adjustments(bot, ranked, *, source="ranking", markets=None):
    ranked = list(ranked or [])
    if not ranked:
        return ranked
    market_lookup = {
        str(market.get("ticker", "") or "").strip(): market
        for market in (markets or [])
        if isinstance(market, dict) and str(market.get("ticker", "") or "").strip()
    }
    decorated = []
    for index, item in enumerate(ranked):
        ticker = bot._extract_ticker_from_ranked_candidate(item)
        market = market_lookup.get(ticker, {})
        family = bot._extract_family_from_ranked_candidate(item)
        family_adjustment = bot._get_family_profit_memory_adjustment(family, log=True, source=source)
        imbalance_context = bot._get_orderbook_imbalance_adjustment(
            item,
            market=market,
            log=True,
            source=source,
        )
        btc_volatility_context = bot._get_btc_volatility_rank_adjustment(
            item,
            market=market,
            log=True,
            source=source,
        )
        ev_context = bot._attach_ev_score_to_candidate(
            item,
            market=market,
            family=family,
            family_profit_memory_adjustment=family_adjustment,
            orderbook_imbalance_context=imbalance_context,
            source=source,
        )
        total_score = (
            bot._rank_score_for_family_profit_memory(item)
            + family_adjustment
            + bot.to_float(imbalance_context.get("adjustment"), 0.0)
            + bot.to_float(btc_volatility_context.get("adjustment"), 0.0)
            + bot._ev_rank_adjustment(ev_context)
        )
        decorated.append((total_score, -index, item))
    decorated.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [item for _total_score, _tie_breaker, item in decorated]

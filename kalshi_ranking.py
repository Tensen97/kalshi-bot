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
                "STRUCTURAL_EDGE_SCORE": ev_context.get("STRUCTURAL_EDGE_SCORE", ev_context.get("structural_edge_score")),
                "CROSS_MARKET_EDGE_SCORE": ev_context.get("CROSS_MARKET_EDGE_SCORE", ev_context.get("cross_market_edge_score")),
                "ELITE_EDGE_CONFIDENCE": ev_context.get("ELITE_EDGE_CONFIDENCE", ev_context.get("elite_edge_confidence")),
                "conviction_size_multiplier": ev_context.get("conviction_size_multiplier"),
                "structural_edge_size_multiplier": ev_context.get("structural_edge_size_multiplier"),
                "elite_edge_size_multiplier": ev_context.get("elite_edge_size_multiplier"),
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
    structural_score = bot.to_float(
        ev_context.get("STRUCTURAL_EDGE_SCORE", ev_context.get("structural_edge_score")),
        bot.to_float(getattr(q, "STRUCTURAL_EDGE_SCORE", None), 0.0) if q is not None else 0.0,
    ) or 0.0
    cross_market_score = bot.to_float(
        ev_context.get("CROSS_MARKET_EDGE_SCORE", ev_context.get("cross_market_edge_score")),
        bot.to_float(getattr(q, "CROSS_MARKET_EDGE_SCORE", None), 0.0) if q is not None else 0.0,
    ) or 0.0
    conviction_size_multiplier = bot.to_float(
        ev_context.get("conviction_size_multiplier"),
        bot.to_float(getattr(q, "conviction_size_multiplier", None), None) if q is not None else None,
    )
    elite_edge_confidence = bot.to_float(
        ev_context.get("ELITE_EDGE_CONFIDENCE", ev_context.get("elite_edge_confidence")),
        bot.to_float(getattr(q, "ELITE_EDGE_CONFIDENCE", None), 0.0) if q is not None else 0.0,
    ) or 0.0
    elite_size_multiplier = bot.to_float(
        ev_context.get("elite_edge_size_multiplier"),
        bot.to_float(getattr(q, "elite_edge_size_multiplier", None), 1.0) if q is not None else 1.0,
    ) or 1.0
    elite_kelly_max_multiplier = bot.to_float(
        ev_context.get("elite_edge_kelly_max_multiplier"),
        bot.to_float(getattr(q, "elite_edge_kelly_max_multiplier", None), None) if q is not None else None,
    )
    multiplier_cap = max(1.60, min(3.0, bot.to_float(kelly_max_multiplier, 1.75) or 1.75))
    if structural_score >= 78.0 or cross_market_score >= 70.0:
        multiplier_cap = max(multiplier_cap, min(4.0, (bot.to_float(kelly_max_multiplier, 1.75) or 1.75) + 0.75))
    if structural_score >= 88.0 and cross_market_score >= 78.0:
        multiplier_cap = max(multiplier_cap, min(6.0, (bot.to_float(kelly_max_multiplier, 1.75) or 1.75) + 1.50))
    if elite_edge_confidence >= 88.0:
        multiplier_cap = max(multiplier_cap, min(15.0, elite_kelly_max_multiplier or 15.0, max(7.5, elite_size_multiplier)))
    elif elite_edge_confidence >= 75.0:
        multiplier_cap = max(multiplier_cap, min(12.0, elite_kelly_max_multiplier or 12.0, max(4.75, elite_size_multiplier)))
    elif elite_edge_confidence >= 60.0:
        multiplier_cap = max(multiplier_cap, min(7.0, elite_kelly_max_multiplier or 7.0, max(2.35, elite_size_multiplier)))
    if conviction_size_multiplier is not None and conviction_size_multiplier < 0.90:
        multiplier_cap = min(multiplier_cap, 1.15)
    multiplier = max(0.0, min(multiplier_cap, float(multiplier)))
    ev_tier = str(ev_context.get("ev_tier") or getattr(q, "ev_tier", None) or "C").strip().upper() or "C"
    requested_size = int(round(base_size * multiplier))
    if ev_tier == "S" and multiplier > 1.0 and max_size > base_size:
        requested_size = max(requested_size, base_size + 1)
    if requested_size <= 0:
        requested_size = 1
    ev_adjusted_size = max(1, min(max_size, requested_size))
    if structural_score > 0.0 or cross_market_score > 0.0 or conviction_size_multiplier not in (None, 1.0):
        try:
            print(
                "[AION-CONVICTION-SIZE] "
                + json.dumps(
                    {
                        "ticker": ev_context.get("ticker") or getattr(q, "ticker", None),
                        "family": ev_context.get("family") or getattr(q, "family", None),
                        "ev_tier": ev_tier,
                        "STRUCTURAL_EDGE_SCORE": round(structural_score, 4),
                        "CROSS_MARKET_EDGE_SCORE": round(cross_market_score, 4),
                        "ELITE_EDGE_CONFIDENCE": round(elite_edge_confidence, 4),
                        "elite_edge_size_multiplier": round(elite_size_multiplier, 4),
                        "elite_edge_kelly_max_multiplier": elite_kelly_max_multiplier,
                        "conviction_size_multiplier": None if conviction_size_multiplier is None else round(conviction_size_multiplier, 4),
                        "ev_sizing_multiplier": round(multiplier, 4),
                        "base_size": int(base_size),
                        "requested_size": int(requested_size),
                        "ev_adjusted_size": int(ev_adjusted_size),
                        "max_size": int(max_size),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
        except Exception:
            pass

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
            "elite_edge_kelly_max_multiplier": elite_kelly_max_multiplier,
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
    final_size = int(bot.to_float((kelly_result or {}).get("final_contract_size"), ev_adjusted_size) or 0)
    if final_size <= 0:
        bot.last_fractional_kelly_result["final_contract_size"] = 0
        bot.last_fractional_kelly_result["kelly_multiplier"] = 0.0
        bot.last_fractional_kelly_result["reason"] = str(
            bot.last_fractional_kelly_result.get("reason") or "kelly_zero"
        )
        ev_context["kelly_contract_size"] = 0
        ev_context["kelly_multiplier"] = 0.0
        ev_context["kelly_reason"] = bot.last_fractional_kelly_result["reason"]
        return 0
    return max(1, min(max_size, final_size))


def _clamp(value, low, high):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return max(float(low), min(float(high), numeric))


def _candidate_q(item):
    if isinstance(item, dict):
        return item.get("q") or item.get("quote")
    if isinstance(item, (list, tuple)) and len(item) > 1:
        return item[1]
    return None


def _candidate_get(bot, item, market, q, *names):
    containers = []
    if isinstance(item, dict):
        containers.append(item)
        for nested_name in ("expectancy_summary", "quality_snapshot", "ev_context", "momentum_context"):
            nested = item.get(nested_name)
            if isinstance(nested, dict):
                containers.append(nested)
    if isinstance(market, dict):
        containers.append(market)
    for name in names:
        if q is not None:
            value = getattr(q, name, None)
            if value not in (None, ""):
                return value
        for container in containers:
            value = container.get(name)
            if value not in (None, ""):
                return value
    return None


def _coerce_probability(bot, value):
    numeric = bot.to_float(value, None)
    if numeric is None:
        return None
    if 0.0 <= numeric <= 1.0:
        return round(float(numeric), 6)
    if 1.0 < numeric <= 100.0:
        return round(float(numeric) / 100.0, 6)
    return None


def _coerce_probability_series(bot, value, *, invert=False):
    if value is None:
        return []
    raw_values = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if "," in text:
            raw_values = [part.strip() for part in text.split(",")]
        else:
            raw_values = [text]
    elif not isinstance(value, (list, tuple)):
        raw_values = [value]
    series = []
    for raw in raw_values:
        if isinstance(raw, dict):
            raw = raw.get("price", raw.get("value", raw.get("mid", raw.get("probability"))))
        probability = _coerce_probability(bot, raw)
        if probability is None:
            continue
        if invert:
            probability = round(1.0 - probability, 6)
        series.append(probability)
    return series


def _current_yes_probability(bot, item, market, q):
    yes_bid = _coerce_probability(bot, _candidate_get(bot, item, market, q, "yes_bid", "yes_bid_dollars"))
    yes_ask = _coerce_probability(bot, _candidate_get(bot, item, market, q, "yes_ask", "yes_ask_dollars"))
    no_bid = _coerce_probability(bot, _candidate_get(bot, item, market, q, "no_bid", "no_bid_dollars"))
    no_ask = _coerce_probability(bot, _candidate_get(bot, item, market, q, "no_ask", "no_ask_dollars"))
    if yes_bid is not None and yes_ask is not None and yes_ask >= yes_bid:
        return round((yes_bid + yes_ask) / 2.0, 6), "yes_mid"
    mid_price = _coerce_probability(bot, _candidate_get(bot, item, market, q, "mid_price", "mid"))
    if mid_price is not None:
        return mid_price, "mid_price"
    last_price = _coerce_probability(bot, _candidate_get(bot, item, market, q, "last_price", "last_price_dollars", "price"))
    if last_price is not None:
        return last_price, "last_price"
    if no_bid is not None and no_ask is not None and no_ask >= no_bid:
        return round(1.0 - ((no_bid + no_ask) / 2.0), 6), "no_mid_inverse"
    selected_side = str(_candidate_get(bot, item, market, q, "selected_side", "side") or "").strip().lower()
    selected_price = _coerce_probability(
        bot,
        _candidate_get(
            bot,
            item,
            market,
            q,
            "selected_price",
            "selected_price_dollars",
            "selected_price_cents",
            "ev_selected_price",
        ),
    )
    if selected_price is not None and selected_side in {"yes", "no"}:
        return (selected_price if selected_side == "yes" else round(1.0 - selected_price, 6)), "selected_price"
    if yes_ask is not None:
        return yes_ask, "yes_ask"
    if yes_bid is not None:
        return yes_bid, "yes_bid"
    if no_bid is not None:
        return round(1.0 - no_bid, 6), "no_bid_inverse"
    if no_ask is not None:
        return round(1.0 - no_ask, 6), "no_ask_inverse"
    return None, "missing"


def _extract_yes_probability_series(bot, item, market, q):
    series = []
    for field in ("yes_price_history", "yes_probability_history", "yes_recent_prices"):
        series.extend(_coerce_probability_series(bot, _candidate_get(bot, item, market, q, field), invert=False))
    for field in ("no_price_history", "no_probability_history", "no_recent_prices"):
        series.extend(_coerce_probability_series(bot, _candidate_get(bot, item, market, q, field), invert=True))
    if series:
        return series[-8:], "side_specific_history"
    for field in ("price_history", "recent_prices", "probability_history", "recent_probability", "prices"):
        series.extend(_coerce_probability_series(bot, _candidate_get(bot, item, market, q, field), invert=False))
    if series:
        return series[-8:], "generic_history"
    previous = _coerce_probability(
        bot,
        _candidate_get(
            bot,
            item,
            market,
            q,
            "previous_price",
            "previous_mid_price",
            "prior_price",
            "last_mid_price",
        ),
    )
    current, current_source = _current_yes_probability(bot, item, market, q)
    if previous is not None and current is not None:
        return [previous, current], f"previous_plus_{current_source}"
    return ([current] if current is not None else []), current_source


def _selected_side(bot, item, market, q, ev_context=None):
    ev_context = ev_context if isinstance(ev_context, dict) else {}
    side = (
        ev_context.get("selected_side")
        or ev_context.get("side")
        or _candidate_get(bot, item, market, q, "selected_side", "side", "chosen_side")
    )
    side = str(side or "").strip().lower()
    if side in {"buy_yes", "yes_bid"}:
        return "yes"
    if side in {"buy_no", "no_bid"}:
        return "no"
    if side in {"yes", "no"}:
        return side
    selected_price = _coerce_probability(
        bot,
        _candidate_get(
            bot,
            item,
            market,
            q,
            "selected_price",
            "selected_price_dollars",
            "selected_price_cents",
            "ev_selected_price",
        ),
    )
    yes_ask = _coerce_probability(bot, _candidate_get(bot, item, market, q, "yes_ask", "yes_ask_dollars"))
    no_ask = _coerce_probability(bot, _candidate_get(bot, item, market, q, "no_ask", "no_ask_dollars"))
    if selected_price is not None:
        if yes_ask is not None and abs(selected_price - yes_ask) <= 0.000001:
            return "yes"
        if no_ask is not None and abs(selected_price - no_ask) <= 0.000001:
            return "no"
    yes_edge = bot.to_float(_candidate_get(bot, item, market, q, "yes_edge_after_cost", "yes_final_edge", "yes_edge"), None)
    no_edge = bot.to_float(_candidate_get(bot, item, market, q, "no_edge_after_cost", "no_final_edge", "no_edge"), None)
    if yes_edge is not None or no_edge is not None:
        if (yes_edge or 0.0) >= (no_edge or 0.0):
            return "yes"
        return "no"
    return "unknown"


def _momentum_log(bot, context, *, log=True, log_cooldown_seconds=20.0):
    if not log:
        return
    ticker = str(context.get("ticker") or "").strip()
    if not ticker:
        return
    adjustment = _clamp(context.get("adjustment"), -10.0, 10.0)
    direction = str(context.get("direction") or "unknown").strip().lower()
    now_ts = time.time()
    cycle_count = getattr(bot, "cycle_count", None)
    if getattr(bot, "last_momentum_engine_log_cycle", None) != cycle_count:
        bot.last_momentum_engine_log_cycle = cycle_count
        bot.last_momentum_engine_log_count = 0
    neutral_sample = abs(adjustment) < 0.05 and direction in {"unknown", "flat"}
    if neutral_sample and int(getattr(bot, "last_momentum_engine_log_count", 0) or 0) >= 4:
        return
    if int(getattr(bot, "last_momentum_engine_log_count", 0) or 0) >= 12:
        return
    log_store = getattr(bot, "momentum_engine_log_markers", None)
    if not isinstance(log_store, dict):
        bot.momentum_engine_log_markers = {}
        log_store = bot.momentum_engine_log_markers
    marker = (
        direction,
        str(context.get("side") or "unknown"),
        round(_clamp(context.get("momentum"), -10.0, 10.0), 3),
        round(adjustment, 3),
        str(context.get("reason") or "unknown"),
    )
    last = log_store.get(ticker, {}) if isinstance(log_store.get(ticker), dict) else {}
    last_ts = _clamp(last.get("ts"), 0.0, now_ts)
    if last.get("marker") == marker and now_ts - last_ts < max(1.0, float(log_cooldown_seconds or 20.0)):
        return
    log_store[ticker] = {"ts": now_ts, "marker": marker}
    bot.last_momentum_engine_log_count = int(getattr(bot, "last_momentum_engine_log_count", 0) or 0) + 1
    print(
        "[MOMENTUM-ENGINE] "
        f"ticker={ticker} "
        f"family={context.get('family') or 'UNKNOWN'} "
        f"side={context.get('side') or 'unknown'} "
        f"momentum={round(_clamp(context.get('momentum'), -10.0, 10.0), 4)} "
        f"direction={direction} "
        f"adjustment={round(adjustment, 4)} "
        f"reason={context.get('reason') or 'unknown'}"
    )


def get_momentum_rank_adjustment(
    bot,
    item=None,
    *,
    market=None,
    family=None,
    imbalance_context=None,
    btc_volatility_context=None,
    ev_context=None,
    source="ranking",
    log=True,
):
    market = market if isinstance(market, dict) else {}
    q = _candidate_q(item)
    if isinstance(item, dict):
        q = q or item.get("q") or item.get("quote")
        market = market or item.get("market") or {}
        family = family or item.get("family")
    elif isinstance(item, (list, tuple)):
        family = family or (item[3] if len(item) > 3 else None)
    ticker = str(
        (item.get("ticker") if isinstance(item, dict) else "")
        or getattr(q, "ticker", "")
        or market.get("ticker", "")
        or ""
    ).strip()
    family = str(family or "UNKNOWN").strip().upper() or "UNKNOWN"
    ev_context = ev_context if isinstance(ev_context, dict) else {}
    imbalance_context = imbalance_context if isinstance(imbalance_context, dict) else {}
    btc_volatility_context = btc_volatility_context if isinstance(btc_volatility_context, dict) else {}
    side = _selected_side(bot, item, market, q, ev_context=ev_context)
    series, series_source = _extract_yes_probability_series(bot, item, market, q)
    current_yes, current_source = _current_yes_probability(bot, item, market, q)
    if current_yes is not None and (not series or abs(series[-1] - current_yes) > 0.000001):
        series = list(series or []) + [current_yes]
    series = [value for value in series if value is not None]
    reason_parts = []
    delta = 0.0
    step_delta = 0.0
    acceleration = 0.0
    continuation = 0.0
    if len(series) >= 2:
        lookback = series[-min(len(series), 5)]
        previous = series[-2]
        current = series[-1]
        delta = round(current - lookback, 6)
        step_delta = round(current - previous, 6)
        direction = "up" if delta > 0.002 else "down" if delta < -0.002 else "flat"
        if len(series) >= 3:
            prev_step = round(series[-2] - series[-3], 6)
            acceleration = round(step_delta - prev_step, 6)
            if step_delta and prev_step and (step_delta > 0) == (prev_step > 0):
                continuation = min(1.0, abs(step_delta) / 0.012)
                reason_parts.append("directional_continuation")
        reason_parts.append(series_source or current_source)
    else:
        direction = "unknown"
        reason_parts.append("missing_history_neutral")

    if side == "yes":
        side_aligned_delta = delta
        side_aligned_step = step_delta
    elif side == "no":
        side_aligned_delta = -delta
        side_aligned_step = -step_delta
    else:
        side_aligned_delta = 0.0
        side_aligned_step = 0.0
        reason_parts.append("unknown_side_neutral")

    momentum = _clamp((side_aligned_delta / 0.02) * 0.70 + (side_aligned_step / 0.01) * 0.30, -1.0, 1.0)
    if continuation > 0.0 and momentum > 0.0:
        momentum = min(1.0, momentum + 0.18 * continuation)
    elif continuation > 0.0 and momentum < 0.0:
        momentum = max(-1.0, momentum - 0.08 * continuation)

    spread = bot.to_float(
        _candidate_get(bot, item, market, q, "spread", "spread_dollars", "bid_ask_spread"),
        None,
    )
    if spread is None:
        yes_bid = _coerce_probability(bot, _candidate_get(bot, item, market, q, "yes_bid", "yes_bid_dollars"))
        yes_ask = _coerce_probability(bot, _candidate_get(bot, item, market, q, "yes_ask", "yes_ask_dollars"))
        spread = (yes_ask - yes_bid) if yes_bid is not None and yes_ask is not None and yes_ask >= yes_bid else None
    spread_factor = 1.0
    if spread is not None:
        spread_factor = _clamp(1.08 - max(0.0, spread) / 0.12, 0.55, 1.08)
        if spread <= 0.03:
            reason_parts.append("tight_executable_spread")
    liquidity = bot.to_float(
        _candidate_get(bot, item, market, q, "liquidity", "liquidity_dollars", "volume"),
        0.0,
    ) or 0.0
    liquidity_factor = 1.0 + min(0.12, max(0.0, liquidity) / 25000.0)

    imbalance = bot.to_float(imbalance_context.get("imbalance"), 0.0) or 0.0
    if side == "no":
        side_imbalance = -imbalance
    elif side == "yes":
        side_imbalance = imbalance
    else:
        side_imbalance = 0.0
    if side_imbalance > 0.05:
        momentum += min(0.18, side_imbalance * 0.20)
        reason_parts.append("orderbook_pressure_aligned")
    elif side_imbalance < -0.10:
        momentum -= min(0.10, abs(side_imbalance) * 0.12)
        reason_parts.append("orderbook_pressure_opposed")
    momentum = _clamp(momentum, -1.0, 1.0)

    btc_direction = str(btc_volatility_context.get("direction") or "neutral").strip().lower()
    btc_preferred_side = str(btc_volatility_context.get("preferred_side") or "").strip().lower()
    btc_active = bool(btc_volatility_context.get("active"))
    if btc_active and side in {"yes", "no"}:
        if btc_preferred_side == side:
            momentum = min(1.0, momentum + 0.22 * _clamp(btc_volatility_context.get("volatility"), 0.0, 3.0) / 3.0)
            reason_parts.append(f"btc_{btc_direction}_aligned")
        elif btc_preferred_side in {"yes", "no"}:
            momentum = max(-1.0, momentum - 0.10 * _clamp(btc_volatility_context.get("volatility"), 0.0, 3.0) / 3.0)
            reason_parts.append(f"btc_{btc_direction}_opposed")

    ev_edge = bot.to_float(ev_context.get("ev_edge"), None)
    if ev_edge is not None and ev_edge > 0.0 and momentum > 0.0:
        momentum = min(1.0, momentum + min(0.10, ev_edge * 0.40))
        reason_parts.append("positive_ev_confirms")

    positive_adjustment = 2.20 * max(0.0, momentum) * spread_factor * liquidity_factor
    negative_adjustment = 0.75 * min(0.0, momentum) * (0.90 if spread_factor >= 0.9 else 1.0)
    adjustment = round(_clamp(positive_adjustment + negative_adjustment, -0.75, 2.35), 4)
    if direction == "unknown" or side == "unknown":
        adjustment = 0.0
    if abs(adjustment) < 0.03:
        adjustment = 0.0
    if adjustment > 0:
        action = "rank_boost"
    elif adjustment < 0:
        action = "rank_soft_penalty"
    else:
        action = "neutral"
    reason = "+".join(dict.fromkeys(reason_parts)) or "neutral"
    context = {
        "ticker": ticker or None,
        "family": family,
        "side": side,
        "momentum": round(momentum, 4),
        "direction": direction,
        "adjustment": adjustment,
        "reason": reason,
        "action": action,
        "delta": round(delta, 6),
        "step_delta": round(step_delta, 6),
        "acceleration": round(acceleration, 6),
        "series_points": len(series),
        "series_source": series_source,
        "current_source": current_source,
        "spread_factor": round(spread_factor, 4),
        "liquidity_factor": round(liquidity_factor, 4),
        "source": source,
    }
    for target in (item if isinstance(item, dict) else None,):
        if isinstance(target, dict):
            target["momentum_score"] = context["momentum"]
            target["momentum_direction"] = context["direction"]
            target["momentum_adjustment"] = context["adjustment"]
            target["momentum_reason"] = context["reason"]
    if q is not None:
        for field, value in (
            ("momentum_score", context["momentum"]),
            ("momentum_direction", context["direction"]),
            ("momentum_adjustment", context["adjustment"]),
            ("momentum_reason", context["reason"]),
        ):
            try:
                setattr(q, field, value)
            except Exception:
                pass
    _momentum_log(bot, context, log=log)
    return context


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
        if hasattr(bot, "_get_structural_edge_rank_adjustment"):
            structural_edge_context = bot._get_structural_edge_rank_adjustment(
                item,
                market=market,
                family=family,
                markets=markets,
                source=source,
                log=True,
            )
        else:
            structural_edge_context = {"boost": 0.0}
        if hasattr(bot, "_get_elite_public_signal_rank_adjustment"):
            elite_signal_context = bot._get_elite_public_signal_rank_adjustment(
                item,
                market=market,
                family=family,
                source=source,
                markets=markets,
                log=True,
            )
        else:
            elite_signal_context = {"boost": 0.0}
        if hasattr(bot, "_get_sports_edge_rank_adjustment"):
            sports_edge_context = bot._get_sports_edge_rank_adjustment(
                item,
                market=market,
                family=family,
                source=source,
                log=True,
            )
        else:
            sports_edge_context = {"boost": 0.0}
        ev_context = bot._attach_ev_score_to_candidate(
            item,
            market=market,
            family=family,
            family_profit_memory_adjustment=family_adjustment,
            orderbook_imbalance_context=imbalance_context,
            structural_edge_context=structural_edge_context,
            source=source,
        )
        if hasattr(bot, "_get_sports_ml_probability_adjustment"):
            ml_probability_context = bot._get_sports_ml_probability_adjustment(
                item,
                market=market,
                family=family,
                base_probability=ev_context.get("estimated_win_probability") or ev_context.get("calibrated_probability"),
                base_edge=ev_context.get("ev_edge") or ev_context.get("expected_roi") or ev_context.get("expected_value"),
                source=source,
                log=True,
            )
        else:
            ml_probability_context = {"rank_adjustment": 0.0, "size_multiplier": 1.0}
        momentum_context = get_momentum_rank_adjustment(
            bot,
            item,
            market=market,
            family=family,
            imbalance_context=imbalance_context,
            btc_volatility_context=btc_volatility_context,
            ev_context=ev_context,
            source=source,
            log=True,
        )
        total_score = (
            bot._rank_score_for_family_profit_memory(item)
            + family_adjustment
            + bot.to_float(imbalance_context.get("adjustment"), 0.0)
            + bot.to_float(btc_volatility_context.get("adjustment"), 0.0)
            + bot.to_float(structural_edge_context.get("boost"), 0.0)
            + bot.to_float(elite_signal_context.get("boost"), 0.0)
            + bot.to_float(sports_edge_context.get("boost"), 0.0)
            + bot._ev_rank_adjustment(ev_context)
            + bot.to_float(ml_probability_context.get("rank_adjustment"), 0.0)
            + bot.to_float(momentum_context.get("adjustment"), 0.0)
        )
        decorated.append((total_score, -index, item))
    decorated.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [item for _total_score, _tie_breaker, item in decorated]

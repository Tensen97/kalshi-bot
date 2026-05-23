"""Dynamic exit helpers moved out of kalshi_web.py."""

import time


def coerce_live_exit_price(value, to_float):
    price = to_float(value, None)
    if price is None:
        return None
    if 0.0 < price <= 1.0:
        return round(price, 4)
    if 1.0 < price <= 100.0:
        return round(price / 100.0, 4)
    return None


def resolve_live_exit_quote_context(bot, position, quotes_by_ticker=None):
    position = position if isinstance(position, dict) else {}
    quotes_by_ticker = quotes_by_ticker if isinstance(quotes_by_ticker, dict) else {}
    ticker = str(position.get("ticker") or "").strip()
    q = quotes_by_ticker.get(ticker)
    market = {}
    source = "quotes_by_ticker" if q is not None else "none"
    reason = None
    if q is None and ticker:
        try:
            market = bot.get_live_market_detail(ticker)
            if isinstance(market, dict) and market:
                q = bot.build_quote(market)
                source = "market_detail"
            else:
                reason = "market_detail_empty"
        except Exception as exc:
            reason = f"market_detail_{exc.__class__.__name__}"
    return q, market if isinstance(market, dict) else {}, source, reason


def get_live_exit_position_metadata(bot, position, *, default_live_order_side):
    position = position if isinstance(position, dict) else {}
    ticker = str(position.get("ticker") or "").strip()
    metadata = bot.get_live_entry_metadata(ticker) if ticker else None
    metadata = metadata if isinstance(metadata, dict) else {}
    side = bot._normalize_live_side(position.get("side") or metadata.get("side")) or default_live_order_side
    entry_price = bot._coerce_live_exit_price(
        metadata.get("entry_price")
        if metadata.get("entry_price") is not None
        else position.get("entry_price")
        if position.get("entry_price") is not None
        else position.get("avg_price")
    )
    count = abs(bot.to_float(position.get("count"), bot.to_float(metadata.get("count"), 1.0)) or 1.0)
    entry_ts = bot.to_float(metadata.get("entry_ts"), bot.to_float(position.get("entry_ts"), None))
    if entry_ts is None:
        entry_ts = bot.to_float(position.get("updated_ts"), None)
    return {
        "ticker": ticker,
        "family": bot.get_market_family({"ticker": ticker}) if ticker else "UNKNOWN",
        "side": side if side in {"yes", "no"} else default_live_order_side,
        "entry_price": entry_price,
        "count": count,
        "entry_ts": entry_ts,
        "metadata": metadata,
    }


def get_live_exit_price_context(bot, q, side, *, default_live_order_side):
    side = bot._normalize_live_side(side) or default_live_order_side
    yes_bid = bot._coerce_live_exit_price(bot._read_field(q, "yes_bid", "yes_bid_dollars", default=None))
    yes_ask = bot._coerce_live_exit_price(bot._read_field(q, "yes_ask", "yes_ask_dollars", default=None))
    no_bid = bot._coerce_live_exit_price(bot._read_field(q, "no_bid", "no_bid_dollars", default=None))
    no_ask = bot._coerce_live_exit_price(bot._read_field(q, "no_ask", "no_ask_dollars", default=None))
    last_price = bot._coerce_live_exit_price(bot._read_field(q, "last_price", "last_price_dollars", default=None))
    if yes_bid is None and no_ask is not None:
        yes_bid = round(max(0.0, 1.0 - no_ask), 4)
    if yes_ask is None and no_bid is not None:
        yes_ask = round(max(0.0, 1.0 - no_bid), 4)
    if no_bid is None and yes_ask is not None:
        no_bid = round(max(0.0, 1.0 - yes_ask), 4)
    if no_ask is None and yes_bid is not None:
        no_ask = round(max(0.0, 1.0 - yes_bid), 4)
    if side == "yes":
        exit_price = yes_bid
        mark_price = yes_bid if yes_bid is not None else last_price
        side_ask = yes_ask
    else:
        exit_price = no_bid
        mark_price = no_bid if no_bid is not None else (round(max(0.0, 1.0 - last_price), 4) if last_price is not None else None)
        side_ask = no_ask
    spread = None
    if exit_price is not None and side_ask is not None and side_ask >= exit_price:
        spread = round(side_ask - exit_price, 4)
    return {
        "current_price": mark_price,
        "exit_price": exit_price,
        "spread": spread,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
    }


def record_live_exit_price_observation(bot, ticker, price, now_ts=None):
    ticker = str(ticker or "").strip()
    price = bot._coerce_live_exit_price(price)
    now_ts = float(now_ts or time.time())
    if not ticker or price is None:
        return {"recent_delta": 0.0, "last_step_delta": 0.0, "samples": 0}
    history_map = getattr(bot, "live_exit_price_history", {})
    if not isinstance(history_map, dict):
        history_map = {}
    history = [
        item for item in list(history_map.get(ticker, []) or [])
        if isinstance(item, dict) and now_ts - float(item.get("ts", 0.0) or 0.0) <= 900.0
    ]
    history.append({"ts": now_ts, "price": price})
    history = history[-8:]
    history_map[ticker] = history
    bot.live_exit_price_history = history_map
    recent_delta = 0.0
    last_step_delta = 0.0
    if len(history) >= 2:
        recent_delta = round(price - bot.to_float(history[0].get("price"), price), 4)
        last_step_delta = round(price - bot.to_float(history[-2].get("price"), price), 4)
    return {"recent_delta": recent_delta, "last_step_delta": last_step_delta, "samples": len(history)}


def log_dynamic_exit_decision(bot, decision, *, log_cooldown_seconds):
    decision = decision if isinstance(decision, dict) else {}
    ticker = str(decision.get("ticker") or "unknown").strip() or "unknown"
    action = str(decision.get("action") or "hold").strip().lower() or "hold"
    reason = str(decision.get("reason") or "unknown").strip().lower() or "unknown"
    pnl = bot.to_float(decision.get("unrealized_pnl"), 0.0) or 0.0
    hold_seconds = bot.to_float(decision.get("hold_seconds"), 0.0) or 0.0
    marker = (
        action,
        reason,
        round(pnl, 4),
        int(hold_seconds // 30),
    )
    now_ts = time.time()
    log_map = getattr(bot, "dynamic_exit_log_markers", {})
    if not isinstance(log_map, dict):
        log_map = {}
    previous = log_map.get(ticker)
    previous_ts = bot.to_float(previous.get("ts"), 0.0) if isinstance(previous, dict) else 0.0
    if isinstance(previous, dict) and previous.get("marker") == marker and now_ts - previous_ts < log_cooldown_seconds:
        return
    log_map[ticker] = {"marker": marker, "ts": now_ts}
    bot.dynamic_exit_log_markers = log_map
    print(
        f"[DYNAMIC-EXIT] ticker={ticker} pnl={round(pnl, 4)} "
        f"hold={int(max(0.0, hold_seconds))} action={action} reason={reason}"
    )


def evaluate_dynamic_live_exit(
    bot,
    position,
    quotes_by_ticker=None,
    *,
    default_live_order_side,
    orderbook_neutral_band,
    max_exit_spread,
    momentum_epsilon,
    min_profit,
    weak_profit_multiplier,
    runner_multiplier,
    adverse_stop_multiplier,
    severe_stop_multiplier,
    loss_exit_confirmation_checks,
    dynamic_exit_enabled,
    fast_cut_min_hold_seconds,
    min_hold_seconds,
    log_cooldown_seconds,
):
    now_ts = time.time()
    position = position if isinstance(position, dict) else {}
    metadata = get_live_exit_position_metadata(
        bot,
        position,
        default_live_order_side=default_live_order_side,
    )
    ticker = metadata.get("ticker")
    side = metadata.get("side")
    family = metadata.get("family")
    entry_price = metadata.get("entry_price")
    count = bot.to_float(metadata.get("count"), 1.0) or 1.0
    entry_ts = bot.to_float(metadata.get("entry_ts"), None)
    hold_seconds = max(0.0, now_ts - entry_ts) if entry_ts is not None else 0.0
    q, market, quote_source, quote_reason = resolve_live_exit_quote_context(bot, position, quotes_by_ticker=quotes_by_ticker)
    price_context = get_live_exit_price_context(bot, q, side, default_live_order_side=default_live_order_side) if q is not None else {}
    current_price = bot.to_float(price_context.get("current_price"), None)
    exit_price = bot.to_float(price_context.get("exit_price"), None)
    spread = bot.to_float(price_context.get("spread"), None)
    if spread is None and q is not None:
        spread = bot.to_float(bot._read_field(q, "spread", default=None), None)
    if not ticker:
        decision = {"ticker": "unknown", "action": "hold", "reason": "missing_ticker", "unrealized_pnl": 0.0, "hold_seconds": hold_seconds}
        log_dynamic_exit_decision(bot, decision, log_cooldown_seconds=log_cooldown_seconds)
        return decision
    if entry_price is None or current_price is None:
        decision = {
            "ticker": ticker,
            "family": family,
            "side": side,
            "action": "hold",
            "reason": quote_reason or "missing_price_context",
            "unrealized_pnl": 0.0,
            "hold_seconds": hold_seconds,
            "entry_price": entry_price,
            "current_price": current_price,
            "exit_price": exit_price,
        }
        log_dynamic_exit_decision(bot, decision, log_cooldown_seconds=log_cooldown_seconds)
        return decision

    pnl_per_contract = round(current_price - entry_price, 4)
    unrealized_pnl = round(pnl_per_contract * count, 4)
    history = record_live_exit_price_observation(bot, ticker, current_price, now_ts=now_ts)
    recent_delta = bot.to_float(history.get("recent_delta"), 0.0) or 0.0
    last_step_delta = bot.to_float(history.get("last_step_delta"), 0.0) or 0.0
    imbalance_context = bot._extract_orderbook_imbalance_context(
        {"ticker": ticker, "side": side, "spread": spread},
        market=market,
        q=q,
        spread=spread,
        family=family,
    )
    imbalance = bot.to_float(imbalance_context.get("imbalance"), 0.0) or 0.0
    side_support = imbalance if side == "yes" else -imbalance
    neutral_band = max(0.0, min(0.95, orderbook_neutral_band))
    spread_ok = bool(spread is None or spread <= max_exit_spread)
    momentum_eps = max(0.0005, momentum_epsilon)
    weak_momentum = bool(recent_delta <= -momentum_eps or last_step_delta <= -momentum_eps or side_support < -neutral_band)
    strong_momentum = bool(recent_delta >= momentum_eps and last_step_delta >= 0.0 and side_support > neutral_band and spread_ok)
    adverse_movement = bool(pnl_per_contract < 0.0 and (recent_delta <= -momentum_eps or side_support < -neutral_band))
    base_take_profit = max(0.001, bot.to_float(getattr(bot, "take_profit", 0.04), 0.04) or 0.04)
    base_stop_loss = max(0.001, bot.to_float(getattr(bot, "stop_loss", 0.03), 0.03) or 0.03)
    weak_take_profit = max(min_profit, base_take_profit * max(0.1, weak_profit_multiplier))
    runner_take_profit = base_take_profit * max(1.0, runner_multiplier)
    dynamic_take_profit = runner_take_profit if strong_momentum else weak_take_profit if weak_momentum else base_take_profit
    dynamic_stop_loss = -base_stop_loss
    if adverse_movement:
        dynamic_stop_loss = -max(0.01, base_stop_loss * max(0.1, adverse_stop_multiplier))
    severe_stop_loss = -base_stop_loss * max(1.0, severe_stop_multiplier)

    action = "hold"
    reason = "existing_hold_logic"
    required_loss_confirmations = int(loss_exit_confirmation_checks or 1)
    if not dynamic_exit_enabled:
        reason = "dynamic_exit_disabled"
    elif exit_price is None or exit_price <= 0.0:
        reason = "missing_executable_exit_bid"
    elif spread is not None and spread > max_exit_spread and pnl_per_contract > severe_stop_loss:
        reason = "exit_spread_too_wide"
    elif pnl_per_contract >= runner_take_profit:
        action = "exit"
        reason = "runner_profit_target"
    elif pnl_per_contract >= base_take_profit and strong_momentum:
        reason = "runner_strong_momentum"
    elif pnl_per_contract >= dynamic_take_profit and weak_momentum:
        action = "exit"
        reason = "profit_momentum_weakened"
    elif pnl_per_contract >= base_take_profit and not strong_momentum:
        action = "exit"
        reason = "take_profit"
    elif pnl_per_contract <= severe_stop_loss and hold_seconds >= fast_cut_min_hold_seconds:
        action = "exit"
        reason = "severe_stop_loss"
        required_loss_confirmations = 1
    elif pnl_per_contract <= dynamic_stop_loss and adverse_movement and hold_seconds >= fast_cut_min_hold_seconds:
        action = "exit"
        reason = "adverse_fast_cut"
        required_loss_confirmations = 1
    elif pnl_per_contract <= -base_stop_loss and hold_seconds >= min_hold_seconds:
        action = "exit"
        reason = "stop_loss"
    elif pnl_per_contract > 0.0 and weak_momentum and pnl_per_contract >= weak_take_profit:
        action = "exit"
        reason = "protect_profit"

    if action == "exit" and pnl_per_contract < 0.0:
        loss_counts = getattr(bot, "live_loss_exit_confirmation_counts", {})
        if not isinstance(loss_counts, dict):
            loss_counts = {}
        current_count = int(loss_counts.get(ticker, 0) or 0) + 1
        loss_counts[ticker] = current_count
        bot.live_loss_exit_confirmation_counts = loss_counts
        if current_count < max(1, required_loss_confirmations):
            action = "hold"
            reason = f"loss_confirmation_{current_count}_of_{max(1, required_loss_confirmations)}"
    elif pnl_per_contract >= 0.0:
        try:
            bot.live_loss_exit_confirmation_counts.pop(ticker, None)
        except Exception:
            pass

    decision = {
        "ticker": ticker,
        "family": family,
        "side": side,
        "action": action,
        "reason": reason,
        "entry_price": entry_price,
        "current_price": current_price,
        "exit_price": exit_price,
        "spread": spread,
        "count": count,
        "hold_seconds": hold_seconds,
        "pnl_per_contract": pnl_per_contract,
        "unrealized_pnl": unrealized_pnl,
        "recent_delta": recent_delta,
        "last_step_delta": last_step_delta,
        "imbalance": imbalance,
        "side_support": side_support,
        "dynamic_take_profit": round(dynamic_take_profit, 4),
        "dynamic_stop_loss": round(dynamic_stop_loss, 4),
        "quote_source": quote_source,
    }
    log_dynamic_exit_decision(bot, decision, log_cooldown_seconds=log_cooldown_seconds)
    return decision


def build_live_exit_order_payload(
    bot,
    position,
    decision,
    *,
    default_live_order_side,
    max_live_order_size,
):
    position = position if isinstance(position, dict) else {}
    decision = decision if isinstance(decision, dict) else {}
    ticker = str(decision.get("ticker") or position.get("ticker") or "").strip()
    side = bot._normalize_live_side(decision.get("side") or position.get("side")) or default_live_order_side
    count = int(max(1, min(int(bot.to_float(decision.get("count"), bot.to_float(position.get("count"), 1.0)) or 1), int(max_live_order_size or 1))))
    exit_price = bot._coerce_live_exit_price(decision.get("exit_price"))
    if not ticker or side not in {"yes", "no"} or exit_price is None:
        return None
    price_cents = max(1, min(99, int(round(exit_price * 100.0))))
    payload = {
        "ticker": ticker,
        "side": side,
        "selected_side": side,
        "action": "sell",
        "count": count,
        "type": "limit",
        "time_in_force": "fill_or_kill",
        "post_only": False,
        "rest_remainder": True,
        "reduce_only": True,
        "price": round(price_cents / 100.0, 4),
        "selected_price": round(price_cents / 100.0, 4),
        "selected_price_dollars": round(price_cents / 100.0, 4),
        "selected_price_cents": price_cents,
    }
    if side == "yes":
        payload["yes_price"] = price_cents
    else:
        payload["no_price"] = price_cents
    return bot._ensure_live_client_order_id(payload)

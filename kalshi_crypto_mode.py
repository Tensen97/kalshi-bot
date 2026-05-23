"""BTC and crypto volatility helpers moved out of kalshi_web.py."""

import time


def is_btc_volatility_family(family_key=None, ticker_key=None):
    combined = f"{str(family_key or '').strip().upper()} {str(ticker_key or '').strip().upper()}"
    return bool(
        "KXBTC" in combined
        or "BTC" in combined
        or "CRYPTO" in combined
        or "ETH" in combined
    )


def coerce_btc_volatility_price(bot, value):
    try:
        coerced = bot._coerce_live_exit_price(value)
        if coerced is not None:
            return coerced
    except Exception:
        pass
    price = bot.to_float(value, None)
    if price is None:
        return None
    if 0.0 < price <= 1.0:
        return round(price, 4)
    if 1.0 < price <= 100.0:
        return round(price / 100.0, 4)
    return None


def extract_btc_volatility_price_context(bot, *, market=None, q=None, selected_side=None):
    market = market if isinstance(market, dict) else {}

    def _pick(*values):
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return None

    def _q_get(*names):
        for name in names:
            if q is not None:
                value = getattr(q, name, None)
                if value is not None and not (isinstance(value, str) and not value.strip()):
                    return value
        return None

    def _market_get(*names):
        for name in names:
            if name in market:
                value = market.get(name)
                if value is not None and not (isinstance(value, str) and not value.strip()):
                    return value
        return None

    side = bot._normalize_orderbook_imbalance_side(
        selected_side
        or _q_get("selected_side", "side")
        or _market_get("selected_side", "side")
    )
    yes_bid = coerce_btc_volatility_price(bot, _pick(_q_get("yes_bid", "yes_bid_dollars"), _market_get("yes_bid_dollars", "yes_bid")))
    yes_ask = coerce_btc_volatility_price(bot, _pick(_q_get("yes_ask", "yes_ask_dollars"), _market_get("yes_ask_dollars", "yes_ask")))
    no_bid = coerce_btc_volatility_price(bot, _pick(_q_get("no_bid", "no_bid_dollars"), _market_get("no_bid_dollars", "no_bid")))
    no_ask = coerce_btc_volatility_price(bot, _pick(_q_get("no_ask", "no_ask_dollars"), _market_get("no_ask_dollars", "no_ask")))
    selected_price = coerce_btc_volatility_price(
        bot,
        _pick(
            _q_get("selected_price", "selected_price_dollars"),
            _market_get("selected_price", "selected_price_dollars"),
        ),
    )
    last_price = coerce_btc_volatility_price(
        bot,
        _pick(
            _q_get("last_price", "last_price_dollars", "price"),
            _market_get("last_price_dollars", "last_price", "price"),
        ),
    )
    yes_mid = None
    no_mid = None
    if yes_bid is not None and yes_ask is not None and yes_bid > 0.0 and yes_ask > 0.0:
        yes_mid = round((yes_bid + yes_ask) / 2.0, 4)
    if no_bid is not None and no_ask is not None and no_bid > 0.0 and no_ask > 0.0:
        no_mid = round((no_bid + no_ask) / 2.0, 4)
    if last_price is not None:
        yes_probability = last_price if side != "no" else round(1.0 - last_price, 4)
        source = "last_price"
    elif selected_price is not None and side in {"yes", "no"}:
        yes_probability = selected_price if side == "yes" else round(1.0 - selected_price, 4)
        source = "selected_price"
    elif yes_mid is not None:
        yes_probability = yes_mid
        source = "yes_mid"
    elif no_mid is not None:
        yes_probability = round(1.0 - no_mid, 4)
        source = "no_mid_inverse"
    elif yes_ask is not None:
        yes_probability = yes_ask
        source = "yes_ask"
    elif yes_bid is not None:
        yes_probability = yes_bid
        source = "yes_bid"
    elif no_ask is not None:
        yes_probability = round(1.0 - no_ask, 4)
        source = "no_ask_inverse"
    elif no_bid is not None:
        yes_probability = round(1.0 - no_bid, 4)
        source = "no_bid_inverse"
    else:
        yes_probability = None
        source = "missing"
    if yes_probability is not None:
        yes_probability = round(bot._clamp(yes_probability, 0.0, 1.0), 4)
    return {
        "yes_probability": yes_probability,
        "source": source,
        "selected_side": side,
        "selected_price": selected_price,
        "last_price": last_price,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_mid": yes_mid,
        "no_mid": no_mid,
    }


def get_btc_volatility_mode(
    bot,
    *,
    market=None,
    q=None,
    family=None,
    spread=None,
    source="ranking",
    log=True,
    mode_enabled=True,
    price_delta_trigger=0.018,
    velocity_trigger=0.00045,
    imbalance_trigger=0.28,
    max_rank_boost=0.0,
    score_boost=0.0,
    threshold_relaxation=0.0,
    spread_relaxation=0.0,
    log_cooldown_seconds=20.0,
):
    ticker = str(
        getattr(q, "ticker", "")
        or ((market or {}).get("ticker") if isinstance(market, dict) else "")
        or ""
    ).strip()
    family_key = bot._normalize_family_for_profit_memory(family, ticker=ticker, market=market if isinstance(market, dict) else None, q=q)
    if not is_btc_volatility_family(family_key, ticker):
        return {
            "active": False,
            "direction": "neutral",
            "volatility": 0.0,
            "preferred_side": None,
            "rank_boost": 0.0,
            "score_boost": 0.0,
            "threshold_relaxation": 0.0,
            "spread_relaxation": 0.0,
            "source": source,
        }
    ticker_key = ticker or family_key or "BTC"
    now_ts = time.time()
    price_context = extract_btc_volatility_price_context(bot, market=market, q=q)
    price = bot.to_float(price_context.get("yes_probability"), None)
    imbalance_context = bot._extract_orderbook_imbalance_context(
        None,
        market=market,
        q=q,
        spread=spread,
        family=family_key,
    )
    imbalance = bot.to_float(imbalance_context.get("imbalance"), 0.0) or 0.0
    state_store = getattr(bot, "btc_volatility_state", None)
    if not isinstance(state_store, dict):
        bot.btc_volatility_state = {}
        state_store = bot.btc_volatility_state
    state = dict(state_store.get(ticker_key, {}) or {})
    history = list(state.get("history") or [])
    cycle_id = None
    try:
        cycle_id = bot._get_phase55_cycle_id()
    except Exception:
        cycle_id = None
    if price is not None:
        last_price = bot.to_float(state.get("last_price"), None)
        same_cycle = bool(cycle_id and state.get("last_cycle_id") == cycle_id)
        if not same_cycle or last_price is None or abs(price - last_price) >= 0.0001:
            history.append({"ts": now_ts, "price": price, "source": price_context.get("source")})
        history = [
            item for item in history[-16:]
            if now_ts - bot.to_float((item or {}).get("ts"), now_ts) <= 900.0
            and bot.to_float((item or {}).get("price"), None) is not None
        ]
        state.update({
            "history": history,
            "last_price": price,
            "last_cycle_id": cycle_id,
            "updated_ts": now_ts,
            "last_source": price_context.get("source"),
        })
        state_store[ticker_key] = state

    delta = 0.0
    step_delta = 0.0
    velocity = 0.0
    step_velocity = 0.0
    if price is not None and len(history) >= 2:
        base = history[0]
        previous = history[-2]
        base_price = bot.to_float(base.get("price"), price)
        previous_price = bot.to_float(previous.get("price"), price)
        elapsed = max(1.0, now_ts - bot.to_float(base.get("ts"), now_ts))
        step_elapsed = max(1.0, now_ts - bot.to_float(previous.get("ts"), now_ts))
        delta = round(price - base_price, 6)
        step_delta = round(price - previous_price, 6)
        velocity = round(delta / elapsed, 8)
        step_velocity = round(step_delta / step_elapsed, 8)

    price_trigger = max(0.001, float(price_delta_trigger or 0.018))
    velocity_trigger = max(0.00001, float(velocity_trigger or 0.00045))
    imbalance_trigger = max(0.05, min(0.95, float(imbalance_trigger or 0.28)))
    signal_candidates = [
        ("price_delta", delta / price_trigger if price is not None else 0.0),
        ("step_delta", step_delta / max(price_trigger * 0.50, 0.001) if price is not None else 0.0),
        ("velocity", velocity / velocity_trigger if price is not None else 0.0),
        ("step_velocity", step_velocity / velocity_trigger if price is not None else 0.0),
        ("book_pressure", imbalance / imbalance_trigger if imbalance_context.get("total_size", 0.0) else 0.0),
    ]
    signal_name, signal_ratio = max(signal_candidates, key=lambda item: abs(item[1]))
    volatility = round(min(3.0, max(abs(ratio) for _name, ratio in signal_candidates)), 4)
    active = bool(mode_enabled and volatility >= 1.0 and signal_ratio != 0.0)
    direction = "pump" if active and signal_ratio > 0.0 else "crash" if active and signal_ratio < 0.0 else "neutral"
    preferred_side = "yes" if direction == "pump" else "no" if direction == "crash" else None
    rank_boost = round((min(1.0, volatility / 2.0) * float(max_rank_boost or 0.0)) if active else 0.0, 4)
    score_boost = round((min(1.0, volatility / 2.0) * float(score_boost or 0.0)) if active else 0.0, 4)
    threshold_relaxation = round((min(1.0, volatility / 2.0) * float(threshold_relaxation or 0.0)) if active else 0.0, 4)
    spread_relaxation = round((min(1.0, volatility / 2.0) * float(spread_relaxation or 0.0)) if active else 0.0, 4)
    mode = {
        "active": active,
        "direction": direction,
        "volatility": volatility,
        "preferred_side": preferred_side,
        "rank_boost": rank_boost,
        "score_boost": score_boost,
        "threshold_relaxation": threshold_relaxation,
        "spread_relaxation": spread_relaxation,
        "signal": signal_name,
        "signal_ratio": round(signal_ratio, 4),
        "price": price,
        "price_source": price_context.get("source"),
        "delta": round(delta, 6),
        "step_delta": round(step_delta, 6),
        "velocity": round(velocity, 8),
        "step_velocity": round(step_velocity, 8),
        "imbalance": round(imbalance, 4),
        "ticker": ticker_key,
        "family": family_key,
        "source": source,
    }
    if log:
        log_store = getattr(bot, "btc_volatility_log_markers", None)
        if not isinstance(log_store, dict):
            bot.btc_volatility_log_markers = {}
            log_store = bot.btc_volatility_log_markers
        last_marker = log_store.get(ticker_key, {}) if isinstance(log_store.get(ticker_key, {}), dict) else {}
        marker = (active, direction, round(volatility, 2), preferred_side, signal_name)
        last_ts = bot.to_float(last_marker.get("ts"), 0.0) or 0.0
        if last_marker.get("marker") != marker or now_ts - last_ts >= max(1.0, float(log_cooldown_seconds or 20.0)):
            log_store[ticker_key] = {"ts": now_ts, "marker": marker}
            print(
                f"[BTC-VOLATILITY-MODE] active={'yes' if active else 'no'} "
                f"direction={direction} volatility={volatility} selected={ticker_key}"
            )
    return mode


def get_btc_volatility_rank_adjustment(
    bot,
    item=None,
    *,
    market=None,
    q=None,
    spread=None,
    family=None,
    log=True,
    source="ranking",
    mode_kwargs=None,
    max_entry_spread=0.0,
    max_effective_spread=0.0,
    spread_relaxation=0.0,
    max_rank_boost=0.0,
    price_delta_trigger=0.018,
    velocity_trigger=0.00045,
    imbalance_trigger=0.28,
):
    if item is not None and isinstance(item, (list, tuple)):
        q = q or (item[1] if len(item) > 1 else None)
        spread = spread if spread is not None else (item[2] if len(item) > 2 else None)
        family = family or (item[3] if len(item) > 3 else None)
    elif isinstance(item, dict):
        q = q or item.get("q") or item.get("quote")
        spread = spread if spread is not None else item.get("spread")
        family = family or item.get("family")
        market = market or item.get("market")
    mode = get_btc_volatility_mode(
        bot,
        market=market,
        q=q,
        family=family,
        spread=spread,
        source=source,
        log=log,
        **(mode_kwargs if isinstance(mode_kwargs, dict) else {}),
    )
    adjustment = 0.0
    if mode.get("active"):
        imbalance_context = bot._extract_orderbook_imbalance_context(
            item,
            market=market,
            q=q,
            spread=spread,
            family=family,
        )
        side = bot._normalize_orderbook_imbalance_side(mode.get("preferred_side"))
        side_support = bot.to_float(imbalance_context.get("imbalance"), 0.0) or 0.0
        if side == "no":
            side_support = -side_support
        spread_value = bot.to_float(imbalance_context.get("spread"), spread)
        if spread_value is None:
            spread_factor = 1.0
        else:
            spread_limit = max(max_entry_spread, min(float(max_effective_spread or max_entry_spread), max_entry_spread + float(spread_relaxation or 0.0)))
            spread_factor = max(0.45, min(1.0, 1.0 - max(0.0, spread_value) / max(spread_limit * 1.5, 0.01)))
        executable = bool(imbalance_context.get("yes_executable")) if side == "yes" else bool(imbalance_context.get("no_executable")) if side == "no" else True
        executable_factor = 1.0 if executable else 0.55
        volatility_strength = min(1.0, bot.to_float(mode.get("volatility"), 0.0) / 2.0)
        momentum_strength = min(
            1.0,
            max(
                abs(bot.to_float(mode.get("delta"), 0.0)) / max(float(price_delta_trigger or 0.018), 0.001),
                abs(bot.to_float(mode.get("velocity"), 0.0)) / max(float(velocity_trigger or 0.00045), 0.00001),
            ),
        )
        pressure_strength = bot._clamp(max(0.0, side_support), 0.0, 1.0)
        adjustment = (
            float(max_rank_boost or 0.0)
            * (0.45 * volatility_strength + 0.35 * momentum_strength + 0.20 * pressure_strength)
            * spread_factor
            * executable_factor
        )
        if side_support < -float(imbalance_trigger or 0.28):
            adjustment -= min(1.5, float(max_rank_boost or 0.0) * 0.18 * min(1.0, abs(side_support)))
    mode["adjustment"] = round(float(adjustment or 0.0), 4)
    return mode

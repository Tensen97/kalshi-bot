"""Orderbook parsing and ranking helpers moved out of kalshi_web.py."""

import time


def calculate_topbook_imbalance(yes_top_size, no_top_size):
    total_size = max(0.0, yes_top_size) + max(0.0, no_top_size)
    imbalance = ((yes_top_size - no_top_size) / total_size) if total_size > 0.0 else 0.0
    pressure_side = "yes" if imbalance > 0.0 else "no" if imbalance < 0.0 else None
    return {
        "total_size": total_size,
        "imbalance": imbalance,
        "pressure_side": pressure_side,
    }


def normalize_orderbook_imbalance_side(side):
    side_value = str(side or "").strip().lower()
    if side_value in {"yes", "y"}:
        return "yes"
    if side_value in {"no", "n"}:
        return "no"
    return None


def extract_orderbook_imbalance_context(
    bot,
    item=None,
    *,
    market=None,
    q=None,
    spread=None,
    family=None,
    neutral_band=0.0,
):
    item = item if item is not None else {}
    market = market if isinstance(market, dict) else {}
    if isinstance(item, dict):
        q = q or item.get("q") or item.get("quote")
        family = family or item.get("family")
        spread = spread if spread is not None else item.get("spread")
        if not market and isinstance(item.get("market"), dict):
            market = item.get("market") or {}
    elif isinstance(item, (list, tuple)):
        q = q or (item[1] if len(item) > 1 else None)
        spread = spread if spread is not None else (item[2] if len(item) > 2 else None)
        family = family or (item[3] if len(item) > 3 else None)

    def _pick(*values):
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return None

    def _item_get(key):
        return item.get(key) if isinstance(item, dict) else None

    def _q_get(key):
        return getattr(q, key, None) if q is not None else None

    def _market_get(*keys):
        for key in keys:
            if key in market:
                value = market.get(key)
                if value is not None and not (isinstance(value, str) and not value.strip()):
                    return value
        return None

    ticker = str(_pick(_item_get("ticker"), _q_get("ticker"), _market_get("ticker")) or "").strip()
    family = bot._normalize_family_for_profit_memory(family, ticker=ticker, market=market, q=q)
    try:
        direction_decision = (bot.live_direction_decisions_by_ticker or {}).get(ticker, {}) or {}
    except Exception:
        direction_decision = {}

    yes_top_size = bot.to_float(
        _pick(_item_get("yes_top_size"), _q_get("yes_top_size"), _market_get("yes_top_size")),
        None,
    )
    no_top_size = bot.to_float(
        _pick(_item_get("no_top_size"), _q_get("no_top_size"), _market_get("no_top_size")),
        None,
    )
    if (yes_top_size is None or no_top_size is None) and market:
        try:
            liquidity_priority = bot.get_market_liquidity_priority(market, q=q)
            yes_top_size = bot.to_float(liquidity_priority.get("yes_top_size"), yes_top_size)
            no_top_size = bot.to_float(liquidity_priority.get("no_top_size"), no_top_size)
        except Exception:
            pass
    yes_top_size = bot.to_float(yes_top_size, 0.0) or 0.0
    no_top_size = bot.to_float(no_top_size, 0.0) or 0.0

    yes_bid = bot.to_float(_pick(_item_get("yes_bid"), _q_get("yes_bid"), _market_get("yes_bid_dollars", "yes_bid")), None)
    yes_ask = bot.to_float(_pick(_item_get("yes_ask"), _q_get("yes_ask"), _market_get("yes_ask_dollars", "yes_ask")), None)
    no_bid = bot.to_float(_pick(_item_get("no_bid"), _q_get("no_bid"), _market_get("no_bid_dollars", "no_bid")), None)
    no_ask = bot.to_float(_pick(_item_get("no_ask"), _q_get("no_ask"), _market_get("no_ask_dollars", "no_ask")), None)
    spread_value = bot.to_float(_pick(spread, _item_get("spread"), _q_get("spread"), _market_get("spread")), None)
    if spread_value is None and yes_bid is not None and yes_ask is not None and yes_bid > 0.0 and yes_ask > 0.0:
        spread_value = max(0.0, yes_ask - yes_bid)

    selected_side = normalize_orderbook_imbalance_side(
        _pick(
            _item_get("selected_side"),
            _item_get("side"),
            _q_get("selected_side"),
            _q_get("side"),
            direction_decision.get("selected_side"),
            direction_decision.get("intended_side"),
            _market_get("selected_side", "side"),
        )
    )
    imbalance_summary = calculate_topbook_imbalance(yes_top_size, no_top_size)
    total_size = imbalance_summary.get("total_size")
    imbalance = imbalance_summary.get("imbalance")
    pressure_side = imbalance_summary.get("pressure_side")
    if selected_side is None and abs(imbalance) >= max(0.0, neutral_band):
        selected_side = pressure_side

    selected_price = bot.to_float(
        _pick(
            _item_get("selected_price"),
            _item_get("selected_price_dollars"),
            _q_get("selected_price"),
            _q_get("selected_price_dollars"),
            direction_decision.get("selected_price"),
            _market_get("selected_price", "selected_price_dollars"),
        ),
        None,
    )
    if selected_price is None and selected_side == "yes":
        selected_price = bot.to_float(yes_ask, bot.to_float(yes_bid, None))
    elif selected_price is None and selected_side == "no":
        selected_price = bot.to_float(no_ask, bot.to_float(no_bid, None))

    yes_executable = bool((yes_ask is not None and yes_ask > 0.0) or (yes_bid is not None and yes_bid > 0.0))
    no_executable = bool((no_ask is not None and no_ask > 0.0) or (no_bid is not None and no_bid > 0.0))
    return {
        "ticker": ticker,
        "family": family,
        "side": selected_side or "unknown",
        "pressure_side": pressure_side or "unknown",
        "selected_price": selected_price,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_top_size": yes_top_size,
        "no_top_size": no_top_size,
        "spread": spread_value,
        "imbalance": imbalance,
        "total_size": total_size,
        "yes_executable": yes_executable,
        "no_executable": no_executable,
    }


def get_orderbook_imbalance_adjustment(
    bot,
    item=None,
    *,
    market=None,
    q=None,
    spread=None,
    family=None,
    log=True,
    source="ranking",
    enabled=True,
    neutral_band=0.0,
    min_total_size=0.0,
    max_rank_adjustment=0.0,
    max_rank_penalty=0.0,
    max_entry_spread=0.0,
):
    context = extract_orderbook_imbalance_context(
        bot,
        item,
        market=market,
        q=q,
        spread=spread,
        family=family,
        neutral_band=neutral_band,
    )
    side = normalize_orderbook_imbalance_side(context.get("side"))
    yes_size = bot.to_float(context.get("yes_top_size"), 0.0) or 0.0
    no_size = bot.to_float(context.get("no_top_size"), 0.0) or 0.0
    total_size = bot.to_float(context.get("total_size"), yes_size + no_size) or 0.0
    imbalance = bot.to_float(context.get("imbalance"), 0.0) or 0.0
    neutral_band = max(0.0, min(0.95, neutral_band))
    adjustment = 0.0
    if enabled and total_size >= max(0.0, min_total_size):
        if side is None and abs(imbalance) >= neutral_band:
            side = "yes" if imbalance > 0.0 else "no"
        if side in {"yes", "no"} and abs(imbalance) >= neutral_band:
            side_support = imbalance if side == "yes" else -imbalance
            strength = max(0.0, (abs(side_support) - neutral_band) / max(1.0 - neutral_band, 0.01))
            spread_value = bot.to_float(context.get("spread"), None)
            if spread_value is None:
                spread_factor = 1.0
            else:
                spread_factor = max(0.65, min(1.0, 1.0 - max(0.0, spread_value) / max(max_entry_spread * 2.0, 0.01)))
            depth_factor = min(1.0, total_size / max(min_total_size * 4.0, 1.0))
            executable = bool(context.get("yes_executable")) if side == "yes" else bool(context.get("no_executable"))
            executable_factor = 1.0 if executable or bot.to_float(context.get("selected_price"), 0.0) > 0.0 else 0.5
            if side_support > neutral_band:
                adjustment = (
                    max_rank_adjustment
                    * strength
                    * (0.65 + 0.35 * depth_factor)
                    * spread_factor
                    * executable_factor
                )
            elif side_support < -neutral_band:
                adjustment = -max_rank_penalty * strength
    adjustment = round(float(adjustment or 0.0), 4)
    context["side"] = side or context.get("side") or "unknown"
    context["adjustment"] = adjustment
    if log:
        ticker = str(context.get("ticker") or "unknown").strip() or "unknown"
        marker = (
            int(time.time() // 60),
            ticker,
            context.get("side"),
            round(yes_size, 4),
            round(no_size, 4),
            round(imbalance, 4),
            adjustment,
        )
        markers = getattr(bot, "orderbook_imbalance_log_markers", None)
        if not isinstance(markers, dict):
            bot.orderbook_imbalance_log_markers = {}
            markers = bot.orderbook_imbalance_log_markers
        if markers.get(ticker) != marker:
            markers[ticker] = marker
            print(
                f"[ORDERBOOK-IMBALANCE] ticker={ticker} side={context.get('side') or 'unknown'} "
                f"yes_size={round(yes_size, 4)} no_size={round(no_size, 4)} "
                f"imbalance={round(imbalance, 4)} adjustment={adjustment}"
            )
    return context

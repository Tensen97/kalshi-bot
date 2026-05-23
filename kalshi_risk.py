"""Hard live-trading risk guards for kalshi_web."""

from collections import Counter
import os
import time


def compute_effective_live_dry_run(
    live_mode=False,
    live_armed=False,
    live_env_confirmed=False,
    configured_dry_run=False,
    allow_live_raw=None,
):
    if allow_live_raw is None:
        allow_live_raw = os.environ.get("KALSHI_ALLOW_LIVE", "")
    allow_live_raw = (allow_live_raw or "").strip().lower()
    allow_live_enabled = allow_live_raw in {"1", "true", "yes", "on"}
    if live_armed and allow_live_enabled:
        return False
    return bool(configured_dry_run or not (live_mode and live_armed and live_env_confirmed))


def reset_live_trade_counter_if_needed(bot):
    today = time.strftime("%Y-%m-%d")
    if bot.live_trade_day != today:
        bot.live_trade_day = today
        bot.live_trade_count_today = 0


def active_live_order_timestamps(bot, *, live_order_rate_window_sec):
    now = time.time()
    window = float(live_order_rate_window_sec or 60)
    bot._live_order_timestamps = [
        ts for ts in (bot._live_order_timestamps or [])
        if now - float(ts or 0.0) < window
    ]
    return bot._live_order_timestamps


def get_live_submit_authorization(bot, payload=None, *, live_test_ticker=None):
    payload = payload if isinstance(payload, dict) else {}
    ticker = str(payload.get("ticker") or "").strip()
    flags = bot.get_live_execution_flags()
    supplied_ticker = str(live_test_ticker or "").strip() or None
    authorized_canary_ticker = str(getattr(bot, "live_canary_authorized_ticker", None) or "").strip() or None
    allowed_ticker = authorized_canary_ticker or supplied_ticker
    normal_live_service_mode = bool(flags["all_live_switches_on"] and not supplied_ticker)
    if not flags["live_mode"]:
        reason = "live_mode_disabled"
    elif not flags["live_armed"]:
        reason = "live_not_armed"
    elif not flags["live_env_confirmed"]:
        reason = "live_env_not_confirmed"
    elif flags["effective_live_dry_run"]:
        reason = "effective_dry_run"
    elif not normal_live_service_mode:
        if not allowed_ticker:
            reason = "no_live_test_ticker"
        elif ticker and ticker != allowed_ticker:
            reason = "ticker_not_allowed"
        elif bot.live_canary_submitted and not authorized_canary_ticker:
            reason = "canary_already_used"
        else:
            reason = "authorized_canary_ticker"
    else:
        reason = "authorized_live_service"
    return {
        "ok": reason in {"authorized_live_service", "authorized_canary_ticker"},
        "reason": reason,
        "flags": flags,
        "supplied_ticker": supplied_ticker,
        "authorized_canary_ticker": authorized_canary_ticker,
        "allowed_ticker": allowed_ticker,
        "normal_live_service_mode": normal_live_service_mode,
    }


def already_holding(bot, ticker):
    ticker = str(ticker or "").strip()
    if not ticker:
        return False
    if not bot.live_positions:
        bot._load_local_live_positions()
    return any(
        str(position.get("ticker") or "").strip() == ticker
        and str(position.get("status", "open") or "open").strip().lower() == "open"
        for position in (bot.live_positions or [])
        if isinstance(position, dict)
    )


def is_on_cooldown(bot, ticker):
    ticker = str(ticker or "").strip()
    if not ticker:
        return False
    until = bot.to_float((bot.market_cooldowns or {}).get(ticker), 0.0) or 0.0
    return time.time() < until


def passes_entry_filters(bot, q):
    ticker = str(bot._read_field(q, "ticker", default="") or "").strip()
    if not ticker:
        return False, "missing_ticker"
    if bot.trading_halted:
        return False, "trading_halted"
    return True, None


def can_open_new_position(bot, q, *, max_simultaneous_live_positions, max_live_trades_per_day):
    ticker = str(bot._read_field(q, "ticker", default="") or "").strip()
    if not ticker:
        return False, "missing_ticker"
    if bot.trading_halted:
        return False, "trading_halted"
    if bot.already_holding(ticker):
        return False, "already_holding"
    open_count = len(bot._get_open_live_positions())
    if open_count >= int(max_simultaneous_live_positions or 0):
        return False, "max_total_live_positions"
    bot._reset_live_trade_counter_if_needed()
    if int(bot.live_trade_count_today or 0) >= int(max_live_trades_per_day or 0):
        return False, "daily_trade_cap"
    return True, None


def should_enter_yes(bot, q, *, default_live_order_side):
    side = str(bot._read_field(q, "selected_side", "side", default=default_live_order_side) or "").strip().lower()
    return side in {"", "yes", "no", "buy_yes", "buy_no"}


def get_live_exposure_snapshot(bot, candidate_ticker=None, candidate_family=None):
    open_positions = bot._get_open_live_positions()
    by_family = Counter(
        bot.get_market_family({"ticker": position.get("ticker")})
        for position in open_positions
        if isinstance(position, dict)
    )
    return {
        "open_position_count": len(open_positions),
        "candidate_ticker": candidate_ticker,
        "candidate_family": candidate_family,
        "same_ticker_open": bool(candidate_ticker and bot.already_holding(candidate_ticker)),
        "positions_by_family": dict(by_family),
    }


def check_live_duplicate_position_api(
    bot,
    ticker,
    *,
    max_sports_live_positions,
    max_simultaneous_live_positions,
):
    ticker = str(ticker or "").strip()
    family = ticker.split("-")[0].upper() if ticker else "UNKNOWN"
    recent_order = bot.recent_submitted_order_for_ticker(ticker)
    pending_family_orders = bot.recent_submitted_order_count_for_family(family)
    max_pending_family_orders = int(
        max_sports_live_positions if bot._is_sports_market_or_family(family) else max_simultaneous_live_positions
    )
    pending_total_orders = bot.recent_submitted_order_count_total()
    max_pending_total_orders = int(max_simultaneous_live_positions or 0)
    family_pending_block = bool(max_pending_family_orders > 0 and pending_family_orders >= max_pending_family_orders)
    global_pending_block = bool(max_pending_total_orders > 0 and pending_total_orders >= max_pending_total_orders)
    blocked = bool(ticker and (bot.already_holding(ticker) or recent_order or family_pending_block or global_pending_block))
    matching = [
        position for position in bot._get_open_live_positions()
        if isinstance(position, dict) and str(position.get("ticker") or "").strip() == ticker
    ]
    reason = "clear"
    if recent_order:
        reason = "recent_order_pending_confirmation"
    elif family_pending_block:
        reason = "pending_family_order_limit"
    elif global_pending_block:
        reason = "pending_global_order_limit"
    elif blocked:
        reason = "duplicate_position_guard"
    return {
        "blocked": blocked,
        "reason": reason,
        "ticker": ticker or None,
        "open_live_matches": len(matching),
        "closed_live_matches": 0,
        "position_size": sum(bot.to_float(position.get("count"), 0.0) or 0.0 for position in matching),
        "simulated_duplicate": False,
        "recent_order": recent_order,
        "pending_family_orders": pending_family_orders,
        "max_pending_family_orders": max_pending_family_orders,
        "pending_total_orders": pending_total_orders,
        "max_pending_total_orders": max_pending_total_orders,
    }

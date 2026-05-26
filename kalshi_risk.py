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


def can_open_new_position(bot, q, *, max_simultaneous_live_positions, max_live_trades_per_day, max_daily_loss=0.0):
    ticker = str(bot._read_field(q, "ticker", default="") or "").strip()
    if not ticker:
        return False, "missing_ticker"
    if bot.trading_halted:
        return False, "trading_halted"
    max_daily_loss = float(max_daily_loss or getattr(bot, "max_daily_loss", 0.0) or 0.0)
    if max_daily_loss > 0.0:
        realized_pnl = bot.to_float(getattr(bot, "realized_pnl", 0.0), 0.0) or 0.0
        live_realized_pnl_today = bot.to_float(getattr(bot, "live_realized_pnl_today", 0.0), 0.0) or 0.0
        if realized_pnl <= -max_daily_loss or live_realized_pnl_today <= -max_daily_loss:
            return False, "daily_loss_guard"
    if bot.already_holding(ticker):
        return False, "already_holding"
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
    same_ticker_open = bool(ticker and bot.already_holding(ticker))
    blocked = bool(ticker and (same_ticker_open or recent_order))
    matching = [
        position for position in bot._get_open_live_positions()
        if isinstance(position, dict) and str(position.get("ticker") or "").strip() == ticker
    ]
    reason = "clear"
    if recent_order:
        reason = "recent_order_pending_confirmation"
    elif same_ticker_open:
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
        "pending_family_warning": family_pending_block,
        "pending_total_orders": pending_total_orders,
        "max_pending_total_orders": max_pending_total_orders,
        "pending_global_warning": global_pending_block,
    }


def _env_flag_enabled(*names):
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and str(raw).strip().lower() in {"1", "true", "yes", "on"}:
            return True, name
    return False, None


def _contract_price(bot, value):
    numeric = bot.to_float(value, None)
    if numeric is None:
        return None
    if 1.0 < numeric <= 100.0:
        numeric = numeric / 100.0
    return float(numeric) if 0.0 < float(numeric) < 1.0 else None


def _position_family_exposure(bot, family, fallback_price=None):
    family_key = str(family or "").strip().upper()
    if not family_key:
        return 0.0
    exposure = 0.0
    for position in bot._get_open_live_positions() or []:
        if not isinstance(position, dict):
            continue
        pos_ticker = str(position.get("ticker") or position.get("market_ticker") or "").strip()
        pos_family = str(position.get("family") or position.get("series_ticker") or "").strip().upper()
        if not pos_family and pos_ticker:
            try:
                pos_family = str(bot.get_market_family({"ticker": pos_ticker}) or "").strip().upper()
            except Exception:
                pos_family = ""
        if pos_family != family_key:
            continue
        count = bot.to_float(
            position.get("count")
            or position.get("contracts")
            or position.get("contract_count")
            or position.get("quantity")
            or position.get("qty"),
            0.0,
        ) or 0.0
        price = _contract_price(
            bot,
            position.get("entry_price")
            or position.get("price")
            or position.get("selected_price")
            or position.get("selected_price_dollars")
            or fallback_price,
        )
        if count > 0.0 and price is not None:
            exposure += float(count) * float(price)
    return max(0.0, exposure)


def evaluate_live_order_risk_governor(
    bot,
    payload,
    q=None,
    *,
    max_live_order_size,
    max_daily_loss,
    max_dollars_per_trade,
    max_family_exposure_dollars,
):
    payload = dict(payload or {})
    warnings = []
    cap_reasons = []
    kill_enabled, kill_env = _env_flag_enabled(
        "AION_RISK_EMERGENCY_KILL_SWITCH",
        "AION_EMERGENCY_KILL_SWITCH",
        "KALSHI_EMERGENCY_KILL_SWITCH",
        "AION_HALT_LIVE_TRADING",
    )
    ticker = str(payload.get("ticker") or getattr(q, "ticker", "") or "").strip()
    family = str(payload.get("family") or getattr(q, "family", "") or "").strip().upper()
    if not family and ticker:
        try:
            family = str(bot.get_market_family(payload or {"ticker": ticker}) or "").strip().upper()
        except Exception:
            family = ticker.split("-")[0].upper()
    side = str(payload.get("selected_side") or payload.get("side") or getattr(q, "selected_side", None) or getattr(q, "side", None) or "").strip().lower()
    selected_price = _contract_price(
        bot,
        payload.get("selected_price_dollars")
        or payload.get("selected_price")
        or payload.get("price")
        or (payload.get("yes_price") if side == "yes" else payload.get("no_price") if side == "no" else None),
    )
    count = int(max(0, bot.to_float(payload.get("count"), 0) or 0))

    def _block(reason):
        return {
            "ok": False,
            "reason": reason,
            "payload": payload,
            "ticker": ticker or None,
            "family": family or None,
            "side": side or None,
            "selected_price": selected_price,
            "final_count": count,
            "warnings": warnings,
            "cap_reasons": cap_reasons,
            "kill_switch_env": kill_env,
        }

    if kill_enabled:
        return _block("emergency_kill_switch")
    if not ticker:
        return _block("missing_ticker")
    if side not in {"yes", "no"}:
        return _block("missing_selected_side")
    if selected_price is None:
        return _block("invalid_selected_price")
    if count <= 0:
        return _block("invalid_order_size")
    if bool(payload.get("synthetic_used")) or str(payload.get("orderbook_source") or "").strip().lower() in {"synthetic", "fake", "fake_book"}:
        return _block("synthetic_or_fake_orderbook")
    malformed_reason = str(
        payload.get("orderbook_missing_reason")
        or payload.get("failure_reason")
        or payload.get("quote_ready_reason")
        or ""
    ).strip().lower()
    if malformed_reason in {"fake_book", "malformed_orderbook", "inverted_book", "quote_invalid", "selected_price_above_0_99", "selected_price_below_0_01"}:
        return _block(malformed_reason)
    orderbook_ready = payload.get("orderbook_ready")
    if orderbook_ready is False and not bool(payload.get("topbook_depth_exists") or payload.get("depth_available") or payload.get("selected_side_has_liquidity")):
        return _block("orderbook_not_ready")
    if getattr(bot, "trading_halted", False):
        return _block("trading_halted")
    daily_loss = float(max_daily_loss or 0.0)
    if daily_loss > 0.0:
        realized_pnl = bot.to_float(getattr(bot, "realized_pnl", 0.0), 0.0) or 0.0
        live_realized_pnl_today = bot.to_float(getattr(bot, "live_realized_pnl_today", 0.0), 0.0) or 0.0
        if realized_pnl <= -daily_loss or live_realized_pnl_today <= -daily_loss:
            return _block("daily_loss_guard")
    if bot.already_holding(ticker):
        return _block("duplicate_position_guard")
    recent_order = bot.recent_submitted_order_for_ticker(ticker)
    if recent_order:
        return _block("recent_order_pending_confirmation")

    max_contracts = int(max(1, max_live_order_size or 1))
    if count > max_contracts:
        count = max_contracts
        cap_reasons.append("max_contracts_per_trade")
    max_dollars = float(max_dollars_per_trade or 0.0)
    if max_dollars > 0.0:
        dollar_cap = int(max_dollars / selected_price)
        if dollar_cap <= 0:
            return _block("max_dollars_per_trade")
        if count > dollar_cap:
            count = dollar_cap
            cap_reasons.append("max_dollars_per_trade")
    max_family = float(max_family_exposure_dollars or 0.0)
    current_family_exposure = _position_family_exposure(bot, family, selected_price)
    if max_family > 0.0:
        remaining_family = max(0.0, max_family - current_family_exposure)
        family_cap = int(remaining_family / selected_price)
        if family_cap <= 0:
            return _block("max_family_exposure")
        if count > family_cap:
            count = family_cap
            cap_reasons.append("max_family_exposure")
    if count <= 0:
        return _block("risk_size_capped_to_zero")
    payload["count"] = int(count)
    if cap_reasons:
        payload["risk_cap_reason"] = "+".join(dict.fromkeys(cap_reasons))
    pending_family_orders = bot.recent_submitted_order_count_for_family(family)
    pending_total_orders = bot.recent_submitted_order_count_total()
    if pending_family_orders:
        warnings.append("pending_family_orders")
    if pending_total_orders:
        warnings.append("pending_global_orders")
    return {
        "ok": True,
        "reason": "risk_pass",
        "payload": payload,
        "ticker": ticker,
        "family": family,
        "side": side,
        "selected_price": selected_price,
        "final_count": int(count),
        "warnings": warnings,
        "cap_reasons": list(dict.fromkeys(cap_reasons)),
        "current_family_exposure_dollars": round(current_family_exposure, 4),
        "max_family_exposure_dollars": round(max_family, 4),
        "max_dollars_per_trade": round(max_dollars, 4),
        "max_contracts_per_trade": int(max_contracts),
    }

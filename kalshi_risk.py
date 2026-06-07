"""Catastrophic live-trading guards for AION."""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List

from kalshi_orderbook import normalize_side


def open_positions(bot: Any) -> List[Dict[str, Any]]:
    positions = getattr(bot, "live_positions", None) or []
    out: List[Dict[str, Any]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        status = str(position.get("status") or "open").strip().lower()
        count = abs(float(bot.to_float(position.get("count"), 0.0) or 0.0))
        if status in {"open", "exit_pending"} and count > 0.0:
            out.append(position)
    return out


def same_ticker_positions(bot: Any, ticker: str) -> List[Dict[str, Any]]:
    ticker = str(ticker or "").strip()
    if not ticker:
        return []
    return [
        position for position in open_positions(bot)
        if str(position.get("ticker") or position.get("market_ticker") or "").strip() == ticker
    ]


def reset_daily_counters_if_needed(bot: Any) -> None:
    today = time.strftime("%Y-%m-%d")
    state = getattr(bot, "state", None)
    if not isinstance(state, dict):
        return
    daily = state.setdefault("daily", {})
    if daily.get("date") != today:
        state["daily"] = {
            "date": today,
            "realized_pnl": 0.0,
            "live_trade_count": 0,
            "submitted_order_count": 0,
        }
        bot.save_state()


def active_order_timestamps(bot: Any) -> List[float]:
    now = time.time()
    window = float(getattr(bot, "live_order_rate_window_sec", 60.0) or 60.0)
    timestamps = [
        float(ts) for ts in (getattr(bot, "live_order_timestamps", None) or [])
        if now - float(ts or 0.0) < window
    ]
    bot.live_order_timestamps = timestamps
    return timestamps


def evaluate_entry_risk(bot: Any, target: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    reset_daily_counters_if_needed(bot)
    ticker = str(getattr(target, "ticker", None) or payload.get("ticker") or "").strip()
    side = normalize_side(getattr(target, "side", None) or payload.get("side"))
    count = int(payload.get("count") or getattr(target, "count", 0) or 0)
    if not ticker:
        return {"ok": False, "reason": "missing_ticker"}
    if side not in {"yes", "no"}:
        return {"ok": False, "reason": "missing_selected_side"}
    if count < 1:
        return {"ok": False, "reason": "invalid_count"}
    if count > int(getattr(bot, "max_live_order_size", 1) or 1):
        return {"ok": False, "reason": "sane_max_sizing_guard", "max_live_order_size": bot.max_live_order_size}

    daily = (getattr(bot, "state", {}) or {}).get("daily", {})
    realized_pnl = float(bot.to_float(daily.get("realized_pnl"), 0.0) or 0.0)
    max_daily_loss = float(getattr(bot, "max_daily_loss", 0.0) or 0.0)
    if max_daily_loss > 0.0 and realized_pnl <= -abs(max_daily_loss):
        return {
            "ok": False,
            "reason": "hard_daily_loss_cap",
            "realized_pnl": realized_pnl,
            "max_daily_loss": max_daily_loss,
        }

    same_ticker = same_ticker_positions(bot, ticker)
    for position in same_ticker:
        position_side = normalize_side(position.get("side"))
        if position_side in {"yes", "no"} and position_side != side:
            return {
                "ok": False,
                "reason": "exact_same_ticker_opposite_side_guard",
                "existing_side": position_side,
                "selected_side": side,
            }
    if same_ticker:
        return {"ok": False, "reason": "duplicate_live_position_guard", "open_count": len(same_ticker)}

    if len(open_positions(bot)) >= int(getattr(bot, "max_open_positions", 1) or 1):
        return {"ok": False, "reason": "max_open_positions_guard", "open_positions": len(open_positions(bot))}

    timestamps = active_order_timestamps(bot)
    if len(timestamps) >= int(getattr(bot, "live_order_rate_limit", 1) or 1):
        return {
            "ok": False,
            "reason": "live_order_rate_limit",
            "active_order_timestamps": len(timestamps),
            "live_order_rate_limit": bot.live_order_rate_limit,
        }
    return {"ok": True, "reason": "passed"}


def evaluate_exit_risk(bot: Any, position: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    ticker = str(payload.get("ticker") or position.get("ticker") or "").strip()
    side = normalize_side(payload.get("side") or position.get("side"))
    action = str(payload.get("action") or "").strip().lower()
    count = int(payload.get("count") or 0)
    if not ticker:
        return {"ok": False, "reason": "missing_ticker"}
    if side not in {"yes", "no"}:
        return {"ok": False, "reason": "missing_selected_side"}
    if action != "sell":
        return {"ok": False, "reason": "exit_must_sell"}
    if not bool(payload.get("reduce_only")):
        return {"ok": False, "reason": "exit_must_be_reduce_only"}
    if payload.get("time_in_force") != "immediate_or_cancel":
        return {"ok": False, "reason": "exit_must_be_ioc"}
    if count < 1:
        return {"ok": False, "reason": "invalid_exit_count"}
    open_count = abs(float(bot.to_float(position.get("count"), 0.0) or 0.0))
    if open_count > 0.0 and count > int(max(1, open_count)):
        return {"ok": False, "reason": "exit_count_exceeds_position", "position_count": open_count}
    return {"ok": True, "reason": "passed"}

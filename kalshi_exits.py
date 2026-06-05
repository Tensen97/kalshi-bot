"""Dynamic exit helpers moved out of kalshi_web.py."""

import json
import time
from datetime import datetime, timezone


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


def log_sports_harvest_decision(bot, decision, *, log_cooldown_seconds):
    decision = decision if isinstance(decision, dict) else {}
    if not decision.get("is_sports_position"):
        return
    ticker = str(decision.get("ticker") or "unknown").strip() or "unknown"
    action = str(decision.get("action") or "hold").strip().lower() or "hold"
    reason = str(decision.get("reason") or "unknown").strip().lower() or "unknown"
    pnl = bot.to_float(decision.get("unrealized_pnl"), 0.0) or 0.0
    current_probability = bot.to_float(decision.get("current_probability"), None)
    marker = (
        action,
        reason,
        round(pnl, 4),
        round(bot.to_float(decision.get("exit_price"), 0.0) or 0.0, 4),
        int((bot.to_float(decision.get("hold_seconds"), 0.0) or 0.0) // 30),
    )
    now_ts = time.time()
    log_map = getattr(bot, "sports_harvest_log_markers", {})
    if not isinstance(log_map, dict):
        log_map = {}
    previous = log_map.get(ticker)
    previous_ts = bot.to_float(previous.get("ts"), 0.0) if isinstance(previous, dict) else 0.0
    if isinstance(previous, dict) and previous.get("marker") == marker and now_ts - previous_ts < log_cooldown_seconds:
        return
    log_map[ticker] = {"marker": marker, "ts": now_ts}
    bot.sports_harvest_log_markers = log_map

    payload = {
        "ticker": ticker,
        "family": decision.get("family"),
        "entry_price": decision.get("entry_price"),
        "current_exit_price": decision.get("exit_price"),
        "current_probability": current_probability,
        "pnl_estimate": decision.get("unrealized_pnl"),
        "pnl_pct": decision.get("pnl_pct"),
        "reason": reason,
        "action": action,
    }
    print("[SPORTS-HARVEST-DECISION] " + json.dumps(payload, sort_keys=True, default=str))

    if action == "exit" and reason in {"defensive_exit", "late_game_decay_exit", "dead_prop_exit", "stale_sports_exit"}:
        print(
            "[SPORTS-DEFENSIVE-EXIT] "
            + json.dumps(
                {
                    "ticker": ticker,
                    "reason": reason,
                    "entry_price": decision.get("entry_price"),
                    "current_exit_price": decision.get("exit_price"),
                    "pnl_estimate": decision.get("unrealized_pnl"),
                },
                sort_keys=True,
                default=str,
            )
        )
    elif action != "exit":
        print(
            "[SPORTS-HARVEST-HOLD] "
            + json.dumps(
                {
                    "ticker": ticker,
                    "reason": reason,
                    "current_probability": current_probability,
                    "pnl_estimate": decision.get("unrealized_pnl"),
                },
                sort_keys=True,
                default=str,
            )
        )


def _coerce_timestamp(value):
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
        if numeric > 100000.0:
            return numeric
    except Exception:
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _resolve_minutes_left(*containers):
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in (
            "minutes_left",
            "minutes_to_expiration",
            "minutes_to_close",
            "time_to_settlement_minutes",
        ):
            if container.get(key) not in (None, ""):
                try:
                    return float(container.get(key))
                except Exception:
                    pass
        for key in (
            "close_time_ts",
            "expiration_time_ts",
            "expected_expiration_time_ts",
            "settlement_time_ts",
            "close_time",
            "expiration_time",
            "expected_expiration_time",
            "settlement_time",
        ):
            ts = _coerce_timestamp(container.get(key))
            if ts is not None:
                return (float(ts) - time.time()) / 60.0
    return None


def _sports_pregame_exit_context(bot, *, market, position, metadata, q=None, now_ts):
    sources = []
    for source in (market, metadata.get("metadata") if isinstance(metadata, dict) else None, position, q):
        if source is None:
            continue
        if isinstance(source, dict):
            if isinstance(source.get("market"), dict):
                sources.append(source.get("market"))
            sources.append(source)
        else:
            sources.append(source)
    start_ts = None
    start_key = None
    status_value = None
    status_key = None
    explicit_live_value = None
    explicit_live_key = None
    scoreboard_live = False
    scoreboard_key = None
    try:
        if hasattr(bot, "_sports_live_state_timestamp"):
            start_ts, start_key, _raw = bot._sports_live_state_timestamp(sources)
        else:
            for source in sources:
                for key in ("event_start_time", "occurrence_datetime", "start_time", "scheduled_start_time", "game_start_time", "match_start_time"):
                    try:
                        raw = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
                    except Exception:
                        raw = None
                    ts = _coerce_timestamp(raw)
                    if ts is not None:
                        start_ts = ts
                        start_key = key
                        raise StopIteration
    except StopIteration:
        pass
    except Exception:
        start_ts = None
        start_key = None
    try:
        if hasattr(bot, "_sports_live_state_field"):
            status_value, status_key = bot._sports_live_state_field(
                sources,
                "status",
                "market_status",
                "event_status",
                "game_status",
                "match_status",
                "live_state",
                "game_state",
            )
            explicit_live_value, explicit_live_key = bot._sports_live_state_field(
                sources,
                "in_play",
                "inplay",
                "is_in_play",
                "is_live",
                "live",
                "currently_live",
                "is_currently_live",
            )
    except Exception:
        status_value = None
        status_key = None
        explicit_live_value = None
        explicit_live_key = None
    try:
        if hasattr(bot, "_sports_live_state_has_scoreboard"):
            scoreboard_live, scoreboard_key, _value = bot._sports_live_state_has_scoreboard(sources)
    except Exception:
        scoreboard_live = False
        scoreboard_key = None

    status_text = str(status_value or "").strip().lower()
    try:
        explicit_live = bool(bot._sports_live_state_bool(explicit_live_value)) if hasattr(bot, "_sports_live_state_bool") else False
    except Exception:
        explicit_live = False
    live_statuses = {"live", "in_play", "inplay", "in-play", "in_progress", "running", "started", "midgame", "halftime", "intermission", "between_periods"}
    pregame_statuses = {"scheduled", "pregame", "pre_game", "not_started", "not started", "upcoming", "preview"}
    future_start = bool(start_ts is not None and start_ts > float(now_ts) + 30.0)
    pregame_status = bool(status_text in pregame_statuses)
    live_evidence = bool(explicit_live or scoreboard_live or status_text in live_statuses or (start_ts is not None and start_ts <= float(now_ts) + 30.0))
    return {
        "is_pregame_or_future": bool((future_start or pregame_status) and not live_evidence),
        "future_start": future_start,
        "pregame_status": pregame_status,
        "live_evidence": live_evidence,
        "start_key": start_key,
        "start_time": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z") if start_ts is not None else None,
        "seconds_to_start": round(float(start_ts - now_ts), 3) if start_ts is not None else None,
        "status_key": status_key,
        "status": status_value,
        "explicit_live_key": explicit_live_key,
        "explicit_live_value": explicit_live_value,
        "scoreboard_key": scoreboard_key,
    }


def log_crypto_exit_decision(bot, decision, *, log_cooldown_seconds):
    decision = decision if isinstance(decision, dict) else {}
    if not decision.get("is_crypto_short_cycle"):
        return
    ticker = str(decision.get("ticker") or "unknown").strip() or "unknown"
    action = str(decision.get("action") or "hold").strip().lower() or "hold"
    reason = str(decision.get("reason") or "unknown").strip().lower() or "unknown"
    pnl = bot.to_float(decision.get("unrealized_pnl"), 0.0) or 0.0
    marker = (
        action,
        reason,
        round(pnl, 4),
        round(bot.to_float(decision.get("exit_price"), 0.0) or 0.0, 4),
        int((bot.to_float(decision.get("hold_seconds"), 0.0) or 0.0) // 15),
    )
    now_ts = time.time()
    log_map = getattr(bot, "crypto_exit_log_markers", {})
    if not isinstance(log_map, dict):
        log_map = {}
    previous = log_map.get(ticker)
    previous_ts = bot.to_float(previous.get("ts"), 0.0) if isinstance(previous, dict) else 0.0
    if isinstance(previous, dict) and previous.get("marker") == marker and now_ts - previous_ts < log_cooldown_seconds:
        return
    log_map[ticker] = {"marker": marker, "ts": now_ts}
    bot.crypto_exit_log_markers = log_map
    payload = {
        "ticker": ticker,
        "family": decision.get("family"),
        "asset": decision.get("crypto_asset"),
        "timeframe": decision.get("crypto_timeframe"),
        "side": decision.get("side"),
        "entry_price": decision.get("entry_price"),
        "current_price": decision.get("current_price"),
        "current_price_value": decision.get("current_price"),
        "exit_price": decision.get("exit_price"),
        "count": decision.get("count"),
        "unrealized_pnl": decision.get("unrealized_pnl"),
        "unrealized_profit_dollars": decision.get("unrealized_pnl"),
        "pnl_pct": decision.get("pnl_pct"),
        "unrealized_profit_pct": decision.get("pnl_pct"),
        "profit_target_dollars": decision.get("crypto_fast_take_profit_dollars"),
        "profit_target_pct": decision.get("crypto_fast_take_profit_pct"),
        "minutes_left": decision.get("minutes_left"),
        "momentum_direction": decision.get("crypto_momentum_direction"),
        "momentum_strength": decision.get("crypto_momentum_strength"),
        "recent_delta": decision.get("recent_delta"),
        "last_step_delta": decision.get("last_step_delta"),
        "action": action,
        "reason": reason,
        "exit_attempted": bool(action == "exit"),
    }
    print("[CRYPTO-EXIT-DECISION] " + json.dumps(payload, sort_keys=True, default=str))
    if action == "exit" and reason == "crypto_fast_take_profit":
        print("[CRYPTO-FAST-PROFIT-EXIT] " + json.dumps(payload, sort_keys=True, default=str))
    elif action == "exit" and reason == "crypto_repricing_profit":
        print("[CRYPTO-REPRICING-PROFIT-EXIT] " + json.dumps(payload, sort_keys=True, default=str))
    elif action == "exit" and reason == "crypto_momentum_stall":
        print("[CRYPTO-MOMENTUM-STALL-EXIT] " + json.dumps(payload, sort_keys=True, default=str))
    elif action == "exit" and reason in {"crypto_momentum_reversal", "crypto_reversal_protect"}:
        print("[CRYPTO-MOMENTUM-REVERSAL-EXIT] " + json.dumps(payload, sort_keys=True, default=str))
        print("[CRYPTO-REVERSAL-PROTECT-EXIT] " + json.dumps(payload, sort_keys=True, default=str))
    elif action != "exit":
        print("[CRYPTO-EXIT-HOLD] " + json.dumps(payload, sort_keys=True, default=str))


def _profit_lock_position_key(ticker, side):
    ticker = str(ticker or "").strip() or "unknown"
    side = str(side or "").strip().lower() or "unknown"
    return f"{ticker}|{side}"


def _load_persisted_live_exit_peak(bot, ticker, side):
    ticker = str(ticker or "").strip()
    if not ticker or not callable(getattr(bot, "load_live_position_state", None)):
        return None
    try:
        state = bot.load_live_position_state()
    except Exception:
        return None
    entry = state.get(ticker) if isinstance(state, dict) and isinstance(state.get(ticker), dict) else None
    if not entry:
        return None
    normalize_side = getattr(bot, "_normalize_live_side", None)
    requested_side = normalize_side(side) if callable(normalize_side) else str(side or "").strip().lower()
    persisted_side = normalize_side(entry.get("live_exit_peak_side") or entry.get("side")) if callable(normalize_side) else str(entry.get("live_exit_peak_side") or entry.get("side") or "").strip().lower()
    if persisted_side in {"yes", "no"} and requested_side in {"yes", "no"} and persisted_side != requested_side:
        return None
    peak_value = bot.to_float(entry.get("live_exit_peak_value"), None)
    if peak_value is None:
        return None
    return {
        "ticker": ticker,
        "side": requested_side or persisted_side,
        "peak_value": round(float(peak_value), 4),
        "entry_cost": bot.to_float(entry.get("live_exit_peak_entry_cost"), None),
        "max_payout": bot.to_float(entry.get("live_exit_peak_max_payout"), None),
        "updated_ts": bot.to_float(entry.get("live_exit_peak_updated_ts"), 0.0) or 0.0,
        "mfe_value": bot.to_float(entry.get("live_exit_mfe_value"), peak_value),
        "mfe_profit": bot.to_float(entry.get("live_exit_mfe_profit"), None),
        "mfe_timestamp": bot.to_float(entry.get("live_exit_mfe_timestamp"), None),
        "mae_value": bot.to_float(entry.get("live_exit_mae_value"), None),
        "mae_loss": bot.to_float(entry.get("live_exit_mae_loss"), None),
        "mae_timestamp": bot.to_float(entry.get("live_exit_mae_timestamp"), None),
        "best_cashout_price": bot.to_float(entry.get("live_exit_best_cashout_price"), None),
        "worst_cashout_price": bot.to_float(entry.get("live_exit_worst_cashout_price"), None),
        "observation_count": int(bot.to_float(entry.get("live_exit_observation_count"), 0.0) or 0),
        "last_observed_price": bot.to_float(entry.get("live_exit_last_observed_price"), None),
        "source": "live_position_state",
    }


def _persist_live_exit_peak_value(bot, ticker, side, peak_record):
    ticker = str(ticker or "").strip()
    if (
        not ticker
        or not isinstance(peak_record, dict)
        or not callable(getattr(bot, "load_live_position_state", None))
        or not callable(getattr(bot, "save_live_position_state", None))
    ):
        return False
    try:
        state = bot.load_live_position_state()
    except Exception:
        return False
    entry = state.get(ticker) if isinstance(state, dict) and isinstance(state.get(ticker), dict) else None
    if not isinstance(entry, dict):
        return False
    peak_value = bot.to_float(peak_record.get("peak_value"), None)
    if peak_value is None:
        return False
    updated_ts = bot.to_float(peak_record.get("updated_ts"), 0.0) or 0.0
    existing_peak = bot.to_float(entry.get("live_exit_peak_value"), None)
    existing_ts = bot.to_float(entry.get("live_exit_peak_updated_ts"), 0.0) or 0.0
    normalize_side = getattr(bot, "_normalize_live_side", None)
    normalized_side = normalize_side(side) if callable(normalize_side) else str(side or "").strip().lower()
    if (
        existing_peak is not None
        and abs(float(existing_peak) - float(peak_value)) < 0.0001
        and abs(float(existing_ts) - float(updated_ts)) < 0.001
        and str(entry.get("live_exit_peak_side") or normalized_side or "").strip().lower() == str(normalized_side or "").strip().lower()
    ):
        return False
    updated_entry = dict(entry)
    updated_entry["live_exit_peak_side"] = normalized_side or str(side or "").strip().lower()
    updated_entry["live_exit_peak_value"] = round(float(peak_value), 4)
    updated_entry["live_exit_peak_entry_cost"] = round(float(bot.to_float(peak_record.get("entry_cost"), 0.0) or 0.0), 4)
    updated_entry["live_exit_peak_max_payout"] = round(float(bot.to_float(peak_record.get("max_payout"), 0.0) or 0.0), 4)
    updated_entry["live_exit_peak_updated_ts"] = updated_ts
    for source_key, state_key in (
        ("mfe_value", "live_exit_mfe_value"),
        ("mfe_profit", "live_exit_mfe_profit"),
        ("mfe_timestamp", "live_exit_mfe_timestamp"),
        ("mae_value", "live_exit_mae_value"),
        ("mae_loss", "live_exit_mae_loss"),
        ("mae_timestamp", "live_exit_mae_timestamp"),
        ("best_cashout_price", "live_exit_best_cashout_price"),
        ("worst_cashout_price", "live_exit_worst_cashout_price"),
        ("observation_count", "live_exit_observation_count"),
        ("last_observed_price", "live_exit_last_observed_price"),
    ):
        value = peak_record.get(source_key)
        if value is None:
            continue
        if source_key == "observation_count":
            updated_entry[state_key] = int(bot.to_float(value, 0.0) or 0)
        else:
            updated_entry[state_key] = round(float(bot.to_float(value, 0.0) or 0.0), 4)
    state[ticker] = updated_entry
    try:
        bot.save_live_position_state(dict(state))
        return True
    except Exception:
        return False


def update_live_exit_peak_value(bot, *, ticker, side, entry_cost, current_value, cashout_value, max_payout, now_ts=None):
    key = _profit_lock_position_key(ticker, side)
    now_ts = float(now_ts or time.time())
    observed_value = cashout_value if cashout_value is not None else current_value
    observed_value = bot.to_float(observed_value, None)
    if observed_value is None:
        return {"peak_value": None, "peak_profit": None, "peak_giveback_pct": 0.0, "updated": False}
    peak_map = getattr(bot, "live_exit_peak_values", {})
    if not isinstance(peak_map, dict):
        peak_map = {}
    previous = peak_map.get(key) if isinstance(peak_map.get(key), dict) else {}
    if not previous:
        persisted_previous = _load_persisted_live_exit_peak(bot, ticker, side)
        if persisted_previous:
            previous = persisted_previous
            peak_map[key] = dict(persisted_previous)
            bot.live_exit_peak_values = peak_map
    previous_peak = bot.to_float(previous.get("peak_value"), None)
    entry_cost = bot.to_float(entry_cost, 0.0) or 0.0
    max_payout = bot.to_float(max_payout, 0.0) or 0.0
    observed_price = None
    if max_payout > 0.0:
        observed_price = max(0.0, min(1.0, float(observed_value) / float(max_payout)))
    elif 0.0 <= float(observed_value) <= 1.0:
        observed_price = float(observed_value)

    previous_mae_value = bot.to_float(previous.get("mae_value"), None)
    previous_mfe_ts = bot.to_float(previous.get("mfe_timestamp"), None)
    previous_mae_ts = bot.to_float(previous.get("mae_timestamp"), None)
    previous_best_price = bot.to_float(previous.get("best_cashout_price"), None)
    previous_worst_price = bot.to_float(previous.get("worst_cashout_price"), None)
    observation_count = int(bot.to_float(previous.get("observation_count"), 0.0) or 0) + 1

    mfe_updated = previous_peak is None or observed_value > previous_peak + 0.005
    mae_updated = previous_mae_value is None or observed_value < previous_mae_value - 0.005
    peak_value = float(observed_value) if mfe_updated else float(previous_peak)
    mae_value = float(observed_value) if mae_updated else float(previous_mae_value)
    mfe_timestamp = now_ts if mfe_updated else previous_mfe_ts
    mae_timestamp = now_ts if mae_updated else previous_mae_ts

    if observed_price is not None:
        if previous_best_price is None or observed_price > previous_best_price:
            best_cashout_price = observed_price
        else:
            best_cashout_price = previous_best_price
        if previous_worst_price is None or observed_price < previous_worst_price:
            worst_cashout_price = observed_price
        else:
            worst_cashout_price = previous_worst_price
    else:
        best_cashout_price = previous_best_price
        worst_cashout_price = previous_worst_price

    current_profit = max(0.0, float(observed_value) - float(entry_cost))
    peak_profit = max(0.0, float(peak_value) - float(entry_cost))
    mae_loss = max(0.0, float(entry_cost) - float(mae_value))
    peak_giveback_pct = 0.0
    if peak_profit > 0.0:
        peak_giveback_pct = max(0.0, min(1.0, (peak_profit - current_profit) / peak_profit))
    peak_record = {
        "ticker": ticker,
        "side": side,
        "peak_value": round(float(peak_value), 4),
        "entry_cost": round(float(entry_cost or 0.0), 4),
        "max_payout": round(float(max_payout or 0.0), 4),
        "updated_ts": now_ts,
        "mfe_value": round(float(peak_value), 4),
        "peak_profit": round(float(peak_profit), 4),
        "mfe_profit": round(float(peak_profit), 4),
        "mfe_timestamp": mfe_timestamp,
        "mae_value": round(float(mae_value), 4),
        "mae_loss": round(float(mae_loss), 4),
        "mae_timestamp": mae_timestamp,
        "best_cashout_price": round(float(best_cashout_price), 4) if best_cashout_price is not None else None,
        "worst_cashout_price": round(float(worst_cashout_price), 4) if worst_cashout_price is not None else None,
        "observation_count": observation_count,
        "last_observed_price": round(float(observed_price), 4) if observed_price is not None else None,
    }
    peak_map[key] = peak_record
    bot.live_exit_peak_values = peak_map
    updated = bool(mfe_updated or mae_updated)
    if updated or observation_count == 1 or observation_count % 20 == 0:
        _persist_live_exit_peak_value(bot, ticker, side, peak_record)
    return {
        **peak_record,
        "peak_giveback_pct": round(float(peak_giveback_pct), 4),
        "updated": bool(updated),
        "mfe_updated": bool(mfe_updated),
        "mae_updated": bool(mae_updated),
        "observed_value": round(float(observed_value), 4),
    }


def _profit_lock_payload(decision):
    decision = decision if isinstance(decision, dict) else {}
    return {
        "ticker": decision.get("ticker"),
        "side": decision.get("side"),
        "market_type": decision.get("family"),
        "lane": decision.get("lane"),
        "entry_cost": decision.get("entry_cost"),
        "current_value": decision.get("current_value"),
        "cashout_value": decision.get("cashout_value"),
        "max_payout": decision.get("max_payout"),
        "unrealized_profit_dollars": decision.get("profit_lock_unrealized_pnl", decision.get("unrealized_pnl")),
        "unrealized_profit_pct": decision.get("profit_lock_unrealized_profit_pct", decision.get("unrealized_profit_pct")),
        "peak_value": decision.get("peak_value"),
        "peak_giveback_pct": decision.get("peak_giveback_pct"),
        "reason": decision.get("reason"),
        "exit_attempted": bool(decision.get("action") == "exit"),
    }


def log_profit_lock_decision(bot, decision, *, log_cooldown_seconds):
    decision = decision if isinstance(decision, dict) else {}
    ticker = str(decision.get("ticker") or "unknown").strip() or "unknown"
    reason = str(decision.get("reason") or "unknown").strip().lower() or "unknown"
    action = str(decision.get("action") or "hold").strip().lower() or "hold"
    pnl = bot.to_float(decision.get("profit_lock_unrealized_pnl", decision.get("unrealized_pnl")), 0.0) or 0.0
    peak_value = bot.to_float(decision.get("peak_value"), 0.0) or 0.0
    peak_profit = bot.to_float(decision.get("peak_profit"), 0.0) or 0.0
    giveback = bot.to_float(decision.get("peak_giveback_pct"), 0.0) or 0.0
    should_log_check = bool(
        (action == "exit" and (decision.get("profit_lock_exit") or pnl > 0.0 or peak_profit > 0.0))
        or decision.get("peak_value_updated")
        or pnl > 0.0
        or giveback > 0.0
        or reason in {"missing_executable_exit_bid", "inverted_exit_book"}
    )
    if not should_log_check:
        return
    marker = (
        action,
        reason,
        round(pnl, 4),
        round(peak_value, 4),
        round(giveback, 3),
        int((bot.to_float(decision.get("hold_seconds"), 0.0) or 0.0) // 15),
    )
    now_ts = time.time()
    log_map = getattr(bot, "profit_lock_log_markers", {})
    if not isinstance(log_map, dict):
        log_map = {}
    previous = log_map.get(ticker)
    previous_ts = bot.to_float(previous.get("ts"), 0.0) if isinstance(previous, dict) else 0.0
    if isinstance(previous, dict) and previous.get("marker") == marker and now_ts - previous_ts < log_cooldown_seconds:
        return
    log_map[ticker] = {"marker": marker, "ts": now_ts}
    bot.profit_lock_log_markers = log_map
    payload = _profit_lock_payload(decision)
    print("[AION-PROFIT-LOCK-CHECK] " + json.dumps(payload, sort_keys=True, default=str))
    if decision.get("peak_value_updated"):
        print("[AION-PEAK-VALUE-UPDATE] " + json.dumps(payload, sort_keys=True, default=str))
    if action == "exit":
        print("[AION-PROFIT-LOCK-EXIT] " + json.dumps(payload, sort_keys=True, default=str))
        if reason in {"trailing_profit_giveback", "green_to_red_peak_giveback"}:
            print("[AION-TRAILING-PROFIT-EXIT] " + json.dumps(payload, sort_keys=True, default=str))
        if decision.get("is_sports_position"):
            print("[SPORTS-PROFIT-LOCK-EXIT] " + json.dumps(payload, sort_keys=True, default=str))
        if decision.get("is_crypto_ladder_position"):
            print("[CRYPTO-PROFIT-LOCK-EXIT] " + json.dumps(payload, sort_keys=True, default=str))
    elif reason in {"missing_executable_exit_bid", "inverted_exit_book", "profit_lock_no_executable_exit"}:
        print("[AION-EXIT-SKIP] " + json.dumps(payload, sort_keys=True, default=str))


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
    crypto_fast_take_profit_dollars=10.0,
    crypto_fast_take_profit_pct=0.25,
    crypto_momentum_reversal_exit=True,
    crypto_near_expiry_profit_exit=True,
    sports_fast_take_profit_dollars=1.0,
    sports_fast_take_profit_pct=0.25,
    sports_trailing_profit_giveback_pct=0.30,
    sports_max_payout_lock_pct=0.70,
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
    pnl_pct = round((pnl_per_contract / entry_price) * 100.0, 4) if entry_price and entry_price > 0.0 else None
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
    is_sports_position = False
    try:
        is_sports_position = bool(bot.is_sports_family(family, ticker=ticker))
    except Exception:
        is_sports_position = False
    if is_sports_position and ticker and not market:
        try:
            refreshed_market = bot.get_live_market_detail(ticker)
            if isinstance(refreshed_market, dict) and refreshed_market:
                market = refreshed_market
        except Exception:
            pass
    is_crypto_ladder_position = False
    crypto_asset = None
    crypto_timeframe = None
    try:
        is_crypto_ladder_position = bool(bot._is_crypto_ladder_market(family=family, ticker=ticker, market=market, q=q))
        crypto_asset = bot._crypto_ladder_asset_symbol(family=family, ticker=ticker, market=market, q=q)
        crypto_timeframe = bot._crypto_ladder_timeframe(family=family, ticker=ticker, market=market, q=q)
    except Exception:
        is_crypto_ladder_position = False
    is_crypto_short_cycle = bool(
        is_crypto_ladder_position
        and crypto_asset in {"BTC", "ETH"}
        and str(crypto_timeframe or "").strip().lower() == "15m"
    )
    sports_lifecycle_state = "unknown"
    if is_sports_position:
        try:
            sports_lifecycle_state = str(bot._get_sports_market_lifecycle_state(market=market, family=family) or "unknown").strip().lower()
        except Exception:
            sports_lifecycle_state = "unknown"
    sports_pregame_exit_context = (
        _sports_pregame_exit_context(bot, market=market, position=position, metadata=metadata, q=q, now_ts=now_ts)
        if is_sports_position
        else {}
    )
    probability_collapse = bool(
        recent_delta <= -max(0.015, momentum_eps * 4.0)
        or last_step_delta <= -max(0.012, momentum_eps * 3.0)
        or side_support < -(neutral_band + 0.18)
    )
    late_or_stale_sports = bool(
        is_sports_position
        and (
            sports_lifecycle_state in {"live_late"}
            or hold_seconds >= max(min_hold_seconds * 3.0, 900.0)
        )
    )
    executable_exit_price = bool(exit_price is not None and 0.0 < exit_price <= 1.0)
    inverted_book = bool(
        (
            price_context.get("yes_bid") is not None
            and price_context.get("yes_ask") is not None
            and bot.to_float(price_context.get("yes_bid"), 0.0) > bot.to_float(price_context.get("yes_ask"), 0.0)
        )
        or (
            price_context.get("no_bid") is not None
            and price_context.get("no_ask") is not None
            and bot.to_float(price_context.get("no_bid"), 0.0) > bot.to_float(price_context.get("no_ask"), 0.0)
        )
    )
    minutes_left = _resolve_minutes_left(position, metadata.get("metadata") if isinstance(metadata, dict) else {}, market)
    entry_cost = round(float(entry_price) * float(count), 4)
    current_value = round(float(current_price) * float(count), 4)
    cashout_value = round(float(exit_price) * float(count), 4) if executable_exit_price else None
    profit_lock_value = cashout_value if cashout_value is not None else current_value
    profit_lock_unrealized = round(float(profit_lock_value) - float(entry_cost), 4)
    profit_lock_pct = round(float(profit_lock_unrealized) / float(entry_cost), 6) if entry_cost > 0.0 else None
    max_payout = round(float(count), 4)
    max_payout_pct = round(float(profit_lock_value) / float(max_payout), 6) if max_payout > 0.0 else 0.0
    family_text = f"{family or ''} {ticker or ''}".upper()
    weak_sports_profit_profile = bool(
        is_sports_position
        and any(token in family_text for token in ("PROP", "PLAYER", "TENNIS", "HR", "HOMER", "STRIKEOUT", "ALT"))
    )
    mainstream_sports_profile = bool(
        is_sports_position
        and any(token in family_text for token in ("SPREAD", "TOTAL", "MONEYLINE", "NBA", "NCAAB", "NFL", "MLB"))
        and not weak_sports_profit_profile
    )
    sports_profit_dollars = max(0.01, bot.to_float(sports_fast_take_profit_dollars, 1.0) or 1.0)
    sports_profit_pct = bot.to_float(sports_fast_take_profit_pct, 0.25)
    if sports_profit_pct is None:
        sports_profit_pct = 0.25
    if sports_profit_pct > 1.0:
        sports_profit_pct = sports_profit_pct / 100.0
    sports_profit_pct = max(0.01, min(5.0, float(sports_profit_pct)))
    sports_trailing_pct = bot.to_float(sports_trailing_profit_giveback_pct, 0.30)
    if sports_trailing_pct is None:
        sports_trailing_pct = 0.30
    if sports_trailing_pct > 1.0:
        sports_trailing_pct = sports_trailing_pct / 100.0
    sports_trailing_pct = max(0.05, min(0.90, float(sports_trailing_pct)))
    if weak_sports_profit_profile:
        sports_trailing_pct = max(0.05, sports_trailing_pct * 0.75)
    elif mainstream_sports_profile:
        sports_trailing_pct = min(0.45, sports_trailing_pct * 1.15)
    sports_max_payout_pct = bot.to_float(sports_max_payout_lock_pct, 0.70)
    if sports_max_payout_pct is None:
        sports_max_payout_pct = 0.70
    if sports_max_payout_pct > 1.0:
        sports_max_payout_pct = sports_max_payout_pct / 100.0
    sports_max_payout_pct = max(0.25, min(0.98, float(sports_max_payout_pct)))
    universal_profit_dollars = sports_profit_dollars if is_sports_position else 1.0
    universal_profit_pct = sports_profit_pct if is_sports_position else 0.25
    universal_trailing_pct = sports_trailing_pct if is_sports_position else 0.30
    universal_max_payout_pct = sports_max_payout_pct if is_sports_position else 0.70
    peak_context = update_live_exit_peak_value(
        bot,
        ticker=ticker,
        side=side,
        entry_cost=entry_cost,
        current_value=current_value,
        cashout_value=cashout_value,
        max_payout=max_payout,
        now_ts=now_ts,
    )
    peak_value = peak_context.get("peak_value")
    peak_profit = bot.to_float(peak_context.get("peak_profit"), 0.0) or 0.0
    peak_giveback_pct = bot.to_float(peak_context.get("peak_giveback_pct"), 0.0) or 0.0
    peak_profit_floor = max(0.25, universal_profit_dollars * 0.50)
    profit_lock_dollar_exit = bool(executable_exit_price and profit_lock_unrealized >= universal_profit_dollars)
    profit_lock_pct_exit = bool(
        executable_exit_price
        and profit_lock_pct is not None
        and float(profit_lock_pct) >= universal_profit_pct
        and profit_lock_unrealized > 0.0
    )
    max_payout_lock_exit = bool(
        executable_exit_price
        and max_payout_pct >= universal_max_payout_pct
        and profit_lock_unrealized > 0.0
    )
    trailing_profit_exit = bool(
        executable_exit_price
        and peak_profit >= peak_profit_floor
        and peak_giveback_pct >= universal_trailing_pct
    )
    green_momentum_flip_exit = bool(
        executable_exit_price
        and peak_profit >= peak_profit_floor
        and weak_momentum
        and profit_lock_unrealized > 0.0
    )
    green_to_red_peak_exit = bool(
        executable_exit_price
        and peak_profit >= peak_profit_floor
        and profit_lock_unrealized <= 0.0
        and peak_giveback_pct >= min(0.20, universal_trailing_pct)
    )
    near_expiry_profit_lock_exit = bool(
        executable_exit_price
        and profit_lock_unrealized > 0.0
        and minutes_left is not None
        and 0.0 <= float(minutes_left) <= 5.0
    )
    universal_profit_lock_exit = bool(
        max_payout_lock_exit
        or profit_lock_dollar_exit
        or profit_lock_pct_exit
        or trailing_profit_exit
        or green_momentum_flip_exit
        or green_to_red_peak_exit
        or near_expiry_profit_lock_exit
    )
    profit_lock_reason = None
    if max_payout_lock_exit:
        profit_lock_reason = "max_payout_profit_lock"
    elif trailing_profit_exit:
        profit_lock_reason = "trailing_profit_giveback"
    elif green_to_red_peak_exit:
        profit_lock_reason = "green_to_red_peak_giveback"
    elif green_momentum_flip_exit:
        profit_lock_reason = "green_momentum_flip"
    elif profit_lock_dollar_exit:
        profit_lock_reason = "fast_profit_dollar_lock"
    elif profit_lock_pct_exit:
        profit_lock_reason = "fast_profit_pct_lock"
    elif near_expiry_profit_lock_exit:
        profit_lock_reason = "near_expiry_profit_lock"
    sports_min_profit = min(max(0.005, min_profit * 0.55), min_profit)
    sports_rotation_profit = max(0.006, min_profit * 0.45)
    sports_fast_profit = max(0.012, min_profit * 0.75)
    near_lock_harvest = bool(
        is_sports_position
        and executable_exit_price
        and (
            (current_price >= 0.95 and pnl_per_contract >= 0.0)
            or (current_price >= 0.90 and pnl_per_contract >= sports_rotation_profit)
        )
    )
    fast_green_harvest = bool(
        is_sports_position
        and executable_exit_price
        and hold_seconds <= 900.0
        and (
            pnl_per_contract >= sports_fast_profit
            or recent_delta >= max(0.018, momentum_eps * 4.0)
            or last_step_delta >= max(0.012, momentum_eps * 3.0)
        )
        and pnl_per_contract > 0.0
    )
    capital_rotation_harvest = bool(
        is_sports_position
        and executable_exit_price
        and pnl_per_contract >= sports_rotation_profit
        and (hold_seconds >= 300.0 or current_price >= 0.82 or not strong_momentum)
    )
    sports_harvest_profit = bool(
        is_sports_position
        and executable_exit_price
        and pnl_per_contract >= sports_min_profit
        and (weak_momentum or not strong_momentum or spread is None or spread > max_exit_spread or hold_seconds >= 120.0)
    )
    defensive_sports_collapse = bool(
        is_sports_position
        and executable_exit_price
        and (
            probability_collapse
            or current_price <= max(0.03, entry_price - max(0.025, entry_price * 0.18))
            or (pnl_per_contract <= -max(0.012, base_stop_loss * 0.45) and adverse_movement)
        )
    )
    late_game_decay_exit = bool(
        late_or_stale_sports
        and executable_exit_price
        and (
            pnl_per_contract < 0.0
            or weak_momentum
            or current_price <= max(0.08, entry_price * 0.65)
        )
    )
    dead_prop_exit = bool(
        is_sports_position
        and executable_exit_price
        and (
            current_price <= 0.04
            or (current_price <= max(0.05, entry_price * 0.35) and pnl_per_contract < 0.0)
        )
    )
    fast_profit_target_dollars = max(0.0, bot.to_float(crypto_fast_take_profit_dollars, 10.0) or 10.0)
    fast_profit_target_pct = bot.to_float(crypto_fast_take_profit_pct, 0.25)
    if fast_profit_target_pct is None:
        fast_profit_target_pct = 0.25
    if fast_profit_target_pct > 1.0:
        fast_profit_target_pct = fast_profit_target_pct / 100.0
    fast_profit_target_pct = max(0.01, min(5.0, float(fast_profit_target_pct)))
    crypto_fast_profit_exit = bool(
        is_crypto_short_cycle
        and executable_exit_price
        and pnl_per_contract > 0.0
        and (
            (fast_profit_target_dollars > 0.0 and unrealized_pnl >= fast_profit_target_dollars)
            or (
                pnl_pct is not None
                and (float(pnl_pct) / 100.0) >= fast_profit_target_pct
                and pnl_per_contract >= max(0.01, min_profit * 0.50)
            )
        )
    )
    crypto_repricing_profit_exit = bool(
        is_crypto_short_cycle
        and executable_exit_price
        and pnl_per_contract >= max(0.012, min_profit * 0.55)
        and (
            recent_delta >= max(0.012, momentum_eps * 3.0)
            or last_step_delta >= max(0.008, momentum_eps * 2.0)
            or current_price >= min(0.98, entry_price + max(0.06, entry_price * 0.20))
        )
        and (spread_ok or pnl_per_contract >= max(0.018, min_profit))
    )
    crypto_momentum_reversal = bool(
        is_crypto_short_cycle
        and bool(crypto_momentum_reversal_exit)
        and executable_exit_price
        and pnl_per_contract > 0.0
        and (
            weak_momentum
            or recent_delta <= -max(0.004, momentum_eps)
            or last_step_delta <= -max(0.003, momentum_eps * 0.75)
            or side_support < -max(neutral_band, 0.10)
        )
    )
    crypto_momentum_stall_exit = bool(
        is_crypto_short_cycle
        and executable_exit_price
        and pnl_per_contract >= max(0.008, min_profit * 0.35)
        and hold_seconds >= max(25.0, fast_cut_min_hold_seconds)
        and not strong_momentum
        and (
            abs(recent_delta) <= max(0.003, momentum_eps)
            or last_step_delta <= max(0.002, momentum_eps * 0.5)
            or side_support <= 0.0
            or (spread is not None and spread > max_exit_spread)
        )
    )
    crypto_near_expiry_profit = bool(
        is_crypto_short_cycle
        and bool(crypto_near_expiry_profit_exit)
        and executable_exit_price
        and pnl_per_contract > 0.0
        and minutes_left is not None
        and 0.0 <= float(minutes_left) <= 3.0
    )

    action = "hold"
    reason = "existing_hold_logic"
    required_loss_confirmations = 1
    if not dynamic_exit_enabled:
        reason = "dynamic_exit_disabled"
    elif not executable_exit_price:
        reason = "missing_executable_exit_bid"
    elif inverted_book:
        reason = "inverted_exit_book"
    elif crypto_fast_profit_exit:
        action = "exit"
        reason = "crypto_fast_take_profit"
    elif universal_profit_lock_exit:
        action = "exit"
        reason = profit_lock_reason or "profit_lock"
        if reason in {"trailing_profit_giveback", "green_to_red_peak_giveback"}:
            required_loss_confirmations = 1
    elif crypto_repricing_profit_exit:
        action = "exit"
        reason = "crypto_repricing_profit"
    elif crypto_momentum_reversal:
        action = "exit"
        reason = "crypto_reversal_protect"
    elif crypto_momentum_stall_exit:
        action = "exit"
        reason = "crypto_momentum_stall"
    elif crypto_near_expiry_profit:
        action = "exit"
        reason = "crypto_near_expiry_profit"
    elif is_sports_position and near_lock_harvest:
        action = "exit"
        reason = "near_lock_harvest"
    elif is_sports_position and fast_green_harvest:
        action = "exit"
        reason = "fast_green_harvest"
    elif is_sports_position and capital_rotation_harvest:
        action = "exit"
        reason = "capital_rotation_harvest"
    elif is_sports_position and sports_harvest_profit:
        action = "exit"
        reason = "harvest_profit"
    elif is_sports_position and dead_prop_exit:
        action = "exit"
        reason = "dead_prop_exit"
        required_loss_confirmations = 1
    elif is_sports_position and defensive_sports_collapse:
        action = "exit"
        reason = "defensive_exit"
        required_loss_confirmations = 1
    elif is_sports_position and late_game_decay_exit:
        action = "exit"
        reason = "late_game_decay_exit"
        required_loss_confirmations = 1
    elif spread is not None and spread > max_exit_spread and pnl_per_contract > severe_stop_loss and not is_sports_position:
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
    elif pnl_per_contract <= severe_stop_loss:
        action = "exit"
        reason = "severe_stop_loss"
        required_loss_confirmations = 1
    elif pnl_per_contract <= dynamic_stop_loss and adverse_movement:
        action = "exit"
        reason = "adverse_fast_cut"
        required_loss_confirmations = 1
    elif is_sports_position and probability_collapse and pnl_per_contract <= -max(0.01, base_stop_loss * 0.45):
        action = "exit"
        reason = "defensive_exit"
        required_loss_confirmations = 1
    elif late_or_stale_sports and weak_momentum and pnl_per_contract < 0.0:
        action = "exit"
        reason = "stale_sports_exit"
    elif pnl_per_contract <= -base_stop_loss:
        action = "exit"
        reason = "stop_loss"
    elif pnl_per_contract > 0.0 and weak_momentum and pnl_per_contract >= weak_take_profit:
        action = "exit"
        reason = "protect_profit"

    pregame_loss_exit_blocked = bool(
        action == "exit"
        and is_sports_position
        and pnl_per_contract < 0.0
        and (sports_pregame_exit_context or {}).get("is_pregame_or_future")
    )
    if pregame_loss_exit_blocked:
        action = "hold"
        reason = "pregame_loss_exit_blocked"

    if pnl_per_contract >= 0.0:
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
        "pnl_pct": pnl_pct,
        "current_probability": current_price,
        "recent_delta": recent_delta,
        "last_step_delta": last_step_delta,
        "imbalance": imbalance,
        "side_support": side_support,
        "dynamic_take_profit": round(dynamic_take_profit, 4),
        "dynamic_stop_loss": round(dynamic_stop_loss, 4),
        "quote_source": quote_source,
        "is_sports_position": bool(is_sports_position),
        "is_crypto_ladder_position": bool(is_crypto_ladder_position),
        "is_crypto_short_cycle": bool(is_crypto_short_cycle),
        "lane": "CRYPTO" if is_crypto_ladder_position else "SPORTS" if is_sports_position else "OTHER",
        "entry_cost": entry_cost,
        "current_value": current_value,
        "cashout_value": cashout_value,
        "max_payout": max_payout,
        "max_payout_pct": max_payout_pct,
        "profit_lock_unrealized_pnl": profit_lock_unrealized,
        "profit_lock_unrealized_profit_pct": profit_lock_pct,
        "peak_value": peak_value,
        "peak_profit": peak_profit,
        "peak_giveback_pct": peak_giveback_pct,
        "peak_value_updated": bool(peak_context.get("updated")),
        "profit_lock_exit": bool(universal_profit_lock_exit),
        "profit_lock_reason": profit_lock_reason,
        "profit_lock_dollar_exit": bool(profit_lock_dollar_exit),
        "profit_lock_pct_exit": bool(profit_lock_pct_exit),
        "max_payout_lock_exit": bool(max_payout_lock_exit),
        "trailing_profit_exit": bool(trailing_profit_exit),
        "green_momentum_flip_exit": bool(green_momentum_flip_exit),
        "green_to_red_peak_exit": bool(green_to_red_peak_exit),
        "near_expiry_profit_lock_exit": bool(near_expiry_profit_lock_exit),
        "sports_fast_take_profit_dollars": sports_profit_dollars,
        "sports_fast_take_profit_pct": sports_profit_pct,
        "sports_trailing_profit_giveback_pct": sports_trailing_pct,
        "sports_max_payout_lock_pct": sports_max_payout_pct,
        "crypto_asset": crypto_asset,
        "crypto_timeframe": crypto_timeframe,
        "crypto_fast_take_profit_dollars": fast_profit_target_dollars,
        "crypto_fast_take_profit_pct": fast_profit_target_pct,
        "crypto_fast_profit_exit": bool(crypto_fast_profit_exit),
        "crypto_repricing_profit_exit": bool(crypto_repricing_profit_exit),
        "crypto_momentum_reversal": bool(crypto_momentum_reversal),
        "crypto_reversal_protect_exit": bool(crypto_momentum_reversal),
        "crypto_momentum_stall_exit": bool(crypto_momentum_stall_exit),
        "crypto_near_expiry_profit": bool(crypto_near_expiry_profit),
        "crypto_momentum_direction": "aligned" if strong_momentum else "reversing" if weak_momentum else "flat",
        "crypto_momentum_strength": round(abs(recent_delta) + abs(last_step_delta) + max(0.0, abs(side_support)), 4),
        "minutes_left": minutes_left,
        "sports_lifecycle_state": sports_lifecycle_state,
        "sports_pregame_exit_context": sports_pregame_exit_context,
        "pregame_loss_exit_blocked": bool(pregame_loss_exit_blocked),
        "probability_collapse": bool(probability_collapse),
        "near_lock_harvest": bool(near_lock_harvest),
        "fast_green_harvest": bool(fast_green_harvest),
        "capital_rotation_harvest": bool(capital_rotation_harvest),
        "dead_prop_exit": bool(dead_prop_exit),
    }
    log_dynamic_exit_decision(bot, decision, log_cooldown_seconds=log_cooldown_seconds)
    log_profit_lock_decision(bot, decision, log_cooldown_seconds=log_cooldown_seconds)
    log_crypto_exit_decision(bot, decision, log_cooldown_seconds=log_cooldown_seconds)
    log_sports_harvest_decision(bot, decision, log_cooldown_seconds=log_cooldown_seconds)
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

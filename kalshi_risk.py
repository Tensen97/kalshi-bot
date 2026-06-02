"""Hard live-trading risk guards for kalshi_web."""

from collections import Counter
import json
import os
import re
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


def _open_positions(bot):
    try:
        return bot._get_open_live_positions() or []
    except Exception:
        return [
            position for position in (getattr(bot, "live_positions", None) or [])
            if isinstance(position, dict)
            and str(position.get("status", "open") or "open").strip().lower() == "open"
        ]


def _same_ticker_open(bot, ticker):
    ticker = str(ticker or "").strip()
    if not ticker:
        return False
    return any(
        str(position.get("ticker") or position.get("market_ticker") or "").strip() == ticker
        for position in _open_positions(bot)
        if isinstance(position, dict)
    )


def _refresh_live_positions_for_guard(bot, *, ticker=None, reason="live_guard", min_interval_sec=5.0):
    reconcile = getattr(bot, "reconcile_live_positions", None)
    if not callable(reconcile):
        return {"ok": True, "refreshed": False, "reason": "reconcile_unavailable"}
    now = time.time()
    last = 0.0
    try:
        last = float(getattr(bot, "_last_live_api_guard_reconcile_ts", 0.0) or 0.0)
    except Exception:
        last = 0.0
    if min_interval_sec and last > 0.0 and now - last < float(min_interval_sec):
        return {
            "ok": bool(getattr(bot, "_last_live_api_guard_reconcile_ok", True)),
            "refreshed": False,
            "reason": str(getattr(bot, "_last_live_api_guard_reconcile_reason", "") or "recent_live_api_snapshot"),
        }

    before_count = len(_open_positions(bot))
    before_same = _same_ticker_open(bot, ticker)
    try:
        result = reconcile()
    except Exception as exc:
        result = {"ok": False, "reason": type(exc).__name__}
    ok = bool(isinstance(result, dict) and result.get("ok"))
    result_reason = "live_api_reconciled" if ok else (result.get("reason") if isinstance(result, dict) else "live_api_reconcile_failed")
    setattr(bot, "_last_live_api_guard_reconcile_ts", now)
    setattr(bot, "_last_live_api_guard_reconcile_ok", ok)
    setattr(bot, "_last_live_api_guard_reconcile_reason", result_reason)
    after_count = len(_open_positions(bot))
    after_same = _same_ticker_open(bot, ticker)
    should_log = bool(ok and (before_count != after_count or before_same != after_same))
    should_log = bool(should_log or not ok)
    if should_log:
        try:
            print(
                "[AION-LIVE-STATE-OVERRIDE] "
                + json.dumps(
                    {
                        "ok": ok,
                        "reason": reason,
                        "ticker": ticker,
                        "before_open_positions": before_count,
                        "after_open_positions": after_count,
                        "before_same_ticker_open": before_same,
                        "after_same_ticker_open": after_same,
                        "api_reason": result.get("reason") if isinstance(result, dict) else None,
                    },
                    sort_keys=True,
                    default=str,
                )
            )
        except Exception:
            pass
    return {
        "ok": ok,
        "refreshed": True,
        "reason": result_reason,
    }


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
    _refresh_live_positions_for_guard(
        bot,
        ticker=ticker,
        reason="can_open_duplicate_guard",
        min_interval_sec=15.0,
    )
    if bot.already_holding(ticker):
        return False, "already_holding"
    return True, None


def should_enter_yes(bot, q, *, default_live_order_side):
    side = str(bot._read_field(q, "selected_side", "side", default=default_live_order_side) or "").strip().lower()
    return side in {"", "yes", "no", "buy_yes", "buy_no"}


def get_live_exposure_snapshot(bot, candidate_ticker=None, candidate_family=None):
    _refresh_live_positions_for_guard(
        bot,
        ticker=candidate_ticker,
        reason="exposure_snapshot",
        min_interval_sec=10.0,
    )
    open_positions = _open_positions(bot)
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
    live_state = _refresh_live_positions_for_guard(
        bot,
        ticker=ticker,
        reason="duplicate_live_api_guard",
        min_interval_sec=5.0,
    )
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
        position for position in _open_positions(bot)
        if isinstance(position, dict) and str(position.get("ticker") or "").strip() == ticker
    ]
    reason = "clear"
    if recent_order:
        reason = "recent_order_pending_confirmation"
    elif same_ticker_open:
        reason = "duplicate_position_guard"
    if same_ticker_open:
        try:
            print(
                "[AION-DUPLICATE-LIVE-API-GUARD] "
                + json.dumps(
                    {
                        "ticker": ticker,
                        "reason": reason,
                        "open_live_matches": len(matching),
                        "live_state_reason": live_state.get("reason"),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
        except Exception:
            pass
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
    for position in _open_positions(bot) or []:
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
        direct_exposure = bot.to_float(
            position.get("market_exposure_dollars")
            or position.get("exposure_dollars")
            or position.get("cost_dollars")
            or position.get("notional_dollars"),
            None,
        )
        if direct_exposure is not None and direct_exposure > 0.0:
            exposure += float(direct_exposure)
            continue
        direct_exposure_cents = bot.to_float(
            position.get("market_exposure_cents")
            or position.get("exposure_cents")
            or position.get("cost_cents")
            or position.get("notional_cents"),
            None,
        )
        if direct_exposure_cents is not None and direct_exposure_cents > 0.0:
            exposure += float(direct_exposure_cents) / 100.0
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
            or position.get("avg_price")
            or position.get("average_price")
            or position.get("price")
            or position.get("selected_price")
            or position.get("selected_price_dollars")
            or fallback_price,
        )
        if count > 0.0 and price is not None:
            exposure += float(count) * float(price)
    return max(0.0, exposure)


def _guard_key(value):
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _read_from_sources(sources, *names):
    for source in sources:
        if not source:
            continue
        if isinstance(source, dict):
            for name in names:
                value = source.get(name)
                if value not in (None, ""):
                    return value
        else:
            for name in names:
                value = getattr(source, name, None)
                if value not in (None, ""):
                    return value
    return None


def _position_side(bot, position):
    position = position if isinstance(position, dict) else {}
    side = str(position.get("side") or position.get("outcome_side") or "").strip().lower()
    if side in {"yes", "no"}:
        return side
    yes_count = bot.to_float(position.get("yes_count"), None)
    no_count = bot.to_float(position.get("no_count"), None)
    if no_count is not None and abs(float(no_count)) > 0.0 and not (yes_count is not None and abs(float(yes_count)) > 0.0):
        return "no"
    if yes_count is not None and abs(float(yes_count)) > 0.0:
        return "yes"
    raw_position = bot.to_float(position.get("position") if position.get("position") is not None else position.get("position_fp"), None)
    if raw_position is not None and raw_position < 0:
        return "no"
    return "yes"


def _position_count(bot, position):
    position = position if isinstance(position, dict) else {}
    value = bot.to_float(
        position.get("count")
        or position.get("contracts")
        or position.get("contract_count")
        or position.get("quantity")
        or position.get("qty")
        or position.get("position_fp")
        or position.get("position")
        or position.get("yes_count")
        or position.get("no_count"),
        0.0,
    )
    return abs(float(value or 0.0))


def _is_local_open_position(bot, entry):
    entry = entry if isinstance(entry, dict) else {}
    status = str(entry.get("status", "open") or "open").strip().lower()
    if status in {"closed", "settled", "cancelled", "canceled"}:
        return False
    return _position_count(bot, entry) > 0.0


def _local_position_state(bot):
    loader = getattr(bot, "load_live_position_state", None)
    if not callable(loader):
        return {}
    try:
        state = loader()
    except Exception:
        state = {}
    return state if isinstance(state, dict) else {}


def _open_positions_for_opposite_guard(bot, *, live_state=None):
    local_state = _local_position_state(bot)
    local_by_ticker = {
        str((entry or {}).get("ticker") or ticker or "").strip(): entry
        for ticker, entry in local_state.items()
        if isinstance(entry, dict)
    }
    api_ok = bool(isinstance(live_state, dict) and live_state.get("ok"))
    rows = []
    seen = set()
    for position in _open_positions(bot) or []:
        if not isinstance(position, dict):
            continue
        ticker = str(position.get("ticker") or position.get("market_ticker") or "").strip()
        if not ticker or _position_count(bot, position) <= 0.0:
            continue
        merged = dict(position)
        local = local_by_ticker.get(ticker)
        if isinstance(local, dict):
            for key, value in local.items():
                if key == "entry_context" and isinstance(value, dict) and not isinstance(merged.get("entry_context"), dict):
                    merged[key] = value
                elif merged.get(key) in (None, "") and value not in (None, ""):
                    merged[key] = value
        merged["_aion_position_source"] = "live_api" if api_ok else "local_or_runtime"
        rows.append(merged)
        seen.add(ticker)
    if not api_ok:
        for ticker, entry in local_by_ticker.items():
            if not ticker or ticker in seen or not _is_local_open_position(bot, entry):
                continue
            row = dict(entry)
            row["ticker"] = str(row.get("ticker") or ticker).strip()
            row["_aion_position_source"] = "local_state"
            rows.append(row)
    return rows


def _candidate_family(bot, ticker, sources):
    family = str(_read_from_sources(sources, "family", "series_ticker") or "").strip().upper()
    if not family and ticker:
        try:
            family = str(bot.get_market_family({"ticker": ticker}) or "").strip().upper()
        except Exception:
            family = ticker.split("-")[0].upper()
    return family or "UNKNOWN"


def _sports_event_key(bot, ticker, family, sources):
    game_id = _read_from_sources(sources, "game_id", "sports_graph_game_id")
    if game_id not in (None, ""):
        return f"{family}:{_guard_key(game_id)}"
    event_ticker = _read_from_sources(sources, "event_ticker")
    if event_ticker not in (None, ""):
        return f"{family}:{_guard_key(event_ticker)}"
    extractor = getattr(bot, "_extract_sports_game_key", None)
    if callable(extractor) and ticker:
        try:
            return f"{family}:{_guard_key(extractor(ticker))}"
        except Exception:
            pass
    parts = str(ticker or "").strip().split("-")
    if len(parts) >= 2:
        return f"{family}:{_guard_key(parts[1])}"
    return f"{family}:{_guard_key(ticker)}" if ticker else ""


def _outcome_slot(ticker):
    parts = str(ticker or "").strip().split("-")
    if len(parts) >= 3:
        return _guard_key(parts[-1])
    return ""


def _profile_for_opposite_guard(bot, *, ticker, side, payload=None, q=None, position=None):
    payload = payload if isinstance(payload, dict) else {}
    position = position if isinstance(position, dict) else {}
    entry_context = payload.get("entry_context") if isinstance(payload.get("entry_context"), dict) else {}
    pos_entry_context = position.get("entry_context") if isinstance(position.get("entry_context"), dict) else {}
    raw = position.get("raw") if isinstance(position.get("raw"), dict) else {}
    sources = [payload, entry_context, position, pos_entry_context, raw, q]
    family = _candidate_family(bot, ticker, sources)
    event_key = _sports_event_key(bot, ticker, family, sources)
    ladder_plan = _guard_key(_read_from_sources(sources, "sports_ladder_plan_id", "plan_id"))
    role = _guard_key(_read_from_sources(sources, "sports_ladder_role", "sports_graph_role", "market_type", "role"))
    thesis = _guard_key(_read_from_sources(sources, "game_thesis_type", "thesis_type", "sports_ladder_leg_type"))
    return {
        "ticker": ticker,
        "family": family,
        "side": side,
        "event_key": event_key,
        "outcome_slot": _outcome_slot(ticker),
        "ladder_plan": ladder_plan,
        "role": role,
        "thesis": thesis,
    }


def _exclusive_outcome_family(family):
    text = str(family or "").upper()
    return any(token in text for token in ("MONEYLINE", "MATCH", "WINNER", "GAME", "SPREAD"))


def _opposite_total_tokens(left, right):
    pair = {_guard_key(left), _guard_key(right)}
    return bool(
        {"OVER", "UNDER"} <= pair
        or {"O", "U"} <= pair
        or {"HIGH", "LOW"} <= pair
    )


def _opposite_side_conflict_reason(candidate, position):
    if not candidate.get("ticker") or not position.get("ticker"):
        return None
    same_ticker = candidate["ticker"] == position["ticker"]
    same_event = bool(candidate.get("event_key") and candidate.get("event_key") == position.get("event_key"))
    same_family = bool(candidate.get("family") and candidate.get("family") == position.get("family"))
    candidate_side = candidate.get("side")
    position_side = position.get("side")
    if same_ticker:
        if candidate_side in {"yes", "no"} and position_side in {"yes", "no"} and candidate_side != position_side:
            return "exact_ticker_opposite_side"
        return None
    if not (same_event and same_family):
        return None
    if candidate.get("ladder_plan") and candidate.get("ladder_plan") == position.get("ladder_plan"):
        if candidate_side != position_side or candidate.get("outcome_slot") != position.get("outcome_slot"):
            return "same_ladder_opposite_exposure"
    if candidate_side in {"yes", "no"} and position_side in {"yes", "no"}:
        if candidate_side != position_side and candidate.get("outcome_slot") == position.get("outcome_slot"):
            return "same_event_opposite_side"
        if candidate_side == position_side and candidate.get("outcome_slot") and candidate.get("outcome_slot") != position.get("outcome_slot"):
            if _exclusive_outcome_family(candidate.get("family")):
                return "same_event_competing_outcome"
            if "TOTAL" in str(candidate.get("family") or "").upper() and _opposite_total_tokens(candidate.get("outcome_slot"), position.get("outcome_slot")):
                return "same_event_total_opposite_outcome"
    if candidate.get("role") and candidate.get("role") == position.get("role") and candidate_side != position_side:
        return "same_role_opposite_side"
    if candidate.get("thesis") and candidate.get("thesis") == position.get("thesis") and candidate_side != position_side:
        return "same_thesis_opposite_side"
    return None


def _emit_opposite_side_log(tag, payload):
    try:
        print(f"[{tag}] " + json.dumps(payload, sort_keys=True, default=str))
    except Exception:
        pass


def evaluate_opposite_side_pre_submit_guard(bot, payload, q=None, *, source="pre_submit", refresh=True, live_state=None):
    payload = dict(payload or {})
    ticker = str(payload.get("ticker") or getattr(q, "ticker", "") or "").strip()
    side = str(payload.get("selected_side") or payload.get("side") or getattr(q, "selected_side", None) or getattr(q, "side", None) or "").strip().lower()
    action = str(payload.get("action") or "buy").strip().lower()
    reduce_only = bool(payload.get("reduce_only"))
    if side not in {"yes", "no"}:
        return {"ok": False, "reason": "missing_selected_side", "ticker": ticker or None, "source": source}
    if not ticker:
        return {"ok": False, "reason": "missing_ticker", "source": source}
    if action != "buy" or reduce_only:
        result = {
            "ok": True,
            "reason": "reduce_only_or_non_buy_allowed",
            "ticker": ticker,
            "side": side,
            "action": action,
            "reduce_only": reduce_only,
            "source": source,
        }
        if reduce_only or action == "sell":
            _emit_opposite_side_log("AION-OPPOSITE-SIDE-ALLOW-REDUCE-ONLY", result)
        return result
    if refresh:
        live_state = _refresh_live_positions_for_guard(
            bot,
            ticker=ticker,
            reason="opposite_side_pre_submit",
            min_interval_sec=0.0,
        )
    candidate = _profile_for_opposite_guard(bot, ticker=ticker, side=side, payload=payload, q=q)
    matches = []
    for position in _open_positions_for_opposite_guard(bot, live_state=live_state):
        position_ticker = str(position.get("ticker") or position.get("market_ticker") or "").strip()
        if not position_ticker:
            continue
        pos_side = _position_side(bot, position)
        pos_profile = _profile_for_opposite_guard(
            bot,
            ticker=position_ticker,
            side=pos_side,
            position=position,
        )
        reason = _opposite_side_conflict_reason(candidate, pos_profile)
        if not reason:
            continue
        matches.append(
            {
                "ticker": position_ticker,
                "side": pos_side,
                "reason": reason,
                "event_key": pos_profile.get("event_key"),
                "outcome_slot": pos_profile.get("outcome_slot"),
                "source": position.get("_aion_position_source"),
                "count": _position_count(bot, position),
            }
        )
    if matches:
        result = {
            "ok": False,
            "reason": "opposite_side_conflict",
            "ticker": ticker,
            "side": side,
            "family": candidate.get("family"),
            "event_key": candidate.get("event_key"),
            "outcome_slot": candidate.get("outcome_slot"),
            "conflict_count": len(matches),
            "conflicts": matches[:6],
            "live_state_reason": (live_state or {}).get("reason") if isinstance(live_state, dict) else None,
            "source": source,
        }
        _emit_opposite_side_log("AION-OPPOSITE-SIDE-AUDIT", result)
        _emit_opposite_side_log("AION-OPPOSITE-SIDE-BLOCK", result)
        return result
    return {
        "ok": True,
        "reason": "no_opposite_side_exposure",
        "ticker": ticker,
        "side": side,
        "family": candidate.get("family"),
        "event_key": candidate.get("event_key"),
        "live_state_reason": (live_state or {}).get("reason") if isinstance(live_state, dict) else None,
        "source": source,
    }


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
    live_state = _refresh_live_positions_for_guard(
        bot,
        ticker=ticker,
        reason="risk_governor_pre_submit",
        min_interval_sec=2.0,
    )
    if (
        not live_state.get("ok", True)
        and str(live_state.get("reason") or "").strip().lower()
        not in {"missing_auth_config", "auth_not_ready", "live_auth_unavailable"}
    ):
        return _block("live_position_reconcile_unavailable")
    opposite_guard = evaluate_opposite_side_pre_submit_guard(
        bot,
        payload,
        q=q,
        source="risk_governor_pre_submit",
        refresh=False,
        live_state=live_state,
    )
    if not opposite_guard.get("ok"):
        blocked = _block(opposite_guard.get("reason") or "opposite_side_conflict")
        blocked["opposite_side_guard"] = opposite_guard
        return blocked
    if bot.already_holding(ticker):
        try:
            print(
                "[AION-DUPLICATE-LIVE-API-GUARD] "
                + json.dumps(
                    {
                        "ticker": ticker,
                        "family": family,
                        "reason": "duplicate_position_guard",
                        "live_state_reason": live_state.get("reason"),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
        except Exception:
            pass
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
    if current_family_exposure > 0.0:
        try:
            print(
                "[AION-EXPOSURE-RECONCILE] "
                + json.dumps(
                    {
                        "ticker": ticker,
                        "family": family,
                        "current_family_exposure_dollars": round(current_family_exposure, 4),
                        "candidate_notional_dollars": round(float(count) * float(selected_price), 4),
                        "max_family_exposure_dollars": round(max_family, 4),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
        except Exception:
            pass
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

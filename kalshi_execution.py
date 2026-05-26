"""Live order execution helpers for kalshi_web."""

import hashlib
import json
import os
import time


def _env_bool(name, default=True):
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return int(default)


def ensure_live_client_order_id(payload):
    payload = dict(payload or {})
    if not payload.get("client_order_id"):
        seed = f"{payload.get('ticker')}:{time.time_ns()}:{os.getpid()}"
        payload["client_order_id"] = "aion-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return payload


def normalize_legacy_order_payload(
    bot,
    payload,
    *,
    default_live_order_side,
    default_live_order_action,
    max_live_order_size,
):
    payload = bot._ensure_live_client_order_id(payload)
    ticker = str(payload.get("ticker") or "").strip()
    side = str(payload.get("selected_side") or payload.get("side") or default_live_order_side).strip().lower()
    if side not in {"yes", "no"}:
        side = default_live_order_side
    action = str(payload.get("action") or default_live_order_action).strip().lower()
    count = int(max(1, min(int(bot.to_float(payload.get("count"), 1) or 1), int(max_live_order_size or 1))))
    price_cents = payload.get("yes_price") if side == "yes" else payload.get("no_price")
    price_dollars = payload.get("selected_price_dollars") or payload.get("selected_price") or payload.get("price")
    if price_cents in (None, "", 0, 0.0):
        normalized_price = bot.normalize_contract_price(price_dollars, ticker=ticker, source="legacy_order_payload")
        if normalized_price is None:
            return None
        price_cents = int(round(float(normalized_price) * 100.0))
    price_cents = max(1, min(99, int(round(float(price_cents)))))
    normalized_price = round(price_cents / 100.0, 4)
    order = {
        "ticker": ticker,
        "side": side,
        "action": action,
        "client_order_id": payload["client_order_id"],
        "count": count,
        "type": "limit",
        "time_in_force": "fill_or_kill",
        "post_only": bool(payload.get("post_only", False)),
        "reduce_only": bool(payload.get("reduce_only", False)),
        "self_trade_prevention_type": "taker_at_cross",
    }
    if side == "yes":
        order["yes_price"] = price_cents
        order["yes_price_dollars"] = f"{normalized_price:.4f}"
    else:
        order["no_price"] = price_cents
        order["no_price_dollars"] = f"{normalized_price:.4f}"
    return order


def normalize_event_order_payload(
    bot,
    legacy_payload,
    *,
    default_live_order_side,
    default_live_order_action,
):
    legacy_payload = dict(legacy_payload or {})
    side = str(legacy_payload.get("side") or default_live_order_side).strip().lower()
    action = str(legacy_payload.get("action") or default_live_order_action).strip().lower()
    selected_price = bot.normalize_contract_price(
        legacy_payload.get("yes_price_dollars")
        if side == "yes"
        else legacy_payload.get("no_price_dollars"),
        ticker=legacy_payload.get("ticker"),
        source="event_order_payload",
    )
    if selected_price is None:
        return None
    if side == "yes":
        event_side = "bid" if action == "buy" else "ask"
        yes_side_price = selected_price
    else:
        event_side = "ask" if action == "buy" else "bid"
        yes_side_price = round(1.0 - selected_price, 4)
    yes_side_price = max(0.01, min(0.99, yes_side_price))
    return {
        "ticker": legacy_payload.get("ticker"),
        "client_order_id": legacy_payload.get("client_order_id"),
        "side": event_side,
        "count": f"{bot.to_float(legacy_payload.get('count'), 1.0) or 1.0:.2f}",
        "price": f"{yes_side_price:.4f}",
        "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": bool(legacy_payload.get("post_only", False)),
        "cancel_order_on_pause": True,
        "reduce_only": bool(legacy_payload.get("reduce_only", False)),
        "subaccount": 0,
        "exchange_index": 0,
    }


def place_live_order(bot, payload):
    payload = payload if isinstance(payload, dict) else {}
    ticker_hint = str(payload.get("ticker") or "").strip()
    family_hint = str(payload.get("family") or "").strip().upper()
    if not family_hint and ticker_hint:
        family_resolver = getattr(bot, "get_market_family", None)
        if callable(family_resolver):
            try:
                family_hint = str(family_resolver(payload) or "").strip().upper()
            except Exception:
                family_hint = ""
    sports_only_blocker = getattr(bot, "_sports_only_execution_blocked", None)
    if callable(sports_only_blocker) and (ticker_hint or family_hint):
        lane_hint = ""
        lane_resolver = getattr(bot, "_candidate_execution_lane", None)
        if callable(lane_resolver):
            try:
                lane_hint = lane_resolver(family=family_hint, ticker=ticker_hint, market=payload, q=None)
            except Exception:
                lane_hint = ""
        reason = "sports_only_non_sports_execution_disabled"
        reason_resolver = getattr(bot, "_sports_only_block_reason", None)
        if callable(reason_resolver):
            try:
                reason = reason_resolver(family=family_hint, ticker=ticker_hint, market=payload, q=None, lane=lane_hint)
            except Exception:
                reason = "sports_only_non_sports_execution_disabled"
        try:
            if sports_only_blocker(
                family=family_hint,
                ticker=ticker_hint,
                market=payload,
                q=None,
                lane=lane_hint,
                reason=reason,
                source="kalshi_execution.place_live_order",
                force=True,
            ):
                return {
                    "submitted": False,
                    "reason": reason,
                    "payload": payload,
                }
        except Exception:
            pass
    legacy_payload = bot._normalize_legacy_order_payload(payload)
    if not legacy_payload:
        return {"submitted": False, "reason": "invalid_payload", "payload": payload}
    event_payload = bot._normalize_event_order_payload(legacy_payload)
    attempts = []
    request_plan = []
    if event_payload:
        request_plan.append(("/trade-api/v2/portfolio/events/orders", event_payload, "events_v2"))
    request_plan.append(("/trade-api/v2/portfolio/orders", legacy_payload, "legacy"))
    for path, request_payload, endpoint_kind in request_plan:
        print(
            "[ORDER-SUBMIT] "
            + json.dumps(
                {
                    "ticker": legacy_payload.get("ticker"),
                    "side": legacy_payload.get("side"),
                    "endpoint": path,
                    "endpoint_kind": endpoint_kind,
                    "client_order_id": legacy_payload.get("client_order_id"),
                },
                sort_keys=True,
                default=str,
            )
        )
        response = bot._live_request_json("POST", path, json_payload=request_payload, timeout=20)
        attempts.append({
            "path": path,
            "endpoint_kind": endpoint_kind,
            "status_code": response.get("status_code"),
            "reason": response.get("reason"),
            "body": response.get("body"),
        })
        if response.get("ok"):
            return {
                "submitted": True,
                "reason": "submitted",
                "endpoint": path,
                "endpoint_kind": endpoint_kind,
                "payload": legacy_payload,
                "request_payload": request_payload,
                "response": response.get("body"),
                "status_code": response.get("status_code"),
                "attempts": attempts,
            }
        if response.get("status_code") in {401, 403}:
            break
    return {
        "submitted": False,
        "reason": attempts[-1].get("reason") if attempts else "request_not_sent",
        "payload": legacy_payload,
        "status_code": attempts[-1].get("status_code") if attempts else None,
        "attempts": attempts,
    }


def _auto_reprice_config():
    return {
        "enabled": _env_bool("ENABLE_AUTO_REPRICING", True),
        "min_age_seconds": max(0.0, _env_float("AUTO_REPRICE_MIN_AGE_SECONDS", 20.0)),
        "cooldown_seconds": max(1.0, _env_float("AUTO_REPRICE_COOLDOWN_SECONDS", 30.0)),
        "max_attempts": max(0, _env_int("AUTO_REPRICE_MAX_ATTEMPTS", 2)),
        "max_price_move_cents": max(1, min(20, _env_int("AUTO_REPRICE_MAX_PRICE_MOVE_CENTS", 3))),
    }


def _auto_reprice_log(tag, **fields):
    try:
        body = " ".join(f"{key}={value}" for key, value in fields.items())
        print(f"[{tag}] {body}".rstrip())
    except Exception:
        pass


def _payload_value(bot, payload, q, *names, default=None):
    payload = payload if isinstance(payload, dict) else {}
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return value
    if q is not None:
        for name in names:
            value = getattr(q, name, None)
            if value not in (None, ""):
                return value
    return default


def _normalized_price(bot, value, ticker=None, source="auto_reprice"):
    price = bot.normalize_contract_price(value, ticker=ticker, source=source)
    return bot.to_float(price, None)


def _price_cents(bot, value, ticker=None, source="auto_reprice"):
    price = _normalized_price(bot, value, ticker=ticker, source=source)
    if price is None:
        return None
    try:
        cents = int(round(float(price) * 100.0))
    except (TypeError, ValueError):
        return None
    return max(1, min(99, cents))


def _extract_current_book(bot, payload, q, *, ticker, side):
    side = str(side or "").strip().lower()
    bid_key = "yes_bid" if side == "yes" else "no_bid"
    ask_key = "yes_ask" if side == "yes" else "no_ask"
    bid = _normalized_price(
        bot,
        _payload_value(bot, payload, q, bid_key, f"{bid_key}_dollars"),
        ticker=ticker,
        source=f"auto_reprice.{bid_key}",
    )
    ask = _normalized_price(
        bot,
        _payload_value(bot, payload, q, ask_key, f"{ask_key}_dollars"),
        ticker=ticker,
        source=f"auto_reprice.{ask_key}",
    )
    if bid is None or ask is None:
        return {"ok": False, "reason": "missing_side_bid_ask", "bid": bid, "ask": ask}
    if bid <= 0.0 or ask <= 0.0:
        return {"ok": False, "reason": "fake_book", "bid": bid, "ask": ask}
    if ask < bid:
        return {"ok": False, "reason": "inverted_book", "bid": bid, "ask": ask}
    return {
        "ok": True,
        "reason": "ok",
        "bid": bid,
        "ask": ask,
        "bid_cents": max(1, min(99, int(round(bid * 100.0)))),
        "ask_cents": max(1, min(99, int(round(ask * 100.0)))),
        "spread": round(max(0.0, ask - bid), 4),
    }


def _edge_vanished(bot, payload, q):
    edge_fields = (
        "ev_edge",
        "edge_after_cost",
        "final_edge",
        "expected_edge",
    )
    roi_fields = ("expected_roi",)
    for field in edge_fields:
        value = bot.to_float(_payload_value(bot, payload, q, field), None)
        if value is not None:
            return value <= -0.002, f"{field}={round(value, 6)}"
    for field in roi_fields:
        value = bot.to_float(_payload_value(bot, payload, q, field), None)
        if value is not None:
            return value <= -0.01, f"{field}={round(value, 6)}"
    ev_score = bot.to_float(_payload_value(bot, payload, q, "ev_score"), None)
    if ev_score is not None:
        return ev_score < -0.01, f"ev_score={round(ev_score, 6)}"
    return False, "edge_unknown_assumed_valid"


def _momentum_aligned(bot, payload, q, side):
    side = str(side or "").strip().lower()
    momentum = bot.to_float(
        _payload_value(
            bot,
            payload,
            q,
            "momentum_score",
            "momentum",
            "sports_momentum",
            "edge_momentum_alignment",
        ),
        0.0,
    ) or 0.0
    direction = str(_payload_value(bot, payload, q, "momentum_direction", default="") or "").strip().lower()
    if side == "yes" and direction == "up":
        momentum = max(momentum, 0.30)
    elif side == "no" and direction == "down":
        momentum = max(momentum, 0.30)
    return momentum >= 0.25, momentum


def _fetch_live_order_status(bot, order_id):
    order_id = str(order_id or "").strip()
    if not order_id:
        return {"ok": False, "reason": "missing_order_id", "status": "unknown"}
    result = bot._live_request_json("GET", f"/trade-api/v2/portfolio/orders/{order_id}", timeout=10)
    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    root = body.get("order") if isinstance(body.get("order"), dict) else body
    status = str(root.get("status") or root.get("state") or "").strip().lower() or "unknown"
    return {
        "ok": bool(result.get("ok")),
        "reason": result.get("reason"),
        "status_code": result.get("status_code"),
        "status": status,
        "body": body,
    }


def maybe_auto_reprice_recent_order(
    bot,
    recent_order,
    current_payload,
    q=None,
    *,
    default_live_order_side,
    max_live_trades_per_day,
    live_order_rate_limit,
):
    config = _auto_reprice_config()
    recent_order = recent_order if isinstance(recent_order, dict) else {}
    current_payload = current_payload if isinstance(current_payload, dict) else {}
    ticker = str(recent_order.get("ticker") or current_payload.get("ticker") or "").strip()
    order_id = str(recent_order.get("order_id") or "").strip()
    age = bot.to_float(recent_order.get("age_seconds"), 0.0) or 0.0
    if not ticker:
        return {"handled": False, "submitted": False, "reason": "missing_ticker"}
    if not config["enabled"]:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="disabled")
        return {"handled": False, "submitted": False, "reason": "disabled"}
    status_result = _fetch_live_order_status(bot, order_id) if order_id else {"status": "unknown", "reason": "missing_order_id"}
    status = str(status_result.get("status") or "unknown").strip().lower()
    _auto_reprice_log(
        "AUTO-REPRICE-CHECK",
        ticker=ticker,
        order_id=order_id or None,
        age=round(age, 3),
        status=status,
    )
    if age < config["min_age_seconds"]:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="too_young")
        return {"handled": False, "submitted": False, "reason": "too_young"}
    state = getattr(bot, "auto_reprice_state", None)
    if not isinstance(state, dict):
        bot.auto_reprice_state = {}
        state = bot.auto_reprice_state
    order_key = order_id or ticker
    entry = state.get(order_key) if isinstance(state.get(order_key), dict) else {}
    attempts = int(bot.to_float(entry.get("attempts"), 0.0) or 0)
    if attempts >= config["max_attempts"]:
        _auto_reprice_log("AUTO-REPRICE-DONE", ticker=ticker, attempts=attempts)
        return {"handled": False, "submitted": False, "reason": "max_attempts_reached"}
    last_ts = bot.to_float(entry.get("last_ts"), 0.0) or 0.0
    now_ts = time.time()
    if last_ts and now_ts - last_ts < config["cooldown_seconds"]:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="cooldown")
        return {"handled": False, "submitted": False, "reason": "cooldown"}
    if status in {"filled", "executed", "closed", "matched"}:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="already_filled")
        return {"handled": False, "submitted": False, "reason": "already_filled"}
    if bot.already_holding(ticker):
        if order_id and hasattr(bot, "cancel_live_order"):
            cancel_result = bot.cancel_live_order(order_id)
            _auto_reprice_log(
                "AUTO-REPRICE-CANCEL",
                ticker=ticker,
                old_order_id=order_id,
                reason="duplicate_position_guard",
            )
            if hasattr(bot, "append_live_audit_log") and cancel_result.get("ok"):
                bot.append_live_audit_log("order_cancelled", ticker=ticker, order_id=order_id, reason="duplicate_position_guard", status_code=cancel_result.get("status_code"))
        return {"handled": True, "submitted": False, "reason": "duplicate_position_guard"}

    old_side = str(recent_order.get("side") or "").strip().lower()
    current_side = str(
        current_payload.get("selected_side")
        or current_payload.get("side")
        or getattr(q, "selected_side", None)
        or getattr(q, "side", None)
        or default_live_order_side
        or ""
    ).strip().lower()
    side = old_side if old_side in {"yes", "no"} else current_side
    if side not in {"yes", "no"}:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="missing_side")
        return {"handled": False, "submitted": False, "reason": "missing_side"}
    if old_side in {"yes", "no"} and current_side in {"yes", "no"} and old_side != current_side:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="side_changed")
        return {"handled": False, "submitted": False, "reason": "side_changed"}

    edge_gone, edge_reason = _edge_vanished(bot, current_payload, q)
    if edge_gone:
        if order_id and hasattr(bot, "cancel_live_order"):
            cancel_result = bot.cancel_live_order(order_id)
            _auto_reprice_log("AUTO-REPRICE-CANCEL", ticker=ticker, old_order_id=order_id, reason=f"edge_vanished:{edge_reason}")
            if hasattr(bot, "append_live_audit_log") and cancel_result.get("ok"):
                bot.append_live_audit_log("order_cancelled", ticker=ticker, order_id=order_id, side=side, reason=f"edge_vanished:{edge_reason}", status_code=cancel_result.get("status_code"))
        return {"handled": True, "submitted": False, "reason": "edge_vanished"}

    book = _extract_current_book(bot, current_payload, q, ticker=ticker, side=side)
    if not book.get("ok"):
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason=f"invalid_orderbook:{book.get('reason')}")
        return {"handled": False, "submitted": False, "reason": f"invalid_orderbook:{book.get('reason')}"}
    action = str(recent_order.get("action") or current_payload.get("action") or "buy").strip().lower()
    if action not in {"buy", "sell"}:
        action = "buy"
    old_price_cents = _price_cents(
        bot,
        recent_order.get("price")
        or current_payload.get("selected_price")
        or current_payload.get("selected_price_dollars")
        or current_payload.get("price"),
        ticker=ticker,
        source="auto_reprice.old_price",
    )
    if old_price_cents is None:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="missing_old_price")
        return {"handled": False, "submitted": False, "reason": "missing_old_price"}
    target_cents = int(book.get("ask_cents") if action == "buy" else book.get("bid_cents"))
    if action == "buy" and old_price_cents >= target_cents:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="already_at_executable_price")
        return {"handled": False, "submitted": False, "reason": "already_at_executable_price"}
    if action == "sell" and old_price_cents <= target_cents:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="already_at_executable_price")
        return {"handled": False, "submitted": False, "reason": "already_at_executable_price"}
    strong_momentum, momentum_value = _momentum_aligned(bot, current_payload, q, side)
    spread = bot.to_float(book.get("spread"), 0.0) or 0.0
    max_move = int(config["max_price_move_cents"])
    if not strong_momentum and spread > 0.06:
        max_move = min(max_move, 1)
    elif strong_momentum or spread <= 0.03:
        max_move = min(max_move + 1, int(config["max_price_move_cents"]))
    if action == "buy":
        new_price_cents = min(target_cents, old_price_cents + max_move)
    else:
        new_price_cents = max(target_cents, old_price_cents - max_move)
    new_price_cents = max(1, min(99, int(new_price_cents)))
    if new_price_cents == old_price_cents:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="no_price_improvement")
        return {"handled": False, "submitted": False, "reason": "no_price_improvement"}

    authorization = bot.get_live_submit_authorization(current_payload)
    if not authorization.get("ok"):
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason=authorization.get("reason"))
        return {"handled": False, "submitted": False, "reason": authorization.get("reason")}
    if not bot.can_build_live_auth():
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="auth_not_ready")
        return {"handled": False, "submitted": False, "reason": "auth_not_ready"}
    bot._reset_live_trade_counter_if_needed()
    if int(bot.live_trade_count_today or 0) >= int(max_live_trades_per_day or 0):
        if order_id and hasattr(bot, "cancel_live_order"):
            cancel_result = bot.cancel_live_order(order_id)
            _auto_reprice_log("AUTO-REPRICE-CANCEL", ticker=ticker, old_order_id=order_id, reason="daily_trade_cap")
            if hasattr(bot, "append_live_audit_log") and cancel_result.get("ok"):
                bot.append_live_audit_log("order_cancelled", ticker=ticker, order_id=order_id, side=side, reason="daily_trade_cap", status_code=cancel_result.get("status_code"))
        return {"handled": True, "submitted": False, "reason": "daily_trade_cap"}
    active_timestamps = bot._active_live_order_timestamps()
    if len(active_timestamps) >= int(live_order_rate_limit or 1):
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="rate_limited")
        return {"handled": False, "submitted": False, "reason": "rate_limited"}

    if order_id and status in {"canceled", "cancelled", "rejected", "expired"} and hasattr(bot, "append_live_audit_log"):
        bot.append_live_audit_log(
            "order_cancelled",
            ticker=ticker,
            order_id=order_id,
            side=side,
            reason=f"order_status_{status}",
            status_code=status_result.get("status_code"),
        )
    if order_id and status not in {"canceled", "cancelled", "rejected", "expired"}:
        cancel_result = bot.cancel_live_order(order_id) if hasattr(bot, "cancel_live_order") else {"ok": False, "reason": "cancel_helper_missing"}
        _auto_reprice_log(
            "AUTO-REPRICE-CANCEL",
            ticker=ticker,
            old_order_id=order_id,
            reason="stale_replace",
        )
        if not cancel_result.get("ok"):
            _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id, reason=f"cancel_failed:{cancel_result.get('reason')}")
            return {"handled": False, "submitted": False, "reason": "cancel_failed", "cancel_result": cancel_result}
        if hasattr(bot, "append_live_audit_log"):
            bot.append_live_audit_log("order_cancelled", ticker=ticker, order_id=order_id, side=side, reason="auto_reprice_stale_replace", status_code=cancel_result.get("status_code"))

    reprice_payload = dict(current_payload)
    reprice_payload.pop("client_order_id", None)
    reprice_payload["ticker"] = ticker
    reprice_payload["side"] = side
    reprice_payload["selected_side"] = side
    reprice_payload["action"] = action
    reprice_payload["selected_price"] = round(new_price_cents / 100.0, 4)
    reprice_payload["selected_price_dollars"] = round(new_price_cents / 100.0, 4)
    reprice_payload["selected_price_cents"] = new_price_cents
    reprice_payload["price"] = round(new_price_cents / 100.0, 4)
    reprice_payload.pop("yes_price", None)
    reprice_payload.pop("no_price", None)
    if side == "yes":
        reprice_payload["yes_price"] = new_price_cents
    else:
        reprice_payload["no_price"] = new_price_cents
    if recent_order.get("count") not in (None, ""):
        reprice_payload["count"] = recent_order.get("count")
    legacy_payload = bot._normalize_legacy_order_payload(reprice_payload)
    if not legacy_payload:
        _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason="invalid_reprice_payload")
        return {"handled": False, "submitted": False, "reason": "invalid_reprice_payload"}
    _auto_reprice_log(
        "AUTO-REPRICE-SUBMIT",
        ticker=ticker,
        side=side,
        old_price=round(old_price_cents / 100.0, 4),
        new_price=round(new_price_cents / 100.0, 4),
        reason=f"spread={round(spread, 4)} momentum={round(momentum_value, 4)} edge={edge_reason}",
    )
    result = bot.place_live_order({**reprice_payload, **legacy_payload})
    state[order_key] = {"attempts": attempts + 1, "last_ts": now_ts, "ticker": ticker}
    if result.get("submitted"):
        bot._live_order_timestamps.append(time.time())
        bot.live_trade_count_today = int(bot.live_trade_count_today or 0) + 1
        order_summary = bot.extract_order_execution_summary(result.get("response") or {})
        new_order_id = order_summary.get("order_id")
        bot.persist_live_entry_metadata(order=result.get("response") or {}, payload=reprice_payload, order_id=new_order_id)
        bot.append_live_audit_log(
            "order_reprice_submitted",
            ticker=ticker,
            order_id=new_order_id,
            side=legacy_payload.get("side"),
            action=legacy_payload.get("action"),
            count=legacy_payload.get("count"),
            price=reprice_payload.get("price"),
            reason="auto_reprice",
            status_code=result.get("status_code"),
        )
        archive_writer = getattr(bot, "append_aion_learning_archive_event", None)
        if callable(archive_writer):
            archive_writer(
                "trade_opened",
                q=q,
                payload=reprice_payload,
                order=result.get("response") or {},
                result=result,
                extra={
                    "ticker": ticker,
                    "order_id": new_order_id,
                    "entry_reason": "auto_reprice",
                    "entry_mode": "auto_reprice",
                    "phase_or_realm_source": "kalshi_execution.maybe_auto_reprice_recent_order",
                    "sports_tags": getattr(q, "sports_edge_tags", None) or getattr(q, "sports_tags", None),
                    "sports_edge_boost": getattr(q, "sports_edge_rank_boost", None),
                    "sports_score_at_entry": getattr(q, "sports_edge_score_at_entry", None),
                    "structural_edge_context": getattr(q, "structural_edge_context", None),
                    "structural_tags": getattr(q, "structural_edge_tags", None),
                    "structural_pattern_keys": getattr(q, "structural_pattern_keys", None),
                    "STRUCTURAL_EDGE_SCORE": getattr(q, "STRUCTURAL_EDGE_SCORE", None),
                    "CROSS_MARKET_EDGE_SCORE": getattr(q, "CROSS_MARKET_EDGE_SCORE", None),
                    "BTC_1H_LADDER_SCORE": getattr(q, "BTC_1H_LADDER_SCORE", None),
                    "btc_1h_ladder_context": getattr(q, "btc_1h_ladder_context", None),
                    "ELITE_EDGE_CONFIDENCE": getattr(q, "ELITE_EDGE_CONFIDENCE", None),
                    "elite_edge_context": getattr(q, "elite_edge_context", None),
                    "cross_market_edge_context": getattr(q, "cross_market_edge_context", None),
                },
            )
        print(
            "[ORDER-SUBMITTED] "
            + json.dumps(
                {
                    "ticker": ticker,
                    "side": legacy_payload.get("side"),
                    "price": reprice_payload.get("price"),
                    "count": legacy_payload.get("count"),
                    "endpoint": result.get("endpoint"),
                    "lane": "auto_reprice",
                    "order_id": new_order_id,
                },
                sort_keys=True,
                default=str,
            )
        )
        _auto_reprice_log("AUTO-REPRICE-DONE", ticker=ticker, attempts=attempts + 1)
        result["payload"] = reprice_payload
        result["reason"] = "auto_reprice_submitted"
        result["auto_reprice"] = True
        return result
    bot.append_live_audit_log(
        "order_reprice_failed",
        ticker=ticker,
        side=legacy_payload.get("side"),
        action=legacy_payload.get("action"),
        count=legacy_payload.get("count"),
        price=reprice_payload.get("price"),
        reason=result.get("reason"),
        status_code=result.get("status_code"),
    )
    _auto_reprice_log("AUTO-REPRICE-SKIP", ticker=ticker, order_id=order_id or None, reason=f"submit_failed:{result.get('reason')}")
    result["payload"] = reprice_payload
    result["reason"] = result.get("reason") or "auto_reprice_submit_failed"
    result["auto_reprice"] = True
    return result


def submit_live_order(
    bot,
    q,
    size=None,
    lane=None,
    payload_override=None,
    *,
    default_live_order_side,
    max_live_trades_per_day,
    max_simultaneous_live_positions,
    max_sports_live_positions,
    live_order_rate_limit,
):
    payload_hint = dict(payload_override) if isinstance(payload_override, dict) else {}
    ticker_hint = str(payload_hint.get("ticker") or getattr(q, "ticker", "") or "").strip()
    family_hint = str(payload_hint.get("family") or getattr(q, "family", "") or "").strip().upper()
    if not family_hint and ticker_hint:
        family_resolver = getattr(bot, "get_market_family", None)
        if callable(family_resolver):
            try:
                family_hint = str(family_resolver(payload_hint or {"ticker": ticker_hint}) or "").strip().upper()
            except Exception:
                family_hint = ""
    sports_only_blocker = getattr(bot, "_sports_only_execution_blocked", None)
    if callable(sports_only_blocker) and (ticker_hint or family_hint):
        market_hint = payload_hint or {"ticker": ticker_hint, "series_ticker": family_hint}
        lane_hint = str(lane or "").strip().upper()
        lane_resolver = getattr(bot, "_candidate_execution_lane", None)
        if not lane_hint and callable(lane_resolver):
            try:
                lane_hint = lane_resolver(family=family_hint, ticker=ticker_hint, market=market_hint, q=q)
            except Exception:
                lane_hint = ""
        reason = "sports_only_non_sports_execution_disabled"
        reason_resolver = getattr(bot, "_sports_only_block_reason", None)
        if callable(reason_resolver):
            try:
                reason = reason_resolver(family=family_hint, ticker=ticker_hint, market=market_hint, q=q, lane=lane_hint)
            except Exception:
                reason = "sports_only_non_sports_execution_disabled"
        try:
            if sports_only_blocker(
                family=family_hint,
                ticker=ticker_hint,
                market=market_hint,
                q=q,
                lane=lane_hint,
                reason=reason,
                source="kalshi_execution.submit_live_order",
            ):
                return {
                    "submitted": False,
                    "reason": reason,
                    "payload": payload_hint or None,
                }
        except Exception:
            pass
    payload = dict(payload_override) if isinstance(payload_override, dict) else bot.build_live_order_payload(q, size=size)
    if not payload:
        return {"submitted": False, "reason": "invalid_payload", "payload": payload}
    dynamic_size_resolver = getattr(bot, "_resolve_dynamic_live_entry_size", None)
    if callable(dynamic_size_resolver):
        try:
            payload["count"] = dynamic_size_resolver(
                q,
                explicit_size=size,
                payload=payload,
                source="submit_live_order",
            )
        except Exception:
            pass
    recent_order = bot.recent_submitted_order_for_ticker(payload.get("ticker"))
    if recent_order:
        reprice_result = maybe_auto_reprice_recent_order(
            bot,
            recent_order,
            payload,
            q=q,
            default_live_order_side=default_live_order_side,
            max_live_trades_per_day=max_live_trades_per_day,
            live_order_rate_limit=live_order_rate_limit,
        )
        if reprice_result.get("submitted") or reprice_result.get("handled"):
            return reprice_result
        return {
            "submitted": False,
            "reason": "recent_order_pending_confirmation",
            "payload": payload,
            "recent_order": recent_order,
        }
    family = str(payload.get("ticker") or "").split("-")[0].upper() if payload.get("ticker") else "UNKNOWN"
    pending_family_orders = bot.recent_submitted_order_count_for_family(family)
    max_pending_family_orders = int(
        max_sports_live_positions if bot._is_sports_market_or_family(family) else max_simultaneous_live_positions
    )
    if max_pending_family_orders > 0 and pending_family_orders >= max_pending_family_orders:
        return {
            "submitted": False,
            "reason": "pending_family_order_limit",
            "payload": payload,
            "pending_family_orders": pending_family_orders,
            "max_pending_family_orders": max_pending_family_orders,
        }
    pending_total_orders = bot.recent_submitted_order_count_total()
    max_pending_total_orders = int(max_simultaneous_live_positions or 0)
    if max_pending_total_orders > 0 and pending_total_orders >= max_pending_total_orders:
        return {
            "submitted": False,
            "reason": "pending_global_order_limit",
            "payload": payload,
            "pending_total_orders": pending_total_orders,
            "max_pending_total_orders": max_pending_total_orders,
        }
    authorization = bot.get_live_submit_authorization(payload)
    if not authorization.get("ok"):
        return {"submitted": False, "reason": authorization.get("reason"), "payload": payload, "authorization": authorization}
    if not bot.can_build_live_auth():
        return {"submitted": False, "reason": "auth_not_ready", "payload": payload}
    bot._reset_live_trade_counter_if_needed()
    if int(bot.live_trade_count_today or 0) >= int(max_live_trades_per_day or 0):
        return {"submitted": False, "reason": "daily_trade_cap", "payload": payload}
    active_timestamps = bot._active_live_order_timestamps()
    if len(active_timestamps) >= int(live_order_rate_limit or 1):
        return {"submitted": False, "reason": "rate_limited", "payload": payload}
    side = str(payload.get("selected_side") or payload.get("side") or default_live_order_side).strip().lower()
    if bot.already_holding(payload.get("ticker")):
        return {"submitted": False, "reason": "duplicate_position_guard", "payload": payload}
    legacy_payload = bot._normalize_legacy_order_payload(payload)
    if not legacy_payload:
        return {"submitted": False, "reason": "invalid_payload", "payload": payload}
    selected_price = (
        legacy_payload.get("yes_price_dollars")
        if legacy_payload.get("side") == "yes"
        else legacy_payload.get("no_price_dollars")
    )
    payload["price"] = bot.normalize_contract_price(
        selected_price,
        ticker=payload.get("ticker"),
        source="submit_live_order.result_price",
    )
    payload["side"] = side if side in {"yes", "no"} else legacy_payload.get("side")
    for field in (
        "structural_edge_context",
        "structural_edge_tags",
        "structural_pattern_keys",
        "STRUCTURAL_EDGE_SCORE",
        "CROSS_MARKET_EDGE_SCORE",
        "btc_1h_ladder_context",
        "btc_1h_ladder_score",
        "BTC_1H_LADDER_SCORE",
        "ELITE_EDGE_CONFIDENCE",
        "elite_edge_context",
        "elite_edge_size_multiplier",
        "elite_edge_kelly_max_multiplier",
        "cross_market_edge_context",
        "sports_edge_context",
        "sports_edge_tags",
    ):
        if payload.get(field) is None and q is not None:
            try:
                value = getattr(q, field, None)
            except Exception:
                value = None
            if value is not None:
                payload[field] = value
    structural_context = payload.get("structural_edge_context") if isinstance(payload.get("structural_edge_context"), dict) else {}
    btc_ladder_context = payload.get("btc_1h_ladder_context") if isinstance(payload.get("btc_1h_ladder_context"), dict) else {}
    if not btc_ladder_context and isinstance(structural_context.get("btc_1h_ladder_context"), dict):
        btc_ladder_context = structural_context.get("btc_1h_ladder_context") or {}
        payload["btc_1h_ladder_context"] = btc_ladder_context
    if btc_ladder_context:
        entry_context = payload.get("entry_context") if isinstance(payload.get("entry_context"), dict) else {}
        entry_context.setdefault("btc_1h_ladder_context", btc_ladder_context)
        entry_context.setdefault("btc_1h_ladder_score", btc_ladder_context.get("score") or btc_ladder_context.get("BTC_1H_LADDER_SCORE"))
        if structural_context:
            entry_context.setdefault("structural_edge_context", structural_context)
        payload["entry_context"] = entry_context
    btc_ladder_group = str(btc_ladder_context.get("settlement_group") or "").strip()
    if btc_ladder_group and str(family or "").upper().startswith("KXBTC"):
        try:
            for position in bot._get_open_live_positions() or []:
                if not isinstance(position, dict):
                    continue
                position_ticker = str(position.get("ticker") or "").strip()
                if not position_ticker or position_ticker == str(payload.get("ticker") or "").strip():
                    continue
                position_context = position.get("entry_context") if isinstance(position.get("entry_context"), dict) else {}
                position_structural = position_context.get("structural_edge_context") if isinstance(position_context.get("structural_edge_context"), dict) else {}
                position_btc_ladder = (
                    position_context.get("btc_1h_ladder_context")
                    if isinstance(position_context.get("btc_1h_ladder_context"), dict)
                    else position_structural.get("btc_1h_ladder_context")
                    if isinstance(position_structural.get("btc_1h_ladder_context"), dict)
                    else {}
                )
                position_group = str(position_btc_ladder.get("settlement_group") or "").strip()
                if position_group and position_group == btc_ladder_group:
                    print(
                        "[BTC-1H-LADDER-SKIP] "
                        + json.dumps(
                            {
                                "ticker": payload.get("ticker"),
                                "strike": btc_ladder_context.get("strike"),
                                "settlement_group": btc_ladder_group,
                                "price": payload.get("price") or payload.get("selected_price"),
                                "bid": btc_ladder_context.get("bid"),
                                "ask": btc_ladder_context.get("ask"),
                                "spread": btc_ladder_context.get("spread"),
                                "liquidity": btc_ladder_context.get("liquidity"),
                                "top_size": btc_ladder_context.get("top_size"),
                                "ladder_boost": btc_ladder_context.get("boost"),
                                "final_score": btc_ladder_context.get("score"),
                                "reason": "same_settlement_window_position_open",
                                "open_ticker": position_ticker,
                            },
                            sort_keys=True,
                            default=str,
                        )
                    )
                    return {
                        "submitted": False,
                        "reason": "btc_1h_ladder_same_window_guard",
                        "payload": payload,
                        "open_ticker": position_ticker,
                    }
        except Exception:
            pass
    if btc_ladder_context.get("enabled") and bot.to_float(btc_ladder_context.get("boost"), 0.0) > 0.0:
        print(
            "[BTC-1H-LADDER-EXECUTE] "
            + json.dumps(
                {
                    "ticker": payload.get("ticker"),
                    "strike": btc_ladder_context.get("strike"),
                    "settlement_group": btc_ladder_context.get("settlement_group"),
                    "price": payload.get("price") or payload.get("selected_price"),
                    "bid": btc_ladder_context.get("bid"),
                    "ask": btc_ladder_context.get("ask"),
                    "spread": btc_ladder_context.get("spread"),
                    "liquidity": btc_ladder_context.get("liquidity"),
                    "top_size": btc_ladder_context.get("top_size"),
                    "ladder_boost": btc_ladder_context.get("boost"),
                    "final_score": btc_ladder_context.get("score"),
                    "reason": btc_ladder_context.get("reason"),
                },
                sort_keys=True,
                default=str,
            )
        )
    result = bot.place_live_order({**payload, **legacy_payload})
    if result.get("submitted"):
        bot._live_order_timestamps.append(time.time())
        bot.live_trade_count_today = int(bot.live_trade_count_today or 0) + 1
        order_summary = bot.extract_order_execution_summary(result.get("response") or {})
        order_id = order_summary.get("order_id")
        bot.persist_live_entry_metadata(order=result.get("response") or {}, payload=payload, order_id=order_id)
        bot.append_live_audit_log(
            "order_submitted",
            ticker=legacy_payload.get("ticker"),
            order_id=order_id,
            side=legacy_payload.get("side"),
            action=legacy_payload.get("action"),
            count=legacy_payload.get("count"),
            price=payload.get("price"),
            status_code=result.get("status_code"),
        )
        archive_writer = getattr(bot, "append_aion_learning_archive_event", None)
        if callable(archive_writer):
            archive_writer(
                "trade_opened",
                q=q,
                payload=payload,
                order=result.get("response") or {},
                result=result,
                extra={
                    "ticker": legacy_payload.get("ticker"),
                    "order_id": order_id,
                    "entry_reason": result.get("reason") or lane or "live_order_submitted",
                    "entry_mode": lane or "live_order",
                    "phase_or_realm_source": "kalshi_execution.submit_live_order",
                    "sports_tags": getattr(q, "sports_edge_tags", None) or getattr(q, "sports_tags", None),
                    "sports_edge_boost": getattr(q, "sports_edge_rank_boost", None),
                    "sports_score_at_entry": getattr(q, "sports_edge_score_at_entry", None),
                    "structural_edge_context": getattr(q, "structural_edge_context", None),
                    "structural_tags": getattr(q, "structural_edge_tags", None),
                    "structural_pattern_keys": getattr(q, "structural_pattern_keys", None),
                    "STRUCTURAL_EDGE_SCORE": getattr(q, "STRUCTURAL_EDGE_SCORE", None),
                    "CROSS_MARKET_EDGE_SCORE": getattr(q, "CROSS_MARKET_EDGE_SCORE", None),
                    "BTC_1H_LADDER_SCORE": getattr(q, "BTC_1H_LADDER_SCORE", None),
                    "btc_1h_ladder_context": getattr(q, "btc_1h_ladder_context", None),
                    "ELITE_EDGE_CONFIDENCE": getattr(q, "ELITE_EDGE_CONFIDENCE", None),
                    "elite_edge_context": getattr(q, "elite_edge_context", None),
                    "cross_market_edge_context": getattr(q, "cross_market_edge_context", None),
                },
            )
        print(
            "[ORDER-SUBMITTED] "
            + json.dumps(
                {
                    "ticker": legacy_payload.get("ticker"),
                    "side": legacy_payload.get("side"),
                    "price": payload.get("price"),
                    "count": legacy_payload.get("count"),
                    "endpoint": result.get("endpoint"),
                    "lane": lane,
                    "order_id": order_id,
                },
                sort_keys=True,
                default=str,
            )
        )
        result["payload"] = payload
        return result
    bot.append_live_audit_log(
        "order_submit_failed",
        ticker=legacy_payload.get("ticker"),
        side=legacy_payload.get("side"),
        action=legacy_payload.get("action"),
        count=legacy_payload.get("count"),
        price=payload.get("price"),
        reason=result.get("reason"),
        status_code=result.get("status_code"),
    )
    result["payload"] = payload
    return result


def emit_submit_dispatch_trace(**payload):
    try:
        print("[AION-SUBMIT-DISPATCH] " + json.dumps(payload, sort_keys=True, default=str))
    except Exception:
        pass

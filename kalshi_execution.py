"""Live order execution helpers for kalshi_web."""

import hashlib
import json
import os
import time


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
    payload = dict(payload_override) if isinstance(payload_override, dict) else bot.build_live_order_payload(q, size=size)
    if not payload:
        return {"submitted": False, "reason": "invalid_payload", "payload": payload}
    recent_order = bot.recent_submitted_order_for_ticker(payload.get("ticker"))
    if recent_order:
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

"""Live order execution helpers for kalshi_web."""

import hashlib
import json
import math
import os
import time


CANONICAL_ORDER_ENDPOINT = "/trade-api/v2/portfolio/orders"
CANONICAL_ORDER_ENDPOINT_KIND = "legacy"


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


def _price_to_cents(value):
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    cents = int(round(numeric * 100.0)) if numeric <= 1.0 else int(round(numeric))
    return max(1, min(99, cents))


def _compact_response_body(body, limit=1200):
    if body is None:
        return None
    try:
        text = json.dumps(body, sort_keys=True, default=str)
    except Exception:
        text = str(body)
    if len(text) <= int(limit or 1200):
        return body
    return text[: int(limit or 1200)] + "...[truncated]"


def _exchange_rejection_reason(response):
    response = response if isinstance(response, dict) else {}
    body = response.get("body") if isinstance(response.get("body"), dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = str(error.get("code") or "").strip()
    if code:
        return code
    return response.get("reason") or "request_failed"


def _cash_value_to_dollars(value, *, cents=False):
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric < 0:
        return None
    if cents:
        numeric = numeric / 100.0
    return round(float(numeric), 4)


def _live_order_time_in_force(*, reduce_only=False):
    if bool(reduce_only):
        return "immediate_or_cancel"
    requested = str(os.environ.get("AION_LIVE_BUY_TIME_IN_FORCE", "immediate_or_cancel") or "immediate_or_cancel").strip().lower()
    if requested not in {"immediate_or_cancel", "fill_or_kill"}:
        requested = "immediate_or_cancel"
    return requested


def _find_mapping_field(mapping, field_names):
    mapping = mapping if isinstance(mapping, dict) else {}
    for field_name in field_names:
        if field_name in mapping and mapping.get(field_name) not in (None, ""):
            return field_name, mapping.get(field_name)
    for value in mapping.values():
        if isinstance(value, dict):
            found_name, found_value = _find_mapping_field(value, field_names)
            if found_name:
                return found_name, found_value
    return None, None


def fetch_live_available_cash(bot):
    dollar_fields = (
        "available_balance_dollars",
        "available_cash_dollars",
        "buying_power_dollars",
        "cash_balance_dollars",
        "balance_dollars",
    )
    cent_fields = (
        "available_balance_cents",
        "available_cash_cents",
        "buying_power_cents",
        "cash_balance_cents",
        "balance_cents",
        "available_balance",
        "buying_power",
        "cash_balance",
        "balance",
    )
    try:
        result = bot._live_request_json("GET", "/trade-api/v2/portfolio/balance", timeout=5)
    except Exception as exc:
        result = {"ok": False, "reason": f"balance_fetch_exception:{exc.__class__.__name__}", "body": None}
    body = result.get("body") if isinstance(result, dict) else {}
    if isinstance(body, dict):
        field_name, raw_value = _find_mapping_field(body, dollar_fields)
        available_cash = _cash_value_to_dollars(raw_value, cents=False)
        source = f"portfolio_balance.{field_name}" if field_name else None
        if available_cash is None:
            field_name, raw_value = _find_mapping_field(body, cent_fields)
            available_cash = _cash_value_to_dollars(raw_value, cents=True)
            source = f"portfolio_balance.{field_name}" if field_name else None
        if available_cash is not None:
            try:
                bot.live_balance = body
                bot.last_live_available_balance = available_cash
                bot.available_cash = available_cash
            except Exception:
                pass
            return {
                "ok": True,
                "available_cash": available_cash,
                "source": source or "portfolio_balance",
                "status_code": result.get("status_code"),
                "body_keys": sorted(body.keys()),
            }
    for attr_name in (
        "available_cash",
        "available_balance",
        "live_available_balance",
        "last_live_available_balance",
        "last_available_balance",
    ):
        try:
            raw_value = getattr(bot, attr_name, None)
        except Exception:
            raw_value = None
        available_cash = _cash_value_to_dollars(raw_value, cents=False)
        if available_cash is not None:
            return {
                "ok": True,
                "available_cash": available_cash,
                "source": f"attribute.{attr_name}",
                "status_code": result.get("status_code") if isinstance(result, dict) else None,
                "body_keys": sorted(body.keys()) if isinstance(body, dict) else [],
                "stale_fallback": True,
            }
    return {
        "ok": False,
        "available_cash": None,
        "source": None,
        "reason": result.get("reason") if isinstance(result, dict) else "balance_unavailable",
        "status_code": result.get("status_code") if isinstance(result, dict) else None,
        "body_keys": sorted(body.keys()) if isinstance(body, dict) else [],
    }


def apply_cash_aware_live_sizing(bot, request_payload, validation):
    request_payload = dict(request_payload or {})
    validation = validation if isinstance(validation, dict) else {}
    action = str(request_payload.get("action") or "").strip().lower()
    if action != "buy":
        return {"ok": True, "payload": request_payload, "cash_sizing": {"checked": False, "reason": "non_buy_order"}}
    selected_price_cents = validation.get("price_cents") or request_payload.get("yes_price") or request_payload.get("no_price")
    selected_price_cents = _price_to_cents(selected_price_cents)
    planned_count = request_payload.get("count")
    try:
        planned_count = int(planned_count)
    except (TypeError, ValueError):
        planned_count = 0
    if selected_price_cents is None or planned_count < 1:
        return {
            "ok": False,
            "reason": "cash_sizing_invalid_price_or_count",
            "payload": request_payload,
            "cash_sizing": {
                "selected_price_cents": selected_price_cents,
                "planned_count": planned_count,
            },
        }
    balance = fetch_live_available_cash(bot)
    available_cash = balance.get("available_cash")
    buffer_cents = max(0, _env_int("AION_CASH_SIZING_BUFFER_CENTS", 0))
    if available_cash is None:
        print(
            "[AION-CASH-SIZING-SKIP] "
            + json.dumps(
                {
                    "ticker": request_payload.get("ticker"),
                    "selected_side": request_payload.get("side"),
                    "selected_price_cents": selected_price_cents,
                    "planned_count": planned_count,
                    "max_affordable_count": 0,
                    "available_cash": None,
                    "estimated_cost": round((selected_price_cents * planned_count) / 100.0, 4),
                    "reason": "cash_balance_unavailable_pre_submit",
                    "balance_reason": balance.get("reason"),
                    "balance_status_code": balance.get("status_code"),
                },
                sort_keys=True,
                default=str,
            )
        )
        return {
            "ok": False,
            "reason": "cash_balance_unavailable_pre_submit",
            "payload": request_payload,
            "cash_sizing": balance,
        }
    available_cash_cents = max(0, int(math.floor(float(available_cash) * 100.0 + 1e-9)))
    spendable_cash_cents = max(0, available_cash_cents - buffer_cents)
    max_affordable_count = int(spendable_cash_cents // max(1, int(selected_price_cents)))
    estimated_cost = round((selected_price_cents * planned_count) / 100.0, 4)
    if max_affordable_count < 1:
        print(
            "[AION-CASH-SIZING-SKIP] "
            + json.dumps(
                {
                    "ticker": request_payload.get("ticker"),
                    "selected_side": request_payload.get("side"),
                    "selected_price_cents": selected_price_cents,
                    "planned_count": planned_count,
                    "max_affordable_count": max_affordable_count,
                    "available_cash": available_cash,
                    "estimated_cost": estimated_cost,
                    "cash_buffer_cents": buffer_cents,
                    "balance_source": balance.get("source"),
                    "reason": "insufficient_cash_pre_submit",
                },
                sort_keys=True,
                default=str,
            )
        )
        return {
            "ok": False,
            "reason": "insufficient_cash_pre_submit",
            "payload": request_payload,
            "cash_sizing": {
                "available_cash": available_cash,
                "selected_price_cents": selected_price_cents,
                "planned_count": planned_count,
                "max_affordable_count": max_affordable_count,
                "estimated_cost": estimated_cost,
                "cash_buffer_cents": buffer_cents,
                "balance_source": balance.get("source"),
            },
        }
    if max_affordable_count < planned_count:
        adjusted_payload = dict(request_payload)
        adjusted_payload["count"] = max_affordable_count
        print(
            "[AION-CASH-SIZING-ADJUSTED] "
            + json.dumps(
                {
                    "ticker": request_payload.get("ticker"),
                    "selected_price_cents": selected_price_cents,
                    "original_count": planned_count,
                    "adjusted_count": max_affordable_count,
                    "available_cash": available_cash,
                    "estimated_cost": estimated_cost,
                    "adjusted_estimated_cost": round((selected_price_cents * max_affordable_count) / 100.0, 4),
                    "cash_buffer_cents": buffer_cents,
                    "balance_source": balance.get("source"),
                },
                sort_keys=True,
                default=str,
            )
        )
        return {
            "ok": True,
            "payload": adjusted_payload,
            "cash_sizing": {
                "adjusted": True,
                "available_cash": available_cash,
                "selected_price_cents": selected_price_cents,
                "planned_count": planned_count,
                "max_affordable_count": max_affordable_count,
                "estimated_cost": estimated_cost,
                "adjusted_estimated_cost": round((selected_price_cents * max_affordable_count) / 100.0, 4),
                "cash_buffer_cents": buffer_cents,
                "balance_source": balance.get("source"),
            },
        }
    return {
        "ok": True,
        "payload": request_payload,
        "cash_sizing": {
            "checked": True,
            "available_cash": available_cash,
            "selected_price_cents": selected_price_cents,
            "planned_count": planned_count,
            "max_affordable_count": max_affordable_count,
            "estimated_cost": estimated_cost,
            "cash_buffer_cents": buffer_cents,
            "balance_source": balance.get("source"),
        },
    }


def aion_intent_to_order_payload_fields(intent, selected_price, endpoint_kind=CANONICAL_ORDER_ENDPOINT_KIND):
    intent = intent if isinstance(intent, dict) else {"side": intent}
    endpoint_kind = str(endpoint_kind or CANONICAL_ORDER_ENDPOINT_KIND).strip().lower()
    side = str(intent.get("selected_side") or intent.get("side") or "").strip().lower()
    action = str(intent.get("action") or "buy").strip().lower()
    if action in {"exit", "close"}:
        action = "sell"
    if endpoint_kind != CANONICAL_ORDER_ENDPOINT_KIND:
        return None
    if side not in {"yes", "no"} or action not in {"buy", "sell"}:
        return None
    price_cents = _price_to_cents(selected_price)
    if price_cents is None:
        return None
    fields = {
        "side": side,
        "action": action,
    }
    fields["yes_price" if side == "yes" else "no_price"] = price_cents
    return fields


def validate_canonical_order_payload(payload, endpoint_kind=CANONICAL_ORDER_ENDPOINT_KIND):
    payload = payload if isinstance(payload, dict) else {}
    endpoint_kind = str(endpoint_kind or CANONICAL_ORDER_ENDPOINT_KIND).strip().lower()
    if endpoint_kind != CANONICAL_ORDER_ENDPOINT_KIND:
        return {"ok": False, "reason": "unsupported_endpoint_kind", "payload_keys": sorted(payload.keys())}
    ticker = str(payload.get("ticker") or "").strip()
    client_order_id = str(payload.get("client_order_id") or "").strip()
    side = str(payload.get("side") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
    count = payload.get("count")
    price_keys = [
        key
        for key in ("yes_price", "no_price", "yes_price_dollars", "no_price_dollars", "price", "buy_max_cost")
        if payload.get(key) not in (None, "")
    ]
    if not ticker:
        return {"ok": False, "reason": "missing_ticker", "payload_keys": sorted(payload.keys())}
    if not client_order_id:
        return {"ok": False, "reason": "missing_client_order_id", "payload_keys": sorted(payload.keys())}
    if side not in {"yes", "no"}:
        return {"ok": False, "reason": "invalid_side", "payload_keys": sorted(payload.keys())}
    if action not in {"buy", "sell"}:
        return {"ok": False, "reason": "invalid_action", "payload_keys": sorted(payload.keys())}
    if len(price_keys) != 1:
        return {
            "ok": False,
            "reason": "exactly_one_legacy_price_field_required",
            "price_keys": price_keys,
            "payload_keys": sorted(payload.keys()),
        }
    expected_price_key = "yes_price" if side == "yes" else "no_price"
    if price_keys[0] != expected_price_key:
        return {
            "ok": False,
            "reason": "price_field_does_not_match_side",
            "price_keys": price_keys,
            "expected_price_key": expected_price_key,
            "payload_keys": sorted(payload.keys()),
        }
    price_value = payload.get(expected_price_key)
    if isinstance(price_value, bool) or not isinstance(price_value, int) or not (1 <= int(price_value) <= 99):
        return {
            "ok": False,
            "reason": "invalid_integer_cents_price",
            "price_key": expected_price_key,
            "price_value": price_value,
            "payload_keys": sorted(payload.keys()),
        }
    if isinstance(count, bool) or not isinstance(count, int) or int(count) < 1:
        return {"ok": False, "reason": "invalid_integer_count", "count": count, "payload_keys": sorted(payload.keys())}
    if bool(payload.get("reduce_only")) and payload.get("time_in_force") != "immediate_or_cancel":
        return {
            "ok": False,
            "reason": "reduce_only_requires_ioc",
            "time_in_force": payload.get("time_in_force"),
            "payload_keys": sorted(payload.keys()),
        }
    return {
        "ok": True,
        "reason": "schema_valid",
        "payload_keys": sorted(payload.keys()),
        "price_key": expected_price_key,
        "price_cents": int(price_value),
        "count": int(count),
    }


def _emit_payload_schema_block(endpoint, endpoint_kind, payload, validation):
    try:
        print(
            "[AION-PAYLOAD-SCHEMA-BLOCK] "
            + json.dumps(
                {
                    "endpoint": endpoint,
                    "endpoint_kind": endpoint_kind,
                    "ticker": (payload or {}).get("ticker") if isinstance(payload, dict) else None,
                    "client_order_id": (payload or {}).get("client_order_id") if isinstance(payload, dict) else None,
                    "side": (payload or {}).get("side") if isinstance(payload, dict) else None,
                    "action": (payload or {}).get("action") if isinstance(payload, dict) else None,
                    "count": (payload or {}).get("count") if isinstance(payload, dict) else None,
                    "reason": (validation or {}).get("reason"),
                    "price_keys": (validation or {}).get("price_keys"),
                    "payload_keys": (validation or {}).get("payload_keys"),
                },
                sort_keys=True,
                default=str,
            )
        )
    except Exception:
        pass


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
    side = str(payload.get("selected_side") or payload.get("side") or "").strip().lower()
    if side not in {"yes", "no"}:
        return None
    action = str(payload.get("action") or default_live_order_action).strip().lower()
    count = int(max(1, min(int(bot.to_float(payload.get("count"), 1) or 1), int(max_live_order_size or 1))))
    price_cents = payload.get("yes_price") if side == "yes" else payload.get("no_price")
    price_dollars = payload.get("selected_price_dollars") or payload.get("selected_price") or payload.get("price")
    price_cents = _price_to_cents(price_cents)
    if price_cents is None:
        normalized_price = bot.normalize_contract_price(price_dollars, ticker=ticker, source="legacy_order_payload")
        if normalized_price is None:
            return None
        price_cents = _price_to_cents(normalized_price)
    if price_cents is None:
        return None
    order_fields = aion_intent_to_order_payload_fields(
        {"side": side, "action": action},
        price_cents,
        CANONICAL_ORDER_ENDPOINT_KIND,
    )
    if not order_fields:
        return None
    reduce_only = bool(payload.get("reduce_only", False))
    order = {
        "ticker": ticker,
        "client_order_id": payload["client_order_id"],
        "count": count,
        "type": "limit",
        "time_in_force": _live_order_time_in_force(reduce_only=reduce_only),
        "post_only": False if reduce_only else bool(payload.get("post_only", False)),
        "reduce_only": reduce_only,
        "self_trade_prevention_type": "taker_at_cross",
    }
    order.update(order_fields)
    return order


def normalize_event_order_payload(
    bot,
    legacy_payload,
    *,
    default_live_order_side,
    default_live_order_action,
):
    legacy_payload = dict(legacy_payload or {})
    side = str(legacy_payload.get("side") or legacy_payload.get("selected_side") or "").strip().lower()
    if side not in {"yes", "no"}:
        return None
    action = str(legacy_payload.get("action") or default_live_order_action).strip().lower()
    raw_price = legacy_payload.get("yes_price") if side == "yes" else legacy_payload.get("no_price")
    selected_price = bot.normalize_contract_price(
        round(float(raw_price) / 100.0, 4) if raw_price not in (None, "") else None,
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
    reduce_only = bool(legacy_payload.get("reduce_only", False))
    return {
        "ticker": legacy_payload.get("ticker"),
        "client_order_id": legacy_payload.get("client_order_id"),
        "side": event_side,
        "count": f"{bot.to_float(legacy_payload.get('count'), 1.0) or 1.0:.2f}",
        "price": f"{yes_side_price:.4f}",
        "time_in_force": _live_order_time_in_force(reduce_only=reduce_only),
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False if reduce_only else bool(legacy_payload.get("post_only", False)),
        "cancel_order_on_pause": True,
        "reduce_only": reduce_only,
        "subaccount": 0,
        "exchange_index": 0,
    }


def place_live_order(bot, payload):
    payload = payload if isinstance(payload, dict) else {}
    ticker_hint = str(payload.get("ticker") or "").strip()
    family_hint = str(payload.get("family") or "").strip().upper()
    action_hint = str(payload.get("action") or "").strip().lower()
    exit_hint = bool(
        action_hint in {"sell", "exit", "close"}
        or payload.get("reduce_only")
        or payload.get("exit_reason")
        or payload.get("exit_order")
    )
    if not family_hint and ticker_hint:
        family_resolver = getattr(bot, "get_market_family", None)
        if callable(family_resolver):
            try:
                family_hint = str(family_resolver(payload) or "").strip().upper()
            except Exception:
                family_hint = ""
    sports_only_blocker = getattr(bot, "_sports_only_execution_blocked", None)
    if callable(sports_only_blocker) and (ticker_hint or family_hint) and not exit_hint:
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
    loss_bucket_guard = getattr(bot, "evaluate_loss_bucket_entry_block", None)
    if callable(loss_bucket_guard):
        try:
            guard_result = loss_bucket_guard(
                payload,
                q=None,
                source="kalshi_execution.place_live_order",
            )
        except Exception:
            guard_result = {"blocked": False}
        if isinstance(guard_result, dict) and guard_result.get("blocked"):
            return {
                "submitted": False,
                "reason": guard_result.get("reason") or "loss_bucket_entry_block",
                "payload": payload,
                "loss_bucket_guard": guard_result,
            }
    if not exit_hint and action_hint in {"", "buy"} and not bool(payload.get("_risk_governor_checked")):
        risk_governor = getattr(bot, "evaluate_live_order_risk_governor", None)
        if callable(risk_governor):
            risk_result = risk_governor(payload, q=None)
            try:
                if (not risk_result.get("ok")) or risk_result.get("cap_reasons") or risk_result.get("warnings"):
                    print(
                        "[AION-RISK-GOVERNOR] "
                        + json.dumps(
                            {
                                "ticker": risk_result.get("ticker") or payload.get("ticker"),
                                "family": risk_result.get("family") or family_hint,
                                "side": risk_result.get("side") or payload.get("side"),
                                "selected_price": risk_result.get("selected_price") or payload.get("price"),
                                "ok": bool(risk_result.get("ok")),
                                "reason": risk_result.get("reason"),
                                "final_count": risk_result.get("final_count"),
                                "cap_reasons": risk_result.get("cap_reasons"),
                                "warnings": risk_result.get("warnings"),
                                "responsible_subsystem": "canonical_risk_governor",
                            },
                            sort_keys=True,
                            default=str,
                        )
                    )
            except Exception:
                pass
            if not risk_result.get("ok"):
                return {
                    "submitted": False,
                    "reason": risk_result.get("reason") or "risk_governor_block",
                    "payload": risk_result.get("payload") or payload,
                    "risk": risk_result,
                }
            payload = risk_result.get("payload") if isinstance(risk_result.get("payload"), dict) else payload
            payload["_risk_governor_checked"] = True
    legacy_payload = bot._normalize_legacy_order_payload(payload)
    if not legacy_payload:
        return {"submitted": False, "reason": "invalid_payload", "payload": payload}
    path = CANONICAL_ORDER_ENDPOINT
    endpoint_kind = CANONICAL_ORDER_ENDPOINT_KIND
    request_payload = legacy_payload
    validation = validate_canonical_order_payload(request_payload, endpoint_kind)
    if not validation.get("ok"):
        _emit_payload_schema_block(path, endpoint_kind, request_payload, validation)
        return {
            "submitted": False,
            "reason": validation.get("reason") or "malformed_payload",
            "payload": request_payload,
            "endpoint": path,
            "endpoint_kind": endpoint_kind,
            "validation": validation,
        }
    cash_sizing = apply_cash_aware_live_sizing(bot, request_payload, validation)
    if not cash_sizing.get("ok"):
        return {
            "submitted": False,
            "reason": cash_sizing.get("reason") or "cash_sizing_block",
            "payload": cash_sizing.get("payload") or request_payload,
            "endpoint": path,
            "endpoint_kind": endpoint_kind,
            "cash_sizing": cash_sizing.get("cash_sizing"),
        }
    request_payload = cash_sizing.get("payload") if isinstance(cash_sizing.get("payload"), dict) else request_payload
    legacy_payload = request_payload
    validation = validate_canonical_order_payload(request_payload, endpoint_kind)
    if not validation.get("ok"):
        _emit_payload_schema_block(path, endpoint_kind, request_payload, validation)
        return {
            "submitted": False,
            "reason": validation.get("reason") or "malformed_payload_after_cash_sizing",
            "payload": request_payload,
            "endpoint": path,
            "endpoint_kind": endpoint_kind,
            "validation": validation,
            "cash_sizing": cash_sizing.get("cash_sizing"),
        }
    selected_price_key = validation.get("price_key")
    selected_price = request_payload.get(selected_price_key)
    sports_live_guard_result = None
    opposite_side_guard_result = None
    sports_live_guard = getattr(bot, "evaluate_sports_live_state_pre_submit_guard", None)
    if callable(sports_live_guard):
        guard_payload = dict(payload)
        guard_payload.update(request_payload)
        guard_result = sports_live_guard(
            guard_payload,
            q=None,
            source="canonical_place_live_order",
            refresh=True,
        )
        sports_live_guard_result = guard_result if isinstance(guard_result, dict) else None
        if not guard_result.get("ok"):
            return {
                "submitted": False,
                "reason": guard_result.get("reason") or "pregame_sports_market",
                "payload": legacy_payload,
                "request_payload": request_payload,
                "endpoint": path,
                "endpoint_kind": endpoint_kind,
                "sports_live_state_guard": guard_result,
            }
    opposite_guard = getattr(bot, "evaluate_opposite_side_pre_submit_guard", None)
    if callable(opposite_guard):
        guard_payload = dict(payload)
        guard_payload.update(request_payload)
        guard_result = opposite_guard(
            guard_payload,
            q=None,
            source="canonical_place_live_order",
            refresh=True,
        )
        opposite_side_guard_result = guard_result if isinstance(guard_result, dict) else None
        if not guard_result.get("ok"):
            conflicts = guard_result.get("conflicts") if isinstance(guard_result.get("conflicts"), list) else []
            ticker = str(request_payload.get("ticker") or legacy_payload.get("ticker") or "").strip()
            exact_ticker_conflict = any(
                isinstance(conflict, dict)
                and (
                    str(conflict.get("reason") or "").strip().lower() == "exact_ticker_opposite_side"
                    or str(conflict.get("ticker") or "").strip() == ticker
                )
                for conflict in conflicts
            )
            if not exact_ticker_conflict:
                print(
                    "[AION-OPPOSITE-SIDE-WARNING] "
                    + json.dumps(
                        {
                            "ticker": ticker or None,
                            "selected_side": request_payload.get("side") or legacy_payload.get("side"),
                            "old_reason": guard_result.get("reason") or "opposite_side_conflict",
                            "reason": "opposite_side_conflict_softened",
                            "conflict_count": guard_result.get("conflict_count"),
                            "conflicts": guard_result.get("conflicts"),
                            "source": "kalshi_execution.place_live_order",
                            "decision": "CONTINUE",
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
            else:
                print(
                    "[AION-OPPOSITE-SIDE-BLOCK] "
                    + json.dumps(guard_result, sort_keys=True, default=str)
                )
                return {
                    "submitted": False,
                    "reason": guard_result.get("reason") or "opposite_side_conflict",
                    "payload": legacy_payload,
                    "request_payload": request_payload,
                    "endpoint": path,
                    "endpoint_kind": endpoint_kind,
                    "opposite_side_guard": guard_result,
                }
    print(
        "[AION-EXECUTION-RECOVERY-SUBMIT] "
        + json.dumps(
            {
                "method": "POST",
                "endpoint": path,
                "endpoint_kind": endpoint_kind,
                "ticker": legacy_payload.get("ticker"),
                "selected_side": legacy_payload.get("side"),
                "action": legacy_payload.get("action"),
                "selected_price_cents": selected_price,
                "count": legacy_payload.get("count"),
                "sports_live_guard_ok": None if sports_live_guard_result is None else bool(sports_live_guard_result.get("ok")),
                "sports_live_guard_reason": None if sports_live_guard_result is None else sports_live_guard_result.get("reason"),
                "sports_live_guard_block_reason": None if sports_live_guard_result is None else sports_live_guard_result.get("block_reason"),
                "opposite_side_guard_ok": None if opposite_side_guard_result is None else bool(opposite_side_guard_result.get("ok")),
                "opposite_side_guard_reason": None if opposite_side_guard_result is None else opposite_side_guard_result.get("reason"),
                "orderbook_ready": payload.get("orderbook_ready"),
                "subsystem": "kalshi_execution.place_live_order",
            },
            sort_keys=True,
            default=str,
        )
    )
    print(
        "[ORDER-SUBMIT] "
        + json.dumps(
            {
                "method": "POST",
                "endpoint": path,
                "endpoint_kind": endpoint_kind,
                "ticker": legacy_payload.get("ticker"),
                "client_order_id": legacy_payload.get("client_order_id"),
                "selected_side": legacy_payload.get("side"),
                "action": legacy_payload.get("action"),
                "selected_price_cents": selected_price,
                "count": legacy_payload.get("count"),
                "time_in_force": request_payload.get("time_in_force"),
                "reduce_only": request_payload.get("reduce_only"),
                "post_only": request_payload.get("post_only"),
                "payload_keys": sorted(request_payload.keys()),
            },
            sort_keys=True,
            default=str,
        )
    )
    response = bot._live_request_json("POST", path, json_payload=request_payload, timeout=20)
    rejection_reason = _exchange_rejection_reason(response)
    attempt = {
        "method": "POST",
        "path": path,
        "endpoint": path,
        "endpoint_kind": endpoint_kind,
        "status_code": response.get("status_code"),
        "reason": rejection_reason,
        "http_reason": response.get("reason"),
        "body": response.get("body"),
        "request_payload_keys": sorted(request_payload.keys()),
        "ticker": request_payload.get("ticker"),
        "client_order_id": request_payload.get("client_order_id"),
        "selected_side": request_payload.get("side"),
        "selected_price_cents": selected_price,
        "count": request_payload.get("count"),
        "buy_max_cost": request_payload.get("buy_max_cost"),
    }
    attempts = [attempt]
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
            "cash_sizing": cash_sizing.get("cash_sizing"),
        }
    print(
        "[ORDER-REJECT] "
        + json.dumps(
            {
                "method": "POST",
                "endpoint": path,
                "endpoint_kind": endpoint_kind,
                "status_code": response.get("status_code"),
                "reason": rejection_reason,
                "http_reason": response.get("reason"),
                "response_body": _compact_response_body(response.get("body")),
                "ticker": request_payload.get("ticker"),
                "client_order_id": request_payload.get("client_order_id"),
                "selected_side": request_payload.get("side"),
                "selected_price_cents": selected_price,
                "count": request_payload.get("count"),
                "time_in_force": request_payload.get("time_in_force"),
                "reduce_only": request_payload.get("reduce_only"),
                "post_only": request_payload.get("post_only"),
                "buy_max_cost": request_payload.get("buy_max_cost"),
                "outbound_payload_keys": sorted(request_payload.keys()),
            },
            sort_keys=True,
            default=str,
        )
    )
    return {
        "submitted": False,
        "reason": rejection_reason,
        "http_reason": response.get("reason"),
        "payload": legacy_payload,
        "request_payload": request_payload,
        "endpoint": path,
        "endpoint_kind": endpoint_kind,
        "status_code": response.get("status_code"),
        "response_body": response.get("body"),
        "attempts": attempts,
        "cash_sizing": cash_sizing.get("cash_sizing"),
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
        current_payload["risk_warning_daily_trade_count"] = {
            "live_trade_count_today": int(bot.live_trade_count_today or 0),
            "max_live_trades_per_day": int(max_live_trades_per_day or 0),
        }
        _auto_reprice_log("AUTO-REPRICE-WARN", ticker=ticker, order_id=order_id or None, reason="daily_trade_count_warning")
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
    reprice_entry_context = reprice_payload.get("entry_context") if isinstance(reprice_payload.get("entry_context"), dict) else {}
    for field in (
        "sports_memory_context",
        "sports_memory_dimensions",
        "sports_memory_rank_adjustment",
        "sports_memory_confidence_adjustment",
        "sports_memory_size_multiplier",
        "sports_memory_aggression_multiplier",
        "sports_memory_regime",
        "sports_regime_context",
        "sports_regime",
        "sports_regime_type",
        "sports_momentum_classification",
        "sports_momentum_score",
        "sports_regime_rank_adjustment",
        "sports_regime_size_multiplier",
        "sports_regime_aggression_multiplier",
        "game_thesis_context",
        "game_id",
        "game_thesis_type",
        "game_thesis_confidence",
        "game_thesis_rank_boost",
        "game_thesis_size_multiplier",
        "game_thesis_aggression_multiplier",
        "game_related_candidates",
        "game_primary_candidate",
        "game_secondary_candidates",
        "sports_ladder_context",
        "sports_ladder_plan_id",
        "sports_ladder_role",
        "sports_ladder_leg_rank",
        "sports_ladder_leg_type",
        "sports_ladder_score",
        "sports_ladder_alignment",
        "sports_ladder_size_multiplier",
        "sports_ladder_size_cap",
        "sports_ladder_game_exposure_cap",
    ):
        value = getattr(q, field, None) if q is not None else None
        if value is not None:
            reprice_payload[field] = value
            reprice_entry_context.setdefault(field, value)
    if reprice_entry_context:
        reprice_payload["entry_context"] = reprice_entry_context
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
                    "sports_memory_context": getattr(q, "sports_memory_context", None),
                    "sports_memory_dimensions": getattr(q, "sports_memory_dimensions", None),
                    "sports_memory_regime": getattr(q, "sports_memory_regime", None),
                    "sports_memory_rank_adjustment": getattr(q, "sports_memory_rank_adjustment", None),
                    "sports_memory_size_multiplier": getattr(q, "sports_memory_size_multiplier", None),
                    "sports_regime_context": getattr(q, "sports_regime_context", None),
                    "sports_regime": getattr(q, "sports_regime", None),
                    "sports_regime_type": getattr(q, "sports_regime_type", None),
                    "sports_momentum_classification": getattr(q, "sports_momentum_classification", None),
                    "sports_momentum_score": getattr(q, "sports_momentum_score", None),
                    "sports_regime_rank_adjustment": getattr(q, "sports_regime_rank_adjustment", None),
                    "sports_regime_size_multiplier": getattr(q, "sports_regime_size_multiplier", None),
                    "sports_regime_aggression_multiplier": getattr(q, "sports_regime_aggression_multiplier", None),
                    "game_thesis_context": getattr(q, "game_thesis_context", None),
                    "game_id": getattr(q, "game_id", None),
                    "game_thesis_type": getattr(q, "game_thesis_type", None),
                    "game_thesis_confidence": getattr(q, "game_thesis_confidence", None),
                    "game_thesis_rank_boost": getattr(q, "game_thesis_rank_boost", None),
                    "game_thesis_size_multiplier": getattr(q, "game_thesis_size_multiplier", None),
                    "sports_ladder_context": getattr(q, "sports_ladder_context", None),
                    "sports_ladder_plan_id": getattr(q, "sports_ladder_plan_id", None),
                    "sports_ladder_role": getattr(q, "sports_ladder_role", None),
                    "sports_ladder_leg_type": getattr(q, "sports_ladder_leg_type", None),
                    "sports_ladder_score": getattr(q, "sports_ladder_score", None),
                    "sports_ladder_size_cap": getattr(q, "sports_ladder_size_cap", None),
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
    action_hint = str(payload_hint.get("action") or "").strip().lower()
    exit_hint = bool(
        action_hint in {"sell", "exit", "close"}
        or payload_hint.get("reduce_only")
        or payload_hint.get("exit_reason")
        or payload_hint.get("exit_order")
    )
    sports_only_blocker = getattr(bot, "_sports_only_execution_blocked", None)
    if callable(sports_only_blocker) and (ticker_hint or family_hint) and not exit_hint:
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
    loss_bucket_guard = getattr(bot, "evaluate_loss_bucket_entry_block", None)
    if callable(loss_bucket_guard):
        try:
            guard_result = loss_bucket_guard(
                payload,
                q=q,
                source="kalshi_execution.submit_live_order",
            )
        except Exception:
            guard_result = {"blocked": False}
        if isinstance(guard_result, dict) and guard_result.get("blocked"):
            return {
                "submitted": False,
                "reason": guard_result.get("reason") or "loss_bucket_entry_block",
                "payload": payload,
                "loss_bucket_guard": guard_result,
            }
    sizing_resolved = bool(payload.get("_aion_sizing_resolved"))
    try:
        sizing_resolved_count = int(bot.to_float(payload.get("count"), 0) or 0)
    except Exception:
        sizing_resolved_count = 0
    dynamic_size_resolver = getattr(bot, "_resolve_dynamic_live_entry_size", None)
    if callable(dynamic_size_resolver) and not (sizing_resolved and sizing_resolved_count > 0):
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
    pending_total_orders = bot.recent_submitted_order_count_total()
    max_pending_total_orders = int(max_simultaneous_live_positions or 0)
    if max_pending_family_orders > 0 and pending_family_orders >= max_pending_family_orders:
        payload["risk_warning_pending_family_orders"] = {
            "pending_family_orders": pending_family_orders,
            "max_pending_family_orders": max_pending_family_orders,
        }
    if max_pending_total_orders > 0 and pending_total_orders >= max_pending_total_orders:
        payload["risk_warning_pending_global_orders"] = {
            "pending_total_orders": pending_total_orders,
            "max_pending_total_orders": max_pending_total_orders,
        }
    risk_governor = getattr(bot, "evaluate_live_order_risk_governor", None)
    if callable(risk_governor):
        risk_result = risk_governor(payload, q=q)
        try:
            if (not risk_result.get("ok")) or risk_result.get("cap_reasons") or risk_result.get("warnings"):
                print(
                    "[AION-RISK-GOVERNOR] "
                    + json.dumps(
                        {
                            "ticker": risk_result.get("ticker") or payload.get("ticker"),
                            "family": risk_result.get("family") or family,
                            "side": risk_result.get("side") or payload.get("side"),
                            "selected_price": risk_result.get("selected_price") or payload.get("price"),
                            "ok": bool(risk_result.get("ok")),
                            "reason": risk_result.get("reason"),
                            "final_count": risk_result.get("final_count"),
                            "cap_reasons": risk_result.get("cap_reasons"),
                            "warnings": risk_result.get("warnings"),
                            "responsible_subsystem": "canonical_risk_governor",
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
        except Exception:
            pass
        if not risk_result.get("ok"):
            return {
                "submitted": False,
                "reason": risk_result.get("reason") or "risk_governor_block",
                "payload": risk_result.get("payload") or payload,
                "risk": risk_result,
            }
        payload = risk_result.get("payload") if isinstance(risk_result.get("payload"), dict) else payload
        payload["_risk_governor_checked"] = True
    authorization = bot.get_live_submit_authorization(payload)
    if not authorization.get("ok"):
        return {"submitted": False, "reason": authorization.get("reason"), "payload": payload, "authorization": authorization}
    if not bot.can_build_live_auth():
        return {"submitted": False, "reason": "auth_not_ready", "payload": payload}
    bot._reset_live_trade_counter_if_needed()
    if int(bot.live_trade_count_today or 0) >= int(max_live_trades_per_day or 0):
        payload["risk_warning_daily_trade_count"] = {
            "live_trade_count_today": int(bot.live_trade_count_today or 0),
            "max_live_trades_per_day": int(max_live_trades_per_day or 0),
        }
    active_timestamps = bot._active_live_order_timestamps()
    if len(active_timestamps) >= int(live_order_rate_limit or 1):
        payload["risk_warning_rate_limited"] = {
            "active_order_timestamps": len(active_timestamps),
            "live_order_rate_limit": int(live_order_rate_limit or 1),
        }
    side = str(payload.get("selected_side") or payload.get("side") or "").strip().lower()
    if side not in {"yes", "no"}:
        return {"submitted": False, "reason": "missing_selected_side", "payload": payload}
    payload["selected_side"] = side
    payload["side"] = side
    if bot.already_holding(payload.get("ticker")):
        return {"submitted": False, "reason": "duplicate_position_guard", "payload": payload}
    legacy_payload = bot._normalize_legacy_order_payload(payload)
    if not legacy_payload:
        return {"submitted": False, "reason": "invalid_payload", "payload": payload}
    selected_price_cents = (
        legacy_payload.get("yes_price")
        if legacy_payload.get("side") == "yes"
        else legacy_payload.get("no_price")
    )
    payload["price"] = (
        round(int(selected_price_cents) / 100.0, 4)
        if selected_price_cents not in (None, "")
        else bot.normalize_contract_price(
            payload.get("selected_price") or payload.get("price"),
            ticker=payload.get("ticker"),
            source="submit_live_order.result_price",
        )
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
        "sports_memory_context",
        "sports_memory_dimensions",
        "sports_memory_rank_adjustment",
        "sports_memory_confidence_adjustment",
        "sports_memory_size_multiplier",
        "sports_memory_aggression_multiplier",
        "sports_memory_regime",
        "sports_regime_context",
        "sports_regime",
        "sports_regime_type",
        "sports_momentum_classification",
        "sports_momentum_score",
        "sports_regime_rank_adjustment",
        "sports_regime_size_multiplier",
        "sports_regime_aggression_multiplier",
        "game_thesis_context",
        "game_id",
        "game_thesis_type",
        "game_thesis_confidence",
        "game_thesis_rank_boost",
        "game_thesis_size_multiplier",
        "game_thesis_aggression_multiplier",
        "game_related_candidates",
        "game_primary_candidate",
        "game_secondary_candidates",
        "sports_ladder_context",
        "sports_ladder_plan_id",
        "sports_ladder_role",
        "sports_ladder_leg_rank",
        "sports_ladder_leg_type",
        "sports_ladder_score",
        "sports_ladder_alignment",
        "sports_ladder_size_multiplier",
        "sports_ladder_size_cap",
        "sports_ladder_game_exposure_cap",
    ):
        if payload.get(field) is None and q is not None:
            try:
                value = getattr(q, field, None)
            except Exception:
                value = None
            if value is not None:
                payload[field] = value
    entry_context = payload.get("entry_context") if isinstance(payload.get("entry_context"), dict) else {}
    for field in (
        "sports_memory_context",
        "sports_memory_dimensions",
        "sports_memory_rank_adjustment",
        "sports_memory_confidence_adjustment",
        "sports_memory_size_multiplier",
        "sports_memory_aggression_multiplier",
        "sports_memory_regime",
        "sports_regime_context",
        "sports_regime",
        "sports_regime_type",
        "sports_momentum_classification",
        "sports_momentum_score",
        "sports_regime_rank_adjustment",
        "sports_regime_size_multiplier",
        "sports_regime_aggression_multiplier",
        "game_thesis_context",
        "game_id",
        "game_thesis_type",
        "game_thesis_confidence",
        "game_thesis_rank_boost",
        "game_thesis_size_multiplier",
        "game_thesis_aggression_multiplier",
        "game_related_candidates",
        "game_primary_candidate",
        "game_secondary_candidates",
        "sports_ladder_context",
        "sports_ladder_plan_id",
        "sports_ladder_role",
        "sports_ladder_leg_rank",
        "sports_ladder_leg_type",
        "sports_ladder_score",
        "sports_ladder_alignment",
        "sports_ladder_size_multiplier",
        "sports_ladder_size_cap",
        "sports_ladder_game_exposure_cap",
        "sports_edge_context",
        "sports_edge_tags",
    ):
        if payload.get(field) is not None:
            entry_context.setdefault(field, payload.get(field))
    if entry_context:
        payload["entry_context"] = entry_context
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
                    "sports_memory_context": getattr(q, "sports_memory_context", None),
                    "sports_memory_dimensions": getattr(q, "sports_memory_dimensions", None),
                    "sports_memory_regime": getattr(q, "sports_memory_regime", None),
                    "sports_memory_rank_adjustment": getattr(q, "sports_memory_rank_adjustment", None),
                    "sports_memory_size_multiplier": getattr(q, "sports_memory_size_multiplier", None),
                    "sports_regime_context": getattr(q, "sports_regime_context", None),
                    "sports_regime": getattr(q, "sports_regime", None),
                    "sports_regime_type": getattr(q, "sports_regime_type", None),
                    "sports_momentum_classification": getattr(q, "sports_momentum_classification", None),
                    "sports_momentum_score": getattr(q, "sports_momentum_score", None),
                    "sports_regime_rank_adjustment": getattr(q, "sports_regime_rank_adjustment", None),
                    "sports_regime_size_multiplier": getattr(q, "sports_regime_size_multiplier", None),
                    "sports_regime_aggression_multiplier": getattr(q, "sports_regime_aggression_multiplier", None),
                    "game_thesis_context": getattr(q, "game_thesis_context", None),
                    "game_id": getattr(q, "game_id", None),
                    "game_thesis_type": getattr(q, "game_thesis_type", None),
                    "game_thesis_confidence": getattr(q, "game_thesis_confidence", None),
                    "game_thesis_rank_boost": getattr(q, "game_thesis_rank_boost", None),
                    "game_thesis_size_multiplier": getattr(q, "game_thesis_size_multiplier", None),
                    "sports_ladder_context": getattr(q, "sports_ladder_context", None),
                    "sports_ladder_plan_id": getattr(q, "sports_ladder_plan_id", None),
                    "sports_ladder_role": getattr(q, "sports_ladder_role", None),
                    "sports_ladder_leg_type": getattr(q, "sports_ladder_leg_type", None),
                    "sports_ladder_score": getattr(q, "sports_ladder_score", None),
                    "sports_ladder_size_cap": getattr(q, "sports_ladder_size_cap", None),
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

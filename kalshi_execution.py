"""Canonical live order submission for AION."""

from __future__ import annotations

import hashlib
import json
import os
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional

from kalshi_orderbook import coerce_price, normalize_side


CANONICAL_ORDER_ENDPOINT = "/trade-api/v2/portfolio/orders"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def price_to_cents(value: Any) -> Optional[int]:
    """Convert a 1-cent tick price without binary floating-point rounding.

    Reject off-tick values instead of silently changing the order limit.
    """
    price = coerce_price(value)
    if price is None:
        return None
    try:
        decimal_price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not decimal_price.is_finite() or not (Decimal("0") < decimal_price < Decimal("1")):
        return None
    cents = decimal_price * 100
    rounded = cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if abs(cents - rounded) > Decimal("0.000001"):
        return None
    return int(rounded)


def ensure_client_order_id(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(payload or {})
    if not payload.get("client_order_id"):
        seed = f"{payload.get('ticker')}:{payload.get('side')}:{time.time_ns()}:{os.getpid()}"
        payload["client_order_id"] = "aion-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return payload


def canonical_order_payload(
    *,
    ticker: str,
    side: str,
    action: str,
    count: int,
    price: Any,
    reduce_only: bool = False,
    target_id: str | None = None,
    client_order_id: str | None = None,
) -> Dict[str, Any]:
    side = normalize_side(side)
    action = str(action or "").strip().lower()
    if action in {"exit", "close"}:
        action = "sell"
    cents = price_to_cents(price)
    payload: Dict[str, Any] = {
        "ticker": str(ticker or "").strip(),
        "side": side,
        "action": action,
        "count": int(count or 0),
        "type": "limit",
        "time_in_force": "immediate_or_cancel",
        "post_only": False,
        "reduce_only": bool(reduce_only),
        "self_trade_prevention_type": "taker_at_cross",
    }
    if client_order_id:
        payload["client_order_id"] = str(client_order_id)
    if target_id:
        payload["target_id"] = str(target_id)
    if side == "yes":
        payload["yes_price"] = cents
    elif side == "no":
        payload["no_price"] = cents
    return ensure_client_order_id(payload)


def validate_canonical_order_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    ticker = str(payload.get("ticker") or "").strip()
    side = normalize_side(payload.get("side"))
    action = str(payload.get("action") or "").strip().lower()
    count = payload.get("count")
    client_order_id = str(payload.get("client_order_id") or "").strip()
    price_keys = [key for key in ("yes_price", "no_price") if payload.get(key) not in (None, "")]
    if not ticker:
        return {"ok": False, "reason": "missing_ticker"}
    if not client_order_id:
        return {"ok": False, "reason": "missing_client_order_id"}
    if side not in {"yes", "no"}:
        return {"ok": False, "reason": "invalid_side"}
    if action not in {"buy", "sell"}:
        return {"ok": False, "reason": "invalid_action"}
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return {"ok": False, "reason": "invalid_count", "count": count}
    expected = "yes_price" if side == "yes" else "no_price"
    if price_keys != [expected]:
        return {
            "ok": False,
            "reason": "price_field_does_not_match_side",
            "price_keys": price_keys,
            "expected_price_key": expected,
        }
    price = payload.get(expected)
    if not isinstance(price, int) or isinstance(price, bool) or not (1 <= price <= 99):
        return {"ok": False, "reason": "invalid_integer_cents_price", "price": price}
    if bool(payload.get("reduce_only")) and payload.get("time_in_force") != "immediate_or_cancel":
        return {"ok": False, "reason": "reduce_only_requires_ioc"}
    return {
        "ok": True,
        "reason": "schema_valid",
        "price_key": expected,
        "price_cents": price,
        "count": count,
    }


def compact_body(body: Any, limit: int = 900) -> Any:
    if body is None:
        return None
    try:
        text = json.dumps(body, sort_keys=True, default=str)
    except Exception:
        text = str(body)
    if len(text) <= limit:
        return body
    return text[:limit] + "...[truncated]"


def exchange_rejection_reason(response: Dict[str, Any]) -> str:
    response = response if isinstance(response, dict) else {}
    body = response.get("body") if isinstance(response.get("body"), dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = str(error.get("code") or "").strip()
    if code:
        return code
    message = str(error.get("message") or body.get("message") or "").strip()
    if message:
        return message[:120]
    return str(response.get("reason") or "request_failed")


def extract_order_id(response_body: Any) -> Optional[str]:
    body = response_body if isinstance(response_body, dict) else {}
    candidates = [
        body.get("order_id"),
        body.get("id"),
        (body.get("order") or {}).get("order_id") if isinstance(body.get("order"), dict) else None,
        (body.get("order") or {}).get("id") if isinstance(body.get("order"), dict) else None,
    ]
    for candidate in candidates:
        if candidate not in (None, ""):
            return str(candidate)
    return None


def submit_canonical_order(bot: Any, payload: Dict[str, Any], *, dry_run: bool = False, meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = ensure_client_order_id(payload if isinstance(payload, dict) else {})
    meta = meta if isinstance(meta, dict) else {}
    validation = validate_canonical_order_payload(payload)
    selected_price = payload.get(validation.get("price_key")) if validation.get("price_key") else None
    if not validation.get("ok"):
        bot.log_json(
            "AION-FINAL-REJECT",
            {
                **meta,
                "ticker": payload.get("ticker"),
                "side": payload.get("side"),
                "selected_price": selected_price,
                "reason": validation.get("reason"),
                "responsible_subsystem": "canonical_payload_validation",
            },
        )
        return {"submitted": False, "reason": validation.get("reason"), "payload": payload, "validation": validation}

    submit_log = {
        **meta,
        "method": "POST",
        "endpoint": CANONICAL_ORDER_ENDPOINT,
        "ticker": payload.get("ticker"),
        "client_order_id": payload.get("client_order_id"),
        "selected_side": payload.get("side"),
        "action": payload.get("action"),
        "selected_price_cents": selected_price,
        "count": payload.get("count"),
        "time_in_force": payload.get("time_in_force"),
        "reduce_only": payload.get("reduce_only"),
        "post_only": payload.get("post_only"),
        "payload_keys": sorted(payload.keys()),
    }
    bot.log_json("ORDER-SUBMIT", submit_log)
    if dry_run:
        bot.log_json("ORDER-DRY-RUN", {**submit_log, "reason": "effective_dry_run"})
        return {
            "submitted": False,
            "reason": "effective_dry_run",
            "payload": payload,
            "request_payload": payload,
            "validation": validation,
        }

    response = bot._live_request_json("POST", CANONICAL_ORDER_ENDPOINT, json_payload=payload, timeout=20)
    if response.get("ok"):
        body = response.get("body")
        order_id = extract_order_id(body)
        bot.log_json(
            "ORDER-SUBMITTED",
            {
                **submit_log,
                "status_code": response.get("status_code"),
                "order_id": order_id,
            },
        )
        return {
            "submitted": True,
            "reason": "submitted",
            "payload": payload,
            "request_payload": payload,
            "response": body,
            "status_code": response.get("status_code"),
            "order_id": order_id,
            "validation": validation,
        }

    reason = exchange_rejection_reason(response)
    bot.log_json(
        "ORDER-REJECT",
        {
            **submit_log,
            "status_code": response.get("status_code"),
            "reason": reason,
            "http_reason": response.get("reason"),
            "response_body": compact_body(response.get("body")),
        },
    )
    return {
        "submitted": False,
        "reason": reason,
        "http_reason": response.get("reason"),
        "payload": payload,
        "request_payload": payload,
        "response_body": response.get("body"),
        "status_code": response.get("status_code"),
        "validation": validation,
    }


def effective_dry_run(*, live_mode: bool, live_armed: bool, live_env_confirmed: bool, configured_dry_run: bool) -> bool:
    if configured_dry_run:
        return True
    if not (live_mode and live_armed and live_env_confirmed and _env_bool("KALSHI_ALLOW_LIVE", False)):
        return True
    return False

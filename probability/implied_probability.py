from __future__ import annotations

from typing import Any, Dict


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def extract_implied_probability(market: Dict[str, Any] | None, quote: Any, ws_snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    market = market if isinstance(market, dict) else {}
    ws_snapshot = ws_snapshot if isinstance(ws_snapshot, dict) else {}
    yes_bid = _safe_float(getattr(quote, "yes_bid", None), 0.0)
    yes_ask = _safe_float(getattr(quote, "yes_ask", None), 0.0)
    last_price = _safe_float(getattr(quote, "last_price", None), 0.0)
    no_bid = _safe_float(getattr(quote, "no_bid", None), 0.0)
    no_ask = _safe_float(getattr(quote, "no_ask", None), 0.0)
    spread = max(0.0, yes_ask - yes_bid) if yes_ask > 0 and yes_bid > 0 else _safe_float(ws_snapshot.get("spread"), 0.0)
    orderbook_bias = _safe_float(ws_snapshot.get("orderbook_imbalance"), 0.0)
    price_velocity = _safe_float(ws_snapshot.get("price_velocity"), 0.0)
    liquidity = _safe_float(ws_snapshot.get("liquidity"), _safe_float(getattr(quote, "liquidity", None), 0.0))
    yes_prob = _clamp(yes_ask if yes_ask > 0 else last_price if last_price > 0 else yes_bid)
    no_prob = _clamp(no_ask if no_ask > 0 else (1.0 - yes_prob))
    spread_adjustment = _clamp(spread * 0.75, 0.0, 0.20)
    spread_adjusted_probability = _clamp(yes_prob - (spread_adjustment / 2.0) + (0.05 if orderbook_bias > 0.15 else -0.05 if orderbook_bias < -0.15 else 0.0))
    probability_velocity = _safe_float(ws_snapshot.get("probability_velocity"), price_velocity)
    short_term_drift = _safe_float(ws_snapshot.get("probability_drift"), 0.0)
    if not short_term_drift and last_price > 0 and yes_prob > 0:
        short_term_drift = _safe_float(yes_prob - last_price, 0.0)
    implied_bias = _clamp((spread_adjusted_probability - 0.5) + (orderbook_bias * 0.15))
    summary = {
        "ticker": str(market.get("ticker") or "").strip(),
        "yes_probability": round(yes_prob, 4),
        "no_probability": round(no_prob, 4),
        "spread_adjusted_probability": round(spread_adjusted_probability, 4),
        "probability_velocity": round(probability_velocity, 4),
        "probability_drift": round(short_term_drift, 4),
        "orderbook_bias": round(orderbook_bias, 4),
        "implied_bias": round(implied_bias, 4),
        "spread": round(spread, 4),
        "liquidity": round(liquidity, 4),
        "last_price": round(last_price, 4),
        "source": str(ws_snapshot.get("source") or "rest").strip() or "rest",
        "stale": bool(ws_snapshot.get("stale")),
    }
    return summary


"""Layer 2 candidate sanitization helpers extracted from the live pipeline."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from kalshi_sniper.orderbook_health import (
        has_executable_pair as orderbook_health_has_executable_pair,
        is_synthetic_book as orderbook_health_is_synthetic_book,
    )
except Exception:
    orderbook_health_has_executable_pair = None
    orderbook_health_is_synthetic_book = None


def to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default
            return float(stripped)
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_round(value: Any, ndigits: int = 4, default: Any = None) -> Any:
    numeric = to_float(value, None)
    if numeric is None:
        return default
    try:
        return round(float(numeric), int(ndigits))
    except (TypeError, ValueError):
        return default


def coerce_mapping(source: Any) -> Dict[str, Any]:
    if isinstance(source, dict):
        return dict(source)
    if source is None:
        return {}
    coerced: Dict[str, Any] = {}
    try:
        if hasattr(source, "__dict__"):
            for key, value in vars(source).items():
                if value not in (None, "", [], {}):
                    coerced[key] = value
    except Exception:
        pass
    return coerced


def _pick(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _source_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        value = source.get(key, default)
    else:
        value = getattr(source, key, default)
    return default if value is None else value


def _as_bool(value: Any, default: Optional[bool] = None) -> Optional[bool]:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", "none", "null", ""}:
            return False
    return bool(value)


def normalize_candidate_skip_reason(reason: Any) -> str:
    normalized = str(reason or "").strip().lower()
    return normalized or "unknown"


def build_candidate_sanitizer_summary(total_candidates_seen: int = 0) -> Dict[str, Any]:
    return {
        "total_candidates_seen": int(total_candidates_seen or 0),
        "removed_non_tradeable": 0,
        "remaining_tradeable": 0,
        "removed_boundary_price": 0,
        "removed_catastrophic_spread": 0,
        "removed_unusable_orderbook": 0,
        "removed_fake_liquidity": 0,
        "removed_unexecutable": 0,
        "selected_tradeable_candidate": None,
        "top_skip_reasons": [],
    }


def _has_executable_pair(*, yes_bid=None, yes_ask=None, no_bid=None, no_ask=None) -> bool:
    if orderbook_health_has_executable_pair is not None:
        return bool(
            orderbook_health_has_executable_pair(
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                no_bid=no_bid,
                no_ask=no_ask,
            )
        )
    yes_bid = to_float(yes_bid, None)
    yes_ask = to_float(yes_ask, None)
    no_bid = to_float(no_bid, None)
    no_ask = to_float(no_ask, None)
    return bool(
        (yes_bid is not None and yes_bid > 0.0 and yes_ask is not None and yes_ask > 0.0)
        or (no_bid is not None and no_bid > 0.0 and no_ask is not None and no_ask > 0.0)
    )


def _is_synthetic_book(*, yes_bid=None, yes_ask=None, no_bid=None, no_ask=None) -> bool:
    if orderbook_health_is_synthetic_book is not None:
        return bool(
            orderbook_health_is_synthetic_book(
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                no_bid=no_bid,
                no_ask=no_ask,
            )
        )
    yes_bid = to_float(yes_bid, None)
    yes_ask = to_float(yes_ask, None)
    no_bid = to_float(no_bid, None)
    no_ask = to_float(no_ask, None)
    return bool(
        (yes_bid is not None and yes_ask is not None and yes_bid == 0.0 and yes_ask >= 0.99)
        or (no_bid is not None and no_ask is not None and no_bid <= 0.01 and no_ask >= 0.99)
    )


def classify_candidate_skip_reason(details: Dict[str, Any]) -> str:
    details = details if isinstance(details, dict) else {}
    if details.get("boundary_price"):
        return "boundary_price"
    if details.get("catastrophic_spread"):
        return "catastrophic_spread"
    if details.get("unusable_orderbook"):
        return "unusable_orderbook"
    if details.get("fake_liquidity"):
        return "fake_liquidity"
    if details.get("unexecutable"):
        return "unexecutable"
    if details.get("synthetic_yes_book") or details.get("synthetic_no_book"):
        return "synthetic_book"
    if details.get("additional_boundary"):
        return "boundary_book"
    return "ok"


def validate_candidate_tradeability(candidate=None, market=None, get_market_family=None, fallback_family=None):
    candidate = coerce_mapping(candidate)
    market = coerce_mapping(market)
    family = str(
        fallback_family
        or candidate.get("family")
        or market.get("family")
        or (get_market_family(market) if callable(get_market_family) else None)
        or "UNKNOWN"
    ).strip().upper() or "UNKNOWN"
    ticker = str(
        _pick(
            candidate.get("ticker"),
            market.get("ticker"),
            _source_value(candidate.get("q"), "ticker", None),
            _source_value(candidate.get("quote"), "ticker", None),
        )
        or ""
    ).strip() or None
    q = _pick(candidate.get("q"), candidate.get("quote"), candidate.get("candidate"))

    selected_side = str(
        _pick(
            _source_value(q, "selected_side", None),
            _source_value(q, "side", None),
            candidate.get("selected_side"),
            candidate.get("side"),
            market.get("selected_side"),
            market.get("side"),
        )
        or ""
    ).strip().lower() or None
    yes_bid = to_float(
        _pick(
            _source_value(q, "yes_bid", None),
            _source_value(q, "yes_bid_dollars", None),
            candidate.get("yes_bid"),
            candidate.get("yes_bid_dollars"),
            market.get("yes_bid"),
            market.get("yes_bid_dollars"),
        ),
        None,
    )
    yes_ask = to_float(
        _pick(
            _source_value(q, "yes_ask", None),
            _source_value(q, "yes_ask_dollars", None),
            candidate.get("yes_ask"),
            candidate.get("yes_ask_dollars"),
            market.get("yes_ask"),
            market.get("yes_ask_dollars"),
        ),
        None,
    )
    no_bid = to_float(
        _pick(
            _source_value(q, "no_bid", None),
            _source_value(q, "no_bid_dollars", None),
            candidate.get("no_bid"),
            candidate.get("no_bid_dollars"),
            market.get("no_bid"),
            market.get("no_bid_dollars"),
        ),
        None,
    )
    no_ask = to_float(
        _pick(
            _source_value(q, "no_ask", None),
            _source_value(q, "no_ask_dollars", None),
            candidate.get("no_ask"),
            candidate.get("no_ask_dollars"),
            market.get("no_ask"),
            market.get("no_ask_dollars"),
        ),
        None,
    )
    selected_price = to_float(
        _pick(
            _source_value(q, "selected_price", None),
            _source_value(q, "selected_price_dollars", None),
            candidate.get("selected_price"),
            candidate.get("selected_price_dollars"),
            yes_ask if selected_side == "yes" else None,
            no_ask if selected_side == "no" else None,
            yes_ask if yes_ask is not None else yes_bid,
            no_ask if no_ask is not None else no_bid,
        ),
        None,
    )
    selected_price_cents = to_float(
        _pick(
            _source_value(q, "selected_price_cents", None),
            candidate.get("selected_price_cents"),
            market.get("selected_price_cents"),
        ),
        None,
    )
    if selected_price_cents is None and selected_price is not None:
        selected_price_cents = int(round(float(selected_price) * 100.0))
    if selected_price is None and selected_price_cents is not None:
        selected_price = round(float(selected_price_cents) / 100.0, 4)
    yes_pair_valid = bool(yes_bid is not None and yes_ask is not None and yes_bid > 0.0 and yes_ask > 0.0)
    no_pair_valid = bool(no_bid is not None and no_ask is not None and no_bid > 0.0 and no_ask > 0.0)
    executable_side_present = _as_bool(
        _pick(
            _source_value(q, "executable_side_present", None),
            candidate.get("executable_side_present"),
            market.get("executable_side_present"),
            yes_pair_valid or no_pair_valid,
        ),
        default=yes_pair_valid or no_pair_valid,
    )
    both_sides_unexecutable = _as_bool(
        _pick(
            _source_value(q, "both_sides_unexecutable", None),
            candidate.get("both_sides_unexecutable"),
            market.get("both_sides_unexecutable"),
            (not yes_pair_valid and not no_pair_valid),
        ),
        default=not yes_pair_valid and not no_pair_valid,
    )
    quote_ready = _as_bool(
        _pick(
            _source_value(q, "quote_ready", None),
            candidate.get("quote_ready"),
            market.get("quote_ready"),
        ),
        default=executable_side_present,
    )
    payload_ready = _as_bool(
        _pick(
            _source_value(q, "payload_ready", None),
            candidate.get("payload_ready"),
            market.get("payload_ready"),
        ),
        default=quote_ready,
    )
    orderbook_ready = _as_bool(
        _pick(
            _source_value(q, "orderbook_ready", None),
            candidate.get("orderbook_ready"),
            market.get("orderbook_ready"),
        ),
        default=None,
    )
    orderbook_usable = _as_bool(
        _pick(
            _source_value(q, "orderbook_usable", None),
            candidate.get("orderbook_usable"),
            market.get("orderbook_usable"),
        ),
        default=None,
    )
    spread_bucket = str(
        _pick(
            _source_value(q, "spread_bucket", None),
            candidate.get("spread_bucket"),
            market.get("spread_bucket"),
        )
        or ""
    ).strip().lower() or None
    spread_is_catastrophic = bool(
        _as_bool(
            _pick(
                _source_value(q, "spread_is_catastrophic", None),
                candidate.get("spread_is_catastrophic"),
                market.get("spread_is_catastrophic"),
                spread_bucket in {"catastrophic", "unsafe"},
            ),
            default=spread_bucket in {"catastrophic", "unsafe"},
        )
    )
    edge_source = str(
        _pick(
            _source_value(q, "edge_source", None),
            candidate.get("edge_source"),
            market.get("edge_source"),
        )
        or ""
    ).strip().lower() or None
    edge_after_cost = to_float(
        _pick(
            _source_value(q, "edge_after_cost", None),
            candidate.get("edge_after_cost"),
            market.get("edge_after_cost"),
        ),
        None,
    )
    liquidity = to_float(
        _pick(
            _source_value(q, "liquidity", None),
            candidate.get("liquidity"),
            market.get("liquidity"),
        ),
        None,
    )
    liquidity_depth = to_float(
        _pick(
            _source_value(q, "liquidity_depth", None),
            _source_value(q, "liquidity_size", None),
            candidate.get("liquidity_depth"),
            candidate.get("liquidity_size"),
            market.get("liquidity_depth"),
            market.get("liquidity_size"),
        ),
        None,
    )
    boundary_price = bool(
        (selected_price_cents is not None and (selected_price_cents <= 0 or selected_price_cents >= 100))
        or (selected_price is not None and (selected_price <= 0.01 or selected_price >= 0.99))
        or (yes_bid is not None and yes_ask is not None and yes_bid == 0.0 and yes_ask >= 0.99)
        or (no_bid is not None and no_ask is not None and no_bid <= 0.01 and no_ask >= 0.99)
    )
    catastrophic_spread = bool(
        spread_is_catastrophic
        or (spread_bucket in {"catastrophic", "unsafe"})
        or (to_float(candidate.get("spread_cents"), None) is not None and to_float(candidate.get("spread_cents"), None) >= 90)
    )
    fake_liquidity = bool(
        liquidity is not None and liquidity_depth is not None and liquidity <= 0.0 and liquidity_depth <= 0.0
    )
    unusable_orderbook = bool(orderbook_ready is False or orderbook_usable is False)
    unexecutable = bool(
        both_sides_unexecutable
        or not executable_side_present
        or quote_ready is False
        or payload_ready is False
        or edge_source == "fallback_zero"
        or (edge_after_cost is not None and edge_after_cost <= 0.0)
        or (not yes_pair_valid and not no_pair_valid)
    )
    synthetic_yes_book = bool(_is_synthetic_book(yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask) and yes_bid == 0.0 and yes_ask >= 0.99)
    synthetic_no_book = bool(_is_synthetic_book(yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask) and no_bid <= 0.01 and no_ask >= 0.99)
    additional_boundary = bool(
        yes_bid is not None and yes_ask is not None and yes_bid <= 0.0 and yes_ask >= 0.95
    ) or bool(
        no_bid is not None and no_ask is not None and no_bid <= 0.05 and no_ask >= 0.99
    )

    details = {
        "ticker": ticker,
        "family": family,
        "selected_side": selected_side,
        "selected_price": safe_round(selected_price, 4, None),
        "selected_price_cents": int(round(selected_price_cents)) if selected_price_cents is not None else None,
        "spread_cents": int(round(to_float(candidate.get("spread_cents"), None))) if to_float(candidate.get("spread_cents"), None) is not None else None,
        "spread_bucket": spread_bucket,
        "spread_is_catastrophic": bool(spread_is_catastrophic),
        "orderbook_ready": bool(orderbook_ready),
        "orderbook_usable": bool(orderbook_usable),
        "both_sides_unexecutable": bool(both_sides_unexecutable),
        "executable_side_present": bool(executable_side_present),
        "quote_ready": bool(quote_ready),
        "payload_ready": bool(payload_ready),
        "edge_source": edge_source,
        "edge_after_cost": safe_round(edge_after_cost, 4, None),
        "liquidity": safe_round(liquidity, 4, None),
        "liquidity_depth": safe_round(liquidity_depth, 4, None),
        "yes_bid": safe_round(yes_bid, 4, None),
        "yes_ask": safe_round(yes_ask, 4, None),
        "no_bid": safe_round(no_bid, 4, None),
        "no_ask": safe_round(no_ask, 4, None),
        "synthetic_yes_book": bool(synthetic_yes_book),
        "synthetic_no_book": bool(synthetic_no_book),
        "boundary_price": bool(boundary_price),
        "catastrophic_spread": bool(catastrophic_spread),
        "fake_liquidity": bool(fake_liquidity),
        "unexecutable": bool(unexecutable),
        "additional_boundary": bool(additional_boundary),
    }
    skip_reason = classify_candidate_skip_reason(details)
    details["skip_reason"] = skip_reason if skip_reason != "ok" else None
    return skip_reason == "ok", skip_reason, details


def sanitize_tradeable_candidates(
    candidates: Iterable[Any],
    markets: Optional[Iterable[Any]] = None,
    get_market_family=None,
):
    markets = markets if isinstance(markets, list) else list(markets or [])
    market_lookup = {
        str(market.get("ticker", "") or "").strip(): market
        for market in markets
        if isinstance(market, dict) and str(market.get("ticker", "") or "").strip()
    }
    summary = build_candidate_sanitizer_summary(0)
    sanitized: List[Any] = []
    best_selected = None
    best_selected_score = None

    for item in candidates or []:
        summary["total_candidates_seen"] += 1
        if isinstance(item, dict):
            raw_score = to_float(item.get("score"), to_float(item.get("raw_score"), 0.0))
            q = _pick(item.get("q"), item.get("quote"), item.get("candidate"))
            spread = to_float(item.get("spread"), None)
            family = str(item.get("family") or "").strip().upper() or None
        elif isinstance(item, (list, tuple)) and len(item) >= 4:
            raw_score, q, spread, family = item[:4]
            raw_score = to_float(raw_score, 0.0)
            family = str(family or "").strip().upper() or None
        else:
            summary["removed_non_tradeable"] += 1
            summary["removed_unexecutable"] += 1
            continue

        ticker = str(getattr(q, "ticker", "") or _source_value(q, "ticker", "") or "").strip()
        market = market_lookup.get(ticker, {})
        candidate_family = str(
            family
            or _source_value(q, "family", None)
            or _source_value(market, "family", None)
            or (get_market_family(market) if callable(get_market_family) else None)
            or "UNKNOWN"
        ).strip().upper() or "UNKNOWN"
        selected_side = str(
            _pick(
                _source_value(q, "selected_side", None),
                _source_value(q, "side", None),
                item.get("selected_side") if isinstance(item, dict) else None,
                item.get("side") if isinstance(item, dict) else None,
                market.get("selected_side"),
                market.get("side"),
            )
            or ""
        ).strip().lower() or None
        yes_bid = to_float(
            _pick(
                _source_value(q, "yes_bid", None),
                _source_value(q, "yes_bid_dollars", None),
                item.get("yes_bid") if isinstance(item, dict) else None,
                item.get("yes_bid_dollars") if isinstance(item, dict) else None,
                market.get("yes_bid"),
                market.get("yes_bid_dollars"),
            ),
            None,
        )
        yes_ask = to_float(
            _pick(
                _source_value(q, "yes_ask", None),
                _source_value(q, "yes_ask_dollars", None),
                item.get("yes_ask") if isinstance(item, dict) else None,
                item.get("yes_ask_dollars") if isinstance(item, dict) else None,
                market.get("yes_ask"),
                market.get("yes_ask_dollars"),
            ),
            None,
        )
        no_bid = to_float(
            _pick(
                _source_value(q, "no_bid", None),
                _source_value(q, "no_bid_dollars", None),
                item.get("no_bid") if isinstance(item, dict) else None,
                item.get("no_bid_dollars") if isinstance(item, dict) else None,
                market.get("no_bid"),
                market.get("no_bid_dollars"),
            ),
            None,
        )
        no_ask = to_float(
            _pick(
                _source_value(q, "no_ask", None),
                _source_value(q, "no_ask_dollars", None),
                item.get("no_ask") if isinstance(item, dict) else None,
                item.get("no_ask_dollars") if isinstance(item, dict) else None,
                market.get("no_ask"),
                market.get("no_ask_dollars"),
            ),
            None,
        )
        selected_price = to_float(
            _pick(
                _source_value(q, "selected_price", None),
                _source_value(q, "selected_price_dollars", None),
                item.get("selected_price") if isinstance(item, dict) else None,
                item.get("selected_price_dollars") if isinstance(item, dict) else None,
                yes_ask if selected_side == "yes" else None,
                no_ask if selected_side == "no" else None,
                yes_ask if yes_ask is not None else yes_bid,
                no_ask if no_ask is not None else no_bid,
            ),
            None,
        )
        selected_price_cents = to_float(
            _pick(
                _source_value(q, "selected_price_cents", None),
                item.get("selected_price_cents") if isinstance(item, dict) else None,
                market.get("selected_price_cents"),
            ),
            None,
        )
        if selected_price_cents is None and selected_price is not None:
            selected_price_cents = int(round(float(selected_price) * 100.0))
        if selected_price is None and selected_price_cents is not None:
            selected_price = round(float(selected_price_cents) / 100.0, 4)
        yes_pair_valid = bool(yes_bid is not None and yes_ask is not None and yes_bid > 0.0 and yes_ask > 0.0)
        no_pair_valid = bool(no_bid is not None and no_ask is not None and no_bid > 0.0 and no_ask > 0.0)
        executable_side_present = bool(
            _as_bool(
                _pick(
                    _source_value(q, "executable_side_present", None),
                    item.get("executable_side_present") if isinstance(item, dict) else None,
                    yes_pair_valid or no_pair_valid,
                ),
                default=yes_pair_valid or no_pair_valid,
            )
        )
        both_sides_unexecutable = bool(
            _as_bool(
                _pick(
                    _source_value(q, "both_sides_unexecutable", None),
                    item.get("both_sides_unexecutable") if isinstance(item, dict) else None,
                    (not yes_pair_valid and not no_pair_valid),
                ),
                default=not yes_pair_valid and not no_pair_valid,
            )
        )
        quote_ready = _as_bool(
            _pick(
                _source_value(q, "quote_ready", None),
                item.get("quote_ready") if isinstance(item, dict) else None,
                market.get("quote_ready"),
            ),
            default=executable_side_present,
        )
        payload_ready = _as_bool(
            _pick(
                _source_value(q, "payload_ready", None),
                item.get("payload_ready") if isinstance(item, dict) else None,
                market.get("payload_ready"),
            ),
            default=quote_ready,
        )
        orderbook_ready = _as_bool(
            _pick(
                _source_value(q, "orderbook_ready", None),
                item.get("orderbook_ready") if isinstance(item, dict) else None,
                market.get("orderbook_ready"),
            ),
            default=None,
        )
        orderbook_usable = _as_bool(
            _pick(
                _source_value(q, "orderbook_usable", None),
                item.get("orderbook_usable") if isinstance(item, dict) else None,
                market.get("orderbook_usable"),
            ),
            default=None,
        )
        spread_bucket = str(
            _pick(
                _source_value(q, "spread_bucket", None),
                item.get("spread_bucket") if isinstance(item, dict) else None,
                market.get("spread_bucket"),
            )
            or ""
        ).strip().lower() or None
        spread_is_catastrophic = bool(
            _as_bool(
                _pick(
                    _source_value(q, "spread_is_catastrophic", None),
                    item.get("spread_is_catastrophic") if isinstance(item, dict) else None,
                    market.get("spread_is_catastrophic"),
                    spread_bucket in {"catastrophic", "unsafe"},
                ),
                default=spread_bucket in {"catastrophic", "unsafe"},
            )
        )
        edge_source = str(
            _pick(
                _source_value(q, "edge_source", None),
                item.get("edge_source") if isinstance(item, dict) else None,
                market.get("edge_source"),
            )
            or ""
        ).strip().lower() or None
        edge_after_cost = to_float(
            _pick(
                _source_value(q, "edge_after_cost", None),
                item.get("edge_after_cost") if isinstance(item, dict) else None,
                market.get("edge_after_cost"),
            ),
            None,
        )
        liquidity = to_float(
            _pick(
                _source_value(q, "liquidity", None),
                item.get("liquidity") if isinstance(item, dict) else None,
                market.get("liquidity"),
            ),
            None,
        )
        liquidity_depth = to_float(
            _pick(
                _source_value(q, "liquidity_depth", None),
                _source_value(q, "liquidity_size", None),
                item.get("liquidity_depth") if isinstance(item, dict) else None,
                item.get("liquidity_size") if isinstance(item, dict) else None,
                market.get("liquidity_depth"),
                market.get("liquidity_size"),
            ),
            None,
        )
        boundary_price = bool(
            (selected_price_cents is not None and (selected_price_cents <= 0 or selected_price_cents >= 100))
            or (selected_price is not None and (selected_price <= 0.01 or selected_price >= 0.99))
            or (yes_bid is not None and yes_ask is not None and yes_bid == 0.0 and yes_ask >= 0.99)
            or (no_bid is not None and no_ask is not None and no_bid <= 0.01 and no_ask >= 0.99)
        )
        fake_liquidity = bool(
            liquidity is not None
            and liquidity_depth is not None
            and liquidity <= 0.0
            and liquidity_depth <= 0.0
        )
        unexecutable = bool(
            both_sides_unexecutable
            or not executable_side_present
            or quote_ready is False
            or payload_ready is False
            or edge_source == "fallback_zero"
            or (edge_after_cost is not None and edge_after_cost <= 0.0)
        )
        unusable_orderbook = bool(
            (orderbook_ready is False)
            or (orderbook_usable is False)
            or (not yes_pair_valid and not no_pair_valid and orderbook_ready is None and orderbook_usable is None)
        )
        synthetic_yes_book = bool(_is_synthetic_book(yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask) and yes_bid == 0.0 and yes_ask >= 0.99)
        synthetic_no_book = bool(_is_synthetic_book(yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=no_ask) and no_bid <= 0.01 and no_ask >= 0.99)
        additional_boundary = bool(
            yes_bid is not None and yes_ask is not None and yes_bid <= 0.0 and yes_ask >= 0.95
        ) or bool(
            no_bid is not None and no_ask is not None and no_bid <= 0.05 and no_ask >= 0.99
        )

        remove_reasons: List[str] = []
        if boundary_price:
            remove_reasons.append("removed_boundary_price")
        if spread_is_catastrophic:
            remove_reasons.append("removed_catastrophic_spread")
        if unusable_orderbook:
            remove_reasons.append("removed_unusable_orderbook")
        if fake_liquidity:
            remove_reasons.append("removed_fake_liquidity")
        if unexecutable:
            remove_reasons.append("removed_unexecutable")

        if remove_reasons:
            summary["removed_non_tradeable"] += 1
            for reason in remove_reasons:
                if reason in summary:
                    summary[reason] += 1
            continue

        sanitized.append(item)
        if best_selected is None or raw_score > best_selected_score:
            best_selected = item
            best_selected_score = raw_score

    summary["remaining_tradeable"] = len(sanitized)
    if best_selected is not None:
        if isinstance(best_selected, dict):
            best_q = _pick(best_selected.get("q"), best_selected.get("quote"), best_selected.get("candidate"))
            best_score = to_float(best_selected.get("score"), to_float(best_selected.get("raw_score"), 0.0))
            best_family = str(best_selected.get("family") or "").strip().upper() or None
            best_spread = to_float(best_selected.get("spread"), None)
        else:
            best_score, best_q, best_spread, best_family = best_selected[:4]
            best_score = to_float(best_score, 0.0)
            best_family = str(best_family or "").strip().upper() or None
        selected_price_cents_value = to_float(
            _pick(
                _source_value(best_q, "selected_price_cents", None),
            ),
            None,
        )
        summary["selected_tradeable_candidate"] = {
            "ticker": str(getattr(best_q, "ticker", "") or _source_value(best_q, "ticker", "") or "").strip() or None,
            "family": best_family,
            "side": str(
                _pick(
                    _source_value(best_q, "selected_side", None),
                    _source_value(best_q, "side", None),
                )
                or ""
            ).strip().lower() or None,
            "selected_price": to_float(
                _pick(
                    _source_value(best_q, "selected_price", None),
                    _source_value(best_q, "selected_price_dollars", None),
                ),
                None,
            ),
            "selected_price_cents": int(round(selected_price_cents_value)) if selected_price_cents_value is not None else None,
            "raw_score": round(best_score, 4),
            "spread": safe_round(best_spread, 4, None),
        }
    summary["top_skip_reasons"] = []
    return sanitized, dict(summary)


__all__ = [
    "build_candidate_sanitizer_summary",
    "classify_candidate_skip_reason",
    "coerce_mapping",
    "normalize_candidate_skip_reason",
    "sanitize_tradeable_candidates",
    "safe_round",
    "to_float",
    "validate_candidate_tradeability",
]

"""Orderbook health helpers extracted from the live execution pipeline.

This module intentionally stays small and pure. It mirrors the existing
spread-severity and orderbook-health logic so `kalshi_web.py` can delegate
without changing live behavior.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


KALSHI_CONTRACT_MIN_PRICE = 0.01
KALSHI_CONTRACT_MAX_PRICE = 0.99


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


def normalize_aggression_profile(profile: Any) -> str:
    return str(profile or "safe").strip().lower() or "safe"


def _pick(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def has_executable_pair(*, yes_bid=None, yes_ask=None, no_bid=None, no_ask=None) -> bool:
    yes_bid = to_float(yes_bid, None)
    yes_ask = to_float(yes_ask, None)
    no_bid = to_float(no_bid, None)
    no_ask = to_float(no_ask, None)
    return bool(
        (yes_bid is not None and yes_bid > 0.0 and yes_ask is not None and yes_ask > 0.0)
        or (no_bid is not None and no_bid > 0.0 and no_ask is not None and no_ask > 0.0)
    )


def is_synthetic_book(*, yes_bid=None, yes_ask=None, no_bid=None, no_ask=None) -> bool:
    yes_bid = to_float(yes_bid, None)
    yes_ask = to_float(yes_ask, None)
    no_bid = to_float(no_bid, None)
    no_ask = to_float(no_ask, None)
    return bool(
        (yes_bid is not None and yes_ask is not None and yes_bid == 0.0 and yes_ask >= 0.99)
        or (no_bid is not None and no_ask is not None and no_bid <= 0.01 and no_ask >= 0.99)
    )


def compute_pair_spread(pair_label: str, bid_value: Any, ask_value: Any) -> Tuple[Optional[float], List[str], Optional[str]]:
    pair_label = str(pair_label or "pair").strip().lower() or "pair"
    bid_missing = bid_value is None
    ask_missing = ask_value is None
    missing_fields: List[str] = []
    if bid_missing:
        missing_fields.append(f"{pair_label}_bid")
    if ask_missing:
        missing_fields.append(f"{pair_label}_ask")
    if missing_fields:
        return None, missing_fields, "missing_bid_ask"
    bid_float = to_float(bid_value, None)
    ask_float = to_float(ask_value, None)
    if bid_float is None or ask_float is None:
        return None, [f"{pair_label}_bid", f"{pair_label}_ask"], "non_numeric_bid_ask"
    if bid_float <= 0.0 or ask_float <= 0.0:
        return None, [f"{pair_label}_bid", f"{pair_label}_ask"], "fake_book"
    if ask_float < bid_float:
        return None, [f"{pair_label}_bid", f"{pair_label}_ask"], "inverted_bid_ask"
    return round(max(0.0, ask_float - bid_float), 4), [], None


def compute_topbook_spread(yes_top_price_value: Any, no_top_price_value: Any) -> Tuple[Optional[float], List[str], Optional[str]]:
    if yes_top_price_value is None or no_top_price_value is None:
        missing: List[str] = []
        if yes_top_price_value is None:
            missing.append("yes_top_price")
        if no_top_price_value is None:
            missing.append("no_top_price")
        return None, missing, "missing_topbook_pair"
    yes_top_float = to_float(yes_top_price_value, None)
    no_top_float = to_float(no_top_price_value, None)
    if yes_top_float is None or no_top_float is None:
        return None, ["yes_top_price", "no_top_price"], "non_numeric_topbook_pair"
    if yes_top_float <= 0.0 or no_top_float <= 0.0:
        return None, ["yes_top_price", "no_top_price"], "fake_topbook"
    raw_spread_value = yes_top_float + no_top_float - 1.0
    if raw_spread_value < 0.0:
        return None, ["yes_top_price", "no_top_price"], "inverted_topbook_pair"
    return round(raw_spread_value, 4), [], None


def classify_spread_bucket(*, spread_cents=None, quote_diag=None, orderbook_resolution=None) -> str:
    quote_diag = quote_diag if isinstance(quote_diag, dict) else {}
    orderbook_resolution = orderbook_resolution if isinstance(orderbook_resolution, dict) else {}
    spread_cents_value = to_float(spread_cents, None)
    if spread_cents_value is None:
        fallback_bucket = "unknown"
    elif spread_cents_value <= 1:
        fallback_bucket = "ultra_tight"
    elif spread_cents_value <= 3:
        fallback_bucket = "tight"
    elif spread_cents_value <= 8:
        fallback_bucket = "wide_but_possible"
    else:
        fallback_bucket = "unsafe"
    spread_bucket = str(
        _pick(
            quote_diag.get("spread_bucket"),
            orderbook_resolution.get("spread_bucket"),
            fallback_bucket,
        )
        or "unknown"
    ).strip().lower() or "unknown"
    return spread_bucket


def classify_realm_vv_spread_severity(
    *,
    spread_bucket="unknown",
    edge_after_cost=0.0,
    profile="safe",
    market_personality=None,
    quote_ready=False,
    orderbook_ready=False,
    price_bounds_pass=False,
    liquidity_depth=0.0,
    fresh_enough=True,
    topbook_depth_exists=False,
    selected_side_has_price=False,
    selected_side_has_liquidity=False,
    depth_available=False,
    aggression_settings=None,
):
    profile = normalize_aggression_profile(profile)
    market_personality = market_personality if isinstance(market_personality, dict) else {}
    spread_bucket = str(spread_bucket or "unknown").strip().lower() or "unknown"
    edge_after_cost = max(0.0, to_float(edge_after_cost, 0.0) or 0.0)
    liquidity_depth = max(0.0, to_float(liquidity_depth, 0.0) or 0.0)
    price_bounds_pass = bool(price_bounds_pass)
    quote_ready = bool(quote_ready)
    orderbook_ready = bool(orderbook_ready)
    topbook_depth_exists = bool(topbook_depth_exists)
    selected_side_has_price = bool(selected_side_has_price)
    selected_side_has_liquidity = bool(selected_side_has_liquidity)
    depth_available = bool(depth_available)
    if liquidity_depth <= 0.0 and (topbook_depth_exists or selected_side_has_liquidity or depth_available):
        liquidity_depth = 1.0
    fresh_enough = bool(fresh_enough)
    aggression_settings = aggression_settings if isinstance(aggression_settings, dict) else {}
    threshold = max(0.001, to_float(aggression_settings.get("effective_edge_threshold"), 0.003) or 0.003)
    personality_category = str(market_personality.get("category") or "").strip().upper() or "THIN_BUT_TRADEABLE"
    beast_mode = profile in {"opportunistic", "beast"}
    survivable_aggressive_precheck = bool(
        beast_mode
        and edge_after_cost > 0.0
        and price_bounds_pass
        and quote_ready
        and fresh_enough
        and (orderbook_ready or topbook_depth_exists or selected_side_has_liquidity or depth_available or selected_side_has_price)
    )
    if not price_bounds_pass or not quote_ready or not fresh_enough:
        return {
            "classification": "UNSAFE",
            "reason": "hard_safety_or_staleness",
            "severity": "catastrophic",
            "is_catastrophic": True,
            "is_survivable": False,
            "beast_spread_override_applied": False,
            "final_submit_allowed_by_realm_vv": False,
            "threshold": round(threshold, 4),
        }
    if not orderbook_ready and survivable_aggressive_precheck and spread_bucket in {"unsafe", "wide_but_possible"}:
        if edge_after_cost >= threshold * (0.75 if profile == "beast" else 1.0):
            return {
                "classification": "AGGRESSIVE_BUT_TRADEABLE",
                "reason": "survivable_aggressive_no_orderbook_ready",
                "severity": "survivable",
                "is_catastrophic": False,
                "is_survivable": True,
                "beast_spread_override_applied": bool(profile == "beast"),
                "final_submit_allowed_by_realm_vv": True,
                "threshold": round(threshold, 4),
            }
        return {
            "classification": "CAUTION_WAIT",
            "reason": "survivable_aggressive_no_orderbook_ready",
            "severity": "survivable",
            "is_catastrophic": False,
            "is_survivable": True,
            "beast_spread_override_applied": False,
            "final_submit_allowed_by_realm_vv": False,
            "threshold": round(threshold, 4),
        }
    if spread_bucket in {"ultra_tight", "tight"}:
        return {
            "classification": "SAFE",
            "reason": "tight_or_ultra_tight_book",
            "severity": "survivable",
            "is_catastrophic": False,
            "is_survivable": True,
            "beast_spread_override_applied": False,
            "final_submit_allowed_by_realm_vv": True,
            "threshold": round(threshold, 4),
        }
    if spread_bucket == "wide_but_possible":
        aggressive_edge_floor = threshold * (0.75 if profile == "beast" else 1.0)
        if edge_after_cost >= aggressive_edge_floor and (liquidity_depth > 0.0 or selected_side_has_liquidity or depth_available or topbook_depth_exists or selected_side_has_price):
            return {
                "classification": "AGGRESSIVE_BUT_TRADEABLE",
                "reason": "positive_edge_with_depth",
                "severity": "survivable",
                "is_catastrophic": False,
                "is_survivable": True,
                "beast_spread_override_applied": bool(profile == "beast"),
                "final_submit_allowed_by_realm_vv": True,
                "threshold": round(threshold, 4),
            }
        if beast_mode and edge_after_cost > 0.0 and (liquidity_depth > 0.0 or selected_side_has_liquidity or depth_available or topbook_depth_exists) and personality_category != "DEAD_MARKET":
            return {
                "classification": "CAUTION_WAIT",
                "reason": "beast_mode_near_miss",
                "severity": "survivable",
                "is_catastrophic": False,
                "is_survivable": True,
                "beast_spread_override_applied": False,
                "final_submit_allowed_by_realm_vv": False,
                "threshold": round(threshold, 4),
            }
        if edge_after_cost > 0.0 and personality_category != "DEAD_MARKET":
            return {
                "classification": "CAUTION_WAIT",
                "reason": "edge_near_threshold",
                "severity": "survivable",
                "is_catastrophic": False,
                "is_survivable": True,
                "beast_spread_override_applied": False,
                "final_submit_allowed_by_realm_vv": False,
                "threshold": round(threshold, 4),
            }
        return {
            "classification": "UNSAFE",
            "reason": "wide_spread_no_survivable_edge",
            "severity": "catastrophic",
            "is_catastrophic": True,
            "is_survivable": False,
            "beast_spread_override_applied": False,
            "final_submit_allowed_by_realm_vv": False,
            "threshold": round(threshold, 4),
        }
    if spread_bucket == "unsafe":
        if beast_mode and edge_after_cost >= threshold * (0.75 if profile == "beast" else 1.0) and (liquidity_depth > 0.0 or selected_side_has_liquidity or depth_available or topbook_depth_exists or selected_side_has_price):
            return {
                "classification": "AGGRESSIVE_BUT_TRADEABLE",
                "reason": "beast_mode_wide_spread_allowed",
                "severity": "survivable",
                "is_catastrophic": False,
                "is_survivable": True,
                "beast_spread_override_applied": True,
                "final_submit_allowed_by_realm_vv": True,
                "threshold": round(threshold, 4),
            }
        if beast_mode and edge_after_cost > 0.0 and (liquidity_depth > 0.0 or selected_side_has_liquidity or depth_available or topbook_depth_exists) and personality_category != "DEAD_MARKET":
            return {
                "classification": "CAUTION_WAIT",
                "reason": "beast_mode_wait_for_fill",
                "severity": "survivable",
                "is_catastrophic": False,
                "is_survivable": True,
                "beast_spread_override_applied": False,
                "final_submit_allowed_by_realm_vv": False,
                "threshold": round(threshold, 4),
            }
        return {
            "classification": "UNSAFE",
            "reason": "catastrophic_spread",
            "severity": "catastrophic",
            "is_catastrophic": True,
            "is_survivable": False,
            "beast_spread_override_applied": False,
            "final_submit_allowed_by_realm_vv": False,
            "threshold": round(threshold, 4),
        }
    return {
        "classification": "CAUTION_WAIT" if edge_after_cost > 0.0 else "UNSAFE",
        "reason": "unknown_spread_bucket",
        "severity": "survivable" if edge_after_cost > 0.0 else "catastrophic",
        "is_catastrophic": bool(edge_after_cost <= 0.0),
        "is_survivable": bool(edge_after_cost > 0.0),
        "beast_spread_override_applied": False,
        "final_submit_allowed_by_realm_vv": False,
        "threshold": round(threshold, 4),
    }


def evaluate_orderbook_usability(
    *,
    ticker=None,
    family=None,
    side=None,
    yes_bid=None,
    yes_ask=None,
    no_bid=None,
    no_ask=None,
    spread_cents=None,
    spread_classification=None,
    spread_severity=None,
    quote_ready=False,
    payload_ready=False,
    orderbook_ready=False,
    orderbook_usable=False,
    selected_price=None,
    selected_price_cents=None,
    selected_side_has_price=False,
    selected_side_has_liquidity=False,
    depth_available=False,
    liquidity=None,
    liquidity_depth=None,
    edge_source=None,
    unsafe_price_status=None,
    orderbook_missing_reason=None,
    blocker_reason=None,
    thin_book_override_applied=False,
    relaxed_book_allowed=False,
    profile="safe",
):
    profile = normalize_aggression_profile(profile)
    spread_classification = str(spread_classification or "UNSAFE").strip().upper() or "UNSAFE"
    spread_severity = str(spread_severity or ("survivable" if spread_classification in {"SAFE", "AGGRESSIVE_BUT_TRADEABLE", "CAUTION_WAIT"} else "catastrophic")).strip().lower() or "unknown"
    yes_bid = to_float(yes_bid, None)
    yes_ask = to_float(yes_ask, None)
    no_bid = to_float(no_bid, None)
    no_ask = to_float(no_ask, None)
    selected_price = to_float(selected_price, None)
    selected_price_cents_value = to_float(selected_price_cents, None)
    if selected_price_cents_value is None and selected_price is not None:
        selected_price_cents_value = int(round(float(selected_price) * 100.0))
    if selected_price is None and selected_price_cents_value is not None:
        selected_price = round(float(selected_price_cents_value) / 100.0, 4)
    spread_cents_value = to_float(spread_cents, None)
    yes_pair_valid = bool(yes_bid is not None and yes_ask is not None and yes_bid > 0.0 and yes_ask > 0.0 and yes_ask >= yes_bid)
    no_pair_valid = bool(no_bid is not None and no_ask is not None and no_bid > 0.0 and no_ask > 0.0 and no_ask >= no_bid)
    synthetic_yes = bool(yes_bid is not None and yes_ask is not None and yes_bid == 0.0 and yes_ask >= 0.99)
    synthetic_no = bool(no_bid is not None and no_ask is not None and no_bid <= 0.01 and no_ask >= 0.99)
    boundary_price = bool(
        (selected_price_cents_value is not None and (selected_price_cents_value <= 0 or selected_price_cents_value >= 100))
        or (selected_price is not None and (selected_price <= 0.01 or selected_price >= 0.99))
    )
    catastrophic_spread = bool(
        spread_classification in {"UNSAFE", "UNSAFE_SPREAD"}
        or spread_severity == "catastrophic"
        or (spread_cents_value is not None and spread_cents_value >= 90)
    )
    fake_liquidity = bool(
        to_float(liquidity, None) is not None
        and to_float(liquidity_depth, None) is not None
        and to_float(liquidity, 0.0) <= 0.0
        and to_float(liquidity_depth, 0.0) <= 0.0
    )
    executable_side_present = bool(selected_side_has_price or selected_side_has_liquidity or yes_pair_valid or no_pair_valid)
    coherent_book = bool(yes_pair_valid or no_pair_valid)
    fallback_zero = str(edge_source or "").strip().lower() == "fallback_zero"
    healthy_spread = spread_classification in {"SAFE", "AGGRESSIVE_BUT_TRADEABLE", "CAUTION_WAIT"} and spread_severity != "catastrophic"
    survivable_path = bool(healthy_spread and quote_ready and payload_ready and executable_side_present and coherent_book and not synthetic_yes and not synthetic_no and not boundary_price and not catastrophic_spread and not fake_liquidity and not fallback_zero)
    previously_blocked_healthy = bool(
        survivable_path
        and (not orderbook_ready or not orderbook_usable)
    )
    if thin_book_override_applied:
        survivable_path = True
    allowed = bool(survivable_path)
    final_orderbook_ready = bool(orderbook_ready or allowed)
    final_orderbook_usable = bool(orderbook_usable or allowed)
    if relaxed_book_allowed and spread_classification in {"SAFE", "AGGRESSIVE_BUT_TRADEABLE", "CAUTION_WAIT"} and quote_ready and payload_ready and executable_side_present and coherent_book:
        final_orderbook_ready = True
        final_orderbook_usable = True
        allowed = True
    rejection_reason = None
    if not allowed:
        if boundary_price:
            rejection_reason = "boundary_price"
        elif catastrophic_spread:
            rejection_reason = "catastrophic_spread"
        elif synthetic_yes or synthetic_no:
            rejection_reason = "synthetic_book"
        elif fake_liquidity:
            rejection_reason = "fake_liquidity"
        elif not executable_side_present:
            rejection_reason = "missing_executable_side"
        elif not coherent_book:
            rejection_reason = "invalid_orderbook_structure"
        elif not quote_ready:
            rejection_reason = "quote_not_ready"
        elif not payload_ready:
            rejection_reason = "payload_not_ready"
        elif fallback_zero:
            rejection_reason = "fallback_zero"
        elif spread_classification in {"SAFE", "AGGRESSIVE_BUT_TRADEABLE", "CAUTION_WAIT"}:
            rejection_reason = orderbook_missing_reason or blocker_reason or "orderbook_not_usable"
        else:
            rejection_reason = orderbook_missing_reason or blocker_reason or "orderbook_not_usable"
    reason = "healthy_orderbook_allowed" if allowed else "orderbook_not_usable"
    should_log = bool(
        (not allowed and spread_classification in {"SAFE", "AGGRESSIVE_BUT_TRADEABLE", "CAUTION_WAIT"})
        or previously_blocked_healthy
    )
    return {
        "ticker": ticker,
        "family": family,
        "side": side,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "spread_cents": int(round(spread_cents_value)) if spread_cents_value is not None else None,
        "spread_classification": spread_classification,
        "spread_severity": spread_severity,
        "orderbook_usable": bool(final_orderbook_usable),
        "orderbook_ready": bool(final_orderbook_ready),
        "allowed": bool(allowed),
        "previously_blocked_healthy": bool(previously_blocked_healthy),
        "rejection_reason": rejection_reason,
        "reason": reason,
        "should_log": should_log,
    }


__all__ = [
    "compute_pair_spread",
    "compute_topbook_spread",
    "classify_realm_vv_spread_severity",
    "classify_spread_bucket",
    "coerce_mapping",
    "evaluate_orderbook_usability",
    "has_executable_pair",
    "is_synthetic_book",
    "normalize_aggression_profile",
    "to_float",
]

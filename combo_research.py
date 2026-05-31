"""Telemetry-only sports combo research.

This module never submits orders. It observes sports candidates that already
survived the live selector's BUY path, builds virtual combo tickets, and
records append-only JSONL evidence for later profitability analysis.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

try:
    from runtime.paths import ensure_runtime_layout, record_persist_event, runtime_memory_file
except Exception:  # pragma: no cover - runtime fallback for isolated imports
    ensure_runtime_layout = None
    record_persist_event = None

    def runtime_memory_file(name):
        return str(Path("runtime") / "memory" / str(name))


COMBO_RESEARCH_FILENAME = "aion_combo_research.jsonl"
MAX_CANDIDATE_POOL = 10
MAX_COMBOS_PER_LEG_COUNT = 12
COMBO_THESIS_REJECT_LOG_LIMIT = 12
OBSIDIAN_EXPORT_MIN_SECONDS = 900.0
SPORTS_TOKENS = (
    "SPORT",
    "NBA",
    "WNBA",
    "MLB",
    "NFL",
    "NHL",
    "NCAAB",
    "NCAAF",
    "ATP",
    "WTA",
    "TENNIS",
    "SOCCER",
    "EPL",
    "UCL",
    "MLS",
)
CRYPTO_TOKENS = ("CRYPTO", "BTC", "ETH", "SOL", "DOGE", "XBT", "BITCOIN", "ETHEREUM")

_LAST_OBSIDIAN_EXPORT_TS = 0.0


@dataclass(frozen=True)
class ComboResearchConfig:
    enabled: bool
    sim_stake: float
    max_legs: int
    min_candidates: int
    output_path: Path
    require_distinct_thesis: bool
    allow_smaller_distinct_combos: bool
    max_legs_per_thesis: int


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name, default):
    try:
        value = float(str(os.environ.get(name, default)).strip())
        if math.isfinite(value):
            return value
    except Exception:
        pass
    return float(default)


def _env_int(name, default):
    try:
        return int(float(str(os.environ.get(name, default)).strip()))
    except Exception:
        return int(default)


def _default_output_path():
    try:
        if callable(ensure_runtime_layout):
            ensure_runtime_layout()
    except Exception:
        pass
    try:
        return Path(runtime_memory_file(COMBO_RESEARCH_FILENAME))
    except Exception:
        return Path("runtime") / "memory" / COMBO_RESEARCH_FILENAME


def get_combo_research_config():
    max_legs = max(2, min(5, _env_int("COMBO_RESEARCH_MAX_LEGS", 4)))
    min_candidates = max(2, _env_int("COMBO_RESEARCH_MIN_CANDIDATES", 2))
    sim_stake = _env_float("COMBO_RESEARCH_SIM_STAKE", 1.0)
    if sim_stake <= 0.0:
        sim_stake = 1.0
    return ComboResearchConfig(
        enabled=_env_bool("COMBO_RESEARCH_ENABLED", True),
        sim_stake=sim_stake,
        max_legs=max_legs,
        min_candidates=min_candidates,
        output_path=_default_output_path(),
        require_distinct_thesis=_env_bool("AION_COMBO_REQUIRE_DISTINCT_THESIS", True),
        allow_smaller_distinct_combos=_env_bool("AION_COMBO_ALLOW_SMALLER_DISTINCT_COMBOS", True),
        max_legs_per_thesis=max(1, _env_int("AION_COMBO_MAX_LEGS_PER_THESIS", 1)),
    )


def _emit(tag, payload):
    try:
        print(f"[{tag}] " + json.dumps(payload, sort_keys=True, default=str))
    except Exception:
        try:
            print(f"[{tag}]")
        except Exception:
            pass


def _to_float(value, default=None):
    try:
        if value is None or isinstance(value, bool):
            return default
        if isinstance(value, str) and not value.strip():
            return default
        parsed = float(value)
        if math.isfinite(parsed):
            return parsed
    except Exception:
        pass
    return default


def _as_probability(value):
    value = _to_float(value, None)
    if value is None:
        return None
    if value > 1.0 and value <= 100.0:
        value /= 100.0
    if value < 0.0 or value > 1.0:
        return None
    return value


def _as_price(value):
    value = _to_float(value, None)
    if value is None:
        return None
    if value > 1.0 and value <= 100.0:
        value /= 100.0
    if value <= 0.0 or value >= 1.0:
        return None
    return value


def _as_edge(value):
    value = _to_float(value, None)
    if value is None:
        return None
    if abs(value) > 1.0 and abs(value) <= 100.0:
        value /= 100.0
    if not math.isfinite(value):
        return None
    return value


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _first_value(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _normalize_side(value):
    text = str(value or "").strip().upper()
    if text in {"Y", "YES", "BUY_YES"} or text.endswith("_YES"):
        return "YES"
    if text in {"N", "NO", "BUY_NO"} or text.endswith("_NO"):
        return "NO"
    if "YES" in text:
        return "YES"
    if "NO" in text:
        return "NO"
    return None


def _ticker_from_market(market):
    if not isinstance(market, dict):
        return ""
    return str(
        market.get("ticker")
        or market.get("market_ticker")
        or market.get("id")
        or ""
    ).strip()


def _market_lookup(markets):
    lookup = {}
    for market in markets or []:
        ticker = _ticker_from_market(market)
        if ticker:
            lookup[ticker] = market
    return lookup


def _event_or_market_key(ticker, market):
    # Duplicate protection is market-level; same-event correlation is research data, not a duplicate market.
    if isinstance(market, dict):
        for key in (
            "ticker",
            "market_ticker",
            "id",
            "market_slug",
        ):
            value = str(market.get(key) or "").strip()
            if value:
                return value
    return str(ticker or "").strip()


def _candidate_tuple_to_dict(item):
    if isinstance(item, dict):
        return item
    if isinstance(item, (tuple, list)) and len(item) >= 4:
        score, q, spread, family = item[:4]
        return {
            "q": q,
            "score": score,
            "spread": spread,
            "family": family,
            "ticker": _get(q, "ticker"),
        }
    return {}


def _readiness_lookup(records):
    lookup = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        ticker = str(record.get("ticker") or "").strip()
        if ticker and ticker not in lookup:
            lookup[ticker] = record
    return lookup


def _text_blob(*values):
    return " ".join(str(value or "") for value in values).upper()


def _compact_thesis_text(value):
    text = str(value or "").upper()
    text = re.sub(r"\b([A-Z]{2,18})\d{1,4}(?:\.\d+)?\b", r"\1#", text)
    text = re.sub(r"\b\d{1,4}(?:\.\d+)?\+?\b", "#", text)
    text = re.sub(r"(?<=[A-Z])#(?=[A-Z])", "#_", text)
    text = re.sub(r"[^A-Z0-9#]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:140] or "UNKNOWN"


def _candidate_meta_value(item, q, market, expectancy_summary, readiness, *keys):
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    containers = (
        allocation,
        item if isinstance(item, dict) else {},
        expectancy_summary if isinstance(expectancy_summary, dict) else {},
        readiness if isinstance(readiness, dict) else {},
        market if isinstance(market, dict) else {},
    )
    for key in keys:
        for container in containers:
            value = container.get(key)
            if value not in (None, ""):
                return value
        value = _get(q, key)
        if value not in (None, ""):
            return value
    return None


def _ticker_event_fallback(ticker):
    ticker = str(ticker or "").strip().upper()
    parts = [part for part in ticker.split("-") if part]
    if len(parts) >= 2:
        return "-".join(parts[:2])
    return ticker


def _derive_combo_thesis_key(item, q, market, family, side, ticker, expectancy_summary, readiness):
    game_context = {}
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    if isinstance(allocation.get("game_thesis_context"), dict):
        game_context = allocation.get("game_thesis_context") or {}
    event_key = _first_value(
        _candidate_meta_value(item, q, market, expectancy_summary, readiness, "game_id", "event_id", "event_ticker"),
        game_context.get("game_id"),
        _ticker_event_fallback(ticker),
    )
    sport = _first_value(
        _candidate_meta_value(item, q, market, expectancy_summary, readiness, "sport", "league", "category"),
        family,
        "SPORTS",
    )
    market_type = _first_value(
        _candidate_meta_value(
            item,
            q,
            market,
            expectancy_summary,
            readiness,
            "market_type",
            "stat_family",
            "prop_type",
            "leg_type",
            "setup_type",
        ),
        family,
    )
    explicit_subject = _first_value(
        _candidate_meta_value(item, q, market, expectancy_summary, readiness, "player_name", "player", "participant_name", "athlete"),
        _candidate_meta_value(item, q, market, expectancy_summary, readiness, "team_name", "team", "side_name", "participant"),
    )
    market_text = _text_blob(
        _candidate_meta_value(item, q, market, expectancy_summary, readiness, "title", "market_title", "name"),
        _candidate_meta_value(item, q, market, expectancy_summary, readiness, "subtitle", "event_title"),
        ticker,
    )
    subject_text = explicit_subject or market_text or ticker
    return "|".join(
        [
            _compact_thesis_text(sport),
            _compact_thesis_text(event_key),
            _compact_thesis_text(family or "UNKNOWN"),
            _compact_thesis_text(market_type or family or "UNKNOWN"),
            _compact_thesis_text(side or "ANY"),
            _compact_thesis_text(subject_text),
        ]
    )


def _is_crypto_candidate(bot, ticker, family, market, q):
    try:
        checker = getattr(bot, "_is_crypto_execution_candidate", None)
        if callable(checker) and checker(family=family, ticker=ticker, market=market, q=q):
            return True
    except Exception:
        pass
    blob = _text_blob(
        ticker,
        family,
        market.get("title") if isinstance(market, dict) else None,
        market.get("subtitle") if isinstance(market, dict) else None,
    )
    return any(token in blob for token in CRYPTO_TOKENS)


def _is_sports_candidate(bot, item, ticker, family, market):
    lane = str(item.get("lane") or "").strip().upper()
    if lane == "SPORTS":
        return True
    try:
        checker = getattr(bot, "_is_sports_market_or_family", None)
        if callable(checker) and checker(family):
            return True
    except Exception:
        pass
    blob = _text_blob(
        lane,
        family,
        ticker,
        market.get("category") if isinstance(market, dict) else None,
        market.get("title") if isinstance(market, dict) else None,
    )
    return any(token in blob for token in SPORTS_TOKENS)


def _selected_side(item, q, expectancy_summary, readiness):
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    return _normalize_side(
        _first_value(
            allocation.get("selected_side"),
            allocation.get("side"),
            expectancy_summary.get("selected_side") if isinstance(expectancy_summary, dict) else None,
            expectancy_summary.get("side") if isinstance(expectancy_summary, dict) else None,
            readiness.get("side") if isinstance(readiness, dict) else None,
            _get(q, "selected_side"),
            _get(q, "side"),
        )
    )


def _side_field_snapshot(item, q, expectancy_summary, readiness):
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    return {
        "item_selected_side": item.get("selected_side"),
        "item_side": item.get("side"),
        "item_preferred_side": item.get("preferred_side"),
        "allocation_selected_side": allocation.get("selected_side"),
        "allocation_side": allocation.get("side"),
        "allocation_preferred_side": allocation.get("preferred_side"),
        "expectancy_selected_side": expectancy_summary.get("selected_side") if isinstance(expectancy_summary, dict) else None,
        "expectancy_side": expectancy_summary.get("side") if isinstance(expectancy_summary, dict) else None,
        "readiness_side": readiness.get("side") if isinstance(readiness, dict) else None,
        "q_selected_side": _get(q, "selected_side"),
        "q_side": _get(q, "side"),
    }


def _side_fields_present(side_fields):
    return sorted([key for key, value in (side_fields or {}).items() if value not in (None, "")])


def _quote_snapshot(item, q, market, expectancy_summary, readiness):
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}

    def _quote_value(*keys):
        for key in keys:
            value = None
            for container in (allocation, expectancy_summary, readiness, market):
                if isinstance(container, dict):
                    value = container.get(key)
                    if value not in (None, ""):
                        return value
            value = _get(q, key)
            if value not in (None, ""):
                return value
        return None

    return {
        "selected_price": _as_price(
            _first_value(
                _quote_value("selected_price"),
                _quote_value("selected_price_dollars"),
                _quote_value("price"),
            )
        ),
        "yes_bid": _as_price(_quote_value("yes_bid", "yes_bid_dollars")),
        "yes_ask": _as_price(_quote_value("yes_ask", "yes_ask_dollars")),
        "no_bid": _as_price(_quote_value("no_bid", "no_bid_dollars")),
        "no_ask": _as_price(_quote_value("no_ask", "no_ask_dollars")),
        "yes_top_size": _to_float(_quote_value("yes_top_size", "yes_ask_size"), None),
        "no_top_size": _to_float(_quote_value("no_top_size", "no_ask_size"), None),
        "spread": _to_float(_first_value(item.get("spread"), _quote_value("spread")), None),
    }


def _safe_price_match_side(quote_snapshot):
    selected_price = quote_snapshot.get("selected_price")
    if selected_price is None:
        return None, None
    matches = []
    for side, key in (("YES", "yes_ask"), ("NO", "no_ask")):
        side_price = quote_snapshot.get(key)
        if side_price is None:
            continue
        if abs(float(selected_price) - float(side_price)) <= 0.0001:
            matches.append((side, side_price))
    if len(matches) == 1:
        return matches[0]
    return None, None


def _derive_side_from_canonical_selector(bot, item, q, market, family, expectancy_summary, readiness):
    selector = getattr(bot, "_aion_select_bidirectional_side", None)
    if not callable(selector):
        return None, None, {"reason": "canonical_selector_unavailable"}
    side_fields = _side_field_snapshot(item, q, expectancy_summary, readiness)
    preferred_side = _normalize_side(_first_value(*side_fields.values()))
    quote_diag = {}
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    for container in (allocation, item, expectancy_summary, readiness):
        if isinstance(container, dict):
            for key in (
                "selected_price",
                "selected_price_dollars",
                "price",
                "estimated_win_probability",
                "probability",
                "calibrated_probability",
                "model_probability",
                "edge_after_cost",
                "sports_edge_after_cost",
                "edge",
                "yes_edge_after_cost",
                "no_edge_after_cost",
                "yes_probability",
                "no_probability",
                "yes_bid",
                "yes_ask",
                "no_bid",
                "no_ask",
                "yes_top_size",
                "no_top_size",
                "liquidity",
                "liquidity_depth",
            ):
                if key not in quote_diag and container.get(key) not in (None, ""):
                    quote_diag[key] = container.get(key)
    try:
        selection = selector(
            ticker=item.get("ticker") or _get(q, "ticker"),
            family=family,
            lane=item.get("lane") or "SPORTS",
            market=market,
            q=q,
            quote_diag=quote_diag,
            preferred_side=preferred_side.lower() if preferred_side else None,
            source="combo_research_admission",
            emit_log=False,
        ) or {}
    except Exception as exc:
        return None, None, {"reason": "canonical_selector_exception", "error": exc.__class__.__name__}
    side = _normalize_side(selection.get("selected_side"))
    price = _as_price(selection.get("selected_price"))
    if side in {"YES", "NO"} and price is not None:
        return side, price, {
            "reason": selection.get("reason") or "canonical_selector",
            "selected_side": side,
            "selected_price": price,
            "selected_price_source": selection.get("selected_price_source"),
            "yes_ask": selection.get("yes_ask"),
            "no_ask": selection.get("no_ask"),
            "yes_top_size": selection.get("yes_top_size"),
            "no_top_size": selection.get("no_top_size"),
        }
    return None, None, {
        "reason": selection.get("reason") or "canonical_selector_no_side",
        "selected_side": selection.get("selected_side"),
        "selected_price": selection.get("selected_price"),
    }


def _emit_candidate_reject(*, ticker, family, reject_reason, side_fields, quote_snapshot, source_function, source, selector_trace=None):
    _emit(
        "AION-COMBO-CANDIDATE-REJECT",
        {
            "ticker": ticker,
            "family": family,
            "reject_reason": reject_reason,
            "source": source,
            "source_function": source_function,
            "condition": "selected_side_missing" if reject_reason == "missing_side" else reject_reason,
            "side_fields_present": _side_fields_present(side_fields),
            "side_fields": {key: value for key, value in (side_fields or {}).items() if value not in (None, "")},
            "orderbook": {key: value for key, value in (quote_snapshot or {}).items() if value is not None},
            "selector_trace": {key: value for key, value in (selector_trace or {}).items() if value not in (None, "")},
        },
    )


def _selected_price(side, item, q, expectancy_summary, readiness):
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    price = _as_price(
        _first_value(
            allocation.get("selected_price"),
            allocation.get("price"),
            allocation.get("ev_selected_price"),
            expectancy_summary.get("selected_price") if isinstance(expectancy_summary, dict) else None,
            expectancy_summary.get("ev_selected_price") if isinstance(expectancy_summary, dict) else None,
            expectancy_summary.get("selected_price_dollars") if isinstance(expectancy_summary, dict) else None,
            readiness.get("selected_price") if isinstance(readiness, dict) else None,
            _get(q, "selected_price"),
            _get(q, "price"),
        )
    )
    if price is not None:
        return price
    if side == "YES":
        return _as_price(_first_value(_get(q, "yes_ask"), _get(q, "ask"), _get(q, "yes_price")))
    if side == "NO":
        return _as_price(_first_value(_get(q, "no_ask"), _get(q, "ask"), _get(q, "no_price")))
    return None


def _candidate_edge(item, q, expectancy_summary, readiness):
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    values = (
        allocation.get("edge"),
        allocation.get("edge_after_cost"),
        allocation.get("EV"),
        expectancy_summary.get("edge_after_cost") if isinstance(expectancy_summary, dict) else None,
        expectancy_summary.get("sports_edge_after_cost") if isinstance(expectancy_summary, dict) else None,
        expectancy_summary.get("ev_edge") if isinstance(expectancy_summary, dict) else None,
        readiness.get("sports_edge_after_cost") if isinstance(readiness, dict) else None,
        _get(q, "edge_after_cost"),
        _get(q, "ev_edge"),
    )
    for value in values:
        edge = _as_edge(value)
        if edge is not None:
            return edge
    return None


def _candidate_probability(item, q, expectancy_summary, readiness):
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    values = (
        allocation.get("estimated_win_probability"),
        allocation.get("probability"),
        expectancy_summary.get("estimated_win_probability") if isinstance(expectancy_summary, dict) else None,
        expectancy_summary.get("probability") if isinstance(expectancy_summary, dict) else None,
        expectancy_summary.get("calibrated_probability") if isinstance(expectancy_summary, dict) else None,
        expectancy_summary.get("sports_ml_final_probability") if isinstance(expectancy_summary, dict) else None,
        readiness.get("estimated_win_probability") if isinstance(readiness, dict) else None,
        _get(q, "estimated_win_probability"),
        _get(q, "probability"),
    )
    for value in values:
        probability = _as_probability(value)
        if probability is not None:
            return probability
    return None


def _candidate_confidence(item, q, expectancy_summary, readiness):
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    values = (
        allocation.get("confidence"),
        allocation.get("probability_confidence"),
        expectancy_summary.get("probability_confidence") if isinstance(expectancy_summary, dict) else None,
        expectancy_summary.get("confidence") if isinstance(expectancy_summary, dict) else None,
        readiness.get("probability_confidence") if isinstance(readiness, dict) else None,
        readiness.get("confidence") if isinstance(readiness, dict) else None,
        _get(q, "probability_confidence"),
        _get(q, "confidence"),
    )
    for value in values:
        confidence = _as_probability(value)
        if confidence is not None:
            return confidence
    return None


def _score_norm(value):
    value = _to_float(value, None)
    if value is None:
        return 0.0
    if value > 1.0 and value <= 100.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


def _edge_norm(edge):
    if edge is None:
        return 0.5
    return max(0.0, min(1.0, 0.5 + float(edge) * 8.0))


def _side_top_size(side, quote_snapshot, item, q, readiness):
    side = _normalize_side(side)
    allocation = item.get("allocation") if isinstance(item.get("allocation"), dict) else {}
    values = [
        allocation.get("top_size"),
        item.get("top_size"),
        readiness.get("top_size") if isinstance(readiness, dict) else None,
    ]
    if side == "YES":
        values.extend([quote_snapshot.get("yes_top_size"), _get(q, "yes_top_size"), _get(q, "yes_ask_size")])
    elif side == "NO":
        values.extend([quote_snapshot.get("no_top_size"), _get(q, "no_top_size"), _get(q, "no_ask_size")])
    values.extend([quote_snapshot.get("yes_top_size"), quote_snapshot.get("no_top_size")])
    best = 0.0
    for value in values:
        number = _to_float(value, None)
        if number is not None:
            best = max(best, float(number))
    return best


def _combo_memory_adjustment(item, q, expectancy_summary, readiness):
    total = 0.0
    for field in (
        "sports_memory_rank_adjustment",
        "family_profit_memory_adjustment",
        "sports_regime_rank_adjustment",
        "sports_graph_rank_adjustment",
        "memory_rank_adjustment",
        "family_rank_adjustment",
    ):
        value = _candidate_meta_value(item, q, {}, expectancy_summary, readiness, field)
        total += _to_float(value, 0.0) or 0.0
    return round(max(-10.0, min(14.0, total)), 6)


def _thesis_representative_score(leg):
    quality = float(leg.get("quality_rank") or 0.0)
    edge = _as_edge(leg.get("edge"))
    confidence = _as_probability(leg.get("confidence"))
    candidate_score = _score_norm(leg.get("candidate_score"))
    execution_quality = _score_norm(leg.get("execution_quality_score"))
    fill_probability = _as_probability(leg.get("fill_probability"))
    spread = _to_float(leg.get("spread"), None)
    liquidity = _to_float(_first_value(leg.get("liquidity"), leg.get("volume")), 0.0) or 0.0
    top_size = _to_float(leg.get("top_size"), 0.0) or 0.0
    memory = _to_float(leg.get("memory_rank_adjustment"), 0.0) or 0.0
    edge_component = 0.0 if edge is None else max(-16.0, min(34.0, edge * 280.0))
    confidence_component = (confidence if confidence is not None else 0.5) * 12.0
    fill_component = (fill_probability if fill_probability is not None else 0.5) * 6.0
    execution_component = execution_quality * 7.0
    spread_component = 0.0
    if spread is not None:
        if spread <= 0.02:
            spread_component = 7.0
        elif spread <= 0.05:
            spread_component = 4.5
        elif spread <= 0.08:
            spread_component = 2.0
        elif spread > 0.12:
            spread_component = -5.0
    liquidity_component = min(7.0, math.log1p(max(liquidity, 0.0)) * 1.0)
    top_size_component = min(4.0, math.log1p(max(top_size, 0.0)) * 1.1)
    score = (
        quality * 55.0
        + candidate_score * 18.0
        + edge_component
        + confidence_component
        + fill_component
        + execution_component
        + spread_component
        + liquidity_component
        + top_size_component
        + memory
    )
    return round(max(0.0, min(140.0, score)), 6)


def _apply_combo_thesis_diversity(pool, config, source):
    pool = [dict(leg) for leg in (pool or []) if isinstance(leg, dict)]
    if not pool:
        return [], {
            "enabled": bool(config.require_distinct_thesis),
            "thesis_count": 0,
            "representative_count": 0,
            "rejected_same_thesis_count": 0,
        }
    for leg in pool:
        if not leg.get("thesis_key"):
            leg["thesis_key"] = _compact_thesis_text(leg.get("ticker"))
        leg["thesis_representative_score"] = _thesis_representative_score(leg)
    if not config.require_distinct_thesis:
        return pool, {
            "enabled": False,
            "thesis_count": len({leg.get("thesis_key") for leg in pool}),
            "representative_count": len(pool),
            "rejected_same_thesis_count": 0,
        }

    max_per_thesis = max(1, int(config.max_legs_per_thesis or 1))
    grouped = defaultdict(list)
    for leg in pool:
        grouped[str(leg.get("thesis_key") or "UNKNOWN")].append(leg)

    representatives = []
    rejected = []
    reject_logs = 0
    for thesis_key, legs in grouped.items():
        ranked = sorted(
            legs,
            key=lambda leg: (
                float(leg.get("thesis_representative_score") or 0.0),
                float(leg.get("quality_rank") or 0.0),
                float(leg.get("edge") if leg.get("edge") is not None else -1.0),
            ),
            reverse=True,
        )
        kept = ranked[:max_per_thesis]
        weaker = ranked[max_per_thesis:]
        representatives.extend(kept)
        rejected.extend(weaker)
        if len(ranked) > 1:
            _emit(
                "AION-COMBO-THESIS-GROUP",
                {
                    "thesis_key": thesis_key[:180],
                    "group_size": len(ranked),
                    "representative_ticker": kept[0].get("ticker") if kept else None,
                    "representative_score": kept[0].get("thesis_representative_score") if kept else None,
                    "rejected_same_thesis_count": len(weaker),
                },
            )
            representative_ticker = kept[0].get("ticker") if kept else None
            for leg in weaker:
                if reject_logs >= COMBO_THESIS_REJECT_LOG_LIMIT:
                    break
                reject_logs += 1
                _emit(
                    "AION-COMBO-THESIS-REJECT",
                    {
                        "ticker": leg.get("ticker"),
                        "thesis_key": thesis_key[:180],
                        "reason": "duplicate_thesis_weaker_rung",
                        "representative_ticker": representative_ticker,
                    },
                )

    representatives.sort(
        key=lambda leg: (
            float(leg.get("thesis_representative_score") or 0.0),
            float(leg.get("quality_rank") or 0.0),
            float(leg.get("edge") if leg.get("edge") is not None else -1.0),
        ),
        reverse=True,
    )
    return representatives[:MAX_CANDIDATE_POOL], {
        "enabled": True,
        "source": source,
        "thesis_count": len(grouped),
        "representative_count": len(representatives),
        "rejected_same_thesis_count": len(rejected),
        "max_legs_per_thesis": max_per_thesis,
    }


def _combo_thesis_is_clean(legs, config):
    if not config.require_distinct_thesis:
        return True
    thesis_counts = Counter(str(leg.get("thesis_key") or leg.get("ticker") or "") for leg in legs)
    return bool(thesis_counts and max(thesis_counts.values()) <= max(1, int(config.max_legs_per_thesis or 1)))


def _normalize_candidate_pool(bot, candidates, trade_readiness_records, markets, expectancy_summary_by_ticker=None, source=None):
    readiness_by_ticker = _readiness_lookup(trade_readiness_records)
    markets_by_ticker = _market_lookup(markets)
    expectancy_by_ticker = expectancy_summary_by_ticker if isinstance(expectancy_summary_by_ticker, dict) else {}
    normalized = []
    reject_reasons = Counter()
    seen = set()

    for raw_item in candidates or []:
        item = _candidate_tuple_to_dict(raw_item)
        if not item:
            reject_reasons["malformed_candidate"] += 1
            continue
        q = item.get("q")
        ticker = str(item.get("ticker") or _get(q, "ticker") or "").strip()
        if not ticker:
            reject_reasons["missing_ticker"] += 1
            continue
        if ticker in seen:
            reject_reasons["duplicate_ticker"] += 1
            continue
        seen.add(ticker)
        market = item.get("market") if isinstance(item.get("market"), dict) else markets_by_ticker.get(ticker, {})
        family = str(item.get("family") or (market.get("family") if isinstance(market, dict) else "") or "").strip().upper()
        readiness = readiness_by_ticker.get(ticker, {})
        if _is_crypto_candidate(bot, ticker, family, market, q):
            reject_reasons["crypto_excluded"] += 1
            continue
        if not _is_sports_candidate(bot, item, ticker, family, market):
            reject_reasons["non_sports_excluded"] += 1
            continue

        expectancy_summary = item.get("expectancy_summary")
        if not isinstance(expectancy_summary, dict) or not expectancy_summary:
            expectancy_summary = expectancy_by_ticker.get(ticker) if isinstance(expectancy_by_ticker.get(ticker), dict) else {}
        if not isinstance(expectancy_summary, dict) or not expectancy_summary:
            expectancy_item = (item.get("expectancy_item") if isinstance(item.get("expectancy_item"), dict) else {})
            expectancy_summary = expectancy_item.get("expectancy_summary") if isinstance(expectancy_item.get("expectancy_summary"), dict) else {}
        side_fields = _side_field_snapshot(item, q, expectancy_summary, readiness)
        quote_snapshot = _quote_snapshot(item, q, market, expectancy_summary, readiness)
        side = _selected_side(item, q, expectancy_summary, readiness)
        side_source = "explicit"
        selector_trace = {}
        derived_price = None
        if side not in {"YES", "NO"}:
            side, derived_price, selector_trace = _derive_side_from_canonical_selector(
                bot,
                item,
                q,
                market,
                family,
                expectancy_summary,
                readiness,
            )
            if side in {"YES", "NO"}:
                side_source = "canonical_side_selector"
            else:
                side, derived_price = _safe_price_match_side(quote_snapshot)
                if side in {"YES", "NO"}:
                    side_source = "selected_price_unique_quote_match"
        price = _selected_price(side, item, q, expectancy_summary, readiness)
        if price is None and derived_price is not None:
            price = derived_price
        if side not in {"YES", "NO"}:
            reject_reasons["missing_side"] += 1
            _emit_candidate_reject(
                ticker=ticker,
                family=family or "UNKNOWN",
                reject_reason="missing_side",
                side_fields=side_fields,
                quote_snapshot=quote_snapshot,
                source_function="combo_research._normalize_candidate_pool",
                source=source,
                selector_trace=selector_trace,
            )
            continue
        if price is None:
            reject_reasons["missing_price"] += 1
            _emit_candidate_reject(
                ticker=ticker,
                family=family or "UNKNOWN",
                reject_reason="missing_price",
                side_fields=side_fields,
                quote_snapshot=quote_snapshot,
                source_function="combo_research._normalize_candidate_pool",
                source=source,
                selector_trace=selector_trace,
            )
            continue

        probability = _candidate_probability(item, q, expectancy_summary, readiness)
        confidence = _candidate_confidence(item, q, expectancy_summary, readiness)
        edge = _candidate_edge(item, q, expectancy_summary, readiness)
        candidate_score = _first_value(item.get("candidate_score"), item.get("score"), readiness.get("candidate_score"))
        execution_quality_score = _first_value(
            _candidate_meta_value(
                item,
                q,
                market,
                expectancy_summary,
                readiness,
                "sports_execution_quality_score",
                "execution_quality_score",
                "quality_score",
            ),
            candidate_score,
        )
        fill_probability = _as_probability(
            _candidate_meta_value(
                item,
                q,
                market,
                expectancy_summary,
                readiness,
                "sports_execution_fill_probability",
                "fill_probability",
                "fill_prob",
            )
        )
        top_size = _side_top_size(side, quote_snapshot, item, q, readiness)
        thesis_key = _derive_combo_thesis_key(item, q, market, family or "UNKNOWN", side, ticker, expectancy_summary, readiness)
        spread = _to_float(_first_value(item.get("spread"), quote_snapshot.get("spread"), readiness.get("spread")), None)
        liquidity = _to_float(_first_value(item.get("liquidity"), _get(q, "liquidity"), readiness.get("liquidity")), None)
        score_component = _score_norm(candidate_score)
        confidence_component = confidence if confidence is not None else 0.5
        edge_component = _edge_norm(edge)
        quality_rank = round((score_component * 0.38) + (confidence_component * 0.34) + (edge_component * 0.28), 6)

        normalized.append(
            {
                "ticker": ticker,
                "market": ticker,
                "market_key": _event_or_market_key(ticker, market),
                "family": family or "UNKNOWN",
                "thesis_key": thesis_key,
                "side": side,
                "side_source": side_source,
                "side_selection_reason": selector_trace.get("reason"),
                "selected_price_source": selector_trace.get("selected_price_source"),
                "price": round(price, 6),
                "probability": round(probability, 6) if probability is not None else None,
                "confidence": round(confidence, 6) if confidence is not None else None,
                "edge": round(edge, 6) if edge is not None else None,
                "candidate_score": round(_to_float(candidate_score, 0.0), 6),
                "execution_quality_score": round(_to_float(execution_quality_score, 0.0), 6),
                "fill_probability": round(fill_probability, 6) if fill_probability is not None else None,
                "quality_rank": quality_rank,
                "spread": spread,
                "liquidity": liquidity,
                "volume": _to_float(_first_value(item.get("volume"), _get(q, "volume"), readiness.get("volume")), None),
                "top_size": round(top_size, 6),
                "memory_rank_adjustment": _combo_memory_adjustment(item, q, expectancy_summary, readiness),
                "decision_reason": item.get("decision_reason") or readiness.get("decision_reason"),
            }
        )

    normalized.sort(
        key=lambda leg: (
            leg.get("quality_rank") or 0.0,
            leg.get("edge") if leg.get("edge") is not None else -1.0,
            leg.get("confidence") if leg.get("confidence") is not None else 0.0,
            leg.get("candidate_score") or 0.0,
        ),
        reverse=True,
    )
    return normalized[:MAX_CANDIDATE_POOL], reject_reasons


def _combo_key(legs):
    return "|".join(sorted(f"{leg.get('ticker')}:{leg.get('side')}" for leg in legs))


def _open_combo_keys(records):
    built = {}
    resolved = set()
    for record in records:
        event_type = record.get("event_type")
        combo_id = record.get("combo_id")
        if not combo_id:
            continue
        if event_type == "combo_built":
            built[combo_id] = record
        elif event_type == "combo_result":
            resolved.add(combo_id)
    return {
        str(record.get("combo_key") or "")
        for combo_id, record in built.items()
        if combo_id not in resolved and str(record.get("combo_key") or "")
    }


def _combo_is_clean(legs):
    tickers = {leg.get("ticker") for leg in legs}
    market_keys = {leg.get("market_key") for leg in legs}
    if len(tickers) != len(legs) or len(market_keys) != len(legs):
        return False

    confidences = [float(leg["confidence"]) for leg in legs if leg.get("confidence") is not None]
    edges = [float(leg["edge"]) for leg in legs if leg.get("edge") is not None]
    leg_count = len(legs)
    if confidences and min(confidences) < 0.45:
        return False
    if edges and min(edges) < -0.015:
        return False
    if leg_count >= 3 and confidences and (sum(confidences) / len(confidences)) < 0.52:
        return False
    if leg_count >= 4 and confidences and (sum(confidences) / len(confidences)) < 0.55:
        return False
    if leg_count >= 4 and edges and min(edges) < 0.0:
        return False
    return True


def _combo_score(legs):
    avg_quality = sum(float(leg.get("quality_rank") or 0.0) for leg in legs) / max(len(legs), 1)
    avg_confidence = sum(float(leg.get("confidence") if leg.get("confidence") is not None else 0.5) for leg in legs) / max(len(legs), 1)
    avg_edge = sum(float(leg.get("edge") if leg.get("edge") is not None else 0.0) for leg in legs) / max(len(legs), 1)
    return round(avg_quality + (avg_confidence * 0.25) + (avg_edge * 4.0), 6)


def _build_combo_record(legs, leg_count, config, timestamp, source, diversity_mode):
    combo_key = _combo_key(legs)
    combo_id = hashlib.sha1(f"{timestamp}|{combo_key}".encode("utf-8")).hexdigest()[:16]
    price_product = 1.0
    estimated_probability_product = 1.0
    has_estimated_probability = True
    for leg in legs:
        price_product *= max(float(leg.get("price") or 0.0), 0.0001)
        if leg.get("probability") is None:
            has_estimated_probability = False
        else:
            estimated_probability_product *= max(min(float(leg.get("probability")), 0.9999), 0.0001)
    estimated_payout = config.sim_stake / max(price_product, 0.0001)
    avg_edge = sum(float(leg.get("edge") if leg.get("edge") is not None else 0.0) for leg in legs) / max(len(legs), 1)
    avg_confidence = sum(float(leg.get("confidence") if leg.get("confidence") is not None else 0.5) for leg in legs) / max(len(legs), 1)
    return {
        "event_type": "combo_built",
        "timestamp": timestamp,
        "source": source,
        "combo_id": combo_id,
        "combo_key": combo_key,
        "leg_count": leg_count,
        "thesis_count": len({leg.get("thesis_key") for leg in legs}),
        "thesis_keys": sorted({str(leg.get("thesis_key") or "")[:180] for leg in legs}),
        "diversity_mode": diversity_mode,
        "legs": legs,
        "implied_combo_probability": round(estimated_probability_product, 8) if has_estimated_probability else None,
        "market_implied_combo_probability": round(price_product, 8),
        "simulated_stake": round(config.sim_stake, 4),
        "estimated_payout": round(estimated_payout, 6),
        "estimated_profit_if_win": round(estimated_payout - config.sim_stake, 6),
        "average_edge": round(avg_edge, 6),
        "average_confidence": round(avg_confidence, 6),
        "combo_score": _combo_score(legs),
        "result_status": "pending",
        "eventual_result": None,
        "win": None,
        "roi": None,
    }


def _build_virtual_combos(pool, config, timestamp, source, existing_open_keys):
    records = []
    max_legs = min(config.max_legs, 5 if len(pool) >= 5 else 4, len(pool))
    diversity_mode = "distinct_thesis" if config.require_distinct_thesis else "raw_candidate_pool"
    for leg_count in range(2, max_legs + 1):
        if leg_count == 5 and len(pool) < 5:
            continue
        scored = []
        for legs_tuple in combinations(pool, leg_count):
            legs = [dict(leg) for leg in legs_tuple]
            if not _combo_is_clean(legs):
                continue
            if not _combo_thesis_is_clean(legs, config):
                continue
            combo_key = _combo_key(legs)
            if combo_key in existing_open_keys:
                continue
            scored.append((_combo_score(legs), legs))
        scored.sort(key=lambda item: item[0], reverse=True)
        for _score, legs in scored[:MAX_COMBOS_PER_LEG_COUNT]:
            records.append(_build_combo_record(legs, leg_count, config, timestamp, source, diversity_mode))
    return records


def _append_jsonl(path, record):
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        try:
            if callable(record_persist_event):
                record_persist_event(
                    subsystem="combo_research",
                    path=str(path),
                    action="append_jsonl",
                    success=True,
                    cooldown_seconds=600.0,
                )
        except Exception:
            pass
        return True
    except Exception as exc:
        try:
            if callable(record_persist_event):
                record_persist_event(
                    subsystem="combo_research",
                    path=str(path),
                    action="append_jsonl",
                    success=False,
                    reason=exc.__class__.__name__,
                    cooldown_seconds=60.0,
                )
        except Exception:
            pass
        return False


def _ensure_jsonl_file(path):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        try:
            if callable(record_persist_event):
                record_persist_event(
                    subsystem="combo_research",
                    path=str(path),
                    action="ensure_jsonl",
                    success=True,
                    cooldown_seconds=600.0,
                )
        except Exception:
            pass
        return True
    except Exception as exc:
        try:
            if callable(record_persist_event):
                record_persist_event(
                    subsystem="combo_research",
                    path=str(path),
                    action="ensure_jsonl",
                    success=False,
                    reason=exc.__class__.__name__,
                    cooldown_seconds=60.0,
                )
        except Exception:
            pass
        return False


def _read_jsonl(path, max_records=20000):
    records = []
    try:
        path = Path(path)
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()[-max_records:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if isinstance(record, dict):
                records.append(record)
    except Exception:
        return records
    return records


def _winner_from_market(market):
    if not isinstance(market, dict):
        return None
    bool_fields = (
        ("yes_result", "YES"),
        ("yes_won", "YES"),
        ("no_result", "NO"),
        ("no_won", "NO"),
    )
    for field, side in bool_fields:
        if isinstance(market.get(field), bool) and market.get(field):
            return side
    for field in ("winning_side", "winner", "result", "outcome", "settled_side", "settlement_side"):
        raw = market.get(field)
        if raw is None:
            continue
        side = _normalize_side(raw)
        if side:
            return side
        text = str(raw).strip().lower()
        if text in {"true", "1", "yes_wins"}:
            return "YES"
        if text in {"false", "0", "no_wins"}:
            return "NO"
    for field in ("settlement_value", "yes_settlement_value", "settled_value"):
        value = _to_float(market.get(field), None)
        if value is None:
            continue
        if value > 1.0 and value <= 100.0:
            value /= 100.0
        if value >= 0.5:
            return "YES"
        if value <= 0.0:
            return "NO"
    return None


def _market_appears_final(market):
    if not isinstance(market, dict):
        return False
    status = str(
        market.get("status")
        or market.get("market_status")
        or market.get("state")
        or ""
    ).strip().lower()
    return status in {"settled", "resolved", "closed", "finalized", "expired"}


def _resolve_leg(leg, markets_by_ticker):
    ticker = str(leg.get("ticker") or "").strip()
    market = markets_by_ticker.get(ticker)
    if not isinstance(market, dict):
        return None
    winner = _winner_from_market(market)
    if winner is None:
        if _market_appears_final(market):
            return {
                "ticker": ticker,
                "side": leg.get("side"),
                "result_status": "final_result_unknown",
                "winner": None,
                "won": None,
            }
        return None
    side = _normalize_side(leg.get("side"))
    return {
        "ticker": ticker,
        "side": side,
        "result_status": "resolved",
        "winner": winner,
        "won": bool(side == winner),
    }


def _resolve_open_combos(path, markets_by_ticker, timestamp):
    records = _read_jsonl(path)
    built = {}
    resolved = set()
    for record in records:
        combo_id = record.get("combo_id")
        if not combo_id:
            continue
        if record.get("event_type") == "combo_built":
            built[combo_id] = record
        elif record.get("event_type") == "combo_result":
            resolved.add(combo_id)

    emitted = 0
    for combo_id, combo in built.items():
        if combo_id in resolved:
            continue
        legs = combo.get("legs") if isinstance(combo.get("legs"), list) else []
        if not legs:
            continue
        leg_results = []
        unresolved = False
        unknown_final = False
        for leg in legs:
            leg_result = _resolve_leg(leg, markets_by_ticker)
            if leg_result is None:
                unresolved = True
                break
            if leg_result.get("won") is None:
                unknown_final = True
            leg_results.append(leg_result)
        if unresolved or unknown_final:
            continue
        combo_win = all(bool(result.get("won")) for result in leg_results)
        stake = _to_float(combo.get("simulated_stake"), 1.0) or 1.0
        payout = _to_float(combo.get("estimated_payout"), 0.0) or 0.0
        profit = (payout - stake) if combo_win else -stake
        result_record = {
            "event_type": "combo_result",
            "timestamp": timestamp,
            "combo_id": combo_id,
            "combo_key": combo.get("combo_key"),
            "leg_count": combo.get("leg_count"),
            "legs": leg_results,
            "result_status": "resolved",
            "eventual_result": "win" if combo_win else "loss",
            "win": combo_win,
            "simulated_stake": round(stake, 4),
            "estimated_payout": round(payout, 6),
            "profit": round(profit, 6),
            "roi": round(profit / max(stake, 0.0001), 6),
        }
        if _append_jsonl(path, result_record):
            emitted += 1
            _emit("AION-COMBO-RESEARCH-RESULT", result_record)
    return emitted


def _summary_from_records(path, built_this_cycle=0, resolved_this_cycle=0, candidate_count=0, skip_reason=None):
    records = _read_jsonl(path)
    built = {}
    results = {}
    for record in records:
        combo_id = record.get("combo_id")
        if not combo_id:
            continue
        if record.get("event_type") == "combo_built":
            built[combo_id] = record
        elif record.get("event_type") == "combo_result":
            results[combo_id] = record

    by_leg = defaultdict(lambda: {
        "total": 0,
        "resolved": 0,
        "wins": 0,
        "stake": 0.0,
        "profit": 0.0,
        "payout_sum": 0.0,
    })
    profitable_families = Counter()
    profitable_markets = Counter()

    for combo_id, combo in built.items():
        leg_count = int(_to_float(combo.get("leg_count"), 0) or 0)
        if leg_count <= 0:
            continue
        stats = by_leg[leg_count]
        stats["total"] += 1
        stats["payout_sum"] += _to_float(combo.get("estimated_payout"), 0.0) or 0.0
        result = results.get(combo_id)
        if result:
            stats["resolved"] += 1
            if result.get("win"):
                stats["wins"] += 1
                for leg in combo.get("legs") or []:
                    profitable_families[str(leg.get("family") or "UNKNOWN")] += 1
                    profitable_markets[str(leg.get("ticker") or "UNKNOWN")] += 1
            stats["stake"] += _to_float(result.get("simulated_stake"), combo.get("simulated_stake")) or 0.0
            stats["profit"] += _to_float(result.get("profit"), 0.0) or 0.0

    by_leg_payload = {}
    for leg_count, stats in sorted(by_leg.items()):
        total = int(stats["total"])
        resolved = int(stats["resolved"])
        stake = float(stats["stake"])
        by_leg_payload[str(leg_count)] = {
            "total": total,
            "resolved": resolved,
            "hit_rate": round(stats["wins"] / resolved, 6) if resolved else None,
            "roi": round(stats["profit"] / stake, 6) if stake > 0 else None,
            "average_payout": round(stats["payout_sum"] / total, 6) if total else None,
        }

    resolved_roi = {
        leg_count: payload.get("roi")
        for leg_count, payload in by_leg_payload.items()
        if payload.get("roi") is not None
    }
    best_size = max(resolved_roi.items(), key=lambda item: item[1])[0] if resolved_roi else None
    worst_size = min(resolved_roi.items(), key=lambda item: item[1])[0] if resolved_roi else None
    total_virtual_combos = len(built)
    summary = {
        "event_type": "combo_summary",
        "timestamp": _utc_now(),
        "enabled": True,
        "candidate_count": int(candidate_count),
        "built_this_cycle": int(built_this_cycle),
        "resolved_this_cycle": int(resolved_this_cycle),
        "total_virtual_combos": total_virtual_combos,
        "pending_combos": max(0, total_virtual_combos - len(results)),
        "resolved_combos": len(results),
        "by_leg_count": by_leg_payload,
        "best_performing_combo_size": best_size,
        "worst_performing_combo_size": worst_size,
        "top_recurring_profitable_families": profitable_families.most_common(8),
        "top_recurring_profitable_markets": profitable_markets.most_common(8),
        "sample_size_warning": total_virtual_combos < 100,
        "sample_size_warning_reason": "under_100_combos" if total_virtual_combos < 100 else None,
        "jsonl_path": str(path),
    }
    if skip_reason:
        summary["skip_reason"] = skip_reason
    return summary


def _empty_summary(path, built_this_cycle=0, resolved_this_cycle=0, candidate_count=0, skip_reason=None):
    summary = {
        "event_type": "combo_summary",
        "timestamp": _utc_now(),
        "enabled": True,
        "candidate_count": int(candidate_count),
        "built_this_cycle": int(built_this_cycle),
        "resolved_this_cycle": int(resolved_this_cycle),
        "total_virtual_combos": 0,
        "pending_combos": 0,
        "resolved_combos": 0,
        "by_leg_count": {},
        "best_performing_combo_size": None,
        "worst_performing_combo_size": None,
        "top_recurring_profitable_families": [],
        "top_recurring_profitable_markets": [],
        "sample_size_warning": True,
        "sample_size_warning_reason": "under_100_combos",
        "jsonl_path": str(path),
    }
    if skip_reason:
        summary["skip_reason"] = skip_reason
    return summary


def _maybe_export_obsidian_summary(summary):
    global _LAST_OBSIDIAN_EXPORT_TS
    if not isinstance(summary, dict):
        return None
    if not summary.get("built_this_cycle") and not summary.get("resolved_this_cycle"):
        return None
    now = time.time()
    if now - _LAST_OBSIDIAN_EXPORT_TS < OBSIDIAN_EXPORT_MIN_SECONDS:
        return None
    try:
        from obsidian_exporter import append_session_intelligence

        status = append_session_intelligence(
            event_type="combo_research_summary",
            facts={
                "total_virtual_combos": summary.get("total_virtual_combos"),
                "built_this_cycle": summary.get("built_this_cycle"),
                "resolved_this_cycle": summary.get("resolved_this_cycle"),
                "best_combo_size": summary.get("best_performing_combo_size"),
                "worst_combo_size": summary.get("worst_performing_combo_size"),
                "sample_size_warning": summary.get("sample_size_warning"),
                "jsonl_path": summary.get("jsonl_path"),
            },
            timestamp=summary.get("timestamp"),
            source="combo_research",
            related=["Sports", "Telemetry", "Candidate Selection", "Adaptive Confidence"],
        )
        _LAST_OBSIDIAN_EXPORT_TS = now
        return status
    except Exception:
        return None


def _attach_diversity_summary(summary, thesis_summary, config):
    if not isinstance(summary, dict):
        return summary
    thesis_summary = thesis_summary if isinstance(thesis_summary, dict) else {}
    distinct_count = int(thesis_summary.get("representative_count") or thesis_summary.get("thesis_count") or 0)
    summary["diversity_mode"] = "distinct_thesis" if config.require_distinct_thesis else "raw_candidate_pool"
    summary["distinct_thesis_count"] = distinct_count
    summary["thesis_group_count"] = int(thesis_summary.get("thesis_count") or 0)
    summary["rejected_same_thesis_count"] = int(thesis_summary.get("rejected_same_thesis_count") or 0)
    if config.require_distinct_thesis and distinct_count < int(config.max_legs or 0):
        summary["larger_combo_skip_reason"] = "insufficient_distinct_thesis_groups_for_max_legs"
    return summary


def run_combo_research_cycle(
    *,
    bot,
    candidates,
    trade_readiness_records=None,
    markets=None,
    expectancy_summary_by_ticker=None,
    selected_ticker=None,
    source="sports_live_intelligence",
):
    config = get_combo_research_config()
    timestamp = _utc_now()
    if not config.enabled:
        return {
            "enabled": False,
            "reason": "COMBO_RESEARCH_ENABLED=false",
            "jsonl_path": str(config.output_path),
        }

    persistence_ready = _ensure_jsonl_file(config.output_path)
    if not persistence_ready:
        summary = _empty_summary(
            config.output_path,
            built_this_cycle=0,
            resolved_this_cycle=0,
            candidate_count=len(candidates or []),
            skip_reason="jsonl_init_failed",
        )
        summary["persistence_ready"] = False
        _emit("AION-COMBO-RESEARCH-SUMMARY", summary)
        return summary
    markets_by_ticker = _market_lookup(markets)
    resolved_this_cycle = _resolve_open_combos(config.output_path, markets_by_ticker, timestamp)
    pool, reject_reasons = _normalize_candidate_pool(
        bot,
        candidates,
        trade_readiness_records,
        markets,
        expectancy_summary_by_ticker,
        source=source,
    )
    _emit(
        "AION-COMBO-RESEARCH-CANDIDATES",
        {
            "timestamp": timestamp,
            "source": source,
            "raw_candidate_count": len(candidates or []),
            "sports_candidate_count": len(pool),
            "min_candidates": config.min_candidates,
            "max_legs": config.max_legs,
            "distinct_thesis_required": config.require_distinct_thesis,
            "selected_ticker": selected_ticker,
            "reject_reasons": dict(reject_reasons),
            "top_candidates": [
                {
                    "ticker": item.get("ticker"),
                    "family": item.get("family"),
                    "thesis_key": item.get("thesis_key"),
                    "side": item.get("side"),
                    "selected_side": item.get("side"),
                    "preferred_side": None,
                    "side_source": item.get("side_source"),
                    "side_selection_reason": item.get("side_selection_reason"),
                    "price": item.get("price"),
                    "probability": item.get("probability"),
                    "edge": item.get("edge"),
                    "confidence": item.get("confidence"),
                    "execution_score": item.get("candidate_score"),
                    "quality_rank": item.get("quality_rank"),
                }
                for item in pool[:8]
            ],
        },
    )

    if len(pool) < config.min_candidates:
        try:
            output_has_records = config.output_path.exists() and config.output_path.stat().st_size > 0
        except Exception:
            output_has_records = False
        if output_has_records:
            summary = _summary_from_records(
                config.output_path,
                built_this_cycle=0,
                resolved_this_cycle=resolved_this_cycle,
                candidate_count=len(pool),
                skip_reason="insufficient_sports_candidates",
            )
        else:
            summary = _empty_summary(
                config.output_path,
                built_this_cycle=0,
                resolved_this_cycle=resolved_this_cycle,
                candidate_count=len(pool),
                skip_reason="insufficient_sports_candidates",
            )
        _emit("AION-COMBO-RESEARCH-SUMMARY", summary)
        _maybe_export_obsidian_summary(summary)
        return summary

    diversified_pool, thesis_summary = _apply_combo_thesis_diversity(pool, config, source)
    min_distinct_needed = 2 if config.allow_smaller_distinct_combos else config.min_candidates
    if len(diversified_pool) < min_distinct_needed:
        try:
            output_has_records = config.output_path.exists() and config.output_path.stat().st_size > 0
        except Exception:
            output_has_records = False
        if output_has_records:
            summary = _summary_from_records(
                config.output_path,
                built_this_cycle=0,
                resolved_this_cycle=resolved_this_cycle,
                candidate_count=len(pool),
                skip_reason="insufficient_distinct_thesis_candidates",
            )
        else:
            summary = _empty_summary(
                config.output_path,
                built_this_cycle=0,
                resolved_this_cycle=resolved_this_cycle,
                candidate_count=len(pool),
                skip_reason="insufficient_distinct_thesis_candidates",
            )
        summary = _attach_diversity_summary(summary, thesis_summary, config)
        _emit("AION-COMBO-RESEARCH-SUMMARY", summary)
        _maybe_export_obsidian_summary(summary)
        return summary

    existing_records = _read_jsonl(config.output_path)
    open_keys = _open_combo_keys(existing_records)
    combo_records = _build_virtual_combos(diversified_pool, config, timestamp, source, open_keys)
    built_this_cycle = 0
    for record in combo_records:
        if _append_jsonl(config.output_path, record):
            built_this_cycle += 1
            _emit(
                "AION-COMBO-DIVERSIFIED-BUILD",
                {
                    "leg_count": record.get("leg_count"),
                    "thesis_count": record.get("thesis_count"),
                    "combo_tickers": [leg.get("ticker") for leg in record.get("legs", [])],
                    "estimated_payout": record.get("estimated_payout"),
                    "stake": record.get("simulated_stake"),
                    "diversity_mode": record.get("diversity_mode"),
                },
            )
            _emit(
                "AION-COMBO-RESEARCH-BUILD",
                {
                    "timestamp": record.get("timestamp"),
                    "combo_id": record.get("combo_id"),
                    "leg_count": record.get("leg_count"),
                    "legs": [
                        {
                            "ticker": leg.get("ticker"),
                            "family": leg.get("family"),
                            "side": leg.get("side"),
                            "side_source": leg.get("side_source"),
                            "price": leg.get("price"),
                            "probability": leg.get("probability"),
                            "confidence": leg.get("confidence"),
                            "edge": leg.get("edge"),
                        }
                        for leg in record.get("legs", [])
                    ],
                    "implied_combo_probability": record.get("implied_combo_probability"),
                    "market_implied_combo_probability": record.get("market_implied_combo_probability"),
                    "simulated_stake": record.get("simulated_stake"),
                    "estimated_payout": record.get("estimated_payout"),
                    "combo_score": record.get("combo_score"),
                    "jsonl_path": str(config.output_path),
                },
            )

    skip_reason = None if built_this_cycle else "no_new_clean_combos"
    summary = _summary_from_records(
        config.output_path,
        built_this_cycle=built_this_cycle,
        resolved_this_cycle=resolved_this_cycle,
        candidate_count=len(pool),
        skip_reason=skip_reason,
    )
    summary = _attach_diversity_summary(summary, thesis_summary, config)
    _emit("AION-COMBO-RESEARCH-SUMMARY", summary)
    _maybe_export_obsidian_summary(summary)
    return summary

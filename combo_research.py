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
    if isinstance(market, dict):
        for key in (
            "event_ticker",
            "event_id",
            "game_id",
            "match_id",
            "series_ticker",
            "market_slug",
            "ticker",
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


def _normalize_candidate_pool(bot, candidates, trade_readiness_records, markets, expectancy_summary_by_ticker=None):
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
        side = _selected_side(item, q, expectancy_summary, readiness)
        price = _selected_price(side, item, q, expectancy_summary, readiness)
        if side not in {"YES", "NO"}:
            reject_reasons["missing_side"] += 1
            continue
        if price is None:
            reject_reasons["missing_price"] += 1
            continue

        probability = _candidate_probability(item, q, expectancy_summary, readiness)
        confidence = _candidate_confidence(item, q, expectancy_summary, readiness)
        edge = _candidate_edge(item, q, expectancy_summary, readiness)
        candidate_score = _first_value(item.get("candidate_score"), item.get("score"), readiness.get("candidate_score"))
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
                "side": side,
                "price": round(price, 6),
                "probability": round(probability, 6) if probability is not None else None,
                "confidence": round(confidence, 6) if confidence is not None else None,
                "edge": round(edge, 6) if edge is not None else None,
                "candidate_score": round(_to_float(candidate_score, 0.0), 6),
                "quality_rank": quality_rank,
                "spread": _to_float(item.get("spread"), None),
                "liquidity": _to_float(_first_value(item.get("liquidity"), _get(q, "liquidity"), readiness.get("liquidity")), None),
                "volume": _to_float(_first_value(item.get("volume"), _get(q, "volume"), readiness.get("volume")), None),
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


def _build_combo_record(legs, leg_count, config, timestamp, source):
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
    for leg_count in range(2, max_legs + 1):
        if leg_count == 5 and len(pool) < 5:
            continue
        scored = []
        for legs_tuple in combinations(pool, leg_count):
            legs = [dict(leg) for leg in legs_tuple]
            if not _combo_is_clean(legs):
                continue
            combo_key = _combo_key(legs)
            if combo_key in existing_open_keys:
                continue
            scored.append((_combo_score(legs), legs))
        scored.sort(key=lambda item: item[0], reverse=True)
        for _score, legs in scored[:MAX_COMBOS_PER_LEG_COUNT]:
            records.append(_build_combo_record(legs, leg_count, config, timestamp, source))
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
            "selected_ticker": selected_ticker,
            "reject_reasons": dict(reject_reasons),
            "top_candidates": [
                {
                    "ticker": item.get("ticker"),
                    "family": item.get("family"),
                    "side": item.get("side"),
                    "price": item.get("price"),
                    "edge": item.get("edge"),
                    "confidence": item.get("confidence"),
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

    existing_records = _read_jsonl(config.output_path)
    open_keys = _open_combo_keys(existing_records)
    combo_records = _build_virtual_combos(pool, config, timestamp, source, open_keys)
    built_this_cycle = 0
    for record in combo_records:
        if _append_jsonl(config.output_path, record):
            built_this_cycle += 1
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
    _emit("AION-COMBO-RESEARCH-SUMMARY", summary)
    _maybe_export_obsidian_summary(summary)
    return summary

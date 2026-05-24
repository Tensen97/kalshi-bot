"""Lightweight public top-trader signal loader and matcher.

This module intentionally does not scrape or call external services. It only
loads operator-provided JSONL/CSV observations and gives the live bot a small
positive-only ranking hint when those observations match current candidates.
"""

from __future__ import annotations

import csv
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


FIELDNAMES = (
    "event_type",
    "trader_handle",
    "source_url_or_label",
    "observed_at",
    "market_title",
    "ticker",
    "side",
    "cost",
    "payout",
    "implied_probability",
    "category",
    "raw_text",
    "confidence_parse_score",
)


@dataclass
class ElitePublicSignal:
    event_type: str = "elite_public_signal"
    trader_handle: str = ""
    source_url_or_label: str = ""
    observed_at: float | None = None
    market_title: str = ""
    ticker: str = ""
    side: str = ""
    cost: float | None = None
    payout: float | None = None
    implied_probability: float | None = None
    category: str = ""
    raw_text: str = ""
    confidence_parse_score: float = 0.0
    resolved_ticker: str = ""
    resolved_market_title: str = ""
    resolved_category: str = ""
    match_confidence: float = 0.0

    def public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.observed_at:
            payload["observed_at"] = datetime.fromtimestamp(self.observed_at, timezone.utc).isoformat().replace("+00:00", "Z")
        else:
            payload["observed_at"] = None
        return {key: value for key, value in payload.items() if value not in (None, "")}


def utc_now_ts() -> float:
    return time.time()


def coerce_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def clean_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def parse_observed_at(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000.0
        return timestamp if timestamp > 0 else None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def normalize_side(value: Any) -> str:
    text = clean_text(value).lower()
    if text in {"yes", "y", "buy_yes", "long_yes"}:
        return "yes"
    if text in {"no", "n", "buy_no", "long_no"}:
        return "no"
    return text


def normalize_text(value: Any) -> str:
    text = clean_text(value).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def token_set(value: Any) -> set[str]:
    return {token for token in normalize_text(value).split() if len(token) >= 2}


def text_similarity(left: Any, right: Any) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    sequence_score = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = token_set(left_norm)
    right_tokens = token_set(right_norm)
    overlap_score = 0.0
    if left_tokens and right_tokens:
        overlap_score = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return round(max(sequence_score * 0.65 + overlap_score * 0.35, overlap_score), 4)


def market_family(market: dict[str, Any]) -> str:
    for key in ("series_ticker", "event_ticker", "ticker"):
        value = clean_text(market.get(key)).upper()
        if value:
            return value.split("-", 1)[0]
    return ""


def market_category(market: dict[str, Any], family: str = "") -> str:
    explicit = clean_text(market.get("category") or market.get("market_type") or market.get("category_name")).lower()
    if explicit:
        return explicit
    family_key = (family or market_family(market)).upper()
    if family_key.startswith(("KXNBA", "KXMLB", "KXNFL", "KXNHL", "KXNCA")):
        return "sports"
    if family_key.startswith(("KXBTC", "KXETH", "KXXRP", "KXCRYPTO")):
        return "crypto"
    return ""


def market_match_text(market: dict[str, Any]) -> str:
    fields = (
        market.get("title"),
        market.get("subtitle"),
        market.get("event_title"),
        market.get("event_ticker"),
        market.get("series_ticker"),
        market.get("yes_sub_title"),
        market.get("no_sub_title"),
        market.get("ticker"),
    )
    return " ".join(clean_text(field) for field in fields if clean_text(field))


def market_title_text(market: dict[str, Any]) -> str:
    return " ".join(
        clean_text(field)
        for field in (
            market.get("title"),
            market.get("subtitle"),
            market.get("event_title"),
            market.get("yes_sub_title"),
            market.get("no_sub_title"),
        )
        if clean_text(field)
    )


def signal_match_text(signal: ElitePublicSignal) -> str:
    return " ".join(
        value
        for value in (signal.market_title, signal.raw_text, signal.ticker, signal.category)
        if clean_text(value)
    )


def estimate_parse_confidence(row: dict[str, Any]) -> float:
    explicit = coerce_float(row.get("confidence_parse_score"), None)
    if explicit is not None:
        return max(0.0, min(1.0, explicit))
    score = 0.0
    if clean_text(row.get("trader_handle")):
        score += 0.15
    if clean_text(row.get("source_url_or_label")):
        score += 0.10
    if clean_text(row.get("market_title")) or clean_text(row.get("raw_text")):
        score += 0.35
    if clean_text(row.get("ticker")):
        score += 0.20
    if clean_text(row.get("side")):
        score += 0.08
    if coerce_float(row.get("cost"), None) is not None or coerce_float(row.get("payout"), None) is not None:
        score += 0.07
    if coerce_float(row.get("implied_probability"), None) is not None:
        score += 0.05
    return round(max(0.0, min(1.0, score)), 4)


def signal_from_row(row: dict[str, Any]) -> ElitePublicSignal | None:
    if not isinstance(row, dict):
        return None
    event_type = clean_text(row.get("event_type") or "elite_public_signal").lower()
    if event_type and event_type != "elite_public_signal":
        return None
    market_title = clean_text(row.get("market_title") or row.get("title"))
    raw_text = clean_text(row.get("raw_text") or row.get("text") or row.get("description"))
    ticker = clean_text(row.get("ticker")).upper()
    if not market_title and not raw_text and not ticker:
        return None
    return ElitePublicSignal(
        event_type="elite_public_signal",
        trader_handle=clean_text(row.get("trader_handle") or row.get("handle") or row.get("trader")),
        source_url_or_label=clean_text(row.get("source_url_or_label") or row.get("source") or row.get("url")),
        observed_at=parse_observed_at(row.get("observed_at") or row.get("timestamp") or row.get("time")),
        market_title=market_title,
        ticker=ticker,
        side=normalize_side(row.get("side")),
        cost=coerce_float(row.get("cost"), None),
        payout=coerce_float(row.get("payout"), None),
        implied_probability=coerce_float(row.get("implied_probability") or row.get("probability"), None),
        category=clean_text(row.get("category")).lower(),
        raw_text=raw_text,
        confidence_parse_score=estimate_parse_confidence(row),
    )


def load_signal_files(paths: Iterable[Path]) -> tuple[list[ElitePublicSignal], dict[str, Any]]:
    signals: list[ElitePublicSignal] = []
    summary = {
        "files_seen": 0,
        "files_missing": 0,
        "rows_seen": 0,
        "rows_loaded": 0,
        "rows_skipped": 0,
        "errors": 0,
        "paths": [],
    }
    for path in paths:
        path = Path(path)
        summary["paths"].append(str(path))
        if not path.exists():
            summary["files_missing"] += 1
            continue
        summary["files_seen"] += 1
        try:
            if path.suffix.lower() == ".csv":
                with path.open("r", encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
            else:
                rows = []
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        parsed = json.loads(line)
                        if isinstance(parsed, list):
                            rows.extend(item for item in parsed if isinstance(item, dict))
                        elif isinstance(parsed, dict):
                            rows.append(parsed)
            for row in rows:
                summary["rows_seen"] += 1
                signal = signal_from_row(row)
                if signal is None:
                    summary["rows_skipped"] += 1
                    continue
                signals.append(signal)
                summary["rows_loaded"] += 1
        except (OSError, csv.Error, json.JSONDecodeError):
            summary["errors"] += 1
    return signals, summary


def is_recent_signal(signal: ElitePublicSignal, *, now_ts: float, max_age_hours: float) -> bool:
    if signal.observed_at is None:
        return True
    return (now_ts - signal.observed_at) <= max(0.0, max_age_hours) * 3600.0


def resolve_signals_to_markets(
    signals: Iterable[ElitePublicSignal],
    markets: Iterable[dict[str, Any]],
    *,
    min_confidence: float,
) -> tuple[list[ElitePublicSignal], dict[str, Any]]:
    market_list = [market for market in markets or [] if isinstance(market, dict)]
    by_ticker = {
        clean_text(market.get("ticker")).upper(): market
        for market in market_list
        if clean_text(market.get("ticker"))
    }
    resolved: list[ElitePublicSignal] = []
    summary = {
        "signals_seen": 0,
        "resolved": 0,
        "unresolved": 0,
        "exact_ticker": 0,
        "fuzzy": 0,
    }
    for signal in signals:
        summary["signals_seen"] += 1
        candidate_ticker = clean_text(signal.ticker).upper()
        if candidate_ticker and candidate_ticker in by_ticker:
            market = by_ticker[candidate_ticker]
            signal.resolved_ticker = candidate_ticker
            signal.resolved_market_title = clean_text(market.get("title") or signal.market_title)
            signal.resolved_category = signal.category or market_category(market)
            signal.match_confidence = 1.0
            summary["resolved"] += 1
            summary["exact_ticker"] += 1
            resolved.append(signal)
            continue
        source_text = signal_match_text(signal)
        best_market: dict[str, Any] | None = None
        best_score = 0.0
        for market in market_list:
            score = max(
                text_similarity(source_text, market_match_text(market)),
                text_similarity(signal.market_title, market_title_text(market)),
                text_similarity(signal.raw_text, market_match_text(market)),
            )
            if score > best_score:
                best_score = score
                best_market = market
        if best_market is not None and best_score >= min_confidence:
            signal.resolved_ticker = clean_text(best_market.get("ticker")).upper()
            signal.resolved_market_title = clean_text(best_market.get("title") or signal.market_title)
            signal.resolved_category = signal.category or market_category(best_market)
            signal.match_confidence = best_score
            summary["resolved"] += 1
            summary["fuzzy"] += 1
        else:
            summary["unresolved"] += 1
        resolved.append(signal)
    return resolved, summary


def match_signal_to_candidate(
    signal: ElitePublicSignal,
    *,
    ticker: str = "",
    title: str = "",
    category: str = "",
    family: str = "",
    min_confidence: float,
) -> dict[str, Any]:
    ticker_key = clean_text(ticker).upper()
    category_key = clean_text(category).lower()
    family_key = clean_text(family).upper()
    signal_ticker = clean_text(signal.resolved_ticker or signal.ticker).upper()
    if ticker_key and signal_ticker and ticker_key == signal_ticker:
        return {"matched": True, "confidence": 1.0, "reason": "ticker_exact", "strong": True}
    signal_category = clean_text(signal.resolved_category or signal.category).lower()
    category_match = bool(category_key and signal_category and category_key == signal_category)
    signal_family = signal_ticker.split("-", 1)[0] if signal_ticker else ""
    family_match = bool(family_key and signal_family and family_key == signal_family)
    source_text = signal_match_text(signal)
    score = max(
        text_similarity(source_text, title),
        text_similarity(signal.market_title, title),
        text_similarity(signal.raw_text, title),
    )
    confidence = max(score, 0.72 if family_match else 0.0, 0.62 if category_match else 0.0)
    if confidence >= min_confidence:
        return {
            "matched": True,
            "confidence": round(confidence, 4),
            "reason": "text_family_category",
            "strong": bool(score >= max(0.9, min_confidence + 0.1) or family_match),
        }
    return {"matched": False, "confidence": round(confidence, 4), "reason": "below_confidence", "strong": False}


def best_candidate_match(
    signals: Iterable[ElitePublicSignal],
    *,
    ticker: str = "",
    title: str = "",
    category: str = "",
    family: str = "",
    now_ts: float | None = None,
    max_age_hours: float = 72.0,
    min_confidence: float = 0.78,
) -> dict[str, Any]:
    now_ts = utc_now_ts() if now_ts is None else now_ts
    best: dict[str, Any] = {"matched": False, "confidence": 0.0, "reason": "no_recent_match"}
    for signal in signals:
        if not is_recent_signal(signal, now_ts=now_ts, max_age_hours=max_age_hours):
            continue
        match = match_signal_to_candidate(
            signal,
            ticker=ticker,
            title=title,
            category=category,
            family=family,
            min_confidence=min_confidence,
        )
        if not match.get("matched"):
            continue
        confidence = coerce_float(match.get("confidence"), 0.0) or 0.0
        if confidence > coerce_float(best.get("confidence"), 0.0):
            best = {
                **match,
                "signal": signal,
                "confidence": round(confidence, 4),
            }
    return best

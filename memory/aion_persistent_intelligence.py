from __future__ import annotations

import builtins
import json
import math
import os
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from runtime.paths import ensure_runtime_layout, record_persist_event, runtime_memory_file, safe_atomic_json_write
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    import fcntl
except Exception:  # pragma: no cover - non-Linux fallback
    fcntl = None

from risk.helpers import bounded_delta, clamp, safe_float, safe_ratio
from runtime.paths import ensure_runtime_layout, legacy_runtime_artifact_paths, runtime_memory_file

try:
    from memory.aion_sports_history_store import AIONSportsHistoryStore
except Exception:  # pragma: no cover - optional runtime store
    AIONSportsHistoryStore = None


def _now():
    return time.time()


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _load_json_file(path):
    try:
        with open(path, "r") as handle:
            return json.load(handle)
    except Exception:
        return None


@contextmanager
def _state_file_lock(path):
    lock_handle = None
    try:
        lock_path = Path(str(path) + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = open(lock_path, "a")
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if lock_handle is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()


def _load_legacy_payload():
    candidates = []
    for path in legacy_runtime_artifact_paths():
        if os.path.exists(path):
            try:
                candidates.append((os.path.getmtime(path), path))
            except Exception:
                continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    for _, path in candidates:
        payload = _load_json_file(path)
        if isinstance(payload, dict):
            return payload
    return None


def _ensure_profile_defaults(key=None, kind="ticker"):
    now = _now()
    return {
        "key": key,
        "kind": kind,
        "first_seen_time": now,
        "last_seen_time": now,
        "total_observations": 0,
        "near_pass_count": 0,
        "improving_edge_count": 0,
        "decaying_edge_count": 0,
        "recovery_count": 0,
        "spread_quality_history": [],
        "stability_history": [],
        "quote_stability_history": [],
        "directional_consistency_history": [],
        "volatility_regime_history": [],
        "execution_outcome_history": [],
        "recent_observations": [],
        "persistence_score": 0.5,
        "quote_stability_trend": 0.5,
        "edge_behavior_trend": 0.5,
        "volatility_tendency": 0.5,
        "directional_consistency": 0.5,
        "execution_outcome_memory": {},
        "last_summary": {},
    }


def _trim_list(values, limit):
    if not isinstance(values, list):
        return []
    if len(values) <= limit:
        return values
    return values[-limit:]


DISTILLED_OBSERVATION_LIMIT = 1200
DISTILLED_OUTCOME_LIMIT = 1200
DISTILLED_SPORTS_HISTORY_LIMIT = 10000
DISTILLED_SPORTS_HISTORY_SEEN_LIMIT = 25000
_LAST_SQLITE_HISTORY_WARN_TS = 0.0

SPORTS_FAMILY_PREFIXES = (
    "KXNBA",
    "KXWNBA",
    "KXMLB",
    "KXNFL",
    "KXNHL",
    "KXNCAA",
    "KXCFB",
    "KXCBB",
    "KXATP",
    "KXWTA",
    "KXITF",
    "KXTENNIS",
    "KXGOLF",
    "KXEPL",
    "KXSOCCER",
    "KXUFC",
    "KXMMA",
    "KXBOX",
    "KXIPLSIX",
)


def _ensure_sports_history_defaults(payload=None):
    payload = payload if isinstance(payload, dict) else {}
    summaries = payload.get("summaries") if isinstance(payload.get("summaries"), dict) else {}
    return {
        "version": int(safe_float(payload.get("version"), 1) or 1),
        "records": _trim_list(payload.get("records", []), DISTILLED_SPORTS_HISTORY_LIMIT),
        "seen_keys": payload.get("seen_keys") if isinstance(payload.get("seen_keys"), dict) else {},
        "summaries": {
            "families": summaries.get("families") if isinstance(summaries.get("families"), dict) else {},
            "sports": summaries.get("sports") if isinstance(summaries.get("sports"), dict) else {},
            "odds_buckets": summaries.get("odds_buckets") if isinstance(summaries.get("odds_buckets"), dict) else {},
            "asymmetry_buckets": summaries.get("asymmetry_buckets") if isinstance(summaries.get("asymmetry_buckets"), dict) else {},
            "liquidity_buckets": summaries.get("liquidity_buckets") if isinstance(summaries.get("liquidity_buckets"), dict) else {},
            "spread_buckets": summaries.get("spread_buckets") if isinstance(summaries.get("spread_buckets"), dict) else {},
            "volume_buckets": summaries.get("volume_buckets") if isinstance(summaries.get("volume_buckets"), dict) else {},
            "movement_buckets": summaries.get("movement_buckets") if isinstance(summaries.get("movement_buckets"), dict) else {},
            "volatility_buckets": summaries.get("volatility_buckets") if isinstance(summaries.get("volatility_buckets"), dict) else {},
            "side_buckets": summaries.get("side_buckets") if isinstance(summaries.get("side_buckets"), dict) else {},
            "side_odds_buckets": summaries.get("side_odds_buckets") if isinstance(summaries.get("side_odds_buckets"), dict) else {},
            "family_odds_buckets": summaries.get("family_odds_buckets") if isinstance(summaries.get("family_odds_buckets"), dict) else {},
            "family_asymmetry_buckets": summaries.get("family_asymmetry_buckets") if isinstance(summaries.get("family_asymmetry_buckets"), dict) else {},
            "sport_asymmetry_buckets": summaries.get("sport_asymmetry_buckets") if isinstance(summaries.get("sport_asymmetry_buckets"), dict) else {},
            "underdog_favorite": summaries.get("underdog_favorite") if isinstance(summaries.get("underdog_favorite"), dict) else {},
            "recency_buckets": summaries.get("recency_buckets") if isinstance(summaries.get("recency_buckets"), dict) else {},
            "sport_family_volatility": summaries.get("sport_family_volatility") if isinstance(summaries.get("sport_family_volatility"), dict) else {},
            "elite_traders": summaries.get("elite_traders") if isinstance(summaries.get("elite_traders"), dict) else {},
            "elite_trader_families": summaries.get("elite_trader_families") if isinstance(summaries.get("elite_trader_families"), dict) else {},
            "elite_trader_odds": summaries.get("elite_trader_odds") if isinstance(summaries.get("elite_trader_odds"), dict) else {},
        },
        "audit": payload.get("audit") if isinstance(payload.get("audit"), dict) else {},
    }


def _ensure_distilled_learning_defaults(payload=None):
    payload = payload if isinstance(payload, dict) else {}
    summaries = payload.get("summaries") if isinstance(payload.get("summaries"), dict) else {}
    return {
        "version": int(safe_float(payload.get("version"), 1) or 1),
        "observations": _trim_list(payload.get("observations", []), DISTILLED_OBSERVATION_LIMIT),
        "outcomes": _trim_list(payload.get("outcomes", []), DISTILLED_OUTCOME_LIMIT),
        "last_observation_by_key": payload.get("last_observation_by_key") if isinstance(payload.get("last_observation_by_key"), dict) else {},
        "summaries": {
            "families": summaries.get("families") if isinstance(summaries.get("families"), dict) else {},
            "categories": summaries.get("categories") if isinstance(summaries.get("categories"), dict) else {},
            "sports_tags": summaries.get("sports_tags") if isinstance(summaries.get("sports_tags"), dict) else {},
            "odds_buckets": summaries.get("odds_buckets") if isinstance(summaries.get("odds_buckets"), dict) else {},
            "book_quality": summaries.get("book_quality") if isinstance(summaries.get("book_quality"), dict) else {},
            "side_buckets": summaries.get("side_buckets") if isinstance(summaries.get("side_buckets"), dict) else {},
            "asymmetry_buckets": summaries.get("asymmetry_buckets") if isinstance(summaries.get("asymmetry_buckets"), dict) else {},
            "liquidity_buckets": summaries.get("liquidity_buckets") if isinstance(summaries.get("liquidity_buckets"), dict) else {},
            "spread_buckets": summaries.get("spread_buckets") if isinstance(summaries.get("spread_buckets"), dict) else {},
            "sport_types": summaries.get("sport_types") if isinstance(summaries.get("sport_types"), dict) else {},
            "elite_traders": summaries.get("elite_traders") if isinstance(summaries.get("elite_traders"), dict) else {},
            "elite_trader_categories": summaries.get("elite_trader_categories") if isinstance(summaries.get("elite_trader_categories"), dict) else {},
        },
        "sports_history": _ensure_sports_history_defaults(payload.get("sports_history")),
        "audit": payload.get("audit") if isinstance(payload.get("audit"), dict) else {},
    }


def _distilled_iso_to_ts(value):
    if isinstance(value, (int, float)):
        return safe_float(value, _now())
    text = str(value or "").strip()
    if not text:
        return _now()
    try:
        return float(text)
    except Exception:
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return _now()


def _distilled_price_bucket(price):
    value = safe_float(price, None)
    if value is None:
        return "unknown"
    if value > 1.0 and value <= 100.0:
        value = value / 100.0
    if value < 0.20:
        return "under_20"
    if value <= 0.35:
        return "20_35"
    if value <= 0.45:
        return "35_45"
    if value <= 0.60:
        return "45_60"
    if value <= 0.80:
        return "60_80"
    return "over_80"


def _distilled_normalized_price(price):
    value = safe_float(price, None)
    if value is None:
        return None
    if value > 1.0 and value <= 100.0:
        value = value / 100.0
    if value < 0.0 or value > 1.0:
        return None
    return round(value, 6)


def _distilled_asymmetry_bucket(price=None, payout_multiple=None):
    multiple = safe_float(payout_multiple, None)
    price_value = _distilled_normalized_price(price)
    if multiple is None and price_value is not None and price_value > 0.0:
        multiple = 1.0 / price_value
    if multiple is None:
        return "unknown"
    if multiple < 1.5:
        return "low_asymmetry"
    if multiple < 2.25:
        return "moderate_asymmetry"
    if multiple < 3.5:
        return "strong_asymmetry"
    return "extreme_asymmetry"


def _distilled_spread_bucket(spread):
    value = safe_float(spread, None)
    if value is None:
        return "unknown"
    if value > 1.0 and value <= 100.0:
        value = value / 100.0
    if value <= 0.03:
        return "tight"
    if value <= 0.06:
        return "usable"
    if value <= 0.12:
        return "wide"
    return "very_wide"


def _distilled_liquidity_bucket(value):
    amount = safe_float(value, None)
    if amount is None:
        return "unknown"
    if amount < 25.0:
        return "thin"
    if amount < 250.0:
        return "light"
    if amount < 1000.0:
        return "usable"
    if amount < 5000.0:
        return "deep"
    return "very_deep"


def _distilled_volume_bucket(value):
    amount = safe_float(value, None)
    if amount is None:
        return "unknown"
    if amount < 25.0:
        return "quiet"
    if amount < 250.0:
        return "light"
    if amount < 1000.0:
        return "active"
    if amount < 5000.0:
        return "very_active"
    return "heavy"


def _distilled_movement_bucket(value):
    movement = safe_float(value, None)
    if movement is None:
        return "unknown"
    if movement > 1.0 and movement <= 100.0:
        movement = movement / 100.0
    if movement <= -0.08:
        return "down_large"
    if movement <= -0.025:
        return "down"
    if movement < 0.025:
        return "flat"
    if movement < 0.08:
        return "up"
    return "up_large"


def _distilled_volatility_bucket(value):
    volatility = safe_float(value, None)
    if volatility is None:
        return "unknown"
    if volatility > 1.0 and volatility <= 100.0:
        volatility = volatility / 100.0
    volatility = abs(volatility)
    if volatility < 0.015:
        return "low"
    if volatility < 0.05:
        return "normal"
    if volatility < 0.12:
        return "high"
    return "extreme"


def _distilled_score_bucket(value, *, prefix="score"):
    score = safe_float(value, None)
    if score is None:
        return f"{prefix}_unknown"
    if score < 25.0:
        return f"{prefix}_weak"
    if score < 50.0:
        return f"{prefix}_present"
    if score < 75.0:
        return f"{prefix}_strong"
    return f"{prefix}_elite"


def _distilled_sport_type(family=None, title=None, ticker=None):
    text = " ".join(str(value or "").upper() for value in (family, title, ticker))
    if "NBA" in text or "WNBA" in text:
        return "nba"
    if "MLB" in text or "BASEBALL" in text:
        return "mlb"
    if "NFL" in text or "FOOTBALL" in text or "CFB" in text or "NCAAF" in text:
        return "football"
    if "NHL" in text or "HOCKEY" in text:
        return "nhl"
    if "NCAA" in text or "CBB" in text or "NCAAB" in text:
        return "ncaa"
    if "ATP" in text or "WTA" in text or "TENNIS" in text:
        return "tennis"
    if "GOLF" in text or "PGA" in text:
        return "golf"
    if "EPL" in text or "SOCCER" in text:
        return "soccer"
    if "UFC" in text or "MMA" in text or "BOX" in text:
        return "combat"
    if "IPL" in text or "CRICKET" in text:
        return "cricket"
    return "sports"


def _distilled_result_to_yes_won(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"yes", "y", "true", "1", "above", "over", "higher", "win", "won"}:
        return True
    if text in {"no", "n", "false", "0", "below", "under", "lower", "loss", "lost"}:
        return False
    return None


def _distilled_recency_weight(timestamp_ts, now_ts=None):
    timestamp_ts = safe_float(timestamp_ts, None)
    if timestamp_ts is None:
        return 0.25
    now_ts = safe_float(now_ts, _now()) or _now()
    age_seconds = max(0.0, now_ts - timestamp_ts)
    if age_seconds <= 30 * 86400.0:
        return 1.0
    if age_seconds <= 90 * 86400.0:
        return 0.75
    if age_seconds <= 365 * 86400.0:
        return 0.45
    if age_seconds <= 730 * 86400.0:
        return 0.22
    return 0.10


def _distilled_age_bucket(timestamp_ts, now_ts=None):
    timestamp_ts = safe_float(timestamp_ts, None)
    if timestamp_ts is None:
        return "unknown_age"
    now_ts = safe_float(now_ts, _now()) or _now()
    age_seconds = max(0.0, now_ts - timestamp_ts)
    if age_seconds <= 30 * 86400.0:
        return "last_30d"
    if age_seconds <= 90 * 86400.0:
        return "last_90d"
    if age_seconds <= 365 * 86400.0:
        return "last_12m"
    if age_seconds <= 730 * 86400.0:
        return "months_12_24"
    return "older"


def _distilled_book_quality_tag(record):
    record = record if isinstance(record, dict) else {}
    explicit = str(record.get("orderbook_quality_tag") or record.get("book_quality_tag") or "").strip().upper()
    if explicit:
        return explicit
    spread = safe_float(record.get("spread"), None)
    liquidity = safe_float(record.get("liquidity_dollars", record.get("liquidity")), None)
    if spread is not None and spread <= 0.03 and (liquidity is None or liquidity >= 0.0):
        return "BOOK_GOOD"
    if spread is not None and spread >= 0.12:
        return "BOOK_WIDE"
    return "BOOK_UNKNOWN"


def _distilled_update_perf_stats(stats, pnl, outcome=None, timestamp_ts=None):
    stats = stats if isinstance(stats, dict) else {}
    pnl = safe_float(pnl, 0.0) or 0.0
    outcome = str(outcome or "").strip().lower()
    stats["count"] = int(stats.get("count", 0) or 0) + 1
    stats["pnl"] = round(safe_float(stats.get("pnl"), 0.0) + pnl, 6)
    if pnl > 0.0 or outcome in {"win", "won"}:
        stats["wins"] = int(stats.get("wins", 0) or 0) + 1
    elif pnl < 0.0 or outcome in {"loss", "lost"}:
        stats["losses"] = int(stats.get("losses", 0) or 0) + 1
    else:
        stats["breakevens"] = int(stats.get("breakevens", 0) or 0) + 1
    recent = stats.get("recent_pnls") if isinstance(stats.get("recent_pnls"), list) else []
    recent = _trim_list(recent + [round(pnl, 6)], 40)
    stats["recent_pnls"] = recent
    count = int(stats.get("count", 0) or 0)
    wins = int(stats.get("wins", 0) or 0)
    losses = int(stats.get("losses", 0) or 0)
    stats["avg_pnl"] = round(stats["pnl"] / max(count, 1), 6)
    stats["win_rate"] = round(wins / max(wins + losses, 1), 4) if wins or losses else None
    stats["recent_pnl"] = round(sum(safe_float(value, 0.0) or 0.0 for value in recent[-10:]), 6)
    if timestamp_ts is not None:
        stats["last_ts"] = max(safe_float(stats.get("last_ts"), 0.0) or 0.0, safe_float(timestamp_ts, 0.0) or 0.0)
    return stats


def _distilled_update_weighted_perf_stats(stats, pnl, outcome=None, timestamp_ts=None, weight=1.0):
    stats = _distilled_update_perf_stats(stats, pnl, outcome=outcome, timestamp_ts=timestamp_ts)
    weight = max(0.0, min(1.0, safe_float(weight, 1.0) or 0.0))
    pnl = safe_float(pnl, 0.0) or 0.0
    outcome = str(outcome or "").strip().lower()
    stats["weighted_count"] = round(safe_float(stats.get("weighted_count"), 0.0) + weight, 6)
    stats["weighted_pnl"] = round(safe_float(stats.get("weighted_pnl"), 0.0) + pnl * weight, 6)
    if pnl > 0.0 or outcome in {"win", "won"}:
        stats["weighted_wins"] = round(safe_float(stats.get("weighted_wins"), 0.0) + weight, 6)
    elif pnl < 0.0 or outcome in {"loss", "lost"}:
        stats["weighted_losses"] = round(safe_float(stats.get("weighted_losses"), 0.0) + weight, 6)
    else:
        stats["weighted_breakevens"] = round(safe_float(stats.get("weighted_breakevens"), 0.0) + weight, 6)
    weighted_count = safe_float(stats.get("weighted_count"), 0.0) or 0.0
    weighted_decided = (safe_float(stats.get("weighted_wins"), 0.0) or 0.0) + (safe_float(stats.get("weighted_losses"), 0.0) or 0.0)
    stats["weighted_avg_pnl"] = round((safe_float(stats.get("weighted_pnl"), 0.0) or 0.0) / max(weighted_count, 1e-9), 6)
    stats["weighted_win_rate"] = round((safe_float(stats.get("weighted_wins"), 0.0) or 0.0) / max(weighted_decided, 1e-9), 4) if weighted_decided > 0 else None
    recent = stats.get("recent_weighted_pnls") if isinstance(stats.get("recent_weighted_pnls"), list) else []
    recent = _trim_list(recent + [round(pnl * weight, 6)], 40)
    stats["recent_weighted_pnls"] = recent
    stats["recent_weighted_pnl"] = round(sum(safe_float(value, 0.0) or 0.0 for value in recent[-10:]), 6)
    return stats


def _distilled_bias_from_stats(stats, min_count=3):
    stats = stats if isinstance(stats, dict) else {}
    count = int(stats.get("count", 0) or 0)
    weighted_count = safe_float(stats.get("weighted_count"), 0.0) or 0.0
    sample_count = max(count, weighted_count)
    if sample_count < max(1, int(min_count or 1)):
        return 0.0
    avg_pnl = safe_float(stats.get("weighted_avg_pnl"), None)
    if avg_pnl is None:
        avg_pnl = safe_float(stats.get("avg_pnl"), 0.0) or 0.0
    recent_pnl = safe_float(stats.get("recent_weighted_pnl"), None)
    if recent_pnl is None:
        recent_pnl = safe_float(stats.get("recent_pnl"), 0.0) or 0.0
    win_rate = safe_float(stats.get("weighted_win_rate"), None)
    if win_rate is None:
        win_rate = safe_float(stats.get("win_rate"), None)
    sample_factor = min(1.0, max(0.0, sample_count / 18.0))
    pnl_signal = max(-1.0, min(1.0, avg_pnl * 20.0))
    recent_signal = max(-1.0, min(1.0, recent_pnl * 4.0))
    win_signal = 0.0 if win_rate is None else max(-1.0, min(1.0, (win_rate - 0.5) * 2.0))
    return round(((pnl_signal * 0.55) + (recent_signal * 0.25) + (win_signal * 0.20)) * sample_factor, 6)


def _distilled_history_record_key(record):
    record = record if isinstance(record, dict) else {}
    return "|".join(
        str(record.get(key) or "").strip()
        for key in ("ticker", "timestamp_ts", "side", "odds_bucket", "decision", "result")
    )


def _merge_distilled_records(existing_records, current_records, limit):
    merged = {}
    for record in list(existing_records or []) + list(current_records or []):
        if not isinstance(record, dict):
            continue
        key = _distilled_history_record_key(record)
        if not key.strip("|"):
            continue
        merged[key] = record
    return _trim_list(
        sorted(merged.values(), key=lambda item: safe_float(item.get("timestamp_ts"), 0.0) or 0.0),
        limit,
    )


def _sports_history_key(record):
    record = record if isinstance(record, dict) else {}
    explicit = str(record.get("history_key") or "").strip()
    if explicit:
        return explicit
    return "|".join(
        str(record.get(key) or "").strip()
        for key in ("ticker", "timestamp_ts", "settlement_result", "data_source")
    )


def _normalize_sports_history_market(record):
    record = record if isinstance(record, dict) else {}
    ticker = str(record.get("ticker") or "").strip()
    family = str(record.get("family") or record.get("series_ticker") or (ticker.split("-", 1)[0] if ticker else "UNKNOWN")).strip().upper() or "UNKNOWN"
    market_title = record.get("market_title") or record.get("title") or record.get("subtitle")
    timestamp = (
        record.get("timestamp")
        or record.get("settlement_time")
        or record.get("close_time")
        or record.get("expiration_time")
        or record.get("open_time")
        or record.get("collected_at_utc")
        or _now()
    )
    timestamp_ts = _distilled_iso_to_ts(timestamp)
    yes_bid = _distilled_normalized_price(record.get("yes_bid"))
    yes_ask = _distilled_normalized_price(record.get("yes_ask"))
    no_bid = _distilled_normalized_price(record.get("no_bid"))
    no_ask = _distilled_normalized_price(record.get("no_ask"))
    last_price = _distilled_normalized_price(record.get("last_price"))
    yes_price = _distilled_normalized_price(record.get("yes_price"))
    if yes_price is None:
        yes_price = last_price
    if yes_price is None and yes_bid is not None and yes_ask is not None:
        yes_price = round((yes_bid + yes_ask) / 2.0, 6)
    if yes_price is None:
        yes_price = yes_ask if yes_ask is not None else yes_bid
    no_price = _distilled_normalized_price(record.get("no_price"))
    if no_price is None and no_bid is not None and no_ask is not None:
        no_price = round((no_bid + no_ask) / 2.0, 6)
    if no_price is None and yes_price is not None:
        no_price = round(max(0.0, min(1.0, 1.0 - yes_price)), 6)
    spread = safe_float(record.get("spread"), None)
    if spread is None and yes_bid is not None and yes_ask is not None:
        spread = max(0.0, yes_ask - yes_bid)
    previous_price = _distilled_normalized_price(record.get("previous_price"))
    if previous_price is None:
        previous_price = _distilled_normalized_price(record.get("previous_yes_bid") or record.get("previous_yes_ask"))
    movement = safe_float(record.get("recent_price_movement"), None)
    if movement is None and previous_price is not None and yes_price is not None:
        movement = yes_price - previous_price
    volatility = safe_float(record.get("volatility"), None)
    if volatility is None and movement is not None:
        volatility = abs(movement)
    settlement_result = str(record.get("settlement_result") or record.get("result") or record.get("resolution") or "").strip().lower()
    yes_won = _distilled_result_to_yes_won(settlement_result)
    recency_weight = _distilled_recency_weight(timestamp_ts)
    sport_type = str(record.get("sport_type") or _distilled_sport_type(family=family, title=market_title, ticker=ticker)).strip().lower() or "sports"
    liquidity = safe_float(record.get("liquidity_dollars", record.get("liquidity")), None)
    volume = safe_float(record.get("volume"), None)
    normalized = {
        "history_key": None,
        "timestamp": timestamp,
        "timestamp_ts": round(timestamp_ts, 6),
        "ticker": ticker,
        "market_title": market_title,
        "sport_type": sport_type,
        "family": family,
        "category": "sports",
        "yes_price": yes_price,
        "no_price": no_price,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "spread": safe_float(spread, None),
        "liquidity": liquidity,
        "volume": volume,
        "implied_probability_bucket": _distilled_price_bucket(yes_price),
        "payout_asymmetry_bucket": _distilled_asymmetry_bucket(price=yes_price),
        "spread_bucket": _distilled_spread_bucket(spread),
        "liquidity_bucket": _distilled_liquidity_bucket(liquidity),
        "movement_bucket": _distilled_movement_bucket(movement),
        "volatility_bucket": _distilled_volatility_bucket(volatility),
        "volume_bucket": _distilled_volume_bucket(volume),
        "settlement_result": settlement_result or None,
        "yes_won": yes_won,
        "open_time": record.get("open_time"),
        "close_time": record.get("close_time"),
        "expiration_time": record.get("expiration_time"),
        "settlement_time": record.get("settlement_time"),
        "data_source": str(record.get("data_source") or record.get("raw_source") or "kalshi_api").strip(),
        "backfill_run_id": record.get("backfill_run_id"),
        "recency_weight": round(recency_weight, 4),
        "age_bucket": _distilled_age_bucket(timestamp_ts),
        "elite_signal_used": bool(record.get("elite_signal_used")),
        "elite_trader_handle": record.get("elite_trader_handle"),
    }
    normalized["history_key"] = _sports_history_key(normalized)
    return _json_safe({key: value for key, value in normalized.items() if value not in (None, "", [], {})})


def _sports_history_side_rows(record):
    yes_won = record.get("yes_won")
    if yes_won is None:
        return []
    rows = []
    for side, price, won in (
        ("yes", record.get("yes_price"), bool(yes_won)),
        ("no", record.get("no_price"), not bool(yes_won)),
    ):
        price_value = _distilled_normalized_price(price)
        if price_value is None:
            continue
        pnl = (1.0 - price_value) if won else -price_value
        rows.append(
            {
                "side": side,
                "price": price_value,
                "pnl": round(pnl, 6),
                "outcome": "win" if won else "loss",
                "odds_bucket": _distilled_price_bucket(price_value),
                "asymmetry_bucket": _distilled_asymmetry_bucket(price=price_value),
                "role": "underdog" if price_value < 0.50 else "favorite",
            }
        )
    return rows


def _sports_history_bucket_update(summaries, section, key, record, side_row=None):
    if not key:
        return
    summaries = summaries if isinstance(summaries, dict) else {}
    bucket = summaries.setdefault(section, {})
    stats = bucket.setdefault(str(key), {})
    stats["records"] = int(stats.get("records", 0) or 0) + 1
    stats["last_ts"] = max(safe_float(stats.get("last_ts"), 0.0) or 0.0, safe_float(record.get("timestamp_ts"), 0.0) or 0.0)
    stats["recency_weight_total"] = round((safe_float(stats.get("recency_weight_total"), 0.0) or 0.0) + (safe_float(record.get("recency_weight"), 0.0) or 0.0), 6)
    if side_row:
        _distilled_update_weighted_perf_stats(
            stats,
            side_row.get("pnl"),
            outcome=side_row.get("outcome"),
            timestamp_ts=record.get("timestamp_ts"),
            weight=record.get("recency_weight"),
        )


def _sports_history_update_summaries(summaries, record):
    summaries = summaries if isinstance(summaries, dict) else {}
    side_rows = _sports_history_side_rows(record)
    if not side_rows:
        for section, key in (
            ("families", record.get("family")),
            ("sports", record.get("sport_type")),
            ("liquidity_buckets", record.get("liquidity_bucket")),
            ("spread_buckets", record.get("spread_bucket")),
            ("volume_buckets", record.get("volume_bucket")),
            ("movement_buckets", record.get("movement_bucket")),
            ("volatility_buckets", record.get("volatility_bucket")),
            ("recency_buckets", record.get("age_bucket")),
            ("sport_family_volatility", f"{record.get('family')}|{record.get('volatility_bucket')}"),
        ):
            _sports_history_bucket_update(summaries, section, key, record)
        return summaries
    for side_row in side_rows:
        family = record.get("family")
        sport = record.get("sport_type")
        odds_bucket = side_row.get("odds_bucket")
        asymmetry_bucket = side_row.get("asymmetry_bucket")
        for section, key in (
            ("families", family),
            ("sports", sport),
            ("odds_buckets", odds_bucket),
            ("asymmetry_buckets", asymmetry_bucket),
            ("liquidity_buckets", record.get("liquidity_bucket")),
            ("spread_buckets", record.get("spread_bucket")),
            ("volume_buckets", record.get("volume_bucket")),
            ("movement_buckets", record.get("movement_bucket")),
            ("volatility_buckets", record.get("volatility_bucket")),
            ("side_buckets", side_row.get("side")),
            ("side_odds_buckets", f"{side_row.get('side')}|{odds_bucket}"),
            ("family_odds_buckets", f"{family}|{odds_bucket}"),
            ("family_asymmetry_buckets", f"{family}|{asymmetry_bucket}"),
            ("sport_asymmetry_buckets", f"{sport}|{asymmetry_bucket}"),
            ("underdog_favorite", side_row.get("role")),
            ("recency_buckets", record.get("age_bucket")),
            ("sport_family_volatility", f"{family}|{record.get('volatility_bucket')}"),
        ):
            _sports_history_bucket_update(summaries, section, key, record, side_row=side_row)
        if record.get("elite_signal_used") and record.get("elite_trader_handle"):
            handle = str(record.get("elite_trader_handle") or "").strip()
            _sports_history_bucket_update(summaries, "elite_traders", handle, record, side_row=side_row)
            _sports_history_bucket_update(summaries, "elite_trader_families", f"{handle}|{family}", record, side_row=side_row)
            _sports_history_bucket_update(summaries, "elite_trader_odds", f"{handle}|{odds_bucket}", record, side_row=side_row)
    return summaries


def _sports_history_rebuild_summaries(records):
    sports_history = _ensure_sports_history_defaults({"records": []})
    summaries = sports_history["summaries"]
    for record in records or []:
        if isinstance(record, dict):
            _sports_history_update_summaries(summaries, record)
    return summaries


def _merge_sports_history(existing, current):
    existing = _ensure_sports_history_defaults(existing)
    current = _ensure_sports_history_defaults(current)
    merged_records = {}
    for record in list(existing.get("records") or []) + list(current.get("records") or []):
        if not isinstance(record, dict):
            continue
        key = _sports_history_key(record)
        if key.strip("|"):
            merged_records[key] = record
    records = _trim_list(
        sorted(merged_records.values(), key=lambda item: safe_float(item.get("timestamp_ts"), 0.0) or 0.0),
        DISTILLED_SPORTS_HISTORY_LIMIT,
    )
    seen = {}
    for source in (existing.get("seen_keys"), current.get("seen_keys")):
        if isinstance(source, dict):
            for key, value in source.items():
                seen[str(key)] = safe_float(value, 0.0) or 0.0
    for record in records:
        seen[_sports_history_key(record)] = safe_float(record.get("timestamp_ts"), 0.0) or 0.0
    if len(seen) > DISTILLED_SPORTS_HISTORY_SEEN_LIMIT:
        seen = dict(sorted(seen.items(), key=lambda item: safe_float(item[1], 0.0) or 0.0, reverse=True)[:DISTILLED_SPORTS_HISTORY_SEEN_LIMIT])
    return {
        "version": max(int(existing.get("version", 1) or 1), int(current.get("version", 1) or 1)),
        "records": records,
        "seen_keys": seen,
        "summaries": _sports_history_rebuild_summaries(records),
        "audit": {},
    }


def _merge_distilled_learning_for_persist(existing, current):
    existing = _ensure_distilled_learning_defaults(existing)
    current = _ensure_distilled_learning_defaults(current)
    merged = deepcopy(current)
    merged["observations"] = _merge_distilled_records(existing.get("observations"), current.get("observations"), DISTILLED_OBSERVATION_LIMIT)
    merged["outcomes"] = _merge_distilled_records(existing.get("outcomes"), current.get("outcomes"), DISTILLED_OUTCOME_LIMIT)
    last_by_key = {}
    for source in (existing.get("last_observation_by_key"), current.get("last_observation_by_key")):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            last_by_key[str(key)] = max(safe_float(last_by_key.get(str(key)), 0.0) or 0.0, safe_float(value, 0.0) or 0.0)
    merged["last_observation_by_key"] = last_by_key
    merged["sports_history"] = _merge_sports_history(existing.get("sports_history"), current.get("sports_history"))
    return _ensure_distilled_learning_defaults(merged)


def _weighted_average(items, key, fallback=0.5, decay_window=86400.0):
    if not isinstance(items, list) or not items:
        return safe_float(fallback, 0.5)
    now = _now()
    numerator = 0.0
    denominator = 0.0
    for item in items:
        if not isinstance(item, dict):
            continue
        value = safe_float(item.get(key), fallback)
        age = max(0.0, now - safe_float(item.get("ts"), now))
        weight = math.exp(-age / max(decay_window, 1.0))
        numerator += value * weight
        denominator += weight
    if denominator <= 0.0:
        return safe_float(fallback, 0.5)
    return numerator / denominator


@dataclass
class AIONObservation:
    ticker: str
    family: str
    market_type: str
    spread_quality: float = 0.0
    stability_score: float = 0.0
    near_pass: bool = False
    improving_edge: bool = False
    decaying_edge: bool = False
    quote_stability: float = 0.0
    directional_consistency: float = 0.0
    volatility_regime: float = 0.0
    execution_outcome: str = "unknown"
    outcome_strength: float = 0.0
    blocker_reason: str = "unknown"
    timestamp: float = 0.0


class AIONPersistentIntelligence:
    def __init__(self, state_file=None, history_limit=500, ttl_seconds=7 * 86400.0, enabled=True):
        ensure_runtime_layout()
        self.state_file = str(state_file or runtime_memory_file("aion_persistent_intelligence.json"))
        self.history_limit = max(25, int(history_limit or 500))
        self.ttl_seconds = max(3600.0, safe_float(ttl_seconds, 7 * 86400.0))
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        self.ticker_profiles = {}
        self.family_profiles = {}
        self.distilled_learning = _ensure_distilled_learning_defaults({})
        self.sports_history_store = None
        if AIONSportsHistoryStore is not None:
            try:
                self.sports_history_store = AIONSportsHistoryStore(log_fn=builtins.print)
            except Exception as exc:
                builtins.print("[AION-SQLITE-AUDIT] " + json.dumps({"reason": "sqlite_store_unavailable", "error": exc.__class__.__name__, "action": "continue_without_sqlite"}, sort_keys=True))
        self.last_summary_ts = 0.0
        self.summary_interval_seconds = 120.0
        self.load()
        builtins.print("[AION-MODULE-LOAD] " + json.dumps({"module": "memory.aion_persistent_intelligence", "state_file": self.state_file}, sort_keys=True))

    def _profile(self, container, key, kind):
        if key not in container or not isinstance(container.get(key), dict):
            container[key] = _ensure_profile_defaults(key=key, kind=kind)
        profile = container[key]
        profile.setdefault("key", key)
        profile.setdefault("kind", kind)
        profile.setdefault("recent_observations", [])
        profile.setdefault("execution_outcome_history", [])
        profile.setdefault("spread_quality_history", [])
        profile.setdefault("stability_history", [])
        profile.setdefault("quote_stability_history", [])
        profile.setdefault("directional_consistency_history", [])
        profile.setdefault("volatility_regime_history", [])
        profile.setdefault("execution_outcome_memory", {})
        profile.setdefault("last_summary", {})
        return profile

    def _prune_profile(self, profile):
        now = _now()
        recent = []
        for entry in profile.get("recent_observations", []) or []:
            if not isinstance(entry, dict):
                continue
            age = now - safe_float(entry.get("ts"), now)
            if age <= self.ttl_seconds:
                recent.append(entry)
        profile["recent_observations"] = _trim_list(recent, self.history_limit)
        for history_key in ("spread_quality_history", "stability_history", "quote_stability_history", "directional_consistency_history", "volatility_regime_history", "execution_outcome_history"):
            profile[history_key] = _trim_list(profile.get(history_key, []), self.history_limit)
        return profile

    def _score_profile(self, profile):
        observations = profile.get("recent_observations", []) or []
        if not observations:
            return {
                "persistence_score": 0.5,
                "near_pass_frequency": 0.0,
                "quote_stability_trend": 0.5,
                "edge_behavior_trend": 0.5,
                "volatility_tendency": 0.5,
                "directional_consistency": 0.5,
                "execution_outcome_memory": dict(profile.get("execution_outcome_memory", {}) or {}),
                "recovery_velocity": 0.0,
                "decay_velocity": 0.0,
            }
        near_pass_frequency = safe_ratio(sum(1 for item in observations if item.get("near_pass")), len(observations), 0.0)
        improving_edge_rate = safe_ratio(sum(1 for item in observations if item.get("improving_edge")), len(observations), 0.0)
        decaying_edge_rate = safe_ratio(sum(1 for item in observations if item.get("decaying_edge")), len(observations), 0.0)
        quote_stability_trend = _weighted_average(observations, "quote_stability", fallback=0.5, decay_window=self.ttl_seconds)
        directional_consistency = _weighted_average(observations, "directional_consistency", fallback=0.5, decay_window=self.ttl_seconds)
        volatility_tendency = _weighted_average(observations, "volatility_regime", fallback=0.5, decay_window=self.ttl_seconds)
        spread_quality = _weighted_average(observations, "spread_quality", fallback=0.5, decay_window=self.ttl_seconds)
        stability_score = _weighted_average(observations, "stability_score", fallback=0.5, decay_window=self.ttl_seconds)
        recovery_velocity = safe_ratio(sum(1 for item in observations if item.get("improving_edge") and not item.get("decaying_edge")), len(observations), 0.0)
        decay_velocity = safe_ratio(sum(1 for item in observations if item.get("decaying_edge")), len(observations), 0.0)
        persistence_score = clamp(
            0.30
            + near_pass_frequency * 0.12
            + improving_edge_rate * 0.18
            - decaying_edge_rate * 0.15
            + quote_stability_trend * 0.12
            + directional_consistency * 0.10
            + spread_quality * 0.10
            + stability_score * 0.08
            + recovery_velocity * 0.06
            - decay_velocity * 0.05,
            0.0,
            1.0,
        )
        return {
            "persistence_score": round(persistence_score, 4),
            "near_pass_frequency": round(near_pass_frequency, 4),
            "quote_stability_trend": round(quote_stability_trend, 4),
            "edge_behavior_trend": round((improving_edge_rate + spread_quality + stability_score) / 3.0, 4),
            "volatility_tendency": round(volatility_tendency, 4),
            "directional_consistency": round(directional_consistency, 4),
            "execution_outcome_memory": dict(profile.get("execution_outcome_memory", {}) or {}),
            "recovery_velocity": round(recovery_velocity, 4),
            "decay_velocity": round(decay_velocity, 4),
        }

    def _record(self, kind, key, observation):
        observation = observation if isinstance(observation, dict) else {}
        container = self.ticker_profiles if kind == "ticker" else self.family_profiles
        profile = self._profile(container, key, kind)
        now = safe_float(observation.get("timestamp"), _now())
        profile["first_seen_time"] = profile.get("first_seen_time") or now
        profile["last_seen_time"] = now
        profile["total_observations"] = int(profile.get("total_observations", 0) or 0) + 1
        profile["near_pass_count"] = int(profile.get("near_pass_count", 0) or 0) + (1 if observation.get("near_pass") else 0)
        profile["improving_edge_count"] = int(profile.get("improving_edge_count", 0) or 0) + (1 if observation.get("improving_edge") else 0)
        profile["decaying_edge_count"] = int(profile.get("decaying_edge_count", 0) or 0) + (1 if observation.get("decaying_edge") else 0)
        profile["recovery_count"] = int(profile.get("recovery_count", 0) or 0) + (1 if observation.get("improving_edge") and not observation.get("decaying_edge") else 0)
        profile.setdefault("spread_quality_history", []).append({"ts": now, "value": safe_float(observation.get("spread_quality"), 0.0)})
        profile.setdefault("stability_history", []).append({"ts": now, "value": safe_float(observation.get("stability_score"), 0.0)})
        profile.setdefault("quote_stability_history", []).append({"ts": now, "value": safe_float(observation.get("quote_stability"), 0.0)})
        profile.setdefault("directional_consistency_history", []).append({"ts": now, "value": safe_float(observation.get("directional_consistency"), 0.0)})
        profile.setdefault("volatility_regime_history", []).append({"ts": now, "value": safe_float(observation.get("volatility_regime"), 0.0)})
        profile.setdefault("execution_outcome_history", []).append({"ts": now, "value": str(observation.get("execution_outcome") or "unknown")})
        outcome = str(observation.get("execution_outcome") or "unknown").strip().lower()
        outcome_memory = profile.setdefault("execution_outcome_memory", {})
        outcome_memory[outcome] = int(outcome_memory.get(outcome, 0) or 0) + 1
        profile["recent_observations"] = _trim_list(profile.get("recent_observations", []) + [observation], self.history_limit)
        profile = self._prune_profile(profile)
        score_summary = self._score_profile(profile)
        profile.update(score_summary)
        profile["last_summary"] = deepcopy(score_summary)
        profile["last_summary"]["key"] = key
        profile["last_summary"]["kind"] = kind
        profile["last_summary"]["total_observations"] = profile.get("total_observations", 0)
        profile["last_summary"]["near_pass_count"] = profile.get("near_pass_count", 0)
        profile["last_summary"]["improving_edge_count"] = profile.get("improving_edge_count", 0)
        profile["last_summary"]["decaying_edge_count"] = profile.get("decaying_edge_count", 0)
        profile["last_summary"]["recovery_count"] = profile.get("recovery_count", 0)
        profile["last_summary"]["last_seen_time"] = profile.get("last_seen_time")
        return deepcopy(profile["last_summary"])

    def record_ticker_behavior(self, ticker, family, market_type="sports", **fields):
        key = f"{market_type}:{ticker}"
        observation = {
            "ticker": ticker,
            "family": family,
            "market_type": market_type,
            "spread_quality": fields.get("spread_quality", fields.get("spread_stability", 0.0)),
            "stability_score": fields.get("stability_score", fields.get("quote_stability", 0.0)),
            "near_pass": bool(fields.get("near_pass")),
            "improving_edge": bool(fields.get("improving_edge")),
            "decaying_edge": bool(fields.get("decaying_edge")),
            "quote_stability": fields.get("quote_stability", fields.get("quote_stability_trend", 0.0)),
            "directional_consistency": fields.get("directional_consistency", fields.get("directional_bias", 0.0)),
            "volatility_regime": fields.get("volatility_regime", fields.get("volatility", 0.0)),
            "execution_outcome": fields.get("execution_outcome", "unknown"),
            "outcome_strength": fields.get("outcome_strength", 0.0),
            "blocker_reason": fields.get("blocker_reason", "unknown"),
            "timestamp": fields.get("timestamp", _now()),
        }
        with self._lock:
            summary = self._record("ticker", key, observation)
            summary.update({"ticker": ticker, "family": family, "market_type": market_type})
            builtins.print("[AION-TICKER-BEHAVIOR] " + json.dumps(summary, sort_keys=True, default=str))
            return summary

    def record_family_profile(self, family, market_type="sports", **fields):
        key = f"{market_type}:{family}"
        observation = {
            "ticker": fields.get("ticker"),
            "family": family,
            "market_type": market_type,
            "spread_quality": fields.get("spread_quality", fields.get("spread_stability", 0.0)),
            "stability_score": fields.get("stability_score", fields.get("quote_stability", 0.0)),
            "near_pass": bool(fields.get("near_pass")),
            "improving_edge": bool(fields.get("improving_edge")),
            "decaying_edge": bool(fields.get("decaying_edge")),
            "quote_stability": fields.get("quote_stability", fields.get("quote_stability_trend", 0.0)),
            "directional_consistency": fields.get("directional_consistency", fields.get("directional_bias", 0.0)),
            "volatility_regime": fields.get("volatility_regime", fields.get("volatility", 0.0)),
            "execution_outcome": fields.get("execution_outcome", "unknown"),
            "outcome_strength": fields.get("outcome_strength", 0.0),
            "blocker_reason": fields.get("blocker_reason", "unknown"),
            "timestamp": fields.get("timestamp", _now()),
        }
        with self._lock:
            summary = self._record("family", key, observation)
            summary.update({"family": family, "market_type": market_type})
            builtins.print("[AION-FAMILY-PROFILE] " + json.dumps(summary, sort_keys=True, default=str))
            return summary

    def record_execution_placeholder(self, ticker, family, market_type="sports", outcome="unknown", **fields):
        return self.record_ticker_behavior(
            ticker,
            family,
            market_type=market_type,
            execution_outcome=outcome,
            **fields,
        )

    def summarize_ticker(self, ticker, market_type="sports"):
        key = f"{market_type}:{ticker}"
        with self._lock:
            profile = self.ticker_profiles.get(key)
            if not isinstance(profile, dict):
                return {}
            self._prune_profile(profile)
            summary = self._score_profile(profile)
            summary.update({"ticker": ticker, "market_type": market_type, "family": profile.get("recent_observations", [{}])[-1].get("family") if profile.get("recent_observations") else None})
            return summary

    def summarize_family(self, family, market_type="sports"):
        key = f"{market_type}:{family}"
        with self._lock:
            profile = self.family_profiles.get(key)
            if not isinstance(profile, dict):
                return {}
            self._prune_profile(profile)
            summary = self._score_profile(profile)
            summary.update({"family": family, "market_type": market_type})
            return summary

    def build_overall_summary(self):
        with self._lock:
            ticker_profiles = [profile for profile in self.ticker_profiles.values() if isinstance(profile, dict)]
            family_profiles = [profile for profile in self.family_profiles.values() if isinstance(profile, dict)]
            distilled_audit = self.audit_distilled_learning()
            if not ticker_profiles and not family_profiles:
                return {
                    "module": "aion_persistent_intelligence",
                    "ticker_profile_count": 0,
                    "family_profile_count": 0,
                    "strongest_ticker": None,
                    "strongest_family": None,
                    "strongest_score": 0.0,
                    "average_persistence_score": 0.0,
                    "average_near_pass_frequency": 0.0,
                    "distilled_memory": distilled_audit,
                    "timestamp": _now(),
                }
            best_ticker_key, best_ticker = max(
                self.ticker_profiles.items(),
                key=lambda item: safe_float(item[1].get("persistence_score"), 0.0),
            ) if ticker_profiles else (None, {})
            best_family_key, best_family = max(
                self.family_profiles.items(),
                key=lambda item: safe_float(item[1].get("persistence_score"), 0.0),
            ) if family_profiles else (None, {})
            average_persistence = 0.0
            average_near_pass = 0.0
            if ticker_profiles:
                average_persistence = sum(safe_float(profile.get("persistence_score"), 0.0) for profile in ticker_profiles) / max(len(ticker_profiles), 1)
                average_near_pass = sum(safe_float(profile.get("near_pass_count"), 0.0) for profile in ticker_profiles) / max(len(ticker_profiles), 1)
            summary = {
                "module": "aion_persistent_intelligence",
                "ticker_profile_count": int(len(ticker_profiles)),
                "family_profile_count": int(len(family_profiles)),
                "strongest_ticker": str(best_ticker_key or "").split(":", 1)[-1] if best_ticker_key else None,
                "strongest_family": str(best_family_key or "").split(":", 1)[-1] if best_family_key else None,
                "strongest_score": round(max(safe_float(best_ticker.get("persistence_score"), 0.0), safe_float(best_family.get("persistence_score"), 0.0)), 4),
                "average_persistence_score": round(average_persistence, 4),
                "average_near_pass_frequency": round(average_near_pass, 4),
                "distilled_memory": distilled_audit,
                "timestamp": _now(),
            }
            return summary

    def _normalize_distilled_record(self, record, *, record_type):
        record = record if isinstance(record, dict) else {}
        timestamp = record.get("timestamp") or record.get("timestamp_utc") or record.get("observed_at") or _now()
        timestamp_ts = _distilled_iso_to_ts(timestamp)
        ticker = str(record.get("ticker") or "").strip()
        family = str(record.get("family") or record.get("category") or "UNKNOWN").strip().upper() or "UNKNOWN"
        sports_tags = record.get("sports_tags") or record.get("sports_edge_tags") or []
        if isinstance(sports_tags, str):
            sports_tags = [tag.strip() for tag in sports_tags.replace(";", ",").split(",") if tag.strip()]
        elif isinstance(sports_tags, (tuple, set)):
            sports_tags = list(sports_tags)
        sports_tags = [str(tag).strip() for tag in sports_tags if str(tag or "").strip()][:12] if isinstance(sports_tags, list) else []
        structural_tags = record.get("structural_tags") or record.get("structural_edge_tags") or []
        if isinstance(structural_tags, str):
            structural_tags = [tag.strip() for tag in structural_tags.replace(";", ",").split(",") if tag.strip()]
        elif isinstance(structural_tags, (tuple, set)):
            structural_tags = list(structural_tags)
        structural_tags = [str(tag).strip() for tag in structural_tags if str(tag or "").strip()][:16] if isinstance(structural_tags, list) else []
        structural_pattern_keys = record.get("structural_pattern_keys") or record.get("structural_patterns") or []
        if isinstance(structural_pattern_keys, str):
            structural_pattern_keys = [key.strip() for key in structural_pattern_keys.replace(";", ",").split(",") if key.strip()]
        elif isinstance(structural_pattern_keys, (tuple, set)):
            structural_pattern_keys = list(structural_pattern_keys)
        structural_pattern_keys = [str(key).strip() for key in structural_pattern_keys if str(key or "").strip()][:24] if isinstance(structural_pattern_keys, list) else []
        elite_handle = str(record.get("elite_trader_handle") or record.get("trader_handle") or "").strip()
        price = record.get("entry_price") if record.get("entry_price") is not None else record.get("price")
        structural_edge_score = safe_float(record.get("structural_edge_score", record.get("STRUCTURAL_EDGE_SCORE")), None)
        cross_market_edge_score = safe_float(record.get("cross_market_edge_score", record.get("CROSS_MARKET_EDGE_SCORE")), None)
        elite_edge_confidence = safe_float(record.get("elite_edge_confidence", record.get("ELITE_EDGE_CONFIDENCE")), None)
        normalized = {
            "timestamp": timestamp,
            "timestamp_ts": round(timestamp_ts, 6),
            "record_type": record_type,
            "ticker": ticker or None,
            "market_title": record.get("market_title") or record.get("title"),
            "category": str(record.get("category") or "unknown").strip().lower() or "unknown",
            "family": family,
            "side": str(record.get("side") or "unknown").strip().lower(),
            "yes_price": safe_float(record.get("yes_price", record.get("yes_ask")), None),
            "no_price": safe_float(record.get("no_price", record.get("no_ask")), None),
            "price": safe_float(price, None),
            "entry_price": safe_float(record.get("entry_price"), None),
            "exit_price": safe_float(record.get("exit_price"), None),
            "spread": safe_float(record.get("spread"), None),
            "liquidity_dollars": safe_float(record.get("liquidity_dollars", record.get("liquidity")), None),
            "depth_summary": record.get("depth_summary"),
            "recent_price_movement": safe_float(record.get("recent_price_movement", record.get("price_delta")), None),
            "orderbook_quality": safe_float(record.get("orderbook_quality"), None),
            "orderbook_quality_tag": _distilled_book_quality_tag(record),
            "sports_data_tag": record.get("sports_data_tag") or record.get("sports_family_activity_level"),
            "sports_tags": sports_tags,
            "structural_tags": structural_tags,
            "structural_pattern_keys": structural_pattern_keys,
            "structural_edge_score": structural_edge_score,
            "cross_market_edge_score": cross_market_edge_score,
            "elite_edge_confidence": elite_edge_confidence,
            "structural_edge_bucket": record.get("structural_edge_bucket") or _distilled_score_bucket(structural_edge_score, prefix="structural"),
            "cross_market_bucket": record.get("cross_market_bucket") or _distilled_score_bucket(cross_market_edge_score, prefix="cross"),
            "elite_edge_bucket": record.get("elite_edge_bucket") or _distilled_score_bucket(elite_edge_confidence, prefix="elite_edge"),
            "elite_edge_tier": record.get("elite_edge_tier"),
            "sports_edge_score": safe_float(record.get("sports_edge_score", record.get("sports_score_at_entry")), None),
            "asymmetry_score": safe_float(record.get("asymmetry_score", record.get("sports_payout_multiple")), None),
            "odds_bucket": record.get("odds_bucket") or _distilled_price_bucket(price),
            "asymmetry_bucket": record.get("asymmetry_bucket") or _distilled_asymmetry_bucket(price=price, payout_multiple=record.get("sports_payout_multiple")),
            "liquidity_bucket": record.get("liquidity_bucket") or _distilled_liquidity_bucket(record.get("liquidity_dollars", record.get("liquidity"))),
            "spread_bucket": record.get("spread_bucket") or _distilled_spread_bucket(record.get("spread")),
            "sport_type": str(record.get("sport_type") or _distilled_sport_type(family=family, title=record.get("market_title") or record.get("title"), ticker=ticker)).strip().lower(),
            "elite_signal_used": bool(record.get("elite_signal_used") or record.get("elite_public_signal_match")),
            "elite_trader_handle": elite_handle or None,
            "elite_boost_used": safe_float(record.get("elite_boost_used", record.get("elite_public_signal_boost")), None),
            "candidate_score": safe_float(record.get("candidate_score", record.get("confidence")), None),
            "score_at_entry": safe_float(record.get("score_at_entry", record.get("sports_score_at_entry")), None),
            "position_size": safe_float(record.get("position_size", record.get("contracts")), None),
            "decision": str(record.get("decision") or record.get("action") or record.get("entry_reason") or "unknown").strip().lower(),
            "sizing_reason": record.get("sizing_reason"),
            "pnl": safe_float(record.get("pnl", record.get("pnl_dollars")), None),
            "result": str(record.get("result") or record.get("outcome") or "unknown").strip().lower(),
        }
        return _json_safe({key: value for key, value in normalized.items() if value not in (None, "", [], {})})

    def record_distilled_observation(self, record, *, throttle_seconds=900.0, max_observations=1200):
        if not self.enabled:
            return {"ok": False, "reason": "disabled"}
        normalized = self._normalize_distilled_record(record, record_type="observation")
        ticker = str(normalized.get("ticker") or "").strip()
        if not ticker:
            return {"ok": False, "reason": "missing_ticker"}
        key = "|".join(
            [
                ticker,
                str(normalized.get("side") or "unknown"),
                str(normalized.get("decision") or "unknown"),
                str(normalized.get("odds_bucket") or "unknown"),
            ]
        )
        timestamp_ts = safe_float(normalized.get("timestamp_ts"), _now()) or _now()
        with self._lock:
            memory = _ensure_distilled_learning_defaults(self.distilled_learning)
            last_by_key = memory.setdefault("last_observation_by_key", {})
            previous_ts = safe_float(last_by_key.get(key), None)
            if previous_ts is not None and timestamp_ts - previous_ts < max(60.0, safe_float(throttle_seconds, 900.0)):
                self.distilled_learning = memory
                return {"ok": True, "skipped": True, "reason": "throttled", "key": key}
            last_by_key[key] = timestamp_ts
            if len(last_by_key) > 600:
                keep = sorted(last_by_key.items(), key=lambda item: safe_float(item[1], 0.0), reverse=True)[:600]
                memory["last_observation_by_key"] = dict(keep)
            observations = memory.get("observations") if isinstance(memory.get("observations"), list) else []
            memory["observations"] = _trim_list(observations + [normalized], max_observations)
            summaries = memory.setdefault("summaries", {})
            family = str(normalized.get("family") or "UNKNOWN")
            families = summaries.setdefault("families", {})
            family_stats = families.setdefault(family, {})
            family_stats["observations"] = int(family_stats.get("observations", 0) or 0) + 1
            family_stats["last_observation_ts"] = timestamp_ts
            categories = summaries.setdefault("categories", {})
            category = str(normalized.get("category") or "unknown")
            category_stats = categories.setdefault(category, {})
            category_stats["observations"] = int(category_stats.get("observations", 0) or 0) + 1
            category_stats["last_observation_ts"] = timestamp_ts
            self.distilled_learning = memory
            self.persist()
        return {"ok": True, "skipped": False, "key": key, "record": normalized}

    def record_distilled_outcome(self, record, *, max_outcomes=1200):
        if not self.enabled:
            return {"ok": False, "reason": "disabled"}
        normalized = self._normalize_distilled_record(record, record_type="outcome")
        ticker = str(normalized.get("ticker") or "").strip()
        if not ticker:
            return {"ok": False, "reason": "missing_ticker"}
        timestamp_ts = safe_float(normalized.get("timestamp_ts"), _now()) or _now()
        pnl = safe_float(normalized.get("pnl"), 0.0) or 0.0
        outcome = normalized.get("result") or "unknown"
        with self._lock:
            memory = _ensure_distilled_learning_defaults(self.distilled_learning)
            outcomes = memory.get("outcomes") if isinstance(memory.get("outcomes"), list) else []
            memory["outcomes"] = _trim_list(outcomes + [normalized], max_outcomes)
            summaries = memory.setdefault("summaries", {})

            def update_bucket(section, key):
                if not key:
                    return None
                bucket = summaries.setdefault(section, {})
                stats = bucket.setdefault(str(key), {})
                bucket[str(key)] = _distilled_update_perf_stats(stats, pnl, outcome=outcome, timestamp_ts=timestamp_ts)
                return bucket[str(key)]

            family_stats = update_bucket("families", normalized.get("family") or "UNKNOWN")
            update_bucket("categories", normalized.get("category") or "unknown")
            update_bucket("odds_buckets", normalized.get("odds_bucket") or "unknown")
            update_bucket("book_quality", normalized.get("orderbook_quality_tag") or "BOOK_UNKNOWN")
            update_bucket("side_buckets", normalized.get("side") or "unknown")
            update_bucket("asymmetry_buckets", normalized.get("asymmetry_bucket") or "unknown")
            update_bucket("liquidity_buckets", normalized.get("liquidity_bucket") or "unknown")
            update_bucket("spread_buckets", normalized.get("spread_bucket") or "unknown")
            update_bucket("sport_types", normalized.get("sport_type") or "sports")
            for tag in normalized.get("sports_tags") or []:
                update_bucket("sports_tags", tag)
            for tag in normalized.get("structural_tags") or []:
                update_bucket("structural_tags", tag)
            for pattern_key in normalized.get("structural_pattern_keys") or []:
                update_bucket("structural_patterns", pattern_key)
            update_bucket("structural_edge_buckets", normalized.get("structural_edge_bucket") or "structural_unknown")
            update_bucket("cross_market_buckets", normalized.get("cross_market_bucket") or "cross_unknown")
            update_bucket("elite_edge_buckets", normalized.get("elite_edge_bucket") or "elite_edge_unknown")
            if normalized.get("elite_signal_used") and normalized.get("elite_trader_handle"):
                handle = str(normalized.get("elite_trader_handle") or "").strip()
                update_bucket("elite_traders", handle)
                update_bucket("elite_trader_categories", f"{handle}|{normalized.get('category') or 'unknown'}")
            memory["audit"] = self.audit_distilled_learning(memory=memory)
            self.distilled_learning = memory
            self.persist()
        return {"ok": True, "record": normalized, "family_stats": family_stats or {}}

    def record_sports_history_market(self, record, *, max_records=DISTILLED_SPORTS_HISTORY_LIMIT, persist=True):
        if not self.enabled:
            return {"ok": False, "reason": "disabled"}
        normalized = _normalize_sports_history_market(record)
        ticker = str(normalized.get("ticker") or "").strip()
        if not ticker:
            return {"ok": False, "reason": "missing_ticker"}
        key = _sports_history_key(normalized)
        timestamp_ts = safe_float(normalized.get("timestamp_ts"), _now()) or _now()
        with self._lock:
            memory = _ensure_distilled_learning_defaults(self.distilled_learning)
            sports_history = _ensure_sports_history_defaults(memory.get("sports_history"))
            seen = sports_history.setdefault("seen_keys", {})
            if key in seen:
                self.distilled_learning = memory
                return {"ok": True, "skipped": True, "reason": "duplicate", "key": key}
            seen[key] = timestamp_ts
            if len(seen) > DISTILLED_SPORTS_HISTORY_SEEN_LIMIT:
                sports_history["seen_keys"] = dict(
                    sorted(seen.items(), key=lambda item: safe_float(item[1], 0.0) or 0.0, reverse=True)[:DISTILLED_SPORTS_HISTORY_SEEN_LIMIT]
                )
            records = sports_history.get("records") if isinstance(sports_history.get("records"), list) else []
            records.append(normalized)
            max_records = max(100, min(DISTILLED_SPORTS_HISTORY_LIMIT, int(max_records or DISTILLED_SPORTS_HISTORY_LIMIT)))
            trimmed = len(records) > max_records
            records = _trim_list(records, max_records)
            sports_history["records"] = records
            if trimmed:
                sports_history["summaries"] = _sports_history_rebuild_summaries(records)
            else:
                sports_history["summaries"] = _sports_history_update_summaries(
                    sports_history.get("summaries") if isinstance(sports_history.get("summaries"), dict) else {},
                    normalized,
                )
            sports_history["audit"] = self.audit_distilled_learning(memory={**memory, "sports_history": sports_history})
            memory["sports_history"] = sports_history
            self.distilled_learning = memory
            if persist:
                self.persist()
        return {"ok": True, "skipped": False, "key": key, "record": normalized}

    def get_distilled_learning_signal(
        self,
        *,
        family=None,
        category=None,
        sports_tags=None,
        odds_bucket=None,
        book_quality_tag=None,
        elite_trader_handle=None,
        side=None,
        asymmetry_bucket=None,
        liquidity_bucket=None,
        spread_bucket=None,
        sport_type=None,
        structural_tags=None,
        structural_pattern_keys=None,
        cross_market_bucket=None,
        elite_edge_bucket=None,
    ):
        memory = _ensure_distilled_learning_defaults(self.distilled_learning)
        summaries = memory.get("summaries") if isinstance(memory.get("summaries"), dict) else {}
        sports_history = _ensure_sports_history_defaults(memory.get("sports_history"))
        sports_summaries = sports_history.get("summaries") if isinstance(sports_history.get("summaries"), dict) else {}
        sports_tags = sports_tags if isinstance(sports_tags, list) else []
        structural_tags = structural_tags if isinstance(structural_tags, list) else []
        structural_pattern_keys = structural_pattern_keys if isinstance(structural_pattern_keys, list) else []
        components = []

        def add_component(label, section, key, weight, min_count=3, summary_source=None):
            if not key:
                return
            source = summary_source if isinstance(summary_source, dict) else summaries
            bucket = source.get(section) if isinstance(source.get(section), dict) else {}
            stats = bucket.get(str(key)) if isinstance(bucket.get(str(key)), dict) else {}
            bias = _distilled_bias_from_stats(stats, min_count=min_count)
            if bias:
                components.append(
                    {
                        "label": label,
                        "key": str(key),
                        "bias": bias,
                        "weight": weight,
                        "count": stats.get("count"),
                        "weighted_count": stats.get("weighted_count"),
                        "pnl": stats.get("pnl"),
                        "weighted_pnl": stats.get("weighted_pnl"),
                    }
                )

        family_key = str(family or "").strip().upper()
        category_key = str(category or "").strip().lower()
        side_key = str(side or "").strip().lower()
        sport_key = str(sport_type or _distilled_sport_type(family=family_key)).strip().lower()
        add_component("family", "families", family_key, 0.42, min_count=3)
        add_component("category", "categories", str(category or "").strip().lower(), 0.12, min_count=5)
        for tag in sports_tags[:8]:
            add_component("sports_tag", "sports_tags", tag, 0.18, min_count=3)
        for tag in structural_tags[:10]:
            add_component("structural_tag", "structural_tags", tag, 0.24, min_count=2)
        for pattern_key in structural_pattern_keys[:12]:
            add_component("structural_pattern", "structural_patterns", pattern_key, 0.30, min_count=2)
        add_component("cross_market_bucket", "cross_market_buckets", cross_market_bucket, 0.16, min_count=2)
        add_component("elite_edge_bucket", "elite_edge_buckets", elite_edge_bucket, 0.20, min_count=2)
        add_component("odds_bucket", "odds_buckets", odds_bucket, 0.18, min_count=4)
        add_component("book_quality", "book_quality", book_quality_tag, 0.12, min_count=4)
        add_component("side", "side_buckets", side_key, 0.08, min_count=4)
        add_component("asymmetry", "asymmetry_buckets", asymmetry_bucket, 0.10, min_count=4)
        add_component("liquidity", "liquidity_buckets", liquidity_bucket, 0.08, min_count=4)
        add_component("spread", "spread_buckets", spread_bucket, 0.08, min_count=4)
        add_component("sport_type", "sport_types", sport_key, 0.08, min_count=4)
        add_component("elite_trader", "elite_traders", elite_trader_handle, 0.28, min_count=2)
        if elite_trader_handle and category_key:
            add_component("elite_trader_category", "elite_trader_categories", f"{elite_trader_handle}|{category_key}", 0.18, min_count=2)

        add_component("sports_history_family", "families", family_key, 0.24, min_count=18, summary_source=sports_summaries)
        add_component("sports_history_sport", "sports", sport_key, 0.14, min_count=18, summary_source=sports_summaries)
        add_component("sports_history_odds", "odds_buckets", odds_bucket, 0.12, min_count=24, summary_source=sports_summaries)
        add_component("sports_history_asymmetry", "asymmetry_buckets", asymmetry_bucket, 0.14, min_count=24, summary_source=sports_summaries)
        add_component("sports_history_liquidity", "liquidity_buckets", liquidity_bucket, 0.08, min_count=24, summary_source=sports_summaries)
        add_component("sports_history_spread", "spread_buckets", spread_bucket, 0.08, min_count=24, summary_source=sports_summaries)
        if side_key and odds_bucket:
            add_component("sports_history_side_odds", "side_odds_buckets", f"{side_key}|{odds_bucket}", 0.10, min_count=12, summary_source=sports_summaries)
        if family_key and odds_bucket:
            add_component("sports_history_family_odds", "family_odds_buckets", f"{family_key}|{odds_bucket}", 0.12, min_count=10, summary_source=sports_summaries)
        if family_key and asymmetry_bucket:
            add_component("sports_history_family_asymmetry", "family_asymmetry_buckets", f"{family_key}|{asymmetry_bucket}", 0.12, min_count=10, summary_source=sports_summaries)
        if elite_trader_handle and family_key:
            add_component("sports_history_elite_family", "elite_trader_families", f"{elite_trader_handle}|{family_key}", 0.10, min_count=2, summary_source=sports_summaries)
        if elite_trader_handle and odds_bucket:
            add_component("sports_history_elite_odds", "elite_trader_odds", f"{elite_trader_handle}|{odds_bucket}", 0.08, min_count=2, summary_source=sports_summaries)

        sqlite_store = getattr(self, "sports_history_store", None)
        if sqlite_store is not None and hasattr(sqlite_store, "get_learning_signal"):
            try:
                sqlite_signal = sqlite_store.get_learning_signal(
                    family=family_key,
                    sport_type=sport_key,
                    odds_bucket=odds_bucket,
                    asymmetry_bucket=asymmetry_bucket,
                    liquidity_bucket=liquidity_bucket,
                    spread_bucket=spread_bucket,
                )
                for component in (sqlite_signal.get("components") or [])[:8]:
                    if isinstance(component, dict):
                        components.append(component)
            except Exception as exc:
                global _LAST_SQLITE_HISTORY_WARN_TS
                now_ts = _now()
                if now_ts - safe_float(_LAST_SQLITE_HISTORY_WARN_TS, 0.0) >= 300.0:
                    _LAST_SQLITE_HISTORY_WARN_TS = now_ts
                    builtins.print(
                        "[AION-SQLITE-AUDIT] "
                        + json.dumps(
                            {
                                "reason": "sqlite_learning_signal_failed",
                                "error": exc.__class__.__name__,
                                "action": "continue_with_json_memory",
                            },
                            sort_keys=True,
                        )
                    )

        raw_bias = sum(safe_float(item.get("bias"), 0.0) * safe_float(item.get("weight"), 0.0) for item in components)
        raw_bias = max(-1.0, min(1.0, raw_bias))
        return {
            "rank_adjustment": round(max(-0.75, min(1.25, raw_bias * 1.15)), 4),
            "sports_boost_adjustment": round(max(-0.45, min(0.75, raw_bias * 0.70)), 4),
            "sizing_confidence_multiplier": round(max(0.85, min(1.12, 1.0 + raw_bias * 0.10)), 4),
            "elite_trust_weight": round(max(0.65, min(1.35, 1.0 + raw_bias * 0.22)), 4),
            "components": components[:12],
        }

    def audit_distilled_learning(self, memory=None):
        memory = _ensure_distilled_learning_defaults(memory if isinstance(memory, dict) else self.distilled_learning)
        observations = [item for item in memory.get("observations", []) if isinstance(item, dict)]
        outcomes = [item for item in memory.get("outcomes", []) if isinstance(item, dict)]
        sports_history = _ensure_sports_history_defaults(memory.get("sports_history"))
        sports_history_records = [item for item in sports_history.get("records", []) if isinstance(item, dict)]
        timestamps = [
            safe_float(item.get("timestamp_ts"), None)
            for item in observations + outcomes + sports_history_records
            if safe_float(item.get("timestamp_ts"), None) is not None
        ]
        sports_history_timestamps = [
            safe_float(item.get("timestamp_ts"), None)
            for item in sports_history_records
            if safe_float(item.get("timestamp_ts"), None) is not None
        ]
        sports_observations = [
            item for item in observations
            if str(item.get("category") or "").lower() == "sports" or str(item.get("family") or "").upper().startswith(SPORTS_FAMILY_PREFIXES)
        ]
        sports_outcomes = [
            item for item in outcomes
            if str(item.get("category") or "").lower() == "sports" or item.get("sports_tags")
        ]
        elite_outcomes = [item for item in outcomes if item.get("elite_signal_used")]
        summaries = memory.get("summaries") if isinstance(memory.get("summaries"), dict) else {}
        families = summaries.get("families") if isinstance(summaries.get("families"), dict) else {}
        top_by_count = sorted(
            (
                {"family": key, "records": int((value or {}).get("observations", 0) or 0) + int((value or {}).get("count", 0) or 0)}
                for key, value in families.items()
                if isinstance(value, dict)
            ),
            key=lambda item: item["records"],
            reverse=True,
        )[:8]
        top_by_pnl = sorted(
            (
                {"family": key, "pnl": round(safe_float((value or {}).get("pnl"), 0.0) or 0.0, 6), "outcomes": int((value or {}).get("count", 0) or 0)}
                for key, value in families.items()
                if isinstance(value, dict)
            ),
            key=lambda item: item["pnl"],
            reverse=True,
        )[:8]
        sports_summaries = sports_history.get("summaries") if isinstance(sports_history.get("summaries"), dict) else {}

        def top_history_records(section, *, sort_key="records", reverse=True, limit=8):
            bucket = sports_summaries.get(section) if isinstance(sports_summaries.get(section), dict) else {}
            rows = []
            for key, value in bucket.items():
                if not isinstance(value, dict):
                    continue
                rows.append(
                    {
                        "key": key,
                        "records": int(value.get("records", 0) or 0),
                        "count": int(value.get("count", 0) or 0),
                        "weighted_count": round(safe_float(value.get("weighted_count"), 0.0) or 0.0, 4),
                        "pnl": round(safe_float(value.get("pnl"), 0.0) or 0.0, 6),
                        "weighted_pnl": round(safe_float(value.get("weighted_pnl"), 0.0) or 0.0, 6),
                        "weighted_avg_pnl": round(safe_float(value.get("weighted_avg_pnl"), 0.0) or 0.0, 6),
                        "weighted_win_rate": value.get("weighted_win_rate"),
                    }
                )
            return sorted(rows, key=lambda item: item.get(sort_key) or 0, reverse=reverse)[:limit]

        sports_history_family_rows = top_history_records("families")
        sports_history_profitable_families = [
            item for item in top_history_records("families", sort_key="weighted_avg_pnl")
            if safe_float(item.get("weighted_avg_pnl"), 0.0) > 0.0
        ][:8]
        sports_history_families = sorted({str(item.get("family") or "").strip().upper() for item in sports_history_records if item.get("family")})
        return {
            "total_stored_observations": len(observations),
            "total_stored_trade_outcomes": len(outcomes),
            "oldest_timestamp_ts": min(timestamps) if timestamps else None,
            "newest_timestamp_ts": max(timestamps) if timestamps else None,
            "sports_observation_count": len(sports_observations),
            "sports_trade_outcome_count": len(sports_outcomes),
            "elite_signal_linked_outcome_count": len(elite_outcomes),
            "top_families_by_record_count": top_by_count,
            "top_families_by_pnl": top_by_pnl,
            "sports_history_record_count": len(sports_history_records),
            "sports_history_oldest_timestamp_ts": min(sports_history_timestamps) if sports_history_timestamps else None,
            "sports_history_newest_timestamp_ts": max(sports_history_timestamps) if sports_history_timestamps else None,
            "sports_history_families_covered": sports_history_families,
            "sports_history_family_count": len(sports_history_families),
            "sports_history_records_by_family": sports_history_family_rows,
            "sports_history_profitable_families": sports_history_profitable_families,
            "sports_history_asymmetry_bucket_performance": top_history_records("asymmetry_buckets", sort_key="weighted_avg_pnl"),
            "sports_history_liquidity_bucket_performance": top_history_records("liquidity_buckets", sort_key="weighted_avg_pnl"),
            "sports_history_spread_bucket_performance": top_history_records("spread_buckets", sort_key="weighted_avg_pnl"),
            "sports_history_elite_confirmed_performance": top_history_records("elite_traders", sort_key="weighted_avg_pnl"),
        }

    def snapshot(self):
        with self._lock:
            return _json_safe(
                {
                    "state_file": self.state_file,
                    "ticker_profiles": deepcopy(self.ticker_profiles),
                    "family_profiles": deepcopy(self.family_profiles),
                    "distilled_learning": deepcopy(_ensure_distilled_learning_defaults(self.distilled_learning)),
                    "summary": self.build_overall_summary(),
                }
            )

    def persist(self):
        if not self.enabled:
            return self.snapshot()
        with self._lock:
            try:
                with _state_file_lock(self.state_file):
                    payload = self.snapshot()
                    disk_payload = _load_json_file(self.state_file)
                    if isinstance(disk_payload, dict):
                        payload["distilled_learning"] = _merge_distilled_learning_for_persist(
                            disk_payload.get("distilled_learning"),
                            payload.get("distilled_learning"),
                        )
                        self.distilled_learning = _ensure_distilled_learning_defaults(payload.get("distilled_learning"))
                    ok = safe_atomic_json_write(self.state_file, payload, indent=2, sort_keys=True, default=str)
                record_persist_event(subsystem="AION-PERSIST", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed")
            except Exception as exc:
                record_persist_event(subsystem="AION-PERSIST", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__)
                builtins.print("[AION-INTELLIGENCE-MEMORY] " + json.dumps({"reason": exc.__class__.__name__, "state_file": self.state_file, "action": "persist"}, sort_keys=True))
            return self.snapshot()

    def load(self):
        payload = None
        if os.path.exists(self.state_file):
            payload = _load_json_file(self.state_file)
        if payload is None:
            payload = _load_legacy_payload()
        if not isinstance(payload, dict):
            return self
        with self._lock:
            ticker_profiles = payload.get("ticker_profiles") or {}
            family_profiles = payload.get("family_profiles") or {}
            if isinstance(ticker_profiles, dict):
                self.ticker_profiles = ticker_profiles
            if isinstance(family_profiles, dict):
                self.family_profiles = family_profiles
            self.distilled_learning = _ensure_distilled_learning_defaults(payload.get("distilled_learning"))
        return self

    def maybe_emit_summary(self, force=False):
        now = _now()
        if force or (now - safe_float(self.last_summary_ts, 0.0)) >= self.summary_interval_seconds:
            self.last_summary_ts = now
            summary = self.build_overall_summary()
            builtins.print("[AION-INTELLIGENCE-MEMORY] " + json.dumps(summary, sort_keys=True, default=str))
            self.persist()
            return summary
        return self.build_overall_summary()


builtins.print("[AION-MODULE-LOAD] " + json.dumps({"module": "memory.aion_persistent_intelligence", "class": "AIONPersistentIntelligence"}, sort_keys=True))

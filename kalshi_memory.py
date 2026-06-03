"""Memory helpers owned outside the kalshi_web.py orchestrator."""

import csv
import json
import os
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone


def normalize_memory_family_value(family=None, ticker=None, default="UNKNOWN"):
    family_value = str(family or "").strip().upper()
    if family_value in {"NONE", "NULL", "UNKNOWN"}:
        family_value = ""
    if not family_value and ticker:
        family_value = str(ticker or "").split("-")[0].strip().upper()
        if family_value in {"NONE", "NULL", "UNKNOWN"}:
            family_value = ""
    return family_value or default


def record_live_family_performance(bot, family, realized_value):
    family = bot._normalize_family_for_profit_memory(family)
    stats = dict(bot.live_family_perf_stats.get(family, {}) or {})
    stats["trades"] = int(stats.get("trades", 0) or 0) + 1
    stats["wins"] = int(stats.get("wins", 0) or 0)
    stats["losses"] = int(stats.get("losses", 0) or 0)
    stats["loss_streak"] = int(stats.get("loss_streak", 0) or 0)
    stats["pnl"] = round(float(stats.get("pnl", 0.0) or 0.0) + float(realized_value or 0.0), 4)
    if realized_value > 0:
        stats["wins"] += 1
        stats["loss_streak"] = 0
    elif realized_value < 0:
        stats["losses"] += 1
        stats["loss_streak"] += 1
    bot.live_family_perf_stats[family] = stats
    bot.live_realized_pnl_by_family_today[family] = stats["pnl"]
    bot.live_recent_family_pnl_events.append({
        "ts": time.time(),
        "family": family,
        "pnl": float(realized_value or 0.0),
    })
    bot.live_recent_family_pnl_events = list(bot.live_recent_family_pnl_events or [])[-200:]
    bot.family_profit_memory_cache = {"loaded_at": 0.0, "stats": {}}
    print(
        f"[LIVE-FAMILY-PERFORMANCE-UPDATE] family={family} trades={stats.get('trades')} "
        f"wins={stats.get('wins')} losses={stats.get('losses')} "
        f"pnl={stats.get('pnl')} loss_streak={stats.get('loss_streak')}"
    )


def normalize_family_for_profit_memory(bot, family=None, ticker=None, market=None, q=None):
    family_value = normalize_memory_family_value(family, default="")
    if not family_value and isinstance(market, dict):
        family_value = str(market.get("family") or market.get("series_ticker") or "").strip().upper()
        if family_value in {"NONE", "NULL", "UNKNOWN"}:
            family_value = ""
    ticker_value = str(ticker or getattr(q, "ticker", None) or "").strip()
    if not ticker_value and isinstance(market, dict):
        ticker_value = str(market.get("ticker") or "").strip()
    if not family_value and ticker_value:
        try:
            family_value = str(bot.get_market_family({"ticker": ticker_value}) or "").strip().upper()
        except Exception:
            family_value = ""
    if not family_value and ticker_value:
        family_value = normalize_memory_family_value(None, ticker_value, default="")
    return family_value or "UNKNOWN"


def family_profit_memory_rows_from_sqlite(bot, *, sqlite_path, lookback, limit=None):
    path = str(getattr(bot, "seraph_memory_db_path", None) or sqlite_path or "").strip()
    if not path or not os.path.exists(path):
        return []
    limit_value = max(1, int(limit or lookback or 400))
    try:
        with sqlite3.connect(path, timeout=2) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT timestamp, timestamp_ts, ticker, family, realized_pnl_delta, outcome
                FROM seraph_trade_memory
                WHERE COALESCE(family, '') != ''
                   OR COALESCE(ticker, '') != ''
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit_value,),
            ).fetchall()
        return [dict(row) for row in rows if row]
    except Exception:
        return []


def family_profit_memory_rows_from_csv(bot, *, lookback, limit=None):
    rows = []
    for row in bot._load_trade_history_rows(limit=limit or lookback):
        if not isinstance(row, dict):
            continue
        rows.append({
            "timestamp": row.get("timestamp") or row.get("time") or row.get("created_at"),
            "timestamp_ts": row.get("timestamp_ts") or row.get("ts"),
            "ticker": row.get("ticker") or row.get("market_ticker"),
            "family": row.get("family") or row.get("series_ticker"),
            "realized_pnl_delta": (
                row.get("realized_pnl_delta")
                if row.get("realized_pnl_delta") is not None
                else row.get("pnl")
                if row.get("pnl") is not None
                else row.get("realized_pnl")
            ),
            "outcome": row.get("outcome") or row.get("result"),
        })
    return rows


def _archive_timestamp_to_epoch(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return float(parsed.timestamp())
    except Exception:
        return 0.0


def _first_present(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def family_profit_memory_rows_from_learning_archive(bot, *, archive_path=None, lookback, limit=None):
    if not bool(getattr(bot, "family_profit_memory_learning_archive_enabled", False)):
        return []
    path = str(
        getattr(bot, "aion_learning_archive_file", None)
        or archive_path
        or os.path.join("data", "aion_learning_archive.jsonl")
    ).strip()
    if not path or not os.path.exists(path):
        return []
    limit_value = max(1, int(limit or lookback or 400))
    rows = deque(maxlen=limit_value)
    try:
        with open(path, "r", encoding="utf-8") as archive_file:
            for line in archive_file:
                if '"trade_closed"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if not isinstance(event, dict) or event.get("event_type") != "trade_closed":
                    continue
                timestamp = (
                    event.get("close_timestamp_utc")
                    or event.get("timestamp_utc")
                    or event.get("timestamp")
                )
                rows.append(
                    {
                        "timestamp": timestamp,
                        "timestamp_ts": (
                            _first_present(event, "close_timestamp_ts", "timestamp_ts", "ts")
                            or _archive_timestamp_to_epoch(timestamp)
                        ),
                        "ticker": event.get("ticker") or event.get("market_ticker"),
                        "family": event.get("family") or event.get("series_ticker"),
                        "realized_pnl_delta": _first_present(
                            event,
                            "pnl_dollars",
                            "realized_pnl",
                            "realized_pnl_delta",
                            "pnl",
                        ),
                        "outcome": event.get("result") or event.get("outcome"),
                    }
                )
        return list(rows)
    except Exception:
        return []


def build_family_profit_memory_stats(bot, *, lookback, recent_window, limit=None):
    limit_value = max(1, int(limit or lookback or 400))
    rows = bot._family_profit_memory_rows_from_sqlite(limit=limit_value)
    rows.extend(bot._family_profit_memory_rows_from_csv(limit=limit_value))
    rows.extend(bot._family_profit_memory_rows_from_learning_archive(limit=limit_value))
    rows.sort(key=lambda row: bot.to_float(row.get("timestamp_ts"), 0.0) or 0.0, reverse=True)
    stats_by_family = {}
    seen_trade_keys = set()

    def _ensure_stats(family_key):
        return stats_by_family.setdefault(
            family_key,
            {
                "family": family_key,
                "trade_count": 0,
                "wins": 0,
                "losses": 0,
                "breakevens": 0,
                "family_pnl": 0.0,
                "recent_pnls": [],
                "latest_ts": 0.0,
                "source": "history",
            },
        )

    for row in rows:
        if not isinstance(row, dict):
            continue
        family = bot._normalize_family_for_profit_memory(row.get("family"), ticker=row.get("ticker"))
        pnl = bot.to_float(row.get("realized_pnl_delta"), None)
        outcome = str(row.get("outcome") or "").strip().lower()
        if pnl is None and outcome not in {"win", "loss", "breakeven"}:
            continue
        if pnl is None:
            pnl = 1.0 if outcome == "win" else -1.0 if outcome == "loss" else 0.0
        trade_key = (
            family,
            str(row.get("ticker") or "").strip().upper(),
            str(row.get("timestamp") or row.get("timestamp_ts") or "").strip(),
            round(float(pnl or 0.0), 6),
        )
        if trade_key in seen_trade_keys:
            continue
        seen_trade_keys.add(trade_key)
        stats = _ensure_stats(family)
        stats["trade_count"] += 1
        stats["family_pnl"] += float(pnl or 0.0)
        if outcome == "win" or pnl > 0:
            stats["wins"] += 1
        elif outcome == "loss" or pnl < 0:
            stats["losses"] += 1
        else:
            stats["breakevens"] += 1
        if len(stats["recent_pnls"]) < max(1, int(recent_window or 5)):
            stats["recent_pnls"].append(float(pnl or 0.0))
        timestamp_ts = bot.to_float(row.get("timestamp_ts"), 0.0) or 0.0
        stats["latest_ts"] = max(float(stats.get("latest_ts", 0.0) or 0.0), timestamp_ts)

    for family, live_stats in (bot.live_family_perf_stats or {}).items():
        if not isinstance(live_stats, dict):
            continue
        family_key = bot._normalize_family_for_profit_memory(family)
        stats = _ensure_stats(family_key)
        session_trades = int(live_stats.get("trades", 0) or 0)
        session_wins = int(live_stats.get("wins", 0) or 0)
        session_losses = int(live_stats.get("losses", 0) or 0)
        session_pnl = bot.to_float(live_stats.get("pnl"), 0.0) or 0.0
        if session_trades > 0:
            stats["trade_count"] += session_trades
            stats["wins"] += session_wins
            stats["losses"] += session_losses
            stats["breakevens"] += max(0, session_trades - session_wins - session_losses)
            stats["family_pnl"] += session_pnl
            stats["source"] = "history+session" if stats.get("source") == "history" else "session"

    for family, stats in list(stats_by_family.items()):
        trade_count = int(stats.get("trade_count", 0) or 0)
        wins = int(stats.get("wins", 0) or 0)
        losses = int(stats.get("losses", 0) or 0)
        family_pnl = round(bot.to_float(stats.get("family_pnl"), 0.0) or 0.0, 4)
        recent_pnls = [bot.to_float(value, 0.0) or 0.0 for value in list(stats.get("recent_pnls") or [])]
        recent_pnl = sum(recent_pnls)
        win_rate = (wins / trade_count) if trade_count > 0 else None
        if recent_pnl > 0:
            recent_trend = "up"
        elif recent_pnl < 0:
            recent_trend = "down"
        else:
            recent_trend = "flat" if trade_count > 0 else "unknown"
        stats.update({
            "family": family,
            "trade_count": trade_count,
            "wins": wins,
            "losses": losses,
            "family_pnl": family_pnl,
            "win_rate": bot._safe_round(win_rate, 4, None),
            "recent_pnl": round(recent_pnl, 4),
            "recent_trend": recent_trend,
        })
    return stats_by_family


def get_family_profit_memory_stats(bot, family, *, refresh=False, cache_seconds, lookback):
    family = bot._normalize_family_for_profit_memory(family)
    now = time.time()
    cache = bot.family_profit_memory_cache if isinstance(getattr(bot, "family_profit_memory_cache", None), dict) else {}
    loaded_at = bot.to_float(cache.get("loaded_at"), 0.0) or 0.0
    if refresh or not isinstance(cache.get("stats"), dict) or (now - loaded_at) >= max(5.0, cache_seconds):
        cache = {
            "loaded_at": now,
            "stats": bot._build_family_profit_memory_stats(limit=lookback),
        }
        bot.family_profit_memory_cache = cache
    return dict((cache.get("stats") or {}).get(family, {}) or {
        "family": family,
        "trade_count": 0,
        "wins": 0,
        "losses": 0,
        "family_pnl": 0.0,
        "win_rate": None,
        "recent_trend": "unknown",
        "recent_pnl": 0.0,
        "adjustment": 0.0,
    })


def get_family_profit_memory_adjustment(bot, family, *, log=True, source="ranking", enabled=True, min_trades_for_penalty=2, max_rank_adjustment=3.0):
    family = bot._normalize_family_for_profit_memory(family)
    stats = bot._get_family_profit_memory_stats(family)
    trade_count = int(stats.get("trade_count", 0) or 0)
    family_pnl = bot.to_float(stats.get("family_pnl"), 0.0) or 0.0
    win_rate = bot.to_float(stats.get("win_rate"), None)
    recent_pnl = bot.to_float(stats.get("recent_pnl"), 0.0) or 0.0
    recent_trend = str(stats.get("recent_trend") or "unknown").strip().lower() or "unknown"
    min_trades = max(1, int(min_trades_for_penalty or 2))
    if not enabled or trade_count < min_trades:
        adjustment = 0.0
    else:
        sample_factor = min(1.0, max(0.0, (trade_count - min_trades + 1) / 8.0))
        avg_pnl = family_pnl / max(trade_count, 1)
        pnl_signal = max(-1.0, min(1.0, avg_pnl * 12.0))
        win_signal = 0.0 if win_rate is None else max(-1.0, min(1.0, (win_rate - 0.5) * 2.0))
        trend_signal = max(-1.0, min(1.0, recent_pnl * 6.0))
        raw_adjustment = ((pnl_signal * 0.55) + (win_signal * 0.30) + (trend_signal * 0.15)) * max_rank_adjustment
        adjustment = max(
            -max_rank_adjustment,
            min(max_rank_adjustment, raw_adjustment * sample_factor),
        )
        if family_pnl < 0 and adjustment > 0:
            adjustment = 0.0
        elif family_pnl > 0 and adjustment < 0:
            adjustment = 0.0
    adjustment = round(float(adjustment or 0.0), 4)
    stats["adjustment"] = adjustment
    if log:
        marker = (
            int(time.time() // 60),
            family,
            round(family_pnl, 4),
            stats.get("win_rate"),
            trade_count,
            recent_trend,
            adjustment,
        )
        if bot.family_profit_memory_log_markers.get(family) != marker:
            bot.family_profit_memory_log_markers[family] = marker
            print(
                f"[FAMILY-PROFIT-MEMORY] family={family} family_pnl={round(family_pnl, 4)} "
                f"win_rate={stats.get('win_rate')} trade_count={trade_count} "
                f"recent_trend={recent_trend} adjustment={adjustment} source={source}"
            )
    return adjustment

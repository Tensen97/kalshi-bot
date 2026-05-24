from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.paths import ensure_runtime_layout, runtime_memory_file


DEFAULT_DB_PATH = runtime_memory_file("aion_sports_history.sqlite")
SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _parse_time(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _iso_time(value) -> str | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _normalize_price(value):
    number = _safe_float(value, None)
    if number is None:
        return None
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    if number < 0.0 or number > 1.0:
        return None
    return round(number, 6)


def _odds_bucket(price):
    price = _normalize_price(price)
    if price is None:
        return "unknown"
    if price < 0.20:
        return "under_20"
    if price <= 0.35:
        return "20_35"
    if price <= 0.45:
        return "35_45"
    if price <= 0.60:
        return "45_60"
    if price <= 0.80:
        return "60_80"
    return "over_80"


def _bucket_midpoint(bucket):
    return {
        "under_20": 0.15,
        "20_35": 0.275,
        "35_45": 0.40,
        "45_60": 0.525,
        "60_80": 0.70,
        "over_80": 0.875,
    }.get(str(bucket or "").strip(), 0.5)


def _asymmetry_bucket(price=None, payout_multiple=None):
    multiple = _safe_float(payout_multiple, None)
    price = _normalize_price(price)
    if multiple is None and price:
        multiple = 1.0 / max(price, 0.01)
    if multiple is None:
        return "unknown"
    if multiple >= 4.0:
        return "extreme_asymmetry"
    if multiple >= 2.5:
        return "strong_asymmetry"
    if multiple >= 1.5:
        return "moderate_asymmetry"
    return "low_asymmetry"


def _spread_bucket(spread):
    spread = _safe_float(spread, None)
    if spread is None:
        return "unknown"
    if spread <= 0.025:
        return "tight"
    if spread <= 0.05:
        return "usable"
    if spread <= 0.10:
        return "wide"
    return "very_wide"


def _liquidity_bucket(value):
    value = _safe_float(value, None)
    if value is None:
        return "unknown"
    if value >= 2500:
        return "deep"
    if value >= 500:
        return "solid"
    if value >= 100:
        return "thin"
    return "very_thin"


def _volume_bucket(value):
    value = _safe_float(value, None)
    if value is None:
        return "unknown"
    if value >= 10000:
        return "high_volume"
    if value >= 1000:
        return "medium_volume"
    if value > 0:
        return "low_volume"
    return "no_volume"


def _movement_bucket(previous_price, current_price):
    previous_price = _normalize_price(previous_price)
    current_price = _normalize_price(current_price)
    if previous_price is None or current_price is None:
        return "unknown"
    delta = current_price - previous_price
    if delta >= 0.06:
        return "surging"
    if delta >= 0.02:
        return "rising"
    if delta <= -0.06:
        return "collapsing"
    if delta <= -0.02:
        return "falling"
    return "flat"


def _volatility_bucket(previous_price, current_price):
    previous_price = _normalize_price(previous_price)
    current_price = _normalize_price(current_price)
    if previous_price is None or current_price is None:
        return "unknown"
    delta = abs(current_price - previous_price)
    if delta >= 0.10:
        return "high"
    if delta >= 0.04:
        return "medium"
    return "low"


def _normalize_outcome(value):
    text = str(value or "").strip().lower()
    if text in {"yes", "y", "true", "1", "win", "won"}:
        return "yes"
    if text in {"no", "n", "false", "0", "loss", "lost"}:
        return "no"
    return text or "unknown"


def _recency_weight(source_time):
    parsed = _parse_time(source_time)
    if parsed is None:
        return 0.25
    age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0)
    if age_days <= 30:
        return 1.0
    if age_days <= 90:
        return 0.75
    if age_days <= 365:
        return 0.45
    return 0.20


def _sports_side_bucket(odds_bucket):
    if odds_bucket in {"under_20", "20_35", "35_50"}:
        return "underdog"
    if odds_bucket in {"60_80", "over_80"}:
        return "favorite"
    if odds_bucket == "45_60":
        return "mid_price"
    return "unknown"


def _summary_bias(stats):
    if not isinstance(stats, dict):
        return 0.0
    count = int(stats.get("record_count") or stats.get("count") or 0)
    if count < 25:
        return 0.0
    avg_pnl = _safe_float(stats.get("weighted_avg_estimated_pnl"), 0.0) or 0.0
    win_rate = _safe_float(stats.get("weighted_win_rate"), 0.5)
    if win_rate is None:
        win_rate = 0.5
    pnl_component = max(-0.35, min(0.35, avg_pnl * 2.0))
    win_component = max(-0.15, min(0.15, (win_rate - 0.5) * 0.4))
    confidence = min(1.0, count / 600.0)
    return round((pnl_component + win_component) * confidence, 6)


class AIONSportsHistoryStore:
    def __init__(self, db_path: str | os.PathLike[str] | None = None, *, log_fn=None, initialize: bool = True):
        ensure_runtime_layout()
        self.db_path = str(db_path or DEFAULT_DB_PATH)
        self.log_fn = log_fn
        if initialize:
            self.initialize()

    def _log(self, tag, **payload):
        if self.log_fn is None:
            return
        try:
            self.log_fn(f"{tag} {json.dumps(payload, sort_keys=True, default=str)}")
        except TypeError:
            try:
                self.log_fn(tag, payload)
            except Exception:
                pass

    def _align_db_artifact_permissions(self) -> int:
        """Keep sudo-created SQLite artifacts readable/writable by the runtime owner."""
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            return 0
        db_path = Path(self.db_path)
        try:
            parent_stat = db_path.parent.stat()
        except OSError:
            return 0
        changed = 0
        for artifact in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            if not artifact.exists():
                continue
            try:
                current = artifact.stat()
                if current.st_uid != parent_stat.st_uid or current.st_gid != parent_stat.st_gid:
                    os.chown(artifact, parent_stat.st_uid, parent_stat.st_gid)
                    changed += 1
                mode = current.st_mode & 0o777
                desired_mode = mode | 0o660
                if mode != desired_mode:
                    os.chmod(artifact, desired_mode)
                    changed += 1
            except OSError:
                continue
        return changed

    @contextmanager
    def connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
        finally:
            conn.close()
            self._align_db_artifact_permissions()

    def initialize(self):
        with self.connect() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sports_history_records (
                    id INTEGER PRIMARY KEY,
                    source_time TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    market_title TEXT,
                    sport_type TEXT,
                    family TEXT,
                    category TEXT,
                    implied_probability_bucket TEXT,
                    payout_asymmetry_bucket TEXT,
                    spread_bucket TEXT,
                    liquidity_bucket TEXT,
                    movement_bucket TEXT,
                    volatility_bucket TEXT,
                    volume_bucket TEXT,
                    outcome TEXT,
                    resolution_status TEXT,
                    data_source TEXT,
                    backfill_run_id TEXT,
                    recency_weight REAL,
                    raw_hash TEXT NOT NULL,
                    UNIQUE(ticker, source_time, raw_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_sports_history_source_time ON sports_history_records(source_time);
                CREATE INDEX IF NOT EXISTS idx_sports_history_ticker ON sports_history_records(ticker);
                CREATE INDEX IF NOT EXISTS idx_sports_history_sport ON sports_history_records(sport_type);
                CREATE INDEX IF NOT EXISTS idx_sports_history_family ON sports_history_records(family);
                CREATE INDEX IF NOT EXISTS idx_sports_history_odds ON sports_history_records(implied_probability_bucket);
                CREATE INDEX IF NOT EXISTS idx_sports_history_asymmetry ON sports_history_records(payout_asymmetry_bucket);
                CREATE INDEX IF NOT EXISTS idx_sports_history_liquidity ON sports_history_records(liquidity_bucket);
                CREATE INDEX IF NOT EXISTS idx_sports_history_run ON sports_history_records(backfill_run_id);
                CREATE TABLE IF NOT EXISTS sports_history_summaries (
                    summary_key TEXT PRIMARY KEY,
                    summary_type TEXT,
                    family TEXT,
                    sport_type TEXT,
                    bucket_name TEXT,
                    record_count INTEGER,
                    outcome_count INTEGER,
                    win_count INTEGER,
                    loss_count INTEGER,
                    avg_recency_weight REAL,
                    computed_at TEXT,
                    payload_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sports_history_summary_type ON sports_history_summaries(summary_type);
                """
            )
            conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
            conn.commit()
        self._log("[AION-SQLITE-INIT]", db_path=self.db_path, journal_mode=journal_mode)
        return {"ok": True, "db_path": self.db_path, "journal_mode": journal_mode}

    def normalize_record(self, record: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(record, dict):
            return None
        ticker = str(record.get("ticker") or "").strip()
        if not ticker:
            return None
        source_time = _iso_time(record.get("source_time") or record.get("timestamp") or record.get("close_time") or record.get("settlement_time"))
        if source_time is None:
            source_time = _utc_now_iso()
        yes_price = _normalize_price(record.get("yes_price") or record.get("last_price") or record.get("yes_ask") or record.get("yes_bid"))
        no_price = _normalize_price(record.get("no_price") or record.get("no_ask") or record.get("no_bid"))
        selected_price = yes_price if yes_price is not None else no_price
        implied_bucket = str(record.get("implied_probability_bucket") or _odds_bucket(selected_price)).strip() or "unknown"
        asymmetry_bucket = str(record.get("payout_asymmetry_bucket") or record.get("asymmetry_bucket") or _asymmetry_bucket(price=selected_price)).strip() or "unknown"
        spread_bucket = str(record.get("spread_bucket") or _spread_bucket(record.get("spread"))).strip() or "unknown"
        liquidity_bucket = str(record.get("liquidity_bucket") or _liquidity_bucket(record.get("liquidity") or record.get("liquidity_dollars"))).strip() or "unknown"
        movement_bucket = str(record.get("movement_bucket") or _movement_bucket(record.get("previous_price"), selected_price)).strip() or "unknown"
        volatility_bucket = str(record.get("volatility_bucket") or _volatility_bucket(record.get("previous_price"), selected_price)).strip() or "unknown"
        volume_bucket = str(record.get("volume_bucket") or _volume_bucket(record.get("volume"))).strip() or "unknown"
        outcome = _normalize_outcome(record.get("outcome") or record.get("settlement_result") or record.get("result"))
        resolution_status = str(record.get("resolution_status") or record.get("status") or ("resolved" if outcome != "unknown" else "unknown")).strip().lower()
        normalized = {
            "source_time": source_time,
            "ingested_at": _utc_now_iso(),
            "ticker": ticker,
            "market_title": str(record.get("market_title") or record.get("title") or "").strip(),
            "sport_type": str(record.get("sport_type") or "sports").strip().lower() or "sports",
            "family": str(record.get("family") or "").strip().upper(),
            "category": str(record.get("category") or "sports").strip().lower() or "sports",
            "implied_probability_bucket": implied_bucket,
            "payout_asymmetry_bucket": asymmetry_bucket,
            "spread_bucket": spread_bucket,
            "liquidity_bucket": liquidity_bucket,
            "movement_bucket": movement_bucket,
            "volatility_bucket": volatility_bucket,
            "volume_bucket": volume_bucket,
            "outcome": outcome,
            "resolution_status": resolution_status,
            "data_source": str(record.get("data_source") or "unknown").strip(),
            "backfill_run_id": str(record.get("backfill_run_id") or "").strip(),
            "recency_weight": _safe_float(record.get("recency_weight"), None),
        }
        if normalized["recency_weight"] is None:
            normalized["recency_weight"] = _recency_weight(source_time)
        hash_payload = {key: normalized.get(key) for key in sorted(normalized) if key not in {"ingested_at", "backfill_run_id"}}
        normalized["raw_hash"] = hashlib.sha256(json.dumps(hash_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return normalized

    def insert_records(self, records, *, batch_size=250) -> dict[str, Any]:
        normalized = []
        malformed = 0
        for record in records or []:
            item = self.normalize_record(record)
            if item is None:
                malformed += 1
                continue
            normalized.append(item)
        if not normalized:
            return {"stored": 0, "duplicates": 0, "malformed": malformed, "errors": 0}
        columns = (
            "source_time", "ingested_at", "ticker", "market_title", "sport_type", "family", "category",
            "implied_probability_bucket", "payout_asymmetry_bucket", "spread_bucket", "liquidity_bucket",
            "movement_bucket", "volatility_bucket", "volume_bucket", "outcome", "resolution_status",
            "data_source", "backfill_run_id", "recency_weight", "raw_hash",
        )
        sql = (
            f"INSERT OR IGNORE INTO sports_history_records ({', '.join(columns)}) "
            f"VALUES ({', '.join(['?'] * len(columns))})"
        )
        stored = 0
        errors = 0
        with self.connect() as conn:
            try:
                for offset in range(0, len(normalized), max(1, int(batch_size or 250))):
                    chunk = normalized[offset:offset + max(1, int(batch_size or 250))]
                    before = conn.total_changes
                    conn.executemany(sql, [tuple(item.get(column) for column in columns) for item in chunk])
                    conn.commit()
                    stored += max(0, conn.total_changes - before)
            except sqlite3.Error:
                conn.rollback()
                errors += len(normalized)
        duplicates = max(0, len(normalized) - stored - errors)
        self._log("[AION-SQLITE-STORE]", stored=stored, duplicates=duplicates, malformed=malformed, errors=errors)
        return {"stored": stored, "duplicates": duplicates, "malformed": malformed, "errors": errors}

    def migrate_json_records(self, records, *, batch_size=500) -> dict[str, Any]:
        result = self.insert_records(records or [], batch_size=batch_size)
        self._log("[AION-SQLITE-MIGRATE]", source="json_sports_history", **result)
        return result

    def _iter_summary_rows(self):
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT family, sport_type, implied_probability_bucket, payout_asymmetry_bucket,
                       spread_bucket, liquidity_bucket, movement_bucket, volatility_bucket,
                       volume_bucket, outcome, recency_weight
                FROM sports_history_records
                """
            ).fetchall()
        return rows

    def recompute_summaries(self) -> dict[str, Any]:
        buckets: dict[tuple[str, str], dict[str, Any]] = {}

        def update(summary_type, key, row):
            key = str(key or "unknown").strip() or "unknown"
            summary_key = f"{summary_type}:{key}"
            stats = buckets.setdefault(
                (summary_key, summary_type),
                {
                    "summary_key": summary_key,
                    "summary_type": summary_type,
                    "family": row["family"] if summary_type.startswith("family") else None,
                    "sport_type": row["sport_type"] if summary_type.startswith("sport") else None,
                    "bucket_name": key,
                    "record_count": 0,
                    "outcome_count": 0,
                    "win_count": 0,
                    "loss_count": 0,
                    "recency_weight_total": 0.0,
                    "weighted_count": 0.0,
                    "weighted_win": 0.0,
                    "weighted_estimated_pnl": 0.0,
                },
            )
            weight = _safe_float(row["recency_weight"], 0.25) or 0.25
            outcome = _normalize_outcome(row["outcome"])
            odds_bucket = row["implied_probability_bucket"]
            price = _bucket_midpoint(odds_bucket)
            estimated_pnl = 0.0
            won = None
            if outcome == "yes":
                won = True
                estimated_pnl = 1.0 - price
            elif outcome == "no":
                won = False
                estimated_pnl = -price
            stats["record_count"] += 1
            stats["recency_weight_total"] += weight
            if won is not None:
                stats["outcome_count"] += 1
                stats["win_count"] += 1 if won else 0
                stats["loss_count"] += 0 if won else 1
                stats["weighted_count"] += weight
                stats["weighted_win"] += weight if won else 0.0
                stats["weighted_estimated_pnl"] += estimated_pnl * weight

        for row in self._iter_summary_rows():
            update("family", row["family"], row)
            update("sport", row["sport_type"], row)
            update("odds_bucket", row["implied_probability_bucket"], row)
            update("asymmetry_bucket", row["payout_asymmetry_bucket"], row)
            update("spread_bucket", row["spread_bucket"], row)
            update("liquidity_bucket", row["liquidity_bucket"], row)
            update("movement_bucket", row["movement_bucket"], row)
            update("volatility_bucket", row["volatility_bucket"], row)
            update("volume_bucket", row["volume_bucket"], row)
            update("side_bucket", _sports_side_bucket(row["implied_probability_bucket"]), row)
            if row["family"] and row["implied_probability_bucket"]:
                update("family_odds_bucket", f"{row['family']}|{row['implied_probability_bucket']}", row)
            if row["family"] and row["payout_asymmetry_bucket"]:
                update("family_asymmetry_bucket", f"{row['family']}|{row['payout_asymmetry_bucket']}", row)
        computed_at = _utc_now_iso()
        rows = []
        for stats in buckets.values():
            record_count = max(0, int(stats.get("record_count") or 0))
            outcome_count = max(0, int(stats.get("outcome_count") or 0))
            weighted_count = _safe_float(stats.get("weighted_count"), 0.0) or 0.0
            avg_recency_weight = (stats["recency_weight_total"] / record_count) if record_count else 0.0
            weighted_win_rate = (stats["weighted_win"] / weighted_count) if weighted_count > 0 else None
            weighted_avg_pnl = (stats["weighted_estimated_pnl"] / weighted_count) if weighted_count > 0 else None
            payload = {
                "record_count": record_count,
                "outcome_count": outcome_count,
                "win_count": stats["win_count"],
                "loss_count": stats["loss_count"],
                "weighted_count": round(weighted_count, 6),
                "weighted_win_rate": round(weighted_win_rate, 6) if weighted_win_rate is not None else None,
                "weighted_estimated_pnl": round(stats["weighted_estimated_pnl"], 6),
                "weighted_avg_estimated_pnl": round(weighted_avg_pnl, 6) if weighted_avg_pnl is not None else None,
            }
            rows.append(
                (
                    stats["summary_key"],
                    stats["summary_type"],
                    stats.get("family"),
                    stats.get("sport_type"),
                    stats.get("bucket_name"),
                    record_count,
                    outcome_count,
                    stats["win_count"],
                    stats["loss_count"],
                    round(avg_recency_weight, 6),
                    computed_at,
                    json.dumps(payload, sort_keys=True),
                )
            )
        with self.connect() as conn:
            conn.execute("DELETE FROM sports_history_summaries")
            conn.executemany(
                """
                INSERT INTO sports_history_summaries (
                    summary_key, summary_type, family, sport_type, bucket_name,
                    record_count, outcome_count, win_count, loss_count,
                    avg_recency_weight, computed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        result = {"summary_count": len(rows), "computed_at": computed_at}
        self._log("[AION-SPORTS-HISTORY-SUMMARY]", **result)
        return result

    def audit(self, *, recompute: bool = True) -> dict[str, Any]:
        if recompute:
            self.recompute_summaries()
        with self.connect() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            total_records = conn.execute("SELECT COUNT(*) FROM sports_history_records").fetchone()[0]
            oldest = conn.execute("SELECT MIN(source_time) FROM sports_history_records").fetchone()[0]
            newest = conn.execute("SELECT MAX(source_time) FROM sports_history_records").fetchone()[0]
            latest_run = conn.execute(
                "SELECT backfill_run_id FROM sports_history_records WHERE backfill_run_id IS NOT NULL AND backfill_run_id != '' ORDER BY ingested_at DESC LIMIT 1"
            ).fetchone()
            duplicate_count = conn.execute(
                "SELECT COUNT(*) - COUNT(DISTINCT ticker || '|' || source_time || '|' || raw_hash) FROM sports_history_records"
            ).fetchone()[0]
            summary_counts = {
                row["summary_type"]: row["count"]
                for row in conn.execute("SELECT summary_type, COUNT(*) AS count FROM sports_history_summaries GROUP BY summary_type").fetchall()
            }

            def grouped(column, limit=12):
                return [
                    {"key": row["key"], "records": row["records"]}
                    for row in conn.execute(
                        f"SELECT COALESCE({column}, 'unknown') AS key, COUNT(*) AS records "
                        f"FROM sports_history_records GROUP BY COALESCE({column}, 'unknown') "
                        "ORDER BY records DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                ]

            def summary_rows(summary_type, limit=8):
                result = []
                for row in conn.execute(
                    """
                    SELECT bucket_name, record_count, outcome_count, win_count, loss_count,
                           avg_recency_weight, payload_json
                    FROM sports_history_summaries
                    WHERE summary_type = ?
                    ORDER BY record_count DESC
                    LIMIT ?
                    """,
                    (summary_type, limit),
                ).fetchall():
                    payload = {}
                    try:
                        payload = json.loads(row["payload_json"] or "{}")
                    except Exception:
                        payload = {}
                    result.append(
                        {
                            "key": row["bucket_name"],
                            "records": row["record_count"],
                            "outcomes": row["outcome_count"],
                            "wins": row["win_count"],
                            "losses": row["loss_count"],
                            "avg_recency_weight": row["avg_recency_weight"],
                            **payload,
                        }
                    )
                return result

            records_by_sport_type = grouped("sport_type")
            records_by_family = grouped("family")
            records_by_odds_bucket = grouped("implied_probability_bucket")
            records_by_asymmetry_bucket = grouped("payout_asymmetry_bucket")
            records_by_liquidity_bucket = grouped("liquidity_bucket")
            family_performance = summary_rows("family")
            sport_performance = summary_rows("sport")
            odds_bucket_performance = summary_rows("odds_bucket")
            asymmetry_bucket_performance = summary_rows("asymmetry_bucket")
            spread_bucket_performance = summary_rows("spread_bucket")
            liquidity_bucket_performance = summary_rows("liquidity_bucket")
            side_bucket_performance = summary_rows("side_bucket")

        db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        wal_path = self.db_path + "-wal"
        audit = {
            "db_path": self.db_path,
            "db_file_size_bytes": db_size,
            "wal_file_size_bytes": os.path.getsize(wal_path) if os.path.exists(wal_path) else 0,
            "wal_mode_status": str(journal_mode or "").lower(),
            "total_records": total_records,
            "oldest_source_time": oldest,
            "newest_source_time": newest,
            "records_by_sport_type": records_by_sport_type,
            "records_by_family": records_by_family,
            "records_by_odds_bucket": records_by_odds_bucket,
            "records_by_asymmetry_bucket": records_by_asymmetry_bucket,
            "records_by_liquidity_bucket": records_by_liquidity_bucket,
            "duplicate_count": duplicate_count,
            "latest_backfill_run_id": latest_run["backfill_run_id"] if latest_run else None,
            "latest_summary_counts": summary_counts,
            "family_performance": family_performance,
            "sport_performance": sport_performance,
            "odds_bucket_performance": odds_bucket_performance,
            "asymmetry_bucket_performance": asymmetry_bucket_performance,
            "spread_bucket_performance": spread_bucket_performance,
            "liquidity_bucket_performance": liquidity_bucket_performance,
            "side_bucket_performance": side_bucket_performance,
        }
        self._log("[AION-SQLITE-AUDIT]", **audit)
        return audit

    def get_learning_signal(
        self,
        *,
        family=None,
        sport_type=None,
        odds_bucket=None,
        asymmetry_bucket=None,
        liquidity_bucket=None,
        spread_bucket=None,
    ) -> dict[str, Any]:
        requested = [
            ("sqlite_sports_history_family", "family", str(family or "").strip().upper(), 0.22, 50),
            ("sqlite_sports_history_sport", "sport", str(sport_type or "").strip().lower(), 0.12, 50),
            ("sqlite_sports_history_odds", "odds_bucket", odds_bucket, 0.10, 75),
            ("sqlite_sports_history_asymmetry", "asymmetry_bucket", asymmetry_bucket, 0.12, 75),
            ("sqlite_sports_history_liquidity", "liquidity_bucket", liquidity_bucket, 0.08, 75),
            ("sqlite_sports_history_spread", "spread_bucket", spread_bucket, 0.08, 75),
        ]
        components = []
        with self.connect() as conn:
            for label, summary_type, key, weight, min_count in requested:
                key = str(key or "").strip()
                if not key:
                    continue
                row = conn.execute(
                    "SELECT record_count, payload_json FROM sports_history_summaries WHERE summary_key = ?",
                    (f"{summary_type}:{key}",),
                ).fetchone()
                if not row:
                    continue
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except Exception:
                    payload = {}
                payload["record_count"] = row["record_count"]
                if int(row["record_count"] or 0) < min_count:
                    continue
                bias = _summary_bias(payload)
                if bias:
                    components.append(
                        {
                            "label": label,
                            "key": key,
                            "bias": bias,
                            "weight": weight,
                            "count": row["record_count"],
                            "weighted_count": payload.get("weighted_count"),
                            "weighted_avg_estimated_pnl": payload.get("weighted_avg_estimated_pnl"),
                            "weighted_win_rate": payload.get("weighted_win_rate"),
                        }
                    )
        raw_bias = sum((_safe_float(item.get("bias"), 0.0) or 0.0) * (_safe_float(item.get("weight"), 0.0) or 0.0) for item in components)
        raw_bias = max(-1.0, min(1.0, raw_bias))
        return {
            "rank_adjustment": round(max(-0.45, min(0.75, raw_bias * 1.0)), 4),
            "sports_boost_adjustment": round(max(-0.25, min(0.45, raw_bias * 0.65)), 4),
            "sizing_confidence_multiplier": round(max(0.9, min(1.08, 1.0 + raw_bias * 0.08)), 4),
            "components": components[:8],
        }

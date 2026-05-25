"""SQLite storage and lightweight learning for crypto ladder history.

The original BTC table names stay in place for compatibility with existing
live code and backfill scripts. Asset/timeframe columns let the same store
cover BTC, ETH, SOL, XRP, DOGE, HYPE, BNB, and future liquid crypto ladders.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "btc_ladder_history.sqlite"
SCHEMA_VERSION = 2
SUPPORTED_CRYPTO_ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "BNB")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def normalize_price(value: Any) -> float | None:
    number = safe_float(value, None)
    if number is None:
        return None
    if 1.0 < number <= 100.0:
        number /= 100.0
    if not 0.0 <= number <= 1.0:
        return None
    return round(number, 6)


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_time(value: Any) -> str | None:
    parsed = parse_datetime(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed is not None else None


def first_value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_btc_strike(*values: Any) -> float | None:
    fallback = None
    for value in values:
        text = str(value or "")
        if not text.strip():
            continue
        preferred = []
        candidates = []
        for raw in re.findall(r"\$?\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\$?\b\d{4,7}(?:\.\d+)?\b", text):
            number = safe_float(str(raw).replace("$", "").replace(",", ""), None)
            if number is None or not 10_000.0 <= number <= 1_000_000.0:
                continue
            candidates.append(number)
            lowered = text.lower()
            if "$" in raw or "," in raw or any(term in lowered for term in ("bitcoin", "btc", "price", "above", "below")):
                preferred.append(number)
        if preferred:
            return float(preferred[-1])
        if candidates and fallback is None:
            fallback = float(candidates[-1])
    return fallback


def parse_crypto_asset(*values: Any) -> str | None:
    text = " ".join(str(value or "") for value in values if str(value or "").strip()).upper()
    if not text:
        return None
    aliases = {
        "BTC": ("BTC", "BITCOIN", "KXBTC"),
        "ETH": ("ETH", "ETHEREUM", "KXETH"),
        "SOL": ("SOL", "SOLANA", "KXSOL"),
        "XRP": ("XRP", "RIPPLE", "KXXRP"),
        "DOGE": ("DOGE", "DOGECOIN", "KXDOGE"),
        "HYPE": ("HYPE", "HYPERLIQUID", "KXHYPE"),
        "BNB": ("BNB", "BINANCE", "KXBNB"),
    }
    for asset, tokens in aliases.items():
        if any(re.search(rf"(?<![A-Z0-9]){re.escape(token)}(?![A-Z0-9])", text) for token in tokens):
            return asset
    match = re.search(r"\bKX([A-Z]{2,8})(?:15M|1H|PRICE|USD)?\b", text)
    if match and match.group(1) in SUPPORTED_CRYPTO_ASSETS:
        return match.group(1)
    return None


def parse_crypto_timeframe(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values if str(value or "").strip()).upper()
    if "15M" in text or "15 MIN" in text or "15-MIN" in text or "15 MINUTE" in text:
        return "15m"
    if "1H" in text or "1 HOUR" in text or "HOURLY" in text or "HOUR" in text:
        return "1h"
    if "DAILY" in text or "TODAY" in text or "24H" in text:
        return "daily"
    if "MONTH" in text or "HIGH" in text or "LOW" in text:
        return "monthly_high_low"
    if re.search(r"\b(?:[0-2]?\d)(?::[0-5]\d)?\s*(?:AM|PM|ET|EDT|EST|UTC)\b", text):
        return "time_settlement"
    return "unknown"


def parse_crypto_strike(*values: Any, asset_symbol: str | None = None) -> float | None:
    asset = str(asset_symbol or parse_crypto_asset(*values) or "").upper()
    if asset == "BTC":
        return parse_btc_strike(*values)
    for value in values:
        match = re.search(r"-(?:B|T)(\d+(?:\.\d+)?)", str(value or "").upper())
        if match:
            return safe_float(match.group(1), None)
    fallback = None
    for value in values:
        text = str(value or "")
        if not text.strip():
            continue
        lowered = text.lower()
        preferred = []
        candidates = []
        for raw in re.findall(r"\$?\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\$?\b\d+(?:\.\d+)?\b", text):
            number = safe_float(str(raw).replace("$", "").replace(",", ""), None)
            if number is None or number <= 0:
                continue
            if asset == "ETH" and not (100.0 <= number <= 100_000.0):
                continue
            if asset in {"SOL", "BNB", "HYPE"} and not (1.0 <= number <= 10_000.0):
                continue
            if asset in {"XRP", "DOGE"} and not (0.001 <= number <= 100.0):
                continue
            candidates.append(number)
            if "$" in raw or any(term in lowered for term in ("price", "above", "below", "range", "between", "target")):
                preferred.append(number)
        if preferred:
            return float(preferred[-1])
        if candidates and fallback is None:
            fallback = float(candidates[-1])
    return fallback


def market_family(market: dict[str, Any]) -> str:
    for key in ("series_ticker", "series", "family"):
        value = str(market.get(key) or "").strip().upper()
        if value:
            return value
    event = str(market.get("event_ticker") or "").strip().upper()
    if event:
        return event.split("-", 1)[0]
    ticker = str(market.get("ticker") or "").strip().upper()
    return ticker.split("-", 1)[0] if ticker else ""


def is_btc_ladder_market(market: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            market_family(market),
            market.get("ticker"),
            market.get("event_ticker"),
            market.get("title"),
            market.get("subtitle"),
            market.get("rules_primary"),
            market.get("rules_secondary"),
        )
    ).upper()
    if "BTC" not in text and "BITCOIN" not in text:
        return False
    if "KXBTC" in text or "BITCOIN" in text:
        return True
    return any(term in text for term in ("BTC PRICE", "BTC ABOVE", "BTC BELOW", "BTC AT"))


def is_crypto_ladder_market(market: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            market_family(market),
            market.get("ticker"),
            market.get("event_ticker"),
            market.get("title"),
            market.get("subtitle"),
            market.get("rules_primary"),
            market.get("rules_secondary"),
        )
    ).upper()
    asset = parse_crypto_asset(text)
    if not asset:
        return False
    if asset == "BTC" and is_btc_ladder_market(market):
        return True
    if f"KX{asset}" in text or (asset == "XRP" and "KXXRP" in text):
        return True
    return any(term in text for term in ("PRICE", "ABOVE", "BELOW", "BETWEEN", "RANGE", "TARGET", "HIGH", "LOW"))


def ladder_group_key(market: dict[str, Any], family: str | None = None) -> str:
    event = str(market.get("event_ticker") or "").strip().upper()
    if event:
        return event
    close_time = (
        iso_time(first_value(market, "close_time", "expiration_time", "latest_expiration_time", "settlement_time"))
        or ""
    )
    if close_time:
        asset = parse_crypto_asset(family, market.get("ticker"), market.get("title"), market.get("subtitle")) or "BTC"
        timeframe = parse_crypto_timeframe(family, market.get("ticker"), market.get("title"), market.get("subtitle"))
        return f"{asset}|{timeframe}|{str(family or market_family(market) or 'KXBTC').upper()}|{close_time[:13]}"
    ticker = str(market.get("ticker") or "").strip().upper()
    parts = ticker.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else ticker


def odds_bucket(price: Any) -> str:
    p = normalize_price(price)
    if p is None:
        return "unknown"
    if p < 0.15:
        return "under_15"
    if p < 0.30:
        return "15_30"
    if p < 0.45:
        return "30_45"
    if p < 0.60:
        return "45_60"
    if p < 0.75:
        return "60_75"
    if p < 0.90:
        return "75_90"
    return "over_90"


def spread_bucket(spread: Any) -> str:
    value = safe_float(spread, None)
    if value is None:
        return "unknown"
    if value <= 0.02:
        return "tight"
    if value <= 0.05:
        return "usable"
    if value <= 0.10:
        return "wide"
    return "very_wide"


def liquidity_bucket(liquidity: Any) -> str:
    value = safe_float(liquidity, None)
    if value is None:
        return "unknown"
    if value <= 0:
        return "none"
    if value < 100:
        return "thin"
    if value < 500:
        return "usable"
    return "deep"


def time_to_expiry_bucket(open_time: Any, close_time: Any) -> str:
    opened = parse_datetime(open_time)
    closed = parse_datetime(close_time)
    if opened is None or closed is None:
        return "unknown"
    minutes = (closed - opened).total_seconds() / 60.0
    if minutes <= 35:
        return "sub_35m"
    if minutes <= 75:
        return "hourly"
    if minutes <= 180:
        return "multi_hour"
    return "longer"


def session_bucket(source_time: Any) -> str:
    parsed = parse_datetime(source_time)
    if parsed is None:
        return "unknown"
    hour = parsed.hour
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 13:
        return "europe"
    if 13 <= hour < 17:
        return "us_morning"
    if 17 <= hour < 21:
        return "us_afternoon"
    return "us_evening"


def strike_distance_bucket(strike: Any, settlement_value: Any) -> str:
    strike_value = safe_float(strike, None)
    actual = safe_float(settlement_value, None)
    if strike_value is None or actual is None or strike_value <= 0 or actual <= 1000:
        return "unknown"
    pct = (actual - strike_value) / strike_value
    abs_pct = abs(pct)
    prefix = "itm" if pct >= 0 else "otm"
    if abs_pct <= 0.0025:
        return f"{prefix}_near_pin"
    if abs_pct <= 0.008:
        return f"{prefix}_near"
    if abs_pct <= 0.02:
        return f"{prefix}_mid"
    return f"{prefix}_far"


def neighbor_gap_bucket(gap: Any) -> str:
    value = safe_float(gap, None)
    if value is None:
        return "unknown"
    if value <= 250:
        return "tight_strikes"
    if value <= 750:
        return "normal_strikes"
    if value <= 1500:
        return "wide_strikes"
    return "very_wide_strikes"


def normalize_outcome(value: Any, *, strike: Any = None, settlement_value: Any = None) -> str:
    text = str(value or "").strip().lower()
    if text in {"yes", "y", "true", "1", "above", "win", "won"}:
        return "yes"
    if text in {"no", "n", "false", "0", "below", "lose", "lost"}:
        return "no"
    strike_value = safe_float(strike, None)
    actual = safe_float(settlement_value, None)
    if strike_value is not None and actual is not None and actual > 1000:
        return "yes" if actual >= strike_value else "no"
    return "unknown"


def summary_bias(payload: dict[str, Any], *, min_count: int) -> float:
    count = int(payload.get("outcome_count") or payload.get("record_count") or 0)
    if count < min_count:
        return 0.0
    avg_pnl = safe_float(payload.get("weighted_avg_pnl"), None)
    winrate = safe_float(payload.get("weighted_winrate"), None)
    if avg_pnl is None and winrate is None:
        return 0.0
    bias = 0.0
    if avg_pnl is not None:
        bias += max(-0.5, min(0.5, avg_pnl)) * 1.7
    if winrate is not None:
        bias += max(-0.5, min(0.5, winrate - 0.5)) * 0.85
    return round(max(-1.0, min(1.0, bias)), 6)


class BTCLadderHistoryStore:
    def __init__(
        self,
        db_path: str | Path | None = None,
        log_fn=None,
        initialize: bool = True,
        read_only: bool = False,
        use_wal: bool = True,
    ):
        self.db_path = str(Path(db_path or os.environ.get("AION_BTC_1H_HISTORY_DB_PATH") or DEFAULT_DB_PATH))
        self.log_fn = log_fn
        self.read_only = bool(read_only)
        self.use_wal = bool(use_wal)
        if initialize:
            self.initialize()

    def _log(self, tag: str, **payload: Any) -> None:
        if callable(self.log_fn):
            self.log_fn(f"{tag} {json.dumps(payload, sort_keys=True, default=str)}")

    @contextmanager
    def connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        if self.read_only:
            uri = f"file:{Path(self.db_path).resolve()}?mode=ro"
            conn = sqlite3.connect(uri, timeout=5.0, uri=True)
        else:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            if not self.read_only:
                conn.execute("PRAGMA synchronous=NORMAL")
                if self.use_wal:
                    conn.execute("PRAGMA journal_mode=WAL")
                else:
                    conn.execute("PRAGMA journal_mode=MEMORY")
            yield conn
        finally:
            conn.close()

    def initialize(self) -> dict[str, Any]:
        if self.read_only:
            return {"ok": True, "db_path": self.db_path, "read_only": True}
        with self.connect() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS btc_ladder_markets (
                    id INTEGER PRIMARY KEY,
                    ticker TEXT NOT NULL UNIQUE,
                    event_ticker TEXT,
                    family TEXT,
                    asset_symbol TEXT,
                    timeframe TEXT,
                    ladder_group TEXT,
                    title TEXT,
                    subtitle TEXT,
                    rules_primary TEXT,
                    rules_secondary TEXT,
                    status TEXT,
                    open_time TEXT,
                    close_time TEXT,
                    expiration_time TEXT,
                    settlement_time TEXT,
                    source_time TEXT,
                    strike REAL,
                    settlement_value REAL,
                    outcome TEXT,
                    yes_bid REAL,
                    yes_ask REAL,
                    no_bid REAL,
                    no_ask REAL,
                    last_price REAL,
                    previous_price REAL,
                    implied_probability REAL,
                    spread REAL,
                    liquidity REAL,
                    volume REAL,
                    open_interest REAL,
                    implied_probability_bucket TEXT,
                    spread_bucket TEXT,
                    liquidity_bucket TEXT,
                    time_to_expiry_bucket TEXT,
                    session_bucket TEXT,
                    strike_distance_bucket TEXT,
                    neighbor_gap_bucket TEXT,
                    data_source TEXT,
                    backfill_run_id TEXT,
                    raw_hash TEXT NOT NULL,
                    raw_json TEXT,
                    ingested_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_btc_ladder_source_time ON btc_ladder_markets(source_time);
                CREATE INDEX IF NOT EXISTS idx_btc_ladder_family ON btc_ladder_markets(family);
                CREATE INDEX IF NOT EXISTS idx_btc_ladder_group ON btc_ladder_markets(ladder_group);
                CREATE INDEX IF NOT EXISTS idx_btc_ladder_strike ON btc_ladder_markets(strike);
                CREATE INDEX IF NOT EXISTS idx_btc_ladder_odds ON btc_ladder_markets(implied_probability_bucket);
                CREATE INDEX IF NOT EXISTS idx_btc_ladder_spread ON btc_ladder_markets(spread_bucket);
                CREATE INDEX IF NOT EXISTS idx_btc_ladder_liquidity ON btc_ladder_markets(liquidity_bucket);
                CREATE TABLE IF NOT EXISTS btc_ladder_summaries (
                    summary_key TEXT PRIMARY KEY,
                    summary_type TEXT,
                    bucket_name TEXT,
                    record_count INTEGER,
                    outcome_count INTEGER,
                    win_count INTEGER,
                    loss_count INTEGER,
                    weighted_winrate REAL,
                    weighted_avg_pnl REAL,
                    computed_at TEXT,
                    payload_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_btc_ladder_summary_type ON btc_ladder_summaries(summary_type);
                """
            )
            existing_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(btc_ladder_markets)").fetchall()
            }
            if "asset_symbol" not in existing_columns:
                conn.execute("ALTER TABLE btc_ladder_markets ADD COLUMN asset_symbol TEXT")
            if "timeframe" not in existing_columns:
                conn.execute("ALTER TABLE btc_ladder_markets ADD COLUMN timeframe TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_btc_ladder_asset ON btc_ladder_markets(asset_symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_btc_ladder_timeframe ON btc_ladder_markets(timeframe)")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        self._log("[BTC-1H-HISTORY-LEARNED]", db_path=self.db_path, journal_mode=journal_mode, reason="sqlite_init")
        return {"ok": True, "db_path": self.db_path, "journal_mode": journal_mode}

    def seen_tickers(self) -> set[str]:
        if not Path(self.db_path).exists():
            return set()
        with self.connect() as conn:
            return {str(row["ticker"]) for row in conn.execute("SELECT ticker FROM btc_ladder_markets").fetchall()}

    def normalize_record(self, record: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(record, dict):
            return None
        ticker = str(record.get("ticker") or "").strip()
        if not ticker:
            return None
        family = str(record.get("family") or market_family(record)).strip().upper()
        asset_symbol = str(
            record.get("asset_symbol")
            or parse_crypto_asset(
                family,
                ticker,
                record.get("event_ticker"),
                record.get("title"),
                record.get("subtitle"),
                record.get("rules_primary"),
                record.get("rules_secondary"),
            )
            or "BTC"
        ).strip().upper()
        timeframe = str(
            record.get("timeframe")
            or parse_crypto_timeframe(
                family,
                ticker,
                record.get("event_ticker"),
                record.get("title"),
                record.get("subtitle"),
                record.get("rules_primary"),
                record.get("rules_secondary"),
            )
        ).strip().lower()
        strike = safe_float(record.get("strike"), None)
        if strike is None:
            strike = parse_crypto_strike(
                record.get("title"),
                record.get("subtitle"),
                record.get("rules_primary"),
                record.get("rules_secondary"),
                ticker,
                asset_symbol=asset_symbol,
            )
        yes_bid = normalize_price(first_value(record, "yes_bid", "yes_bid_dollars"))
        yes_ask = normalize_price(first_value(record, "yes_ask", "yes_ask_dollars"))
        no_bid = normalize_price(first_value(record, "no_bid", "no_bid_dollars"))
        no_ask = normalize_price(first_value(record, "no_ask", "no_ask_dollars"))
        last_price = normalize_price(first_value(record, "last_price", "last_price_dollars", "last_trade_price"))
        previous_price = normalize_price(first_value(record, "previous_price", "previous_price_dollars"))
        implied = normalize_price(record.get("implied_probability"))
        if implied is None:
            if last_price is not None:
                implied = last_price
            elif yes_bid is not None and yes_ask is not None:
                implied = round((yes_bid + yes_ask) / 2.0, 6)
            elif yes_ask is not None:
                implied = yes_ask
            elif yes_bid is not None:
                implied = yes_bid
        spread = safe_float(record.get("spread"), None)
        if spread is None and yes_bid is not None and yes_ask is not None and yes_ask >= yes_bid:
            spread = round(yes_ask - yes_bid, 6)
        elif spread is None and no_bid is not None and no_ask is not None and no_ask >= no_bid:
            spread = round(no_ask - no_bid, 6)
        source_time = (
            iso_time(record.get("source_time"))
            or iso_time(first_value(record, "settlement_time", "close_time", "expiration_time", "latest_expiration_time", "open_time"))
            or utc_now_iso()
        )
        open_time = iso_time(record.get("open_time"))
        close_time = iso_time(first_value(record, "close_time", "expiration_time", "latest_expiration_time"))
        settlement_value = safe_float(first_value(record, "settlement_value", "expiration_value", "final_settlement"), None)
        outcome = normalize_outcome(
            first_value(record, "outcome", "settlement_result", "result", "winning_outcome"),
            strike=strike,
            settlement_value=settlement_value,
        )
        normalized = {
            "ticker": ticker,
            "event_ticker": str(record.get("event_ticker") or "").strip(),
            "family": family,
            "asset_symbol": asset_symbol,
            "timeframe": timeframe,
            "ladder_group": str(record.get("ladder_group") or ladder_group_key(record, family)).strip(),
            "title": str(record.get("title") or record.get("market_title") or "").strip(),
            "subtitle": str(record.get("subtitle") or "").strip(),
            "rules_primary": str(record.get("rules_primary") or "").strip(),
            "rules_secondary": str(record.get("rules_secondary") or "").strip(),
            "status": str(record.get("status") or "").strip().lower(),
            "open_time": open_time,
            "close_time": close_time,
            "expiration_time": iso_time(first_value(record, "expiration_time", "latest_expiration_time")),
            "settlement_time": iso_time(first_value(record, "settlement_time", "settled_time")),
            "source_time": source_time,
            "strike": strike,
            "settlement_value": settlement_value,
            "outcome": outcome,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "last_price": last_price,
            "previous_price": previous_price,
            "implied_probability": implied,
            "spread": spread,
            "liquidity": safe_float(first_value(record, "liquidity", "liquidity_dollars"), None),
            "volume": safe_float(first_value(record, "volume", "volume_dollars", "volume_24h", "volume_24h_dollars"), None),
            "open_interest": safe_float(first_value(record, "open_interest", "open_interest_dollars", "open_interest_fp"), None),
            "implied_probability_bucket": str(record.get("implied_probability_bucket") or odds_bucket(implied)),
            "spread_bucket": str(record.get("spread_bucket") or spread_bucket(spread)),
            "liquidity_bucket": str(record.get("liquidity_bucket") or liquidity_bucket(first_value(record, "liquidity", "liquidity_dollars"))),
            "time_to_expiry_bucket": str(record.get("time_to_expiry_bucket") or time_to_expiry_bucket(open_time, close_time)),
            "session_bucket": str(record.get("session_bucket") or session_bucket(source_time)),
            "strike_distance_bucket": str(record.get("strike_distance_bucket") or strike_distance_bucket(strike, settlement_value)),
            "neighbor_gap_bucket": str(record.get("neighbor_gap_bucket") or "unknown"),
            "data_source": str(record.get("data_source") or "kalshi_api:markets").strip(),
            "backfill_run_id": str(record.get("backfill_run_id") or "").strip(),
            "raw_json": json.dumps(record, sort_keys=True, default=str),
            "ingested_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        hash_payload = {key: normalized.get(key) for key in sorted(normalized) if key not in {"ingested_at", "updated_at", "raw_json"}}
        normalized["raw_hash"] = hashlib.sha256(json.dumps(hash_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return normalized

    def insert_records(self, records: list[dict[str, Any]], *, batch_size: int = 250) -> dict[str, int]:
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
            "ticker", "event_ticker", "family", "asset_symbol", "timeframe", "ladder_group", "title", "subtitle", "rules_primary", "rules_secondary",
            "status", "open_time", "close_time", "expiration_time", "settlement_time", "source_time", "strike",
            "settlement_value", "outcome", "yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "previous_price",
            "implied_probability", "spread", "liquidity", "volume", "open_interest", "implied_probability_bucket",
            "spread_bucket", "liquidity_bucket", "time_to_expiry_bucket", "session_bucket", "strike_distance_bucket",
            "neighbor_gap_bucket", "data_source", "backfill_run_id", "raw_hash", "raw_json", "ingested_at", "updated_at",
        )
        sql = f"INSERT OR IGNORE INTO btc_ladder_markets ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})"
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
        return {"stored": stored, "duplicates": duplicates, "malformed": malformed, "errors": errors}

    def upsert_records(self, records: list[dict[str, Any]], *, batch_size: int = 250) -> dict[str, int]:
        normalized = []
        malformed = 0
        for record in records or []:
            item = self.normalize_record(record)
            if item is None:
                malformed += 1
                continue
            normalized.append(item)
        if not normalized:
            return {"stored": 0, "updated": 0, "duplicates": 0, "malformed": malformed, "errors": 0}
        columns = (
            "ticker", "event_ticker", "family", "asset_symbol", "timeframe", "ladder_group", "title", "subtitle", "rules_primary", "rules_secondary",
            "status", "open_time", "close_time", "expiration_time", "settlement_time", "source_time", "strike",
            "settlement_value", "outcome", "yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "previous_price",
            "implied_probability", "spread", "liquidity", "volume", "open_interest", "implied_probability_bucket",
            "spread_bucket", "liquidity_bucket", "time_to_expiry_bucket", "session_bucket", "strike_distance_bucket",
            "neighbor_gap_bucket", "data_source", "backfill_run_id", "raw_hash", "raw_json", "ingested_at", "updated_at",
        )
        update_columns = [column for column in columns if column not in {"ticker", "ingested_at"}]
        set_clause = ", ".join(
            f"{column}=excluded.{column}" for column in update_columns
        ) + ", ingested_at=COALESCE(btc_ladder_markets.ingested_at, excluded.ingested_at)"
        difference_checks = " OR ".join(
            f"COALESCE(btc_ladder_markets.{column}, '') != COALESCE(excluded.{column}, '')"
            for column in update_columns
            if column != "updated_at"
        )
        sql = f"""
            INSERT INTO btc_ladder_markets ({', '.join(columns)})
            VALUES ({', '.join(['?'] * len(columns))})
            ON CONFLICT(ticker) DO UPDATE SET {set_clause}
            WHERE {difference_checks}
        """
        stored = 0
        updated = 0
        errors = 0
        existing = self.seen_tickers()
        with self.connect() as conn:
            try:
                for offset in range(0, len(normalized), max(1, int(batch_size or 250))):
                    chunk = normalized[offset:offset + max(1, int(batch_size or 250))]
                    before = conn.total_changes
                    conn.executemany(sql, [tuple(item.get(column) for column in columns) for item in chunk])
                    conn.commit()
                    changed = max(0, conn.total_changes - before)
                    chunk_new = sum(1 for item in chunk if item.get("ticker") not in existing)
                    chunk_stored = min(changed, chunk_new)
                    stored += chunk_stored
                    updated += max(0, changed - chunk_stored)
                    existing.update(str(item.get("ticker")) for item in chunk if item.get("ticker"))
            except sqlite3.Error:
                conn.rollback()
                errors += len(normalized)
        duplicates = max(0, len(normalized) - stored - updated - errors)
        return {"stored": stored, "updated": updated, "duplicates": duplicates, "malformed": malformed, "errors": errors}

    def _rows_for_summary(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM btc_ladder_markets").fetchall()]
        by_group: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("ladder_group") and row.get("strike") is not None:
                by_group.setdefault(str(row["ladder_group"]), []).append(row)
        neighbor_bucket_by_ticker = {}
        for group_rows in by_group.values():
            ordered = sorted(group_rows, key=lambda item: safe_float(item.get("strike"), 0.0) or 0.0)
            for idx, row in enumerate(ordered):
                gaps = []
                strike = safe_float(row.get("strike"), None)
                if strike is None:
                    continue
                if idx > 0:
                    gaps.append(abs(strike - (safe_float(ordered[idx - 1].get("strike"), strike) or strike)))
                if idx + 1 < len(ordered):
                    gaps.append(abs((safe_float(ordered[idx + 1].get("strike"), strike) or strike) - strike))
                if gaps:
                    neighbor_bucket_by_ticker[row["ticker"]] = neighbor_gap_bucket(min(gaps))
        for row in rows:
            if row.get("ticker") in neighbor_bucket_by_ticker:
                row["neighbor_gap_bucket"] = neighbor_bucket_by_ticker[row["ticker"]]
        return rows

    def recompute_summaries(self) -> dict[str, Any]:
        buckets: dict[tuple[str, str], dict[str, Any]] = {}

        def update(summary_type: str, key: str, *, side: str, price: float | None, outcome: str, row: dict[str, Any]) -> None:
            if not key or price is None or not 0.0 < price < 1.0:
                return
            summary_key = f"{summary_type}:{key}"
            stats = buckets.setdefault(
                (summary_key, summary_type),
                {
                    "summary_key": summary_key,
                    "summary_type": summary_type,
                    "bucket_name": key,
                    "record_count": 0,
                    "outcome_count": 0,
                    "win_count": 0,
                    "loss_count": 0,
                    "weighted_win": 0.0,
                    "weighted_count": 0.0,
                    "weighted_pnl": 0.0,
                },
            )
            stats["record_count"] += 1
            if outcome not in {"yes", "no"}:
                return
            won = outcome == side
            pnl = (1.0 - price) if won else -price
            source_time = parse_datetime(row.get("source_time"))
            age_days = (datetime.now(timezone.utc) - source_time).days if source_time is not None else 365
            weight = 1.0 if age_days <= 30 else 0.75 if age_days <= 90 else 0.45 if age_days <= 180 else 0.25
            stats["outcome_count"] += 1
            stats["win_count"] += 1 if won else 0
            stats["loss_count"] += 0 if won else 1
            stats["weighted_win"] += weight if won else 0.0
            stats["weighted_count"] += weight
            stats["weighted_pnl"] += pnl * weight

        for row in self._rows_for_summary():
            implied = normalize_price(row.get("implied_probability"))
            if implied is None:
                continue
            outcome = normalize_outcome(row.get("outcome"))
            side_prices = {"yes": implied, "no": round(1.0 - implied, 6)}
            for side, price in side_prices.items():
                side_prefix = f"{side}|"
                update("side_asset", side_prefix + str(row.get("asset_symbol") or "unknown"), side=side, price=price, outcome=outcome, row=row)
                update("side_family", side_prefix + str(row.get("family") or "unknown"), side=side, price=price, outcome=outcome, row=row)
                update("side_timeframe", side_prefix + str(row.get("timeframe") or "unknown"), side=side, price=price, outcome=outcome, row=row)
                update("side_odds_bucket", side_prefix + odds_bucket(price), side=side, price=price, outcome=outcome, row=row)
                update("side_spread_bucket", side_prefix + str(row.get("spread_bucket") or "unknown"), side=side, price=price, outcome=outcome, row=row)
                update("side_liquidity_bucket", side_prefix + str(row.get("liquidity_bucket") or "unknown"), side=side, price=price, outcome=outcome, row=row)
                update("side_time_bucket", side_prefix + str(row.get("time_to_expiry_bucket") or "unknown"), side=side, price=price, outcome=outcome, row=row)
                update("side_session_bucket", side_prefix + str(row.get("session_bucket") or "unknown"), side=side, price=price, outcome=outcome, row=row)
                update("side_strike_distance_bucket", side_prefix + str(row.get("strike_distance_bucket") or "unknown"), side=side, price=price, outcome=outcome, row=row)
                update("side_neighbor_gap_bucket", side_prefix + str(row.get("neighbor_gap_bucket") or "unknown"), side=side, price=price, outcome=outcome, row=row)

        computed_at = utc_now_iso()
        rows = []
        for stats in buckets.values():
            weighted_count = stats["weighted_count"]
            weighted_winrate = (stats["weighted_win"] / weighted_count) if weighted_count > 0 else None
            weighted_avg_pnl = (stats["weighted_pnl"] / weighted_count) if weighted_count > 0 else None
            payload = {
                "record_count": stats["record_count"],
                "outcome_count": stats["outcome_count"],
                "win_count": stats["win_count"],
                "loss_count": stats["loss_count"],
                "weighted_count": round(weighted_count, 6),
                "weighted_winrate": round(weighted_winrate, 6) if weighted_winrate is not None else None,
                "weighted_avg_pnl": round(weighted_avg_pnl, 6) if weighted_avg_pnl is not None else None,
                "weighted_pnl": round(stats["weighted_pnl"], 6),
            }
            rows.append(
                (
                    stats["summary_key"],
                    stats["summary_type"],
                    stats["bucket_name"],
                    stats["record_count"],
                    stats["outcome_count"],
                    stats["win_count"],
                    stats["loss_count"],
                    payload["weighted_winrate"],
                    payload["weighted_avg_pnl"],
                    computed_at,
                    json.dumps(payload, sort_keys=True),
                )
            )
        with self.connect() as conn:
            conn.execute("DELETE FROM btc_ladder_summaries")
            conn.executemany(
                """
                INSERT INTO btc_ladder_summaries (
                    summary_key, summary_type, bucket_name, record_count, outcome_count,
                    win_count, loss_count, weighted_winrate, weighted_avg_pnl, computed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        result = {"summary_count": len(rows), "computed_at": computed_at}
        self._log("[BTC-1H-HISTORY-LEARNED]", **result)
        return result

    def audit(self, *, recompute: bool = True) -> dict[str, Any]:
        if recompute:
            self.recompute_summaries()
        with self.connect() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM btc_ladder_markets").fetchone()[0]
            groups = conn.execute("SELECT COUNT(DISTINCT ladder_group) FROM btc_ladder_markets").fetchone()[0]
            oldest = conn.execute("SELECT MIN(source_time) FROM btc_ladder_markets").fetchone()[0]
            newest = conn.execute("SELECT MAX(source_time) FROM btc_ladder_markets").fetchone()[0]
            latest_run = conn.execute(
                "SELECT backfill_run_id FROM btc_ladder_markets WHERE backfill_run_id IS NOT NULL AND backfill_run_id != '' ORDER BY ingested_at DESC LIMIT 1"
            ).fetchone()

            def grouped(column: str, limit: int = 10):
                return [
                    {"key": row["key"], "records": row["records"]}
                    for row in conn.execute(
                        f"SELECT COALESCE({column}, 'unknown') AS key, COUNT(*) AS records FROM btc_ladder_markets "
                        f"GROUP BY COALESCE({column}, 'unknown') ORDER BY records DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                ]

            learned = [
                {"key": row["bucket_name"], "type": row["summary_type"], "records": row["record_count"], "outcomes": row["outcome_count"], "weighted_avg_pnl": row["weighted_avg_pnl"], "weighted_winrate": row["weighted_winrate"]}
                for row in conn.execute(
                    "SELECT * FROM btc_ladder_summaries WHERE outcome_count > 0 ORDER BY ABS(COALESCE(weighted_avg_pnl, 0)) DESC, record_count DESC LIMIT 12"
                ).fetchall()
            ]
            records_by_family = grouped("family")
            records_by_asset = grouped("asset_symbol")
            records_by_timeframe = grouped("timeframe")
            records_by_odds_bucket = grouped("implied_probability_bucket")
            records_by_spread_bucket = grouped("spread_bucket")
            records_by_liquidity_bucket = grouped("liquidity_bucket")
            records_by_time_bucket = grouped("time_to_expiry_bucket")
        audit = {
            "db_path": self.db_path,
            "db_file_size_bytes": os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0,
            "wal_mode_status": str(journal_mode or "").lower(),
            "total_records": total,
            "ladder_groups": groups,
            "oldest_source_time": oldest,
            "newest_source_time": newest,
            "latest_backfill_run_id": latest_run["backfill_run_id"] if latest_run else None,
            "records_by_family": records_by_family,
            "records_by_asset": records_by_asset,
            "records_by_timeframe": records_by_timeframe,
            "records_by_odds_bucket": records_by_odds_bucket,
            "records_by_spread_bucket": records_by_spread_bucket,
            "records_by_liquidity_bucket": records_by_liquidity_bucket,
            "records_by_time_bucket": records_by_time_bucket,
            "learned_buckets": learned,
        }
        self._log("[BTC-1H-HISTORY-LEARNED]", audit=True, **audit)
        return audit

    def get_learning_signal(
        self,
        *,
        family: str | None = None,
        asset_symbol: str | None = None,
        timeframe: str | None = None,
        selected_side: str | None = None,
        selected_price: Any = None,
        spread: Any = None,
        liquidity: Any = None,
        time_to_expiry_bucket_value: str | None = None,
        session_bucket_value: str | None = None,
        strike_distance_bucket_value: str | None = None,
        neighbor_gap_bucket_value: str | None = None,
    ) -> dict[str, Any]:
        side = str(selected_side or "yes").strip().lower()
        if side not in {"yes", "no"}:
            side = "yes"
        price_bucket = odds_bucket(selected_price)
        requested = [
            ("side_asset", f"{side}|{str(asset_symbol or '').strip().upper()}", 0.16, 20),
            ("side_family", f"{side}|{str(family or '').strip().upper()}", 0.24, 20),
            ("side_timeframe", f"{side}|{str(timeframe or '').strip().lower()}", 0.10, 20),
            ("side_odds_bucket", f"{side}|{price_bucket}", 0.20, 25),
            ("side_spread_bucket", f"{side}|{spread_bucket(spread)}", 0.12, 25),
            ("side_liquidity_bucket", f"{side}|{liquidity_bucket(liquidity)}", 0.10, 25),
            ("side_time_bucket", f"{side}|{time_to_expiry_bucket_value or 'unknown'}", 0.12, 20),
            ("side_session_bucket", f"{side}|{session_bucket_value or 'unknown'}", 0.08, 25),
            ("side_strike_distance_bucket", f"{side}|{strike_distance_bucket_value or 'unknown'}", 0.08, 20),
            ("side_neighbor_gap_bucket", f"{side}|{neighbor_gap_bucket_value or 'unknown'}", 0.06, 20),
        ]
        components = []
        with self.connect() as conn:
            for summary_type, key, weight, min_count in requested:
                if not key or key.endswith("|"):
                    continue
                row = conn.execute(
                    "SELECT record_count, outcome_count, weighted_winrate, weighted_avg_pnl, payload_json FROM btc_ladder_summaries WHERE summary_key = ?",
                    (f"{summary_type}:{key}",),
                ).fetchone()
                if not row:
                    continue
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except Exception:
                    payload = {}
                payload["record_count"] = row["record_count"]
                payload["outcome_count"] = row["outcome_count"]
                payload["weighted_winrate"] = row["weighted_winrate"]
                payload["weighted_avg_pnl"] = row["weighted_avg_pnl"]
                bias = summary_bias(payload, min_count=min_count)
                if bias:
                    components.append(
                        {
                            "summary_type": summary_type,
                            "key": key,
                            "bias": bias,
                            "weight": weight,
                            "sample_size": row["outcome_count"],
                            "record_count": row["record_count"],
                            "learned_winrate": row["weighted_winrate"],
                            "learned_avg_pnl": row["weighted_avg_pnl"],
                        }
                    )
        raw_bias = sum((safe_float(item.get("bias"), 0.0) or 0.0) * (safe_float(item.get("weight"), 0.0) or 0.0) for item in components)
        raw_bias = max(-1.0, min(1.0, raw_bias))
        sample_size = max([int(item.get("sample_size") or 0) for item in components] or [0])
        learned_avg_pnl_values = [safe_float(item.get("learned_avg_pnl"), None) for item in components if safe_float(item.get("learned_avg_pnl"), None) is not None]
        learned_winrate_values = [safe_float(item.get("learned_winrate"), None) for item in components if safe_float(item.get("learned_winrate"), None) is not None]
        return {
            "available": bool(components),
            "sample_size": sample_size,
            "learned_avg_pnl": round(sum(learned_avg_pnl_values) / len(learned_avg_pnl_values), 6) if learned_avg_pnl_values else None,
            "learned_winrate": round(sum(learned_winrate_values) / len(learned_winrate_values), 6) if learned_winrate_values else None,
            "historical_edge_boost": round(max(0.0, min(5.5, raw_bias * 16.0)), 4),
            "historical_risk_penalty": round(max(0.0, min(1.75, -raw_bias * 5.0)), 4),
            "probability_adjustment": round(max(-0.012, min(0.026, raw_bias * 0.055)), 6),
            "sizing_confidence_multiplier": round(max(0.94, min(1.22, 1.0 + raw_bias * 0.42)), 4),
            "components": components[:8],
        }

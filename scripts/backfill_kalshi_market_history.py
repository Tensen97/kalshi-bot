#!/usr/bin/env python3
"""Minimal Kalshi historical market JSONL backfill collector.

This script is intentionally standalone from live execution. It loads the same
local .env file convention as run_bot.sh, reads public Kalshi market history via
the existing API surface, and appends reusable records to data/.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
OUTPUT_PATH = REPO_ROOT / "data" / "kalshi_market_history.jsonl"

DEFAULT_LIVE_BASE_URL = "https://api.elections.kalshi.com"
DEFAULT_LIVE_PRIVATE_KEY_PATH = "/Users/Kevin/Downloads/kalshi.key.txt"

MARKET_STATUSES = ("settled", "closed", "resolved")
PRICE_HISTORY_ENDPOINTS_AVAILABLE = False

PRIORITY_FAMILIES: tuple[dict[str, Any], ...] = (
    {
        "name": "nba_1h_totals",
        "series_tickers": ("KXNBA1HTOTAL",),
        "tokens": ("NBA", "1H", "TOTAL"),
    },
    {
        "name": "nba_spreads",
        "series_tickers": ("KXNBA1HSPREAD", "KXNBASPREAD"),
        "tokens": ("NBA", "SPREAD"),
    },
    {
        "name": "mlb_totals",
        "series_tickers": ("KXMLBTOTAL",),
        "tokens": ("MLB", "TOTAL"),
    },
    {
        "name": "mlb_spreads",
        "series_tickers": ("KXMLBSPREAD",),
        "tokens": ("MLB", "SPREAD"),
    },
    {
        "name": "strikeout_player_props",
        "series_tickers": ("KXMLBHRR", "KXMLBTB", "KXMLBHIT", "KXMLBHR"),
        "tokens": ("MLB",),
    },
    {
        "name": "nfl_spreads_totals",
        "series_tickers": ("KXNFLSPREAD", "KXNFLTOTAL", "KXNFLMONEYLINE"),
        "tokens": ("NFL",),
    },
    {
        "name": "nhl_markets",
        "series_tickers": ("KXNHL", "KXNHLGOAL", "KXNHLSPREAD", "KXNHLTOTAL"),
        "tokens": ("NHL",),
    },
    {
        "name": "ncaa_markets",
        "series_tickers": ("KXNCAAB", "KXNCAAF", "KXCFB", "KXCBB"),
        "tokens": ("NCAA",),
    },
    {
        "name": "tennis_matches",
        "series_tickers": ("KXATPCHALLENGERMATCH", "KXTENNISMATCH", "KXATP", "KXWTA", "KXITF"),
        "tokens": ("TENNIS",),
    },
    {
        "name": "golf_soccer_combat",
        "series_tickers": ("KXGOLF", "KXEPL", "KXSOCCER", "KXUFC", "KXMMA", "KXBOX"),
        "tokens": ("SPORT",),
    },
    {
        "name": "sports_combo_player_props",
        "series_tickers": ("KXPLAYERPROP", "KXSPORTSCOMBO", "KXSPORTSPARLAY"),
        "tokens": ("PROP",),
    },
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(tag: str, **fields: Any) -> None:
    print(f"{tag} {json.dumps(fields, sort_keys=True, default=str)}", flush=True)


def load_dotenv(path: Path) -> int:
    if not path.exists():
        log("[KALSHI-HISTORY-BACKFILL-WARN]", reason="missing_env_file", path=str(path))
        return 0

    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
        loaded += 1
    return loaded


def parse_date(value: str, *, end_of_day: bool = False) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def resolve_date_range(args: argparse.Namespace) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if args.start_date or args.end_date:
        end_dt = parse_date(args.end_date, end_of_day=True) if args.end_date else now
        start_dt = parse_date(args.start_date) if args.start_date else end_dt - timedelta(days=args.days_back)
        return start_dt, end_dt
    return now - timedelta(days=args.days_back), now


def parse_market_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000.0
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def first_value(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def number_or_none(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number


def clean_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return text or None


def get_live_base_url() -> str:
    return os.environ.get("KALSHI_LIVE_BASE_URL", "") or DEFAULT_LIVE_BASE_URL


def build_live_request_url(path: str) -> str:
    base_url = get_live_base_url().rstrip("/")
    canonical_prefix = "/trade-api/v2"
    if base_url.endswith(canonical_prefix) and path.startswith(f"{canonical_prefix}/"):
        return f"{base_url}{path[len(canonical_prefix):]}"
    return f"{base_url}{path}"


def get_live_auth_config() -> dict[str, Any]:
    api_key_source = None
    api_key = ""
    if os.environ.get("KALSHI_API_KEY_ID", ""):
        api_key_source = "KALSHI_API_KEY_ID"
        api_key = os.environ.get("KALSHI_API_KEY_ID", "")
    elif os.environ.get("KALSHI_API_KEY", ""):
        api_key_source = "KALSHI_API_KEY"
        api_key = os.environ.get("KALSHI_API_KEY", "")

    private_key_path_source = None
    private_key_path = ""
    if os.environ.get("LIVE_PRIVATE_KEY_PATH", ""):
        private_key_path_source = "LIVE_PRIVATE_KEY_PATH"
        private_key_path = os.environ.get("LIVE_PRIVATE_KEY_PATH", "")
    elif os.environ.get("KALSHI_PRIVATE_KEY_PATH", ""):
        private_key_path_source = "KALSHI_PRIVATE_KEY_PATH"
        private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
    elif os.environ.get("KALSHI_PRIVATE_KEY_FILE", ""):
        private_key_path_source = "KALSHI_PRIVATE_KEY_FILE"
        private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_FILE", "")
    else:
        private_key_path_source = "DEFAULT_LIVE_PRIVATE_KEY_PATH"
        private_key_path = DEFAULT_LIVE_PRIVATE_KEY_PATH

    return {
        "api_key": api_key,
        "api_key_source": api_key_source,
        "private_key_path": private_key_path,
        "private_key_path_source": private_key_path_source,
        "private_key_exists": bool(private_key_path and os.path.exists(private_key_path)),
    }


def load_live_private_key(path: str) -> bytes | None:
    if not path or not os.path.exists(path):
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


class KalshiHistoryClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.auth_config = get_live_auth_config()
        self.private_key_text = load_live_private_key(self.auth_config["private_key_path"])
        self._private_key_obj: Any = None
        self._auth_warned = False

    def _load_private_key_obj(self) -> Any:
        if self._private_key_obj is not None:
            return self._private_key_obj
        if not self.private_key_text:
            return None
        try:
            from cryptography.hazmat.primitives import serialization

            self._private_key_obj = serialization.load_pem_private_key(
                self.private_key_text,
                password=None,
            )
            return self._private_key_obj
        except Exception as exc:
            if not self._auth_warned:
                log(
                    "[KALSHI-HISTORY-BACKFILL-WARN]",
                    reason="private_key_load_failed",
                    error=exc.__class__.__name__,
                )
                self._auth_warned = True
            return None

    def _build_auth_headers(self, method: str, path: str) -> dict[str, str]:
        api_key = self.auth_config.get("api_key")
        private_key = self._load_private_key_obj()
        if not api_key or private_key is None:
            return {}
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            timestamp_ms = str(int(time.time() * 1000))
            path_to_sign = path.split("?", 1)[0]
            message = f"{timestamp_ms}{method.upper()}{path_to_sign}".encode("utf-8")
            signature = private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256().digest_size,
                ),
                hashes.SHA256(),
            )
            return {
                "KALSHI-ACCESS-KEY": api_key,
                "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
                "Accept": "application/json",
            }
        except Exception as exc:
            if not self._auth_warned:
                log(
                    "[KALSHI-HISTORY-BACKFILL-WARN]",
                    reason="auth_sign_failed",
                    error=exc.__class__.__name__,
                )
                self._auth_warned = True
            return {}

    def get_json(self, path: str, *, params: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
        url = build_live_request_url(path)
        headers = self._build_auth_headers("GET", path)
        response = self.session.get(url, params=params or {}, headers=headers or None, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def get_markets(
        self,
        *,
        limit: int,
        status: str,
        cursor: str | None,
        series_ticker: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "limit": limit,
            "status": status,
            "mve_filter": "exclude",
        }
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        data = self.get_json("/trade-api/v2/markets", params=params)
        markets = data.get("markets") if isinstance(data, dict) else []
        if not isinstance(markets, list):
            markets = []
        return [market for market in markets if isinstance(market, dict)], data.get("cursor") or data.get("next_cursor")

    def get_orderbook(self, ticker: str) -> dict[str, Any] | None:
        data = self.get_json(f"/trade-api/v2/markets/{ticker}/orderbook", timeout=10)
        if isinstance(data, dict):
            return data
        return None


def category_for_market(market: dict[str, Any], family: str | None) -> str | None:
    explicit = clean_text(first_value(market, ("category", "category_name", "market_type")))
    if explicit:
        return explicit
    family_upper = (family or "").upper()
    ticker_upper = str(market.get("ticker") or "").upper()
    text = f"{family_upper} {ticker_upper}"
    if any(
        prefix in text
        for prefix in (
            "KXNBA", "KXWNBA", "KXMLB", "KXNFL", "KXNHL", "KXNCAA", "KXCFB", "KXCBB",
            "KXATP", "KXWTA", "KXITF", "KXTENNIS", "KXGOLF", "KXEPL", "KXSOCCER",
            "KXUFC", "KXMMA", "KXBOX", "NBA", "MLB", "NFL", "NHL", "NCAA",
            "TENNIS", "GOLF", "SOCCER", "SPREAD", "TOTAL", "PLAYER PROP", "STRIKEOUT",
            "COMBO", "PARLAY",
        )
    ):
        return "sports"
    return None


def market_family(market: dict[str, Any]) -> str | None:
    explicit = clean_text(first_value(market, ("series_ticker", "series", "family")))
    if explicit:
        return explicit
    event_ticker = clean_text(market.get("event_ticker"))
    if event_ticker:
        return event_ticker.split("-", 1)[0]
    ticker = clean_text(market.get("ticker"))
    if ticker:
        return ticker.split("-", 1)[0]
    return None


def market_reference_datetime(market: dict[str, Any]) -> datetime | None:
    for key in (
        "settlement_time",
        "settled_time",
        "close_time",
        "expiration_time",
        "latest_expiration_time",
        "open_time",
    ):
        parsed = parse_market_datetime(market.get(key))
        if parsed is not None:
            return parsed
    return None


def should_include_family(market: dict[str, Any], family_spec: dict[str, Any]) -> bool:
    family = (market_family(market) or "").upper()
    ticker = str(market.get("ticker") or "").upper()
    title = str(market.get("title") or "").upper()
    event_ticker = str(market.get("event_ticker") or "").upper()
    series_matches = tuple(series.upper() for series in family_spec.get("series_tickers", ()))
    if family and family in series_matches:
        return True
    if any(value.startswith(series) for value in (ticker, event_ticker) for series in series_matches):
        return True
    tokens = tuple(str(token).upper() for token in family_spec.get("tokens", ()))
    combined = f"{family} {ticker} {title} {event_ticker}"
    return bool(tokens and all(token in combined for token in tokens))


def family_specs_for_filter(family_filter: str | None) -> list[dict[str, Any]]:
    if not family_filter:
        return [dict(spec) for spec in PRIORITY_FAMILIES]
    raw = family_filter.strip()
    upper = raw.upper()
    matching_specs = []
    for spec in PRIORITY_FAMILIES:
        names = {str(spec["name"]).upper()}
        names.update(str(series).upper() for series in spec.get("series_tickers", ()))
        if upper in names:
            matching_specs.append(dict(spec))
    if matching_specs:
        return matching_specs
    return [
        {
            "name": raw,
            "series_tickers": (upper,),
            "tokens": (upper,),
        }
    ]


def market_to_record(market: dict[str, Any], *, collected_at: str, family_name: str) -> dict[str, Any]:
    family = market_family(market)
    record = {
        "event_type": "market_history",
        "collected_at_utc": collected_at,
        "ticker": clean_text(market.get("ticker")),
        "title": clean_text(first_value(market, ("title", "subtitle"))),
        "category": category_for_market(market, family),
        "family": family,
        "series_ticker": clean_text(first_value(market, ("series_ticker", "series"))),
        "event_ticker": clean_text(market.get("event_ticker")),
        "priority_family": family_name,
        "status": clean_text(market.get("status")),
        "open_time": clean_text(market.get("open_time")),
        "close_time": clean_text(market.get("close_time")),
        "expiration_time": clean_text(first_value(market, ("expiration_time", "latest_expiration_time"))),
        "settlement_time": clean_text(first_value(market, ("settlement_time", "settled_time"))),
        "settlement_result": first_value(
            market,
            (
                "settlement_result",
                "settlement_value",
                "result",
                "expiration_value",
                "winning_outcome",
                "strike_result",
            ),
        ),
        "yes_bid": number_or_none(first_value(market, ("yes_bid_dollars", "yes_bid"))),
        "yes_ask": number_or_none(first_value(market, ("yes_ask_dollars", "yes_ask"))),
        "no_bid": number_or_none(first_value(market, ("no_bid_dollars", "no_bid"))),
        "no_ask": number_or_none(first_value(market, ("no_ask_dollars", "no_ask"))),
        "last_price": number_or_none(first_value(market, ("last_price_dollars", "last_price", "last_trade_price"))),
        "volume": number_or_none(first_value(market, ("volume", "volume_dollars", "volume_24h", "volume_24h_dollars"))),
        "liquidity": number_or_none(first_value(market, ("liquidity_dollars", "liquidity"))),
        "open_interest": number_or_none(first_value(market, ("open_interest", "open_interest_dollars"))),
        "raw_source": "kalshi_api",
    }
    return {key: value for key, value in record.items() if value is not None}


def orderbook_to_record(ticker: str, payload: dict[str, Any], *, collected_at: str) -> dict[str, Any] | None:
    orderbook = payload.get("orderbook") if isinstance(payload.get("orderbook"), dict) else payload
    if not isinstance(orderbook, dict):
        return None
    yes_levels = orderbook.get("yes") or orderbook.get("yes_dollars") or orderbook.get("yes_orders")
    no_levels = orderbook.get("no") or orderbook.get("no_dollars") or orderbook.get("no_orders")
    if not yes_levels and not no_levels:
        return None
    return {
        "event_type": "orderbook_snapshot",
        "collected_at_utc": collected_at,
        "ticker": ticker,
        "yes_levels": yes_levels,
        "no_levels": no_levels,
        "raw_source": "kalshi_api",
    }


def load_existing_market_tickers(path: Path) -> set[str]:
    tickers: set[str] = set()
    if not path.exists():
        return tickers
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("event_type") != "market_history":
                    continue
                ticker = clean_text(record.get("ticker"))
                if ticker:
                    tickers.add(ticker)
    except OSError as exc:
        log("[KALSHI-HISTORY-BACKFILL-WARN]", reason="existing_archive_read_failed", error=exc.__class__.__name__)
    return tickers


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def write_or_preview(path: Path, record: dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        log(
            "[KALSHI-HISTORY-BACKFILL-WRITE]",
            dry_run=True,
            event_type=record.get("event_type"),
            ticker=record.get("ticker"),
        )
        return
    append_jsonl(path, record)
    log(
        "[KALSHI-HISTORY-BACKFILL-WRITE]",
        dry_run=False,
        event_type=record.get("event_type"),
        ticker=record.get("ticker"),
    )


def collect_markets(args: argparse.Namespace) -> dict[str, Any]:
    env_count = load_dotenv(ENV_PATH)
    start_dt, end_dt = resolve_date_range(args)
    family_specs = family_specs_for_filter(args.family)
    existing_tickers = load_existing_market_tickers(OUTPUT_PATH)
    client = KalshiHistoryClient()
    collected_at = utc_now_iso()
    limit = max(0, args.limit)
    page_limit = min(200, max(1, min(limit or 200, 200)))

    summary = {
        "market_history_written": 0,
        "orderbook_snapshot_attempted": 0,
        "orderbook_snapshot_written": 0,
        "orderbook_snapshot_unavailable": 0,
        "price_history_written": 0,
        "duplicates_skipped": 0,
        "date_skipped": 0,
        "api_warnings": 0,
        "families": [spec["name"] for spec in family_specs],
        "statuses_attempted": list(MARKET_STATUSES),
        "price_history_available": PRICE_HISTORY_ENDPOINTS_AVAILABLE,
    }

    log(
        "[KALSHI-HISTORY-BACKFILL-START]",
        dry_run=args.dry_run,
        output_path=str(OUTPUT_PATH),
        env_file=str(ENV_PATH),
        env_values_loaded=env_count,
        base_url=get_live_base_url(),
        auth_key_source=client.auth_config.get("api_key_source"),
        private_key_source=client.auth_config.get("private_key_path_source"),
        private_key_exists=client.auth_config.get("private_key_exists"),
        start_date=start_dt.date().isoformat(),
        end_date=end_dt.date().isoformat(),
        limit=limit,
        family=args.family,
    )

    if not PRICE_HISTORY_ENDPOINTS_AVAILABLE:
        log("[KALSHI-HISTORY-BACKFILL-WARN]", reason="price_history_endpoint_not_available_in_existing_client")

    if limit == 0:
        log("[KALSHI-HISTORY-BACKFILL-DONE]", **summary)
        return summary

    orderbook_unavailable_warned = False

    for family_spec in family_specs:
        if summary["market_history_written"] >= limit:
            break
        series_tickers = tuple(family_spec.get("series_tickers") or (None,))
        for series_ticker in series_tickers:
            if summary["market_history_written"] >= limit:
                break
            for status in MARKET_STATUSES:
                if summary["market_history_written"] >= limit:
                    break
                cursor = None
                reached_start_boundary = False
                while summary["market_history_written"] < limit:
                    try:
                        markets, next_cursor = client.get_markets(
                            limit=page_limit,
                            status=status,
                            cursor=cursor,
                            series_ticker=series_ticker,
                        )
                    except requests.HTTPError as exc:
                        status_code = getattr(exc.response, "status_code", None)
                        log(
                            "[KALSHI-HISTORY-BACKFILL-WARN]",
                            reason="markets_request_http_error",
                            family=family_spec["name"],
                            series_ticker=series_ticker,
                            status=status,
                            status_code=status_code,
                        )
                        summary["api_warnings"] += 1
                        break
                    except requests.RequestException as exc:
                        log(
                            "[KALSHI-HISTORY-BACKFILL-WARN]",
                            reason="markets_request_failed",
                            family=family_spec["name"],
                            series_ticker=series_ticker,
                            status=status,
                            error=exc.__class__.__name__,
                        )
                        summary["api_warnings"] += 1
                        break

                    if not markets:
                        break

                    oldest_dt: datetime | None = None
                    for market in markets:
                        if summary["market_history_written"] >= limit:
                            break
                        ticker = clean_text(market.get("ticker"))
                        if not ticker:
                            continue
                        if not should_include_family(market, family_spec):
                            continue
                        ref_dt = market_reference_datetime(market)
                        if ref_dt is not None:
                            oldest_dt = ref_dt if oldest_dt is None else min(oldest_dt, ref_dt)
                            if ref_dt < start_dt:
                                summary["date_skipped"] += 1
                                continue
                            if ref_dt > end_dt:
                                summary["date_skipped"] += 1
                                continue
                        if ticker in existing_tickers:
                            summary["duplicates_skipped"] += 1
                            log(
                                "[KALSHI-HISTORY-BACKFILL-SKIP]",
                                reason="duplicate_ticker",
                                ticker=ticker,
                            )
                            continue

                        market_record = market_to_record(market, collected_at=collected_at, family_name=family_spec["name"])
                        write_or_preview(OUTPUT_PATH, market_record, dry_run=args.dry_run)
                        existing_tickers.add(ticker)
                        summary["market_history_written"] += 1

                        try:
                            summary["orderbook_snapshot_attempted"] += 1
                            orderbook_payload = client.get_orderbook(ticker)
                            orderbook_record = orderbook_to_record(ticker, orderbook_payload or {}, collected_at=collected_at)
                            if orderbook_record:
                                write_or_preview(OUTPUT_PATH, orderbook_record, dry_run=args.dry_run)
                                summary["orderbook_snapshot_written"] += 1
                            else:
                                summary["orderbook_snapshot_unavailable"] += 1
                                if not orderbook_unavailable_warned:
                                    log(
                                        "[KALSHI-HISTORY-BACKFILL-WARN]",
                                        reason="orderbook_snapshot_empty_or_unavailable",
                                    )
                                    orderbook_unavailable_warned = True
                        except requests.HTTPError as exc:
                            summary["orderbook_snapshot_unavailable"] += 1
                            if not orderbook_unavailable_warned:
                                status_code = getattr(exc.response, "status_code", None)
                                log(
                                    "[KALSHI-HISTORY-BACKFILL-WARN]",
                                    reason="orderbook_snapshot_unavailable",
                                    status_code=status_code,
                                )
                                orderbook_unavailable_warned = True
                        except requests.RequestException as exc:
                            summary["orderbook_snapshot_unavailable"] += 1
                            if not orderbook_unavailable_warned:
                                log(
                                    "[KALSHI-HISTORY-BACKFILL-WARN]",
                                    reason="orderbook_snapshot_request_failed",
                                    error=exc.__class__.__name__,
                                )
                                orderbook_unavailable_warned = True

                    if oldest_dt is not None and oldest_dt < start_dt:
                        reached_start_boundary = True
                    if reached_start_boundary or not next_cursor:
                        break
                    cursor = next_cursor

    log("[KALSHI-HISTORY-BACKFILL-DONE]", **summary)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill historical Kalshi market metadata to JSONL.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum new market_history records to collect.")
    parser.add_argument("--family", default=None, help="Optional priority family name or Kalshi series ticker filter.")
    parser.add_argument("--days-back", type=int, default=365, help="Default lookback range when explicit dates are absent.")
    parser.add_argument("--start-date", default=None, help="Explicit UTC start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", default=None, help="Explicit UTC end date, YYYY-MM-DD.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and log records without writing JSONL.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.days_back < 0:
        parser.error("--days-back must be >= 0")
    try:
        collect_markets(args)
    except Exception as exc:
        log("[KALSHI-HISTORY-BACKFILL-WARN]", reason="unhandled_backfill_failure", error=exc.__class__.__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

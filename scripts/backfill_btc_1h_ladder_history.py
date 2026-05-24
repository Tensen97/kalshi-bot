#!/usr/bin/env python3
"""Backfill compact BTC 1H ladder history into local SQLite.

This is intentionally standalone from live execution. It fetches available
Kalshi market metadata, stores compact BTC ladder records, and recomputes
advisory summaries consumed by the live BTC ladder scorer.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from memory.btc_ladder_history_store import (  # noqa: E402
    BTCLadderHistoryStore,
    DEFAULT_DB_PATH,
    first_value,
    is_btc_ladder_market,
    ladder_group_key,
    market_family,
    normalize_outcome,
    normalize_price,
    parse_btc_strike,
    parse_datetime,
    safe_float,
    spread_bucket,
)
from scripts.backfill_kalshi_market_history import (  # noqa: E402
    ENV_PATH,
    MARKET_STATUSES,
    KalshiHistoryClient,
    clean_text,
    get_live_base_url,
    load_dotenv,
    parse_date,
)


MAX_DAYS_BACK = 365
DEFAULT_SERIES_TICKERS = ("KXBTC1H", "KXBTC", "KXBTCPRICE", "KXBTCUSD")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(tag: str, **payload: Any) -> None:
    print(f"{tag} {json.dumps(payload, sort_keys=True, default=str)}", flush=True)


def resolve_date_range(args: argparse.Namespace) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if args.start_date or args.end_date:
        end_dt = parse_date(args.end_date, end_of_day=True) if args.end_date else now
        start_dt = parse_date(args.start_date) if args.start_date else end_dt - timedelta(days=MAX_DAYS_BACK)
        if (end_dt - start_dt).days > MAX_DAYS_BACK:
            start_dt = end_dt - timedelta(days=MAX_DAYS_BACK)
        return start_dt, end_dt
    days = max(0, min(MAX_DAYS_BACK, int(args.days_back or MAX_DAYS_BACK)))
    return now - timedelta(days=days), now


def market_reference_datetime(market: dict[str, Any]) -> datetime | None:
    for key in ("settlement_time", "settled_time", "close_time", "expiration_time", "latest_expiration_time", "open_time"):
        parsed = parse_datetime(market.get(key))
        if parsed is not None:
            return parsed
    return None


def number_or_none(value: Any) -> float | int | None:
    number = safe_float(value, None)
    if number is None:
        return None
    return int(number) if float(number).is_integer() else number


def btc_ladder_record_from_market(
    market: dict[str, Any],
    *,
    backfill_run_id: str,
    orderbook_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(market, dict) or not is_btc_ladder_market(market):
        return None
    ticker = clean_text(market.get("ticker"))
    if not ticker:
        return None
    family = market_family(market)
    strike = parse_btc_strike(
        market.get("title"),
        market.get("subtitle"),
        market.get("rules_primary"),
        market.get("rules_secondary"),
        market.get("event_title"),
        ticker,
    )
    if strike is None:
        return None
    yes_bid = normalize_price(first_value(market, "yes_bid_dollars", "yes_bid"))
    yes_ask = normalize_price(first_value(market, "yes_ask_dollars", "yes_ask"))
    no_bid = normalize_price(first_value(market, "no_bid_dollars", "no_bid"))
    no_ask = normalize_price(first_value(market, "no_ask_dollars", "no_ask"))
    last_price = normalize_price(first_value(market, "last_price_dollars", "last_price", "last_trade_price"))
    previous_price = normalize_price(first_value(market, "previous_price_dollars", "previous_price", "previous_yes_bid_dollars", "previous_yes_ask_dollars"))
    spread = None
    if yes_bid is not None and yes_ask is not None and yes_ask >= yes_bid:
        spread = round(yes_ask - yes_bid, 6)
    elif no_bid is not None and no_ask is not None and no_ask >= no_bid:
        spread = round(no_ask - no_bid, 6)
    settlement_value = number_or_none(first_value(market, "settlement_value", "expiration_value", "final_settlement"))
    record = {
        "ticker": ticker,
        "event_ticker": clean_text(market.get("event_ticker")),
        "family": family,
        "ladder_group": ladder_group_key(market, family),
        "title": clean_text(market.get("title")),
        "subtitle": clean_text(market.get("subtitle")),
        "rules_primary": clean_text(market.get("rules_primary")),
        "rules_secondary": clean_text(market.get("rules_secondary")),
        "status": clean_text(market.get("status")),
        "open_time": clean_text(market.get("open_time")),
        "close_time": clean_text(market.get("close_time")),
        "expiration_time": clean_text(first_value(market, "expiration_time", "latest_expiration_time")),
        "settlement_time": clean_text(first_value(market, "settlement_time", "settled_time")),
        "source_time": (market_reference_datetime(market) or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        "strike": strike,
        "settlement_value": settlement_value,
        "outcome": normalize_outcome(
            first_value(market, "settlement_result", "result", "winning_outcome", "strike_result"),
            strike=strike,
            settlement_value=settlement_value,
        ),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "last_price": last_price,
        "previous_price": previous_price,
        "spread": spread,
        "spread_bucket": spread_bucket(spread),
        "liquidity": number_or_none(first_value(market, "liquidity_dollars", "liquidity")),
        "volume": number_or_none(first_value(market, "volume", "volume_dollars", "volume_24h", "volume_24h_dollars", "volume_fp", "volume_24h_fp")),
        "open_interest": number_or_none(first_value(market, "open_interest", "open_interest_dollars", "open_interest_fp")),
        "data_source": "kalshi_api:markets",
        "backfill_run_id": backfill_run_id,
    }
    if orderbook_payload:
        record["orderbook_snapshot"] = orderbook_payload
    return {key: value for key, value in record.items() if value is not None}


def series_tickers_for_args(args: argparse.Namespace) -> list[str | None]:
    if args.series_ticker:
        return [str(args.series_ticker).strip().upper()]
    tickers: list[str | None] = list(DEFAULT_SERIES_TICKERS)
    if args.broad_scan:
        tickers.append(None)
    return tickers


def run_backfill(args: argparse.Namespace) -> dict[str, Any]:
    env_count = load_dotenv(ENV_PATH)
    start_dt, end_dt = resolve_date_range(args)
    db_path = Path(args.db_path or DEFAULT_DB_PATH)
    store = BTCLadderHistoryStore(db_path=db_path, log_fn=print, initialize=not args.dry_run or args.audit_only)
    if args.audit_only:
        audit = store.audit(recompute=True)
        log("[BTC-1H-HISTORY-LEARNED]", audit_only=True, learned_buckets=audit.get("learned_buckets"), total_records=audit.get("total_records"))
        return {"audit": audit}

    client = KalshiHistoryClient()
    seen_tickers = store.seen_tickers() if db_path.exists() else set()
    run_id = args.run_id or f"btc-1h-history-{int(time.time())}"
    limit = None if int(args.limit or 0) == 0 else max(1, int(args.limit))
    page_limit = max(1, min(250, int(args.page_limit or 100)))
    batch_size = max(1, int(args.batch_size or 100))
    summary = {
        "run_id": run_id,
        "dry_run": bool(args.dry_run),
        "db_path": str(db_path),
        "markets_seen": 0,
        "btc_candidates_seen": 0,
        "ladder_groups_found": 0,
        "records_stored": 0,
        "duplicates_skipped": 0,
        "malformed_skipped": 0,
        "date_skipped": 0,
        "api_warnings": 0,
        "orderbook_snapshots": 0,
        "start_date": start_dt.date().isoformat(),
        "end_date": end_dt.date().isoformat(),
        "series_tickers": series_tickers_for_args(args),
    }
    log(
        "[BTC-1H-HISTORY-BACKFILL-START]",
        **summary,
        env_file=str(ENV_PATH),
        env_values_loaded=env_count,
        base_url=get_live_base_url(),
        auth_key_source=client.auth_config.get("api_key_source"),
        private_key_exists=client.auth_config.get("private_key_exists"),
        limit=args.limit,
        page_limit=page_limit,
        batch_size=batch_size,
    )

    pending: list[dict[str, Any]] = []
    groups_found: set[str] = set()

    def progress_count() -> int:
        return int(summary["records_stored"]) + int(summary["duplicates_skipped"]) + int(summary["malformed_skipped"])

    def limit_reached() -> bool:
        return limit is not None and progress_count() >= limit

    def flush_pending(force: bool = False) -> None:
        if args.dry_run or not pending:
            return
        if not force and len(pending) < batch_size:
            return
        batch = list(pending)
        pending.clear()
        result = store.insert_records(batch, batch_size=batch_size)
        summary["records_stored"] += int(result.get("stored") or 0)
        summary["duplicates_skipped"] += int(result.get("duplicates") or 0)
        summary["malformed_skipped"] += int(result.get("malformed") or 0)
        log("[BTC-1H-HISTORY-LEARNED]", batch_size=len(batch), stored=result.get("stored"), duplicates=result.get("duplicates"), malformed=result.get("malformed"))

    for series_ticker in series_tickers_for_args(args):
        if limit_reached():
            break
        for status in MARKET_STATUSES:
            if limit_reached():
                break
            cursor = None
            while True:
                try:
                    markets, next_cursor = client.get_markets(
                        limit=page_limit,
                        status=status,
                        cursor=cursor,
                        series_ticker=series_ticker,
                    )
                except requests.HTTPError as exc:
                    summary["api_warnings"] += 1
                    log(
                        "[BTC-1H-HISTORY-BACKFILL-SKIP]",
                        reason="markets_request_http_error",
                        series_ticker=series_ticker,
                        status=status,
                        status_code=getattr(exc.response, "status_code", None),
                    )
                    break
                except requests.RequestException as exc:
                    summary["api_warnings"] += 1
                    log(
                        "[BTC-1H-HISTORY-BACKFILL-SKIP]",
                        reason="markets_request_failed",
                        series_ticker=series_ticker,
                        status=status,
                        error=exc.__class__.__name__,
                    )
                    break
                if not markets:
                    break
                reached_start = False
                for market in markets:
                    if limit_reached():
                        break
                    summary["markets_seen"] += 1
                    if not is_btc_ladder_market(market):
                        continue
                    summary["btc_candidates_seen"] += 1
                    ref_dt = market_reference_datetime(market)
                    if ref_dt is not None:
                        if ref_dt < start_dt:
                            summary["date_skipped"] += 1
                            reached_start = True
                            continue
                        if ref_dt > end_dt:
                            summary["date_skipped"] += 1
                            continue
                    ticker = clean_text(market.get("ticker"))
                    if not ticker:
                        summary["malformed_skipped"] += 1
                        continue
                    if ticker in seen_tickers:
                        summary["duplicates_skipped"] += 1
                        log("[BTC-1H-HISTORY-BACKFILL-SKIP]", reason="duplicate_ticker", ticker=ticker)
                        continue
                    orderbook_payload = None
                    if args.include_orderbook:
                        try:
                            orderbook_payload = client.get_orderbook(ticker)
                            if orderbook_payload:
                                summary["orderbook_snapshots"] += 1
                        except requests.RequestException as exc:
                            summary["api_warnings"] += 1
                            log("[BTC-1H-HISTORY-BACKFILL-SKIP]", reason="orderbook_unavailable", ticker=ticker, error=exc.__class__.__name__)
                    record = btc_ladder_record_from_market(market, backfill_run_id=run_id, orderbook_payload=orderbook_payload)
                    if not record:
                        summary["malformed_skipped"] += 1
                        log("[BTC-1H-HISTORY-BACKFILL-SKIP]", reason="not_parseable_btc_ladder", ticker=ticker)
                        continue
                    groups_found.add(str(record.get("ladder_group") or ""))
                    if args.dry_run:
                        summary["records_stored"] += 1
                        if summary["records_stored"] <= 5 or summary["records_stored"] % batch_size == 0:
                            log(
                                "[BTC-1H-HISTORY-LEARNED]",
                                dry_run=True,
                                ticker=record.get("ticker"),
                                ladder_group=record.get("ladder_group"),
                                strike=record.get("strike"),
                                outcome=record.get("outcome"),
                                stored_preview=summary["records_stored"],
                            )
                    else:
                        pending.append(record)
                        seen_tickers.add(ticker)
                        flush_pending(force=len(pending) >= batch_size or limit_reached())
                if args.sleep_seconds > 0:
                    time.sleep(float(args.sleep_seconds))
                if reached_start or not next_cursor or limit_reached():
                    break
                cursor = next_cursor

    flush_pending(force=True)
    summary["ladder_groups_found"] = len([group for group in groups_found if group])
    audit = None
    if args.dry_run:
        log("[BTC-1H-HISTORY-BACKFILL-DONE]", **summary, learned_buckets=[])
    else:
        audit = store.audit(recompute=True)
        log(
            "[BTC-1H-HISTORY-BACKFILL-DONE]",
            **summary,
            total_records=audit.get("total_records"),
            learned_buckets=audit.get("learned_buckets"),
            oldest_source_time=audit.get("oldest_source_time"),
            newest_source_time=audit.get("newest_source_time"),
        )
    return {"summary": summary, "audit": audit}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill BTC 1H ladder history into local SQLite.")
    parser.add_argument("--days-back", type=int, default=MAX_DAYS_BACK, help="Lookback days, capped at 365.")
    parser.add_argument("--start-date", default=None, help="Explicit UTC start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", default=None, help="Explicit UTC end date, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum new BTC ladder records to collect. Use 0 for no explicit limit.")
    parser.add_argument("--page-limit", type=int, default=100, help="Kalshi markets page size, max 250.")
    parser.add_argument("--batch-size", type=int, default=100, help="Persist every N records.")
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="Small delay between API pages.")
    parser.add_argument("--series-ticker", default=None, help="Optional single Kalshi series ticker to query.")
    parser.add_argument("--broad-scan", action="store_true", help="Also scan generic markets pages and filter for BTC ladder names.")
    parser.add_argument("--include-orderbook", action="store_true", help="Attempt current orderbook snapshot fetches where available.")
    parser.add_argument("--run-id", default=None, help="Optional stable backfill run id.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite output path.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch/parse without writing SQLite.")
    parser.add_argument("--audit-only", action="store_true", help="Only print current BTC ladder history audit.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.days_back < 0:
        parser.error("--days-back must be >= 0")
    if args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.batch_size <= 0:
        parser.error("--batch-size must be > 0")
    try:
        run_backfill(args)
    except Exception as exc:
        log("[BTC-1H-HISTORY-BACKFILL-SKIP]", reason="unhandled_backfill_failure", error=exc.__class__.__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

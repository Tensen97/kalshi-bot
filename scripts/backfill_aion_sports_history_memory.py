#!/usr/bin/env python3
"""Backfill compact sports history into AION persistent learning memory."""

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

from memory.aion_persistent_intelligence import AIONPersistentIntelligence  # noqa: E402
from memory.aion_sports_history_store import AIONSportsHistoryStore, DEFAULT_DB_PATH  # noqa: E402
from runtime.paths import runtime_memory_file  # noqa: E402
from scripts.backfill_kalshi_market_history import (  # noqa: E402
    ENV_PATH,
    MARKET_STATUSES,
    PRIORITY_FAMILIES,
    KalshiHistoryClient,
    category_for_market,
    clean_text,
    first_value,
    get_live_base_url,
    load_dotenv,
    market_family,
    market_reference_datetime,
    number_or_none,
    parse_date,
    should_include_family,
)


MAX_DAYS_BACK = 730
SPORTS_STATUS_ORDER = tuple(status for status in MARKET_STATUSES if status in {"settled", "closed", "resolved"})
SPORTS_FAMILY_NAMES = tuple(
    spec
    for spec in PRIORITY_FAMILIES
    if "btc" not in str(spec.get("name", "")).lower()
    and "crypto" not in str(spec.get("name", "")).lower()
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(tag: str, **fields: Any) -> None:
    print(f"{tag} {json.dumps(fields, sort_keys=True, default=str)}", flush=True)


def resolve_date_range(args: argparse.Namespace) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if args.start_date or args.end_date:
        end_dt = parse_date(args.end_date, end_of_day=True) if args.end_date else now
        start_dt = parse_date(args.start_date) if args.start_date else end_dt - timedelta(days=MAX_DAYS_BACK)
        if (end_dt - start_dt).days > MAX_DAYS_BACK:
            start_dt = end_dt - timedelta(days=MAX_DAYS_BACK)
        return start_dt, end_dt
    days_back = max(0, min(MAX_DAYS_BACK, int(args.days_back or MAX_DAYS_BACK)))
    return now - timedelta(days=days_back), now


def sports_family_specs(family_filter: str | None) -> list[dict[str, Any]]:
    if not family_filter:
        return [dict(spec) for spec in SPORTS_FAMILY_NAMES]
    raw = str(family_filter or "").strip()
    upper = raw.upper()
    matching = []
    for spec in SPORTS_FAMILY_NAMES:
        names = {str(spec.get("name") or "").upper()}
        names.update(str(series).upper() for series in spec.get("series_tickers", ()))
        if upper in names:
            matching.append(dict(spec))
    if matching:
        return matching
    return [{"name": raw, "series_tickers": (upper,), "tokens": (upper,)}]


def normalized_price(value: Any) -> float | None:
    number = number_or_none(value)
    if number is None:
        return None
    number = float(number)
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    if number < 0.0 or number > 1.0:
        return None
    return round(number, 6)


def sport_type_for_market(market: dict[str, Any], family: str | None) -> str:
    text = " ".join(
        str(value or "").upper()
        for value in (
            family,
            market.get("ticker"),
            market.get("title"),
            market.get("subtitle"),
            market.get("event_ticker"),
        )
    )
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


def is_sports_market(market: dict[str, Any], family: str | None) -> bool:
    category = str(category_for_market(market, family) or "").strip().lower()
    if category == "sports":
        return True
    text = " ".join(
        str(value or "").upper()
        for value in (
            family,
            market.get("ticker"),
            market.get("title"),
            market.get("subtitle"),
            market.get("event_ticker"),
        )
    )
    sports_tokens = (
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
        "KXTENNIS",
        "KXGOLF",
        "KXEPL",
        "KXSOCCER",
        "KXUFC",
        "KXMMA",
        "KXBOX",
        "NBA",
        "MLB",
        "NFL",
        "NHL",
        "NCAA",
        "TENNIS",
        "GOLF",
        "SOCCER",
        "SPREAD",
        "TOTAL",
        "STRIKEOUT",
        "PLAYER",
        "PROP",
        "PARLAY",
        "COMBO",
    )
    return any(token in text for token in sports_tokens)


def market_to_sports_history_record(market: dict[str, Any], *, backfill_run_id: str) -> dict[str, Any] | None:
    ticker = clean_text(market.get("ticker"))
    if not ticker:
        return None
    family = market_family(market)
    if not is_sports_market(market, family):
        return None
    ref_dt = market_reference_datetime(market)
    timestamp = ref_dt.isoformat().replace("+00:00", "Z") if ref_dt else utc_now_iso()
    yes_bid = normalized_price(first_value(market, ("yes_bid_dollars", "yes_bid")))
    yes_ask = normalized_price(first_value(market, ("yes_ask_dollars", "yes_ask")))
    no_bid = normalized_price(first_value(market, ("no_bid_dollars", "no_bid")))
    no_ask = normalized_price(first_value(market, ("no_ask_dollars", "no_ask")))
    last_price = normalized_price(first_value(market, ("last_price_dollars", "last_price", "last_trade_price")))
    previous_price = normalized_price(first_value(market, ("previous_price_dollars", "previous_price", "previous_yes_bid_dollars", "previous_yes_ask_dollars")))
    spread = None
    if yes_bid is not None and yes_ask is not None:
        spread = max(0.0, yes_ask - yes_bid)
    elif no_bid is not None and no_ask is not None:
        spread = max(0.0, no_ask - no_bid)
    return {
        "timestamp": timestamp,
        "ticker": ticker,
        "market_title": clean_text(first_value(market, ("title", "subtitle"))),
        "sport_type": sport_type_for_market(market, family),
        "family": family,
        "category": "sports",
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_price": last_price or yes_ask or yes_bid,
        "no_price": no_ask or no_bid,
        "last_price": last_price,
        "previous_price": previous_price,
        "spread": spread,
        "liquidity": number_or_none(first_value(market, ("liquidity_dollars", "liquidity"))),
        "volume": number_or_none(first_value(market, ("volume", "volume_dollars", "volume_24h", "volume_24h_dollars"))),
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
        "open_time": clean_text(market.get("open_time")),
        "close_time": clean_text(market.get("close_time")),
        "expiration_time": clean_text(first_value(market, ("expiration_time", "latest_expiration_time"))),
        "settlement_time": clean_text(first_value(market, ("settlement_time", "settled_time"))),
        "data_source": "kalshi_api:markets",
        "backfill_run_id": backfill_run_id,
    }


def emit_learning_logs(audit: dict[str, Any], *, source: str) -> None:
    total_records = audit.get("total_records", audit.get("sports_history_record_count"))
    oldest = audit.get("oldest_source_time", audit.get("sports_history_oldest_timestamp_ts"))
    newest = audit.get("newest_source_time", audit.get("sports_history_newest_timestamp_ts"))
    family_count = len(audit.get("records_by_family") or audit.get("sports_history_families_covered") or [])
    log(
        "[AION-SPORTS-HISTORY-LEARN]",
        source=source,
        total_sports_records=total_records,
        oldest_source_time=oldest,
        newest_source_time=newest,
        family_count=family_count,
    )
    for family in (audit.get("family_performance") or audit.get("sports_history_profitable_families") or [])[:8]:
        log("[AION-SPORTS-FAMILY-LEARN]", source=source, **family)
    for bucket in (audit.get("asymmetry_bucket_performance") or audit.get("sports_history_asymmetry_bucket_performance") or [])[:8]:
        log("[AION-SPORTS-ASYMMETRY-LEARN]", source=source, **bucket)


def json_sports_history_records(engine: AIONPersistentIntelligence) -> list[dict[str, Any]]:
    memory = getattr(engine, "distilled_learning", {}) if engine is not None else {}
    sports_history = memory.get("sports_history") if isinstance(memory, dict) else {}
    records = sports_history.get("records") if isinstance(sports_history, dict) else []
    return [record for record in records or [] if isinstance(record, dict)]


def backfill(args: argparse.Namespace) -> dict[str, Any]:
    env_count = load_dotenv(ENV_PATH)
    start_dt, end_dt = resolve_date_range(args)
    family_specs = sports_family_specs(args.family)
    state_path = Path(runtime_memory_file("aion_persistent_intelligence.json"))
    sqlite_path = Path(DEFAULT_DB_PATH)
    engine = AIONPersistentIntelligence(state_file=state_path, enabled=not args.dry_run)
    store = None if args.dry_run else AIONSportsHistoryStore(db_path=sqlite_path, log_fn=print)
    migration = {"stored": 0, "duplicates": 0, "malformed": 0, "errors": 0}
    if store is not None:
        migration = store.migrate_json_records(json_sports_history_records(engine), batch_size=max(100, int(args.batch_size or 250)))
    client = KalshiHistoryClient()
    run_id = args.run_id or f"sports-history-{int(time.time())}"
    limit = None if int(args.limit or 0) == 0 else max(1, int(args.limit))
    batch_size = max(1, int(args.batch_size or 100))
    page_limit = max(1, min(250, int(args.page_limit or 100)))
    summary = {
        "run_id": run_id,
        "dry_run": bool(args.dry_run),
        "market_seen": 0,
        "stored": 0,
        "duplicates": 0,
        "malformed": 0,
        "errors": 0,
        "date_skipped": 0,
        "non_sports_skipped": 0,
        "api_warnings": 0,
        "sqlite_db_path": str(sqlite_path),
        "migration": migration,
        "families": [spec.get("name") for spec in family_specs],
        "start_date": start_dt.date().isoformat(),
        "end_date": end_dt.date().isoformat(),
    }

    def limit_progress() -> int:
        if args.dry_run:
            return int(summary["stored"])
        return int(summary["stored"]) + int(summary["duplicates"]) + int(summary["malformed"]) + int(summary["errors"])

    def limit_reached() -> bool:
        return limit is not None and limit_progress() >= int(limit)

    def remaining_limit() -> int:
        if limit is None:
            return batch_size
        return max(0, int(limit) - limit_progress())

    if args.audit_only:
        audit = store.audit(recompute=True) if store is not None else engine.audit_distilled_learning()
        log("[AION-SPORTS-BACKFILL-SQLITE]", audit_only=True, **summary, audit=audit)
        emit_learning_logs(audit, source="audit_only")
        return summary

    log(
        "[AION-SPORTS-BACKFILL-START]",
        **summary,
        env_file=str(ENV_PATH),
        env_values_loaded=env_count,
        base_url=get_live_base_url(),
        limit=args.limit,
        page_limit=page_limit,
        batch_size=batch_size,
        max_records=args.max_records,
    )

    pending_records: list[dict[str, Any]] = []

    def flush_pending(*, force=False):
        if args.dry_run or store is None or not pending_records:
            return
        if not force and len(pending_records) < batch_size:
            return
        batch = list(pending_records)
        pending_records.clear()
        result = store.insert_records(batch, batch_size=batch_size)
        summary["stored"] += int(result.get("stored") or 0)
        summary["duplicates"] += int(result.get("duplicates") or 0)
        summary["malformed"] += int(result.get("malformed") or 0)
        summary["errors"] += int(result.get("errors") or 0)
        log("[AION-SPORTS-BACKFILL-SQLITE]", batch_size=len(batch), **summary)

    for family_spec in family_specs:
        if limit_reached():
            break
        for series_ticker in tuple(family_spec.get("series_tickers") or (None,)):
            if limit_reached():
                break
            for status in SPORTS_STATUS_ORDER:
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
                            "[AION-SPORTS-BACKFILL-PROGRESS]",
                            reason="markets_request_http_error",
                            status_code=getattr(exc.response, "status_code", None),
                            family=family_spec.get("name"),
                            series_ticker=series_ticker,
                            status=status,
                        )
                        break
                    except requests.RequestException as exc:
                        summary["api_warnings"] += 1
                        log(
                            "[AION-SPORTS-BACKFILL-PROGRESS]",
                            reason="markets_request_failed",
                            error=exc.__class__.__name__,
                            family=family_spec.get("name"),
                            series_ticker=series_ticker,
                            status=status,
                        )
                        break
                    if not markets:
                        break
                    reached_start = False
                    for market in markets:
                        if limit_reached():
                            break
                        summary["market_seen"] += 1
                        if not should_include_family(market, family_spec):
                            continue
                        ref_dt = market_reference_datetime(market)
                        if ref_dt is not None:
                            if ref_dt < start_dt:
                                summary["date_skipped"] += 1
                                reached_start = True
                                continue
                            if ref_dt > end_dt:
                                summary["date_skipped"] += 1
                                continue
                        record = market_to_sports_history_record(market, backfill_run_id=run_id)
                        if not record:
                            summary["non_sports_skipped"] += 1
                            continue
                        if args.dry_run:
                            summary["stored"] += 1
                            if summary["stored"] <= 5 or summary["stored"] % batch_size == 0:
                                log(
                                    "[AION-SPORTS-BACKFILL-STORE]",
                                    dry_run=True,
                                    ticker=record.get("ticker"),
                                    family=record.get("family"),
                                    sport_type=record.get("sport_type"),
                                    stored=summary["stored"],
                            )
                            continue
                        pending_records.append(record)
                        remaining = remaining_limit()
                        flush_pending(force=remaining <= 0 or len(pending_records) >= min(batch_size, max(1, remaining)))
                    if limit_reached():
                        break
                    if args.sleep_seconds > 0:
                        time.sleep(float(args.sleep_seconds))
                    if reached_start or not next_cursor:
                        break
                    cursor = next_cursor

    flush_pending(force=True)
    audit = store.audit(recompute=True) if store is not None else engine.audit_distilled_learning()
    summary["audit"] = audit
    log("[AION-SPORTS-BACKFILL-SQLITE]", done=True, **summary)
    emit_learning_logs(audit, source="backfill")
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill 24-month Kalshi sports history into AION persistent memory.")
    parser.add_argument("--days-back", type=int, default=MAX_DAYS_BACK, help="Lookback days, capped at 730 unless narrower dates are provided.")
    parser.add_argument("--start-date", default=None, help="Explicit UTC start date, YYYY-MM-DD. Range is capped to 730 days.")
    parser.add_argument("--end-date", default=None, help="Explicit UTC end date, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum new sports markets to store. Use 0 for no explicit limit.")
    parser.add_argument("--family", default=None, help="Optional sports family name or series ticker.")
    parser.add_argument("--batch-size", type=int, default=100, help="Persist and log every N stored records.")
    parser.add_argument("--page-limit", type=int, default=100, help="Kalshi markets page size, max 250.")
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="Small delay between API pages.")
    parser.add_argument("--max-records", type=int, default=10000, help="Legacy JSON memory cap retained for compatibility; SQLite storage is not capped by this.")
    parser.add_argument("--run-id", default=None, help="Optional stable backfill run id.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and log without writing memory.")
    parser.add_argument("--audit-only", action="store_true", help="Only print current sports-history audit.")
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
    if args.max_records < 100:
        parser.error("--max-records must be >= 100")
    try:
        backfill(args)
    except Exception as exc:
        log("[AION-SPORTS-BACKFILL-PROGRESS]", reason="unhandled_backfill_failure", error=exc.__class__.__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SPORTS_FAMILY_PREFIXES = (
    "KXNBA",
    "KXWNBA",
    "KXMLB",
    "KXNFL",
    "KXNHL",
    "KXNCAA",
    "KXNCAAB",
    "KXNCAAF",
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
)

SPORTS_TEXT_MARKERS = (
    " NBA ",
    " WNBA ",
    " MLB ",
    " NFL ",
    " NHL ",
    " NCAA ",
    " NCAAB ",
    " NCAAF ",
    " CFB ",
    " CBB ",
    " TENNIS ",
    " ATP ",
    " WTA ",
    " GOLF ",
    " SOCCER ",
    " EPL ",
    " UFC ",
    " MMA ",
    " SPREAD ",
    " TOTAL ",
    " PLAYER PROP ",
    " STRIKEOUT ",
    " COMBO ",
    " PARLAY ",
)

ACTIVE_SOURCE_SPECS = (
    {"path": "data/aion_learning_archive.jsonl", "kind": "jsonl", "label": "aion_learning_archive"},
    {"path": "data/kalshi_market_history.jsonl", "kind": "jsonl", "label": "kalshi_market_history"},
    {"path": "runtime/state/live_audit_log.csv", "kind": "csv", "label": "live_audit_log"},
    {"path": "runtime/state/kalshi_trade_log.csv", "kind": "csv", "label": "kalshi_trade_log"},
    {"path": "runtime/memory/aion_persistent_intelligence.json", "kind": "json_profiles", "label": "aion_persistent_intelligence"},
    {"path": "runtime/state/market_personality_profiles.json", "kind": "json_profiles", "label": "market_personality_profiles"},
    {"path": "runtime/state/aion_expectancy_state.json", "kind": "json_profiles", "label": "aion_expectancy_state"},
    {"path": "runtime/memory/seraph_memory.sqlite3", "kind": "sqlite", "label": "seraph_memory"},
    {"path": "runtime/memory/realm_xvii_strategy_evolution.sqlite3", "kind": "sqlite", "label": "realm_xvii_strategy_evolution"},
    {"path": "runtime/memory/realm_xviii_portfolio_brain.sqlite3", "kind": "sqlite", "label": "realm_xviii_portfolio_brain"},
    {"path": "runtime/telemetry/aion_events.jsonl", "kind": "jsonl", "label": "aion_events", "telemetry": True},
)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value):
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
        text = text[:-1] + "+00:00"
    for parser in (
        lambda raw: datetime.fromisoformat(raw),
        lambda raw: datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc),
        lambda raw: datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc),
    ):
        try:
            parsed = parser(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def iso_or_none(dt):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def first_value(record, *keys):
    for key in keys:
        value = record.get(key) if isinstance(record, dict) else None
        if value not in (None, ""):
            return value
    return None


def safe_float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_family(record):
    if not isinstance(record, dict):
        return "UNKNOWN"
    family = first_value(record, "family", "series_ticker", "series", "event_ticker")
    if family:
        return str(family).strip().upper().split("-", 1)[0] or "UNKNOWN"
    ticker = first_value(record, "ticker", "market_ticker", "contract_id")
    if ticker:
        return str(ticker).strip().upper().split("-", 1)[0] or "UNKNOWN"
    return "UNKNOWN"


def normalize_ticker(record):
    ticker = first_value(record, "ticker", "market_ticker", "contract_id")
    return str(ticker).strip().upper() if ticker else None


def normalize_category(record, family=None):
    category = first_value(record, "category", "market_category", "event_category")
    if category:
        return str(category).strip().lower()
    return "sports" if is_sports_record(record, family=family) else "unknown"


def record_timestamp(record):
    for key in (
        "timestamp_utc",
        "close_timestamp_utc",
        "collected_at_utc",
        "settlement_time",
        "settled_time",
        "close_time",
        "expiration_time",
        "latest_expiration_time",
        "open_time",
        "timestamp",
        "ts",
        "created_at",
    ):
        parsed = parse_timestamp(first_value(record, key))
        if parsed is not None:
            return parsed
    return None


def is_sports_record(record, family=None):
    if not isinstance(record, dict):
        record = {}
    family = str(family or normalize_family(record) or "").strip().upper()
    if family.startswith(SPORTS_FAMILY_PREFIXES):
        return True
    category = str(first_value(record, "category", "market_category", "event_category") or "").strip().lower()
    if "sport" in category:
        return True
    text = " " + " ".join(
        str(first_value(record, key) or "")
        for key in ("ticker", "event_ticker", "series_ticker", "title", "subtitle", "market_title", "raw_text")
    ).upper() + " "
    return any(marker in text for marker in SPORTS_TEXT_MARKERS)


def has_liquidity_sample(record):
    if not isinstance(record, dict):
        return False
    if safe_float(first_value(record, "liquidity", "liquidity_dollars", "open_interest", "open_interest_dollars"), None) is not None:
        return True
    if safe_float(first_value(record, "spread"), None) is not None:
        return True
    return any(first_value(record, key) is not None for key in ("yes_bid", "yes_ask", "no_bid", "no_ask", "yes_levels", "no_levels"))


def has_price_observation(record):
    if not isinstance(record, dict):
        return False
    if str(record.get("event_type") or "").strip().lower() in {"trade_opened", "trade_closed", "orderbook_snapshot", "price_history"}:
        return True
    return any(first_value(record, key) is not None for key in ("price", "entry_price", "exit_price", "last_price", "yes_bid", "yes_ask", "no_bid", "no_ask"))


def update_range(summary, dt):
    if dt is None:
        return
    if summary.get("oldest_dt") is None or dt < summary["oldest_dt"]:
        summary["oldest_dt"] = dt
    if summary.get("newest_dt") is None or dt > summary["newest_dt"]:
        summary["newest_dt"] = dt


def new_summary(label=None, path=None, kind=None):
    return {
        "label": label,
        "path": path,
        "kind": kind,
        "records": 0,
        "sports_records": 0,
        "tickers": set(),
        "sports_tickers": set(),
        "families": Counter(),
        "categories": Counter(),
        "sports_families": Counter(),
        "oldest_dt": None,
        "newest_dt": None,
        "sports_oldest_dt": None,
        "sports_newest_dt": None,
        "sqlite_tables": {},
        "errors": [],
    }


def update_family_stats(audit, record, family, ticker, dt):
    stats = audit["sports_family_stats"].setdefault(
        family,
        {
            "records": 0,
            "tickers": set(),
            "oldest_dt": None,
            "newest_dt": None,
            "liquidity_samples": 0,
            "price_observations": 0,
            "spread_sum": 0.0,
            "spread_samples": 0,
            "liquidity_sum": 0.0,
            "liquidity_value_samples": 0,
            "wins": 0,
            "losses": 0,
            "pnl_dollars": 0.0,
            "closed_trades": 0,
        },
    )
    stats["records"] += 1
    if ticker:
        stats["tickers"].add(ticker)
    update_range(stats, dt)
    if has_liquidity_sample(record):
        stats["liquidity_samples"] += 1
    if has_price_observation(record):
        stats["price_observations"] += 1
    spread = safe_float(first_value(record, "spread"), None)
    if spread is not None:
        stats["spread_sum"] += spread
        stats["spread_samples"] += 1
    liquidity = safe_float(first_value(record, "liquidity", "liquidity_dollars"), None)
    if liquidity is not None:
        stats["liquidity_sum"] += liquidity
        stats["liquidity_value_samples"] += 1
    pnl = safe_float(first_value(record, "pnl_dollars", "realized_pnl_delta", "pnl"), None)
    outcome = str(first_value(record, "result", "outcome") or "").strip().lower()
    if pnl is not None or outcome in {"win", "won", "loss", "lost"}:
        stats["closed_trades"] += 1
        if pnl is not None:
            stats["pnl_dollars"] += pnl
        if outcome in {"win", "won"} or (pnl is not None and pnl > 0):
            stats["wins"] += 1
        elif outcome in {"loss", "lost"} or (pnl is not None and pnl < 0):
            stats["losses"] += 1


def update_audit_with_record(audit, source_summary, record):
    if not isinstance(record, dict):
        return
    family = normalize_family(record)
    ticker = normalize_ticker(record)
    category = normalize_category(record, family=family)
    dt = record_timestamp(record)
    is_sports = is_sports_record(record, family=family)

    for summary in (audit["totals"], source_summary):
        summary["records"] += 1
        if ticker:
            summary["tickers"].add(ticker)
        if family:
            summary["families"][family] += 1
        if category:
            summary["categories"][category] += 1
        update_range(summary, dt)
        if is_sports:
            summary["sports_records"] += 1
            if ticker:
                summary["sports_tickers"].add(ticker)
            if family:
                summary["sports_families"][family] += 1
            if dt is not None:
                if summary.get("sports_oldest_dt") is None or dt < summary["sports_oldest_dt"]:
                    summary["sports_oldest_dt"] = dt
                if summary.get("sports_newest_dt") is None or dt > summary["sports_newest_dt"]:
                    summary["sports_newest_dt"] = dt
    if is_sports:
        update_family_stats(audit, record, family or "UNKNOWN", ticker, dt)


def iter_json_profile_records(payload):
    if isinstance(payload, dict):
        for container_key, key_field in (
            ("ticker_profiles", "ticker"),
            ("family_profiles", "family"),
            ("families", "family"),
            ("markets", "ticker"),
            ("positions", "ticker"),
        ):
            container = payload.get(container_key)
            if isinstance(container, dict):
                for key, value in container.items():
                    if isinstance(value, dict):
                        record = dict(value)
                        record.setdefault(key_field, key)
                        yield record
            elif isinstance(container, list):
                for value in container:
                    if isinstance(value, dict):
                        yield value
        if not any(key in payload for key in ("ticker_profiles", "family_profiles", "families", "markets", "positions")):
            yield payload
    elif isinstance(payload, list):
        for value in payload:
            if isinstance(value, dict):
                yield value


def scan_jsonl(path, audit, source_summary):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                source_summary["errors"].append("json_decode_error")
                continue
            update_audit_with_record(audit, source_summary, record)


def scan_csv(path, audit, source_summary):
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            update_audit_with_record(audit, source_summary, row)


def scan_json_profiles(path, audit, source_summary):
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    for record in iter_json_profile_records(payload):
        update_audit_with_record(audit, source_summary, record)


def scan_sqlite(path, audit, source_summary):
    uri = "file:" + str(path.resolve()) + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        for table_row in table_rows:
            table = str(table_row["name"])
            if table.startswith("sqlite_"):
                continue
            count_row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            row_count = int(count_row["count"] if count_row else 0)
            source_summary["sqlite_tables"][table] = row_count
            columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if not row_count:
                continue
            useful_columns = [
                column for column in columns
                if column in {
                    "timestamp", "timestamp_ts", "created_at", "ticker", "category", "family", "series_ticker",
                    "side", "entry_price", "exit_price", "realized_pnl_delta", "outcome", "exit_reason",
                    "pnl_dollars", "result", "notes_json", "memory_tags_json",
                }
            ]
            if not useful_columns:
                source_summary["records"] += row_count
                audit["totals"]["records"] += row_count
                continue
            sql = "SELECT " + ", ".join(useful_columns) + f" FROM {table}"
            for row in conn.execute(sql):
                record = dict(row)
                record["sqlite_table"] = table
                update_audit_with_record(audit, source_summary, record)


def finalize_summary(summary):
    return {
        "label": summary.get("label"),
        "path": summary.get("path"),
        "kind": summary.get("kind"),
        "records": int(summary.get("records", 0)),
        "oldest_timestamp": iso_or_none(summary.get("oldest_dt")),
        "newest_timestamp": iso_or_none(summary.get("newest_dt")),
        "unique_markets_tickers": len(summary.get("tickers", set())),
        "total_categories_families": len(set(summary.get("families", {}).keys()).union(summary.get("categories", {}).keys())),
        "top_categories": dict(summary.get("categories", Counter()).most_common(10)),
        "top_families": dict(summary.get("families", Counter()).most_common(10)),
        "sports_record_count": int(summary.get("sports_records", 0)),
        "sports_unique_markets_tickers": len(summary.get("sports_tickers", set())),
        "sports_oldest_timestamp": iso_or_none(summary.get("sports_oldest_dt")),
        "sports_newest_timestamp": iso_or_none(summary.get("sports_newest_dt")),
        "top_sports_families": dict(summary.get("sports_families", Counter()).most_common(10)),
        "sqlite_tables": dict(summary.get("sqlite_tables", {})),
        "errors": list(summary.get("errors", []))[:10],
    }


def finalize_family_stats(family, stats, now_dt):
    records = int(stats.get("records", 0))
    unique_tickers = len(stats.get("tickers", set()))
    newest_dt = stats.get("newest_dt")
    age_hours = None
    if newest_dt is not None:
        age_hours = max(0.0, (now_dt - newest_dt).total_seconds() / 3600.0)
    enough_records = records >= 20 or unique_tickers >= 5
    recent_activity = bool(age_hours is not None and age_hours <= 168.0)
    active_now = bool(age_hours is not None and age_hours <= 24.0)
    enough_liquidity = int(stats.get("liquidity_samples", 0)) >= 5
    enough_price = int(stats.get("price_observations", 0)) >= 5
    tags = []
    if enough_records and recent_activity and enough_liquidity and enough_price:
        tags.append("SPORTS_DATA_READY")
    if active_now:
        tags.append("SPORTS_DATA_ACTIVE")
    if not recent_activity:
        tags.append("SPORTS_DATA_STALE")
    if not (enough_records and enough_liquidity and enough_price):
        tags.append("SPORTS_DATA_THIN")
    closed = int(stats.get("closed_trades", 0))
    wins = int(stats.get("wins", 0))
    losses = int(stats.get("losses", 0))
    avg_spread = None
    if stats.get("spread_samples"):
        avg_spread = round(float(stats["spread_sum"]) / max(1, int(stats["spread_samples"])), 6)
    avg_liquidity = None
    if stats.get("liquidity_value_samples"):
        avg_liquidity = round(float(stats["liquidity_sum"]) / max(1, int(stats["liquidity_value_samples"])), 6)
    return {
        "family": family,
        "records": records,
        "unique_markets_tickers": unique_tickers,
        "oldest_timestamp": iso_or_none(stats.get("oldest_dt")),
        "newest_timestamp": iso_or_none(newest_dt),
        "age_hours": round(age_hours, 3) if age_hours is not None else None,
        "liquidity_samples": int(stats.get("liquidity_samples", 0)),
        "price_observations": int(stats.get("price_observations", 0)),
        "avg_spread": avg_spread,
        "avg_liquidity": avg_liquidity,
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / closed, 4) if closed else None,
        "pnl_dollars": round(float(stats.get("pnl_dollars", 0.0)), 6),
        "tags": tags,
        "activity_level": "ready" if "SPORTS_DATA_READY" in tags else "active" if "SPORTS_DATA_ACTIVE" in tags else "stale" if "SPORTS_DATA_STALE" in tags else "thin",
    }


def audit_kalshi_data_coverage(repo_root=None, include_telemetry=True):
    root = Path(repo_root or ".").resolve()
    audit = {
        "audited_at_utc": utc_now_iso(),
        "repo_root": str(root),
        "include_telemetry": bool(include_telemetry),
        "totals": new_summary(label="TOTAL", path=None, kind="aggregate"),
        "sources": [],
        "sports_family_stats": {},
    }
    for spec in ACTIVE_SOURCE_SPECS:
        if spec.get("telemetry") and not include_telemetry:
            continue
        path = root / spec["path"]
        source_summary = new_summary(label=spec["label"], path=str(path), kind=spec["kind"])
        if not path.exists():
            source_summary["errors"].append("missing")
            audit["sources"].append(finalize_summary(source_summary))
            continue
        try:
            if spec["kind"] == "jsonl":
                scan_jsonl(path, audit, source_summary)
            elif spec["kind"] == "csv":
                scan_csv(path, audit, source_summary)
            elif spec["kind"] == "json_profiles":
                scan_json_profiles(path, audit, source_summary)
            elif spec["kind"] == "sqlite":
                scan_sqlite(path, audit, source_summary)
        except PermissionError:
            source_summary["errors"].append("permission_denied")
        except Exception as exc:
            source_summary["errors"].append(exc.__class__.__name__)
        audit["sources"].append(finalize_summary(source_summary))

    now_dt = datetime.now(timezone.utc)
    family_coverages = [
        finalize_family_stats(family, stats, now_dt)
        for family, stats in audit["sports_family_stats"].items()
    ]
    family_coverages.sort(key=lambda item: (item["records"], item["unique_markets_tickers"]), reverse=True)
    audit["sports_family_coverage"] = family_coverages
    audit["totals"] = finalize_summary(audit["totals"])
    audit["sports"] = {
        "record_count": audit["totals"]["sports_record_count"],
        "unique_markets_tickers": audit["totals"]["sports_unique_markets_tickers"],
        "oldest_timestamp": audit["totals"]["sports_oldest_timestamp"],
        "newest_timestamp": audit["totals"]["sports_newest_timestamp"],
        "top_families": audit["totals"]["top_sports_families"],
        "ready_families": sum(1 for item in family_coverages if "SPORTS_DATA_READY" in item.get("tags", [])),
        "active_families": sum(1 for item in family_coverages if "SPORTS_DATA_ACTIVE" in item.get("tags", [])),
        "thin_families": sum(1 for item in family_coverages if "SPORTS_DATA_THIN" in item.get("tags", [])),
        "stale_families": sum(1 for item in family_coverages if "SPORTS_DATA_STALE" in item.get("tags", [])),
    }
    audit.pop("sports_family_stats", None)
    return audit


def build_sports_data_signal(audit, family):
    family = str(family or "").strip().upper()
    coverage = {}
    for item in (audit or {}).get("sports_family_coverage", []) or []:
        if str(item.get("family") or "").strip().upper() == family:
            coverage = dict(item)
            break
    if not coverage:
        return {
            "family": family or "UNKNOWN",
            "records": 0,
            "unique_markets_tickers": 0,
            "tags": ["SPORTS_DATA_THIN"],
            "activity_level": "thin",
            "rank_boost": 0.0,
        }
    tags = list(coverage.get("tags") or [])
    rank_boost = 0.0
    if "SPORTS_DATA_READY" in tags:
        rank_boost += 0.45
    if "SPORTS_DATA_ACTIVE" in tags:
        rank_boost += 0.35
    avg_spread = safe_float(coverage.get("avg_spread"), None)
    if avg_spread is not None and 0.0 <= avg_spread <= 0.08:
        rank_boost += 0.20
    avg_liquidity = safe_float(coverage.get("avg_liquidity"), None)
    if avg_liquidity is not None and avg_liquidity > 0:
        rank_boost += min(0.20, avg_liquidity / 500.0)
    win_rate = safe_float(coverage.get("win_rate"), None)
    if win_rate is not None and win_rate > 0.50:
        rank_boost += min(0.35, (win_rate - 0.50) * 1.2)
    pnl = safe_float(coverage.get("pnl_dollars"), 0.0) or 0.0
    if pnl > 0:
        rank_boost += min(0.25, pnl / 25.0)
    coverage["rank_boost"] = round(max(0.0, min(1.25, rank_boost)), 4)
    return coverage

#!/usr/bin/env python3
"""Print current AION/Kalshi stored data coverage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from aion_data_coverage import audit_kalshi_data_coverage  # noqa: E402


def emit(tag: str, payload: dict) -> None:
    print(f"{tag} {json.dumps(payload, sort_keys=True, default=str)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit stored Kalshi market/trade/orderbook/performance data coverage.")
    parser.add_argument("--sports-only", action="store_true", help="Print only sports coverage summaries.")
    parser.add_argument("--no-telemetry", action="store_true", help="Skip runtime/telemetry/aion_events.jsonl for a faster audit.")
    parser.add_argument("--json", action="store_true", help="Print the full audit object as JSON.")
    parser.add_argument("--top", type=int, default=10, help="Number of sports families to print.")
    args = parser.parse_args()

    audit = audit_kalshi_data_coverage(REPO_ROOT, include_telemetry=not args.no_telemetry)
    top_n = max(1, int(args.top or 10))

    if args.json:
        print(json.dumps(audit, sort_keys=True, indent=2, default=str))
        return 0

    totals = audit.get("totals", {})
    sports = audit.get("sports", {})
    if not args.sports_only:
        emit(
            "[AION-DATA-AUDIT]",
            {
                "audited_at_utc": audit.get("audited_at_utc"),
                "include_telemetry": audit.get("include_telemetry"),
                "total_records": totals.get("records"),
                "oldest_timestamp": totals.get("oldest_timestamp"),
                "newest_timestamp": totals.get("newest_timestamp"),
                "unique_markets_tickers": totals.get("unique_markets_tickers"),
                "total_categories_families": totals.get("total_categories_families"),
                "top_families": totals.get("top_families"),
                "sources": [
                    {
                        "label": source.get("label"),
                        "path": source.get("path"),
                        "kind": source.get("kind"),
                        "records": source.get("records"),
                        "sqlite_tables": source.get("sqlite_tables"),
                        "errors": source.get("errors"),
                    }
                    for source in audit.get("sources", [])
                ],
            },
        )

    emit(
        "[AION-SPORTS-DATA]",
        {
            "sports_record_count": sports.get("record_count"),
            "sports_unique_markets_tickers": sports.get("unique_markets_tickers"),
            "sports_oldest_timestamp": sports.get("oldest_timestamp"),
            "sports_newest_timestamp": sports.get("newest_timestamp"),
            "top_sports_families": sports.get("top_families"),
        },
    )

    for family in (audit.get("sports_family_coverage") or [])[:top_n]:
        emit(
            "[AION-SPORTS-FAMILY-COVERAGE]",
            {
                "family": family.get("family"),
                "records": family.get("records"),
                "unique_markets_tickers": family.get("unique_markets_tickers"),
                "oldest_timestamp": family.get("oldest_timestamp"),
                "newest_timestamp": family.get("newest_timestamp"),
                "liquidity_samples": family.get("liquidity_samples"),
                "price_observations": family.get("price_observations"),
                "avg_spread": family.get("avg_spread"),
                "avg_liquidity": family.get("avg_liquidity"),
                "closed_trades": family.get("closed_trades"),
                "win_rate": family.get("win_rate"),
                "pnl_dollars": family.get("pnl_dollars"),
                "tags": family.get("tags"),
                "activity_level": family.get("activity_level"),
            },
        )

    emit(
        "[AION-SPORTS-DATA-READY]",
        {
            "ready_families": sports.get("ready_families"),
            "active_families": sports.get("active_families"),
            "thin_families": sports.get("thin_families"),
            "stale_families": sports.get("stale_families"),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Offline AION adaptive pipeline. No imports from live execution or network clients."""
from __future__ import annotations
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import mean
from .core import TradeMemory, AdaptiveLab, diagnose


def _timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamps must have a timezone")
    return dt.timestamp()


def import_closed_trades(csv_path, journal_path):
    """Read-only CSV source -> validated append-only research journal.

    Only fee-reconciled CLOSED trades; no claims of broker authentication.
    gross_realized_pnl and fees must both be supplied to prevent double deduction.
    """
    journal = TradeMemory(journal_path)
    imported = duplicates = 0
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"trade_id", "closed_at", "status", "strategy", "family", "side",
                    "gross_realized_pnl", "fees"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("CSV missing required columns: " + ", ".join(sorted(required)))
        for row in reader:
            if row["status"].strip().lower() not in ("closed", "settled"):
                raise ValueError("input includes an unclosed trade")
            gross = float(row["gross_realized_pnl"])
            fees = float(row["fees"])
            if not all(math.isfinite(v) for v in (gross, fees)) or fees < 0:
                raise ValueError("invalid gross P&L or fees")
            record = {key: row[key] for key in ("trade_id", "closed_at", "strategy", "family", "side")}
            _timestamp(record["closed_at"])
            record.update(net_pnl=round(gross-fees, 8), gross_realized_pnl=gross, fees=fees,
                          provenance="user_supplied_csv_unverified")
            if journal.append(record):
                imported += 1
            else:
                duplicates += 1
    return {"imported": imported, "duplicates": duplicates, "total": len(journal.read()),
            "provenance": "unverified_csv"}


def chronological_comparison(rows, *, min_samples=30):
    """Compare two strategy labels on overlapping calendar days, not cherry-picked trades.

    Input rows: closed_at, strategy, net_pnl. Non-trading days within the overlap
    are counted as zero; dates before either strategy starts are excluded.
    """
    if not rows:
        return {"eligible_for_review": False, "reason": "no_data"}
    daily = {}
    for row in rows:
        strategy = str(row["strategy"])
        day = datetime.fromtimestamp(_timestamp(row["closed_at"])).date().isoformat()
        key = (strategy, day)
        daily[key] = daily.get(key, 0.0) + float(row["net_pnl"])
    strategies = sorted({key[0] for key in daily})
    if len(strategies) != 2:
        return {"eligible_for_review": False, "reason": "requires_exactly_two_strategies"}
    dates = {s: sorted(day for (name, day) in daily if name == s) for s in strategies}
    start = max(dates[s][0] for s in strategies)
    end = min(dates[s][-1] for s in strategies)
    if start > end:
        return {"eligible_for_review": False, "reason": "no_overlapping_period"}
    from datetime import date, timedelta
    d = date.fromisoformat(start)
    finish = date.fromisoformat(end)
    a, b = [], []
    while d <= finish:
        day = d.isoformat()
        a.append(daily.get((strategies[0], day), 0.0))
        b.append(daily.get((strategies[1], day), 0.0))
        d += timedelta(days=1)
    if len(a) < min_samples:
        return {"eligible_for_review": False, "reason": "insufficient_overlapping_days",
                "overlapping_days": len(a)}
    return {"eligible_for_review": False, "reason": "manual_review_required",
            "strategies": strategies, "overlapping_days": len(a),
            "net_pnl": [round(sum(a), 4), round(sum(b), 4)],
            "mean_daily_net": [round(mean(a), 6), round(mean(b), 6)],
            "note": "Unpaired trade exposure and market conditions can bias comparisons; no auto-promotion."}


def offline_health_report(journal_path, output_path, *, min_samples=20):
    """Self-diagnose and propose human-reviewed actions; never touch live configuration."""
    rows = TradeMemory(journal_path).read()
    segments = diagnose(rows, min_samples=min_samples)
    actions = []
    for item in segments:
        if item["flag"] == "negative_expectancy":
            actions.append({"action": "review_or_suspend_strategy", "family": item["family"],
                            "side": item["side"], "strategy": item["strategy"],
                            "status": "proposed_only"})
    report = {"observations": len(rows), "segments": segments, "proposed_actions": actions,
              "live_changes": False, "data_source": "local_journal_unverified"}
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def create_recovery_plan(lab_root, *, current_config, candidate_config, reason):
    """Write review-only rollback plan; never mutate the bot's config."""
    if not isinstance(current_config, dict) or not isinstance(candidate_config, dict):
        raise ValueError("configs must be dicts")
    lab = AdaptiveLab(lab_root)
    return lab.code_change_proposal(
        "Review candidate and rollback plan", reason,
        ["approved_config_snapshot.json", "candidate_config_snapshot.json"],
        ["test_aion_adaptive", "broker_reconciliation", "paper_canary"])

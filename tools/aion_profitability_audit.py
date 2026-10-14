#!/usr/bin/env python3
"""Offline, read-only realized trade audit. No broker access or order placement.

Input CSV columns: trade_id, closed_at, realized_pnl, fees
Optional: market, side, strategy, source. Each row must be a distinct CLOSED
trade; realized_pnl excludes fees. Monetary values are USD.
"""
import argparse
import csv
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

ZERO = Decimal("0")
REQUIRED = {"trade_id", "closed_at", "realized_pnl", "fees"}


def audit(path, starting_equity=None):
    trades = []
    ids = set()
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError("CSV must include: " + ", ".join(sorted(REQUIRED)))
        for line, row in enumerate(reader, 2):
            trade_id = (row["trade_id"] or "").strip()
            closed_at = (row["closed_at"] or "").strip()
            if not trade_id or not closed_at or trade_id in ids:
                raise ValueError(f"line {line}: missing/duplicate trade_id or missing closed_at")
            ids.add(trade_id)
            try:
                pnl = Decimal(row["realized_pnl"])
                fees = Decimal(row["fees"])
            except (InvalidOperation, TypeError) as exc:
                raise ValueError(f"line {line}: invalid money field") from exc
            if not pnl.is_finite() or not fees.is_finite() or fees < ZERO:
                raise ValueError(f"line {line}: non-finite P&L or negative fee")
            trades.append({"id": trade_id, "at": closed_at, "gross": pnl,
                           "fees": fees, "net": pnl - fees,
                           "strategy": (row.get("strategy") or "unknown").strip() or "unknown"})
    trades.sort(key=lambda t: (t["at"], t["id"]))
    if not trades:
        raise ValueError("No closed trades supplied")
    wins = [t for t in trades if t["net"] > ZERO]
    losses = [t for t in trades if t["net"] < ZERO]
    gross_profit = sum((t["net"] for t in wins), ZERO)
    gross_loss = -sum((t["net"] for t in losses), ZERO)
    net = sum((t["net"] for t in trades), ZERO)
    fee_total = sum((t["fees"] for t in trades), ZERO)
    by_strategy = defaultdict(lambda: {"trades": 0, "net_pnl": ZERO})
    for t in trades:
        x = by_strategy[t["strategy"]]
        x["trades"] += 1
        x["net_pnl"] += t["net"]
    result = {
        "closed_trades": len(trades), "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": float(Decimal(len(wins)) * 100 / len(trades)),
        "gross_realized_pnl_usd": str(sum((t["gross"] for t in trades), ZERO)),
        "fees_usd": str(fee_total), "net_realized_pnl_usd": str(net),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else None,
        "profit_factor_note": "undefined: no losing trades" if not gross_loss else None,
        "by_strategy": {k: {"trades": v["trades"], "net_pnl_usd": str(v["net_pnl"])}
                        for k, v in sorted(by_strategy.items())},
        "warnings": ["Closed trades only: excludes open P&L, deposits, withdrawals, funding, and other adjustments.",
                     "Trade-level peak-to-trough drawdown is not intraday mark-to-market drawdown.",
                     "CSV provenance and exchange/broker authenticity are not verified."]
    }
    if starting_equity is not None:
        initial = Decimal(str(starting_equity))
        if not initial.is_finite() or initial <= ZERO:
            raise ValueError("starting_equity must be positive and finite")
        equity = peak = initial
        max_drawdown = ZERO
        for t in trades:
            equity += t["net"]
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        result.update({"starting_equity_usd": str(initial),
                       "closed_trade_equity_usd": str(equity),
                       "closed_trade_max_drawdown_pct": float(max_drawdown)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("closed_trades_csv")
    parser.add_argument("--starting-equity", type=str)
    args = parser.parse_args()
    print(json.dumps(audit(args.closed_trades_csv, args.starting_equity), indent=2))


if __name__ == "__main__":
    main()

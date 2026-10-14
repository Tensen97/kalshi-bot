# AION revival: evidence-first profitability gate

This addition is deliberately **offline and read-only**. It does not change the
live trading loop, risk limits, strategy selection, or execution endpoints.

## Why

An external open-source forex bot (Mr. Scrooge V6) published 922 closed live
trades as of 2026-10-13, 730 winners, but approximately -$1,238 in summed
closed-trade P&L and a much larger decline in sampled account NAV. Its
winning-trade percentage did not imply a profitable system. Its published
trade totals did not fully reconcile to its equity history. Do not transplant
its wide-stop or averaging/grid entry logic into AION as a proven edge.

## Audit your own Kalshi fills

Prepare a local CSV of **distinct closed positions/trades** with columns:

`trade_id,closed_at,realized_pnl,fees,strategy`

- `realized_pnl`: realized USD before fees (include losses as negative).
- `fees`: total entry + exit USD fees, nonnegative.
- `closed_at`: UTC ISO-8601 timestamp, e.g. `2026-10-01T12:00:00Z`.
- `trade_id`: unique stable identifier; avoid double-counting partial fills.
- `strategy`: optional entry family or strategy label.

Run:

```bash
python3 -m unittest tests.test_aion_profitability_audit
python3 tools/aion_profitability_audit.py /path/to/closed_trades.csv --starting-equity 100
```

**Do not upload account exports or API keys to a public repository.**
Generate the CSV locally from authoritative exchange fills and settlements.
Reconcile separately to cash balance, deposits, withdrawals, outstanding
positions, settlement events, and fee/adjustment ledger. Do not infer that
the report proves a strategy has positive expected value.

## Before modifying AION's live strategy

1. Recover and freeze the intended May 2026 baseline on a separate branch.
2. Audit genuine fills/settlements and reconcile net realized P&L to Kalshi.
3. Compare performance by sports family, YES/NO side, price zone, holding
   duration, entry/exit reason, and maker/taker fee treatment.
4. Use chronological out-of-sample testing; include realistic fill probability,
   spread, fees, latency, and settlement risk. Reject look-ahead leakage.
5. Require a defined positive net edge with uncertainty bounds before any
   supervised live canary. Preserve hard loss limits and a manual kill switch.

No tool or external strategy can guarantee profitability.

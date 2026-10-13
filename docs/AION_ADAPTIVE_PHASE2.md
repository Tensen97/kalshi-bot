# Adaptive intelligence phase 2 — offline integrations

This adds a fee-aware closed-trade importer, research journal diagnostics, overlapping-calendar-day comparison, and review-only rollback proposals. No changes to live trading, Kalshi API calls, or historical market intelligence.

## Run

From the repository root:

```bash
python -m unittest discover -s tests -p 'test_aion_adaptive*.py' -v
PYTHONPATH=. python tools/aion_adaptive_offline.py import-closed closed_trades.csv research/aion_closed_trades.jsonl
PYTHONPATH=. python tools/aion_adaptive_offline.py diagnose research/aion_closed_trades.jsonl research/health_report.json
```

CSV columns: `trade_id,closed_at,status,strategy,family,side,gross_realized_pnl,fees`. Dates must be timezone-aware ISO 8601. Only `closed` or `settled` records are accepted. Gross P&L must exclude fees; fees must be nonnegative. Imported data are labeled **unverified** until matched to broker fills and settlements.

The importer does not access the previous 254k+ historical market records, which are not a substitute for the bot's own executed-trade ledger. It also does not connect to the existing live bot state automatically. Do not pass unverified CSV exports into production controls.

The offline health report flags consistently negative net results after 20 closed observations per sport/side/strategy. These flags are hypotheses, not evidence of a causal trading disadvantage. No live strategy is suspended.

The comparison helper aligns two candidate strategies by overlapping calendar dates and reports daily net P&L; it **never automatically promotes** a candidate. It is not a full order-book replay or a causal evaluation, and zero-volume days, exposure differences and market drift require further analysis.

Recovery plans are human-review proposals, not automatic rollback. Next: map the bot's actual fill/settlement schema to the importer, add walk-forward event replay with realistic fills and uncertainty checks, and implement signed configuration snapshots after reviewing existing deployment behavior.

**Testing:** tests were added but have not been run through the GitHub connector. Merge/deployment requires test verification and review.

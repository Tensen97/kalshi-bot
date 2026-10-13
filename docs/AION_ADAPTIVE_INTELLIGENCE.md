# AION Adaptive Intelligence — five-layer foundation

This is an **offline, opt-in research foundation**, not a live self-modifying trading system. It does not change AION's existing order submission, trading thresholds, position sizing, or deployment. The prior historical market dataset and current bot learning remain intact.

1. **Continuous learning / TradeMemory**: Append reconciled CLOSED live trade observations to an idempotent JSONL research journal. Do not mistake historical market records for AION-executed trades. This initial foundation does not automatically import broker records.
2. **Strategy experimentation / AdaptiveLab.propose + evaluate**: Record proposed strategy parameters and compare out-of-sample NET P&L. No automated market simulation or historical replay integration yet.
3. **Model updates / register_model**: Register model versions and validation metrics as inactive artifacts. Retraining, probability calibration, walk-forward splits, and champion/challenger selection remain future integration work.
4. **Self-diagnosis / diagnose**: Segment closed-trade results by sport/side/strategy and flag negative average results once minimum sample sizes are reached. Flags are research signals, not automatic live-trading actions.
5. **AI-assisted code evolution / code_change_proposal**: Persist human-review-required improvement proposals; does not invoke external AI or edit repository code automatically.

## Test locally

`python -m unittest discover -s tests -p 'test_aion_adaptive.py' -v`

## Example

```python
from aion_adaptive import TradeMemory, AdaptiveLab, diagnose
memory = TradeMemory('research/aion_closed_trades.jsonl')
# Feed only verified, closed and fee-adjusted trade records.
print(diagnose(memory.read()))
lab = AdaptiveLab('research/adaptive_experiments')
candidate = lab.propose('maker_threshold_v2', {'threshold': 0.02}, 'Test fee-aware entry threshold')
# Supply independent chronological holdout net P&L series, NOT training results.
print(lab.evaluate(candidate, baseline=[0.0]*40, challenger=[0.01]*40))
```

## Safety and next integration steps

- Preserve production engine and its existing historical knowledge; opt in to offline experiments explicitly.
- Build a read-only, broker-reconciled importer with exact fee accounting and stable trade IDs; avoid CSV-to-live shortcuts.
- Add timestamped chronological walk-forward replay, realistic bid/ask and partial-fill assumptions, uncertainty and drawdown gates. The simple average comparison here is **not** proof of statistical significance or profitability.
- Only promote validated models within predefined exposure limits after approval; code changes require reviewed PRs, tests and rollback plans.
- Do not auto-rewrite execution/risk code or let an LLM alter real-money order behavior without human review.
- Run tests before merging. No tests have been executed through this GitHub connector.

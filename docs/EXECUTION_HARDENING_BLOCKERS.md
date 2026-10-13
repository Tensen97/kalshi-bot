# Execution hardening: required before live reactivation

Status: **NOT LIVE-READY**. Changes on this branch are not a complete remediation.

## Completed
- Decimal-based cent-price conversion; reject non-cent prices instead of
  silently moving limit prices. This is intentionally conservative and may
  reject valid sub-cent ticks if Kalshi enables them for a market. Market-
  specific tick metadata must be incorporated before production deployment.
- Regression tests for cent-price conversion (not executed in this session).
- Offline realized-profitability audit and tests.

## Open blockers
1. **Exchange API compatibility:** Confirm endpoint and order payload against
   the current official Kalshi API documentation. Do not change endpoints based
   on an unverified migration assumption. Test request signing and payloads in
   a non-trading environment.
2. **Maker orders:** Trace confirm_entry -> canonical submit. Preserve
   post_only / time_in_force semantics and test maker, taker, cancel, and
   partial-fill transitions. Confirm the exchange permits each combination.
3. **Fill accounting:** An accepted HTTP request is not an execution.
   Reconcile fills, remaining quantity, fees, cancel states, and settlement
   against authoritative exchange data, including after restart.
4. **Portfolio risk:** Fail closed when broker reconciliation fails. Include
   unsettled exposure, pending orders, correlated markets, open P&L, and
   daily mark-to-market loss limits; keep emergency liquidation separate
   from entry lockout.
5. **Exits:** Existing max-hold exits only when P&L is nonnegative. Specify
   whether timeout means forced exit, then test illiquid books, rejected
   exits, partial fills, stale quotes, and markets near settlement.
6. **Net P&L:** Subtract all fees and actual execution slippage before
   learning from outcomes. Reconcile daily P&L to the exchange ledger.
7. **Edge validation:** Calibrate probability estimates on held-out,
   time-separated outcomes; reject unverifiable probability inputs. Model
   maker fill probability, selection bias, spreads, and latency.

Do not merge this branch or start live trading until these blockers are
resolved, tests are executed, and the original May baseline is compared
against the new implementation. No profitability guarantee is possible.

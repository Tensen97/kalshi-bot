# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

This is **AION**, a production live trading bot for the [Kalshi](https://kalshi.com) prediction market exchange. It trades sports markets in real money. The canonical deployment lives at `/opt/kalshi-bot` on a VPS and runs as a systemd service. This repo syncs with that deployment.

**Primary objective: profitable live trade execution.** If the bot is not trading, treat that as the highest-priority failure. Architecture, telemetry, and abstractions are secondary to real order submission.

## Setup

```bash
./install_deps.sh          # creates .venv and installs requirements.txt
cp .env.example .env       # then fill in real API credentials
```

## Validation Commands

Run these before committing any changes to execution logic:

```bash
# Syntax check all modules
python3 -m py_compile kalshi_web.py kalshi_execution.py kalshi_exits.py kalshi_orderbook.py kalshi_risk.py

# Unit tests
python3 -m pytest tests -q
# or
python3 -m unittest discover -s tests

# Run a single test
python3 -m pytest tests/test_live_engine.py::LiveEngineTests::test_confirm_entry_rejects_synthetic_book -v

# Offline stability smoke checks (no network, no credentials required)
python3 tools/aion_stability_smoke_test.py
```

## Running the Bot

```bash
./run_bot.sh               # loads .env, activates .venv, runs kalshi_web.py --live --arm-live
```

Live execution requires all four env flags to be active simultaneously: `KALSHI_ALLOW_LIVE=true`, `LIVE_ENV_CONFIRMED=true`, `DRY_RUN_LIVE_ORDERS=false`, plus `--live --arm-live` CLI flags. Any one missing results in a dry run that logs `ORDER-DRY-RUN` instead of submitting.

## Service Management (on VPS)

```bash
sudo systemctl restart kalshi-bot
systemctl status kalshi-bot --no-pager
sudo journalctl -u kalshi-bot -n 200 --no-pager
./healthcheck.sh
```

## Architecture

### Module Roles

| File | Role |
|------|------|
| `kalshi_web.py` | Main loop, `KalshiSniperBot` class, `GreenEntryScorer`, `EntryConfirm`, `ExitConfirm` — the orchestration hub |
| `kalshi_orderbook.py` | Market classification, orderbook parsing, `Quote`/`OrderbookSnapshot`/`BookTop` dataclasses, sports/tennis/KXMLBKS detection |
| `kalshi_execution.py` | Single canonical order submit path — all orders go through `submit_canonical_order()` |
| `kalshi_risk.py` | Catastrophic entry/exit guards (`evaluate_entry_risk`, `evaluate_exit_risk`) |
| `kalshi_exits.py` | `PositionExecutor`, MFE/MAE tracking, `evaluate_exit`, `build_exit_payload`, `outcome_record` |

### Live Pipeline

```
SCAN → QUALIFY → EXECUTE → MANAGE → LEARN
```

Every candidate must either:
- Progress through `[AION-QUALIFIED]` → `[AION-DISPATCH]` → `[LIVE-EXECUTION]` → `[ORDER-SUBMIT]` → `[POSITION-OPEN]`
- Or terminate with exactly one `[AION-FINAL-REJECT]` log containing: ticker, family, side, selected_price, spread, liquidity, volume, rejection reason, responsible subsystem

**No silent suppression. No hidden returns. No invisible blockers.**

### Key Data Structures

**`TradeTarget`** (`kalshi_web.py`) — frozen dataclass representing a scored entry opportunity. Immutable after creation. Carries all signal metadata (score, confidence, price_zone, spread, liquidity, EV metrics).

**`Quote`** (`kalshi_orderbook.py`) — frozen dataclass with the full orderbook snapshot for a market (yes/no bid/ask prices and sizes, spread, liquidity, volume, `raw_market` dict). `orderbook_real=True` means data came from the real orderbook endpoint (not synthetic/derived). Synthetic books are always rejected at `confirm_entry`.

**`PositionExecutor`** (`kalshi_exits.py`) — tracks MFE/MAE, trailing stop, hold time for an open position.

### Entry Decision Flow (`confirm_entry`)

1. **Hard blocks**: tennis markets, KXMLBKS markets, synthetic/fake orderbooks, stale signals (>45s), invalid prices, side mismatches
2. **EV scoring** via `GreenEntryScorer.score_side()`: computes fair probability from market mid + explicit model probability + momentum adjustment, then calculates taker EV and maker EV after all costs (spread, exit costs, fees, slippage, model error, adverse selection buffers)
3. **Entry mode selection**: `taker` if taker EV ≥ strong threshold; `maker` if maker EV > threshold and fill probability ≥ minimum; otherwise reject with `ev_after_cost_not_positive`
4. **Risk guards** (`kalshi_risk.evaluate_entry_risk`): duplicate position guard, opposite-side same-ticker guard, max open positions, daily loss cap, rate limiting

Maker orders are submitted `post_only=True, time_in_force=good_till_canceled` and tracked in `state["pending_orders"]` until reconciled as filled or cancelled (max age: 30s by default).

### Exit Decision Flow (`evaluate_exit`)

`PositionExecutor` checks each cycle for: take profit, trailing lock (activates after MFE ≥ trailing_start, exits if pullback ≥ trailing_distance), stop loss, max hold timeout. All exits are `reduce_only=True, action=sell, time_in_force=IOC`.

### State Persistence

`KalshiSniperBot` persists state to a JSON file at `AION_LIVE_ENGINE_STATE` (default: `runtime/state/aion_live_engine.json`). State schema:
```json
{
  "daily": { "date", "realized_pnl", "live_trade_count", "submitted_order_count" },
  "positions": { "<ticker>": { position dict } },
  "pending_orders": { "<order_id>": { pending maker order } },
  "learning": {
    "family_side_zone": { "<family:side:zone>": { bucket stats } },
    "exit_outcomes": [ ...last N outcome records ]
  }
}
```

### Learning System

Trades are bucketed by `family:side:price_zone`. Each bucket accumulates win/loss counts, PnL, MFE, MAE, immediate PnL (cashout right after entry), and MFE-before-MAE statistics. `GreenEntryScorer` reads these buckets to adjust EV estimates and reject historically-losing setups (red entry rate > threshold). Requires ≥3 samples before influencing decisions.

### Market Classification

`kalshi_orderbook.py` classifies markets by ticker prefix. Sports prefixes include `KXNBA`, `KXWNBA`, `KXMLB`, `KXNFL`, `KXNHL`, etc. Tennis (`KXATP`, `KXWTA`, `KXITF`, `KXTENNIS`) and `KXMLBKS` are classified separately and disabled for new entries. Combo/cross-family markets (tokens: `MULTIGAME`, `CROSSCATEGORY`, `COMBO`, `PARLAY`, `SAMEGAME`) get tighter EV thresholds and cashout quality requirements.

## Preserved Guards (Never Remove)

- Sports-only new entries; tennis and KXMLBKS disabled
- `TradeTarget` immutability (frozen dataclass)
- `confirm_entry` freshness TTL
- Synthetic/fake book rejection
- Canonical submit only (all orders via `submit_canonical_order`)
- `PositionExecutor` for all exits
- `confirm_exit` reduce-only enforcement
- Duplicate live position guard
- Exact same-ticker opposite-side guard
- Hard daily loss cap
- Sane max sizing

## Environment Variables

All tunable parameters are in `.env`. Key ones:

| Variable | Default | Purpose |
|----------|---------|---------|
| `KALSHI_ALLOW_LIVE` | `false` | Master live-fire switch |
| `DRY_RUN_LIVE_ORDERS` | `true` | Forces dry run regardless of other flags |
| `AION_MAX_DAILY_LOSS` | `50` | Hard daily loss cap in dollars |
| `AION_MAX_OPEN_POSITIONS` | `8` | Max concurrent open positions |
| `AION_MAX_LIVE_ORDER_SIZE` | `3` | Max contracts per order |
| `AION_MAX_SIGNAL_AGE_SECONDS` | `45` | Staleness cutoff for entries |
| `AION_LOOP_SLEEP_SECONDS` | `5` | Main loop cadence |
| `LIVE_ORDER_RATE_LIMIT` | `5` | Max orders per rate window |

## Code Modification Rules

1. Validate syntax: `python3 -m py_compile <file>`
2. Run smoke checks: `python3 tools/aion_stability_smoke_test.py`
3. Run unit tests: `python3 -m pytest tests -q`
4. Verify live logs after deploying to VPS
5. Confirm execution flow still works end-to-end
6. Confirm no silent candidate death was introduced

Prefer fixing the actual blocker over adding another filter layer. The bot should be actively discovering where edge exists, not being filtered into a dead candidate queue.

# Kalshi Sniper Refactor Migration Notes

## What Changed

- `kalshi_web.py` remains the CLI/service entry point.
- New modular services live under `kalshi_sniper/`:
  - `core.py`: HTTP session, sliding-window rate limit, exponential backoff, GET cache, circuit breaker.
  - `discovery.py`: cursor-aware targeted and broad market discovery with short TTL caching.
  - `live.py`: WebSocket quote-cache hydration and safe Telegram command polling.
  - `risk.py`: configurable entry/exit guard thresholds and profiles.
  - `utils.py`: env parsing, timestamps, and TTL cache utilities.
- Existing dry-run, live arming, canary, auth, and execution safety checks are preserved in `kalshi_web.py`.

## Runtime Behavior

- Market discovery now goes through `MarketDiscoveryService`:
  - targeted series calls are deduped and cursor-aware;
  - broad scans stop when cursor, market-count, or in-window targets are satisfied;
  - short TTL caches avoid repeated `get_markets()` calls inside the same live cycle.
- REST calls made through the service use:
  - `KALSHI_HTTP_MAX_CALLS_PER_MINUTE` default `120`;
  - `KALSHI_HTTP_RETRY_ATTEMPTS` default `3`;
  - `KALSHI_HTTP_BACKOFF_BASE_SECONDS` default `0.25`;
  - `KALSHI_HTTP_CIRCUIT_FAILURE_THRESHOLD` default `5`.
- WebSocket support remains optional. When `REALM_XX_WS_ENABLE=true` and `REALM_XX_WS_URL` is set, fresh cache snapshots hydrate quote fields before polling fallbacks are used.
- Entry/exit guard thresholds are centralized in `EntryExitGuardService`. Set `KALSHI_ENTRY_GUARD_PROFILE=safe|balanced|opportunistic` to tune strictness without editing code.

## Useful New Env Knobs

- `KALSHI_MARKET_CACHE_TTL_SECONDS=4`
- `KALSHI_TARGETED_DISCOVERY_CACHE_TTL_SECONDS=12`
- `KALSHI_BROAD_DISCOVERY_CACHE_TTL_SECONDS=8`
- `KALSHI_ORDERBOOK_CACHE_TTL_SECONDS=1`
- `KALSHI_MARKET_DETAIL_CACHE_TTL_SECONDS=2`
- `KALSHI_POSITIONS_CACHE_TTL_SECONDS=2`
- `KALSHI_WS_QUOTE_STALE_SECONDS=15`
- `TELEGRAM_COMMANDS_ENABLE=false`
- `TELEGRAM_COMMAND_POLL_INTERVAL_SECONDS=10`

## Telegram Commands

When `TELEGRAM_COMMANDS_ENABLE=true`, the bot accepts only safe commands from `TELEGRAM_CHAT_ID`:

- `/status`
- `/risk`
- `/ws`
- `/pause`
- `/resume_dryrun`
- `/help`

Telegram cannot arm live trading, disable dry-run safeguards, or bypass canary/live authorization.

## Rollout

1. Run syntax checks: `python3 -m py_compile kalshi_web.py kalshi_sniper/*.py`.
2. Start in dry-run or preflight mode first.
3. Watch for `[MODULAR-SERVICES] enabled=yes` on startup.
4. Watch `[DISCOVERY-*]`, `[HTTP-*]`, and `[WS-HYDRATE-*]` telemetry for cache/retry behavior.
5. Enable live trading only through the existing `--live --arm-live` and environment/control-state gates.

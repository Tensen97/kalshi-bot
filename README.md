# Kalshi Bot / AION

This repository contains the live AION/Kalshi bot orchestrator and the supporting modular intelligence layers.

`kalshi_web.py` remains the entrypoint and orchestrator. New modules are added conservatively around it so live behavior stays protected.

## Safety Philosophy

- `AION_LIVE_ARB_ENABLED` stays off by default in code.
- Realm V.V is canonical; Phase 5.5 aliases remain compatibility-only.
- Spread, liquidity, cooldown, duplicate, arm-live, unsafe-price, and execution protections remain unchanged.
- New intelligence layers are telemetry-first, bounded, and reversible.
- Sports calibration only informs ranking and diagnostics; it does not create a direct execution path.

## Repo Structure

- `kalshi_web.py` - live orchestrator / runtime entrypoint
- `sports/` - sports calibration, harvest, and telemetry helpers
- `memory/` - persistent intelligence and rolling watchlists
- `runtime/` - runtime path helpers and runtime layout utilities
- `risk/` - bounded risk helpers
- `strategy/` - formatting and telemetry presentation helpers
- `telemetry/` - live state registry and snapshot export
- `execution/` - execution guardrail namespace
- `docs/` - architecture and migration notes

## Runtime Layout

Runtime state is centralized under:

- `runtime/memory/`
- `runtime/cache/`
- `runtime/state/`
- `runtime/tmp/`

These paths are for live artifacts only. They are not where source code lives.

## Service Workflow

Restart the live service:

```bash
sudo systemctl restart kalshi-bot
systemctl status kalshi-bot --no-pager
```

## Run Commands

Run the orchestrator directly:

```bash
python3 kalshi_web.py
```

Run a compile check:

```bash
python3 -m py_compile kalshi_web.py
```

Run local validation:

```bash
python3 -m py_compile /opt/kalshi-bot/kalshi_web.py
python3 -m pytest tests -q || true
python3 -m unittest discover -s tests -q
```

## Log Commands

Tail recent service logs:

```bash
sudo journalctl -u kalshi-bot -n 200 --no-pager
```

Search telemetry tags:

```bash
sudo journalctl -u kalshi-bot -n 200 --no-pager | grep -E "AION-|LIVE-SPORTS-|LIVE-CANARY-|LIVE-REACTIVATION-|LIVE-ENTRY-TIMING-FUSION"
```

## Realm V.V

Realm V.V is the final entry timing and safety gate before any live execution consideration.

It is still responsible for:

- validating entry readiness
- preserving cooldown and duplicate protections
- preserving spread and liquidity checks
- blocking unsafe prices
- preventing execution when the market is not structurally ready

No persistent intelligence layer in this repo is allowed to override Realm V.V.

## Sports Telemetry

Sports markets have separate microstructure behavior from BTC-style markets.

The current sports telemetry stack focuses on:

- spread calibration
- quote stability
- edge after cost
- near-harvest tracking
- harvest watchlists
- CLV-style memory
- harvest rerank signals

This is telemetry and ranking support only. It does not create a live execution path.

## Persistent Intelligence

The new memory foundation stores rolling ticker and family behavior so AION can remember:

- repeated near-pass behavior
- improving or decaying edge
- stability history
- quote behavior
- directional consistency
- volatility tendency
- outcome placeholders

It survives restarts when the runtime state file is available and falls back to legacy memory files when present.

## Expectancy Activation

The expectancy activation layer is intentionally bounded.

Relevant environment flags:

- `AION_CONTROLLED_LOOSENING_ENABLED=1`
- `AION_PHASE55_FASTPATH_ENABLED=1`
- `AION_MICRO_PULLBACK_RELAX_ENABLED=1`
- `AION_COOLDOWN_SOFT_BYPASS_ENABLED=1`
- `AION_SHADOW_TRADE_TRACKING_ENABLED=1`
- `AION_EXPECTANCY_SCORE_ENABLED=1`
- `AION_MAX_RELAXATION_TRADES_PER_DAY=15`

These settings only adjust telemetry and tiny ranking decisions after existing validation survives. They do not enable live arbitrage or remove hard safety gates.

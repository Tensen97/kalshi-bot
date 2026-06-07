# Kalshi Bot / AION

This repo is the production AION live trading engine for `/opt/kalshi-bot`.

The service entrypoint is:

```bash
./run_bot.sh
```

`run_bot.sh` loads `.env`, activates `.venv`, and runs:

```bash
python3 kalshi_web.py --live --arm-live
```

## Active Engine

- `kalshi_web.py` - sports-only live loop and entry/exit orchestration
- `kalshi_orderbook.py` - market classification and real orderbook parsing
- `kalshi_execution.py` - single canonical order submit path
- `kalshi_risk.py` - catastrophic live risk guards
- `kalshi_exits.py` - PositionExecutor, MFE/MAE, reduce-only exits, outcome learning

The live pipeline is:

```text
SCAN -> QUALIFY -> EXECUTE -> MANAGE -> LEARN
```

## Preserved Guards

- sports-only new entries
- tennis shutdown
- KXMLBKS shutdown
- YES/NO side support
- immutable `TradeTarget`
- `confirm_entry`
- hard freshness TTL enforcement in `confirm_entry`
- canonical submit only
- `PositionExecutor`
- `confirm_exit`
- minimal MFE/MAE and exit outcome learning
- family/side/price-zone learning
- capital routing and confidence sizing
- duplicate live position guard
- exact same-ticker opposite-side guard
- hard daily loss cap
- sane max sizing
- valid API/orderbook/payload checks
- fake/synthetic book rejection
- crash prevention
- reduce-only exits

## Validation

```bash
python3 -m py_compile kalshi_web.py kalshi_execution.py kalshi_exits.py kalshi_orderbook.py kalshi_risk.py
python3 -m unittest discover -s tests
python3 -m pytest tests -q
python3 tools/aion_stability_smoke_test.py
git diff --check
sqlite3 --version
python3 -c "import sqlite3; print(sqlite3.sqlite_version)"
```

## Service

```bash
sudo systemctl restart kalshi-bot
systemctl status kalshi-bot --no-pager
sudo journalctl -u kalshi-bot -n 200 --no-pager
```

Expected live markers:

```text
[AION-LIVE-DISCOVERY]
[AION-QUALIFIED]
[AION-DISPATCH]
[LIVE-EXECUTION]
[ORDER-SUBMIT]
[ORDER-SUBMITTED]
[POSITION-OPEN]
[AION-FINAL-REJECT]
```

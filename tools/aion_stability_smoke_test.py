#!/usr/bin/env python3
"""Offline stability smoke checks for the reduced AION live engine."""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kalshi_execution
import kalshi_exits
from kalshi_orderbook import Quote
from kalshi_web import KalshiSniperBot, TradeTarget


def make_quote(
    ticker: str = "KXMLBMONEYLINE-TEST",
    family: str = "KXMLBMONEYLINE",
    *,
    fair_yes: float = 0.49,
    synthetic: bool = False,
) -> Quote:
    return Quote(
        ticker=ticker,
        family=family,
        title="smoke sports market",
        yes_bid=0.44,
        yes_ask=0.46,
        no_bid=0.54,
        no_ask=0.56,
        yes_bid_size=20,
        yes_ask_size=30,
        no_bid_size=30,
        no_ask_size=20,
        spread=0.02,
        liquidity=100.0,
        volume=1000.0,
        open_interest=100.0,
        orderbook_snapshot_id="smoke",
        orderbook_source="orderbook_endpoint:smoke",
        orderbook_real=not synthetic,
        synthetic=synthetic,
        raw_market={"ticker": ticker, "series_ticker": family, "aion_fair_probability_yes": fair_yes},
    )


def make_target(ticker: str = "KXMLBMONEYLINE-TEST", family: str = "KXMLBMONEYLINE", side: str = "yes", created_ts: float | None = None) -> TradeTarget:
    created_ts = time.time() if created_ts is None else created_ts
    return TradeTarget(
        target_id=f"{ticker}:{side}:smoke",
        ticker=ticker,
        family=family,
        side=side,
        selected_price=0.46 if side == "yes" else 0.56,
        count=1,
        confidence=0.5,
        ranking_score=50.0,
        spread=0.02,
        liquidity=100.0,
        volume=1000.0,
        orderbook_snapshot_id="smoke",
        price_zone="mid",
        source="smoke",
        created_ts=created_ts,
        target_created_ts=created_ts,
        trace_id=f"{ticker}:{side}:smoke",
    )


def main() -> int:
    bot = KalshiSniperBot()
    bot.live_positions = []
    bot.live_order_timestamps = []
    bot.state["positions"] = {}
    bot.state["pending_orders"] = {}
    bot.state["learning"] = {"family_side_zone": {}, "exit_outcomes": []}
    bot.state["daily"] = {
        "date": time.strftime("%Y-%m-%d"),
        "realized_pnl": 0.0,
        "live_trade_count": 0,
        "submitted_order_count": 0,
    }
    bot.reconcile_live_positions = lambda **kwargs: {"ok": True, "positions": []}
    checks = {}
    allowed = bot.confirm_entry(make_target(), make_quote())
    checks["real_sports_entry_allowed"] = bool(allowed.get("allowed"))
    checks["immediate_red_allowed_when_ev_positive"] = (
        bool(allowed.get("allowed"))
        and float(((allowed.get("payload") or {}).get("immediate_pnl_per_contract") or 0.0)) < 0.0
        and float(((allowed.get("payload") or {}).get("ev_after_all_costs") or 0.0)) > 0.0
    )
    checks["negative_ev_blocked"] = bot.confirm_entry(make_target(), make_quote(fair_yes=0.445)).get("reason") == "ev_after_cost_not_positive"
    missing_cashout_quote = dataclasses.replace(make_quote(), yes_bid=None, yes_bid_size=0)
    checks["missing_first_cashout_blocked"] = bot.confirm_entry(make_target(), missing_cashout_quote).get("reason") == "missing_first_cashout"
    checks["tennis_blocked"] = (
        bot.confirm_entry(
            make_target("KXATPCHALLENGERMATCH-TEST", "KXATPCHALLENGERMATCH"),
            make_quote("KXATPCHALLENGERMATCH-TEST", "KXATPCHALLENGERMATCH"),
        ).get("reason")
        == "tennis_live_trading_disabled"
    )
    checks["kxmlbks_blocked"] = (
        bot.confirm_entry(make_target("KXMLBKS-TEST", "KXMLBKS"), make_quote("KXMLBKS-TEST", "KXMLBKS")).get("reason")
        == "kxmlbks_live_trading_disabled"
    )
    combo_target = make_target("KXMVECROSSCATEGORY-TEST", "KXMVECROSSCATEGORY")
    combo_quote = dataclasses.replace(
        make_quote("KXMVECROSSCATEGORY-TEST", "KXMVECROSSCATEGORY"),
        yes_bid=0.013,
        yes_ask=0.289,
        no_bid=0.711,
        no_ask=0.987,
        yes_bid_size=8800,
        yes_ask_size=440,
        no_bid_size=440,
        no_ask_size=8800,
        spread=0.276,
    )
    checks["combo_near_zero_cashout_blocked"] = bot.confirm_entry(combo_target, combo_quote).get("reason") == "weak_cashout_quality"
    checks["synthetic_book_blocked"] = (
        bot.confirm_entry(make_target(), make_quote(synthetic=True)).get("reason") == "fake_or_synthetic_book_rejected"
    )
    stale_target = make_target(created_ts=time.time() - bot.max_signal_age_seconds - 5)
    checks["stale_entry_blocked"] = bot.confirm_entry(stale_target, make_quote()).get("reason") == "stale_signal"
    submit_calls = []
    original_submit = kalshi_execution.submit_canonical_order
    try:
        kalshi_execution.submit_canonical_order = lambda *args, **kwargs: submit_calls.append((args, kwargs)) or {"submitted": True}
        stale_submit = bot.submit_target(stale_target, make_quote())
    finally:
        kalshi_execution.submit_canonical_order = original_submit
    checks["stale_entry_not_submitted"] = stale_submit.get("reason") == "stale_signal" and not submit_calls
    exit_position = {
        "ticker": "KXMLBMONEYLINE-TEST",
        "family": "KXMLBMONEYLINE",
        "side": "yes",
        "count": 1,
        "entry_price": 0.46,
        "entry_ts": time.time() - 10_000,
        "target_id": "stale-but-exit-ok",
    }
    exit_decision = {
        "ticker": "KXMLBMONEYLINE-TEST",
        "family": "KXMLBMONEYLINE",
        "side": "yes",
        "count": 1,
        "exit_price": 0.50,
        "reason": "smoke_reduce_only_exit",
    }
    exit_payload = kalshi_exits.build_exit_payload(exit_position, exit_decision)
    checks["reduce_only_exit_not_freshness_blocked"] = bool(bot.confirm_exit(exit_position, exit_decision, exit_payload).get("allowed"))
    mismatch_payload = kalshi_execution.canonical_order_payload(ticker="KXMLBMONEYLINE-TEST", side="no", action="buy", count=1, price=0.56)
    checks["side_mismatch_blocked"] = bot.confirm_entry(make_target(side="yes"), make_quote(), payload=mismatch_payload).get("reason") == "side_payload_mismatch"
    yes_payload = kalshi_execution.canonical_order_payload(ticker="KXMLBMONEYLINE-TEST", side="yes", action="buy", count=1, price=0.46)
    no_payload = kalshi_execution.canonical_order_payload(ticker="KXMLBMONEYLINE-TEST", side="no", action="sell", count=1, price=0.54, reduce_only=True)
    checks["yes_payload_valid"] = bool(kalshi_execution.validate_canonical_order_payload(yes_payload).get("ok"))
    checks["reduce_only_no_payload_valid"] = bool(kalshi_execution.validate_canonical_order_payload(no_payload).get("ok"))
    checks["trade_target_immutable"] = dataclasses.is_dataclass(make_target()) and getattr(TradeTarget, "__dataclass_params__").frozen
    print(json.dumps({"ok": all(checks.values()), "checks": checks}, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

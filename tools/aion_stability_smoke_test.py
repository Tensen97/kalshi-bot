#!/usr/bin/env python3
"""Offline stability smoke checks for the reduced AION live engine."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kalshi_execution
from kalshi_orderbook import Quote
from kalshi_web import KalshiSniperBot, TradeTarget


def make_quote(ticker: str = "KXMLBMONEYLINE-TEST", family: str = "KXMLBMONEYLINE", *, synthetic: bool = False) -> Quote:
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
        raw_market={"ticker": ticker, "series_ticker": family},
    )


def make_target(ticker: str = "KXMLBMONEYLINE-TEST", family: str = "KXMLBMONEYLINE", side: str = "yes") -> TradeTarget:
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
        created_ts=1.0,
    )


def main() -> int:
    bot = KalshiSniperBot()
    bot.reconcile_live_positions = lambda **kwargs: {"ok": True, "positions": []}
    checks = {}
    checks["real_sports_entry_allowed"] = bool(bot.confirm_entry(make_target(), make_quote()).get("allowed"))
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
    checks["synthetic_book_blocked"] = (
        bot.confirm_entry(make_target(), make_quote(synthetic=True)).get("reason") == "fake_or_synthetic_book_rejected"
    )
    yes_payload = kalshi_execution.canonical_order_payload(ticker="KXMLBMONEYLINE-TEST", side="yes", action="buy", count=1, price=0.46)
    no_payload = kalshi_execution.canonical_order_payload(ticker="KXMLBMONEYLINE-TEST", side="no", action="sell", count=1, price=0.54, reduce_only=True)
    checks["yes_payload_valid"] = bool(kalshi_execution.validate_canonical_order_payload(yes_payload).get("ok"))
    checks["reduce_only_no_payload_valid"] = bool(kalshi_execution.validate_canonical_order_payload(no_payload).get("ok"))
    checks["trade_target_immutable"] = dataclasses.is_dataclass(make_target()) and getattr(TradeTarget, "__dataclass_params__").frozen
    print(json.dumps({"ok": all(checks.values()), "checks": checks}, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

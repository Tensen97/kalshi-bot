import dataclasses
import time
import unittest
from pathlib import Path

import kalshi_execution
import kalshi_exits
from kalshi_orderbook import Quote, build_quote, is_kxmlbks_market, is_sports_market, is_tennis_market
from kalshi_web import KalshiSniperBot, TradeTarget


def quote(
    ticker="KXMLBMONEYLINE-26JUN07-TEAM",
    family="KXMLBMONEYLINE",
    *,
    yes_bid=0.44,
    yes_ask=0.46,
    no_bid=0.54,
    no_ask=0.56,
    yes_bid_size=20,
    yes_ask_size=30,
    no_bid_size=30,
    no_ask_size=20,
    fair_yes=0.49,
    synthetic=False,
):
    return Quote(
        ticker=ticker,
        family=family,
        title="MLB test market",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        yes_bid_size=yes_bid_size,
        yes_ask_size=yes_ask_size,
        no_bid_size=no_bid_size,
        no_ask_size=no_ask_size,
        spread=0.0 if yes_bid is not None and yes_ask is not None and yes_bid >= yes_ask else 0.02,
        liquidity=100.0,
        volume=1000.0,
        open_interest=100.0,
        orderbook_snapshot_id="snapshot",
        orderbook_source="orderbook_endpoint:test",
        orderbook_real=not synthetic,
        synthetic=synthetic,
        raw_market={"ticker": ticker, "series_ticker": family, "aion_fair_probability_yes": fair_yes},
    )


def target(ticker="KXMLBMONEYLINE-26JUN07-TEAM", family="KXMLBMONEYLINE", side="yes", created_ts=None):
    created_ts = time.time() if created_ts is None else created_ts
    return TradeTarget(
        target_id="target-1",
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
        orderbook_snapshot_id="snapshot",
        price_zone="mid",
        source="test",
        created_ts=created_ts,
        target_created_ts=created_ts,
        trace_id="trace-1",
    )


def fresh_bot():
    bot = KalshiSniperBot()
    bot.state_path = Path("/tmp") / f"aion_live_engine_test_{time.time_ns()}.json"
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
    return bot


class LiveEngineTests(unittest.TestCase):
    def test_trade_target_is_immutable(self):
        item = target()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            item.count = 2

    def test_sports_tennis_and_kxmlbks_classification(self):
        self.assertTrue(is_sports_market(family="KXMLBMONEYLINE", ticker="KXMLBMONEYLINE-TEST"))
        self.assertTrue(is_tennis_market(family="KXATPCHALLENGERMATCH", ticker="KXATPCHALLENGERMATCH-TEST"))
        self.assertTrue(is_kxmlbks_market(family="KXMLBKS", ticker="KXMLBKS-TEST"))

    def test_confirm_entry_rejects_tennis_and_kxmlbks(self):
        bot = fresh_bot()
        tennis = target(ticker="KXATPCHALLENGERMATCH-TEST", family="KXATPCHALLENGERMATCH")
        self.assertEqual(
            bot.confirm_entry(tennis, quote(tennis.ticker, tennis.family))["reason"],
            "tennis_live_trading_disabled",
        )
        kxmlbks = target(ticker="KXMLBKS-TEST", family="KXMLBKS")
        self.assertEqual(
            bot.confirm_entry(kxmlbks, quote(kxmlbks.ticker, kxmlbks.family))["reason"],
            "kxmlbks_live_trading_disabled",
        )

    def test_confirm_entry_rejects_synthetic_book(self):
        bot = fresh_bot()
        fake_quote = quote(synthetic=True)
        self.assertEqual(bot.confirm_entry(target(), fake_quote)["reason"], "fake_or_synthetic_book_rejected")

    def test_immediate_red_by_spread_does_not_reject_when_ev_positive(self):
        bot = fresh_bot()
        bot.reconcile_live_positions = lambda **kwargs: {"ok": True, "positions": []}
        result = bot.confirm_entry(target(), quote())
        self.assertTrue(result["allowed"])
        self.assertTrue(result["payload"]["_aion_confirm_entry_passed"])
        self.assertTrue(result["payload"]["_aion_ev_entry_confirm_passed"])
        self.assertLess(result["payload"]["immediate_pnl_per_contract"], 0.0)
        self.assertGreater(result["payload"]["ev_after_all_costs"], 0.0)

    def test_negative_ev_rejects(self):
        bot = fresh_bot()
        result = bot.confirm_entry(target(), quote(fair_yes=0.445))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "ev_after_cost_not_positive")

    def test_taker_allowed_only_with_strong_ev(self):
        bot = fresh_bot()
        bot.reconcile_live_positions = lambda **kwargs: {"ok": True, "positions": []}
        result = bot.confirm_entry(target(), quote(fair_yes=0.58))
        self.assertTrue(result["allowed"])
        self.assertEqual(result["payload"]["entry_mode"], "taker")
        self.assertFalse(result["payload"]["post_only"])
        self.assertGreaterEqual(result["payload"]["taker_ev"], bot.green_entry_scorer.strong_taker_edge_threshold)

    def test_maker_selected_when_taker_weak_but_maker_ev_positive(self):
        bot = fresh_bot()
        bot.reconcile_live_positions = lambda **kwargs: {"ok": True, "positions": []}
        result = bot.confirm_entry(target(), quote(fair_yes=0.49))
        self.assertTrue(result["allowed"])
        self.assertEqual(result["payload"]["entry_mode"], "maker")
        self.assertTrue(result["payload"]["post_only"])
        self.assertEqual(result["payload"]["time_in_force"], "good_till_canceled")
        self.assertLess(result["payload"]["yes_price"], 46)
        self.assertGreater(result["payload"]["maker_ev"], 0.0)

    def test_missing_first_cashout_rejects(self):
        bot = fresh_bot()
        missing_cashout = quote(yes_bid=None, yes_bid_size=0, yes_ask=0.46, no_bid=0.54, no_ask=0.56)
        result = bot.confirm_entry(target(), missing_cashout)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "missing_first_cashout")

    def test_confirm_entry_rejects_stale_target(self):
        bot = fresh_bot()
        stale = target(created_ts=time.time() - bot.max_signal_age_seconds - 5)
        seen = []
        bot.log_json = lambda tag, payload=None: seen.append((tag, payload or {}))
        result = bot.confirm_entry(stale, quote())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "stale_signal")
        self.assertGreater(result["signal_age_seconds"], bot.max_signal_age_seconds)
        self.assertTrue(any(tag == "AION-EV-ENTRY-REJECT" and payload.get("reason") == "stale_signal" for tag, payload in seen))

    def test_stale_target_cannot_submit(self):
        bot = fresh_bot()
        stale = target(created_ts=time.time() - bot.max_signal_age_seconds - 5)
        calls = []
        original_submit = kalshi_execution.submit_canonical_order
        try:
            kalshi_execution.submit_canonical_order = lambda *args, **kwargs: calls.append((args, kwargs)) or {"submitted": True}
            result = bot.submit_target(stale, quote())
        finally:
            kalshi_execution.submit_canonical_order = original_submit
        self.assertFalse(result["submitted"])
        self.assertEqual(result["reason"], "stale_signal")
        self.assertEqual(calls, [])

    def test_reduce_only_exit_not_blocked_by_entry_freshness(self):
        bot = fresh_bot()
        old_position = {
            "ticker": "KXMLBMONEYLINE-26JUN07-TEAM",
            "family": "KXMLBMONEYLINE",
            "side": "yes",
            "count": 1,
            "entry_price": 0.46,
            "entry_ts": time.time() - 10_000,
            "target_id": "old-target",
        }
        decision = {
            "ticker": old_position["ticker"],
            "family": old_position["family"],
            "side": "yes",
            "count": 1,
            "exit_price": 0.50,
            "reason": "test_reduce_only",
        }
        payload = kalshi_exits.build_exit_payload(old_position, decision)
        result = bot.confirm_exit(old_position, decision, payload)
        self.assertTrue(result["allowed"])
        self.assertTrue(result["payload"]["reduce_only"])

    def test_confirm_entry_rejects_payload_side_mismatch(self):
        bot = fresh_bot()
        item = target(side="yes")
        payload = kalshi_execution.canonical_order_payload(
            ticker=item.ticker,
            side="no",
            action="buy",
            count=1,
            price=0.56,
        )
        result = bot.confirm_entry(item, quote(), payload=payload)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "side_payload_mismatch")

    def test_duplicate_guard_rejects_existing_position(self):
        bot = fresh_bot()
        bot.live_positions = [{"ticker": "KXMLBMONEYLINE-26JUN07-TEAM", "family": "KXMLBMONEYLINE", "side": "yes", "count": 1, "status": "open"}]
        bot.reconcile_live_positions = lambda **kwargs: {"ok": True, "positions": bot.live_positions}
        result = bot.confirm_entry(target(), quote())
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "duplicate_live_position_guard")

    def test_maker_order_does_not_open_position_until_reconcile_fill(self):
        bot = fresh_bot()
        bot.reconcile_live_positions = lambda **kwargs: {"ok": True, "positions": []}
        seen = []
        bot.log_json = lambda tag, payload=None: seen.append((tag, payload or {}))
        item = target()
        calls = []
        original_submit = kalshi_execution.submit_canonical_order
        try:
            kalshi_execution.submit_canonical_order = lambda *args, **kwargs: calls.append((args, kwargs)) or {"submitted": True, "reason": "submitted", "order_id": "order-1", "payload": args[1]}
            result = bot.submit_target(item, quote())
        finally:
            kalshi_execution.submit_canonical_order = original_submit
        self.assertTrue(result["submitted"])
        self.assertEqual(bot.state["positions"], {})
        self.assertEqual(len(bot.active_pending_orders()), 1)
        self.assertTrue(any(tag == "ORDER-PENDING" for tag, _payload in seen))
        self.assertFalse(any(tag == "POSITION-OPEN" for tag, _payload in seen))

    def test_pending_maker_fill_confirms_position(self):
        bot = fresh_bot()
        item = target()
        payload = bot._payload_for_target(item)
        payload.update(
            {
                "entry_mode": "maker",
                "entry_price": 0.45,
                "selected_price": 0.45,
                "fair_probability_for_side": 0.49,
                "ev_after_all_costs": 0.03,
                "immediate_cashout": 0.44,
                "immediate_pnl_per_contract": -0.01,
                "event_window_key": "KXMLBMONEYLINE-26JUN07",
            }
        )
        bot.record_pending_entry_order(item, payload, {"submitted": True, "order_id": "order-1"})
        bot._live_request_json = lambda *args, **kwargs: {
            "ok": True,
            "status_code": 200,
            "body": {"market_positions": [{"ticker": item.ticker, "side": "yes", "count": 1, "entry_price": 0.45}]},
        }
        seen = []
        bot.log_json = lambda tag, payload=None: seen.append((tag, payload or {}))
        bot.reconcile_live_positions(force=True)
        self.assertEqual(len(bot.active_pending_orders()), 0)
        self.assertIn(item.ticker, bot.state["positions"])
        self.assertTrue(any(tag == "AION-MAKER-ORDER-FILLED" for tag, _payload in seen))
        self.assertTrue(any(tag == "AION-POSITION-CONFIRMED" for tag, _payload in seen))
        self.assertTrue(any(tag == "POSITION-OPEN" for tag, _payload in seen))

    def test_stale_maker_order_cancels(self):
        bot = fresh_bot()
        bot.state["pending_orders"] = {
            "order-1": {
                "pending_id": "order-1",
                "order_id": "order-1",
                "ticker": "KXMLBMONEYLINE-26JUN07-TEAM",
                "family": "KXMLBMONEYLINE",
                "side": "yes",
                "entry_mode": "maker",
                "status": "pending",
                "submitted_ts": time.time() - bot.max_pending_maker_age_seconds - 5,
                "expires_ts": time.time() - 1,
            }
        }
        bot.cancel_live_order = lambda order_id: {"ok": True, "reason": "cancelled", "order_id": order_id}
        result = bot.manage_pending_maker_orders()
        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(bot.active_pending_orders(), [])

    def test_combo_overconcentration_rejects(self):
        bot = fresh_bot()
        bot.live_positions = [
            {
                "ticker": "KXMVESPORTSMULTIGAMEEXTENDED-26JUN07-A",
                "family": "KXMVESPORTSMULTIGAMEEXTENDED",
                "side": "yes",
                "count": 1,
                "status": "open",
            }
        ]
        bot.reconcile_live_positions = lambda **kwargs: {"ok": True, "positions": bot.live_positions}
        item = target(
            ticker="KXMVESPORTSMULTIGAMEEXTENDED-26JUN07-B",
            family="KXMVESPORTSMULTIGAMEEXTENDED",
        )
        result = bot.confirm_entry(item, quote(item.ticker, item.family))
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "overconcentrated_combo_family")

    def test_combo_near_zero_cashout_rejects(self):
        bot = fresh_bot()
        item = target(
            ticker="KXMVECROSSCATEGORY-26JUN07-A",
            family="KXMVECROSSCATEGORY",
        )
        thin_exit_quote = quote(
            item.ticker,
            item.family,
            yes_bid=0.013,
            yes_ask=0.289,
            no_bid=0.711,
            no_ask=0.987,
            yes_bid_size=8800,
            yes_ask_size=440,
            no_bid_size=440,
            no_ask_size=8800,
        )
        result = bot.confirm_entry(item, thin_exit_quote)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "weak_cashout_quality")

    def test_canonical_payload_supports_yes_no_and_reduce_only(self):
        yes_payload = kalshi_execution.canonical_order_payload(
            ticker="KXMLBMONEYLINE-TEST",
            side="yes",
            action="buy",
            count=1,
            price=0.46,
        )
        no_payload = kalshi_execution.canonical_order_payload(
            ticker="KXMLBMONEYLINE-TEST",
            side="no",
            action="sell",
            count=1,
            price=0.54,
            reduce_only=True,
        )
        self.assertTrue(kalshi_execution.validate_canonical_order_payload(yes_payload)["ok"])
        self.assertEqual(yes_payload["yes_price"], 46)
        self.assertTrue(kalshi_execution.validate_canonical_order_payload(no_payload)["ok"])
        self.assertEqual(no_payload["no_price"], 54)
        self.assertTrue(no_payload["reduce_only"])
        self.assertEqual(no_payload["time_in_force"], "immediate_or_cancel")

    def test_build_quote_requires_real_endpoint_levels(self):
        market = {"ticker": "KXMLBMONEYLINE-TEST", "series_ticker": "KXMLBMONEYLINE"}
        book = {"orderbook": {"yes": [[44, 20]], "no": [[54, 30]]}}
        parsed = build_quote(market, book)
        self.assertTrue(parsed.orderbook_real)
        self.assertEqual(parsed.yes_ask, 0.46)
        self.assertEqual(parsed.no_ask, 0.56)


if __name__ == "__main__":
    unittest.main()

class PriceIntegrityTests(unittest.TestCase):
    def test_exact_cent_prices(self):
        self.assertEqual(kalshi_execution.price_to_cents("0.46"), 46)
        self.assertEqual(kalshi_execution.price_to_cents(0.46), 46)

    def test_off_tick_price_is_rejected(self):
        self.assertIsNone(kalshi_execution.price_to_cents("0.465"))
        self.assertIsNone(kalshi_execution.price_to_cents("0.461"))
        self.assertIsNone(kalshi_execution.price_to_cents("1.00"))
        self.assertIsNone(kalshi_execution.price_to_cents("0.00"))

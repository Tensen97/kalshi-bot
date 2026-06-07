import dataclasses
import unittest

import kalshi_execution
from kalshi_orderbook import Quote, build_quote, is_kxmlbks_market, is_sports_market, is_tennis_market
from kalshi_web import KalshiSniperBot, TradeTarget


def quote(ticker="KXMLBMONEYLINE-26JUN07-TEAM", family="KXMLBMONEYLINE"):
    return Quote(
        ticker=ticker,
        family=family,
        title="MLB test market",
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
        orderbook_snapshot_id="snapshot",
        orderbook_source="orderbook_endpoint:test",
        orderbook_real=True,
        synthetic=False,
        raw_market={"ticker": ticker, "series_ticker": family},
    )


def target(ticker="KXMLBMONEYLINE-26JUN07-TEAM", family="KXMLBMONEYLINE", side="yes"):
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
        created_ts=1.0,
    )


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
        bot = KalshiSniperBot()
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
        bot = KalshiSniperBot()
        fake_quote = dataclasses.replace(quote(), synthetic=True, orderbook_real=False)
        self.assertEqual(bot.confirm_entry(target(), fake_quote)["reason"], "fake_or_synthetic_book_rejected")

    def test_confirm_entry_allows_real_sports_book(self):
        bot = KalshiSniperBot()
        bot.reconcile_live_positions = lambda **kwargs: {"ok": True, "positions": []}
        result = bot.confirm_entry(target(), quote())
        self.assertTrue(result["allowed"])
        self.assertTrue(result["payload"]["_aion_confirm_entry_passed"])

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

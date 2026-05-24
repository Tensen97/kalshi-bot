import unittest
from types import SimpleNamespace

import kalshi_web as kw


class SportsEdgeRankingTests(unittest.TestCase):
    def _make_bot(self):
        bot = kw.KalshiSniperBot.__new__(kw.KalshiSniperBot)
        bot.sports_edge_ranking_log_markers = {}
        bot.sports_momentum_state = {}
        bot.targeted_orderbook_cache = {}
        bot.trading_halted = False
        bot.realized_pnl = 0.0
        bot.live_realized_pnl_today = 0.0
        bot.aion_data_coverage_cache = {
            "sports_family_coverage": [
                {
                    "family": "KXNBA1HTOTAL",
                    "records": 25,
                    "unique_markets_tickers": 8,
                    "liquidity_samples": 12,
                    "price_observations": 12,
                    "avg_spread": 0.03,
                    "tags": ["SPORTS_DATA_READY", "SPORTS_DATA_ACTIVE"],
                    "activity_level": "ready",
                }
            ]
        }
        bot.aion_data_coverage_last_ts = 0.0
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False}
        return bot

    def test_sports_underdog_asymmetry_gets_tags_and_boost(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-TEST",
            selected_side="yes",
            selected_price=0.34,
            yes_bid=0.31,
            yes_ask=0.34,
            no_bid=0.65,
            no_ask=0.68,
            yes_top_size=8,
            no_top_size=7,
        )
        context = bot._build_sports_edge_ranking_context(
            q=q,
            spread=0.03,
            family="KXNBA1HTOTAL",
            expectancy_summary={
                "sports_edge_after_cost": 0.03,
                "momentum_score": 0.05,
                "directional_bias": "flat",
            },
            source="unit_test",
            log=False,
        )

        self.assertTrue(context["is_sports"])
        self.assertGreater(context["boost"], 0.0)
        self.assertIn("SPORTS_UNDERDOG_VALUE", context["tags"])
        self.assertIn("SPORTS_ASYMMETRIC_PAYOUT", context["tags"])
        self.assertIn("SPORTS_GOOD_BOOK", context["tags"])
        self.assertIn("SPORTS_DATA_READY", context["data_readiness_tags"])

    def test_sports_edge_can_nudge_ev_sizing_without_exceeding_cap_hint(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-TEST",
            selected_side="yes",
            selected_price=0.34,
            yes_bid=0.31,
            yes_ask=0.34,
            no_bid=0.65,
            no_ask=0.68,
            yes_top_size=8,
            no_top_size=7,
        )
        ev_context = {
            "ticker": q.ticker,
            "family": "KXNBA1HTOTAL",
            "ev_sizing_multiplier": 1.0,
            "expectancy_summary": {
                "sports_edge_after_cost": 0.03,
                "momentum_score": 0.05,
                "directional_bias": "flat",
            },
        }

        adjusted = bot._apply_sports_edge_sizing_hint(
            ev_context,
            q=q,
            spread=0.03,
            family="KXNBA1HTOTAL",
            source="unit_test",
        )

        self.assertGreater(adjusted["ev_sizing_multiplier"], 1.0)
        self.assertLessEqual(adjusted["ev_sizing_multiplier"], kw.AION_SPORTS_EDGE_MAX_SIZE_MULTIPLIER)
        self.assertIn("SPORTS_GOOD_BOOK", adjusted["sports_edge_tags"])


if __name__ == "__main__":
    unittest.main()

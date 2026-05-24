import unittest
from types import SimpleNamespace

import kalshi_web as kw
from kalshi_sniper.candidate_quality import compute_candidate_ev


class StructuralEdgeTests(unittest.TestCase):
    def _make_bot(self):
        bot = kw.KalshiSniperBot.__new__(kw.KalshiSniperBot)
        bot.structural_edge_log_markers = {}
        bot.sports_edge_ranking_log_markers = {}
        bot.sports_momentum_state = {}
        bot.targeted_orderbook_cache = {}
        bot.family_profit_memory_cache = {"loaded_at": 0.0, "stats": {}}
        bot.family_profit_memory_log_markers = {}
        return bot

    def test_ladder_dislocation_gets_structural_score_and_boost(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBSTRIKEOUT-TEST-65",
            selected_side="yes",
            selected_price=0.34,
            yes_bid=0.31,
            yes_ask=0.35,
            no_bid=0.64,
            no_ask=0.69,
            yes_top_size=4,
            no_top_size=9,
        )
        market = {
            "ticker": q.ticker,
            "event_ticker": "KXMLBSTRIKEOUT-TEST",
            "title": "Pitcher strikeouts over 6.5",
            "yes_bid": 31,
            "yes_ask": 35,
            "no_bid": 64,
            "no_ask": 69,
        }
        markets = [
            market,
            {
                "ticker": "KXMLBSTRIKEOUT-TEST-55",
                "event_ticker": "KXMLBSTRIKEOUT-TEST",
                "title": "Pitcher strikeouts over 5.5",
                "yes_bid": 58,
                "yes_ask": 62,
            },
            {
                "ticker": "KXMLBSTRIKEOUT-TEST-75",
                "event_ticker": "KXMLBSTRIKEOUT-TEST",
                "title": "Pitcher strikeouts over 7.5",
                "yes_bid": 46,
                "yes_ask": 50,
            },
        ]

        context = bot._get_structural_edge_rank_adjustment(
            market=market,
            q=q,
            spread=0.04,
            family="KXMLBSTRIKEOUT",
            markets=markets,
            source="unit_test",
            log=False,
        )

        self.assertGreater(context["STRUCTURAL_EDGE_SCORE"], 50.0)
        self.assertGreater(context["boost"], 0.0)
        self.assertGreater(context["probability_adjustment"], 0.0)
        self.assertIn("STRUCTURAL_PROBABILITY_DISLOCATION", context["tags"])

    def test_cross_market_disagreement_adds_cross_market_score(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBSTRIKEOUT-TEST-65",
            selected_side="yes",
            selected_price=0.22,
            yes_bid=0.20,
            yes_ask=0.24,
            no_bid=0.76,
            no_ask=0.80,
            yes_top_size=1,
            no_top_size=10,
        )
        market = {
            "ticker": q.ticker,
            "event_ticker": "KXMLB-GAME-TEST",
            "title": "Pitcher strikeouts over 6.5",
            "yes_bid": 20,
            "yes_ask": 24,
            "no_bid": 76,
            "no_ask": 80,
        }
        markets = [
            market,
            {
                "ticker": "KXMLBTOTAL-TEST-85",
                "event_ticker": "KXMLB-GAME-TEST",
                "title": "Game total over 8.5",
                "yes_bid": 70,
                "yes_ask": 74,
            },
            {
                "ticker": "KXMLBSPREAD-TEST-HOME",
                "event_ticker": "KXMLB-GAME-TEST",
                "title": "Home team spread -1.5",
                "yes_bid": 66,
                "yes_ask": 70,
            },
            {
                "ticker": "KXMLBPROP-TEST-HITS",
                "event_ticker": "KXMLB-GAME-TEST",
                "title": "Player hits over 1.5",
                "yes_bid": 60,
                "yes_ask": 64,
            },
        ]

        context = bot._get_structural_edge_rank_adjustment(
            market=market,
            q=q,
            spread=0.04,
            family="KXMLBSTRIKEOUT",
            markets=markets,
            source="unit_test_cross",
            log=False,
        )

        self.assertGreater(context["CROSS_MARKET_EDGE_SCORE"], 35.0)
        self.assertGreater(context["ELITE_EDGE_CONFIDENCE"], 40.0)
        self.assertGreater(context["cross_market_edge_context"]["peer_count"], 0)
        self.assertIn("CROSS_MARKET_DISTRIBUTION_INCONSISTENCY", context["tags"])
        self.assertGreater(context["size_multiplier"], 1.0)

    def test_btc_1h_ladder_underpriced_strike_gets_promoted(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXBTC1H-TEST-71000",
            family="KXBTC1H",
            selected_side="yes",
            selected_price=0.37,
            yes_bid=0.35,
            yes_ask=0.37,
            no_bid=0.63,
            no_ask=0.65,
            yes_top_size=32,
            no_top_size=40,
            price_delta=0.00,
            repricing_velocity=0.00,
        )
        market = {
            "ticker": q.ticker,
            "series_ticker": "KXBTC1H",
            "event_ticker": "KXBTC1H-TEST-HOUR",
            "title": "Bitcoin above $71,000 at 4pm",
            "yes_bid": 35,
            "yes_ask": 37,
            "no_bid": 63,
            "no_ask": 65,
            "yes_top_size": 32,
            "no_top_size": 40,
            "price_delta": 0.00,
        }
        markets = [
            {
                "ticker": "KXBTC1H-TEST-70000",
                "series_ticker": "KXBTC1H",
                "event_ticker": "KXBTC1H-TEST-HOUR",
                "title": "Bitcoin above $70,000 at 4pm",
                "yes_bid": 67,
                "yes_ask": 69,
                "no_bid": 31,
                "no_ask": 33,
                "yes_top_size": 22,
                "no_top_size": 30,
                "price_delta": 0.04,
            },
            {
                "ticker": "KXBTC1H-TEST-70500",
                "series_ticker": "KXBTC1H",
                "event_ticker": "KXBTC1H-TEST-HOUR",
                "title": "Bitcoin above $70,500 at 4pm",
                "yes_bid": 60,
                "yes_ask": 62,
                "no_bid": 38,
                "no_ask": 40,
                "yes_top_size": 22,
                "no_top_size": 30,
                "price_delta": 0.035,
            },
            market,
            {
                "ticker": "KXBTC1H-TEST-71500",
                "series_ticker": "KXBTC1H",
                "event_ticker": "KXBTC1H-TEST-HOUR",
                "title": "Bitcoin above $71,500 at 4pm",
                "yes_bid": 48,
                "yes_ask": 50,
                "no_bid": 50,
                "no_ask": 52,
                "yes_top_size": 20,
                "no_top_size": 25,
                "price_delta": 0.03,
            },
        ]

        context = bot._get_structural_edge_rank_adjustment(
            market=market,
            q=q,
            spread=0.02,
            family="KXBTC1H",
            markets=markets,
            source="unit_test_btc_ladder",
            log=False,
        )

        btc_context = context["btc_1h_ladder_context"]
        self.assertGreater(btc_context["BTC_1H_LADDER_SCORE"], 45.0)
        self.assertGreater(btc_context["boost"], 0.0)
        self.assertGreater(context["boost"], 0.0)
        self.assertGreater(context["size_multiplier"], 1.0)
        self.assertIn("BTC_1H_LADDER_RELATIVE_MISPRICING", context["tags"])
        self.assertEqual(getattr(q, "BTC_1H_LADDER_SCORE"), btc_context["BTC_1H_LADDER_SCORE"])

    def test_structural_context_increases_ev_conviction_sizing(self):
        candidate = {
            "ticker": "KXMLBSTRIKEOUT-TEST-65",
            "family": "KXMLBSTRIKEOUT",
            "selected_side": "yes",
            "selected_price": 0.34,
            "yes_bid": 0.31,
            "yes_ask": 0.35,
            "no_bid": 0.64,
            "no_ask": 0.69,
            "STRUCTURAL_EDGE_SCORE": 84.0,
            "CROSS_MARKET_EDGE_SCORE": 76.0,
            "cross_market_edge_context": {"size_multiplier": 1.52, "probability_adjustment": 0.04},
            "ELITE_EDGE_CONFIDENCE": 82.0,
            "elite_edge_context": {"size_multiplier": 4.75, "kelly_max_multiplier": 12.0, "tier": "strong"},
            "structural_edge_probability_adjustment": 0.052,
            "structural_edge_size_multiplier": 1.34,
            "family_profile": {"sizing_bias": 1.10, "structural_edge_weight": 1.25},
            "live_state_context": {"live_state_score": 0.82},
        }

        ev = compute_candidate_ev(candidate)

        self.assertGreater(ev["estimated_win_probability"], ev["market_implied_probability"])
        self.assertIn(ev["ev_tier"], {"A", "S"})
        self.assertGreater(ev["ev_sizing_multiplier"], 2.0)
        self.assertGreater(ev["conviction_size_multiplier"], 4.0)
        self.assertEqual(ev["elite_edge_concentration_tier"], "strong")

    def test_dynamic_entry_size_promotes_elite_context_above_legacy_one_contract_payload(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBSTRIKEOUT-TEST-65",
            family="KXMLBSTRIKEOUT",
            ev_tier="S",
            ev_sizing_multiplier=1.0,
            conviction_size_multiplier=1.0,
            elite_edge_size_multiplier=8.0,
            ELITE_EDGE_CONFIDENCE=91.0,
            STRUCTURAL_EDGE_SCORE=92.0,
            CROSS_MARKET_EDGE_SCORE=84.0,
            expected_roi=0.11,
            expected_value=0.038,
            ev_edge=0.055,
            selected_side="yes",
            selected_price=0.20,
            yes_bid=0.19,
            yes_ask=0.21,
            no_bid=0.79,
            no_ask=0.81,
            yes_top_size=180,
            no_top_size=200,
            spread=0.02,
            orderbook_ready=True,
            quote_ready=True,
        )

        resolved = bot._resolve_dynamic_live_entry_size(
            q,
            explicit_size=1,
            payload={"ticker": q.ticker, "count": 1, "selected_side": "yes", "selected_price": 0.20},
            ev_context={
                "ticker": q.ticker,
                "family": q.family,
                "ev_tier": "S",
                "ELITE_EDGE_CONFIDENCE": 91.0,
                "STRUCTURAL_EDGE_SCORE": 92.0,
                "CROSS_MARKET_EDGE_SCORE": 84.0,
                "elite_edge_size_multiplier": 8.0,
                "expected_roi": 0.11,
                "expected_value": 0.038,
                "ev_edge": 0.055,
            },
        )

        self.assertGreaterEqual(resolved, 100)
        self.assertLessEqual(resolved, 150)
        self.assertGreaterEqual(resolved * q.selected_price, 20.0)
        self.assertLessEqual(resolved * q.selected_price, kw.AION_ELITE_MAX_DOLLAR_EXPOSURE)
        self.assertLessEqual(resolved, kw.MAX_LIVE_ORDER_SIZE)

    def test_dynamic_entry_size_downgrades_fake_elite_without_positive_ev_or_book_depth(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBSTRIKEOUT-TEST-65",
            family="KXMLBSTRIKEOUT",
            ev_tier="S",
            elite_edge_size_multiplier=12.0,
            ELITE_EDGE_CONFIDENCE=94.0,
            STRUCTURAL_EDGE_SCORE=91.0,
            CROSS_MARKET_EDGE_SCORE=82.0,
            expected_roi=-0.03,
            expected_value=-0.01,
            ev_edge=-0.02,
            selected_side="yes",
            selected_price=0.34,
            yes_bid=None,
            yes_ask=0.35,
            no_bid=0.64,
            no_ask=0.69,
            yes_top_size=1,
            spread=0.04,
            orderbook_ready=True,
            quote_ready=True,
        )

        resolved = bot._resolve_dynamic_live_entry_size(
            q,
            explicit_size=1,
            payload={"ticker": q.ticker, "count": 1, "selected_side": "yes", "selected_price": 0.34},
            ev_context={
                "ticker": q.ticker,
                "family": q.family,
                "ev_tier": "S",
                "ELITE_EDGE_CONFIDENCE": 94.0,
                "STRUCTURAL_EDGE_SCORE": 91.0,
                "CROSS_MARKET_EDGE_SCORE": 82.0,
                "elite_edge_size_multiplier": 12.0,
                "expected_roi": -0.03,
                "expected_value": -0.01,
                "ev_edge": -0.02,
            },
        )

        self.assertEqual(resolved, 1)

    def test_dynamic_entry_size_keeps_weak_context_as_probe(self):
        bot = self._make_bot()
        resolved = bot._resolve_dynamic_live_entry_size(
            explicit_size=1,
            payload={"ticker": "KXMLBSPREAD-TEST", "count": 1},
            ev_context={
                "ticker": "KXMLBSPREAD-TEST",
                "ev_tier": "C",
                "ELITE_EDGE_CONFIDENCE": 18.0,
                "ev_sizing_multiplier": 1.0,
            },
        )

        self.assertEqual(resolved, 1)

    def test_dynamic_entry_size_keeps_weak_cheap_contract_to_small_dollar_probe(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBPROP-TEST",
            family="KXMLBPROP",
            ev_tier="C",
            ELITE_EDGE_CONFIDENCE=18.0,
            STRUCTURAL_EDGE_SCORE=12.0,
            CROSS_MARKET_EDGE_SCORE=8.0,
            expected_roi=0.004,
            expected_value=0.001,
            selected_side="yes",
            selected_price=0.20,
            yes_bid=0.19,
            yes_ask=0.21,
            no_bid=0.79,
            no_ask=0.81,
            yes_top_size=100,
            no_top_size=100,
            spread=0.02,
            orderbook_ready=True,
            quote_ready=True,
        )

        resolved = bot._resolve_dynamic_live_entry_size(
            q,
            explicit_size=1,
            payload={"ticker": q.ticker, "count": 1, "selected_side": "yes", "selected_price": 0.20},
            ev_context={
                "ticker": q.ticker,
                "family": q.family,
                "ev_tier": "C",
                "ELITE_EDGE_CONFIDENCE": 18.0,
                "STRUCTURAL_EDGE_SCORE": 12.0,
                "CROSS_MARKET_EDGE_SCORE": 8.0,
                "expected_roi": 0.004,
                "expected_value": 0.001,
            },
        )

        self.assertGreaterEqual(resolved, 1)
        self.assertGreaterEqual(resolved * q.selected_price, 1.0)
        self.assertLessEqual(resolved * q.selected_price, kw.AION_WEAK_MAX_DOLLAR_EXPOSURE)


if __name__ == "__main__":
    unittest.main()

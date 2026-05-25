import unittest
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import kalshi_web as kw
from kalshi_sniper.candidate_quality import compute_candidate_ev
from memory.btc_ladder_history_store import BTCLadderHistoryStore


class StructuralEdgeTests(unittest.TestCase):
    def _make_bot(self):
        bot = kw.KalshiSniperBot.__new__(kw.KalshiSniperBot)
        bot.structural_edge_log_markers = {}
        bot.sports_edge_ranking_log_markers = {}
        bot.sports_momentum_state = {}
        bot.targeted_orderbook_cache = {}
        bot.family_profit_memory_cache = {"loaded_at": 0.0, "stats": {}}
        bot.family_profit_memory_log_markers = {}
        bot.btc_1h_history_store = None
        bot.btc_1h_history_audit = {}
        bot.btc_1h_history_no_data_logged = True
        bot.btc1h_cycle_last_run_ts = 0.0
        bot.btc1h_cycle_priority_by_ticker = {}
        bot.btc1h_cycle_last_summary = {}
        bot.btc1h_cycle_write_store = None
        bot.btc1h_cycle_storage_warning_logged = True
        bot.last_lane_allocation_summary = {}
        bot.last_dual_lane_buy_candidates = []
        bot.last_dual_lane_execution_summary = {}
        bot.live_trade_count_today = 0
        bot.live_positions = []
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

    def test_btc_1h_ladder_history_boost_is_advisory_and_size_positive(self):
        bot = self._make_bot()
        with tempfile.TemporaryDirectory() as tmpdir:
            store = BTCLadderHistoryStore(db_path=Path(tmpdir) / "btc_ladder.sqlite")
            base = datetime(2026, 5, 20, 14, 0, tzinfo=timezone.utc)
            records = []
            for group_idx in range(12):
                opened = base - timedelta(hours=group_idx + 1)
                closed = opened + timedelta(hours=1)
                for strike in (70000, 70500, 71000):
                    records.append(
                        {
                            "ticker": f"KXBTC1H-HIST-{group_idx}-{strike}",
                            "event_ticker": f"KXBTC1H-HIST-{group_idx}",
                            "family": "KXBTC1H",
                            "title": f"Bitcoin above ${strike:,} at 3pm",
                            "open_time": opened.isoformat().replace("+00:00", "Z"),
                            "close_time": closed.isoformat().replace("+00:00", "Z"),
                            "source_time": closed.isoformat().replace("+00:00", "Z"),
                            "strike": strike,
                            "outcome": "yes",
                            "yes_bid": 0.35,
                            "yes_ask": 0.37,
                            "no_bid": 0.63,
                            "no_ask": 0.65,
                            "last_price": 0.36,
                            "liquidity": 250,
                        }
                    )
            store.insert_records(records, batch_size=10)
            store.recompute_summaries()
            bot.btc_1h_history_store = store

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
                source="unit_test_btc_history",
                log=False,
            )

            btc_context = context["btc_1h_ladder_context"]
            self.assertTrue(btc_context["history_signal"]["available"])
            self.assertGreater(btc_context["historical_edge_boost"], 0.0)
            self.assertGreater(btc_context["history_sample_size"], 20)
            self.assertIn("BTC_1H_HISTORY_PROFITABLE_BUCKET", context["tags"])
            self.assertGreater(context["size_multiplier"], 1.0)

    def test_btc1h_adaptive_cycle_promotes_best_ladder_candidate_without_storage_dependency(self):
        bot = self._make_bot()
        bot._fetch_btc1h_cycle_markets = lambda status="open", limit=None: ([], [])
        bot._refresh_btc1h_settlement_learning = lambda source="unit": {
            "stored": 0,
            "updated": 0,
            "duplicates": 0,
            "malformed": 0,
            "errors": 0,
            "records_seen": 0,
        }
        markets = [
            {
                "ticker": "KXBTC1H-CYCLE-70000",
                "series_ticker": "KXBTC1H",
                "event_ticker": "KXBTC1H-CYCLE-HOUR",
                "title": "Bitcoin above $70,000 at 4pm",
                "yes_bid": 67,
                "yes_ask": 69,
                "no_bid": 31,
                "no_ask": 33,
                "yes_top_size": 35,
                "no_top_size": 30,
                "price_delta": 0.04,
            },
            {
                "ticker": "KXBTC1H-CYCLE-70500",
                "series_ticker": "KXBTC1H",
                "event_ticker": "KXBTC1H-CYCLE-HOUR",
                "title": "Bitcoin above $70,500 at 4pm",
                "yes_bid": 60,
                "yes_ask": 62,
                "no_bid": 38,
                "no_ask": 40,
                "yes_top_size": 35,
                "no_top_size": 30,
                "price_delta": 0.035,
            },
            {
                "ticker": "KXBTC1H-CYCLE-71000",
                "series_ticker": "KXBTC1H",
                "event_ticker": "KXBTC1H-CYCLE-HOUR",
                "title": "Bitcoin above $71,000 at 4pm",
                "yes_bid": 35,
                "yes_ask": 37,
                "no_bid": 63,
                "no_ask": 65,
                "yes_top_size": 35,
                "no_top_size": 30,
                "price_delta": 0.00,
            },
            {
                "ticker": "KXBTC1H-CYCLE-71500",
                "series_ticker": "KXBTC1H",
                "event_ticker": "KXBTC1H-CYCLE-HOUR",
                "title": "Bitcoin above $71,500 at 4pm",
                "yes_bid": 48,
                "yes_ask": 50,
                "no_bid": 50,
                "no_ask": 52,
                "yes_top_size": 35,
                "no_top_size": 30,
                "price_delta": 0.03,
            },
        ]

        summary = bot._run_btc1h_adaptive_cycle(markets=markets, source="unit_cycle", force=True)

        self.assertTrue(summary["enabled"])
        self.assertEqual(summary["ladder_groups"], 1)
        self.assertGreaterEqual(summary["scored"], 1)
        self.assertGreaterEqual(summary["promoted"], 1)
        self.assertTrue(bot.btc1h_cycle_priority_by_ticker)
        self.assertIn("markets_to_merge", summary)

    def test_crypto_ladder_cycle_promotes_eth_ladder_and_uses_crypto_lane(self):
        bot = self._make_bot()
        bot._fetch_btc1h_cycle_markets = lambda status="open", limit=None: ([], [])
        bot._refresh_btc1h_settlement_learning = lambda source="unit": {
            "stored": 0,
            "updated": 0,
            "duplicates": 0,
            "malformed": 0,
            "errors": 0,
            "records_seen": 0,
        }
        markets = [
            {
                "ticker": "KXETH1H-CYCLE-3600",
                "series_ticker": "KXETH1H",
                "event_ticker": "KXETH1H-CYCLE-HOUR",
                "title": "Ethereum above $3,600 at 4pm",
                "yes_bid": 67,
                "yes_ask": 69,
                "no_bid": 31,
                "no_ask": 33,
                "yes_top_size": 35,
                "no_top_size": 30,
                "price_delta": 0.04,
            },
            {
                "ticker": "KXETH1H-CYCLE-3650",
                "series_ticker": "KXETH1H",
                "event_ticker": "KXETH1H-CYCLE-HOUR",
                "title": "Ethereum above $3,650 at 4pm",
                "yes_bid": 60,
                "yes_ask": 62,
                "no_bid": 38,
                "no_ask": 40,
                "yes_top_size": 35,
                "no_top_size": 30,
                "price_delta": 0.035,
            },
            {
                "ticker": "KXETH1H-CYCLE-3700",
                "series_ticker": "KXETH1H",
                "event_ticker": "KXETH1H-CYCLE-HOUR",
                "title": "Ethereum above $3,700 at 4pm",
                "yes_bid": 35,
                "yes_ask": 37,
                "no_bid": 63,
                "no_ask": 65,
                "yes_top_size": 35,
                "no_top_size": 30,
                "price_delta": 0.00,
            },
        ]

        summary = bot._run_btc1h_adaptive_cycle(markets=markets, source="unit_eth_cycle", force=True)
        promoted = next(iter(bot.btc1h_cycle_priority_by_ticker.values()))
        q = SimpleNamespace(
            ticker=promoted["ticker"],
            selected_side=promoted["selected_side"],
            selected_price=promoted["selected_price"],
            yes_bid=0.35,
            yes_ask=0.37,
            no_bid=0.63,
            no_ask=0.65,
            liquidity=35,
        )
        lane = bot._candidate_execution_lane(
            family="KXETH1H",
            ticker=q.ticker,
            market=next(item for item in markets if item["ticker"] == q.ticker),
            q=q,
            expectancy_item={"btc1h_cycle_context": promoted},
        )

        self.assertTrue(summary["enabled"])
        self.assertIn("ETH", summary["crypto_assets_detected"])
        self.assertEqual(promoted["asset_symbol"], "ETH")
        self.assertEqual(promoted["timeframe"], "1h")
        self.assertEqual(lane, "CRYPTO_LADDER")

    def test_dual_lane_allocation_reserves_btc1h_over_weak_sports_prop(self):
        bot = self._make_bot()
        btc_q = SimpleNamespace(
            ticker="KXBTC-26MAY2517-B74875",
            selected_side="yes",
            selected_price=0.04,
            yes_bid=0.01,
            yes_ask=0.04,
            no_bid=0.96,
            no_ask=0.99,
            liquidity=258,
        )
        sports_q = SimpleNamespace(
            ticker="KXMLBHR-PLAYER-LONGSHOT",
            selected_side="yes",
            selected_price=0.06,
            yes_bid=0.03,
            yes_ask=0.06,
            no_bid=0.94,
            no_ask=0.97,
            liquidity=1,
        )
        markets = [
            {
                "ticker": btc_q.ticker,
                "series_ticker": "KXBTC",
                "event_ticker": "KXBTC-26MAY2517",
                "title": "Bitcoin above $74,999.99 at 5pm",
            },
            {
                "ticker": sports_q.ticker,
                "series_ticker": "KXMLBHR",
                "event_ticker": "KXMLBHR-GAME",
                "title": "Player to hit a home run",
            },
        ]
        expectancy = {
            btc_q.ticker: {
                "raw_score": 68.0,
                "adjusted_score": 68.0,
                "q": btc_q,
                "spread": 0.03,
                "family": "KXBTC",
                "expectancy_summary": {"final_edge": 0.004, "selected_side": "yes", "selected_price": 0.04},
                "quality_snapshot": {"selected_price": 0.04, "liquidity_depth": 258, "spread": 0.03},
                "ev_context": {"ev_edge": 0.004},
                "btc1h_cycle_context": {
                    "final_score": 41.5,
                    "cycle_rank_boost": 1.7,
                    "historical_edge_boost": 0.4,
                    "components": {"nearby_confirmation": 1.0},
                },
            },
            sports_q.ticker: {
                "raw_score": 82.0,
                "adjusted_score": 82.0,
                "q": sports_q,
                "spread": 0.03,
                "family": "KXMLBHR",
                "expectancy_summary": {
                    "final_edge": 0.0,
                    "sports_edge_after_cost": 0.0,
                    "selected_side": "yes",
                    "selected_price": 0.06,
                    "expectancy_score": 0.31,
                },
                "quality_snapshot": {"selected_price": 0.06, "liquidity_depth": 1, "spread": 0.03},
                "sports_edge_context": {"boost": 0.0, "tags": []},
                "ev_context": {"ev_edge": 0.0},
            },
        }

        ranked = bot._apply_dual_lane_allocation_ranking(
            [(82.0, sports_q, 0.03, "KXMLBHR"), (68.0, btc_q, 0.03, "KXBTC")],
            markets,
            expectancy_summary_by_ticker=expectancy,
            source="unit_test",
        )

        self.assertEqual(ranked[0][1].ticker, btc_q.ticker)
        self.assertEqual(expectancy[sports_q.ticker]["lane_allocation"]["decision"], "SKIP_WEAK_SPORT")
        self.assertTrue(expectancy[btc_q.ticker]["lane_allocation"]["reserved"])

    def test_sports_lane_allocation_keeps_strong_sports_candidate(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBSPREAD-QUALITY",
            selected_side="yes",
            selected_price=0.47,
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            liquidity=120,
        )
        context = bot._build_lane_allocation_context(
            {
                "raw_score": 74.0,
                "adjusted_score": 74.0,
                "q": q,
                "spread": 0.02,
                "family": "KXMLBSPREAD",
                "expectancy_summary": {
                    "final_edge": 0.018,
                    "sports_edge_after_cost": 0.018,
                    "selected_side": "yes",
                    "selected_price": 0.47,
                    "expectancy_score": 0.71,
                },
                "quality_snapshot": {"selected_price": 0.47, "liquidity_depth": 120, "spread": 0.02},
                "sports_edge_context": {"boost": 4.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.018},
            },
            market={"ticker": q.ticker, "series_ticker": "KXMLBSPREAD", "title": "MLB spread"},
        )

        self.assertEqual(context["decision"], "ALLOW_STRONG_SPORT")
        self.assertGreater(context["allocation_adjustment"], 0.0)

    def test_dual_lane_secondary_executes_different_strong_lane_through_submit_guard(self):
        bot = self._make_bot()
        submitted = []
        sports_q = SimpleNamespace(
            ticker="KXMLBSPREAD-SECONDARY",
            selected_side="yes",
            selected_price=0.48,
            yes_bid=0.46,
            yes_ask=0.48,
            no_bid=0.52,
            no_ask=0.54,
            liquidity=80,
        )
        bot.last_dual_lane_buy_candidates = [
            {"ticker": "KXBTC-PRIMARY", "lane": "BTC1H_LADDER", "family": "KXBTC"},
            {
                "ticker": sports_q.ticker,
                "lane": "SPORTS",
                "family": "KXMLBSPREAD",
                "q": sports_q,
                "market": {"ticker": sports_q.ticker, "series_ticker": "KXMLBSPREAD"},
                "allocation": {
                    "lane": "SPORTS",
                    "allocation_score": 81.0,
                    "edge": 0.015,
                    "confidence": 0.7,
                    "spread": 0.02,
                    "liquidity": 80,
                    "decision": "ALLOW_STRONG_SPORT",
                    "reason": "strong_positive_edge",
                },
            },
        ]
        bot.already_holding = lambda ticker: False
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False}
        bot.build_live_order_payload = lambda q, **kwargs: {
            "ticker": q.ticker,
            "side": "yes",
            "price": 0.48,
            "count": 2,
            "yes_price": 48,
        }
        bot.submit_live_order = lambda q, lane=None, payload_override=None, **kwargs: {
            "submitted": True,
            "reason": "submitted",
            "payload": dict(payload_override or {}),
        }
        bot.record_live_position = lambda q, size=None, entry_price=None, side=None: submitted.append((q.ticker, size, entry_price, side))

        result = bot._execute_dual_lane_secondary_candidate(primary_ticker="KXBTC-PRIMARY", primary_lane="BTC1H_LADDER")

        self.assertTrue(result["submitted"])
        self.assertEqual(submitted, [(sports_q.ticker, 2, 0.48, "yes")])

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

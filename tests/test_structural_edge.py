import unittest
import contextlib
import io
import tempfile
import time
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
        bot.game_thesis_log_markers = {}
        bot.sports_ladder_log_markers = {}
        bot.sports_regime_log_markers = {}
        bot.sports_graph_log_markers = {}
        bot.sports_spread_log_markers = {}
        bot.last_sports_graph_contexts = {}
        bot.last_sports_ladder_plan = {}
        bot.last_sports_ladder_execution_summary = {}
        bot.sports_momentum_state = {}
        bot.targeted_orderbook_cache = {}
        bot.family_profit_memory_cache = {"loaded_at": 0.0, "stats": {}}
        bot.family_profit_memory_log_markers = {}
        bot.live_family_perf_stats = {}
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

    def _set_sports_only_mode(self, enabled):
        old_mode = kw.AION_SPORTS_ONLY_MODE
        kw.AION_SPORTS_ONLY_MODE = bool(enabled)
        self.addCleanup(setattr, kw, "AION_SPORTS_ONLY_MODE", old_mode)

    def _set_temp_sports_memory(self, bot, tmpdir):
        bot.aion_sports_memory_file = str(Path(tmpdir) / "aion_adaptive_sports_memory.json")
        bot.aion_sports_memory_state = None
        bot.aion_sports_memory_loaded_path = None
        bot.sports_memory_log_markers = {}

    def _record_sports_memory_result(self, bot, ticker, *, family="KXNBA1HTOTAL", pnl=0.25, entry_price=0.45, exit_price=0.55):
        event = {
            "event_type": "trade_closed",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "close_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker,
            "family": family,
            "category": "sports",
            "market_title": "NBA 1H total ladder",
            "side": "yes",
            "entry_price": entry_price,
            "exit_price": exit_price,
            "price": exit_price,
            "contracts": 3,
            "pnl_dollars": pnl,
            "hold_seconds": 420,
            "spread": 0.02,
            "original_confidence": 0.72,
            "sports_tags": ["SPORTS_GOOD_BOOK", "STRUCTURED_LADDER"],
            "result": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
            "exit_reason": "unit_test",
            "order_id": f"{ticker}-{pnl}-{time.time_ns()}",
        }
        with contextlib.redirect_stdout(io.StringIO()):
            return bot._record_sports_memory_trade_event(event)

    def test_execution_log_mode_keeps_execution_and_suppresses_advisory(self):
        old_mode = kw.AION_LOG_MODE
        old_debug = kw.AION_DEBUG_TELEMETRY
        old_total = kw._TELEMETRY_STATE.get("advisory_suppressed_total", 0)
        old_counts = kw._TELEMETRY_STATE.get("advisory_suppressed_by_tag").copy()
        old_last_summary = kw._TELEMETRY_STATE.get("last_advisory_summary_ts", 0.0)
        try:
            kw.AION_LOG_MODE = "execution"
            kw.AION_DEBUG_TELEMETRY = False
            kw._TELEMETRY_STATE["last_advisory_summary_ts"] = time.time()
            before = int(kw._TELEMETRY_STATE.get("advisory_suppressed_total", 0) or 0)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(
                    kw._should_emit_module_log(
                        '[AION-CRYPTO-SUBMIT-ATTEMPT] {"ticker":"KXBTC-TEST","reason":"unit_test"}'
                    )
                )
                self.assertTrue(
                    kw._should_emit_module_log(
                        "[LIVE-SELECT-DECISION] final_trade_path chosen_ticker=KXBTC-TEST family=KXBTC"
                    )
                )
                self.assertFalse(
                    kw._should_emit_module_log(
                        "[LIVE-SELECT-DECISION] top_decision_trace=[]"
                    )
                )
                self.assertFalse(
                    kw._should_emit_module_log(
                        '[AION-SELECTED-PRICE-RESOLVED] {"ticker":"KXBTC-TEST","selected_price_dollars":0.42}'
                    )
                )
                self.assertFalse(
                    kw._should_emit_module_log(
                        "[AION-FINAL-BLOCKER-TRACE] ticker=KXBTC-TEST final_block_reason=unsafe_price"
                    )
                )
                self.assertFalse(
                    kw._should_emit_module_log(
                        '[LIVE-REACTIVATION-WATCHLIST] {"ticker":"KXBTC-TEST","state":"unchanged"}'
                    )
                )
                self.assertFalse(kw._should_emit_module_log("operator dashboard snapshot"))
            self.assertGreaterEqual(
                int(kw._TELEMETRY_STATE.get("advisory_suppressed_total", 0) or 0),
                before + 1,
            )
        finally:
            kw.AION_LOG_MODE = old_mode
            kw.AION_DEBUG_TELEMETRY = old_debug
            kw._TELEMETRY_STATE["advisory_suppressed_total"] = old_total
            kw._TELEMETRY_STATE["advisory_suppressed_by_tag"].clear()
            kw._TELEMETRY_STATE["advisory_suppressed_by_tag"].update(old_counts)
            kw._TELEMETRY_STATE["last_advisory_summary_ts"] = old_last_summary

    def test_cycle_execution_summary_tracks_execution_truth(self):
        old_mode = kw.AION_LOG_MODE
        old_debug = kw.AION_DEBUG_TELEMETRY
        old_cycle_state = {
            key: value.copy() if hasattr(value, "copy") else value
            for key, value in kw._AION_EXECUTION_CYCLE_STATE.items()
        }
        old_total = kw._TELEMETRY_STATE.get("advisory_suppressed_total", 0)
        old_counts = kw._TELEMETRY_STATE.get("advisory_suppressed_by_tag").copy()
        old_last_summary = kw._TELEMETRY_STATE.get("last_advisory_summary_ts", 0.0)
        try:
            kw.AION_LOG_MODE = "execution"
            kw.AION_DEBUG_TELEMETRY = False
            kw._aion_reset_cycle_execution_summary(None)
            kw._TELEMETRY_STATE["advisory_suppressed_total"] = 0
            kw._TELEMETRY_STATE["advisory_suppressed_by_tag"].clear()
            kw._TELEMETRY_STATE["last_advisory_summary_ts"] = time.time()

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                kw.print('[AION-RUNONCE-START] {"cycle_count":101,"timestamp":1779694300.0}')
                kw.print('[CRYPTO-PROB-CALIBRATION] {"ticker":"KXBTC-TEST"}')
                kw.print('[CRYPTO-QUALIFIED] {"ticker":"KXBTC-TEST"}')
                kw.print('[SPORTS-CONVICTION-SCORE] {"ticker":"KXNCAABSPREAD-PURMICH"}')
                kw.print('[AION-SPORTS-SUBMIT-ATTEMPT] {"ticker":"KXNCAABSPREAD-PURMICH","reason":"unit_test"}')
                kw.print('[AION-CRYPTO-SUBMIT-ATTEMPT] {"ticker":"KXBTC-TEST","reason":"unit_test"}')
                kw.print('[POSITION-OPEN] {"ticker":"KXBTC-TEST"}')
                kw.print('[AION-FINAL-SUBMIT-BLOCKED] {"ticker":"KXETH-TEST","reason":"duplicate_guard"}')
                kw.print("[AION-FINAL-BLOCKER-TRACE] ticker=KXBTC-TRACE final_block_reason=unsafe_price")
                kw.print('[LIVE-REACTIVATION-WATCHLIST] {"ticker":"KXBTC-TEST","state":"unchanged"}')
                kw.print('[AION-RUNONCE-START] {"cycle_count":102,"timestamp":1779694360.0}')

            rendered = out.getvalue()
            self.assertIn("[AION-CYCLE-EXECUTION-SUMMARY]", rendered)
            self.assertIn('"crypto_candidates_scanned": 1', rendered)
            self.assertIn('"sports_candidates_scanned": 1', rendered)
            self.assertIn('"crypto_qualified_count": 1', rendered)
            self.assertIn('"sports_qualified_count": 1', rendered)
            self.assertIn('"positions_opened": 1', rendered)
            self.assertIn('"duplicate_guard": 1', rendered)
            self.assertIn('"unsafe_price": 1', rendered)
            self.assertIn('"advisory_logs_suppressed": 1', rendered)
            self.assertNotIn("[AION-FINAL-BLOCKER-TRACE]", rendered)
            self.assertNotIn("[LIVE-REACTIVATION-WATCHLIST]", rendered)
        finally:
            kw.AION_LOG_MODE = old_mode
            kw.AION_DEBUG_TELEMETRY = old_debug
            kw._AION_EXECUTION_CYCLE_STATE.clear()
            kw._AION_EXECUTION_CYCLE_STATE.update(old_cycle_state)
            kw._TELEMETRY_STATE["advisory_suppressed_total"] = old_total
            kw._TELEMETRY_STATE["advisory_suppressed_by_tag"].clear()
            kw._TELEMETRY_STATE["advisory_suppressed_by_tag"].update(old_counts)
            kw._TELEMETRY_STATE["last_advisory_summary_ts"] = old_last_summary

    def test_crypto_side_selection_can_choose_yes_when_yes_edge_is_better(self):
        bot = self._make_bot()
        decision = bot._aion_select_bidirectional_side(
            ticker="KXBTC-26MAY2505-B79150",
            family="KXBTC",
            lane="CRYPTO_LADDER",
            market={"ticker": "KXBTC-26MAY2505-B79150", "title": "Bitcoin range"},
            yes_bid=0.39,
            yes_ask=0.40,
            no_bid=0.58,
            no_ask=0.61,
            yes_top_size=120,
            no_top_size=120,
            quote_diag={"yes_edge_after_cost": 0.05, "no_edge_after_cost": 0.01},
            source="unit_test",
        )
        self.assertEqual(decision["selected_side"], "yes")
        self.assertEqual(decision["selected_price"], 0.40)
        self.assertGreater(decision["yes_final_score"], decision["no_final_score"])

    def test_crypto_side_selection_can_choose_no_when_no_edge_is_better(self):
        bot = self._make_bot()
        decision = bot._aion_select_bidirectional_side(
            ticker="KXETH-26MAY2505-B2130",
            family="KXETH",
            lane="CRYPTO_LADDER",
            market={"ticker": "KXETH-26MAY2505-B2130", "title": "Ethereum range"},
            yes_bid=0.54,
            yes_ask=0.57,
            no_bid=0.41,
            no_ask=0.44,
            yes_top_size=80,
            no_top_size=90,
            quote_diag={"yes_edge_after_cost": -0.01, "no_edge_after_cost": 0.06},
            source="unit_test",
        )
        self.assertEqual(decision["selected_side"], "no")
        self.assertEqual(decision["selected_price"], 0.44)
        self.assertGreater(decision["no_final_score"], decision["yes_final_score"])

    def test_btc15m_down_momentum_can_select_no_on_up_contract(self):
        bot = self._make_bot()
        decision = bot._aion_select_bidirectional_side(
            ticker="KXBTC15M-26MAY250715-UP",
            family="KXBTC15M",
            lane="CRYPTO_LADDER",
            market={"ticker": "KXBTC15M-26MAY250715-UP", "title": "Bitcoin up in 15 minutes"},
            yes_bid=0.49,
            yes_ask=0.52,
            no_bid=0.46,
            no_ask=0.49,
            yes_top_size=50,
            no_top_size=50,
            quote_diag={
                "yes_edge_after_cost": 0.002,
                "no_edge_after_cost": 0.004,
                "momentum_direction": "down",
                "momentum_strength": 0.90,
            },
            preferred_side="yes",
            source="unit_test",
        )
        self.assertEqual(decision["selected_side"], "no")
        self.assertEqual(decision["reason"], "switched_to_no_better_score")

    def test_sports_side_selection_can_choose_yes_when_yes_edge_is_better(self):
        bot = self._make_bot()
        decision = bot._aion_select_bidirectional_side(
            ticker="KXNCAABSPREAD-PURMICH",
            family="KXNCAABSPREAD",
            lane="SPORTS",
            market={"ticker": "KXNCAABSPREAD-PURMICH", "title": "Purdue vs Michigan spread"},
            yes_bid=0.45,
            yes_ask=0.48,
            no_bid=0.49,
            no_ask=0.52,
            yes_top_size=30,
            no_top_size=30,
            quote_diag={"yes_edge_after_cost": 0.04, "no_edge_after_cost": -0.01},
            source="unit_test",
        )
        self.assertEqual(decision["selected_side"], "yes")
        self.assertEqual(decision["selected_price"], 0.48)

    def test_sports_side_selection_can_choose_no_when_no_edge_is_better(self):
        bot = self._make_bot()
        decision = bot._aion_select_bidirectional_side(
            ticker="KXNCAABTOTAL-PURMICH",
            family="KXNCAABTOTAL",
            lane="SPORTS",
            market={"ticker": "KXNCAABTOTAL-PURMICH", "title": "Purdue vs Michigan total"},
            yes_bid=0.60,
            yes_ask=0.63,
            no_bid=0.34,
            no_ask=0.37,
            yes_top_size=25,
            no_top_size=25,
            quote_diag={"yes_edge_after_cost": -0.02, "no_edge_after_cost": 0.05},
            source="unit_test",
        )
        self.assertEqual(decision["selected_side"], "no")
        self.assertEqual(decision["selected_price"], 0.37)

    def test_selected_side_is_preserved_into_order_payload(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNCAABTOTAL-PURMICH",
            "title": "Purdue vs Michigan total",
            "yes_bid": 0.60,
            "yes_ask": 0.63,
            "no_bid": 0.34,
            "no_ask": 0.37,
            "yes_top_size": 25,
            "no_top_size": 25,
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            title=market["title"],
            yes_bid=0.60,
            yes_ask=0.63,
            no_bid=0.34,
            no_ask=0.37,
            yes_top_size=25,
            no_top_size=25,
            liquidity=50,
        )
        quote_diag = {"yes_edge_after_cost": -0.02, "no_edge_after_cost": 0.05}
        resolution = bot.resolve_aion_orderbook_for_payload(
            market["ticker"],
            candidate=q,
            market=market,
            quote_diag=quote_diag,
            price_source="ask",
            selection_stage="submit_unit_test",
            allow_refresh=False,
        )
        self.assertEqual(resolution["selected_side"], "no")
        self.assertEqual(resolution["selected_price"], 0.37)
        payload = bot.build_live_order_payload(
            q,
            size=1,
            market=market,
            quote_diag=quote_diag,
            orderbook_resolution=resolution,
            allow_orderbook_refresh=False,
        )
        self.assertEqual(payload["side"], "no")
        self.assertEqual(payload["selected_side"], "no")
        self.assertEqual(payload["selected_price"], 0.37)
        self.assertEqual(payload["no_price"], 37)
        self.assertNotIn("yes_price", payload)

    def test_missing_no_quote_blocks_two_sided_selection(self):
        bot = self._make_bot()
        decision = bot._aion_select_bidirectional_side(
            ticker="KXBTC-TEST",
            family="KXBTC",
            lane="CRYPTO_LADDER",
            market={"ticker": "KXBTC-TEST"},
            yes_bid=None,
            yes_ask=0.52,
            no_bid=None,
            no_ask=None,
            yes_top_size=10,
            no_top_size=10,
            quote_diag={"yes_edge_after_cost": 0.05, "no_edge_after_cost": 0.05},
            source="unit_test",
        )
        self.assertIsNone(decision["selected_side"])
        self.assertEqual(decision["reason"], "missing_two_sided_quote")

    def test_missing_yes_quote_blocks_two_sided_selection(self):
        bot = self._make_bot()
        decision = bot._aion_select_bidirectional_side(
            ticker="KXETH-TEST",
            family="KXETH",
            lane="CRYPTO_LADDER",
            market={"ticker": "KXETH-TEST"},
            yes_bid=None,
            yes_ask=None,
            no_bid=None,
            no_ask=0.48,
            yes_top_size=10,
            no_top_size=10,
            quote_diag={"yes_edge_after_cost": 0.05, "no_edge_after_cost": 0.05},
            source="unit_test",
        )
        self.assertIsNone(decision["selected_side"])
        self.assertEqual(decision["reason"], "missing_two_sided_quote")

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
        self._set_sports_only_mode(False)
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
        self._set_sports_only_mode(False)
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
        self._set_sports_only_mode(False)
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

    def test_sports_only_mode_blocks_crypto_allocation_and_keeps_sports(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        crypto_q = SimpleNamespace(
            ticker="KXBTC15M-26MAY250715-UP",
            selected_side="yes",
            selected_price=0.34,
            yes_bid=0.33,
            yes_ask=0.34,
            no_bid=0.66,
            no_ask=0.67,
            liquidity=220,
        )
        sports_q = SimpleNamespace(
            ticker="KXNCAABSPREAD-PURMICH",
            selected_side="yes",
            selected_price=0.46,
            yes_bid=0.44,
            yes_ask=0.46,
            no_bid=0.54,
            no_ask=0.56,
            liquidity=180,
        )
        markets = [
            {"ticker": crypto_q.ticker, "series_ticker": "KXBTC15M", "title": "Bitcoin up in 15 minutes"},
            {"ticker": sports_q.ticker, "series_ticker": "KXNCAABSPREAD", "title": "Purdue vs Michigan spread"},
        ]
        expectancy = {
            crypto_q.ticker: {
                "raw_score": 95.0,
                "adjusted_score": 95.0,
                "q": crypto_q,
                "spread": 0.01,
                "family": "KXBTC15M",
                "expectancy_summary": {"final_edge": 0.030, "selected_side": "yes", "selected_price": 0.34},
                "quality_snapshot": {"selected_price": 0.34, "liquidity_depth": 220, "spread": 0.01},
                "ev_context": {"ev_edge": 0.030, "crypto_momentum_score": 90.0},
            },
            sports_q.ticker: {
                "raw_score": 72.0,
                "adjusted_score": 72.0,
                "q": sports_q,
                "spread": 0.02,
                "family": "KXNCAABSPREAD",
                "expectancy_summary": {
                    "final_edge": 0.020,
                    "sports_edge_after_cost": 0.020,
                    "selected_side": "yes",
                    "selected_price": 0.46,
                    "expectancy_score": 0.75,
                },
                "quality_snapshot": {"selected_price": 0.46, "liquidity_depth": 180, "spread": 0.02},
                "sports_edge_context": {"boost": 4.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.020},
            },
        }

        ranked = bot._apply_dual_lane_allocation_ranking(
            [(95.0, crypto_q, 0.01, "KXBTC15M"), (72.0, sports_q, 0.02, "KXNCAABSPREAD")],
            markets,
            expectancy_summary_by_ticker=expectancy,
            source="unit_test_sports_only",
        )

        self.assertEqual(ranked[0][1].ticker, sports_q.ticker)
        self.assertEqual(expectancy[crypto_q.ticker]["lane_allocation"]["decision"], "SPORTS_ONLY_CRYPTO_BLOCK")
        self.assertEqual(expectancy[crypto_q.ticker]["unified_opportunity_score"], 0.0)
        self.assertGreater(expectancy[sports_q.ticker]["lane_allocation"]["unified_opportunity_score"], 0.0)

    def test_sports_only_mode_blocks_crypto_payload_before_sizing(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXETH15M-26MAY250715-UP",
            selected_side="yes",
            selected_price=0.28,
            yes_bid=0.27,
            yes_ask=0.28,
            no_bid=0.72,
            no_ask=0.73,
            liquidity=120,
        )
        bot.resolve_aion_orderbook_for_payload = lambda *args, **kwargs: self.fail("crypto payload should block before orderbook/sizing")
        bot._resolve_dynamic_live_entry_size = lambda *args, **kwargs: self.fail("crypto payload should block before sizing")

        payload = bot.build_live_order_payload(
            q,
            market={"ticker": q.ticker, "series_ticker": "KXETH15M", "title": "Ethereum up in 15 minutes"},
            selected_ticker=q.ticker,
            selection_stage="unit_test_sports_only",
        )

        self.assertIsNone(payload)

    def test_sports_only_discovery_filters_crypto_and_non_sports(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        markets = [
            {"ticker": "KXBTC15M-26MAY250715-UP", "series_ticker": "KXBTC15M", "title": "Bitcoin up"},
            {"ticker": "KXTEMPCHI-26MAY-HIGH", "series_ticker": "KXTEMP", "title": "Chicago temperature"},
            {"ticker": "KXNCAABSPREAD-PURMICH", "series_ticker": "KXNCAABSPREAD", "title": "Purdue vs Michigan spread"},
            {"ticker": "KXMLBMONEYLINE-NYYBOS", "series_ticker": "KXMLBMONEYLINE", "title": "Yankees vs Red Sox"},
        ]

        filtered = bot._filter_sports_only_markets(markets, source="unit_test_discovery", force_log=True)

        self.assertEqual([market["ticker"] for market in filtered], ["KXNCAABSPREAD-PURMICH", "KXMLBMONEYLINE-NYYBOS"])

    def test_sports_only_btc_cycle_discovery_is_disabled(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        bot._fetch_btc1h_cycle_markets = lambda *args, **kwargs: self.fail("sports-only mode should not fetch crypto cycle markets")

        summary = bot._run_btc1h_adaptive_cycle(
            markets=[{"ticker": "KXBTC1H-CYCLE-70000", "series_ticker": "KXBTC1H", "title": "Bitcoin above $70,000"}],
            source="unit_test_sports_only",
            force=True,
        )

        self.assertFalse(summary["enabled"])
        self.assertEqual(summary["markets_to_merge"], [])
        self.assertEqual(summary["reason"], "sports_only_mode_crypto_discovery_disabled")

    def test_sports_only_final_ranking_purges_crypto_and_weather(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        crypto_q = SimpleNamespace(ticker="KXBTC15M-26MAY250715-UP", liquidity=220, volume=1000)
        weather_q = SimpleNamespace(ticker="KXTEMPCHI-26MAY-HIGH", liquidity=180, volume=800)
        sports_q = SimpleNamespace(ticker="KXNCAABSPREAD-PURMICH", liquidity=160, volume=700)
        markets = [
            {"ticker": crypto_q.ticker, "series_ticker": "KXBTC15M", "title": "Bitcoin up"},
            {"ticker": weather_q.ticker, "series_ticker": "KXTEMP", "title": "Chicago high temperature"},
            {"ticker": sports_q.ticker, "series_ticker": "KXNCAABSPREAD", "title": "Purdue vs Michigan spread"},
        ]

        ranked = bot.rank_final_candidates(
            [
                (99.0, crypto_q, 0.01, "KXBTC15M"),
                (98.0, weather_q, 0.01, "KXTEMP"),
                (72.0, sports_q, 0.02, "KXNCAABSPREAD"),
            ],
            markets=markets,
        )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][1].ticker, sports_q.ticker)

    def test_sports_only_dynamic_sizing_blocks_crypto_and_non_sports(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        crypto_q = SimpleNamespace(ticker="KXETH15M-26MAY250715-UP", family="KXETH15M", selected_side="yes", selected_price=0.28)
        weather_q = SimpleNamespace(ticker="KXTEMPCHI-26MAY-HIGH", family="KXTEMP", selected_side="yes", selected_price=0.42)

        crypto_size = bot._resolve_dynamic_live_entry_size(
            crypto_q,
            explicit_size=5,
            payload={"ticker": crypto_q.ticker, "family": crypto_q.family, "count": 5},
            ev_context={"ticker": crypto_q.ticker, "family": crypto_q.family, "ev_tier": "S"},
            source="unit_test_sports_only_sizing",
        )
        weather_size = bot._resolve_dynamic_live_entry_size(
            weather_q,
            explicit_size=5,
            payload={"ticker": weather_q.ticker, "family": weather_q.family, "count": 5},
            ev_context={"ticker": weather_q.ticker, "family": weather_q.family, "ev_tier": "S"},
            source="unit_test_sports_only_sizing",
        )

        self.assertEqual(crypto_size, 0)
        self.assertEqual(weather_size, 0)

    def test_sports_only_submit_live_order_blocks_non_sports(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(ticker="KXTEMPCHI-26MAY-HIGH", family="KXTEMP", selected_side="yes", selected_price=0.42)

        result = bot.submit_live_order(
            q,
            payload_override={"ticker": q.ticker, "family": q.family, "side": "yes", "price": 0.42, "count": 1},
        )

        self.assertFalse(result["submitted"])
        self.assertIn(result["reason"], {"sports_only_weather_execution_disabled", "sports_only_non_sports_execution_disabled"})

    def test_sports_only_submit_live_order_allows_sports_yes_and_no(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        captured_payloads = []
        bot.recent_submitted_order_for_ticker = lambda ticker: None
        bot.recent_submitted_order_count_for_family = lambda family: 0
        bot.recent_submitted_order_count_total = lambda: 0
        bot.get_live_submit_authorization = lambda payload: {"ok": True}
        bot.can_build_live_auth = lambda: True
        bot._reset_live_trade_counter_if_needed = lambda: None
        bot._active_live_order_timestamps = lambda: []
        bot._live_order_timestamps = []
        bot.already_holding = lambda ticker: False
        bot.extract_order_execution_summary = lambda response: {"order_id": response.get("order_id")}
        bot.persist_live_entry_metadata = lambda **kwargs: None
        bot.append_live_audit_log = lambda *args, **kwargs: None
        bot.append_aion_learning_archive_event = lambda *args, **kwargs: None

        def fake_place_live_order(payload):
            captured_payloads.append(dict(payload))
            return {
                "submitted": True,
                "reason": "submitted",
                "endpoint": "/trade-api/v2/portfolio/orders",
                "payload": dict(payload),
                "response": {"order_id": f"unit-{len(captured_payloads)}"},
                "status_code": 201,
            }

        bot.place_live_order = fake_place_live_order

        for side, price in (("yes", 0.48), ("no", 0.37)):
            q = SimpleNamespace(
                ticker=f"KXNCAABTOTAL-PURMICH-{side.upper()}",
                family="KXNCAABTOTAL",
                selected_side=side,
                selected_price=price,
                yes_bid=0.45,
                yes_ask=0.48,
                no_bid=0.36,
                no_ask=0.37,
                liquidity=100,
                sports_memory_context={"regime": "hot", "reason": "unit_test_memory"},
                sports_memory_dimensions={"setup_type": "total", "period": "full_game"},
                sports_memory_rank_adjustment=2.1,
                sports_memory_size_multiplier=1.08,
                sports_memory_regime="hot",
            )

            result = bot.submit_live_order(
                q,
                size=2,
                lane="SPORTS",
                payload_override={
                    "ticker": q.ticker,
                    "family": q.family,
                    "side": side,
                    "selected_side": side,
                    "selected_price": price,
                    "price": price,
                    "count": 2,
                },
            )

            self.assertTrue(result["submitted"])

        self.assertEqual([payload["side"] for payload in captured_payloads], ["yes", "no"])
        self.assertEqual(captured_payloads[0]["entry_context"]["sports_memory_regime"], "hot")
        self.assertEqual(captured_payloads[0]["entry_context"]["sports_memory_size_multiplier"], 1.08)

    def test_game_thesis_boosts_aligned_sports_candidates(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        total_1h = SimpleNamespace(
            ticker="KXNBA1HTOTAL-OKCSAS-1135",
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.46,
            yes_bid=0.44,
            yes_ask=0.46,
            no_bid=0.54,
            no_ask=0.56,
            liquidity=220,
        )
        total_full = SimpleNamespace(
            ticker="KXNBATOTAL-OKCSAS-2245",
            family="KXNBATOTAL",
            selected_side="yes",
            selected_price=0.48,
            yes_bid=0.46,
            yes_ask=0.48,
            no_bid=0.52,
            no_ask=0.54,
            liquidity=210,
        )
        unrelated = SimpleNamespace(
            ticker="KXMLBMONEYLINE-NYYBOS-NYY",
            family="KXMLBMONEYLINE",
            selected_side="yes",
            selected_price=0.50,
            yes_bid=0.48,
            yes_ask=0.50,
            no_bid=0.50,
            no_ask=0.52,
            liquidity=180,
        )
        markets = [
            {
                "ticker": total_1h.ticker,
                "series_ticker": "KXNBA1HTOTAL",
                "event_ticker": "KXNBA-OKCSAS",
                "title": "NBA Thunder vs Spurs 1H total over 113.5",
            },
            {
                "ticker": total_full.ticker,
                "series_ticker": "KXNBATOTAL",
                "event_ticker": "KXNBA-OKCSAS",
                "title": "NBA Thunder vs Spurs game total over 224.5",
            },
            {
                "ticker": unrelated.ticker,
                "series_ticker": "KXMLBMONEYLINE",
                "event_ticker": "KXMLB-NYYBOS",
                "title": "MLB Yankees vs Red Sox moneyline",
            },
        ]
        expectancy = {
            total_1h.ticker: {
                "expectancy_summary": {"final_edge": 0.038, "selected_side": "yes", "selected_price": 0.46, "expectancy_score": 0.92},
                "quality_snapshot": {"selected_price": 0.46, "liquidity_depth": 220, "spread": 0.02},
                "sports_edge_context": {"boost": 6.0, "tags": ["SPORTS_GOOD_BOOK", "STRUCTURED_LADDER"]},
                "ev_context": {"ev_edge": 0.038},
            },
            total_full.ticker: {
                "expectancy_summary": {"final_edge": 0.036, "selected_side": "yes", "selected_price": 0.48, "expectancy_score": 0.90},
                "quality_snapshot": {"selected_price": 0.48, "liquidity_depth": 210, "spread": 0.02},
                "sports_edge_context": {"boost": 5.5, "tags": ["SPORTS_GOOD_BOOK", "STRUCTURED_LADDER"]},
                "ev_context": {"ev_edge": 0.036},
            },
            unrelated.ticker: {
                "expectancy_summary": {"final_edge": 0.024, "selected_side": "yes", "selected_price": 0.50, "expectancy_score": 0.74},
                "quality_snapshot": {"selected_price": 0.50, "liquidity_depth": 180, "spread": 0.02},
                "sports_edge_context": {"boost": 2.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.024},
            },
        }
        for ticker, item in expectancy.items():
            item["q"] = {total_1h.ticker: total_1h, total_full.ticker: total_full, unrelated.ticker: unrelated}[ticker]
            item["family"] = getattr(item["q"], "family")
            item["spread"] = 0.02
            item["raw_score"] = 76.0 if ticker != unrelated.ticker else 78.0
            item["adjusted_score"] = item["raw_score"]

        with contextlib.redirect_stdout(io.StringIO()):
            ranked = bot._apply_dual_lane_allocation_ranking(
                [
                    (76.0, total_1h, 0.02, "KXNBA1HTOTAL"),
                    (76.0, total_full, 0.02, "KXNBATOTAL"),
                    (78.0, unrelated, 0.02, "KXMLBMONEYLINE"),
                ],
                markets,
                expectancy_summary_by_ticker=expectancy,
                source="unit_test_game_thesis",
            )

        boosted = expectancy[total_1h.ticker]["lane_allocation"]
        self.assertEqual(ranked[0][1].ticker, total_1h.ticker)
        self.assertEqual(boosted["game_thesis_type"], "high_scoring_environment")
        self.assertGreaterEqual(boosted["game_thesis_confidence"], kw.AION_GAME_THESIS_STRONG_SCORE)
        self.assertGreater(boosted["game_thesis_rank_boost"], 0.0)
        self.assertGreater(boosted["game_thesis_size_multiplier"], 1.0)
        self.assertIn(total_full.ticker, boosted["game_related_candidates"])
        self.assertEqual(getattr(total_1h, "game_thesis_type"), "high_scoring_environment")

    def test_game_thesis_weak_game_does_not_block_sports_candidate(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBMONEYLINE-NYYBOS-NYY",
            family="KXMLBMONEYLINE",
            selected_side="yes",
            selected_price=0.49,
            yes_bid=0.47,
            yes_ask=0.49,
            no_bid=0.51,
            no_ask=0.53,
            liquidity=110,
        )
        expectancy = {
            q.ticker: {
                "q": q,
                "family": q.family,
                "spread": 0.02,
                "raw_score": 70.0,
                "adjusted_score": 70.0,
                "expectancy_summary": {"final_edge": 0.018, "selected_side": "yes", "selected_price": 0.49, "expectancy_score": 0.70},
                "quality_snapshot": {"selected_price": 0.49, "liquidity_depth": 110, "spread": 0.02},
                "sports_edge_context": {"boost": 2.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.018},
            }
        }

        with contextlib.redirect_stdout(io.StringIO()):
            ranked = bot._apply_dual_lane_allocation_ranking(
                [(70.0, q, 0.02, q.family)],
                [{"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXMLB-NYYBOS", "title": "MLB Yankees vs Red Sox moneyline"}],
                expectancy_summary_by_ticker=expectancy,
                source="unit_test_game_thesis_weak",
            )

        allocation = expectancy[q.ticker]["lane_allocation"]
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][1].ticker, q.ticker)
        self.assertEqual(allocation["lane"], "SPORTS")
        self.assertNotEqual(allocation.get("final_quality_action"), "skip")
        self.assertEqual(allocation.get("game_thesis_rank_boost"), 0.0)

    def test_game_thesis_ignores_crypto_and_non_sports_candidates(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        crypto_q = SimpleNamespace(ticker="KXBTC15M-26MAY250715-UP", family="KXBTC15M", selected_side="yes", selected_price=0.40, liquidity=500)
        sports_q = SimpleNamespace(ticker="KXNCAABSPREAD-PURMICH", family="KXNCAABSPREAD", selected_side="yes", selected_price=0.52, liquidity=120)
        expectancy = {
            crypto_q.ticker: {"q": crypto_q, "family": crypto_q.family, "spread": 0.01, "raw_score": 98.0, "adjusted_score": 98.0},
            sports_q.ticker: {
                "q": sports_q,
                "family": sports_q.family,
                "spread": 0.02,
                "raw_score": 72.0,
                "adjusted_score": 72.0,
                "expectancy_summary": {"final_edge": 0.020, "selected_side": "yes", "selected_price": 0.52, "expectancy_score": 0.72},
                "quality_snapshot": {"selected_price": 0.52, "liquidity_depth": 120, "spread": 0.02},
                "sports_edge_context": {"boost": 2.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.020},
            },
        }

        with contextlib.redirect_stdout(io.StringIO()):
            ranked = bot._apply_dual_lane_allocation_ranking(
                [(98.0, crypto_q, 0.01, crypto_q.family), (72.0, sports_q, 0.02, sports_q.family)],
                [
                    {"ticker": crypto_q.ticker, "series_ticker": crypto_q.family, "title": "Bitcoin up in 15 minutes"},
                    {"ticker": sports_q.ticker, "series_ticker": sports_q.family, "event_ticker": "KXNCAAB-PURMICH", "title": "Purdue vs Michigan spread"},
                ],
                expectancy_summary_by_ticker=expectancy,
                source="unit_test_game_thesis_sports_only",
            )

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][1].ticker, sports_q.ticker)
        self.assertEqual(expectancy[crypto_q.ticker]["lane_allocation"]["decision"], "SPORTS_ONLY_CRYPTO_BLOCK")
        self.assertNotIn("game_thesis_context", expectancy[crypto_q.ticker]["lane_allocation"])

    def test_sports_graph_groups_same_game_tags_lag_and_boosts_related_market(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        first_half = SimpleNamespace(
            ticker="KXNBA1HTOTAL-OKCSAS-1135",
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.45,
            yes_bid=0.43,
            yes_ask=0.45,
            no_bid=0.55,
            no_ask=0.57,
            liquidity=220,
        )
        full_game = SimpleNamespace(
            ticker="KXNBATOTAL-OKCSAS-2245",
            family="KXNBATOTAL",
            selected_side="yes",
            selected_price=0.48,
            yes_bid=0.46,
            yes_ask=0.48,
            no_bid=0.52,
            no_ask=0.54,
            liquidity=190,
        )
        markets = [
            {"ticker": first_half.ticker, "series_ticker": first_half.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA Thunder vs Spurs 1H total over 113.5"},
            {"ticker": full_game.ticker, "series_ticker": full_game.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA Thunder vs Spurs game total over 224.5"},
        ]
        expectancy = {
            first_half.ticker: {
                "q": first_half,
                "family": first_half.family,
                "spread": 0.02,
                "raw_score": 88.0,
                "adjusted_score": 88.0,
                "expectancy_summary": {"final_edge": 0.040, "selected_side": "yes", "selected_price": 0.45, "expectancy_score": 0.94},
                "quality_snapshot": {"selected_price": 0.45, "liquidity_depth": 220, "spread": 0.02},
                "sports_edge_context": {"boost": 6.0, "tags": ["SPORTS_GOOD_BOOK", "STRUCTURED_LADDER"]},
                "ev_context": {"ev_edge": 0.040},
            },
            full_game.ticker: {
                "q": full_game,
                "family": full_game.family,
                "spread": 0.02,
                "raw_score": 66.0,
                "adjusted_score": 66.0,
                "expectancy_summary": {"final_edge": 0.026, "selected_side": "yes", "selected_price": 0.48, "expectancy_score": 0.76},
                "quality_snapshot": {"selected_price": 0.48, "liquidity_depth": 190, "spread": 0.02},
                "sports_edge_context": {"boost": 3.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.026},
            },
        }

        with contextlib.redirect_stdout(io.StringIO()):
            ranked = bot._apply_dual_lane_allocation_ranking(
                [(88.0, first_half, 0.02, first_half.family), (66.0, full_game, 0.02, full_game.family)],
                markets,
                expectancy_summary_by_ticker=expectancy,
                source="unit_test_sports_graph_lag",
            )

        full_allocation = expectancy[full_game.ticker]["lane_allocation"]
        self.assertEqual(len(ranked), 2)
        self.assertEqual(full_allocation["sports_graph_game_id"], "nba:kxnba_okcsas")
        self.assertGreater(full_allocation["sports_graph_rank_adjustment"], 0.0)
        self.assertTrue(full_allocation["sports_graph_lag_detected"])
        self.assertIn("high_scoring", full_allocation["sports_graph_direction"])
        self.assertTrue(
            {"1H-to-full-game continuation", "scoring-aligned", "pace-aligned"}.intersection(
                set(full_allocation["sports_graph_relationship_types"])
            )
        )
        self.assertGreaterEqual(getattr(full_game, "sports_graph_size_multiplier"), 1.0)

    def test_sports_graph_conflict_deprioritizes_without_blocking_candidate(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        over_q = SimpleNamespace(ticker="KXNBATOTAL-OKCSAS-2245-O", family="KXNBATOTAL", selected_side="yes", selected_price=0.49, liquidity=150)
        under_q = SimpleNamespace(ticker="KXNBATOTAL-OKCSAS-2245-U", family="KXNBATOTAL", selected_side="yes", selected_price=0.48, liquidity=150)
        markets = [
            {"ticker": over_q.ticker, "series_ticker": over_q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA Thunder vs Spurs game total over 224.5"},
            {"ticker": under_q.ticker, "series_ticker": under_q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA Thunder vs Spurs game total under 224.5"},
        ]
        expectancy = {}
        for q, raw_score, edge in ((over_q, 72.0, 0.024), (under_q, 70.0, 0.023)):
            expectancy[q.ticker] = {
                "q": q,
                "family": q.family,
                "spread": 0.02,
                "raw_score": raw_score,
                "adjusted_score": raw_score,
                "expectancy_summary": {"final_edge": edge, "selected_side": "yes", "selected_price": q.selected_price, "expectancy_score": 0.76},
                "quality_snapshot": {"selected_price": q.selected_price, "liquidity_depth": q.liquidity, "spread": 0.02},
                "sports_edge_context": {"boost": 2.5, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": edge},
            }

        with contextlib.redirect_stdout(io.StringIO()):
            ranked = bot._apply_dual_lane_allocation_ranking(
                [(72.0, over_q, 0.02, over_q.family), (70.0, under_q, 0.02, under_q.family)],
                markets,
                expectancy_summary_by_ticker=expectancy,
                source="unit_test_sports_graph_conflict",
            )

        under_allocation = expectancy[under_q.ticker]["lane_allocation"]
        self.assertEqual(len(ranked), 2)
        self.assertGreater(under_allocation["sports_graph_conflict_count"], 0)
        self.assertLess(under_allocation["sports_graph_rank_adjustment"], 0.0)
        self.assertNotEqual(under_allocation.get("final_quality_action"), "skip")
        self.assertIn(under_q.ticker, [row[1].ticker for row in ranked])

    def test_sports_graph_ignores_crypto_and_non_sports_candidates(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        crypto_q = SimpleNamespace(ticker="KXBTC15M-26MAY250715-UP", family="KXBTC15M", selected_side="yes", selected_price=0.40, liquidity=500)
        sports_q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-1135", family="KXNBA1HTOTAL", selected_side="yes", selected_price=0.46, liquidity=130)
        macro_q = SimpleNamespace(ticker="KXFEDDECISION-26MAY-CUT", family="KXFEDDECISION", selected_side="yes", selected_price=0.35, liquidity=200)
        expectancy = {
            crypto_q.ticker: {"q": crypto_q, "family": crypto_q.family, "spread": 0.01, "raw_score": 99.0, "adjusted_score": 99.0},
            macro_q.ticker: {"q": macro_q, "family": macro_q.family, "spread": 0.01, "raw_score": 95.0, "adjusted_score": 95.0},
            sports_q.ticker: {
                "q": sports_q,
                "family": sports_q.family,
                "spread": 0.02,
                "raw_score": 74.0,
                "adjusted_score": 74.0,
                "expectancy_summary": {"final_edge": 0.028, "selected_side": "yes", "selected_price": 0.46, "expectancy_score": 0.78},
                "quality_snapshot": {"selected_price": 0.46, "liquidity_depth": 130, "spread": 0.02},
                "sports_edge_context": {"boost": 3.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.028},
            },
        }

        with contextlib.redirect_stdout(io.StringIO()):
            ranked = bot._apply_dual_lane_allocation_ranking(
                [
                    (99.0, crypto_q, 0.01, crypto_q.family),
                    (95.0, macro_q, 0.01, macro_q.family),
                    (74.0, sports_q, 0.02, sports_q.family),
                ],
                [
                    {"ticker": crypto_q.ticker, "series_ticker": crypto_q.family, "title": "Bitcoin up in 15 minutes"},
                    {"ticker": macro_q.ticker, "series_ticker": macro_q.family, "title": "Fed decision"},
                    {"ticker": sports_q.ticker, "series_ticker": sports_q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA Thunder vs Spurs 1H total over 113.5"},
                ],
                expectancy_summary_by_ticker=expectancy,
                source="unit_test_sports_graph_sports_only",
            )

        self.assertEqual([row[1].ticker for row in ranked], [sports_q.ticker])
        self.assertNotIn("sports_graph_context", expectancy[crypto_q.ticker]["lane_allocation"])
        self.assertNotIn("sports_graph_context", expectancy[macro_q.ticker]["lane_allocation"])
        self.assertEqual(expectancy[crypto_q.ticker]["lane_allocation"]["decision"], "SPORTS_ONLY_CRYPTO_BLOCK")
        self.assertEqual(expectancy[macro_q.ticker]["lane_allocation"]["decision"], "SPORTS_ONLY_NONSPORTS_BLOCK")

    def test_sports_ladder_consumes_relationship_graph_scores(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        game_context = {
            "game_id": "basketball:kxnba_okcsas",
            "thesis_type": "high_scoring_environment",
            "thesis_confidence": 82.0,
            "strong": True,
            "primary_candidate": "KXNBATOTAL-OKCSAS-LAG",
        }
        lag_q = SimpleNamespace(ticker="KXNBATOTAL-OKCSAS-LAG", family="KXNBATOTAL", selected_side="yes", selected_price=0.47)
        plain_q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-1135", family="KXNBA1HTOTAL", selected_side="yes", selected_price=0.46)

        def candidate(q, unified_score, graph_adjustment=0.0, lag=False):
            return {
                "ticker": q.ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA total over"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": q.ticker,
                    "family": q.family,
                    "allocation_score": unified_score,
                    "unified_opportunity_score": unified_score,
                    "edge": 0.030,
                    "spread": 0.02,
                    "liquidity": 150,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_context["game_id"],
                    "game_thesis_type": game_context["thesis_type"],
                    "game_thesis_confidence": game_context["thesis_confidence"],
                    "game_thesis_context": dict(game_context),
                    "game_thesis_signal": {"thesis_weights": {"high_scoring_environment": 0.94}},
                    "sports_graph_role": "total",
                    "sports_graph_direction": "high_scoring",
                    "sports_graph_relationship_types": ["1H-to-full-game continuation"],
                    "sports_graph_rank_adjustment": graph_adjustment,
                    "sports_graph_lag_detected": lag,
                    "sports_graph_conflict_count": 0,
                    "sports_graph_alignment_count": 1,
                },
            }

        bot.last_dual_lane_buy_candidates = [
            candidate(lag_q, 70.0, graph_adjustment=4.0, lag=True),
            candidate(plain_q, 72.0, graph_adjustment=0.0, lag=False),
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            plan = bot._build_sports_ladder_plan(bot.last_dual_lane_buy_candidates, source="unit_test_ladder_graph")

        self.assertEqual(plan["primary_candidate"], lag_q.ticker)
        self.assertTrue(plan["legs"][0]["sports_graph_lag_detected"])
        self.assertGreater(plan["legs"][0]["sports_graph_component"], 0.0)

    def test_sports_graph_dynamic_size_adjustment_is_controlled(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBATOTAL-OKCSAS-LAG",
            family="KXNBATOTAL",
            selected_side="yes",
            selected_price=0.47,
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            liquidity=140,
            orderbook_ready=True,
            quote_ready=True,
            sports_graph_size_multiplier=1.10,
            sports_graph_role="total",
            sports_graph_direction="high_scoring",
            sports_graph_lag_detected=True,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            resolved = bot._resolve_dynamic_live_entry_size(
                q,
                explicit_size=4,
                payload={
                    "ticker": q.ticker,
                    "family": q.family,
                    "count": 4,
                    "selected_side": "yes",
                    "selected_price": 0.47,
                    "sports_graph_size_multiplier": 1.10,
                    "sports_graph_role": "total",
                    "sports_graph_lag_detected": True,
                },
                ev_context={"ticker": q.ticker, "family": q.family, "ev_tier": "B"},
                source="unit_test_sports_graph_size",
            )

        self.assertGreaterEqual(resolved, 4)
        self.assertLessEqual(resolved, kw.MAX_LIVE_ORDER_SIZE)

    def test_game_thesis_dynamic_size_adjustment_is_controlled(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-SIZE-TEST",
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.42,
            yes_bid=0.40,
            yes_ask=0.42,
            no_bid=0.58,
            no_ask=0.60,
            yes_top_size=100,
            no_top_size=100,
            liquidity=180,
            orderbook_ready=True,
            quote_ready=True,
            game_id="nba:okcsas",
            game_thesis_type="high_scoring_environment",
            game_thesis_confidence=76.0,
            game_thesis_size_multiplier=1.18,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            resolved = bot._resolve_dynamic_live_entry_size(
                q,
                explicit_size=4,
                payload={
                    "ticker": q.ticker,
                    "family": q.family,
                    "count": 4,
                    "selected_side": "yes",
                    "selected_price": 0.42,
                    "game_id": q.game_id,
                    "game_thesis_type": q.game_thesis_type,
                    "game_thesis_confidence": q.game_thesis_confidence,
                    "game_thesis_size_multiplier": q.game_thesis_size_multiplier,
                },
                ev_context={"ticker": q.ticker, "family": q.family, "ev_tier": "B"},
                source="unit_test_game_thesis_size",
            )

        self.assertGreaterEqual(resolved, 4)
        self.assertLessEqual(resolved, kw.MAX_LIVE_ORDER_SIZE)

    def test_game_thesis_metadata_reaches_submit_payload(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        captured_payloads = []
        bot.recent_submitted_order_for_ticker = lambda ticker: None
        bot.recent_submitted_order_count_for_family = lambda family: 0
        bot.recent_submitted_order_count_total = lambda: 0
        bot.get_live_submit_authorization = lambda payload: {"ok": True}
        bot.can_build_live_auth = lambda: True
        bot._reset_live_trade_counter_if_needed = lambda: None
        bot._active_live_order_timestamps = lambda: []
        bot._live_order_timestamps = []
        bot.already_holding = lambda ticker: False
        bot.extract_order_execution_summary = lambda response: {"order_id": response.get("order_id")}
        bot.persist_live_entry_metadata = lambda **kwargs: None
        bot.append_live_audit_log = lambda *args, **kwargs: None
        bot.append_aion_learning_archive_event = lambda *args, **kwargs: None
        bot.place_live_order = lambda payload: captured_payloads.append(dict(payload)) or {
            "submitted": True,
            "reason": "submitted",
            "endpoint": "/trade-api/v2/portfolio/orders",
            "payload": dict(payload),
            "response": {"order_id": "unit-game-thesis"},
            "status_code": 201,
        }
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-OKCSAS-1135",
            family="KXNBA1HTOTAL",
            selected_side="no",
            selected_price=0.41,
            yes_bid=0.58,
            yes_ask=0.60,
            no_bid=0.39,
            no_ask=0.41,
            liquidity=100,
            game_thesis_context={
                "game_id": "nba:kxnba_okcsas",
                "thesis_type": "low_scoring_environment",
                "thesis_confidence": 74.0,
                "related_candidates": ["KXNBA1HTOTAL-OKCSAS-1135"],
            },
            game_id="nba:kxnba_okcsas",
            game_thesis_type="low_scoring_environment",
            game_thesis_confidence=74.0,
            game_thesis_rank_boost=4.2,
            game_thesis_size_multiplier=1.10,
            game_related_candidates=["KXNBA1HTOTAL-OKCSAS-1135"],
            game_primary_candidate="KXNBA1HTOTAL-OKCSAS-1135",
            game_secondary_candidates=[],
        )

        result = bot.submit_live_order(
            q,
            size=2,
            lane="SPORTS",
            payload_override={
                "ticker": q.ticker,
                "family": q.family,
                "side": "no",
                "selected_side": "no",
                "selected_price": 0.41,
                "price": 0.41,
                "count": 2,
            },
        )

        self.assertTrue(result["submitted"])
        self.assertEqual(captured_payloads[0]["entry_context"]["game_id"], "nba:kxnba_okcsas")
        self.assertEqual(captured_payloads[0]["entry_context"]["game_thesis_type"], "low_scoring_environment")
        self.assertEqual(captured_payloads[0]["entry_context"]["game_thesis_size_multiplier"], 1.10)

    def test_sports_ladder_builds_multiple_same_game_legs_from_strong_thesis(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        game_context = {
            "game_id": "nba:kxnba_okcsas",
            "thesis_type": "high_scoring_environment",
            "thesis_confidence": 78.0,
            "strong": True,
            "primary_candidate": "KXNBA1HTOTAL-OKCSAS-1135",
            "related_candidates": ["KXNBA1HTOTAL-OKCSAS-1135", "KXNBATOTAL-OKCSAS-2245"],
        }
        primary_q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-1135", family="KXNBA1HTOTAL", selected_side="yes", selected_price=0.46)
        secondary_q = SimpleNamespace(ticker="KXNBATOTAL-OKCSAS-2245", family="KXNBATOTAL", selected_side="yes", selected_price=0.48)
        bot.last_dual_lane_buy_candidates = [
            {
                "ticker": primary_q.ticker,
                "family": primary_q.family,
                "lane": "SPORTS",
                "q": primary_q,
                "market": {"ticker": primary_q.ticker, "series_ticker": primary_q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA 1H total over 113.5"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": primary_q.ticker,
                    "family": primary_q.family,
                    "allocation_score": 82.0,
                    "unified_opportunity_score": 84.0,
                    "edge": 0.032,
                    "spread": 0.02,
                    "liquidity": 180,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_context["game_id"],
                    "game_thesis_type": game_context["thesis_type"],
                    "game_thesis_confidence": game_context["thesis_confidence"],
                    "game_thesis_context": dict(game_context),
                },
            },
            {
                "ticker": secondary_q.ticker,
                "family": secondary_q.family,
                "lane": "SPORTS",
                "q": secondary_q,
                "market": {"ticker": secondary_q.ticker, "series_ticker": secondary_q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA game total over 224.5"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": secondary_q.ticker,
                    "family": secondary_q.family,
                    "allocation_score": 78.0,
                    "unified_opportunity_score": 80.0,
                    "edge": 0.027,
                    "spread": 0.02,
                    "liquidity": 160,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_context["game_id"],
                    "game_thesis_type": game_context["thesis_type"],
                    "game_thesis_confidence": game_context["thesis_confidence"],
                    "game_thesis_context": dict(game_context),
                },
            },
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            plan = bot._build_sports_ladder_plan(bot.last_dual_lane_buy_candidates, primary_ticker=primary_q.ticker, source="unit_test_ladder")

        self.assertEqual(plan["game_id"], "nba:kxnba_okcsas")
        self.assertEqual(plan["thesis_type"], "high_scoring_environment")
        self.assertEqual(plan["primary_candidate"], primary_q.ticker)
        self.assertEqual(plan["secondary_candidates"], [secondary_q.ticker])
        self.assertEqual(plan["legs"][0]["role"], "primary")
        self.assertEqual(plan["legs"][1]["role"], "secondary")
        self.assertGreater(plan["legs"][0]["size_cap"], plan["legs"][1]["size_cap"])

    def test_sports_ladder_weak_thesis_does_not_force_laddering(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(ticker="KXMLBMONEYLINE-NYYBOS-NYY", family="KXMLBMONEYLINE", selected_side="yes", selected_price=0.50)
        bot.last_dual_lane_buy_candidates = [
            {
                "ticker": q.ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXMLB-NYYBOS", "title": "MLB moneyline"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": q.ticker,
                    "family": q.family,
                    "allocation_score": 72.0,
                    "unified_opportunity_score": 74.0,
                    "edge": 0.018,
                    "spread": 0.02,
                    "liquidity": 90,
                    "decision": "ALLOW_SPORT",
                    "game_id": "mlb:kxmlb_nyybos",
                    "game_thesis_type": "favorite_dominance",
                    "game_thesis_confidence": 54.0,
                    "game_thesis_context": {"game_id": "mlb:kxmlb_nyybos", "thesis_type": "favorite_dominance", "thesis_confidence": 54.0, "strong": False},
                },
            }
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            plan = bot._build_sports_ladder_plan(bot.last_dual_lane_buy_candidates, primary_ticker=q.ticker, source="unit_test_ladder_weak")

        self.assertEqual(plan["reason"], "no_strong_sports_game_thesis")

    def test_sports_ladder_bad_secondary_leg_does_not_block_valid_leg(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        submitted = []
        submitted_payloads = []
        primary_q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-1135", family="KXNBA1HTOTAL", selected_side="yes", selected_price=0.46)
        bad_q = SimpleNamespace(ticker="KXNBATOTAL-OKCSAS-BAD", family="KXNBATOTAL", selected_side="yes", selected_price=None)
        good_q = SimpleNamespace(ticker="KXNBATOTAL-OKCSAS-2245", family="KXNBATOTAL", selected_side="yes", selected_price=0.48)
        game_context = {"game_id": "nba:kxnba_okcsas", "thesis_type": "high_scoring_environment", "thesis_confidence": 80.0, "strong": True, "primary_candidate": primary_q.ticker}

        def candidate(q, score):
            return {
                "ticker": q.ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA total over"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": q.ticker,
                    "family": q.family,
                    "allocation_score": score,
                    "unified_opportunity_score": score,
                    "edge": 0.030,
                    "spread": 0.02,
                    "liquidity": 150,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_context["game_id"],
                    "game_thesis_type": game_context["thesis_type"],
                    "game_thesis_confidence": game_context["thesis_confidence"],
                    "game_thesis_context": dict(game_context),
                },
            }

        bot.last_dual_lane_buy_candidates = [candidate(primary_q, 86.0), candidate(bad_q, 84.0), candidate(good_q, 82.0)]
        bot.live_positions = [{"ticker": primary_q.ticker, "status": "open", "count": 3, "entry_context": {"game_id": game_context["game_id"]}}]
        bot.already_holding = lambda ticker: False
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False}
        bot.can_open_new_position = lambda q: (True, None)
        bot.build_live_order_payload = lambda q, **kwargs: None if q.ticker == bad_q.ticker else {
            "ticker": q.ticker,
            "side": "yes",
            "selected_side": "yes",
            "selected_price": 0.48,
            "price": 0.48,
            "count": kwargs.get("size") or 1,
            "entry_context": {},
        }
        def submit_ladder(q, size=None, lane=None, payload_override=None, **kwargs):
            submitted_payloads.append(dict(payload_override or {}))
            return {
                "submitted": True,
                "reason": "submitted",
                "payload": dict(payload_override or {}),
            }

        bot.submit_live_order = submit_ladder
        bot.record_live_position = lambda q, size=None, entry_price=None, side=None: submitted.append((q.ticker, size, entry_price, side))

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot._execute_sports_ladder_attempts(
                primary_ticker=primary_q.ticker,
                primary_result={"submitted": True},
                source="unit_test_ladder_bad_leg",
            )

        self.assertTrue(result["submitted"])
        self.assertIn((good_q.ticker, 2, 0.48, "yes"), submitted)
        self.assertIn({"ticker": bad_q.ticker, "reason": "selected_price_missing_or_invalid_payload"}, result["skipped_legs"])
        self.assertEqual(submitted_payloads[0]["entry_context"]["sports_ladder_role"], "secondary")
        self.assertEqual(submitted_payloads[0]["entry_context"]["game_id"], game_context["game_id"])

    def test_sports_ladder_duplicate_leg_skips_and_continues(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        submitted = []
        primary_q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-1135", family="KXNBA1HTOTAL", selected_side="yes", selected_price=0.46)
        duplicate_q = SimpleNamespace(ticker="KXNBATOTAL-OKCSAS-2245", family="KXNBATOTAL", selected_side="yes", selected_price=0.48)
        valid_q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-1165", family="KXNBA1HTOTAL", selected_side="yes", selected_price=0.40)
        game_context = {"game_id": "nba:kxnba_okcsas", "thesis_type": "high_scoring_environment", "thesis_confidence": 81.0, "strong": True, "primary_candidate": primary_q.ticker}

        def candidate(q, score):
            return {
                "ticker": q.ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA total over"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": q.ticker,
                    "family": q.family,
                    "allocation_score": score,
                    "unified_opportunity_score": score,
                    "edge": 0.028,
                    "spread": 0.02,
                    "liquidity": 140,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_context["game_id"],
                    "game_thesis_type": game_context["thesis_type"],
                    "game_thesis_confidence": game_context["thesis_confidence"],
                    "game_thesis_context": dict(game_context),
                },
            }

        bot.last_dual_lane_buy_candidates = [candidate(primary_q, 88.0), candidate(duplicate_q, 86.0), candidate(valid_q, 83.0)]
        bot.live_positions = [{"ticker": primary_q.ticker, "status": "open", "count": 3, "entry_context": {"game_id": game_context["game_id"]}}]
        bot.already_holding = lambda ticker: ticker == duplicate_q.ticker
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False}
        bot.can_open_new_position = lambda q: (True, None)
        bot.build_live_order_payload = lambda q, **kwargs: {"ticker": q.ticker, "side": "yes", "price": 0.40, "count": kwargs.get("size") or 1, "entry_context": {}}
        bot.submit_live_order = lambda q, size=None, lane=None, payload_override=None, **kwargs: {"submitted": True, "reason": "submitted", "payload": dict(payload_override or {})}
        bot.record_live_position = lambda q, size=None, entry_price=None, side=None: submitted.append(q.ticker)

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot._execute_sports_ladder_attempts(
                primary_ticker=primary_q.ticker,
                primary_result={"submitted": True},
                source="unit_test_ladder_duplicate",
            )

        self.assertTrue(result["submitted"])
        self.assertEqual(submitted, [valid_q.ticker])
        self.assertIn({"ticker": duplicate_q.ticker, "reason": "duplicate_position_guard"}, result["skipped_legs"])

    def test_sports_ladder_game_exposure_cap_blocks_only_ladder(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-1135", family="KXNBA1HTOTAL", selected_side="yes", selected_price=0.46)
        game_id = "nba:kxnba_okcsas"
        bot.last_dual_lane_buy_candidates = [
            {
                "ticker": q.ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA 1H total over"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": q.ticker,
                    "family": q.family,
                    "allocation_score": 88.0,
                    "unified_opportunity_score": 90.0,
                    "edge": 0.035,
                    "spread": 0.02,
                    "liquidity": 160,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_id,
                    "game_thesis_type": "high_scoring_environment",
                    "game_thesis_confidence": 82.0,
                    "game_thesis_context": {"game_id": game_id, "thesis_type": "high_scoring_environment", "thesis_confidence": 82.0, "strong": True},
                },
            }
        ]
        bot.live_positions = [{"ticker": "existing", "status": "open", "count": kw.AION_SPORTS_LADDER_MAX_GAME_CONTRACTS, "entry_context": {"game_id": game_id}}]

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot._execute_sports_ladder_attempts(primary_ticker=q.ticker, primary_result={"submitted": True}, source="unit_test_ladder_cap")

        self.assertFalse(result["submitted"])
        self.assertEqual(result["reason"], "game_exposure_cap_reached")

    def test_sports_ladder_daily_loss_guard_blocks_ladder_submit(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        primary_q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-1135", family="KXNBA1HTOTAL", selected_side="yes", selected_price=0.46)
        secondary_q = SimpleNamespace(ticker="KXNBATOTAL-OKCSAS-2245", family="KXNBATOTAL", selected_side="yes", selected_price=0.48)
        game_context = {"game_id": "nba:kxnba_okcsas", "thesis_type": "high_scoring_environment", "thesis_confidence": 82.0, "strong": True, "primary_candidate": primary_q.ticker}

        def candidate(q, score):
            return {
                "ticker": q.ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA total over"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": q.ticker,
                    "family": q.family,
                    "allocation_score": score,
                    "unified_opportunity_score": score,
                    "edge": 0.031,
                    "spread": 0.02,
                    "liquidity": 150,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_context["game_id"],
                    "game_thesis_type": game_context["thesis_type"],
                    "game_thesis_confidence": game_context["thesis_confidence"],
                    "game_thesis_context": dict(game_context),
                },
            }

        bot.last_dual_lane_buy_candidates = [candidate(primary_q, 86.0), candidate(secondary_q, 82.0)]
        bot.realized_pnl = -kw.MAX_DAILY_LOSS
        bot.live_realized_pnl_today = 0.0
        bot.trading_halted = False
        bot.already_holding = lambda ticker: False
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False}
        bot.can_open_new_position = lambda q: (True, None)
        bot.build_live_order_payload = lambda *args, **kwargs: self.fail("daily loss guard should block payload construction")
        bot.submit_live_order = lambda *args, **kwargs: self.fail("daily loss guard should block submit")

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot._execute_sports_ladder_attempts(
                primary_ticker=primary_q.ticker,
                primary_result={"submitted": True},
                source="unit_test_ladder_daily_loss",
            )

        self.assertFalse(result["submitted"])
        self.assertEqual(result["reason"], "no_ladder_legs_submitted")
        self.assertIn({"ticker": secondary_q.ticker, "reason": "daily_loss_guard"}, result["skipped_legs"])

    def test_sports_ladder_ignores_crypto_candidates(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        crypto_q = SimpleNamespace(ticker="KXBTC15M-26MAY250715-UP", family="KXBTC15M", selected_side="yes", selected_price=0.40)
        sports_q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-1135", family="KXNBA1HTOTAL", selected_side="yes", selected_price=0.46)
        game_context = {"game_id": "nba:kxnba_okcsas", "thesis_type": "high_scoring_environment", "thesis_confidence": 80.0, "strong": True}
        bot.last_dual_lane_buy_candidates = [
            {"ticker": crypto_q.ticker, "family": crypto_q.family, "lane": "CRYPTO_LADDER", "q": crypto_q, "allocation": {"lane": "CRYPTO_LADDER", "unified_opportunity_score": 99.0}},
            {
                "ticker": sports_q.ticker,
                "family": sports_q.family,
                "lane": "SPORTS",
                "q": sports_q,
                "market": {"ticker": sports_q.ticker, "series_ticker": sports_q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA 1H total over"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": sports_q.ticker,
                    "family": sports_q.family,
                    "allocation_score": 86.0,
                    "unified_opportunity_score": 88.0,
                    "edge": 0.034,
                    "spread": 0.02,
                    "liquidity": 180,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_context["game_id"],
                    "game_thesis_type": game_context["thesis_type"],
                    "game_thesis_confidence": game_context["thesis_confidence"],
                    "game_thesis_context": dict(game_context),
                },
            },
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            plan = bot._build_sports_ladder_plan(bot.last_dual_lane_buy_candidates, source="unit_test_ladder_sports_only")

        self.assertEqual([leg["ticker"] for leg in plan["legs"]], [sports_q.ticker])

    def test_live_sports_regime_favorable_boosts_ranking_aggression_and_sizing(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-OKCSAS-1135",
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.46,
            yes_bid=0.44,
            yes_ask=0.46,
            no_bid=0.53,
            no_ask=0.55,
            liquidity=180,
        )
        market = {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA 1H total over"}
        allocation = {
            "lane": "SPORTS",
            "ticker": q.ticker,
            "family": q.family,
            "selected_side": "yes",
            "selected_price": 0.46,
            "spread": 0.02,
            "liquidity": 180,
            "edge": 0.032,
            "sports_conviction_score": 84.0,
            "sports_memory_rank_adjustment": 2.5,
            "sports_memory_regime": "hot",
            "game_id": "nba:kxnba_okcsas",
            "game_thesis_type": "high_scoring_environment",
            "game_thesis_confidence": 81.0,
            "game_related_candidates": ["a", "b", "c"],
        }
        bot.compute_live_sports_momentum = lambda *args, **kwargs: {
            "momentum_score": 0.62,
            "price_delta": 0.018,
            "repricing_velocity": 0.002,
            "directional_consistency": 0.9,
            "spread_behavior": "tightening",
            "volatility_state": "stable",
            "orderbook_pressure": "yes_pressure",
            "size_acceleration": 0.45,
            "history_points": 5,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot._apply_live_sports_regime_to_allocation(allocation, market=market, q=q, source="unit_test_regime_hot")
            unified = bot._build_unified_opportunity_context({"q": q, "adjusted_score": 70.0}, market=market, allocation=result, source="unit_test_regime_hot")

        self.assertEqual(result["sports_regime"], "favorable")
        self.assertEqual(result["sports_momentum_classification"], "strong_positive")
        self.assertGreater(result["sports_regime_rank_adjustment"], 0.0)
        self.assertGreater(result["sports_regime_aggression_multiplier"], 1.0)
        self.assertGreater(result["sports_regime_size_multiplier"], 1.0)
        self.assertIn("sports_regime_favorable", unified["reason"])

    def test_live_sports_regime_choppy_reduces_size_without_blocking(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(ticker="KXMLBTOTAL-NYYBOS-85", family="KXMLBTOTAL", selected_side="yes", selected_price=0.49, liquidity=8)
        market = {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXMLB-NYYBOS", "title": "MLB total over"}
        allocation = {"lane": "SPORTS", "ticker": q.ticker, "family": q.family, "selected_side": "yes", "selected_price": 0.49, "spread": 0.07, "liquidity": 8, "edge": 0.018}
        bot.compute_live_sports_momentum = lambda *args, **kwargs: {
            "momentum_score": 0.01,
            "price_delta": 0.001,
            "repricing_velocity": 0.0,
            "directional_consistency": 0.1,
            "spread_behavior": "widening",
            "volatility_state": "burst",
            "orderbook_pressure": "balanced",
            "size_acceleration": 1.0,
            "history_points": 4,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            signal = bot._get_live_sports_regime_signal(market=market, q=q, allocation=allocation, source="unit_test_regime_choppy")
            check = bot._sports_momentum_pre_submit_check(
                q=q,
                market=market,
                allocation=allocation,
                payload={"ticker": q.ticker, "family": q.family, "side": "yes", "selected_side": "yes", "selected_price": 0.49, "price": 0.49, "orderbook_ready": True},
                source="unit_test_regime_choppy",
            )

        self.assertEqual(signal["regime"], "choppy")
        self.assertEqual(signal["momentum_classification"], "unstable_choppy")
        self.assertLess(signal["size_multiplier"], 1.0)
        self.assertLess(signal["rank_adjustment"], 0.0)
        self.assertTrue(check["allowed"])

    def test_live_sports_regime_negative_allows_exceptional_candidate(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(ticker="KXNFLSPREAD-KCBUF", family="KXNFLSPREAD", selected_side="yes", selected_price=0.51, liquidity=120)
        market = {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXNFL-KCBUF", "title": "NFL spread favorite"}
        allocation = {
            "lane": "SPORTS",
            "ticker": q.ticker,
            "family": q.family,
            "selected_side": "yes",
            "selected_price": 0.51,
            "spread": 0.03,
            "liquidity": 120,
            "edge": 0.041,
            "sports_conviction_score": 90.0,
            "unified_opportunity_score": 88.0,
        }
        bot.compute_live_sports_momentum = lambda *args, **kwargs: {
            "momentum_score": -0.78,
            "price_delta": -0.02,
            "repricing_velocity": -0.003,
            "directional_consistency": -0.9,
            "spread_behavior": "stable",
            "volatility_state": "stable",
            "orderbook_pressure": "no_pressure",
            "size_acceleration": -0.2,
            "history_points": 5,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            signal = bot._get_live_sports_regime_signal(market=market, q=q, allocation=allocation, source="unit_test_regime_negative")
            check = bot._sports_momentum_pre_submit_check(
                q=q,
                market=market,
                allocation=allocation,
                payload={"ticker": q.ticker, "family": q.family, "side": "yes", "selected_side": "yes", "selected_price": 0.51, "price": 0.51, "orderbook_ready": True},
                source="unit_test_regime_negative",
            )

        self.assertEqual(signal["regime"], "negative")
        self.assertTrue(signal["exceptional_candidate"])
        self.assertLess(signal["size_multiplier"], 1.0)
        self.assertTrue(check["allowed"])

    def test_live_sports_regime_ignores_crypto_and_non_sports(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(ticker="KXBTC15M-26MAY250715-UP", family="KXBTC15M", selected_side="yes", selected_price=0.40)

        with contextlib.redirect_stdout(io.StringIO()):
            signal = bot._get_live_sports_regime_signal(
                market={"ticker": q.ticker, "series_ticker": q.family},
                q=q,
                allocation={"lane": "CRYPTO_LADDER", "ticker": q.ticker, "family": q.family},
                source="unit_test_regime_crypto",
            )

        self.assertFalse(signal["has_signal"])
        self.assertEqual(signal["rank_adjustment"], 0.0)
        self.assertEqual(signal["size_multiplier"], 1.0)
        self.assertIn(signal["reason"], {"non_sports_regime_ignored", "non_sports_lane_regime_ignored"})

    def test_live_sports_momentum_skip_moves_to_next_sports_candidate(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        bad_q = SimpleNamespace(ticker="KXNBA1HTOTAL-OKCSAS-BAD", family="KXNBA1HTOTAL", selected_side="yes", selected_price=None)
        good_q = SimpleNamespace(ticker="KXNBATOTAL-OKCSAS-2245", family="KXNBATOTAL", selected_side="yes", selected_price=0.48)
        submitted = []

        def candidate(q, score):
            return {
                "ticker": q.ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXNBA-OKCSAS", "title": "NBA total"},
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": q.ticker,
                    "family": q.family,
                    "allocation_score": score,
                    "unified_opportunity_score": score,
                    "edge": 0.03,
                    "spread": 0.02,
                    "liquidity": 150,
                    "decision": "ALLOW_STRONG_SPORT",
                },
            }

        bot.last_dual_lane_buy_candidates = [candidate(bad_q, 90.0), candidate(good_q, 82.0)]
        bot._opportunity_cost_gate = lambda *args, **kwargs: {"allowed": True}
        bot.already_holding = lambda ticker: False
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False}
        bot.build_live_order_payload = lambda q, **kwargs: None if q.ticker == bad_q.ticker else {
            "ticker": q.ticker,
            "family": q.family,
            "side": "yes",
            "selected_side": "yes",
            "selected_price": 0.48,
            "price": 0.48,
            "count": 1,
            "orderbook_ready": True,
            "entry_context": {},
        }
        bot.compute_live_sports_momentum = lambda *args, **kwargs: {
            "momentum_score": 0.25,
            "price_delta": 0.006,
            "repricing_velocity": 0.001,
            "directional_consistency": 0.7,
            "spread_behavior": "stable",
            "volatility_state": "stable",
            "orderbook_pressure": "yes_pressure",
            "size_acceleration": 0.2,
            "history_points": 4,
        }
        bot.submit_live_order = lambda q, lane=None, payload_override=None, **kwargs: submitted.append(q.ticker) or {"submitted": True, "payload": dict(payload_override or {}), "reason": "submitted"}
        bot.record_live_position = lambda *args, **kwargs: None
        bot._emit_btc1h_live_execute_log = lambda *args, **kwargs: None

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot._execute_dual_lane_secondary_candidate(target_lane="SPORTS", source="unit_test_regime_skip")

        self.assertTrue(result["submitted"])
        self.assertEqual(submitted, [good_q.ticker])

    def test_sports_only_parallel_submits_only_sports(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        submitted = []
        crypto_q = SimpleNamespace(
            ticker="KXBTC15M-26MAY250715-UP",
            selected_side="yes",
            selected_price=0.34,
            yes_bid=0.33,
            yes_ask=0.34,
            no_bid=0.66,
            no_ask=0.67,
            liquidity=220,
        )
        sports_q = SimpleNamespace(
            ticker="KXNCAABSPREAD-PURMICH",
            selected_side="yes",
            selected_price=0.46,
            yes_bid=0.44,
            yes_ask=0.46,
            no_bid=0.54,
            no_ask=0.56,
            liquidity=180,
        )
        bot.last_dual_lane_buy_candidates = [
            {
                "ticker": crypto_q.ticker,
                "lane": "CRYPTO_LADDER",
                "family": "KXBTC15M",
                "q": crypto_q,
                "market": {"ticker": crypto_q.ticker, "series_ticker": "KXBTC15M"},
                "allocation": {"lane": "CRYPTO_LADDER", "allocation_score": 96.0, "edge": 0.03, "spread": 0.01, "liquidity": 220},
            },
            {
                "ticker": sports_q.ticker,
                "lane": "SPORTS",
                "family": "KXNCAABSPREAD",
                "q": sports_q,
                "market": {"ticker": sports_q.ticker, "series_ticker": "KXNCAABSPREAD"},
                "allocation": {
                    "lane": "SPORTS",
                    "allocation_score": 84.0,
                    "edge": 0.02,
                    "confidence": 0.78,
                    "spread": 0.02,
                    "liquidity": 180,
                    "decision": "ALLOW_STRONG_SPORT",
                    "reason": "strong_sports_only_candidate",
                },
            },
        ]
        bot.already_holding = lambda ticker: False
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False}
        bot.build_live_order_payload = lambda q, **kwargs: {
            "ticker": q.ticker,
            "side": "yes",
            "price": q.selected_price,
            "count": 2,
            "yes_price": int(q.selected_price * 100),
        }
        bot.submit_live_order = lambda q, lane=None, payload_override=None, **kwargs: submitted.append(q.ticker) or {
            "submitted": True,
            "reason": "submitted",
            "payload": dict(payload_override or {}),
        }
        bot.record_live_position = lambda q, size=None, entry_price=None, side=None: None

        result = bot._execute_parallel_lane_submit_attempts(source="unit_test_sports_only")

        self.assertFalse(result["crypto_submitted"])
        self.assertTrue(result["sports_submitted"])
        self.assertEqual(result["crypto_reason"], "sports_only_crypto_execution_disabled")
        self.assertEqual(submitted, [sports_q.ticker])

    def test_sports_only_submit_live_order_hard_blocks_crypto(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(ticker="KXSOL-26MAY2517-UP", family="KXSOL", selected_side="yes", selected_price=0.30)

        result = bot.submit_live_order(
            q,
            lane="CRYPTO_LADDER",
            payload_override={"ticker": q.ticker, "side": "yes", "price": 0.30, "count": 1},
        )

        self.assertFalse(result["submitted"])
        self.assertEqual(result["reason"], "sports_only_crypto_execution_disabled")

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

    def test_quality_calibration_skips_trash_hr_moonshot(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBHR-PLAYER-2HR-MOONSHOT",
            selected_side="yes",
            selected_price=0.05,
            yes_bid=0.02,
            yes_ask=0.05,
            no_bid=0.95,
            no_ask=0.98,
            liquidity=1,
        )
        context = bot._build_lane_allocation_context(
            {
                "raw_score": 86.0,
                "adjusted_score": 86.0,
                "q": q,
                "spread": 0.03,
                "family": "KXMLBHR",
                "expectancy_summary": {
                    "final_edge": 0.0,
                    "sports_edge_after_cost": 0.0,
                    "selected_side": "yes",
                    "selected_price": 0.05,
                    "expectancy_score": 0.28,
                },
                "quality_snapshot": {"selected_price": 0.05, "liquidity_depth": 1, "spread": 0.03},
                "sports_edge_context": {"boost": 0.0, "tags": []},
                "ev_context": {"ev_edge": 0.0},
            },
            market={
                "ticker": q.ticker,
                "series_ticker": "KXMLBHR",
                "title": "Player 2+ home runs",
            },
        )

        self.assertEqual(context["decision"], "SKIP_WEAK_SPORT")
        self.assertEqual(context["final_quality_action"], "skip")
        self.assertIn("home_run_prop", context["trash_prop_patterns"])
        self.assertIn("multi_home_run_moonshot", context["trash_prop_patterns"])
        self.assertGreater(context["trash_prop_penalty"], 0.0)
        self.assertTrue(context["sports_weak_prop_reject"])

    def test_quality_calibration_boosts_nba_1h_total_ladder(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-OKCSAS-1125",
            selected_side="yes",
            selected_price=0.46,
            yes_bid=0.44,
            yes_ask=0.46,
            no_bid=0.54,
            no_ask=0.56,
            liquidity=65,
        )
        context = bot._build_lane_allocation_context(
            {
                "raw_score": 70.0,
                "adjusted_score": 70.0,
                "q": q,
                "spread": 0.02,
                "family": "KXNBA1HTOTAL",
                "expectancy_summary": {
                    "final_edge": 0.009,
                    "sports_edge_after_cost": 0.009,
                    "selected_side": "yes",
                    "selected_price": 0.46,
                    "expectancy_score": 0.58,
                },
                "quality_snapshot": {"selected_price": 0.46, "liquidity_depth": 65, "spread": 0.02},
                "sports_edge_context": {"boost": 3.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.009},
            },
            market={
                "ticker": q.ticker,
                "series_ticker": "KXNBA1HTOTAL",
                "title": "NBA 1H total ladder over 112.5",
                "price_ranges": [{"floor": 110, "cap": 115}],
            },
        )

        self.assertNotEqual(context["decision"], "SKIP_WEAK_SPORT")
        self.assertEqual(context["final_quality_action"], "allow_structured_sports_ladder")
        self.assertIn("nba_1h_total_ladder", context["structured_ladder_reasons"])
        self.assertGreater(context["structured_ladder_boost"], 0.0)
        self.assertGreater(context["allocation_score"], context["pre_lane_score"])

    def test_sports_memory_updates_after_completed_trade(self):
        bot = self._make_bot()
        with tempfile.TemporaryDirectory() as tmpdir:
            self._set_temp_sports_memory(bot, tmpdir)
            result = self._record_sports_memory_result(
                bot,
                "KXNBA1HTOTAL-OKCSAS-1125",
                pnl=0.42,
                entry_price=0.44,
                exit_price=0.58,
            )
            state = bot._load_sports_memory_state(force=True)

        self.assertTrue(result["ok"])
        self.assertEqual(len(state["records"]), 1)
        record = state["records"][0]
        self.assertEqual(record["family"], "KXNBA1HTOTAL")
        self.assertEqual(record["sport"], "nba")
        self.assertEqual(record["setup_type"], "1h_total")
        self.assertGreater(record["roi"], 0.0)
        self.assertLess(record["confidence_error"], 0.5)

    def test_sports_memory_ignores_crypto_trade_results(self):
        bot = self._make_bot()
        with tempfile.TemporaryDirectory() as tmpdir:
            self._set_temp_sports_memory(bot, tmpdir)
            with contextlib.redirect_stdout(io.StringIO()):
                result = bot._record_sports_memory_trade_event(
                    {
                        "event_type": "trade_closed",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "ticker": "KXBTC15M-26MAY250715-UP",
                        "family": "KXBTC15M",
                        "category": "crypto",
                        "side": "yes",
                        "entry_price": 0.40,
                        "exit_price": 0.50,
                        "contracts": 2,
                        "pnl_dollars": 0.20,
                    }
                )
            state = bot._load_sports_memory_state(force=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_sports_trade_closed")
        self.assertEqual(state["records"], [])

    def test_sports_memory_profitable_structure_boosts_ranking_and_sizing(self):
        bot = self._make_bot()
        with tempfile.TemporaryDirectory() as tmpdir:
            self._set_temp_sports_memory(bot, tmpdir)
            for index in range(6):
                self._record_sports_memory_result(
                    bot,
                    f"KXNBA1HTOTAL-OKCSAS-{1120 + index}",
                    pnl=0.36,
                    entry_price=0.44,
                    exit_price=0.56,
                )
            q = SimpleNamespace(
                ticker="KXNBA1HTOTAL-OKCSAS-1135",
                selected_side="yes",
                selected_price=0.46,
                yes_bid=0.44,
                yes_ask=0.46,
                no_bid=0.54,
                no_ask=0.56,
                liquidity=90,
            )
            item = {
                "raw_score": 70.0,
                "adjusted_score": 70.0,
                "q": q,
                "spread": 0.02,
                "family": "KXNBA1HTOTAL",
                "expectancy_summary": {
                    "final_edge": 0.012,
                    "sports_edge_after_cost": 0.012,
                    "selected_side": "yes",
                    "selected_price": 0.46,
                    "expectancy_score": 0.64,
                },
                "quality_snapshot": {"selected_price": 0.46, "liquidity_depth": 90, "spread": 0.02},
                "sports_edge_context": {"boost": 3.0, "tags": ["SPORTS_GOOD_BOOK", "STRUCTURED_LADDER"]},
                "ev_context": {"ev_edge": 0.012},
            }
            market = {"ticker": q.ticker, "series_ticker": "KXNBA1HTOTAL", "title": "NBA 1H total ladder over 113.5"}
            with contextlib.redirect_stdout(io.StringIO()):
                allocation = bot._build_lane_allocation_context(item, market=market)
                unified = bot._build_unified_opportunity_context(item, market=market, allocation=allocation)

        self.assertEqual(allocation["sports_memory_regime"], "hot")
        self.assertGreater(allocation["sports_memory_rank_adjustment"], 0.0)
        self.assertGreater(allocation["sports_memory_size_multiplier"], 1.0)
        self.assertIn("sports_memory_hot", unified["reason"])
        self.assertGreater(unified["sports_memory_rank_adjustment"], 0.0)

    def test_sports_memory_losing_structure_reduces_smoothly_without_starving_execution(self):
        bot = self._make_bot()
        with tempfile.TemporaryDirectory() as tmpdir:
            self._set_temp_sports_memory(bot, tmpdir)
            for index in range(6):
                self._record_sports_memory_result(
                    bot,
                    f"KXNBA1HTOTAL-OKCSAS-COLD-{index}",
                    pnl=-0.30,
                    entry_price=0.50,
                    exit_price=0.40,
                )
            q = SimpleNamespace(
                ticker="KXNBA1HTOTAL-OKCSAS-CANDIDATE",
                selected_side="yes",
                selected_price=0.46,
                yes_bid=0.44,
                yes_ask=0.46,
                no_bid=0.54,
                no_ask=0.56,
                liquidity=160,
            )
            item = {
                "raw_score": 82.0,
                "adjusted_score": 82.0,
                "q": q,
                "spread": 0.02,
                "family": "KXNBA1HTOTAL",
                "expectancy_summary": {
                    "final_edge": 0.030,
                    "sports_edge_after_cost": 0.030,
                    "selected_side": "yes",
                    "selected_price": 0.46,
                    "expectancy_score": 0.86,
                },
                "quality_snapshot": {"selected_price": 0.46, "liquidity_depth": 160, "spread": 0.02},
                "sports_edge_context": {"boost": 5.0, "tags": ["SPORTS_GOOD_BOOK", "STRUCTURED_LADDER"]},
                "ev_context": {"ev_edge": 0.030},
            }
            market = {"ticker": q.ticker, "series_ticker": "KXNBA1HTOTAL", "title": "NBA 1H total ladder over 113.5"}
            with contextlib.redirect_stdout(io.StringIO()):
                allocation = bot._build_lane_allocation_context(item, market=market)
                unified = bot._build_unified_opportunity_context(item, market=market, allocation=allocation)

        self.assertEqual(allocation["sports_memory_regime"], "cold")
        self.assertLess(allocation["sports_memory_rank_adjustment"], 0.0)
        self.assertGreaterEqual(allocation["sports_memory_size_multiplier"], kw.AION_SPORTS_MEMORY_MIN_SIZE_MULTIPLIER)
        self.assertLess(allocation["sports_memory_size_multiplier"], 1.0)
        self.assertEqual(allocation["decision"], "ALLOW_STRONG_SPORT")
        self.assertGreater(allocation["allocation_score"], 0.0)
        self.assertIn("sports_memory_cold", unified["reason"])

    def test_sports_memory_dynamic_size_adjustment_is_controlled(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-SIZE-TEST",
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.42,
            yes_bid=0.40,
            yes_ask=0.42,
            no_bid=0.58,
            no_ask=0.60,
            yes_top_size=50,
            no_top_size=50,
            liquidity=120,
            orderbook_ready=True,
            quote_ready=True,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            resolved = bot._resolve_dynamic_live_entry_size(
                q,
                explicit_size=4,
                payload={
                    "ticker": q.ticker,
                    "family": q.family,
                    "count": 4,
                    "selected_side": "yes",
                    "selected_price": 0.42,
                    "sports_memory_size_multiplier": 0.82,
                    "sports_memory_regime": "cold",
                },
                ev_context={"ticker": q.ticker, "family": q.family, "ev_tier": "B"},
                source="unit_test_sports_memory",
            )

        self.assertEqual(resolved, 3)
        self.assertGreaterEqual(resolved, 1)

    def test_quality_calibration_allows_exceptional_hr_quality(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBHR-PLAYER-STRONG",
            selected_side="yes",
            selected_price=0.22,
            yes_bid=0.20,
            yes_ask=0.22,
            no_bid=0.78,
            no_ask=0.80,
            liquidity=140,
        )
        context = bot._build_lane_allocation_context(
            {
                "raw_score": 76.0,
                "adjusted_score": 76.0,
                "q": q,
                "spread": 0.02,
                "family": "KXMLBHR",
                "expectancy_summary": {
                    "final_edge": 0.026,
                    "sports_edge_after_cost": 0.026,
                    "selected_side": "yes",
                    "selected_price": 0.22,
                    "expectancy_score": 0.76,
                },
                "quality_snapshot": {"selected_price": 0.22, "liquidity_depth": 140, "spread": 0.02},
                "sports_edge_context": {"boost": 5.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.026},
            },
            market={
                "ticker": q.ticker,
                "series_ticker": "KXMLBHR",
                "title": "Player to hit a home run",
            },
        )

        self.assertNotEqual(context["decision"], "SKIP_WEAK_SPORT")
        self.assertTrue(context["exceptional_trash_quality"])
        self.assertEqual(context["final_quality_action"], "allow_exceptional_trash_pattern")
        self.assertIn("home_run_prop", context["trash_prop_patterns"])

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

    def test_parallel_lane_submit_attempts_submit_crypto_and_sports_independently(self):
        self._set_sports_only_mode(False)
        bot = self._make_bot()
        submitted = []
        crypto_q = SimpleNamespace(
            ticker='KXBTC-26MAY2517-B74875',
            selected_side='yes',
            selected_price=0.28,
            yes_bid=0.27,
            yes_ask=0.28,
            no_bid=0.72,
            no_ask=0.73,
            liquidity=180,
        )
        sports_q = SimpleNamespace(
            ticker='KXNBA1HTOTAL-OKCSAS-1125',
            selected_side='yes',
            selected_price=0.48,
            yes_bid=0.46,
            yes_ask=0.48,
            no_bid=0.52,
            no_ask=0.54,
            liquidity=95,
        )
        bot.last_dual_lane_buy_candidates = [
            {
                'ticker': crypto_q.ticker,
                'lane': 'CRYPTO_LADDER',
                'family': 'KXBTC',
                'q': crypto_q,
                'market': {'ticker': crypto_q.ticker, 'series_ticker': 'KXBTC'},
                'allocation': {
                    'lane': 'CRYPTO_LADDER',
                    'allocation_score': 92.0,
                    'edge': 0.021,
                    'confidence': 0.82,
                    'spread': 0.01,
                    'liquidity': 180,
                    'decision': 'RESERVE_CRYPTO_LADDER',
                    'reason': 'strong_crypto_ladder',
                },
            },
            {
                'ticker': sports_q.ticker,
                'lane': 'SPORTS',
                'family': 'KXNBA1HTOTAL',
                'q': sports_q,
                'market': {'ticker': sports_q.ticker, 'series_ticker': 'KXNBA1HTOTAL'},
                'allocation': {
                    'lane': 'SPORTS',
                    'allocation_score': 84.0,
                    'edge': 0.016,
                    'confidence': 0.74,
                    'spread': 0.02,
                    'liquidity': 95,
                    'decision': 'ALLOW_STRONG_SPORT',
                    'reason': 'strong_structured_sports_ladder',
                },
            },
        ]
        bot.already_holding = lambda ticker: False
        bot.check_live_duplicate_position_api = lambda ticker: {'blocked': False}
        bot.build_live_order_payload = lambda q, **kwargs: {
            'ticker': q.ticker,
            'side': 'yes',
            'price': q.selected_price,
            'count': 3,
            'yes_price': int(q.selected_price * 100),
        }
        bot.submit_live_order = lambda q, lane=None, payload_override=None, **kwargs: submitted.append((q.ticker, lane, payload_override['count'])) or {
            'submitted': True,
            'reason': 'submitted',
            'payload': dict(payload_override or {}),
        }
        bot.record_live_position = lambda q, size=None, entry_price=None, side=None: None

        result = bot._execute_parallel_lane_submit_attempts(source='unit_test')

        self.assertTrue(result['crypto_submitted'])
        self.assertTrue(result['sports_submitted'])
        self.assertEqual([item[0] for item in submitted], [crypto_q.ticker, sports_q.ticker])

    def test_parallel_lane_submit_attempts_crypto_when_sports_primary_blocked(self):
        self._set_sports_only_mode(False)
        bot = self._make_bot()
        submitted = []
        crypto_q = SimpleNamespace(
            ticker='KXETH-26MAY2517-B3500',
            selected_side='yes',
            selected_price=0.31,
            yes_bid=0.30,
            yes_ask=0.31,
            no_bid=0.69,
            no_ask=0.70,
            liquidity=140,
        )
        sports_q = SimpleNamespace(
            ticker='KXMLBHR-PLAYER-3HR-MOONSHOT',
            selected_side='yes',
            selected_price=0.04,
            yes_bid=0.01,
            yes_ask=0.04,
            no_bid=0.96,
            no_ask=0.99,
            liquidity=1,
        )
        bot.last_dual_lane_buy_candidates = [
            {
                'ticker': sports_q.ticker,
                'lane': 'SPORTS',
                'family': 'KXMLBHR',
                'q': sports_q,
                'market': {'ticker': sports_q.ticker, 'series_ticker': 'KXMLBHR'},
                'allocation': {
                    'lane': 'SPORTS',
                    'allocation_score': 70.0,
                    'edge': 0.0,
                    'confidence': 0.2,
                    'spread': 0.03,
                    'liquidity': 1,
                    'decision': 'SKIP_WEAK_SPORT',
                    'final_quality_action': 'skip',
                    'reason': 'trash_prop_weak_edge',
                },
            },
            {
                'ticker': crypto_q.ticker,
                'lane': 'CRYPTO_LADDER',
                'family': 'KXETH',
                'q': crypto_q,
                'market': {'ticker': crypto_q.ticker, 'series_ticker': 'KXETH'},
                'allocation': {
                    'lane': 'CRYPTO_LADDER',
                    'allocation_score': 88.0,
                    'edge': 0.018,
                    'confidence': 0.79,
                    'spread': 0.01,
                    'liquidity': 140,
                    'decision': 'RESERVE_CRYPTO_LADDER',
                    'reason': 'strong_crypto_ladder',
                },
            },
        ]
        bot.already_holding = lambda ticker: False
        bot.check_live_duplicate_position_api = lambda ticker: {'blocked': False}
        bot.build_live_order_payload = lambda q, **kwargs: {
            'ticker': q.ticker,
            'side': 'yes',
            'price': q.selected_price,
            'count': 4,
            'yes_price': int(q.selected_price * 100),
        }
        bot.submit_live_order = lambda q, lane=None, payload_override=None, **kwargs: submitted.append(q.ticker) or {
            'submitted': True,
            'reason': 'submitted',
            'payload': dict(payload_override or {}),
        }
        bot.record_live_position = lambda q, size=None, entry_price=None, side=None: None

        result = bot._execute_parallel_lane_submit_attempts(
            primary_ticker=sports_q.ticker,
            primary_lane='SPORTS',
            primary_result={'submitted': False, 'reason': 'weak_sports_quality_demoted'},
            source='unit_test_blocked_primary',
        )

        self.assertTrue(result['crypto_submitted'])
        self.assertEqual(submitted, [crypto_q.ticker])

    def test_crypto_quote_hydration_builds_selected_price_and_depth_from_fp_sizes(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXBTC-26MAY2517-B73875",
            "series_ticker": "KXBTC",
            "event_ticker": "KXBTC-26MAY2517",
            "title": "Bitcoin above $73,875 at 5pm",
            "yes_bid_dollars": 0.0,
            "yes_ask_dollars": 0.01,
            "no_bid_dollars": 0.99,
            "no_ask_dollars": 1.0,
            "yes_ask_size_fp": 272,
            "no_bid_size_fp": 272,
            "no_ask_size_fp": 309,
            "yes_bid_size_fp": 309,
        }

        q = bot.build_quote(market)

        self.assertEqual(q.selected_side, "yes")
        self.assertEqual(q.selected_price, 0.01)
        self.assertGreaterEqual(q.yes_top_size, 272)
        self.assertGreaterEqual(q.liquidity, 272)

        resolution = bot.resolve_aion_orderbook_for_payload(
            q.ticker,
            candidate=q,
            market=market,
            price_source="ask",
            selected_side="yes",
            allow_refresh=False,
        )

        self.assertTrue(resolution["orderbook_ready"])
        self.assertEqual(resolution["selected_side"], "yes")
        self.assertEqual(resolution["selected_price_cents"], 1)
        self.assertTrue(resolution["selected_side_has_liquidity"])
        self.assertGreaterEqual(resolution["yes_top_size"], 272)

    def test_crypto_malformed_book_blocks_without_executable_price(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXBTC-26MAY2517-B73875",
            "series_ticker": "KXBTC",
            "event_ticker": "KXBTC-26MAY2517",
            "title": "Bitcoin above $73,875 at 5pm",
        }

        resolution = bot.resolve_aion_orderbook_for_payload(
            market["ticker"],
            market=market,
            price_source="ask",
            selected_side="yes",
            allow_refresh=False,
        )

        self.assertFalse(resolution["orderbook_ready"])
        self.assertIn(
            resolution["block_reason"],
            {"orderbook_fetch_not_called", "selected_price_missing", "missing_price"},
        )

    def test_crypto_hydration_does_not_mutate_sports_candidate(self):
        bot = self._make_bot()
        sports_q = SimpleNamespace(
            ticker="KXMLBSPREAD-SPORTS-TEST",
            selected_side="yes",
            selected_price=0.47,
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            yes_top_size=18,
            no_top_size=22,
            liquidity=22,
        )
        sports_market = {
            "ticker": sports_q.ticker,
            "series_ticker": "KXMLBSPREAD",
            "title": "MLB spread",
            "yes_bid": 45,
            "yes_ask": 47,
            "no_bid": 53,
            "no_ask": 55,
        }
        before = dict(vars(sports_q))

        snapshot = bot._crypto_ladder_quote_hydration_snapshot(
            market=sports_market,
            q=sports_q,
            family="KXMLBSPREAD",
            ticker=sports_q.ticker,
            emit_log=False,
        )
        bot._crypto_ladder_quote_hydration_snapshot(
            market={
                "ticker": "KXETH-26MAY2517-B3500",
                "series_ticker": "KXETH",
                "title": "Ethereum above $3,500 at 5pm",
                "yes_ask_dollars": 0.31,
                "yes_ask_size_fp": 140,
            },
            family="KXETH",
            ticker="KXETH-26MAY2517-B3500",
            emit_log=False,
        )

        self.assertFalse(snapshot["is_crypto_ladder"])
        self.assertEqual(vars(sports_q), before)

    def test_crypto_final_decision_uses_ready_book_not_sports_liquidity_score(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXETH-26MAY2517-B1920",
            selected_side="yes",
            selected_price=None,
            yes_bid=0.30,
            yes_ask=0.31,
            no_bid=0.69,
            no_ask=0.70,
            yes_top_size=3,
            no_top_size=3,
            liquidity=3,
            volume=0,
        )
        market = {
            "ticker": q.ticker,
            "series_ticker": "KXETH",
            "event_ticker": "KXETH-26MAY2517",
            "title": "Ethereum above $1,920 at 5pm",
            "yes_bid_dollars": 0.30,
            "yes_ask_dollars": 0.31,
            "no_bid_dollars": 0.69,
            "no_ask_dollars": 0.70,
            "yes_ask_size_fp": 3,
            "no_ask_size_fp": 3,
            "ladder_boost": 1.0,
        }
        bot.is_targeted_series_market = lambda market: False
        bot.get_market_min_entry_liquidity = lambda market: 10.0
        bot.get_market_liquidity_priority = lambda market: {
            "yes_top_size": 3,
            "no_top_size": 3,
            "score": 1.0,
            "reasons": ["unit_low_depth"],
            "has_real_liquidity": True,
            "both_sides_quoted": True,
        }
        bot._resolve_effective_liquidity_signal = lambda *args, **kwargs: {
            "effective_source": "topbook",
            "liquidity_final": 3.0,
            "fallback_used": True,
            "effective_top_size": 3.0,
        }
        bot._build_aion_market_priority_profile = lambda **kwargs: {
            "crypto_priority": True,
            "market_priority_score": 0.0,
            "execution_quality_profile": {},
        }
        bot._build_aion_scan_rebalancer_profile = lambda **kwargs: {"scan_weight": 1.0}
        bot._get_family_performance_weight = lambda family: 0.0
        bot.get_market_discovery_category = lambda family: "crypto"
        bot._is_btc_volatility_family = lambda *args, **kwargs: False
        bot.get_minutes_to_event = lambda market: 30.0
        bot.get_event_time_bucket = lambda minutes: "normal"
        bot.get_effective_max_minutes_to_event = lambda: 120.0
        bot._get_elite_public_signal_match = lambda **kwargs: {"boost": 0.0}
        bot._evaluate_market_psychology = lambda **kwargs: {
            "blocked": False,
            "shadow_only": False,
            "psychology_delta": 0.0,
            "adjusted_score": kwargs.get("original_score", 0.0),
            "original_score": kwargs.get("original_score", 0.0),
        }
        bot._evaluate_live_edge_quality = lambda **kwargs: {
            "confidence_score": 0.0,
            "confidence_weight": 0.0,
        }
        bot._realm_xxxii_shadow_arb_audit = lambda *args, **kwargs: {
            "hard_block": True,
            "reject_reason": "low_liquidity_score",
        }
        bot._record_shadow_arb_validation = lambda *args, **kwargs: {}
        bot._get_live_session_entry_adjustment = lambda **kwargs: {"score_penalty": 0.0}
        bot._record_live_reject_telemetry = lambda **kwargs: None
        bot._record_live_near_pass = lambda **kwargs: None
        bot.resolve_aion_orderbook_for_payload = lambda *args, **kwargs: {
            "orderbook_ready": True,
            "selected_side": "yes",
            "selected_price": 0.31,
            "selected_price_cents": 31,
            "yes_bid": 0.30,
            "yes_ask": 0.31,
            "no_bid": 0.69,
            "no_ask": 0.70,
            "yes_top_size": 3,
            "no_top_size": 3,
            "selected_top_size": 3,
            "liquidity_depth": 3,
            "selected_side_has_liquidity": True,
        }

        result = bot.score_final_candidate_decision(market, q, 0.01, "KXETH")

        self.assertEqual(result["decision"], "BUY")
        self.assertEqual(result["decision_reason"], "crypto_lane_orderbook_ready")
        self.assertEqual(q.selected_side, "yes")
        self.assertEqual(q.selected_price, 0.31)

    def test_crypto_live_eligibility_allows_cheap_valid_price(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        market = {
            "ticker": "KXBTC-26MAY2517-B73875",
            "series_ticker": "KXBTC",
            "event_ticker": "KXBTC-26MAY2517",
            "title": "Bitcoin above $73,875 at 5pm",
            "status": "open",
            "close_time": close_time,
        }
        q = SimpleNamespace(ticker=market["ticker"], selected_side="yes", selected_price=0.01, liquidity=0)
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market=market,
            q=q,
            family="KXBTC",
            ticker=market["ticker"],
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.01,
                "selected_price_cents": 1,
                "yes_bid": 0.0,
                "yes_ask": 0.01,
                "no_bid": 0.99,
                "no_ask": 1.0,
                "yes_top_size": 272,
                "no_top_size": 309,
                "selected_top_size": 272,
                "selected_side_has_liquidity": True,
            },
            spread=0.01,
            edge_after_cost=0.0,
            emit_log=False,
        )

        self.assertTrue(result["eligible"])
        self.assertTrue(result["price_allowed"])
        self.assertEqual(result["selected_price_cents"], 1)
        self.assertEqual(result["liquidity_source"], "topbook_fallback")

    def test_crypto_live_eligibility_blocks_impossible_price(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        market = {
            "ticker": "KXBTC-26MAY2517-B73875",
            "series_ticker": "KXBTC",
            "status": "open",
            "close_time": close_time,
        }
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market=market,
            family="KXBTC",
            ticker=market["ticker"],
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 1.25,
                "selected_price_cents": 125,
                "yes_bid": 0.98,
                "yes_ask": 1.25,
                "yes_top_size": 10,
                "selected_top_size": 10,
                "selected_side_has_liquidity": True,
            },
            spread=0.02,
            edge_after_cost=0.01,
            emit_log=False,
        )

        self.assertFalse(result["eligible"])
        self.assertFalse(result["price_allowed"])
        self.assertIn("unsafe_price", result["blockers"])

    def test_crypto_live_market_allowed_despite_stale_sports_lifecycle(self):
        bot = self._make_bot()
        bot._get_sports_market_lifecycle_state = lambda **kwargs: "pregame_far"
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=35)).isoformat()
        market = {
            "ticker": "KXETH-26MAY2517-B1920",
            "series_ticker": "KXETH",
            "status": "open",
            "close_time": close_time,
            "ladder_boost": 2.0,
        }
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market=market,
            family="KXETH",
            ticker=market["ticker"],
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.31,
                "selected_price_cents": 31,
                "yes_bid": 0.30,
                "yes_ask": 0.31,
                "no_bid": 0.69,
                "no_ask": 0.70,
                "yes_top_size": 6,
                "no_top_size": 6,
                "selected_top_size": 6,
                "selected_side_has_liquidity": True,
            },
            spread=0.01,
            edge_after_cost=0.0,
            emit_log=False,
        )

        self.assertTrue(result["eligible"])
        self.assertTrue(result["market_live_allowed"])
        self.assertTrue(result["lifecycle_allowed"])

    def test_crypto_closed_or_expired_market_blocked(self):
        bot = self._make_bot()
        future_close = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        past_close = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        base_resolution = {
            "orderbook_ready": True,
            "selected_side": "yes",
            "selected_price": 0.31,
            "selected_price_cents": 31,
            "yes_bid": 0.30,
            "yes_ask": 0.31,
            "no_bid": 0.69,
            "no_ask": 0.70,
            "yes_top_size": 6,
            "no_top_size": 6,
            "selected_top_size": 6,
            "selected_side_has_liquidity": True,
        }

        for status, close_time, blocker in (
            ("closed", future_close, "market_not_live"),
            ("open", past_close, "lifecycle_not_allowed"),
        ):
            with self.subTest(status=status):
                result = bot._evaluate_crypto_ladder_live_eligibility(
                    market={
                        "ticker": "KXSOL-26MAY2517-B180",
                        "series_ticker": "KXSOL",
                        "status": status,
                        "close_time": close_time,
                        "ladder_boost": 2.0,
                    },
                    family="KXSOL",
                    ticker="KXSOL-26MAY2517-B180",
                    orderbook_resolution=base_resolution,
                    spread=0.01,
                    edge_after_cost=0.0,
                    emit_log=False,
                )
                self.assertFalse(result["eligible"])
                self.assertIn(blocker, result["blockers"])

    def test_crypto_topbook_liquidity_fallback_qualifies_when_reported_zero(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=40)).isoformat()
        market = {
            "ticker": "KXXRP-26MAY2517-B250",
            "series_ticker": "KXXRP",
            "status": "open",
            "close_time": close_time,
            "ladder_boost": 1.0,
            "liquidity": 0,
        }
        q = SimpleNamespace(ticker=market["ticker"], selected_side="yes", selected_price=0.24, liquidity=0)
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market=market,
            q=q,
            family="KXXRP",
            ticker=market["ticker"],
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.24,
                "selected_price_cents": 24,
                "yes_bid": 0.22,
                "yes_ask": 0.24,
                "no_bid": 0.76,
                "no_ask": 0.78,
                "yes_top_size": 7,
                "no_top_size": 8,
                "selected_top_size": 7,
                "liquidity_depth": 0,
                "selected_side_has_liquidity": True,
            },
            spread=0.02,
            edge_after_cost=0.0,
            emit_log=False,
        )

        self.assertTrue(result["eligible"])
        self.assertEqual(result["liquidity_source"], "topbook_fallback")
        self.assertGreater(result["liquidity"], 0)

    def test_crypto_ranked_candidate_score_can_clear_zero_raw_edge(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market={
                "ticker": "KXBTC-26MAY2502-B77250",
                "series_ticker": "KXBTC",
                "status": "open",
                "close_time": close_time,
            },
            q=SimpleNamespace(ticker="KXBTC-26MAY2502-B77250", selected_side="yes"),
            family="KXBTC",
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.19,
                "selected_price_cents": 19,
                "yes_bid": 0.14,
                "yes_ask": 0.19,
                "no_bid": 0.81,
                "no_ask": 0.86,
                "yes_top_size": 40,
                "no_top_size": 140,
                "selected_top_size": 40,
                "liquidity_depth": 140,
                "selected_side_has_liquidity": True,
            },
            spread=0.05,
            edge_after_cost=0.0,
            candidate_score=90.0,
            emit_log=False,
        )

        self.assertTrue(result["eligible"])
        self.assertTrue(result["edge_allowed"])
        self.assertEqual(result["candidate_score"], 90.0)
        self.assertEqual(result["edge_after_cost"], 0.0)

    def test_crypto_deep_tail_without_structure_is_skipped(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market={
                "ticker": "KXBTC-26MAY2517-B79000",
                "series_ticker": "KXBTC",
                "status": "open",
                "close_time": close_time,
                "current_asset_price": 77000,
                "selected_range_low": 79000,
                "selected_range_high": 79100,
                "momentum_direction": "down",
            },
            q=SimpleNamespace(ticker="KXBTC-26MAY2517-B79000", selected_side="yes"),
            family="KXBTC",
            ticker="KXBTC-26MAY2517-B79000",
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.02,
                "selected_price_cents": 2,
                "yes_bid": 0.01,
                "yes_ask": 0.02,
                "no_bid": 0.98,
                "no_ask": 0.99,
                "yes_top_size": 80,
                "no_top_size": 100,
                "selected_top_size": 80,
                "selected_side_has_liquidity": True,
            },
            spread=0.01,
            edge_after_cost=0.0,
            emit_log=False,
        )

        self.assertFalse(result["eligible"])
        self.assertEqual(result["distance_class"], "deep_tail_range")
        self.assertIn("unsupported_deep_tail", result["blockers"])
        self.assertEqual(result["cheap_range_decision"], "skip_unsupported_deep_tail")

    def test_crypto_deep_tail_with_momentum_cluster_support_can_pass(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        bot.btc1h_cycle_priority_by_ticker = {
            "KXBTC-26MAY2517-B79000": {
                "ladder_boost": 3.0,
                "historical_boost": 1.5,
                "neighbor_confirmation": True,
                "components": {"nearby_confirmation": 0.72, "repricing_lag": 0.40},
                "score": 72.0,
            }
        }
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market={
                "ticker": "KXBTC-26MAY2517-B79000",
                "series_ticker": "KXBTC",
                "status": "open",
                "close_time": close_time,
                "current_asset_price": 77000,
                "selected_range_low": 79000,
                "selected_range_high": 79100,
                "momentum_direction": "up",
            },
            q=SimpleNamespace(ticker="KXBTC-26MAY2517-B79000", selected_side="yes"),
            family="KXBTC",
            ticker="KXBTC-26MAY2517-B79000",
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.02,
                "selected_price_cents": 2,
                "yes_bid": 0.01,
                "yes_ask": 0.02,
                "no_bid": 0.98,
                "no_ask": 0.99,
                "yes_top_size": 80,
                "no_top_size": 100,
                "selected_top_size": 80,
                "selected_side_has_liquidity": True,
            },
            spread=0.01,
            edge_after_cost=0.0,
            emit_log=False,
        )

        self.assertTrue(result["eligible"])
        self.assertEqual(result["distance_class"], "deep_tail_range")
        self.assertTrue(result["deep_tail_supported"])
        self.assertEqual(result["cheap_range_decision"], "allow_supported_deep_tail")
        self.assertLess(result["crypto_size_multiplier"], 1.0)

    def test_crypto_reachable_range_gets_quality_and_size_boost(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        bot.btc1h_cycle_priority_by_ticker = {
            "KXETH-26MAY2517-B2420": {
                "ladder_boost": 2.0,
                "historical_boost": 1.0,
                "neighbor_confirmation": True,
                "components": {"nearby_confirmation": 0.66, "repricing_lag": 0.35},
                "score": 65.0,
            }
        }
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market={
                "ticker": "KXETH-26MAY2517-B2420",
                "series_ticker": "KXETH",
                "status": "open",
                "close_time": close_time,
                "current_asset_price": 2400,
                "selected_range_low": 2412,
                "selected_range_high": 2420,
                "momentum_direction": "up",
            },
            q=SimpleNamespace(ticker="KXETH-26MAY2517-B2420", selected_side="yes"),
            family="KXETH",
            ticker="KXETH-26MAY2517-B2420",
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.08,
                "selected_price_cents": 8,
                "yes_bid": 0.07,
                "yes_ask": 0.08,
                "no_bid": 0.92,
                "no_ask": 0.93,
                "yes_top_size": 90,
                "no_top_size": 100,
                "selected_top_size": 90,
                "selected_side_has_liquidity": True,
            },
            spread=0.01,
            edge_after_cost=0.0,
            emit_log=False,
        )

        self.assertTrue(result["eligible"])
        self.assertIn(result["distance_class"], {"near_range", "reachable_range"})
        self.assertTrue(result["cluster_support"])
        self.assertTrue(result["momentum_support"])
        self.assertGreater(result["crypto_size_multiplier"], 1.0)

    def test_btc15m_momentum_gets_boosted(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market={
                "ticker": "KXBTC15M-26MAY250715-UP",
                "series_ticker": "KXBTC15M",
                "title": "Bitcoin up in 15 minutes",
                "status": "open",
                "close_time": close_time,
                "momentum_direction": "up",
                "momentum_score": 0.82,
                "repricing_velocity": 0.012,
            },
            q=SimpleNamespace(ticker="KXBTC15M-26MAY250715-UP", selected_side="yes"),
            family="KXBTC15M",
            ticker="KXBTC15M-26MAY250715-UP",
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.42,
                "selected_price_cents": 42,
                "yes_bid": 0.40,
                "yes_ask": 0.42,
                "no_bid": 0.58,
                "no_ask": 0.60,
                "yes_top_size": 120,
                "no_top_size": 130,
                "selected_top_size": 120,
                "selected_side_has_liquidity": True,
            },
            spread=0.02,
            edge_after_cost=0.0,
            emit_log=False,
        )

        self.assertTrue(result["eligible"])
        self.assertTrue(result["crypto_momentum_entry"])
        self.assertGreaterEqual(result["crypto_momentum_score"], 58.0)
        self.assertGreaterEqual(result["repricing_capture_score"], 58.0)
        self.assertTrue(result["static_ev_override"])
        self.assertGreater(result["momentum_edge"], 0.0)
        self.assertGreater(result["effective_edge_after_cost"], 0.0)
        self.assertGreater(result["crypto_size_multiplier"], 1.5)

    def test_btc15m_static_zero_edge_without_momentum_is_not_overridden(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market={
                "ticker": "KXBTC15M-26MAY250715-UP",
                "series_ticker": "KXBTC15M",
                "title": "Bitcoin up in 15 minutes",
                "status": "open",
                "close_time": close_time,
                "momentum_direction": "unknown",
                "momentum_score": 0.0,
                "repricing_velocity": 0.0,
            },
            q=SimpleNamespace(ticker="KXBTC15M-26MAY250715-UP", selected_side="yes"),
            family="KXBTC15M",
            ticker="KXBTC15M-26MAY250715-UP",
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.42,
                "selected_price_cents": 42,
                "yes_bid": 0.40,
                "yes_ask": 0.42,
                "no_bid": 0.58,
                "no_ask": 0.60,
                "yes_top_size": 120,
                "no_top_size": 130,
                "selected_top_size": 120,
                "selected_side_has_liquidity": True,
            },
            spread=0.02,
            edge_after_cost=0.0,
            emit_log=False,
        )

        self.assertFalse(result["eligible"])
        self.assertFalse(result["static_ev_override"])
        self.assertEqual(result["momentum_edge"], 0.0)
        self.assertIn("edge_too_low", result["blockers"])

    def test_btc15m_repricing_capture_scores_and_sizes_up(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=9)).isoformat()
        bot.btc1h_cycle_priority_by_ticker = {
            "KXETH15M-26MAY250715-DOWN": {
                "historical_boost": 1.2,
                "neighbor_confirmation": True,
                "components": {"nearby_confirmation": 0.70, "repricing_lag": 0.45},
            }
        }
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market={
                "ticker": "KXETH15M-26MAY250715-DOWN",
                "series_ticker": "KXETH15M",
                "title": "Ethereum down in 15 minutes",
                "status": "open",
                "close_time": close_time,
                "momentum_direction": "down",
                "momentum_score": -0.75,
                "repricing_velocity": -0.018,
            },
            q=SimpleNamespace(ticker="KXETH15M-26MAY250715-DOWN", selected_side="yes"),
            family="KXETH15M",
            ticker="KXETH15M-26MAY250715-DOWN",
            orderbook_resolution={
                "orderbook_ready": True,
                "selected_side": "yes",
                "selected_price": 0.44,
                "selected_price_cents": 44,
                "yes_bid": 0.42,
                "yes_ask": 0.44,
                "no_bid": 0.56,
                "no_ask": 0.58,
                "yes_top_size": 160,
                "no_top_size": 150,
                "selected_top_size": 160,
                "selected_side_has_liquidity": True,
            },
            spread=0.02,
            edge_after_cost=0.0,
            emit_log=False,
        )

        self.assertTrue(result["eligible"])
        self.assertTrue(result["crypto_momentum_entry"])
        self.assertEqual(result["side_expected_direction"], "down")
        self.assertGreaterEqual(result["repricing_capture_score"], 70.0)
        self.assertGreater(result["crypto_size_multiplier"], 2.0)

    def test_crypto_quality_size_cap_does_not_apply_to_sports(self):
        bot = self._make_bot()
        sports_q = SimpleNamespace(
            ticker="KXMLBSTRIKEOUT-TEST-65",
            family="KXMLBSTRIKEOUT",
            ev_tier="S",
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
            crypto_quality_size_cap=1,
        )

        resolved = bot._resolve_dynamic_live_entry_size(
            sports_q,
            explicit_size=1,
            payload={"ticker": sports_q.ticker, "count": 1, "selected_side": "yes", "selected_price": 0.20},
            ev_context={
                "ticker": sports_q.ticker,
                "family": sports_q.family,
                "ev_tier": "S",
                "ELITE_EDGE_CONFIDENCE": 91.0,
                "STRUCTURAL_EDGE_SCORE": 92.0,
                "CROSS_MARKET_EDGE_SCORE": 84.0,
                "elite_edge_size_multiplier": 8.0,
                "expected_roi": 0.11,
                "expected_value": 0.038,
                "ev_edge": 0.055,
                "crypto_quality_size_cap": 1,
            },
        )

        self.assertGreater(resolved, 1)

    def test_crypto_fast_profit_exit_triggers_near_target(self):
        bot = self._make_bot()
        ticker = "KXBTC15M-26MAY250715-UP"
        bot.live_exit_price_history = {}
        bot.dynamic_exit_log_markers = {}
        bot.crypto_exit_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.30,
            "side": "yes",
            "count": 100,
            "entry_ts": time.time() - 45,
        }
        bot.get_live_market_detail = lambda _ticker: {}
        bot.get_market_family = lambda _market: "KXBTC15M"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": 0.25}
        q = SimpleNamespace(
            ticker=ticker,
            yes_bid=0.41,
            yes_ask=0.42,
            no_bid=0.58,
            no_ask=0.59,
        )

        decision = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.30, "count": 100, "entry_ts": time.time() - 45},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "exit")
        self.assertEqual(decision["reason"], "crypto_fast_take_profit")
        self.assertGreaterEqual(decision["unrealized_pnl"], 10.0)

    def test_crypto_reversal_exit_triggers_with_profit(self):
        bot = self._make_bot()
        ticker = "KXETH15M-26MAY250715-UP"
        bot.live_exit_price_history = {ticker: [{"ts": time.time() - 20, "price": 0.42}]}
        bot.dynamic_exit_log_markers = {}
        bot.crypto_exit_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.32,
            "side": "yes",
            "count": 50,
            "entry_ts": time.time() - 70,
        }
        bot.get_live_market_detail = lambda _ticker: {}
        bot.get_market_family = lambda _market: "KXETH15M"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": -0.20}
        q = SimpleNamespace(
            ticker=ticker,
            yes_bid=0.38,
            yes_ask=0.39,
            no_bid=0.61,
            no_ask=0.62,
        )

        decision = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.32, "count": 50, "entry_ts": time.time() - 70},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "exit")
        self.assertEqual(decision["reason"], "green_momentum_flip")
        self.assertTrue(decision["green_momentum_flip_exit"])
        self.assertGreater(decision["unrealized_pnl"], 0.0)

    def test_crypto_stall_exit_protects_profit(self):
        bot = self._make_bot()
        ticker = "KXBTC15M-26MAY250715-UP"
        now = time.time()
        bot.live_exit_price_history = {
            ticker: [
                {"ts": now - 45, "price": 0.37},
                {"ts": now - 20, "price": 0.372},
            ]
        }
        bot.dynamic_exit_log_markers = {}
        bot.crypto_exit_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.35,
            "side": "yes",
            "count": 20,
            "entry_ts": now - 80,
        }
        bot.get_live_market_detail = lambda _ticker: {}
        bot.get_market_family = lambda _market: "KXBTC15M"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": 0.0}
        q = SimpleNamespace(
            ticker=ticker,
            yes_bid=0.372,
            yes_ask=0.382,
            no_bid=0.618,
            no_ask=0.628,
        )

        decision = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.35, "count": 20, "entry_ts": now - 80},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "exit")
        self.assertEqual(decision["reason"], "crypto_momentum_stall")
        self.assertTrue(decision["crypto_momentum_stall_exit"])

    def test_sports_profitable_position_triggers_profit_lock(self):
        bot = self._make_bot()
        ticker = "KXNCAABSPREAD-PURMICH-35"
        bot.live_exit_price_history = {}
        bot.dynamic_exit_log_markers = {}
        bot.profit_lock_log_markers = {}
        bot.sports_harvest_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.30,
            "side": "yes",
            "count": 5,
            "entry_ts": time.time() - 120,
        }
        bot.get_live_market_detail = lambda _ticker: {}
        bot.get_market_family = lambda _market: "KXNCAABSPREAD"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": 0.18}
        q = SimpleNamespace(ticker=ticker, yes_bid=0.52, yes_ask=0.54, no_bid=0.46, no_ask=0.48)

        decision = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.30, "count": 5, "entry_ts": time.time() - 120},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "exit")
        self.assertEqual(decision["reason"], "fast_profit_dollar_lock")
        self.assertTrue(decision["profit_lock_exit"])
        self.assertTrue(decision["is_sports_position"])

    def test_crypto_profitable_position_triggers_profit_lock(self):
        bot = self._make_bot()
        ticker = "KXBTC-26MAY2517-B79000"
        bot.live_exit_price_history = {}
        bot.dynamic_exit_log_markers = {}
        bot.profit_lock_log_markers = {}
        bot.crypto_exit_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.02,
            "side": "yes",
            "count": 100,
            "entry_ts": time.time() - 60,
        }
        bot.get_live_market_detail = lambda _ticker: {}
        bot.get_market_family = lambda _market: "KXBTC"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": 0.0}
        q = SimpleNamespace(ticker=ticker, yes_bid=0.04, yes_ask=0.05, no_bid=0.95, no_ask=0.96)

        decision = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.02, "count": 100, "entry_ts": time.time() - 60},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "exit")
        self.assertEqual(decision["reason"], "fast_profit_dollar_lock")
        self.assertTrue(decision["profit_lock_exit"])
        self.assertTrue(decision["is_crypto_ladder_position"])

    def test_profit_lock_tracks_peak_value_and_trailing_giveback(self):
        bot = self._make_bot()
        ticker = "KXNCAABTOTAL-PURMICH"
        now = time.time()
        bot.live_exit_price_history = {}
        bot.dynamic_exit_log_markers = {}
        bot.profit_lock_log_markers = {}
        bot.sports_harvest_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.30,
            "side": "yes",
            "count": 10,
            "entry_ts": now - 180,
        }
        bot.get_live_market_detail = lambda _ticker: {}
        bot.get_market_family = lambda _market: "KXNCAABTOTAL"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": 0.10}

        first = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.30, "count": 10, "entry_ts": now - 180},
            quotes_by_ticker={ticker: SimpleNamespace(ticker=ticker, yes_bid=0.70, yes_ask=0.72, no_bid=0.28, no_ask=0.30)},
        )
        second = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.30, "count": 10, "entry_ts": now - 210},
            quotes_by_ticker={ticker: SimpleNamespace(ticker=ticker, yes_bid=0.50, yes_ask=0.52, no_bid=0.48, no_ask=0.50)},
        )

        self.assertTrue(first["peak_value_updated"])
        self.assertGreaterEqual(first["peak_value"], 7.0)
        self.assertEqual(second["action"], "exit")
        self.assertEqual(second["reason"], "trailing_profit_giveback")
        self.assertTrue(second["trailing_profit_exit"])
        self.assertGreaterEqual(second["peak_giveback_pct"], 0.30)

    def test_near_max_payout_position_exits(self):
        bot = self._make_bot()
        ticker = "KXNFLMONEYLINE-TEST"
        bot.live_exit_price_history = {}
        bot.dynamic_exit_log_markers = {}
        bot.profit_lock_log_markers = {}
        bot.sports_harvest_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.40,
            "side": "yes",
            "count": 2,
            "entry_ts": time.time() - 90,
        }
        bot.get_live_market_detail = lambda _ticker: {}
        bot.get_market_family = lambda _market: "KXNFLMONEYLINE"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": 0.25}
        q = SimpleNamespace(ticker=ticker, yes_bid=0.72, yes_ask=0.74, no_bid=0.26, no_ask=0.28)

        decision = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.40, "count": 2, "entry_ts": time.time() - 90},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "exit")
        self.assertEqual(decision["reason"], "max_payout_profit_lock")
        self.assertTrue(decision["max_payout_lock_exit"])

    def test_profit_lock_handles_no_side_positions(self):
        bot = self._make_bot()
        ticker = "KXNCAABTOTAL-PURMICH"
        bot.live_exit_price_history = {}
        bot.dynamic_exit_log_markers = {}
        bot.profit_lock_log_markers = {}
        bot.sports_harvest_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.50,
            "side": "no",
            "count": 4,
            "entry_ts": time.time() - 80,
        }
        bot.get_live_market_detail = lambda _ticker: {}
        bot.get_market_family = lambda _market: "KXNCAABTOTAL"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": -0.15}
        q = SimpleNamespace(ticker=ticker, yes_bid=0.33, yes_ask=0.35, no_bid=0.65, no_ask=0.67)

        decision = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "no", "entry_price": 0.50, "count": 4, "entry_ts": time.time() - 80},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "exit")
        self.assertEqual(decision["side"], "no")
        self.assertEqual(decision["reason"], "fast_profit_pct_lock")
        self.assertTrue(decision["profit_lock_pct_exit"])

    def test_strong_sports_conviction_sizes_and_ranks_higher(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNCAABSPREAD-PURMICH-35",
            selected_side="yes",
            selected_price=0.46,
            yes_bid=0.44,
            yes_ask=0.46,
            no_bid=0.54,
            no_ask=0.56,
            liquidity=180,
        )
        context = bot._build_lane_allocation_context(
            {
                "raw_score": 74.0,
                "adjusted_score": 74.0,
                "q": q,
                "spread": 0.02,
                "family": "KXNCAABSPREAD",
                "expectancy_summary": {
                    "final_edge": 0.024,
                    "sports_edge_after_cost": 0.024,
                    "selected_side": "yes",
                    "selected_price": 0.46,
                    "expectancy_score": 0.78,
                },
                "quality_snapshot": {"selected_price": 0.46, "liquidity_depth": 180, "spread": 0.02},
                "sports_edge_context": {"boost": 5.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.024},
            },
            market={"ticker": q.ticker, "series_ticker": "KXNCAABSPREAD", "title": "Purdue vs Michigan spread"},
        )

        self.assertEqual(context["decision"], "ALLOW_STRONG_SPORT")
        self.assertTrue(context["sports_high_conviction"])
        self.assertGreater(context["sports_conviction_score"], 62.0)
        self.assertGreater(context["sports_conviction_size_multiplier"], 1.0)
        self.assertGreater(context["allocation_score"], context["pre_lane_score"] + kw.AION_STRONG_SPORTS_LANE_BOOST)

    def test_crypto_activation_gate_uses_crypto_eligibility_not_sports_prefilter_survivors(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        market = {
            "ticker": "KXETH-26MAY2517-B2400",
            "series_ticker": "KXETH",
            "event_ticker": "KXETH-26MAY2517",
            "title": "Ethereum above $2,400 at 5pm",
            "status": "open",
            "close_time": close_time,
            "ladder_boost": 1.0,
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            selected_side="yes",
            selected_price=0.19,
            yes_bid=0.17,
            yes_ask=0.19,
            no_bid=0.81,
            no_ask=0.83,
            yes_top_size=5,
            no_top_size=6,
            liquidity=0,
        )
        bot.build_quote = lambda market_arg: q
        bot.resolve_aion_orderbook_for_payload = lambda *args, **kwargs: {
            "orderbook_ready": True,
            "selected_side": "yes",
            "selected_price": 0.19,
            "selected_price_cents": 19,
            "yes_bid": 0.17,
            "yes_ask": 0.19,
            "no_bid": 0.81,
            "no_ask": 0.83,
            "yes_top_size": 5,
            "no_top_size": 6,
            "selected_top_size": 5,
            "liquidity_depth": 0,
            "selected_side_has_liquidity": True,
        }

        result = bot.get_controlled_activation_status(market, "KXETH", {})

        self.assertTrue(result["armed"])
        self.assertEqual(result["denied_reasons"], [])
        self.assertEqual(result["activation_reason"], "crypto_specific_live_eligibility")

    def test_crypto_activation_gate_uses_strict_prefilter_survivor_for_ranked_edge(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        market = {
            "ticker": "KXBTC-26MAY2502-B77250",
            "series_ticker": "KXBTC",
            "event_ticker": "KXBTC-26MAY2502",
            "title": "Bitcoin above $77,250 at 2am",
            "status": "open",
            "close_time": close_time,
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            selected_side="yes",
            selected_price=0.19,
            yes_bid=0.14,
            yes_ask=0.19,
            no_bid=0.81,
            no_ask=0.86,
            yes_top_size=40,
            no_top_size=140,
            liquidity=140,
        )
        bot.build_quote = lambda market_arg: q
        bot.resolve_aion_orderbook_for_payload = lambda *args, **kwargs: {
            "orderbook_ready": True,
            "selected_side": "yes",
            "selected_price": 0.19,
            "selected_price_cents": 19,
            "yes_bid": 0.14,
            "yes_ask": 0.19,
            "no_bid": 0.81,
            "no_ask": 0.86,
            "yes_top_size": 40,
            "no_top_size": 140,
            "selected_top_size": 40,
            "liquidity_depth": 140,
            "selected_side_has_liquidity": True,
        }

        result = bot.get_controlled_activation_status(market, "KXBTC", {"KXBTC": 1})

        self.assertTrue(result["armed"])
        self.assertEqual(result["denied_reasons"], [])
        self.assertEqual(result["activation_reason"], "crypto_specific_live_eligibility")

    def test_sports_activation_gate_still_requires_sports_prefilter_survivor(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA1HTOTAL-TEST-1125",
            "series_ticker": "KXNBA1HTOTAL",
            "title": "NBA first half total over 112.5",
            "yes_bid": 48,
            "yes_ask": 50,
            "no_bid": 50,
            "no_ask": 52,
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            yes_bid=0.48,
            yes_ask=0.50,
            no_bid=0.50,
            no_ask=0.52,
        )
        bot.build_quote = lambda market_arg: q
        bot.get_market_liquidity_priority = lambda market_arg: {"top_book_state": "both_non_zero"}
        bot.get_market_quality_preference = lambda market_arg, liquidity_priority=None: {
            "both_sides_quoted": True,
            "narrow_spread": True,
            "recent_activity": True,
        }
        bot._derive_sports_synthetic_top_size = lambda *args, **kwargs: (None, None, None)

        result = bot.get_controlled_activation_status(market, "KXNBA1HTOTAL", {})

        self.assertFalse(result["armed"])
        self.assertIn("no_prefilter_survivors", result["denied_reasons"])

    def test_sports_wide_spread_prefilter_converts_to_adaptive_pass(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA1HTOTAL-WIDE-TEST",
            "series_ticker": "KXNBA1HTOTAL",
            "status": "open",
            "title": "NBA first half total over 112.5",
            "yes_bid": 0.40,
            "yes_ask": 0.58,
            "no_bid": 0.42,
            "no_ask": 0.60,
            "volume": 12,
            "open_interest": 80,
            "liquidity_dollars": 30,
        }
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "both_non_zero",
            "yes_top_size": 6,
            "no_top_size": 6,
            "both_sides_quoted": True,
            "liquidity_final": 30,
        }
        bot.build_quote = lambda market_arg: SimpleNamespace(
            ticker=market["ticker"],
            yes_bid=0.40,
            yes_ask=0.58,
            no_bid=0.42,
            no_ask=0.60,
            liquidity=30,
            volume=12,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            allowed, reason, family = bot.coarse_prefilter(market)

        self.assertTrue(allowed)
        self.assertIsNone(reason)
        self.assertEqual(family, "KXNBA1HTOTAL")
        self.assertIn("_sports_spread_context", market)
        self.assertEqual(market["_sports_spread_context"]["threshold_before"], kw.MAX_ENTRY_SPREAD)
        self.assertGreater(market["_sports_spread_context"]["threshold_used"], kw.MAX_ENTRY_SPREAD)
        self.assertLess(market["_sports_spread_context"]["size_multiplier"], 1.0)

    def test_sports_absurd_spread_still_rejected_as_bad_book(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA1HTOTAL-BROKEN-TEST",
            "series_ticker": "KXNBA1HTOTAL",
            "status": "open",
            "title": "NBA first half total over 112.5",
            "yes_bid": 0.08,
            "yes_ask": 0.82,
            "no_bid": 0.18,
            "no_ask": 0.92,
            "volume": 10,
            "open_interest": 60,
            "liquidity_dollars": 30,
        }
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "both_non_zero",
            "yes_top_size": 8,
            "no_top_size": 8,
            "both_sides_quoted": True,
            "liquidity_final": 30,
        }
        bot.build_quote = lambda market_arg: SimpleNamespace(
            ticker=market["ticker"],
            yes_bid=0.08,
            yes_ask=0.82,
            no_bid=0.18,
            no_ask=0.92,
            liquidity=30,
            volume=10,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            allowed, reason, _family = bot.coarse_prefilter(market)

        self.assertFalse(allowed)
        self.assertEqual(reason, "wide_spread")
        self.assertEqual(market["_sports_spread_context"]["reason"], "spread_absurd_unusable")

    def test_non_sports_wide_spread_remains_strict(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXFEDRATE-WIDE-TEST",
            "series_ticker": "KXFEDRATE",
            "status": "open",
            "yes_bid": 0.40,
            "yes_ask": 0.58,
            "no_bid": 0.42,
            "no_ask": 0.60,
            "volume": 100,
            "liquidity_dollars": 100,
        }
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "both_non_zero",
            "yes_top_size": 10,
            "no_top_size": 10,
            "both_sides_quoted": True,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            allowed, reason, _family = bot.coarse_prefilter(market)

        self.assertFalse(allowed)
        self.assertEqual(reason, "wide_spread")

    def test_sports_wide_spread_reduces_ranking_and_size_without_blocking(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-WIDE-RANK",
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.58,
            yes_bid=0.40,
            yes_ask=0.58,
            no_bid=0.42,
            no_ask=0.60,
            liquidity=40,
            volume=20,
        )
        market = {
            "ticker": q.ticker,
            "series_ticker": q.family,
            "status": "open",
            "title": "NBA first half total over 112.5",
            "yes_bid": 0.40,
            "yes_ask": 0.58,
            "no_bid": 0.42,
            "no_ask": 0.60,
            "volume": 20,
            "liquidity_dollars": 40,
            "open_interest": 100,
        }
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "both_non_zero",
            "yes_top_size": 8,
            "no_top_size": 8,
            "both_sides_quoted": True,
            "liquidity_final": 40,
        }
        item = {
            "q": q,
            "family": q.family,
            "spread": 0.18,
            "raw_score": 82.0,
            "adjusted_score": 82.0,
            "expectancy_summary": {
                "final_edge": 0.025,
                "edge_after_cost": 0.025,
                "expectancy_score": 0.72,
                "sports_edge_after_cost": 0.025,
            },
            "sports_edge_context": {"boost": 4.0, "tags": ["SPORTS_GOOD_BOOK"]},
            "quality_snapshot": {"selected_price": 0.58, "liquidity_depth": 40, "spread": 0.18, "confidence_score": 0.72},
        }

        with contextlib.redirect_stdout(io.StringIO()):
            allocation = bot._build_lane_allocation_context(item, market=market)
            context = bot._build_unified_opportunity_context(item, market=market, allocation=allocation, source="unit_test_spread")

        self.assertEqual(allocation["lane"], "SPORTS")
        self.assertLess(allocation["sports_spread_rank_adjustment"], 0.0)
        self.assertLess(allocation["sports_spread_size_multiplier"], 1.0)
        self.assertLess(context["sports_spread_size_multiplier"], 1.0)
        self.assertGreater(context["unified_score"], 0.0)

    def test_crypto_eligibility_helper_does_not_apply_to_sports_candidate(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBSPREAD-SPORTS-TEST",
            selected_side="yes",
            selected_price=0.47,
            yes_bid=0.45,
            yes_ask=0.47,
            liquidity=12,
        )
        before = dict(vars(q))
        result = bot._evaluate_crypto_ladder_live_eligibility(
            market={"ticker": q.ticker, "series_ticker": "KXMLBSPREAD", "status": "open"},
            q=q,
            family="KXMLBSPREAD",
            ticker=q.ticker,
            emit_log=False,
        )

        self.assertFalse(result["is_crypto_ladder"])
        self.assertEqual(vars(q), before)

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

    def test_unified_opportunity_elite_crypto_beats_mediocre_future_sports(self):
        self._set_sports_only_mode(False)
        bot = self._make_bot()
        crypto_close = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
        sports_start = (datetime.now(timezone.utc) + timedelta(minutes=240)).isoformat()
        crypto_q = SimpleNamespace(
            ticker="KXBTC15M-26MAY250715-UP",
            selected_side="yes",
            selected_price=0.34,
            yes_bid=0.33,
            yes_ask=0.34,
            no_bid=0.66,
            no_ask=0.67,
            liquidity=180,
            crypto_momentum_score=82.0,
            repricing_capture_score=78.0,
            crypto_distance_class="reachable_range",
        )
        sports_q = SimpleNamespace(
            ticker="KXNCAABSPREAD-FUTURE-MEDIOCRE",
            selected_side="yes",
            selected_price=0.47,
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            liquidity=80,
        )
        markets = [
            {"ticker": crypto_q.ticker, "series_ticker": "KXBTC15M", "title": "Bitcoin up in 15 minutes", "close_time": crypto_close},
            {"ticker": sports_q.ticker, "series_ticker": "KXNCAABSPREAD", "title": "NCAA spread", "event_start_time": sports_start},
        ]
        expectancy = {
            crypto_q.ticker: {
                "raw_score": 70.0,
                "adjusted_score": 70.0,
                "q": crypto_q,
                "spread": 0.01,
                "family": "KXBTC15M",
                "expectancy_summary": {"final_edge": 0.022, "selected_side": "yes", "selected_price": 0.34},
                "quality_snapshot": {"selected_price": 0.34, "liquidity_depth": 180, "spread": 0.01},
                "ev_context": {"ev_edge": 0.022, "crypto_momentum_score": 82.0, "repricing_capture_score": 78.0},
            },
            sports_q.ticker: {
                "raw_score": 88.0,
                "adjusted_score": 88.0,
                "q": sports_q,
                "spread": 0.02,
                "family": "KXNCAABSPREAD",
                "expectancy_summary": {"final_edge": 0.006, "sports_edge_after_cost": 0.006, "selected_side": "yes", "selected_price": 0.47, "expectancy_score": 0.52},
                "quality_snapshot": {"selected_price": 0.47, "liquidity_depth": 80, "spread": 0.02},
                "sports_edge_context": {"boost": 0.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.006},
            },
        }

        ranked = bot._apply_dual_lane_allocation_ranking(
            [(88.0, sports_q, 0.02, "KXNCAABSPREAD"), (70.0, crypto_q, 0.01, "KXBTC15M")],
            markets,
            expectancy_summary_by_ticker=expectancy,
            source="unit_test_unified",
        )

        self.assertEqual(ranked[0][1].ticker, crypto_q.ticker)
        self.assertGreater(
            expectancy[crypto_q.ticker]["lane_allocation"]["unified_opportunity_score"],
            expectancy[sports_q.ticker]["lane_allocation"]["unified_opportunity_score"],
        )
        self.assertTrue(expectancy[crypto_q.ticker]["lane_allocation"]["unified_opportunity"]["fast_turnover_boost"])

    def test_unified_opportunity_elite_sports_beats_mediocre_crypto(self):
        bot = self._make_bot()
        sports_start = (datetime.now(timezone.utc) + timedelta(minutes=240)).isoformat()
        crypto_close = (datetime.now(timezone.utc) + timedelta(minutes=180)).isoformat()
        sports_q = SimpleNamespace(
            ticker="KXNCAABSPREAD-PURMICH-ELITE",
            selected_side="yes",
            selected_price=0.46,
            yes_bid=0.44,
            yes_ask=0.46,
            no_bid=0.54,
            no_ask=0.56,
            liquidity=220,
        )
        crypto_q = SimpleNamespace(
            ticker="KXETH-26MAY2517-DEEPTAIL",
            selected_side="yes",
            selected_price=0.02,
            yes_bid=0.01,
            yes_ask=0.02,
            no_bid=0.98,
            no_ask=0.99,
            liquidity=20,
            crypto_momentum_score=12.0,
            repricing_capture_score=10.0,
            crypto_distance_class="deep_tail_range",
        )
        markets = [
            {"ticker": sports_q.ticker, "series_ticker": "KXNCAABSPREAD", "title": "Purdue vs Michigan spread", "event_start_time": sports_start},
            {"ticker": crypto_q.ticker, "series_ticker": "KXETH", "title": "Ethereum far range", "close_time": crypto_close},
        ]
        expectancy = {
            sports_q.ticker: {
                "raw_score": 78.0,
                "adjusted_score": 78.0,
                "q": sports_q,
                "spread": 0.02,
                "family": "KXNCAABSPREAD",
                "expectancy_summary": {"final_edge": 0.030, "sports_edge_after_cost": 0.030, "selected_side": "yes", "selected_price": 0.46, "expectancy_score": 0.86},
                "quality_snapshot": {"selected_price": 0.46, "liquidity_depth": 220, "spread": 0.02},
                "sports_edge_context": {"boost": 5.0, "tags": ["SPORTS_GOOD_BOOK"]},
                "ev_context": {"ev_edge": 0.030},
            },
            crypto_q.ticker: {
                "raw_score": 92.0,
                "adjusted_score": 92.0,
                "q": crypto_q,
                "spread": 0.01,
                "family": "KXETH",
                "expectancy_summary": {"final_edge": 0.001, "selected_side": "yes", "selected_price": 0.02},
                "quality_snapshot": {"selected_price": 0.02, "liquidity_depth": 20, "spread": 0.01},
                "ev_context": {"ev_edge": 0.001, "crypto_momentum_score": 12.0, "repricing_capture_score": 10.0, "crypto_distance_class": "deep_tail_range"},
            },
        }

        ranked = bot._apply_dual_lane_allocation_ranking(
            [(92.0, crypto_q, 0.01, "KXETH"), (78.0, sports_q, 0.02, "KXNCAABSPREAD")],
            markets,
            expectancy_summary_by_ticker=expectancy,
            source="unit_test_unified",
        )

        self.assertEqual(ranked[0][1].ticker, sports_q.ticker)
        self.assertLess(
            expectancy[sports_q.ticker]["lane_allocation"]["capital_lock_penalty"],
            10.0,
        )
        self.assertGreater(
            expectancy[sports_q.ticker]["lane_allocation"]["unified_opportunity_score"],
            expectancy[crypto_q.ticker]["lane_allocation"]["unified_opportunity_score"],
        )

    def test_capital_duration_penalizes_future_sports_but_allows_elite(self):
        bot = self._make_bot()
        far_start = (datetime.now(timezone.utc) + timedelta(minutes=240)).isoformat()
        weak_q = SimpleNamespace(ticker="KXNCAABSPREAD-FAR-WEAK", selected_side="yes", selected_price=0.49, yes_bid=0.47, yes_ask=0.49, no_bid=0.51, no_ask=0.53, liquidity=60)
        elite_q = SimpleNamespace(ticker="KXNCAABSPREAD-FAR-ELITE", selected_side="yes", selected_price=0.46, yes_bid=0.44, yes_ask=0.46, no_bid=0.54, no_ask=0.56, liquidity=220)
        weak_item = {
            "raw_score": 78.0,
            "adjusted_score": 78.0,
            "q": weak_q,
            "spread": 0.02,
            "family": "KXNCAABSPREAD",
            "expectancy_summary": {"final_edge": 0.005, "sports_edge_after_cost": 0.005, "selected_side": "yes", "selected_price": 0.49, "expectancy_score": 0.50},
            "quality_snapshot": {"selected_price": 0.49, "liquidity_depth": 60, "spread": 0.02},
            "sports_edge_context": {"boost": 0.0, "tags": ["SPORTS_GOOD_BOOK"]},
            "ev_context": {"ev_edge": 0.005},
        }
        elite_item = {
            "raw_score": 78.0,
            "adjusted_score": 78.0,
            "q": elite_q,
            "spread": 0.02,
            "family": "KXNCAABSPREAD",
            "expectancy_summary": {"final_edge": 0.032, "sports_edge_after_cost": 0.032, "selected_side": "yes", "selected_price": 0.46, "expectancy_score": 0.88},
            "quality_snapshot": {"selected_price": 0.46, "liquidity_depth": 220, "spread": 0.02},
            "sports_edge_context": {"boost": 5.0, "tags": ["SPORTS_GOOD_BOOK"]},
            "ev_context": {"ev_edge": 0.032},
        }

        weak_allocation = bot._build_lane_allocation_context(weak_item, market={"ticker": weak_q.ticker, "series_ticker": "KXNCAABSPREAD", "event_start_time": far_start})
        elite_allocation = bot._build_lane_allocation_context(elite_item, market={"ticker": elite_q.ticker, "series_ticker": "KXNCAABSPREAD", "title": "Purdue vs Michigan spread", "event_start_time": far_start})
        weak_context = bot._build_unified_opportunity_context(weak_item, market={"ticker": weak_q.ticker, "series_ticker": "KXNCAABSPREAD", "event_start_time": far_start}, allocation=weak_allocation)
        elite_context = bot._build_unified_opportunity_context(elite_item, market={"ticker": elite_q.ticker, "series_ticker": "KXNCAABSPREAD", "title": "Purdue vs Michigan spread", "event_start_time": far_start}, allocation=elite_allocation)

        self.assertTrue(weak_context["future_event_demote"])
        self.assertFalse(elite_context["future_event_demote"])
        self.assertGreater(weak_context["capital_lock_penalty"], elite_context["capital_lock_penalty"])
        self.assertGreater(elite_context["unified_score"], weak_context["unified_score"])

    def test_btc15m_fast_turnover_boost_with_momentum(self):
        bot = self._make_bot()
        close_time = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
        q = SimpleNamespace(
            ticker="KXBTC15M-26MAY250715-UP",
            selected_side="yes",
            selected_price=0.36,
            yes_bid=0.35,
            yes_ask=0.36,
            no_bid=0.64,
            no_ask=0.65,
            liquidity=150,
            crypto_momentum_score=84.0,
            repricing_capture_score=80.0,
            crypto_distance_class="reachable_range",
        )
        item = {
            "raw_score": 72.0,
            "adjusted_score": 72.0,
            "q": q,
            "spread": 0.01,
            "family": "KXBTC15M",
            "expectancy_summary": {"final_edge": 0.018, "selected_side": "yes", "selected_price": 0.36},
            "quality_snapshot": {"selected_price": 0.36, "liquidity_depth": 150, "spread": 0.01},
            "ev_context": {"ev_edge": 0.018, "crypto_momentum_score": 84.0, "repricing_capture_score": 80.0},
        }
        market = {"ticker": q.ticker, "series_ticker": "KXBTC15M", "title": "Bitcoin up in 15 minutes", "close_time": close_time}
        allocation = bot._build_lane_allocation_context(item, market=market)
        context = bot._build_unified_opportunity_context(item, market=market, allocation=allocation)

        self.assertTrue(context["fast_turnover_boost"])
        self.assertEqual(context["capital_lock_penalty"], 0.0)
        self.assertGreater(context["unified_score"], 70.0)

    def test_low_bankroll_concentrates_capital_in_best_opportunity(self):
        bot = self._make_bot()
        bot.available_cash = 10.0
        best = {
            "ticker": "KXBTC15M-BEST",
            "lane": "CRYPTO_LADDER",
            "family": "KXBTC15M",
            "allocation": {"unified_opportunity_score": 90.0, "allocation_score": 86.0},
        }
        weaker = {
            "ticker": "KXNCAABSPREAD-WEAKER",
            "lane": "SPORTS",
            "family": "KXNCAABSPREAD",
            "allocation": {"unified_opportunity_score": 68.0, "allocation_score": 84.0},
        }

        self.assertTrue(bot._opportunity_cost_gate(best, [best, weaker], source="unit_test")["allowed"])
        blocked = bot._opportunity_cost_gate(weaker, [best, weaker], source="unit_test")

        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "low_cash_concentrate_best_opportunity")

    def test_unified_scoring_preserves_selected_no_side_into_payload(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNCAABTOTAL-NOEDGE",
            selected_side="no",
            selected_price=0.42,
            yes_bid=0.56,
            yes_ask=0.58,
            no_bid=0.40,
            no_ask=0.42,
            liquidity=120,
        )
        item = {
            "raw_score": 82.0,
            "adjusted_score": 82.0,
            "q": q,
            "spread": 0.02,
            "family": "KXNCAABTOTAL",
            "expectancy_summary": {"final_edge": 0.022, "sports_edge_after_cost": 0.022, "selected_side": "no", "selected_price": 0.42, "expectancy_score": 0.78},
            "quality_snapshot": {"selected_side": "no", "selected_price": 0.42, "liquidity_depth": 120, "spread": 0.02},
            "sports_edge_context": {"boost": 4.0, "tags": ["SPORTS_GOOD_BOOK"]},
            "ev_context": {"ev_edge": 0.022},
        }
        market = {"ticker": q.ticker, "series_ticker": "KXNCAABTOTAL", "title": "NCAA total"}
        allocation = bot._build_lane_allocation_context(item, market=market)
        context = bot._build_unified_opportunity_context(item, market=market, allocation=allocation)
        bot.resolve_aion_orderbook_for_payload = lambda *args, **kwargs: {
            "orderbook_ready": True,
            "selected_side": "no",
            "selected_price": 0.42,
            "selected_price_dollars": 0.42,
            "selected_price_cents": 42,
            "yes_bid": 0.56,
            "yes_ask": 0.58,
            "no_bid": 0.40,
            "no_ask": 0.42,
            "no_top_size": 120,
            "selected_side_has_liquidity": True,
        }
        q.unified_opportunity_score = context["unified_score"]
        q.unified_size_multiplier = context["size_multiplier"]

        payload = bot.build_live_order_payload(q, market=market, selection_stage="dual_lane_secondary")

        self.assertEqual(payload["side"], "no")
        self.assertEqual(payload["selected_side"], "no")
        self.assertEqual(payload["no_price"], 42)
        self.assertNotIn("yes_price", payload)


if __name__ == "__main__":
    unittest.main()

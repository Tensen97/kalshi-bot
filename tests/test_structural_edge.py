import unittest
import contextlib
import io
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import kalshi_web as kw
from sports import live_intelligence as sli
from kalshi_ranking import get_momentum_rank_adjustment
from kalshi_sniper.candidate_quality import compute_candidate_ev


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
        bot.sports_probability_log_markers = {}
        bot.sports_execution_log_markers = {}
        bot.last_sports_graph_contexts = {}
        bot.last_sports_ladder_plan = {}
        bot.last_sports_ladder_execution_summary = {}
        bot.sports_momentum_state = {}
        bot.targeted_orderbook_cache = {}
        bot.family_profit_memory_cache = {"loaded_at": 0.0, "stats": {}}
        bot.family_profit_memory_log_markers = {}
        bot.live_family_perf_stats = {}
        bot.last_lane_allocation_summary = {}
        bot.last_dual_lane_buy_candidates = []
        bot.last_dual_lane_execution_summary = {}
        bot.live_trade_count_today = 0
        bot.live_positions = []
        bot.get_live_market_detail = lambda ticker: {"ticker": ticker, "in_play": True, "status": "in_play"}
        return bot

    def _set_sports_only_mode(self, enabled):
        old_mode = kw.AION_SPORTS_ONLY_MODE
        kw.AION_SPORTS_ONLY_MODE = bool(enabled)
        self.addCleanup(setattr, kw, "AION_SPORTS_ONLY_MODE", old_mode)

    def _set_position_flip_config(self, *, enabled=True, min_edge_delta=0.08, min_loss=0.05, cooldown=120, allow_reenter=True):
        old_values = {
            "AION_POSITION_FLIP_ENABLED": kw.AION_POSITION_FLIP_ENABLED,
            "AION_POSITION_FLIP_MIN_EDGE_DELTA": kw.AION_POSITION_FLIP_MIN_EDGE_DELTA,
            "AION_POSITION_FLIP_MIN_UNREALIZED_LOSS": kw.AION_POSITION_FLIP_MIN_UNREALIZED_LOSS,
            "AION_POSITION_FLIP_COOLDOWN_SECONDS": kw.AION_POSITION_FLIP_COOLDOWN_SECONDS,
            "AION_POSITION_FLIP_ALLOW_REENTER": kw.AION_POSITION_FLIP_ALLOW_REENTER,
        }
        kw.AION_POSITION_FLIP_ENABLED = bool(enabled)
        kw.AION_POSITION_FLIP_MIN_EDGE_DELTA = float(min_edge_delta)
        kw.AION_POSITION_FLIP_MIN_UNREALIZED_LOSS = float(min_loss)
        kw.AION_POSITION_FLIP_COOLDOWN_SECONDS = int(cooldown)
        kw.AION_POSITION_FLIP_ALLOW_REENTER = bool(allow_reenter)
        for name, value in old_values.items():
            self.addCleanup(setattr, kw, name, value)

    def _configure_position_flip_bot(self, bot, family="KXATPCHALLENGERMATCH"):
        bot.live_exit_price_history = {}
        bot.dynamic_exit_log_markers = {}
        bot.profit_lock_log_markers = {}
        bot.sports_harvest_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.position_flip_cooldowns = {}
        bot.position_flip_log_markers = {}
        bot.live_exit_cooldowns = {}
        bot.live_exit_pending_tickers = set()
        bot.get_live_entry_metadata = lambda _ticker: {}
        bot.get_live_market_detail = lambda _ticker: {}
        bot.get_market_family = lambda _market: family
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": 0.0}
        return bot

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

    def test_extract_position_summary_uses_kalshi_position_fp(self):
        bot = self._make_bot()
        with contextlib.redirect_stdout(io.StringIO()):
            summary = bot.extract_position_summary(
                {
                    "ticker": "KXMLBSPREAD-26MAY311920CHCSTL-STL5",
                    "position_fp": "5.00",
                    "market_exposure_dollars": "1.570000",
                    "realized_pnl_dollars": "0.000000",
                }
            )
        self.assertEqual(summary["ticker"], "KXMLBSPREAD-26MAY311920CHCSTL-STL5")
        self.assertEqual(summary["side"], "yes")
        self.assertEqual(summary["count"], 5.0)
        self.assertEqual(summary["status"], "open")
        self.assertAlmostEqual(summary["avg_price"], 0.314)
        self.assertEqual(summary["realized_pnl"], 0.0)
        self.assertAlmostEqual(summary["market_exposure_dollars"], 1.57)

    def test_extract_order_execution_summary_uses_kalshi_count_fp_fields(self):
        bot = self._make_bot()
        with contextlib.redirect_stdout(io.StringIO()):
            summary = bot.extract_order_execution_summary(
                {
                    "order": {
                        "id": "order-1",
                        "market_ticker": "KXMLBHRRBI-26JUN01ARISEA-PERDOMO3",
                        "side": "yes",
                        "action": "buy",
                        "yes_price": 47,
                        "initial_count_fp": "4.00",
                        "fill_count_fp": "2.00",
                        "remaining_count_fp": "2.00",
                        "status": "partially_filled",
                    }
                }
            )
        self.assertEqual(summary["ticker"], "KXMLBHRRBI-26JUN01ARISEA-PERDOMO3")
        self.assertEqual(summary["side"], "yes")
        self.assertEqual(summary["action"], "buy")
        self.assertAlmostEqual(summary["price"], 0.47)
        self.assertEqual(summary["filled_count"], 2.0)
        self.assertEqual(summary["initial_count"], 4.0)
        self.assertEqual(summary["remaining_count"], 2.0)

    def test_sizing_account_value_treats_kalshi_balance_as_cents(self):
        bot = self._make_bot()
        bot.live_balance = {"balance": 7174, "portfolio_value": 47, "exchange_index": 0}
        account_value = sli._aion_sizing_account_value(bot)
        self.assertAlmostEqual(account_value, 72.21)

    def test_position_sizing_cap_includes_existing_same_game_exposure(self):
        bot = self._make_bot()
        bot.live_positions = [
            {
                "ticker": "KXMLBHRRBI-26JUN01ARISEA-PERDOMO3",
                "family": "KXMLBHRRBI",
                "status": "open",
                "count": 1,
                "avg_price": 0.47,
            }
        ]
        bot.reconcile_live_positions = lambda: {"ok": True, "positions": bot.live_positions}
        q = SimpleNamespace(
            ticker="KXMLBHRRBI-26JUN01ARISEA-RODRIGUEZ2",
            family="KXMLBHRRBI",
            selected_side="yes",
            selected_price=0.47,
            unified_opportunity_score=70,
            edge_after_cost=0.03,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            adjusted, details = sli._apply_position_sizing_intelligence(
                bot,
                q,
                planned_size=2,
                selected_price=0.47,
                payload={"ticker": q.ticker, "family": q.family, "side": "yes"},
                ev_context={"account_value": 10.0, "ticker": q.ticker, "family": q.family},
                hard_max=5,
                source="unit_test",
            )
        self.assertEqual(adjusted, 0)
        self.assertEqual(details["existing_exposure_dollars"], 0.47)
        self.assertEqual(details["reason"], "standard_same_thesis_account_pct_cap_exhausted")

    def test_reduce_only_live_order_uses_ioc_time_in_force(self):
        bot = self._make_bot()
        normalized = bot._normalize_legacy_order_payload(
            {
                "ticker": "KXNCAABBGAME-26MAY312100TAMUSC-USC",
                "client_order_id": "unit-reduce-only",
                "side": "yes",
                "action": "sell",
                "yes_price": 58,
                "count": 2,
                "post_only": True,
                "reduce_only": True,
            }
        )
        self.assertEqual(normalized["time_in_force"], "immediate_or_cancel")
        self.assertFalse(normalized["post_only"])
        self.assertTrue(normalized["reduce_only"])
        invalid = dict(normalized, time_in_force="fill_or_kill")
        self.assertEqual(
            kw.kalshi_execution_module.validate_canonical_order_payload(invalid)["reason"],
            "reduce_only_requires_ioc",
        )

    def test_live_monitor_reconciles_after_submitted_exit(self):
        bot = self._make_bot()
        bot.live_positions = [{"ticker": "KXATPCHALLENGERMATCH-26JUN01MATIMA-MAT", "status": "open", "count": 1}]
        reconcile_calls = []

        def evaluate_exit(position, quotes_by_ticker=None):
            return {"ticker": position["ticker"], "action": "exit", "reason": "unit_exit"}

        def submit_exit(position, decision):
            return {"submitted": True, "reason": "submitted"}

        def reconcile():
            reconcile_calls.append(True)
            bot.live_positions = []
            return {"ok": True, "positions": []}

        bot._evaluate_dynamic_live_exit = evaluate_exit
        bot.submit_live_exit_order = submit_exit
        bot.reconcile_live_positions = reconcile

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot.run_live_monitor_positions_pass(allow_real_exits=True)

        self.assertEqual(result["exit_orders_submitted"], 1)
        self.assertEqual(len(reconcile_calls), 1)
        self.assertTrue(result["post_exit_reconcile"]["ok"])
        self.assertEqual(bot._get_open_live_positions(), [])

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
                        '[AION-SPORTS-SUBMIT-ATTEMPT] {"ticker":"KXNCAABSPREAD-PURMICH","reason":"unit_test"}'
                    )
                )
                self.assertTrue(
                    kw._should_emit_module_log(
                        "[LIVE-SELECT-DECISION] final_trade_path chosen_ticker=KXNCAABSPREAD-PURMICH family=KXNCAABSPREAD"
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
                kw.print('[SPORTS-CONVICTION-SCORE] {"ticker":"KXNCAABSPREAD-PURMICH"}')
                kw.print('[AION-SPORTS-SUBMIT-ATTEMPT] {"ticker":"KXNCAABSPREAD-PURMICH","reason":"unit_test"}')
                kw.print('[POSITION-OPEN] {"ticker":"KXNCAABSPREAD-PURMICH"}')
                kw.print('[AION-FINAL-SUBMIT-BLOCKED] {"ticker":"KXNCAABTOTAL-PURMICH","reason":"duplicate_guard"}')
                kw.print('[AION-FINAL-GATE-BLOCKER] {"ticker":"KXNCAABSPREAD-PURMICH","reason":"all_guards_passed"}')
                kw.print("[AION-FINAL-BLOCKER-TRACE] ticker=KXNCAABTOTAL-PURMICH final_block_reason=unsafe_price")
                kw.print('[LIVE-REACTIVATION-WATCHLIST] {"ticker":"KXNCAABSPREAD-PURMICH","state":"unchanged"}')
                kw.print('[AION-RUNONCE-START] {"cycle_count":102,"timestamp":1779694360.0}')

            rendered = out.getvalue()
            self.assertIn("[AION-CYCLE-EXECUTION-SUMMARY]", rendered)
            self.assertIn('"sports_candidates_scanned": 1', rendered)
            self.assertIn('"sports_qualified_count": 1', rendered)
            self.assertIn('"positions_opened": 1', rendered)
            self.assertIn('"duplicate_guard": 1', rendered)
            self.assertIn('"unsafe_price": 1', rendered)
            self.assertNotIn('"all_guards_passed":', rendered)
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

    def test_lane_selected_price_derives_missing_side_from_bidirectional_selector(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNCAABSPREAD-SIDELESS",
            title="College basketball spread",
            yes_bid=0.40,
            yes_ask=0.42,
            no_bid=0.56,
            no_ask=0.58,
            yes_top_size=20,
            no_top_size=20,
            liquidity=40,
        )

        selected_side, selected_price = bot._extract_lane_selected_price(
            q,
            {"yes_edge_after_cost": 0.04, "no_edge_after_cost": -0.01},
            {},
            market={"ticker": q.ticker, "series_ticker": "KXNCAABSPREAD", "title": q.title},
            family="KXNCAABSPREAD",
        )

        self.assertEqual(selected_side, "yes")
        self.assertEqual(selected_price, 0.42)
        self.assertEqual(q.selected_side, "yes")
        self.assertEqual(q.selected_price, 0.42)
        self.assertEqual(q.selected_price_cents, 42)

    def test_candidate_quality_preserves_cent_price_units(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXATPCHALLENGERMATCH-UNIT",
            family="KXATPCHALLENGERMATCH",
            selected_side="yes",
            selected_price=0.80,
            selected_price_dollars=0.80,
            selected_price_cents=80,
            yes_bid=0.74,
            yes_ask=0.80,
            no_bid=0.18,
            no_ask=0.24,
            liquidity=100,
        )

        quality = bot._evaluate_aion_candidate_quality(
            market={"ticker": q.ticker, "series_ticker": q.family},
            q=q,
            expectancy_summary={"quote_ready": True, "payload_ready": True},
            tradeability={"orderbook_ready": True},
        )

        self.assertEqual(quality["selected_price"], 0.80)
        self.assertEqual(quality["selected_price_cents"], 80)
        self.assertTrue(quality["selected_price_in_bounds"])
        self.assertTrue(quality["selected_side_executable"])
        self.assertNotEqual(quality["preferred_reason"], "selected_price_out_of_range")

    def test_lane_selected_price_accepts_cents_field(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXATPCHALLENGERMATCH-CENTS",
            selected_side="yes",
            selected_price_cents=46,
        )

        selected_side, selected_price = bot._extract_lane_selected_price(
            q,
            {},
            {},
            market={"ticker": q.ticker, "series_ticker": "KXATPCHALLENGERMATCH"},
            family="KXATPCHALLENGERMATCH",
        )

        self.assertEqual(selected_side, "yes")
        self.assertEqual(selected_price, 0.46)

    def test_momentum_engine_derives_side_from_selected_price(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXATPCHALLENGERMATCH-MOMENTUM",
            selected_price_cents=80,
            yes_bid=0.74,
            yes_ask=0.80,
            no_bid=0.18,
            no_ask=0.24,
            price_history=[0.76, 0.80],
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = get_momentum_rank_adjustment(
                bot,
                {"q": q, "selected_price_cents": 80, "price_history": [0.76, 0.80]},
                market={"ticker": q.ticker},
                family="KXATPCHALLENGERMATCH",
                imbalance_context={"imbalance": 0.15},
                log=True,
            )

        self.assertEqual(result["side"], "yes")
        self.assertNotIn("unknown_side_neutral", result["reason"])
        self.assertIn("side=yes", out.getvalue())

    def test_sports_side_selection_allows_one_sided_thin_executable_book(self):
        bot = self._make_bot()
        decision = bot._aion_select_bidirectional_side(
            ticker="KXNBA-ONESIDED-TEST",
            family="KXNBA",
            lane="SPORTS",
            market={
                "ticker": "KXNBA-ONESIDED-TEST",
                "series_ticker": "KXNBA",
                "selected_side": "yes",
                "selected_price": 0.52,
                "title": "NBA game winner",
            },
            yes_bid=None,
            yes_ask=None,
            no_bid=None,
            no_ask=None,
            yes_top_size=0,
            no_top_size=0,
            preferred_side="yes",
            quote_diag={"selected_side": "yes", "selected_price": 0.52},
            source="unit_test",
        )

        self.assertEqual(decision["selected_side"], "yes")
        self.assertEqual(decision["selected_price"], 0.52)
        self.assertEqual(decision["reason"], "sports_one_sided_yes_executable")
        self.assertTrue(decision["yes_executable"])

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

    def test_missing_side_refresh_uses_bidirectional_selector_for_sports_payload(self):
        bot = self._make_bot()
        ticker = "KXNBASPREAD-SIDELESS"
        q = SimpleNamespace(ticker=ticker, title="NBA spread")
        market = {"ticker": ticker, "series_ticker": "KXNBASPREAD", "title": "NBA spread"}
        bot.get_live_market_orderbook = lambda requested_ticker: {
            "ticker": requested_ticker,
            "yes_bid": 0.40,
            "yes_ask": 0.42,
            "no_bid": 0.56,
            "no_ask": 0.58,
            "yes_top_size": 12,
            "no_top_size": 9,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            resolution = bot.resolve_aion_orderbook_for_payload(
                ticker,
                candidate=q,
                market=market,
                quote_diag={"yes_edge_after_cost": 0.04, "no_edge_after_cost": -0.01},
                price_source="ask",
                selection_stage="quote_hydration",
                allow_refresh=True,
            )

        self.assertTrue(resolution["orderbook_fetch_called"])
        self.assertTrue(resolution["orderbook_ready"])
        self.assertEqual(resolution["selected_side"], "yes")
        self.assertEqual(resolution["selected_price"], 0.42)
        self.assertEqual(resolution["selected_price_cents"], 42)

        with contextlib.redirect_stdout(io.StringIO()):
            payload = bot.build_live_order_payload(
                q,
                size=1,
                market=market,
                quote_diag={"yes_edge_after_cost": 0.04, "no_edge_after_cost": -0.01},
                orderbook_resolution=resolution,
                allow_orderbook_refresh=False,
            )

        self.assertEqual(payload["side"], "yes")
        self.assertEqual(payload["selected_side"], "yes")
        self.assertEqual(payload["yes_price"], 42)
        self.assertNotIn("no_price", payload)

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
            {"ticker": "KXITFMATCH-26MAY26CLAJOH-CLA", "series_ticker": "KXITFMATCH", "title": "Clarke vs Johnson tennis match"},
        ]

        filtered = bot._filter_sports_only_markets(markets, source="unit_test_discovery", force_log=True)

        self.assertEqual([market["ticker"] for market in filtered], ["KXNCAABSPREAD-PURMICH", "KXMLBMONEYLINE-NYYBOS", "KXITFMATCH-26MAY26CLAJOH-CLA"])

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
        self.assertEqual(summary["reason"], "sports_only_mode_crypto_discovery_removed")

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

    def test_submit_preserves_canonical_payload_resolved_size(self):
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
        bot._resolve_dynamic_live_entry_size = lambda *args, **kwargs: 0

        def fake_place_live_order(payload):
            captured_payloads.append(dict(payload))
            return {
                "submitted": True,
                "reason": "submitted",
                "endpoint": "/trade-api/v2/portfolio/orders",
                "payload": dict(payload),
                "response": {"order_id": "unit-sized"},
                "status_code": 201,
            }

        bot.place_live_order = fake_place_live_order
        q = SimpleNamespace(
            ticker="KXNCAABTOTAL-PURMICH-YES",
            family="KXNCAABTOTAL",
            selected_side="yes",
            selected_price=0.54,
        )

        result = bot.submit_live_order(
            q,
            size=1,
            lane="SPORTS",
            payload_override={
                "ticker": q.ticker,
                "family": q.family,
                "side": "yes",
                "selected_side": "yes",
                "selected_price": 0.54,
                "price": 0.54,
                "count": 1,
                "_aion_sizing_resolved": True,
            },
        )

        self.assertTrue(result["submitted"])
        self.assertEqual(captured_payloads[0]["count"], 1)
        self.assertEqual(captured_payloads[0]["yes_price"], 54)

    def test_canonical_place_order_blocks_opposite_side_before_post(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        posted = []
        bot.live_positions = [
            {
                "ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-MIL",
                "family": "KXATPCHALLENGERMATCH",
                "side": "yes",
                "count": 2,
                "entry_price": 0.60,
            }
        ]
        bot.load_live_position_state = lambda: {}
        bot.reconcile_live_positions = lambda: {"ok": True, "positions": bot.live_positions}

        def fake_live_request(method, path, json_payload=None, timeout=15):
            if method == "GET" and "balance" in str(path):
                return {"ok": True, "status_code": 200, "body": {"balance": 10000}}
            posted.append(dict(json_payload or {}))
            return {
                "ok": True,
                "status_code": 201,
                "body": {"order": {"order_id": "should-not-post"}},
            }

        bot._live_request_json = fake_live_request

        result = bot.place_live_order(
            {
                "ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-LAN",
                "family": "KXATPCHALLENGERMATCH",
                "side": "yes",
                "action": "buy",
                "count": 2,
                "yes_price": 46,
                "selected_price": 0.46,
                "_risk_governor_checked": True,
            }
        )

        self.assertFalse(result["submitted"])
        self.assertEqual(result["reason"], "opposite_side_conflict")
        self.assertEqual(posted, [])

    def test_canonical_place_order_allows_reduce_only_exit_with_conflict(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        posted = []
        bot.live_positions = [
            {
                "ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-MIL",
                "family": "KXATPCHALLENGERMATCH",
                "side": "yes",
                "count": 2,
                "entry_price": 0.60,
            }
        ]
        bot.load_live_position_state = lambda: {}
        bot.reconcile_live_positions = lambda: {"ok": True, "positions": bot.live_positions}

        def fake_live_request(method, path, json_payload=None, timeout=15):
            if method == "GET" and "balance" in str(path):
                return {"ok": True, "status_code": 200, "body": {"balance": 10000}}
            posted.append(dict(json_payload or {}))
            return {
                "ok": True,
                "status_code": 201,
                "body": {"order": {"order_id": "unit-exit"}},
            }

        bot._live_request_json = fake_live_request

        result = bot.place_live_order(
            {
                "ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-LAN",
                "family": "KXATPCHALLENGERMATCH",
                "side": "yes",
                "action": "sell",
                "count": 2,
                "yes_price": 40,
                "selected_price": 0.40,
                "reduce_only": True,
                "_risk_governor_checked": True,
            }
        )

        self.assertTrue(result["submitted"])
        self.assertEqual(posted[0]["action"], "sell")
        self.assertTrue(posted[0]["reduce_only"])
        self.assertEqual(posted[0]["time_in_force"], "immediate_or_cancel")

    def test_canonical_place_order_keeps_one_sided_payload_shape(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        posted = []
        bot.live_positions = []
        bot.load_live_position_state = lambda: {}
        bot.reconcile_live_positions = lambda: {"ok": True, "positions": []}

        def fake_live_request(method, path, json_payload=None, timeout=15):
            if method == "GET" and "balance" in str(path):
                return {"ok": True, "status_code": 200, "body": {"balance": 10000}}
            posted.append(dict(json_payload or {}))
            return {
                "ok": True,
                "status_code": 201,
                "body": {"order": {"order_id": "unit-one-sided"}},
            }

        bot._live_request_json = fake_live_request

        result = bot.place_live_order(
            {
                "ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-LAN",
                "family": "KXATPCHALLENGERMATCH",
                "side": "yes",
                "action": "buy",
                "count": 2,
                "yes_price": 46,
                "selected_price": 0.46,
                "_risk_governor_checked": True,
            }
        )

        self.assertTrue(result["submitted"])
        self.assertEqual(posted[0]["ticker"], "KXATPCHALLENGERMATCH-26JUN01MILLAN-LAN")
        self.assertEqual(posted[0]["side"], "yes")
        self.assertEqual(posted[0]["action"], "buy")
        self.assertEqual(posted[0]["count"], 2)
        self.assertEqual(posted[0]["yes_price"], 46)
        self.assertEqual(posted[0]["time_in_force"], "immediate_or_cancel")
        self.assertNotIn("no_price", posted[0])

    def test_sports_live_state_guard_blocks_future_start_time(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        posted = []
        future_start = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat().replace("+00:00", "Z")
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXATPCHALLENGERMATCH",
            "status": "active",
            "start_time": future_start,
        }
        bot.live_positions = []
        bot.load_live_position_state = lambda: {}
        bot.reconcile_live_positions = lambda: {"ok": True, "positions": []}

        def fake_live_request(method, path, json_payload=None, timeout=15):
            if method == "GET" and "balance" in str(path):
                return {"ok": True, "status_code": 200, "body": {"balance": 10000}}
            posted.append(dict(json_payload or {}))
            return {
                "ok": True,
                "status_code": 201,
                "body": {"order": {"order_id": "should-not-post"}},
            }

        bot._live_request_json = fake_live_request

        result = bot.place_live_order(
            {
                "ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-LAN",
                "family": "KXATPCHALLENGERMATCH",
                "side": "yes",
                "action": "buy",
                "count": 2,
                "yes_price": 46,
                "selected_price": 0.46,
                "_risk_governor_checked": True,
            }
        )

        self.assertFalse(result["submitted"])
        self.assertEqual(result["reason"], "pregame_sports_market")
        self.assertEqual(result["sports_live_state_guard"]["block_reason"], "future_start_time")
        self.assertEqual(posted, [])

    def test_sports_live_state_guard_allows_atp_live_flag_despite_future_occurrence(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        future_occurrence = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat().replace("+00:00", "Z")
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXATPCHALLENGERMATCH",
            "status": "active",
            "event_status": "in_play",
            "occurrence_datetime": future_occurrence,
            "expected_expiration_time": future_occurrence,
        }

        result = bot.evaluate_sports_live_state_pre_submit_guard(
            {
                "ticker": "KXATPCHALLENGERMATCH-26JUN01BIRM-LIVE",
                "family": "KXATPCHALLENGERMATCH",
                "side": "yes",
                "action": "buy",
                "count": 1,
                "yes_price": 44,
                "selected_price": 0.44,
            },
            source="unit_test",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "sports_live_state_ok")
        self.assertEqual(result["occurrence_key"], "occurrence_datetime")

    def test_sports_live_state_guard_allows_itf_scoreboard_despite_future_occurrence(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        future_occurrence = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXITFMATCH",
            "status": "active",
            "occurrence_datetime": future_occurrence,
            "scoreboard": {"set": 2, "game_status": "in_progress", "clock": "live"},
        }

        result = bot.evaluate_sports_live_state_pre_submit_guard(
            {
                "ticker": "KXITFMATCH-26JUN01ITF-LIVE",
                "family": "KXITFMATCH",
                "side": "no",
                "action": "buy",
                "count": 1,
                "no_price": 57,
                "selected_price": 0.57,
                "selected_side": "no",
            },
            source="unit_test",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "sports_live_state_ok")
        self.assertEqual(result["scoreboard_key"], "game_status")

    def test_sports_live_state_guard_blocks_future_occurrence_without_live_signal(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        future_occurrence = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXITFMATCH",
            "status": "active",
            "occurrence_datetime": future_occurrence,
            "expected_expiration_time": future_occurrence,
        }

        result = bot.evaluate_sports_live_state_pre_submit_guard(
            {
                "ticker": "KXITFMATCH-26JUN01ITF-OPEN",
                "family": "KXITFMATCH",
                "side": "yes",
                "action": "buy",
                "count": 1,
                "yes_price": 44,
                "selected_price": 0.44,
                "orderbook_ready": True,
                "selected_side_has_liquidity": True,
            },
            source="unit_test",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["block_reason"], "future_occurrence_no_live_evidence")

    def test_sports_live_state_guard_blocks_timezone_offset_future_start(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        future_dt = datetime.now(timezone.utc) + timedelta(hours=2)
        future_start = future_dt.astimezone(timezone(timedelta(hours=-4))).isoformat()
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXATPCHALLENGERMATCH",
            "status": "active",
            "start_time": future_start,
        }

        result = bot.evaluate_sports_live_state_pre_submit_guard(
            {
                "ticker": "KXATPCHALLENGERMATCH-26JUN01TZ-FUT",
                "family": "KXATPCHALLENGERMATCH",
                "side": "yes",
                "action": "buy",
                "count": 1,
                "yes_price": 44,
                "selected_price": 0.44,
            },
            source="unit_test",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["block_reason"], "future_start_time")

    def test_sports_live_state_guard_blocks_missing_live_metadata(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        bot.get_live_market_detail = lambda ticker: {}

        result = bot.evaluate_sports_live_state_pre_submit_guard(
            {
                "ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-LAN",
                "family": "KXATPCHALLENGERMATCH",
                "side": "yes",
                "action": "buy",
                "count": 1,
                "yes_price": 44,
                "selected_price": 0.44,
            },
            source="unit_test",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "pregame_sports_market")
        self.assertEqual(result["block_reason"], "unknown_sports_live_state")

    def test_sports_live_state_guard_allows_missing_start_with_executable_book(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        posted = []
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXMLBSPREAD",
            "status": "open",
        }
        bot.live_positions = []
        bot.load_live_position_state = lambda: {}
        bot.reconcile_live_positions = lambda: {"ok": True, "positions": []}

        def fake_live_request(method, path, json_payload=None, timeout=15):
            if method == "GET" and "balance" in str(path):
                return {"ok": True, "status_code": 200, "body": {"balance": 10000}}
            posted.append(dict(json_payload or {}))
            return {
                "ok": True,
                "status_code": 201,
                "body": {"order": {"order_id": "unit-live-book"}},
            }

        bot._live_request_json = fake_live_request

        result = bot.place_live_order(
            {
                "ticker": "KXMLBSPREAD-26JUN011840DETTB-DET2",
                "family": "KXMLBSPREAD",
                "side": "yes",
                "action": "buy",
                "count": 1,
                "yes_price": 49,
                "selected_price": 0.49,
                "selected_side": "yes",
                "orderbook_ready": True,
                "selected_side_has_liquidity": True,
                "_risk_governor_checked": True,
            }
        )

        self.assertTrue(result["submitted"])
        self.assertEqual(posted[0]["ticker"], "KXMLBSPREAD-26JUN011840DETTB-DET2")
        self.assertEqual(posted[0]["side"], "yes")
        self.assertEqual(posted[0]["yes_price"], 49)
        self.assertNotIn("no_price", posted[0])

    def test_sports_live_state_guard_blocks_future_even_with_executable_book(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        posted = []
        future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXMLBSPREAD",
            "status": "open",
            "occurrence_datetime": future_start,
        }
        bot.live_positions = []
        bot.load_live_position_state = lambda: {}
        bot.reconcile_live_positions = lambda: {"ok": True, "positions": []}

        def fake_live_request(method, path, json_payload=None, timeout=15):
            if method == "GET" and "balance" in str(path):
                return {"ok": True, "status_code": 200, "body": {"balance": 10000}}
            posted.append(dict(json_payload or {}))
            return {
                "ok": True,
                "status_code": 201,
                "body": {"order": {"order_id": "should-not-post"}},
            }

        bot._live_request_json = fake_live_request

        result = bot.place_live_order(
            {
                "ticker": "KXMLBSPREAD-26JUN020100FUTURE-HOME2",
                "family": "KXMLBSPREAD",
                "side": "yes",
                "action": "buy",
                "count": 1,
                "yes_price": 49,
                "selected_price": 0.49,
                "selected_side": "yes",
                "orderbook_ready": True,
                "selected_side_has_liquidity": True,
                "_risk_governor_checked": True,
            }
        )

        self.assertFalse(result["submitted"])
        self.assertEqual(result["reason"], "pregame_sports_market")
        self.assertEqual(result["sports_live_state_guard"]["block_reason"], "future_start_time")
        self.assertEqual(posted, [])

    def test_sports_live_state_guard_allows_elapsed_start_with_executable_book(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        past_start = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat().replace("+00:00", "Z")
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXMLBTOTAL",
            "status": "open",
            "occurrence_datetime": past_start,
        }

        result = bot.evaluate_sports_live_state_pre_submit_guard(
            {
                "ticker": "KXMLBTOTAL-26JUN011840DETTB-8",
                "family": "KXMLBTOTAL",
                "side": "no",
                "action": "buy",
                "count": 1,
                "no_price": 52,
                "selected_price": 0.52,
                "selected_side": "no",
                "orderbook_ready": True,
                "selected_side_has_liquidity": True,
            },
            source="unit_test",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "sports_live_state_ok")
        self.assertEqual(result["side"], "no")
        self.assertEqual(result["selected_price"], 0.52)

    def test_sports_live_state_guard_allows_live_scoreboard(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        posted = []
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXNBAGAME",
            "status": "open",
            "scoreboard": {"game_status": "in_progress", "quarter": 2, "clock": "07:41"},
        }
        bot.live_positions = []
        bot.load_live_position_state = lambda: {}
        bot.reconcile_live_positions = lambda: {"ok": True, "positions": []}

        def fake_live_request(method, path, json_payload=None, timeout=15):
            if method == "GET" and "balance" in str(path):
                return {"ok": True, "status_code": 200, "body": {"balance": 10000}}
            posted.append(dict(json_payload or {}))
            return {
                "ok": True,
                "status_code": 201,
                "body": {"order": {"order_id": "unit-live-scoreboard"}},
            }

        bot._live_request_json = fake_live_request

        result = bot.place_live_order(
            {
                "ticker": "KXNBAGAME-26JUN01-BOSNYK-BOS",
                "family": "KXNBAGAME",
                "side": "yes",
                "action": "buy",
                "count": 1,
                "yes_price": 44,
                "selected_price": 0.44,
                "_risk_governor_checked": True,
            }
        )

        self.assertTrue(result["submitted"])
        self.assertEqual(posted[0]["ticker"], "KXNBAGAME-26JUN01-BOSNYK-BOS")
        self.assertEqual(posted[0]["side"], "yes")
        self.assertEqual(posted[0]["yes_price"], 44)
        self.assertNotIn("no_price", posted[0])

    def test_sports_live_state_guard_allows_in_play_flag(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXNBAGAME",
            "in_play": True,
            "status": "open",
        }

        result = bot.evaluate_sports_live_state_pre_submit_guard(
            {
                "ticker": "KXNBAGAME-26JUN01-BOSNYK-BOS",
                "family": "KXNBAGAME",
                "side": "yes",
                "action": "buy",
                "count": 1,
                "yes_price": 44,
                "selected_price": 0.44,
            },
            source="unit_test",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "sports_live_state_ok")

    def test_sports_live_state_guard_skips_non_sports(self):
        bot = self._make_bot()
        bot.get_live_market_detail = lambda ticker: {}

        result = bot.evaluate_sports_live_state_pre_submit_guard(
            {
                "ticker": "KXHIGHAUS-26JUN01",
                "family": "KXHIGHAUS",
                "side": "yes",
                "action": "buy",
                "count": 1,
                "yes_price": 44,
                "selected_price": 0.44,
            },
            source="unit_test",
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "not_sports_buy")

    def test_live_discovery_drops_future_sports_before_ranking(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        future_start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        live_market = {
            "ticker": "KXITFWMATCH-26JUN01LIVE-LIVE",
            "series_ticker": "KXITFWMATCH",
            "title": "ITF live match",
            "status": "active",
            "game_status": "in_progress",
            "yes_ask": 0.48,
            "yes_bid": 0.45,
            "volume": 200,
        }
        future_market = {
            "ticker": "KXATPCHALLENGERMATCH-26JUN02FUT-FUT",
            "series_ticker": "KXATPCHALLENGERMATCH",
            "title": "ATP Challenger tomorrow",
            "status": "active",
            "occurrence_datetime": future_start,
            "yes_ask": 0.42,
            "yes_bid": 0.39,
            "volume": 500,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            admitted, summary = bot._filter_live_sports_discovery_markets(
                [future_market, live_market],
                source="unit_test",
                limit=10,
            )

        self.assertEqual([market["ticker"] for market in admitted], [live_market["ticker"]])
        self.assertEqual(summary["dropped_future_or_pregame"], 1)
        self.assertEqual(summary["admitted_count"], 1)

    def test_live_discovery_drops_future_ticker_date_without_metadata(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        token = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%y%b%d").upper()
        future_market = {
            "ticker": f"KXATPCHALLENGERMATCH-{token}UNIT-FUT",
            "series_ticker": "KXATPCHALLENGERMATCH",
            "title": "ATP Challenger future without occurrence metadata",
            "status": "active",
            "yes_ask": 0.42,
            "yes_bid": 0.39,
            "volume": 500,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            admitted, summary = bot._filter_live_sports_discovery_markets(
                [future_market],
                source="unit_test",
                limit=10,
            )

        self.assertEqual(admitted, [])
        self.assertEqual(summary["dropped_future_or_pregame"], 1)
        self.assertEqual(summary["reason_counts"]["future_start_time"], 1)

    def test_live_discovery_admits_ambiguous_ticker_date_with_executable_book(self):
        self._set_sports_only_mode(True)
        old_window = kw.AION_TICKER_DATE_LIVE_AMBIGUITY_SECONDS
        kw.AION_TICKER_DATE_LIVE_AMBIGUITY_SECONDS = 48 * 60 * 60
        self.addCleanup(setattr, kw, "AION_TICKER_DATE_LIVE_AMBIGUITY_SECONDS", old_window)
        bot = self._make_bot()
        token = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%y%b%d").upper()
        live_market = {
            "ticker": f"KXITFMATCH-{token}UNIT-LIVE",
            "series_ticker": "KXITFMATCH",
            "title": "ITF ambiguous date live market",
            "status": "active",
            "yes_ask": 0.47,
            "yes_bid": 0.45,
            "no_ask": 0.55,
            "no_bid": 0.53,
            "yes_top_size": 12,
            "no_top_size": 14,
            "volume": 500,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            admitted, summary = bot._filter_live_sports_discovery_markets(
                [live_market],
                source="unit_test",
                limit=10,
            )

        self.assertEqual([market["ticker"] for market in admitted], [live_market["ticker"]])
        self.assertEqual(summary["admitted_count"], 1)
        self.assertEqual(summary["reason_counts"]["fresh_executable_orderbook_ambiguous_ticker_date"], 1)

    def test_live_discovery_hydrates_ambiguous_ticker_date_before_drop(self):
        self._set_sports_only_mode(True)
        old_window = kw.AION_TICKER_DATE_LIVE_AMBIGUITY_SECONDS
        old_limit = kw.AION_LIVE_DISCOVERY_AMBIGUOUS_HYDRATION_LIMIT
        kw.AION_TICKER_DATE_LIVE_AMBIGUITY_SECONDS = 48 * 60 * 60
        kw.AION_LIVE_DISCOVERY_AMBIGUOUS_HYDRATION_LIMIT = 4
        self.addCleanup(setattr, kw, "AION_TICKER_DATE_LIVE_AMBIGUITY_SECONDS", old_window)
        self.addCleanup(setattr, kw, "AION_LIVE_DISCOVERY_AMBIGUOUS_HYDRATION_LIMIT", old_limit)
        bot = self._make_bot()
        token = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%y%b%d").upper()
        thin_live_market = {
            "ticker": f"KXITFWMATCH-{token}UNIT-HYDRATE",
            "series_ticker": "KXITFWMATCH",
            "title": "ITF ambiguous date list payload",
            "status": "active",
            "volume": 500,
        }
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXITFWMATCH",
            "status": "active",
        }
        bot.get_live_market_orderbook = lambda ticker: {
            "yes_bid": 0.45,
            "yes_ask": 0.47,
            "no_bid": 0.53,
            "no_ask": 0.55,
            "yes_top_size": 8,
            "no_top_size": 9,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            admitted, summary = bot._filter_live_sports_discovery_markets(
                [thin_live_market],
                source="unit_test",
                limit=10,
            )

        self.assertEqual([market["ticker"] for market in admitted], [thin_live_market["ticker"]])
        self.assertEqual(summary["ambiguous_hydration_attempts"], 1)
        self.assertEqual(summary["ambiguous_hydration_success"], 1)

    def test_live_discovery_hydration_preserves_exact_future_metadata_block(self):
        self._set_sports_only_mode(True)
        old_window = kw.AION_TICKER_DATE_LIVE_AMBIGUITY_SECONDS
        old_limit = kw.AION_LIVE_DISCOVERY_AMBIGUOUS_HYDRATION_LIMIT
        kw.AION_TICKER_DATE_LIVE_AMBIGUITY_SECONDS = 48 * 60 * 60
        kw.AION_LIVE_DISCOVERY_AMBIGUOUS_HYDRATION_LIMIT = 4
        self.addCleanup(setattr, kw, "AION_TICKER_DATE_LIVE_AMBIGUITY_SECONDS", old_window)
        self.addCleanup(setattr, kw, "AION_LIVE_DISCOVERY_AMBIGUOUS_HYDRATION_LIMIT", old_limit)
        bot = self._make_bot()
        token = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%y%b%d").upper()
        exact_future = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat().replace("+00:00", "Z")
        thin_future_market = {
            "ticker": f"KXITFWMATCH-{token}UNIT-FUTURE",
            "series_ticker": "KXITFWMATCH",
            "title": "ITF exact future list payload",
            "status": "active",
            "volume": 500,
        }
        bot.get_live_market_detail = lambda ticker: {
            "ticker": ticker,
            "series_ticker": "KXITFWMATCH",
            "status": "active",
            "occurrence_datetime": exact_future,
        }
        bot.get_live_market_orderbook = lambda ticker: {
            "yes_bid": 0.45,
            "yes_ask": 0.47,
            "no_bid": 0.53,
            "no_ask": 0.55,
            "yes_top_size": 8,
            "no_top_size": 9,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            admitted, summary = bot._filter_live_sports_discovery_markets(
                [thin_future_market],
                source="unit_test",
                limit=10,
            )

        self.assertEqual(admitted, [])
        self.assertEqual(summary["ambiguous_hydration_attempts"], 1)
        self.assertEqual(summary["ambiguous_hydration_success"], 0)
        self.assertEqual(summary["dropped_future_or_pregame"], 1)

    def test_live_discovery_drops_future_ticker_even_with_ambiguous_elapsed_timestamp(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        future_token = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%y%b%d").upper()
        past_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        future_market = {
            "ticker": f"KXATPCHALLENGERMATCH-{future_token}UNIT-FUT",
            "series_ticker": "KXATPCHALLENGERMATCH",
            "title": "ATP Challenger future with ambiguous timestamp",
            "status": "active",
            "start_time": past_start,
            "yes_ask": 0.42,
            "yes_bid": 0.39,
            "volume": 500,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            admitted, summary = bot._filter_live_sports_discovery_markets(
                [future_market],
                source="unit_test",
                limit=10,
            )

        self.assertEqual(admitted, [])
        self.assertEqual(summary["dropped_future_or_pregame"], 1)
        self.assertEqual(summary["reason_counts"]["future_start_time"], 1)

    def test_live_discovery_admits_itf_atp_wta_match_series(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        exact_series = ("KXITFMATCH", "KXITFWMATCH", "KXATPMATCH", "KXWTAMATCH", "KXSQUASHMATCH")
        for family in exact_series:
            self.assertIn(family, kw.TARGETED_DISCOVERY_EXPANSION_SERIES_TICKERS)
            self.assertTrue(bot.is_sports_family(family, ticker=f"{family}-26JUN01LIVE-TEST"))

    def test_quote_hydration_propagates_canonical_side_and_price(self):
        bot = self._make_bot()
        quote = SimpleNamespace(
            ticker="KXATPMATCH-26JUN01TIAARN-TIA",
            family="KXATPMATCH",
            yes_bid=0.38,
            yes_ask=0.39,
            no_bid=0.6,
            no_ask=0.62,
            yes_top_size=100,
            no_top_size=80,
            selected_side=None,
            selected_price=None,
        )
        market = {
            "ticker": quote.ticker,
            "series_ticker": "KXATPMATCH",
            "status": "active",
            "yes_bid": 0.38,
            "yes_ask": 0.39,
            "no_bid": 0.6,
            "no_ask": 0.62,
            "yes_top_size": 100,
            "no_top_size": 80,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot._propagate_quote_hydration_selection(
                quote=quote,
                market=market,
                quote_diag={},
                source="unit_test",
            )

        self.assertTrue(result["applied"])
        self.assertIn(quote.selected_side, {"yes", "no"})
        self.assertIsNotNone(quote.selected_price)
        self.assertGreaterEqual(quote.selected_price_cents, 1)
        self.assertEqual(market["selected_side"], quote.selected_side)

    def test_live_discovery_missing_start_with_scoreboard_is_admitted(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        market = {
            "ticker": "KXATPCHALLENGERMATCH-26JUN01BIRM-LIVE",
            "series_ticker": "KXATPCHALLENGERMATCH",
            "title": "ATP Challenger Birmingham live",
            "status": "active",
            "scoreboard": {"set": 2, "clock": "live"},
            "yes_ask": 0.57,
            "yes_bid": 0.53,
            "volume": 125,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            admitted, summary = bot._filter_live_sports_discovery_markets(
                [market],
                source="unit_test",
                limit=10,
            )

        self.assertEqual(len(admitted), 1)
        self.assertEqual(admitted[0]["ticker"], market["ticker"])
        self.assertEqual(summary["admitted_count"], 1)
        self.assertIn("scoreboard_live_state", summary["reason_counts"])

    def test_live_discovery_admits_live_tennis_despite_future_occurrence(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        future_occurrence = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat().replace("+00:00", "Z")
        market = {
            "ticker": "KXATPCHALLENGERMATCH-26JUN01BIRM-LIVE",
            "series_ticker": "KXATPCHALLENGERMATCH",
            "event_ticker": "KXATPCHALLENGERMATCH-26JUN01BIRM",
            "title": "ATP Challenger Birmingham live",
            "status": "active",
            "event_status": "in_play",
            "occurrence_datetime": future_occurrence,
            "expected_expiration_time": future_occurrence,
            "yes_ask": 0.57,
            "yes_bid": 0.53,
            "volume": 125,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            admitted, summary = bot._filter_live_sports_discovery_markets(
                [market],
                source="unit_test",
                limit=10,
            )

        self.assertEqual([item["ticker"] for item in admitted], [market["ticker"]])
        self.assertEqual(summary["admitted_count"], 1)
        self.assertIn("scoreboard_live_state", summary["reason_counts"])

    def test_live_discovery_expanded_scan_recovers_live_market_after_future_root(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        future_start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        future_market = {
            "ticker": "KXATPCHALLENGERMATCH-26JUN02ROOT-FUT",
            "series_ticker": "KXATPCHALLENGERMATCH",
            "status": "active",
            "occurrence_datetime": future_start,
            "yes_ask": 0.44,
            "yes_bid": 0.41,
        }
        live_market = {
            "ticker": "KXITFMATCH-26JUN01LIVE-RECOVERED",
            "series_ticker": "KXITFMATCH",
            "status": "active",
            "event_status": "in_play",
            "yes_ask": 0.52,
            "yes_bid": 0.49,
            "volume": 100,
        }

        batches = iter([([future_market, live_market], None)])
        bot.fetch_market_batch = lambda cursor=None: next(batches)
        bot.get_markets = lambda *args, **kwargs: []

        with contextlib.redirect_stdout(io.StringIO()):
            admitted, summary = bot.fetch_live_sports_discovery_markets(
                root_markets=[future_market],
                scan_depth=1,
            )

        self.assertEqual([market["ticker"] for market in admitted], [live_market["ticker"]])
        self.assertEqual(summary["root_summary"]["dropped_future_or_pregame"], 1)
        self.assertGreaterEqual(summary["unique_discovered"], 2)

    def test_sports_only_live_selector_admits_started_live_market(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        market = {
            "ticker": "KXATPMATCH-26JUN01LIVE-ONE",
            "series_ticker": "KXATPMATCH",
            "status": "active",
            "event_status": "in_play",
            "yes_bid": 0.24,
            "yes_ask": 0.25,
            "no_bid": 0.74,
            "no_ask": 0.75,
        }

        admitted, state = bot._aion_sports_only_live_selector_admission(
            market=market,
            selection_merge_source="provided_markets",
        )

        self.assertTrue(admitted)
        self.assertIn(state["reason"], {"live_status", "scoreboard_live_state", "explicit_live_flag"})

    def test_sports_only_live_selector_rejects_future_market(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        future_start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        market = {
            "ticker": "KXATPMATCH-26JUN02FUT-ONE",
            "series_ticker": "KXATPMATCH",
            "status": "active",
            "start_time": future_start,
            "yes_bid": 0.24,
            "yes_ask": 0.25,
            "no_bid": 0.74,
            "no_ask": 0.75,
        }

        admitted, state = bot._aion_sports_only_live_selector_admission(
            market=market,
            selection_merge_source="provided_markets",
        )

        self.assertFalse(admitted)
        self.assertEqual(state["reason"], "future_start_time")

    def test_sports_only_live_selector_retries_keep_authoritative_live_markets(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        live_markets = [
            {
                "ticker": "KXATPMATCH-26JUN01LIVE-ONE",
                "series_ticker": "KXATPMATCH",
                "status": "active",
                "event_status": "in_play",
                "yes_bid": 0.24,
                "yes_ask": 0.25,
                "no_bid": 0.74,
                "no_ask": 0.75,
            }
        ]
        old_retries = kw.MAX_DISCOVERY_RETRIES
        old_delay = kw.DISCOVERY_RETRY_DELAY_SECONDS
        kw.MAX_DISCOVERY_RETRIES = 1
        kw.DISCOVERY_RETRY_DELAY_SECONDS = 0
        self.addCleanup(setattr, kw, "MAX_DISCOVERY_RETRIES", old_retries)
        self.addCleanup(setattr, kw, "DISCOVERY_RETRY_DELAY_SECONDS", old_delay)
        calls = []

        def fake_find_best(markets=None):
            calls.append(markets)
            bot.last_live_select_summary = {
                "within_window": len(markets or []),
                "families_in_window": ["KXATPMATCH"] if markets else [],
                "candidates_found": 0,
            }
            return None, None

        bot.find_best_live_ticker = fake_find_best

        with contextlib.redirect_stdout(io.StringIO()):
            ticker, reason = bot.find_best_live_ticker_with_retries(markets=live_markets)

        self.assertIsNone(ticker)
        self.assertIsNone(reason)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], live_markets)
        self.assertEqual(calls[1], live_markets)
        self.assertIsNotNone(calls[1])

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

    def test_best_rung_selection_suppresses_far_tail_ladder_spam(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        game_context = {
            "game_id": "nba:kxnba_okcsas",
            "thesis_type": "favorite_dominance",
            "thesis_confidence": 84.0,
            "strong": True,
            "primary_candidate": "KXNBASPREAD-OKCSAS-015",
        }

        def candidate(ticker, threshold, price, score, edge):
            q = SimpleNamespace(
                ticker=ticker,
                family="KXNBASPREAD",
                selected_side="yes",
                selected_price=price,
                yes_top_size=80,
            )
            return {
                "ticker": ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {
                    "ticker": ticker,
                    "series_ticker": q.family,
                    "event_ticker": "KXNBA-OKCSAS",
                    "title": f"Thunder win by more than {threshold}",
                },
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": ticker,
                    "family": q.family,
                    "selected_side": "yes",
                    "selected_price": price,
                    "top_size": 80,
                    "allocation_score": score,
                    "unified_opportunity_score": score,
                    "edge_after_cost": edge,
                    "edge": edge,
                    "spread": 0.02,
                    "liquidity": 220,
                    "sports_execution_quality_score": 88.0,
                    "sports_execution_fill_probability": 0.76,
                    "family_profit_memory_adjustment": 2.0,
                    "sports_memory_rank_adjustment": 1.5,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_context["game_id"],
                    "game_thesis_type": game_context["thesis_type"],
                    "game_thesis_confidence": game_context["thesis_confidence"],
                    "game_thesis_context": dict(game_context),
                },
            }

        bot.last_dual_lane_buy_candidates = [
            candidate("KXNBASPREAD-OKCSAS-015", "1.5", 0.48, 94.0, 0.050),
            candidate("KXNBASPREAD-OKCSAS-025", "2.5", 0.38, 92.0, 0.046),
            candidate("KXNBASPREAD-OKCSAS-055", "5.5", 0.18, 89.0, 0.035),
            candidate("KXNBASPREAD-OKCSAS-155", "15.5", 0.04, 86.0, 0.030),
            candidate("KXNBASPREAD-OKCSAS-215", "21.5", 0.02, 84.0, 0.020),
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            plan = bot._build_sports_ladder_plan(
                bot.last_dual_lane_buy_candidates,
                primary_ticker="KXNBASPREAD-OKCSAS-015",
                source="unit_test_best_rung_far_tail",
            )

        selected_tickers = [leg["ticker"] for leg in plan["legs"]]
        rejected_tickers = set(plan["best_rung_summary"]["rejected_tickers"])
        self.assertIn("KXNBASPREAD-OKCSAS-015", selected_tickers)
        self.assertIn("KXNBASPREAD-OKCSAS-025", selected_tickers)
        self.assertNotIn("KXNBASPREAD-OKCSAS-155", selected_tickers)
        self.assertNotIn("KXNBASPREAD-OKCSAS-215", selected_tickers)
        self.assertIn("KXNBASPREAD-OKCSAS-155", rejected_tickers)
        self.assertIn("KXNBASPREAD-OKCSAS-215", rejected_tickers)

    def test_best_rung_selection_groups_live_style_spread_suffixes_without_titles(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        game_context = {
            "game_id": "nba:kxnbaspread_26may30sasokc",
            "thesis_type": "high_scoring_environment",
            "thesis_confidence": 86.0,
            "strong": True,
            "primary_candidate": "KXNBASPREAD-26MAY30SASOKC-SAS7",
        }

        def candidate(ticker, price, score, edge):
            q = SimpleNamespace(
                ticker=ticker,
                family="KXNBASPREAD",
                selected_side="yes",
                selected_price=price,
                yes_top_size=70,
            )
            return {
                "ticker": ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {
                    "ticker": ticker,
                    "series_ticker": q.family,
                    "event_ticker": "KXNBASPREAD-26MAY30SASOKC",
                },
                "allocation": {
                    "lane": "SPORTS",
                    "ticker": ticker,
                    "family": q.family,
                    "selected_side": "yes",
                    "selected_price": price,
                    "top_size": 70,
                    "allocation_score": score,
                    "unified_opportunity_score": score,
                    "edge_after_cost": edge,
                    "edge": edge,
                    "spread": 0.02,
                    "liquidity": 240,
                    "sports_execution_quality_score": 88.0,
                    "sports_execution_fill_probability": 0.74,
                    "family_profit_memory_adjustment": 2.0,
                    "sports_memory_rank_adjustment": 1.5,
                    "decision": "ALLOW_STRONG_SPORT",
                    "game_id": game_context["game_id"],
                    "game_thesis_type": game_context["thesis_type"],
                    "game_thesis_confidence": game_context["thesis_confidence"],
                    "game_thesis_context": dict(game_context),
                },
            }

        bot.last_dual_lane_buy_candidates = [
            candidate("KXNBASPREAD-26MAY30SASOKC-SAS4", 0.75, 89.0, 0.042),
            candidate("KXNBASPREAD-26MAY30SASOKC-SAS7", 0.36, 94.0, 0.050),
            candidate("KXNBASPREAD-26MAY30SASOKC-SAS9", 0.36, 92.0, 0.046),
            candidate("KXNBASPREAD-26MAY30SASOKC-SAS15", 0.03, 85.0, 0.025),
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            plan = bot._build_sports_ladder_plan(
                bot.last_dual_lane_buy_candidates,
                primary_ticker="KXNBASPREAD-26MAY30SASOKC-SAS7",
                source="unit_test_best_rung_live_suffix",
            )

        selected_tickers = [leg["ticker"] for leg in plan["legs"]]
        rejected_tickers = set(plan["best_rung_summary"]["rejected_tickers"])
        self.assertEqual(plan["best_rung_summary"]["group_count"], 1)
        self.assertEqual(plan["best_rung_summary"]["ladder_group_count"], 1)
        self.assertLessEqual(len(selected_tickers), 2)
        self.assertNotIn("KXNBASPREAD-26MAY30SASOKC-SAS15", selected_tickers)
        self.assertIn("KXNBASPREAD-26MAY30SASOKC-SAS15", rejected_tickers)

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

    def test_sports_ladder_game_exposure_cap_counts_api_reconciled_tickers(self):
        self._set_sports_only_mode(True)
        bot = self._make_bot()
        q = SimpleNamespace(ticker="KXMLBSPREAD-26MAY311920CHCSTL-STL6", family="KXMLBSPREAD", selected_side="yes", selected_price=0.38)
        game_id = "mlb:kxmlb_chcstl"
        bot.last_dual_lane_buy_candidates = [
            {
                "ticker": q.ticker,
                "family": q.family,
                "lane": "SPORTS",
                "q": q,
                "market": {"ticker": q.ticker, "series_ticker": q.family, "event_ticker": "KXMLB-26MAY311920CHCSTL", "title": "Cardinals wins by over 5.5 runs?"},
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
                    "game_thesis_type": "favorite_dominance",
                    "game_thesis_confidence": 82.0,
                    "game_thesis_context": {"game_id": game_id, "thesis_type": "favorite_dominance", "thesis_confidence": 82.0, "strong": True},
                },
            }
        ]
        bot.live_positions = [
            {
                "ticker": "KXMLBSPREAD-26MAY311920CHCSTL-STL5",
                "status": "open",
                "count": kw.AION_SPORTS_LADDER_MAX_GAME_CONTRACTS,
                "entry_context": None,
            }
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot._execute_sports_ladder_attempts(primary_ticker=q.ticker, primary_result={"submitted": True}, source="unit_test_ladder_cap_api_state")

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

        self.assertTrue(result["sports_submitted"])
        self.assertNotIn("CRYPTO_LADDER", result["results"])
        self.assertNotIn("crypto_submitted", result)
        self.assertNotIn("crypto_reason", result)
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

        self.assertEqual(context["decision"], "DEMOTE_WEAK_SPORT")
        self.assertEqual(context["final_quality_action"], "demote")
        self.assertIn("home_run_prop", context["trash_prop_patterns"])
        self.assertIn("multi_home_run_moonshot", context["trash_prop_patterns"])
        self.assertGreater(context["trash_prop_penalty"], 0.0)
        self.assertTrue(context["sports_weak_prop_reject"])

    def test_sports_context_required_for_weak_signal_demotes_when_payload_tradeable(self):
        bot = self._make_bot()
        bot._get_trusted_sports_context = lambda *args, **kwargs: {
            "trusted": False,
            "status": "context_unavailable",
            "reason": "unit_missing_context",
        }
        bot._emit_live_correlation_snapshot = lambda *args, **kwargs: None
        q = SimpleNamespace(
            ticker="KXNBA-WEAK-CONTEXT",
            yes_bid=0.40,
            yes_ask=0.58,
            no_bid=0.42,
            no_ask=0.60,
            liquidity=0,
            volume=0,
        )
        candidate = {
            "ticker": q.ticker,
            "family": "KXNBA",
            "market": {"ticker": q.ticker, "series_ticker": "KXNBA", "title": "NBA game winner"},
            "quote": q,
            "payload": {
                "selected_side": "yes",
                "selected_price": 0.58,
                "yes_price": 0.58,
            },
        }

        with contextlib.redirect_stdout(io.StringIO()):
            allowed, reason, details = bot.sports_context_check(candidate)

        self.assertTrue(allowed)
        self.assertEqual(reason, "weak_signal_context_demoted_to_ranking_cost")
        self.assertTrue(details["weak_signal_demoted"])

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

        self.assertEqual(resolved, 2)
        self.assertGreaterEqual(resolved, 1)
        self.assertEqual(getattr(q, "conviction_tier", None), "weak")

    def test_conviction_sizing_boosts_high_confidence_tight_book(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNCAABBGAME-CONFIDENCE-STRONG",
            family="KXNCAABBGAME",
            selected_side="yes",
            selected_price=0.40,
            yes_bid=0.39,
            yes_ask=0.40,
            no_bid=0.60,
            no_ask=0.61,
            yes_top_size=100,
            no_top_size=100,
            confidence=88.0,
            spread=0.01,
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
                    "selected_price": 0.40,
                    "confidence": 88.0,
                    "spread": 0.01,
                },
                ev_context={"ticker": q.ticker, "family": q.family, "ev_tier": "B"},
                source="unit_test_conviction_strong",
            )

        self.assertEqual(resolved, 6)
        self.assertEqual(getattr(q, "conviction_tier", None), "strong")

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

    def test_pregame_sports_position_negative_exit_is_held(self):
        bot = self._make_bot()
        ticker = "KXMLBKS-26JUN011845MIAWSH-WSHCCAVALLI24-7"
        future_start = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat().replace("+00:00", "Z")
        bot.live_exit_price_history = {}
        bot.dynamic_exit_log_markers = {}
        bot.profit_lock_log_markers = {}
        bot.sports_harvest_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.29,
            "side": "yes",
            "count": 2,
            "entry_ts": time.time() - 300,
        }
        bot.get_live_market_detail = lambda _ticker: {
            "ticker": ticker,
            "series_ticker": "KXMLBKS",
            "status": "active",
            "occurrence_datetime": future_start,
            "yes_bid_dollars": "0.2600",
            "yes_ask_dollars": "0.2900",
            "no_bid_dollars": "0.7100",
            "no_ask_dollars": "0.7400",
        }
        bot.get_market_family = lambda _market: "KXMLBKS"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": -0.50}
        q = SimpleNamespace(ticker=ticker, yes_bid=0.26, yes_ask=0.29, no_bid=0.71, no_ask=0.74)

        decision = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.29, "count": 2, "entry_ts": time.time() - 300},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "hold")
        self.assertEqual(decision["reason"], "pregame_loss_exit_blocked")
        self.assertTrue(decision["pregame_loss_exit_blocked"])
        self.assertTrue(decision["sports_pregame_exit_context"]["future_start"])

    def test_pregame_sports_position_profitable_exit_still_allowed(self):
        bot = self._make_bot()
        ticker = "KXMLBKS-26JUN011845MIAWSH-WSHCCAVALLI24-7"
        future_start = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat().replace("+00:00", "Z")
        bot.live_exit_price_history = {}
        bot.dynamic_exit_log_markers = {}
        bot.profit_lock_log_markers = {}
        bot.sports_harvest_log_markers = {}
        bot.live_loss_exit_confirmation_counts = {}
        bot.get_live_entry_metadata = lambda _ticker: {
            "entry_price": 0.20,
            "side": "yes",
            "count": 5,
            "entry_ts": time.time() - 300,
        }
        bot.get_live_market_detail = lambda _ticker: {
            "ticker": ticker,
            "series_ticker": "KXMLBKS",
            "status": "active",
            "occurrence_datetime": future_start,
            "yes_bid_dollars": "0.2900",
            "yes_ask_dollars": "0.3100",
            "no_bid_dollars": "0.6900",
            "no_ask_dollars": "0.7100",
        }
        bot.get_market_family = lambda _market: "KXMLBKS"
        bot._extract_orderbook_imbalance_context = lambda *args, **kwargs: {"imbalance": 0.20}
        q = SimpleNamespace(ticker=ticker, yes_bid=0.29, yes_ask=0.31, no_bid=0.69, no_ask=0.71)

        decision = bot._evaluate_dynamic_live_exit(
            {"ticker": ticker, "side": "yes", "entry_price": 0.20, "count": 5, "entry_ts": time.time() - 300},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "exit")
        self.assertFalse(decision["pregame_loss_exit_blocked"])
        self.assertGreater(decision["unrealized_pnl"], 0.0)

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

    def test_position_flip_weakening_yes_with_stronger_no_exits_reduce_only(self):
        self._set_position_flip_config(allow_reenter=False)
        bot = self._configure_position_flip_bot(self._make_bot())
        ticker = "KXATPCHALLENGERMATCH-26JUN01LANGMO-LANGMO"
        position = {"ticker": ticker, "family": "KXATPCHALLENGERMATCH", "side": "yes", "entry_price": 0.60, "count": 2, "entry_ts": time.time() - 300}
        q = SimpleNamespace(
            ticker=ticker,
            family="KXATPCHALLENGERMATCH",
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            yes_top_size=20,
            no_top_size=20,
            yes_edge_after_cost=-0.04,
            no_edge_after_cost=0.10,
        )

        base = bot._evaluate_dynamic_live_exit(position, quotes_by_ticker={ticker: q})
        decision = bot._evaluate_position_flip(position, base_decision=base, quotes_by_ticker={ticker: q})
        payload = bot._build_live_exit_order_payload(position, decision)

        self.assertEqual(decision["action"], "exit")
        self.assertEqual(decision["reason"], "position_flip_opposite_edge")
        self.assertEqual(decision["position_flip"]["opposite_side"], "no")
        self.assertAlmostEqual(decision["position_flip"]["edge_delta"], 0.14)
        self.assertEqual(payload["action"], "sell")
        self.assertTrue(payload["reduce_only"])
        self.assertEqual(payload["side"], "yes")

    def test_position_flip_weakening_no_with_stronger_yes_exits_reduce_only(self):
        self._set_position_flip_config(allow_reenter=False)
        bot = self._configure_position_flip_bot(self._make_bot())
        ticker = "KXATPCHALLENGERMATCH-26JUN01MILAVSKY-LANGMO"
        position = {"ticker": ticker, "family": "KXATPCHALLENGERMATCH", "side": "no", "entry_price": 0.62, "count": 3, "entry_ts": time.time() - 300}
        q = SimpleNamespace(
            ticker=ticker,
            family="KXATPCHALLENGERMATCH",
            yes_bid=0.54,
            yes_ask=0.56,
            no_bid=0.44,
            no_ask=0.46,
            yes_top_size=20,
            no_top_size=20,
            yes_edge_after_cost=0.12,
            no_edge_after_cost=-0.03,
        )

        base = bot._evaluate_dynamic_live_exit(position, quotes_by_ticker={ticker: q})
        decision = bot._evaluate_position_flip(position, base_decision=base, quotes_by_ticker={ticker: q})
        payload = bot._build_live_exit_order_payload(position, decision)

        self.assertEqual(decision["action"], "exit")
        self.assertEqual(decision["reason"], "position_flip_opposite_edge")
        self.assertEqual(decision["position_flip"]["opposite_side"], "yes")
        self.assertAlmostEqual(decision["position_flip"]["edge_delta"], 0.15)
        self.assertEqual(payload["action"], "sell")
        self.assertTrue(payload["reduce_only"])
        self.assertEqual(payload["side"], "no")

    def test_position_flip_exit_failure_blocks_opposite_reentry(self):
        self._set_position_flip_config(allow_reenter=True)
        bot = self._configure_position_flip_bot(self._make_bot())
        ticker = "KXATPCHALLENGERMATCH-26JUN01LANGMO-LANGMO"
        position = {"ticker": ticker, "family": "KXATPCHALLENGERMATCH", "side": "yes", "entry_price": 0.60, "count": 2, "entry_ts": time.time() - 300}
        bot.live_positions = [position]
        bot._get_open_live_positions = lambda: [position]
        bot.submit_live_exit_order = lambda _position, _decision: {"submitted": False, "reason": "exchange_reject"}
        reentries = []
        bot.submit_live_order = lambda *args, **kwargs: reentries.append((args, kwargs)) or {"submitted": True, "reason": "submitted"}
        q = SimpleNamespace(
            ticker=ticker,
            family="KXATPCHALLENGERMATCH",
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            yes_top_size=20,
            no_top_size=20,
            yes_edge_after_cost=-0.04,
            no_edge_after_cost=0.10,
        )

        result = bot.run_live_monitor_positions_pass(allow_real_exits=True, quotes_by_ticker={ticker: q})

        self.assertEqual(result["exit_orders_submitted"], 0)
        self.assertEqual(result["flip_reentries_submitted"], 0)
        self.assertEqual(reentries, [])
        self.assertTrue(result["decisions"][0]["position_flip"])
        self.assertFalse(result["decisions"][0]["submitted"])

    def test_position_flip_reentry_blocked_until_live_exposure_clears(self):
        self._set_position_flip_config(allow_reenter=True)
        bot = self._configure_position_flip_bot(self._make_bot())
        ticker = "KXATPCHALLENGERMATCH-26JUN01LANGMO-LANGMO"
        position = {"ticker": ticker, "family": "KXATPCHALLENGERMATCH", "side": "yes", "entry_price": 0.60, "count": 2, "entry_ts": time.time() - 300}
        bot.live_positions = [position]
        bot._get_open_live_positions = lambda: [position]
        bot.submit_live_exit_order = lambda _position, _decision: {"submitted": True, "reason": "submitted", "response": {"order": {"order_id": "exit-1"}}}
        bot.reconcile_live_positions = lambda: {"ok": True, "positions": bot.live_positions}
        reentries = []
        bot.submit_live_order = lambda *args, **kwargs: reentries.append((args, kwargs)) or {"submitted": True, "reason": "submitted"}
        q = SimpleNamespace(
            ticker=ticker,
            family="KXATPCHALLENGERMATCH",
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            yes_top_size=20,
            no_top_size=20,
            yes_edge_after_cost=-0.04,
            no_edge_after_cost=0.10,
        )

        result = bot.run_live_monitor_positions_pass(allow_real_exits=True, quotes_by_ticker={ticker: q})

        self.assertEqual(result["exit_orders_submitted"], 1)
        self.assertEqual(result["flip_reentries_submitted"], 0)
        self.assertEqual(result["decisions"][0]["reentry_reason"], "exit_not_cleared")
        self.assertEqual(reentries, [])

    def test_position_flip_reentry_allowed_after_live_exposure_clears(self):
        self._set_position_flip_config(allow_reenter=True)
        bot = self._configure_position_flip_bot(self._make_bot())
        ticker = "KXATPCHALLENGERMATCH-26JUN01LANGMO-LANGMO"
        position = {"ticker": ticker, "family": "KXATPCHALLENGERMATCH", "side": "yes", "entry_price": 0.60, "count": 2, "entry_ts": time.time() - 300}
        bot.live_positions = [position]
        bot._get_open_live_positions = lambda: [position]
        bot.submit_live_exit_order = lambda _position, _decision: {"submitted": True, "reason": "submitted", "response": {"order": {"order_id": "exit-1"}}}

        def clear_exposure():
            bot.live_positions = []
            return {"ok": True, "positions": []}

        bot.reconcile_live_positions = clear_exposure
        reentries = []
        bot.submit_live_order = lambda *args, **kwargs: reentries.append((args, kwargs)) or {"submitted": True, "reason": "submitted"}
        q = SimpleNamespace(
            ticker=ticker,
            family="KXATPCHALLENGERMATCH",
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            yes_top_size=20,
            no_top_size=20,
            yes_edge_after_cost=-0.04,
            no_edge_after_cost=0.10,
        )

        result = bot.run_live_monitor_positions_pass(allow_real_exits=True, quotes_by_ticker={ticker: q})

        self.assertEqual(result["exit_orders_submitted"], 1)
        self.assertEqual(result["flip_reentries_submitted"], 1)
        self.assertEqual(result["decisions"][0]["reentry_reason"], "submitted")
        self.assertEqual(reentries[0][1]["payload_override"]["side"], "no")
        self.assertEqual(reentries[0][1]["payload_override"]["action"], "buy")

    def test_position_flip_small_edge_changes_do_not_churn(self):
        self._set_position_flip_config(min_edge_delta=0.08, allow_reenter=False)
        bot = self._configure_position_flip_bot(self._make_bot())
        ticker = "KXATPCHALLENGERMATCH-26JUN01LANGMO-LANGMO"
        position = {"ticker": ticker, "family": "KXATPCHALLENGERMATCH", "side": "yes", "entry_price": 0.60, "count": 2, "entry_ts": time.time() - 300}
        q = SimpleNamespace(
            ticker=ticker,
            family="KXATPCHALLENGERMATCH",
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            yes_top_size=20,
            no_top_size=20,
            yes_edge_after_cost=0.01,
            no_edge_after_cost=0.05,
        )

        base = bot._evaluate_dynamic_live_exit(position, quotes_by_ticker={ticker: q})
        decision = bot._evaluate_position_flip(position, base_decision=base, quotes_by_ticker={ticker: q})

        self.assertEqual(decision["action"], "hold")
        self.assertEqual(decision["reason"], "position_flip_edge_delta_threshold_not_met")

    def test_position_flip_cooldown_prevents_repeated_flip_spam(self):
        self._set_position_flip_config(allow_reenter=False)
        bot = self._configure_position_flip_bot(self._make_bot())
        ticker = "KXATPCHALLENGERMATCH-26JUN01LANGMO-LANGMO"
        bot.position_flip_cooldowns[ticker] = time.time() + 120
        position = {"ticker": ticker, "family": "KXATPCHALLENGERMATCH", "side": "yes", "entry_price": 0.60, "count": 2, "entry_ts": time.time() - 300}
        decision = bot._evaluate_position_flip(position, base_decision={"ticker": ticker, "side": "yes", "action": "hold"}, quotes_by_ticker={ticker: SimpleNamespace(ticker=ticker)})

        self.assertEqual(decision["action"], "hold")
        self.assertEqual(decision["reason"], "position_flip_cooldown_active")

    def test_position_flip_profitable_hold_is_not_exited(self):
        self._set_position_flip_config(allow_reenter=False)
        bot = self._configure_position_flip_bot(self._make_bot())
        ticker = "KXATPCHALLENGERMATCH-26JUN01LANGMO-LANGMO"
        position = {"ticker": ticker, "family": "KXATPCHALLENGERMATCH", "side": "yes", "entry_price": 0.45, "count": 2, "entry_ts": time.time() - 300}
        q = SimpleNamespace(
            ticker=ticker,
            family="KXATPCHALLENGERMATCH",
            yes_bid=0.56,
            yes_ask=0.58,
            no_bid=0.42,
            no_ask=0.44,
            yes_top_size=20,
            no_top_size=20,
            yes_edge_after_cost=0.10,
            no_edge_after_cost=-0.02,
        )

        decision = bot._evaluate_position_flip(
            position,
            base_decision={"ticker": ticker, "side": "yes", "action": "hold", "entry_price": 0.45, "exit_price": 0.56, "count": 2, "unrealized_pnl": 0.22},
            quotes_by_ticker={ticker: q},
        )

        self.assertEqual(decision["action"], "hold")
        self.assertEqual(decision["reason"], "position_flip_loss_threshold_not_met")

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

    def test_sports_spread_selected_price_cents_prevents_missing_price_blocker(self):
        bot = self._make_bot()
        decision = bot._sports_adaptive_spread_decision(
            {"ticker": "KXNBA-CENTS-PRICE", "series_ticker": "KXNBA", "volume": 5},
            family="KXNBA",
            spread=0.04,
            liquidity=5,
            quote_diag={"selected_side": "yes", "selected_price_cents": 58},
            context="unit_selected_price_cents",
            emit_log=False,
        )

        self.assertTrue(decision["selected_price_ready"])
        self.assertTrue(decision["allowed"])
        self.assertNotEqual(decision["reason"], "selected_price_missing_or_invalid")

    def test_sports_liquidity_relaxation_demotes_thin_book_without_confirmed_edge(self):
        bot = self._make_bot()
        decision = bot._sports_liquidity_relaxation_decision(
            ticker="KXNBA-THIN-BOOK",
            family="KXNBA",
            selected_side="yes",
            selected_price=0.55,
            spread=0.08,
            blocker_reason="orderbook_not_usable",
            quote_ready=False,
            orderbook_ready=False,
            edge_after_cost=0.0,
            profitability_score=0.0,
        )
        broken = bot._sports_liquidity_relaxation_decision(
            ticker="KXNBA-BROKEN-BOOK",
            family="KXNBA",
            selected_side="yes",
            selected_price=0.55,
            spread=0.08,
            blocker_reason="orderbook_not_usable",
            unsafe_price_status="fake_orderbook",
            quote_ready=False,
            orderbook_ready=False,
        )

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["reason"], "thin_book_without_confirmed_edge_demoted_to_size")
        self.assertEqual(decision["final_effect"], "sports_thin_liquidity_warning")
        self.assertFalse(decision["true_book_block"])
        self.assertFalse(broken["allowed"])
        self.assertTrue(broken["true_book_block"])
        self.assertEqual(broken["reason"], "hard_book_or_price_integrity_block")

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

    def test_sports_smart_entry_demotes_wide_spread_to_cost(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA1HTOTAL-WIDE-SMART",
            "series_ticker": "KXNBA1HTOTAL",
            "status": "open",
            "title": "NBA first half total over 112.5",
            "yes_bid": 0.40,
            "yes_ask": 0.58,
            "no_bid": 0.42,
            "no_ask": 0.60,
            "volume": 20,
            "liquidity_dollars": 40,
            "open_interest": 100,
            "edge_after_cost": 0.025,
            "game_thesis_confidence": 74,
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.58,
            yes_bid=0.40,
            yes_ask=0.58,
            no_bid=0.42,
            no_ask=0.60,
            yes_top_size=8,
            no_top_size=8,
            liquidity=40,
            volume=20,
            edge_after_cost=0.025,
            game_thesis_confidence=74,
            sports_regime="favorable",
        )
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "both_non_zero",
            "yes_top_size": 8,
            "no_top_size": 8,
            "both_sides_quoted": True,
            "liquidity_final": 40,
        }
        bot._get_sports_adaptive_spread_threshold = lambda *args, **kwargs: {
            "adaptive_threshold": kw.MAX_ENTRY_SPREAD,
            "block_reason": "unit_smart_spread_cost",
        }
        bot._get_live_session_entry_adjustment = lambda **kwargs: {"adaptive_block": False}

        with contextlib.redirect_stdout(io.StringIO()):
            allowed, reason, details = bot._evaluate_smart_entry_gate(
                market,
                q,
                payload={
                    "selected_side": "yes",
                    "selected_price": 0.58,
                    "selected_price_dollars": 0.58,
                    "selected_price_cents": 58,
                    "liquidity": 40,
                    "edge_after_cost": 0.025,
                    "game_thesis_confidence": 74,
                    "sports_regime": "favorable",
                },
            )

        self.assertTrue(allowed)
        self.assertEqual(reason, "sports_wide_spread_cost_smart_entry_pass")
        self.assertTrue(details["adaptive_allow"])
        self.assertTrue(details["sports_spread_context"]["allowed"])
        self.assertLess(details["sports_spread_context"]["size_multiplier"], 1.0)

    def test_sports_smart_entry_demotes_timing_gate_to_execution_cost(self):
        bot = self._make_bot()
        bot.live_momentum_hard_block_log_last_seen = {}
        bot.phase55_rolling_price_history = {"unit_timing": [0.55, 0.53]}
        bot._build_phase55_timing_history_key = lambda *args, **kwargs: "unit_timing"
        bot._should_adaptive_allow_momentum_warning = lambda *args, **kwargs: (False, {"why": "unit_momentum_down"})
        bot._get_live_session_entry_adjustment = lambda **kwargs: {"adaptive_block": False}
        market = {
            "ticker": "KXNBA-TIMING-GATE",
            "series_ticker": "KXNBA",
            "status": "open",
            "title": "NBA game winner",
            "yes_bid": 0.50,
            "yes_ask": 0.52,
            "no_bid": 0.48,
            "no_ask": 0.50,
            "volume": 20,
            "liquidity_dollars": 30,
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            family="KXNBA",
            selected_side="yes",
            selected_price=0.52,
            yes_bid=0.50,
            yes_ask=0.52,
            no_bid=0.48,
            no_ask=0.50,
            liquidity=30,
            volume=20,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            allowed, reason, details = bot._evaluate_smart_entry_gate(
                market,
                q,
                payload={"selected_side": "yes", "selected_price": 0.52, "selected_price_cents": 52},
            )

        self.assertTrue(allowed)
        self.assertTrue(details["adaptive_allow"])
        self.assertEqual(reason, "sports_timing_gate_demoted_to_execution_cost")

    def test_soft_quote_orderbook_blockers_are_profit_quality_not_hard_safety(self):
        bot = self._make_bot()
        for reason in ("quote_not_ready", "quote_not_ready_after_refresh", "orderbook_not_usable", "no_executable_side"):
            self.assertEqual(bot._classify_aion_execution_blocker(reason), "profit_quality")
        for reason in ("malformed_orderbook", "fake_book", "duplicate_position", "daily_loss_guard"):
            self.assertEqual(bot._classify_aion_execution_blocker(reason), "hard_safety")

    def test_sports_probability_block_demotes_to_cost_with_executable_edge(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA-PROB-EDGE",
            "series_ticker": "KXNBA",
            "status": "open",
            "title": "NBA game winner",
            "yes_bid": 0.45,
            "yes_ask": 0.47,
            "no_bid": 0.53,
            "no_ask": 0.55,
            "liquidity_dollars": 60,
            "game_thesis_confidence": 72,
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            family="KXNBA",
            selected_side="yes",
            selected_price=0.47,
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            yes_top_size=12,
            no_top_size=14,
            liquidity=60,
            edge_after_cost=0.026,
            sports_execution_fill_probability=0.61,
            sports_execution_quality_score=61,
            game_thesis_confidence=72,
            sports_regime="favorable",
        )

        with contextlib.redirect_stdout(io.StringIO()) as logs:
            decision = bot._sports_probability_cost_decision(
                market,
                q=q,
                family="KXNBA",
                probability_context={
                    "final_signal": "weak",
                    "final_conviction_score": 0.18,
                    "block_reason": "probability_mispricing_block",
                    "implied_probability": {"spread_adjusted_probability": 0.47},
                    "fair_value": {"fair_probability": 0.54},
                    "edge": {"divergence": 0.026},
                },
                selected_side="yes",
                selected_price=0.47,
                spread=0.02,
                liquidity=60,
                edge_after_cost=0.026,
                fill_probability=0.61,
                execution_quality_score=61,
                context="unit_probability",
            )

        self.assertTrue(decision["allowed"])
        self.assertFalse(decision["hard_reject"])
        self.assertEqual(decision["action"], "override")
        self.assertLess(decision["rank_adjustment"], 0.0)
        self.assertLess(decision["size_multiplier"], 1.0)
        self.assertIn("[SPORTS-PROBABILITY-OVERRIDE]", logs.getvalue())

    def test_sports_probability_block_preserves_true_invalid_price_block(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA-PROB-BROKEN",
            "series_ticker": "KXNBA",
            "status": "open",
            "title": "NBA game winner",
            "yes_bid": None,
            "yes_ask": None,
            "no_bid": None,
            "no_ask": None,
        }
        q = SimpleNamespace(ticker=market["ticker"], family="KXNBA", selected_side="yes", selected_price=None)

        with contextlib.redirect_stdout(io.StringIO()) as logs:
            decision = bot._sports_probability_cost_decision(
                market,
                q=q,
                family="KXNBA",
                probability_context={
                    "final_signal": "weak",
                    "block_reason": "probability_mispricing_block",
                    "implied_probability": {},
                    "fair_value": {},
                },
                selected_side="yes",
                selected_price=None,
                context="unit_probability",
            )

        self.assertFalse(decision["allowed"])
        self.assertTrue(decision["hard_reject"])
        self.assertEqual(decision["action"], "block")
        self.assertEqual(decision["reason"], "selected_price_missing_or_invalid")
        self.assertIn("[SPORTS-PROBABILITY-BLOCK]", logs.getvalue())
        self.assertIn("[AION-PROBABILITY-BLOCKER]", logs.getvalue())

    def test_entry_timing_probability_block_no_longer_vetoes_valid_sports_candidate(self):
        bot = self._make_bot()
        old_realm_vv = kw.AION_USE_REALM_VV_ENTRY_ENGINE
        old_probability_blocks = kw.REALM_XXIII_ALLOW_BLOCKS
        kw.AION_USE_REALM_VV_ENTRY_ENGINE = False
        kw.REALM_XXIII_ALLOW_BLOCKS = True
        self.addCleanup(setattr, kw, "AION_USE_REALM_VV_ENTRY_ENGINE", old_realm_vv)
        self.addCleanup(setattr, kw, "REALM_XXIII_ALLOW_BLOCKS", old_probability_blocks)
        bot.market_psychology_profiles = {}
        bot.last_live_select_summary = {}
        bot.phase55_current_select_pass_marker = "unit_probability"
        bot.phase55_select_pass_counter = 0
        bot.cycle_count = 1
        bot.phase55_rolling_price_history = {}
        bot.phase55_rolling_price_marker_cache = {}
        bot.phase55_market_history_cache = {}
        bot._get_websocket_snapshot = lambda *args, **kwargs: {}
        bot.compute_live_sports_momentum = lambda *args, **kwargs: {"momentum_score": 0.10, "directional_bias": "up", "volatility_state": "normal"}
        bot.get_market_min_entry_liquidity = lambda market_arg: 1
        bot.get_market_liquidity_priority = lambda *args, **kwargs: {
            "top_book_state": "both_non_zero",
            "both_sides_quoted": True,
            "has_real_liquidity": True,
            "yes_top_size": 12,
            "no_top_size": 14,
            "liquidity_final": 60,
        }
        bot._get_sports_adaptive_spread_threshold = lambda *args, **kwargs: {"adaptive_threshold": 0.18, "block_reason": "unit_test"}
        bot._get_personality_snapshot = lambda **kwargs: {"profile": {}, "regime": {}, "adaptive_thresholds": {}, "memory": {}, "priority_score": 0.0}
        bot._get_probability_snapshot = lambda **kwargs: {
            "final_signal": "weak",
            "final_conviction_score": 0.18,
            "block_reason": "probability_mispricing_block",
            "implied_probability": {"spread_adjusted_probability": 0.47, "yes_probability": 0.47},
            "fair_value": {"fair_probability": 0.54, "uncertainty_score": 0.32},
            "edge": {"divergence": 0.026, "edge_category": "positive"},
            "mispricing": {"divergence": 0.026, "mispricing_type": "sports_edge"},
        }
        bot._get_capital_snapshot = lambda: {"capital_state": "NORMAL", "aggression_level": 0.35, "aggression_mode": "normal", "risk_multiplier": 1.0, "soft_circuit_active": False}
        bot._get_external_snapshot = lambda: {"event_risk": {}, "active_catalysts": [], "execution_caution": False}
        bot._get_evolution_snapshot = lambda: {}
        bot._get_phase55_stochastic_context = lambda *args, **kwargs: {
            "stochastic_inputs_available": True,
            "stochastic_k": 62,
            "stochastic_d": 58,
            "previous_stochastic_k": 55,
            "previous_stochastic_d": 57,
            "stochastic_source": "unit_test",
            "history_points": 6,
            "required_points": 6,
            "history_reason": "unit_test",
            "timing_history_key": "unit_probability",
            "history_appended": False,
            "history_append_reason": "unit_test",
            "cycle_id": "unit",
        }
        market = {
            "ticker": "KXNBA-PROB-TIMING",
            "series_ticker": "KXNBA",
            "status": "open",
            "title": "NBA game winner",
            "yes_bid": 0.45,
            "yes_ask": 0.47,
            "no_bid": 0.53,
            "no_ask": 0.55,
            "liquidity_dollars": 60,
            "micro_pullback": True,
            "game_thesis_confidence": 72,
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            family="KXNBA",
            side="yes",
            selected_side="yes",
            selected_price=0.47,
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            yes_top_size=12,
            no_top_size=14,
            liquidity=60,
            edge_after_cost=0.026,
            sports_execution_fill_probability=0.61,
            sports_execution_quality_score=61,
            game_thesis_confidence=72,
            sports_regime="favorable",
        )

        with contextlib.redirect_stdout(io.StringIO()):
            observation = bot.get_entry_timing_observation(market, q, "KXNBA")

        self.assertTrue(observation["probability_block_demoted"])
        self.assertFalse(observation["probability_execution_blocked"])
        self.assertNotEqual(observation["phase55_status"], "probability_block")
        self.assertEqual(observation["phase55_status"], "entry_ready")
        self.assertLess(observation["sports_probability_size_multiplier"], 1.0)

    def test_dynamic_entry_size_applies_sports_probability_downsize(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA-PROB-SIZE",
            family="KXNBA",
            selected_side="yes",
            selected_price=0.48,
            yes_bid=0.46,
            yes_ask=0.48,
            no_bid=0.52,
            no_ask=0.54,
            yes_top_size=20,
            no_top_size=20,
            liquidity=80,
            sports_probability_size_multiplier=0.50,
            sports_probability_rank_adjustment=-2.5,
            sports_probability_action="override",
            sports_probability_reason="probability_block_demoted_to_cost",
            sports_probability_edge_after_cost=0.02,
            sports_probability_fill_probability=0.58,
            sports_probability_execution_quality_score=58,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            resolved = bot._resolve_dynamic_live_entry_size(
                q,
                explicit_size=10,
                payload={"ticker": q.ticker, "family": q.family, "count": 10, "selected_side": "yes", "selected_price": 0.48},
                ev_context={"ticker": q.ticker, "family": q.family, "sports_probability_size_multiplier": 0.50},
                source="unit_probability_size",
            )

        self.assertGreaterEqual(resolved, 1)
        self.assertLess(resolved, 10)

    def test_lane_selected_price_uses_phase55_forensics_payload(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXATPCHALLENGERMATCH-LIVE-EDGE",
            family="KXATPCHALLENGERMATCH",
            yes_bid=0.20,
            yes_ask=0.22,
            no_bid=0.78,
            no_ask=0.80,
            yes_top_size=120,
            no_top_size=90,
        )

        selected_side, selected_price = bot._extract_lane_selected_price(
            q=q,
            expectancy_summary={},
            quality_snapshot={
                "payload": {
                    "selected_side": "yes",
                    "selected_price": 0.22,
                    "selected_price_cents": 22,
                }
            },
            market={"ticker": q.ticker, "series_ticker": q.family},
            family=q.family,
        )

        self.assertEqual(selected_side, "yes")
        self.assertAlmostEqual(selected_price, 0.22)

    def test_execution_chokepoint_recovery_promotes_quote_ready_phase55_veto(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXATPCHALLENGERMATCH-LIVE-EDGE",
            family="KXATPCHALLENGERMATCH",
            yes_bid=0.20,
            yes_ask=0.22,
            no_bid=0.78,
            no_ask=0.80,
            yes_top_size=120,
            no_top_size=90,
        )

        result = bot._should_apply_execution_chokepoint_recovery(
            family=q.family,
            market={"ticker": q.ticker, "series_ticker": q.family, "status": "open"},
            q=q,
            expectancy_summary={
                "quote_ready": True,
                "spread_bucket": "wide_but_possible",
                "sports_execution_fill_probability": 0.75,
            },
            candidate_forensics={
                "quote_ready": True,
                "unsafe_price_status": "safe",
                "spread_bucket": "wide_but_possible",
                "payload": {
                    "selected_side": "yes",
                    "selected_price": 0.22,
                    "selected_top_size": 120,
                    "edge_after_cost": 0.0155,
                    "selected_fill_probability": 0.75,
                    "spread": 0.19,
                },
            },
            entry_timing_observation={"phase55_status": "trigger_wait"},
            decision="SKIP",
            hard_reject_reason="",
            execution_safety_blocked=False,
            spread=0.19,
            candidate_edge_after_cost=0.0,
            unsafe_price_status="safe",
            candidate_quote_ready=True,
            candidate_spread_bucket="wide_but_possible",
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason"], "execution_chokepoint_phase55_recovery")
        self.assertEqual(result["selected_side"], "yes")
        self.assertAlmostEqual(result["selected_price"], 0.22)

    def test_execution_chokepoint_recovery_preserves_capital_safety_block(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXATPCHALLENGERMATCH-LIVE-BLOCK",
            family="KXATPCHALLENGERMATCH",
            selected_side="yes",
            selected_price=0.22,
            yes_top_size=120,
            yes_bid=0.20,
            yes_ask=0.22,
        )

        result = bot._should_apply_execution_chokepoint_recovery(
            family=q.family,
            market={"ticker": q.ticker, "series_ticker": q.family, "status": "open"},
            q=q,
            expectancy_summary={"quote_ready": True, "edge_after_cost": 0.02},
            candidate_forensics={
                "quote_ready": True,
                "unsafe_price_status": "safe",
                "payload": {"selected_side": "yes", "selected_price": 0.22, "selected_top_size": 120, "edge_after_cost": 0.02},
            },
            entry_timing_observation={"phase55_status": "capital_block", "capital_execution_blocked": True},
            decision="SKIP",
            hard_reject_reason="",
            execution_safety_blocked=True,
            spread=0.02,
            candidate_edge_after_cost=0.02,
            unsafe_price_status="safe",
            candidate_quote_ready=True,
            candidate_spread_bucket="tight",
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "execution_safety_blocked")

    def test_realm_vv_edge_floor_probability_block_becomes_sports_probability_cost(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA-PROB-REALM",
            "series_ticker": "KXNBA",
            "status": "open",
            "title": "NBA game winner",
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            family="KXNBA",
            selected_side="yes",
            selected_price=0.47,
            yes_bid=0.45,
            yes_ask=0.47,
            no_bid=0.53,
            no_ask=0.55,
            yes_top_size=10,
            no_top_size=10,
            liquidity=50,
        )
        tradeability = {
            "selected_price_dollars": 0.47,
            "selected_price": 0.47,
            "selected_price_cents": 47,
            "selected_side": "yes",
            "quote_ready": True,
            "orderbook_ready": True,
            "selected_side_has_price": True,
            "selected_side_has_liquidity": True,
            "depth_available": True,
            "tradeable": False,
            "blocker_class": None,
            "blocker": None,
            "blocker_detail": None,
            "unsafe_price_status": "safe",
            "spread_classification": "CAUTION_WAIT",
            "spread_bucket": "wide_but_possible",
            "spread_cents": 2,
            "liquidity": 50,
            "yes_bid_cents": 45,
            "yes_ask_cents": 47,
            "no_bid_cents": 53,
            "no_ask_cents": 55,
            "yes_top_size": 10,
            "no_top_size": 10,
            "edge_after_cost": 0.001,
            "confidence_score": 0.82,
        }

        with contextlib.redirect_stdout(io.StringIO()) as logs:
            result = bot.resolve_realm_vv_entry_engine(
                q,
                canonical_payload={
                    "ticker": q.ticker,
                    "edge_after_cost": 0.001,
                    "final_edge": 0.001,
                    "canonical_tradeability": tradeability,
                },
                orderbook_context={"ticker": q.ticker, "selected_side": "yes"},
                decision_context={
                    "market": market,
                    "ticker": q.ticker,
                    "family": "KXNBA",
                    "profile": "safe",
                    "selected_side": "yes",
                    "edge_after_cost": 0.001,
                    "confidence": 0.82,
                    "canonical_tradeability": tradeability,
                    "live_arm_ok": True,
                },
            )

        self.assertEqual(result["phase55_status"], "entry_ready")
        self.assertTrue(result["tradeable"])
        self.assertIsNone(result["blocker"])
        self.assertIn("[SPORTS-PROBABILITY-OVERRIDE]", logs.getvalue())
        self.assertEqual(getattr(q, "sports_probability_action"), "override")

    def test_realm_vv_no_edge_probability_skip_uses_non_legacy_reason(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA-PROB-NOEDGE",
            "series_ticker": "KXNBA",
            "status": "open",
            "title": "NBA player points",
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            family="KXNBA",
            selected_side="no",
            selected_price=0.96,
            no_bid=0.94,
            no_ask=0.96,
            no_top_size=1,
            liquidity=0,
        )
        tradeability = {
            "selected_price_dollars": 0.96,
            "selected_price": 0.96,
            "selected_price_cents": 96,
            "selected_side": "no",
            "quote_ready": True,
            "orderbook_ready": True,
            "selected_side_has_price": True,
            "selected_side_has_liquidity": True,
            "depth_available": True,
            "tradeable": False,
            "blocker_class": None,
            "blocker": None,
            "blocker_detail": None,
            "unsafe_price_status": "safe",
            "spread_classification": "CAUTION_WAIT",
            "spread_bucket": "wide_but_possible",
            "spread_cents": 2,
            "liquidity": 0,
            "no_bid_cents": 94,
            "no_ask_cents": 96,
            "no_top_size": 1,
            "edge_after_cost": 0.0,
            "confidence_score": 0.24,
            "fill_probability": 0.28,
            "execution_quality_score": 24,
        }

        with contextlib.redirect_stdout(io.StringIO()) as logs:
            result = bot.resolve_realm_vv_entry_engine(
                q,
                canonical_payload={
                    "ticker": q.ticker,
                    "edge_after_cost": 0.0,
                    "final_edge": 0.0,
                    "canonical_tradeability": tradeability,
                },
                orderbook_context={"ticker": q.ticker, "selected_side": "no"},
                decision_context={
                    "market": market,
                    "ticker": q.ticker,
                    "family": "KXNBA",
                    "profile": "safe",
                    "selected_side": "no",
                    "edge_after_cost": 0.0,
                    "confidence": 0.24,
                    "canonical_tradeability": tradeability,
                    "live_arm_ok": True,
                },
            )

        self.assertFalse(result["tradeable"])
        self.assertEqual(result["phase55_status"], "insufficient_sports_execution_evidence")
        self.assertEqual(result["blocker"], "insufficient_sports_execution_evidence")
        self.assertNotEqual(result["phase55_status"], "probability_block")
        self.assertIn("[SPORTS-PROBABILITY-BLOCK]", logs.getvalue())

    def _realm_confidence_tradeability(self, **overrides):
        tradeability = {
            "selected_price_dollars": 0.42,
            "selected_price": 0.42,
            "selected_price_cents": 42,
            "selected_side": "yes",
            "quote_ready": True,
            "orderbook_ready": True,
            "selected_side_has_price": True,
            "selected_side_has_liquidity": True,
            "depth_available": True,
            "tradeable": False,
            "blocker_class": None,
            "blocker": None,
            "blocker_detail": None,
            "unsafe_price_status": "safe",
            "spread_classification": "SAFE",
            "spread_bucket": "tight",
            "spread_cents": 2,
            "spread": 0.02,
            "liquidity": 80,
            "yes_bid_cents": 40,
            "yes_ask_cents": 42,
            "no_bid_cents": 58,
            "no_ask_cents": 60,
            "yes_top_size": 25,
            "no_top_size": 25,
            "selected_top_size": 25,
            "edge_after_cost": 0.024,
            "ev_edge": 0.024,
            "expected_roi": 0.057,
            "confidence_score": 0.54,
            "suggested_size_multiplier": 1.0,
        }
        tradeability.update(overrides)
        return tradeability

    def _run_realm_confidence_case(self, bot, *, tradeability, edge=0.024, ev=0.024, confidence=0.54, duplicate_block=False, max_positions_block=False, live_arm_ok=True):
        market = {
            "ticker": "KXNBA-CONFIDENCE-LIVE",
            "series_ticker": "KXNBA",
            "status": "in_play",
            "in_play": True,
            "title": "NBA live confidence admission",
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            family="KXNBA",
            selected_side=tradeability.get("selected_side"),
            selected_price=tradeability.get("selected_price"),
            yes_bid=0.40,
            yes_ask=0.42,
            no_bid=0.58,
            no_ask=0.60,
            yes_top_size=25,
            no_top_size=25,
            liquidity=tradeability.get("liquidity"),
        )
        canonical_payload = {
            "ticker": q.ticker,
            "edge_after_cost": edge,
            "final_edge": edge,
            "ev_edge": ev,
            "confidence": confidence,
            "canonical_tradeability": dict(tradeability),
        }
        expected_payload = {
            "ticker": q.ticker,
            "edge_after_cost": edge,
            "final_edge": edge,
            "ev_edge": ev,
            "confidence": confidence,
            "canonical_tradeability": dict(tradeability),
        }
        with contextlib.redirect_stdout(io.StringIO()) as logs:
            result = bot.resolve_realm_vv_entry_engine(
                q,
                canonical_payload=canonical_payload,
                orderbook_context={"ticker": q.ticker, "selected_side": tradeability.get("selected_side")},
                decision_context={
                    "market": market,
                    "ticker": q.ticker,
                    "family": "KXNBA",
                    "profile": "safe",
                    "selected_side": tradeability.get("selected_side"),
                    "edge_after_cost": edge,
                    "ev_edge": ev,
                    "confidence": confidence,
                    "canonical_tradeability": dict(tradeability),
                    "duplicate_block": duplicate_block,
                    "max_positions_block": max_positions_block,
                    "live_arm_ok": live_arm_ok,
                    "live_market": True,
                },
            )
        return result, logs.getvalue(), canonical_payload, expected_payload

    def test_realm_vv_moderate_confidence_positive_ev_can_submit_with_penalty(self):
        bot = self._make_bot()
        tradeability = self._realm_confidence_tradeability()

        result, logs, canonical_payload, expected_payload = self._run_realm_confidence_case(
            bot,
            tradeability=tradeability,
            edge=0.024,
            ev=0.024,
            confidence=0.54,
        )

        self.assertTrue(result["tradeable"])
        self.assertEqual(result["decision"], "BUY")
        self.assertEqual(result["phase55_status"], "entry_ready")
        self.assertEqual(result["confidence_gate_action"], "allow")
        self.assertTrue(result["confidence_penalty_applied"])
        self.assertLess(result["confidence_size_multiplier"], 1.0)
        self.assertEqual(result["selected_side"], "yes")
        self.assertAlmostEqual(result["selected_price"], 0.42)
        self.assertEqual(canonical_payload, expected_payload)
        self.assertIn("[AION-CONFIDENCE-AUDIT]", logs)
        self.assertIn("[AION-CONFIDENCE-ALLOW]", logs)
        self.assertIn("[AION-LIVE-EXECUTION-RESTORE]", logs)

    def test_realm_vv_negative_ev_still_rejected_under_confidence_restore(self):
        bot = self._make_bot()
        tradeability = self._realm_confidence_tradeability(ev_edge=-0.01)

        result, logs, _, _ = self._run_realm_confidence_case(
            bot,
            tradeability=tradeability,
            edge=0.024,
            ev=-0.01,
            confidence=0.54,
        )

        self.assertFalse(result["tradeable"])
        self.assertEqual(result["blocker"], "confidence_below_threshold")
        self.assertEqual(result["confidence_gate_action"], "block")
        self.assertEqual(result["confidence_gate_reason"], "negative_ev")
        self.assertIn("[AION-CONFIDENCE-BLOCK]", logs)
        self.assertNotIn("[AION-CONFIDENCE-ALLOW]", logs)

    def test_realm_vv_spread_extreme_still_rejected_under_confidence_restore(self):
        bot = self._make_bot()
        tradeability = self._realm_confidence_tradeability(
            blocker_class="spread_extreme",
            blocker="spread_extreme",
            blocker_detail="spread_extreme",
            spread_bucket="extreme_wide",
            spread_classification="UNSAFE",
            unsafe_price_status="unsafe_spread",
            spread_cents=65,
            spread=0.65,
        )

        result, logs, _, _ = self._run_realm_confidence_case(
            bot,
            tradeability=tradeability,
            edge=0.001,
            ev=0.08,
            confidence=0.54,
        )

        self.assertFalse(result["tradeable"])
        self.assertEqual(result["blocker"], "spread_extreme")
        self.assertNotEqual(result["confidence_gate_action"], "allow")
        self.assertIn("[AION-CONFIDENCE-AUDIT]", logs)
        self.assertNotIn("[AION-CONFIDENCE-ALLOW]", logs)
        self.assertNotIn("[SPORTS-PROBABILITY-OVERRIDE]", logs)

    def test_realm_vv_duplicate_guard_preserved_under_confidence_restore(self):
        bot = self._make_bot()
        tradeability = self._realm_confidence_tradeability()

        result, logs, _, _ = self._run_realm_confidence_case(
            bot,
            tradeability=tradeability,
            edge=0.024,
            ev=0.024,
            confidence=0.54,
            duplicate_block=True,
        )

        self.assertFalse(result["tradeable"])
        self.assertEqual(result["blocker"], "duplicate_position")
        self.assertEqual(result["phase55_status"], "immediate_reject")
        self.assertNotIn("[AION-CONFIDENCE-ALLOW]", logs)

    def test_realm_vv_exposure_cap_preserved_under_confidence_restore(self):
        bot = self._make_bot()
        tradeability = self._realm_confidence_tradeability()

        result, logs, _, _ = self._run_realm_confidence_case(
            bot,
            tradeability=tradeability,
            edge=0.024,
            ev=0.024,
            confidence=0.54,
            max_positions_block=True,
        )

        self.assertFalse(result["tradeable"])
        self.assertFalse(result["adaptive_confidence_allow"])
        self.assertEqual(result["confidence_gate_action"], "block")
        self.assertEqual(result["confidence_gate_reason"], "risk_or_duplicate_guard")
        self.assertIn("max_live_positions_warning", result["canonical_tradeability"].get("risk_capacity_warnings", []))
        self.assertNotIn("[AION-CONFIDENCE-ALLOW]", logs)

    def test_realm_vv_risk_governor_preserved_under_confidence_restore(self):
        bot = self._make_bot()
        tradeability = self._realm_confidence_tradeability()

        result, logs, _, _ = self._run_realm_confidence_case(
            bot,
            tradeability=tradeability,
            edge=0.024,
            ev=0.024,
            confidence=0.54,
            live_arm_ok=False,
        )

        self.assertFalse(result["tradeable"])
        self.assertEqual(result["blocker"], "live_arm")
        self.assertEqual(result["phase55_status"], "immediate_reject")
        self.assertNotIn("[AION-CONFIDENCE-ALLOW]", logs)

    def test_sports_smart_entry_still_blocks_broken_spread_book(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA1HTOTAL-BROKEN-SMART",
            "series_ticker": "KXNBA1HTOTAL",
            "status": "open",
            "title": "NBA first half total over 112.5",
            "yes_bid": 0.05,
            "yes_ask": 0.90,
            "no_bid": 0.10,
            "no_ask": 0.95,
            "volume": 10,
            "liquidity_dollars": 10,
            "open_interest": 60,
            "edge_after_cost": 0.02,
        }
        q = SimpleNamespace(
            ticker=market["ticker"],
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.90,
            yes_bid=0.05,
            yes_ask=0.90,
            no_bid=0.10,
            no_ask=0.95,
            yes_top_size=2,
            no_top_size=2,
            liquidity=10,
            volume=10,
            edge_after_cost=0.02,
        )
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "both_non_zero",
            "yes_top_size": 2,
            "no_top_size": 2,
            "both_sides_quoted": True,
            "liquidity_final": 10,
        }
        bot._get_sports_adaptive_spread_threshold = lambda *args, **kwargs: {
            "adaptive_threshold": kw.MAX_ENTRY_SPREAD,
            "block_reason": "unit_smart_spread_block",
        }
        bot._get_live_session_entry_adjustment = lambda **kwargs: {"adaptive_block": False}

        with contextlib.redirect_stdout(io.StringIO()):
            allowed, reason, details = bot._evaluate_smart_entry_gate(
                market,
                q,
                payload={
                    "selected_side": "yes",
                    "selected_price": 0.90,
                    "selected_price_dollars": 0.90,
                    "selected_price_cents": 90,
                    "liquidity": 10,
                    "edge_after_cost": 0.02,
                },
            )

        self.assertFalse(allowed)
        self.assertNotEqual(reason, "spread_too_wide")
        self.assertIn(reason, {"spread_absurd_unusable", "spread_above_adaptive_tolerance"})
        self.assertTrue(details["sports_spread_context"]["hard_reject"])

    def test_sports_final_scoring_demotes_wide_spread_to_cost(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-WIDE-FINAL",
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.60,
            yes_bid=0.40,
            yes_ask=0.60,
            no_bid=0.40,
            no_ask=0.60,
            yes_top_size=10,
            no_top_size=10,
            liquidity=50,
            volume=50,
            edge_after_cost=0.035,
            game_thesis_confidence=74,
            sports_regime="favorable",
            sports_momentum_classification="strong_positive",
        )
        market = {
            "ticker": q.ticker,
            "series_ticker": q.family,
            "status": "open",
            "title": "NBA first half total over 112.5",
            "yes_bid": 0.40,
            "yes_ask": 0.60,
            "no_bid": 0.40,
            "no_ask": 0.60,
            "yes_top_size": 10,
            "no_top_size": 10,
            "volume": 50,
            "liquidity_dollars": 50,
            "open_interest": 120,
            "edge_after_cost": 0.035,
            "game_thesis_confidence": 74,
            "sports_regime": "favorable",
            "sports_momentum_classification": "strong_positive",
        }
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "both_non_zero",
            "yes_top_size": 10,
            "no_top_size": 10,
            "both_sides_quoted": True,
            "has_real_liquidity": True,
            "liquidity_final": 50,
            "score": 0.8,
        }
        bot._get_sports_adaptive_spread_threshold = lambda *args, **kwargs: {
            "adaptive_threshold": kw.MAX_ENTRY_SPREAD,
            "block_reason": "spread_cost_test",
        }
        bot._diagnose_quote_readiness_context = lambda **kwargs: {
            "quote_ready": True,
            "quote_missing": False,
            "spread": 0.20,
            "spread_bucket": "extreme_wide",
            "selected_side": "yes",
            "selected_price": 0.60,
            "selected_price_dollars": 0.60,
            "yes_bid": 0.40,
            "yes_ask": 0.60,
            "no_bid": 0.40,
            "no_ask": 0.60,
            "yes_top_size": 10,
            "no_top_size": 10,
            "edge_after_cost": 0.035,
            "sports_edge_after_cost": 0.035,
            "source_of_quote_data": "direct_fields",
        }
        bot._resolve_unsafe_price_detail = lambda **kwargs: {
            "unsafe_price_status": "caution_wide_spread",
            "unsafe_resolution_reason": "unit_wide_spread_cost",
        }
        bot._resolve_effective_liquidity_signal = lambda *args, **kwargs: {
            "liquidity_final": 50,
            "effective_source": "unit_topbook",
            "fallback_used": False,
            "effective_top_size": 10,
        }
        bot._build_aion_market_priority_profile = lambda **kwargs: {
            "market_priority_score": 8,
            "priority_tier": 1,
            "execution_probability": 0.7,
            "execution_quality_profile": {
                "execution_quality_score": 60,
                "spread_bucket": "wide_but_possible",
                "quote_ready": True,
                "unsafe_price_status": "caution_wide_spread",
                "top_book_depth": 20,
                "execution_probability": 0.7,
            },
            "crypto_priority": False,
        }
        bot._build_aion_scan_rebalancer_profile = lambda **kwargs: {"scan_weight": 1.0}
        bot.compute_sports_post_cost_edge = lambda **kwargs: {
            "sports_edge_after_cost": 0.035,
            "quote_ready": True,
            "final_edge": 0.035,
        }
        bot.compute_live_sports_momentum = lambda *args, **kwargs: {
            "momentum_score": 0.25,
            "directional_bias": "favorite",
            "volatility_state": "normal",
        }
        bot._get_sports_edge_score = lambda *args, **kwargs: {"score": 72, "components": {}}
        bot._get_elite_public_signal_match = lambda **kwargs: {"boost": 0}
        bot._build_sports_edge_ranking_context = lambda **kwargs: {"boost": 4, "tags": ["UNIT_WIDE_COST"]}
        bot._evaluate_market_psychology = lambda **kwargs: {
            "blocked": False,
            "shadow_only": True,
            "adjusted_score": kwargs.get("original_score", 0.0),
            "original_score": kwargs.get("original_score", 0.0),
            "psychology_delta": 0.0,
        }
        bot._evaluate_live_edge_quality = lambda **kwargs: {
            "confidence_weight": 0.0,
            "confidence_score": 0.7,
            "regime_state": "ok",
            "momentum_alignment": 0.0,
            "continuity_quality": 0.0,
            "spread_stability": 0.0,
            "liquidity_stability": 0.0,
            "directional_consistency": 0.0,
        }
        bot._realm_xxxii_shadow_arb_audit = lambda *args, **kwargs: {"hard_block": False}
        bot._record_shadow_arb_validation = lambda *args, **kwargs: {}
        bot._record_live_reject_telemetry = lambda *args, **kwargs: None
        bot._record_execution_failure_audit = lambda *args, **kwargs: None
        bot._record_live_near_pass = lambda *args, **kwargs: None
        bot.is_targeted_series_market = lambda market_arg: False
        bot._get_live_session_entry_adjustment = lambda **kwargs: {"score_penalty": 0.0, "mode": "unit", "reason": "unit"}
        bot.get_minutes_to_event = lambda market_arg: 15

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot.score_final_candidate_decision(market, q, 0.20, q.family)

        self.assertNotEqual(result["debug_context"].get("hard_reject_reason"), "wide_spread")
        self.assertTrue(result["debug_context"].get("wide_spread_demoted_to_cost"))
        self.assertEqual(result["decision"], "BUY")
        self.assertLess(result["score_breakdown"]["sports_spread_cost"], 0.0)
        self.assertLess(getattr(q, "sports_spread_size_multiplier"), 1.0)

    def test_sports_final_scoring_still_blocks_broken_spread_book(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA1HTOTAL-BROKEN-FINAL",
            family="KXNBA1HTOTAL",
            selected_side="yes",
            selected_price=0.90,
            yes_bid=0.05,
            yes_ask=0.90,
            no_bid=0.10,
            no_ask=0.95,
            yes_top_size=2,
            no_top_size=2,
            liquidity=10,
            volume=10,
            edge_after_cost=0.02,
        )
        market = {
            "ticker": q.ticker,
            "series_ticker": q.family,
            "status": "open",
            "title": "NBA first half total over 112.5",
            "yes_bid": 0.05,
            "yes_ask": 0.90,
            "no_bid": 0.10,
            "no_ask": 0.95,
            "yes_top_size": 2,
            "no_top_size": 2,
            "volume": 10,
            "liquidity_dollars": 10,
            "edge_after_cost": 0.02,
        }
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "both_non_zero",
            "yes_top_size": 2,
            "no_top_size": 2,
            "both_sides_quoted": True,
            "liquidity_final": 10,
        }
        bot._get_sports_adaptive_spread_threshold = lambda *args, **kwargs: {
            "adaptive_threshold": kw.MAX_ENTRY_SPREAD,
            "block_reason": "spread_cost_test",
        }
        bot._diagnose_quote_readiness_context = lambda **kwargs: {
            "quote_ready": True,
            "quote_missing": False,
            "spread": 0.85,
            "spread_bucket": "unsafe",
            "selected_side": "yes",
            "selected_price": 0.90,
            "selected_price_dollars": 0.90,
            "yes_bid": 0.05,
            "yes_ask": 0.90,
            "no_bid": 0.10,
            "no_ask": 0.95,
            "yes_top_size": 2,
            "no_top_size": 2,
            "edge_after_cost": 0.02,
            "sports_edge_after_cost": 0.02,
            "source_of_quote_data": "direct_fields",
        }
        bot._record_execution_failure_audit = lambda *args, **kwargs: None

        with contextlib.redirect_stdout(io.StringIO()):
            result = bot.score_final_candidate_decision(market, q, 0.85, q.family)

        self.assertEqual(result["decision"], "SKIP")
        self.assertNotEqual(result["debug_context"].get("hard_reject_reason"), "wide_spread")
        self.assertIn(
            result["debug_context"].get("hard_reject_reason"),
            {"spread_absurd_unusable", "spread_above_adaptive_tolerance"},
        )

    def test_sports_side_selection_uses_execution_fill_quality(self):
        bot = self._make_bot()
        market = {
            "ticker": "KXNBA-MICROSTRUCTURE-SIDE",
            "series_ticker": "KXNBA",
            "title": "NBA game winner",
            "yes_bid": 0.45,
            "yes_ask": 0.49,
            "no_bid": 0.47,
            "no_ask": 0.51,
            "yes_top_size": 30,
            "no_top_size": 1,
        }

        decision = bot._aion_select_bidirectional_side(
            ticker=market["ticker"],
            family="KXNBA",
            lane="SPORTS",
            market=market,
            quote_diag={
                "yes_edge_after_cost": 0.012,
                "no_edge_after_cost": 0.012,
                "yes_top_size": 30,
                "no_top_size": 1,
            },
        )

        self.assertEqual(decision["selected_side"], "yes")
        self.assertGreater(decision["yes_fill_probability"], decision["no_fill_probability"])
        self.assertGreater(decision["selected_fill_probability"], 0.0)

    def test_sports_execution_quality_boosts_deep_tradeable_books(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA-EXEC-QUALITY",
            family="KXNBA",
            selected_side="yes",
            selected_price=0.49,
            yes_bid=0.45,
            yes_ask=0.49,
            no_bid=0.47,
            no_ask=0.51,
            yes_top_size=24,
            no_top_size=18,
            liquidity=110,
            volume=80,
            game_thesis_confidence=74,
            sports_regime="favorable",
            sports_momentum_classification="strong_positive",
        )
        market = {
            "ticker": q.ticker,
            "series_ticker": q.family,
            "status": "open",
            "title": "NBA game winner",
            "yes_bid": 0.45,
            "yes_ask": 0.49,
            "no_bid": 0.47,
            "no_ask": 0.51,
            "yes_top_size": 24,
            "no_top_size": 18,
            "liquidity_dollars": 110,
            "volume": 80,
            "game_thesis_confidence": 74,
        }
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "both_non_zero",
            "yes_top_size": 24,
            "no_top_size": 18,
            "both_sides_quoted": True,
            "liquidity_final": 110,
        }
        item = {
            "q": q,
            "family": q.family,
            "spread": 0.04,
            "raw_score": 76.0,
            "adjusted_score": 76.0,
            "expectancy_summary": {
                "final_edge": 0.022,
                "edge_after_cost": 0.022,
                "expectancy_score": 0.70,
                "game_thesis_confidence": 74,
                "sports_regime": "favorable",
                "sports_momentum_classification": "strong_positive",
            },
            "sports_edge_context": {"boost": 4.0, "tags": ["SPORTS_GOOD_BOOK"]},
            "quality_snapshot": {"selected_price": 0.49, "liquidity_depth": 110, "spread": 0.04, "confidence_score": 0.70},
        }

        with contextlib.redirect_stdout(io.StringIO()):
            allocation = bot._build_lane_allocation_context(item, market=market)
            context = bot._build_unified_opportunity_context(item, market=market, allocation=allocation, source="unit_test_execution_quality")

        self.assertEqual(allocation["lane"], "SPORTS")
        self.assertGreater(allocation["sports_execution_fill_probability"], 0.65)
        self.assertGreater(allocation["sports_execution_rank_adjustment"], 0.0)
        self.assertGreaterEqual(allocation["sports_execution_size_multiplier"], 1.0)
        self.assertGreater(context["sports_execution_rank_adjustment"], 0.0)
        self.assertIn("sports_execution_quality", context["reason"])

    def test_sports_execution_quality_downsizes_thin_books_without_blocking(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA-EXEC-THIN",
            family="KXNBA",
            selected_side="yes",
            selected_price=0.59,
            yes_bid=0.38,
            yes_ask=0.59,
            no_bid=0.41,
            no_ask=0.62,
            yes_top_size=0,
            no_top_size=1,
            liquidity=2,
            volume=3,
        )
        market = {
            "ticker": q.ticker,
            "series_ticker": q.family,
            "status": "open",
            "title": "NBA game winner",
            "yes_bid": 0.38,
            "yes_ask": 0.59,
            "no_bid": 0.41,
            "no_ask": 0.62,
            "yes_top_size": 0,
            "no_top_size": 1,
            "liquidity_dollars": 2,
            "volume": 3,
        }
        bot.get_market_liquidity_priority = lambda market_arg: {
            "top_book_state": "one_sided",
            "yes_top_size": 0,
            "no_top_size": 1,
            "both_sides_quoted": True,
            "liquidity_final": 2,
        }
        item = {
            "q": q,
            "family": q.family,
            "spread": 0.21,
            "raw_score": 70.0,
            "adjusted_score": 70.0,
            "expectancy_summary": {
                "final_edge": 0.018,
                "edge_after_cost": 0.018,
                "expectancy_score": 0.68,
            },
            "sports_edge_context": {"boost": 4.0, "tags": ["SPORTS_GOOD_BOOK"]},
            "quality_snapshot": {"selected_price": 0.59, "liquidity_depth": 2, "spread": 0.21, "confidence_score": 0.68},
        }

        with contextlib.redirect_stdout(io.StringIO()):
            allocation = bot._build_lane_allocation_context(item, market=market)
            context = bot._build_unified_opportunity_context(item, market=market, allocation=allocation, source="unit_test_execution_thin")

        self.assertEqual(allocation["lane"], "SPORTS")
        self.assertLess(allocation["sports_execution_rank_adjustment"], 0.0)
        self.assertLess(allocation["sports_execution_size_multiplier"], 1.0)
        self.assertGreater(context["unified_score"], 0.0)

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

        self.assertEqual(resolved, 0)
        self.assertEqual(
            getattr(bot, "last_fractional_kelly_result", {}).get("cap_reason"),
            "nonpositive_edge",
        )

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

    def test_position_sizing_caps_non_elite_to_account_percentage(self):
        bot = self._make_bot()
        bot.available_cash = 100.0
        q = SimpleNamespace(
            ticker="KXNCAABSPREAD-OKLA-CITADEL",
            family="KXNCAABSPREAD",
            ev_tier="A",
            ELITE_EDGE_CONFIDENCE=42.0,
            STRUCTURAL_EDGE_SCORE=44.0,
            CROSS_MARKET_EDGE_SCORE=25.0,
            expected_value=0.002,
            selected_side="yes",
            selected_price=0.51,
            yes_bid=0.50,
            yes_ask=0.51,
            no_bid=0.49,
            no_ask=0.50,
            yes_top_size=100,
            no_top_size=100,
            spread=0.01,
            orderbook_ready=True,
            quote_ready=True,
            unified_opportunity_score=64.0,
        )

        resolved = bot._resolve_dynamic_live_entry_size(
            q,
            explicit_size=30,
            payload={"ticker": q.ticker, "count": 30, "selected_side": "yes", "selected_price": 0.51},
            ev_context={
                "ticker": q.ticker,
                "family": q.family,
                "ev_tier": "A",
                "ELITE_EDGE_CONFIDENCE": 42.0,
                "STRUCTURAL_EDGE_SCORE": 44.0,
                "CROSS_MARKET_EDGE_SCORE": 25.0,
                "expected_value": 0.002,
                "unified_opportunity_score": 64.0,
            },
        )

        self.assertGreaterEqual(resolved, 1)
        self.assertLessEqual(resolved * q.selected_price, 5.0)
        self.assertEqual(getattr(q, "position_sizing_reason", None), "standard_account_pct_cap_requires_elite")

    def test_position_sizing_keeps_longshots_tiny_without_elite_evidence(self):
        bot = self._make_bot()
        bot.available_cash = 100.0
        q = SimpleNamespace(
            ticker="KXMLBHRRBI-TAIL-5PLUS",
            family="KXMLBHRRBI",
            ev_tier="B",
            ELITE_EDGE_CONFIDENCE=36.0,
            STRUCTURAL_EDGE_SCORE=35.0,
            CROSS_MARKET_EDGE_SCORE=22.0,
            expected_value=0.001,
            selected_side="yes",
            selected_price=0.03,
            yes_bid=0.02,
            yes_ask=0.03,
            no_bid=0.97,
            no_ask=0.98,
            yes_top_size=500,
            no_top_size=500,
            spread=0.01,
            orderbook_ready=True,
            quote_ready=True,
            sports_ladder_role="tail",
            unified_opportunity_score=61.0,
        )

        resolved = bot._resolve_dynamic_live_entry_size(
            q,
            explicit_size=150,
            payload={"ticker": q.ticker, "count": 150, "selected_side": "yes", "selected_price": 0.03},
            ev_context={
                "ticker": q.ticker,
                "family": q.family,
                "ev_tier": "B",
                "ELITE_EDGE_CONFIDENCE": 36.0,
                "STRUCTURAL_EDGE_SCORE": 35.0,
                "CROSS_MARKET_EDGE_SCORE": 22.0,
                "expected_value": 0.001,
                "sports_ladder_role": "tail",
                "unified_opportunity_score": 61.0,
            },
        )

        self.assertGreaterEqual(resolved, 1)
        self.assertLessEqual(resolved * q.selected_price, 1.0)
        self.assertEqual(getattr(q, "position_sizing_reason", None), "longshot_account_pct_cap")

    def test_position_sizing_allows_elite_above_standard_but_caps_at_elite_percentage(self):
        bot = self._make_bot()
        bot.available_cash = 100.0
        q = SimpleNamespace(
            ticker="KXMLBSTRIKEOUT-ELITE-TEST",
            family="KXMLBSTRIKEOUT",
            ev_tier="S",
            ev_sizing_multiplier=1.0,
            conviction_size_multiplier=1.0,
            elite_edge_size_multiplier=8.0,
            ELITE_EDGE_CONFIDENCE=92.0,
            STRUCTURAL_EDGE_SCORE=94.0,
            CROSS_MARKET_EDGE_SCORE=86.0,
            expected_roi=0.11,
            expected_value=0.038,
            ev_edge=0.055,
            estimated_win_probability=0.31,
            market_implied_probability=0.20,
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
            unified_opportunity_score=93.0,
            sports_execution_quality_score=88.0,
            sports_execution_fill_probability=0.82,
        )

        resolved = bot._resolve_dynamic_live_entry_size(
            q,
            explicit_size=1,
            payload={"ticker": q.ticker, "count": 1, "selected_side": "yes", "selected_price": 0.20},
            ev_context={
                "ticker": q.ticker,
                "family": q.family,
                "ev_tier": "S",
                "ELITE_EDGE_CONFIDENCE": 92.0,
                "STRUCTURAL_EDGE_SCORE": 94.0,
                "CROSS_MARKET_EDGE_SCORE": 86.0,
                "elite_edge_size_multiplier": 8.0,
                "expected_roi": 0.11,
                "expected_value": 0.038,
                "ev_edge": 0.055,
                "estimated_win_probability": 0.31,
                "market_implied_probability": 0.20,
                "unified_opportunity_score": 93.0,
                "sports_execution_quality_score": 88.0,
                "sports_execution_fill_probability": 0.82,
            },
        )

        self.assertGreater(resolved * q.selected_price, 5.0)
        self.assertLessEqual(resolved * q.selected_price, 10.0)
        self.assertIn(getattr(q, "position_sizing_reason", None), {"elite_account_pct_cap", "elite_within_account_pct_cap"})

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

    def test_low_bankroll_concentrates_capital_in_best_opportunity(self):
        bot = self._make_bot()
        bot.available_cash = 10.0
        best = {
            "ticker": "KXMLBMONEYLINE-BEST",
            "lane": "SPORTS",
            "family": "KXMLBMONEYLINE",
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

    def test_live_candidate_terminal_accounting_balances_every_sports_candidate(self):
        bot = self._make_bot()
        future_start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        markets = [
            {"ticker": "KXNBA-FUTURE", "series_ticker": "KXNBA", "occurrence_datetime": future_start},
            {"ticker": "KXNBA-NOQUOTE", "series_ticker": "KXNBA", "in_play": True},
            {"ticker": "KXNBA-NOSIDE", "series_ticker": "KXNBA", "in_play": True},
            {"ticker": "KXNBA-NOPRICE", "series_ticker": "KXNBA", "in_play": True},
            {"ticker": "KXNBA-LOWEDGE", "series_ticker": "KXNBA", "in_play": True},
            {"ticker": "KXNBA-SUBMIT", "series_ticker": "KXNBA", "in_play": True},
        ]
        quotes_by_ticker = {
            "KXNBA-FUTURE": SimpleNamespace(ticker="KXNBA-FUTURE", selected_side="yes", selected_price=0.45, yes_ask=0.45),
            "KXNBA-NOSIDE": SimpleNamespace(ticker="KXNBA-NOSIDE", selected_price=0.45, yes_ask=0.45),
            "KXNBA-NOPRICE": SimpleNamespace(ticker="KXNBA-NOPRICE", selected_side="yes"),
            "KXNBA-LOWEDGE": SimpleNamespace(ticker="KXNBA-LOWEDGE", selected_side="yes", selected_price=0.45, yes_ask=0.45),
            "KXNBA-SUBMIT": SimpleNamespace(ticker="KXNBA-SUBMIT", selected_side="yes", selected_price=0.45, yes_ask=0.45),
        }
        readiness_records = [
            {
                "ticker": "KXNBA-LOWEDGE",
                "decision_reason": "edge_below_cost",
                "sports_edge_after_cost": -0.01,
                "liquidity": 120,
                "spread_bucket": "tight",
            }
        ]

        accounting = bot._build_aion_live_candidate_terminal_accounting(
            markets=markets,
            quotes_by_ticker=quotes_by_ticker,
            quote_hydration_summary={"attempted": 6, "success": 5, "failure": 1},
            trade_readiness_records=readiness_records,
            selected_ticker="KXNBA-SUBMIT",
            submit_attempted=True,
            stage="unit_test",
        )

        outcomes = accounting["terminal_outcomes"]
        self.assertTrue(accounting["terminal_balance_ok"])
        self.assertEqual(accounting["terminal_total"], len(markets))
        self.assertEqual(sum(outcomes.values()), len(markets))
        self.assertEqual(outcomes["future_start_time_block"], 1)
        self.assertEqual(outcomes["no_quote"], 1)
        self.assertEqual(outcomes["no_side"], 1)
        self.assertEqual(outcomes["no_price"], 1)
        self.assertEqual(outcomes["low_edge"], 1)
        self.assertEqual(outcomes["submit_attempt"], 1)

    def test_live_candidate_terminal_accounting_fail_log_emits_on_mismatch(self):
        bot = self._make_bot()
        bad_accounting = {
            "stage": "unit_test",
            "sports_candidates": 3,
            "terminal_total": 2,
            "terminal_balance_ok": False,
            "terminal_outcomes": {"future_start_time_block": 2},
        }
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            bot._emit_aion_live_candidate_terminal_accounting(bad_accounting)
        output = stream.getvalue()
        self.assertIn("[AION-FUNNEL-ACCOUNTING]", output)
        self.assertIn("[AION-FUNNEL-ACCOUNTING-FAIL]", output)


if __name__ == "__main__":
    unittest.main()

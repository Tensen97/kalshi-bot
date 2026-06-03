import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import kalshi_web as kw


class ExpectancyActivationTests(unittest.TestCase):
    def _make_bot(self):
        bot = kw.KalshiSniperBot.__new__(kw.KalshiSniperBot)
        bot._get_live_session_mode = lambda: {"mode": "neutral"}
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False}
        bot.relaxation_trade_day = time.strftime("%Y-%m-%d")
        bot.relaxation_trades_today = 0
        bot.relaxation_trade_pnl_today = 0.0
        bot.relaxation_trade_tickers = set()
        bot.trading_halted = False
        bot.realized_pnl = 0.0
        bot.live_realized_pnl_today = 0.0
        bot.live_family_perf_stats = {}
        bot.live_realized_pnl_by_family_today = {}
        bot.live_recent_family_pnl_events = []
        bot.live_post_loss_ticker_cooldowns = {}
        bot.live_recent_negative_exit_events = []
        bot.live_positions = []
        bot.positions = []
        bot.market_cooldowns = {}
        bot.family_profit_memory_cache = {"loaded_at": 0.0, "stats": {}}
        bot.family_profit_memory_log_markers = {}
        bot.seraph_memory_db_path = "/tmp/aion_missing_seraph_memory.sqlite3"
        bot.trade_log_csv = "/tmp/aion_missing_trade_history.csv"
        bot.live_post_loss_family_cooldowns = {
            "KXNBA": {
                "ticker": "OTHER",
                "expires_at": time.time() + 120.0,
                "reason": "family_cooldown",
            }
        }
        bot.aion_expectancy_state_last_save_ts = 0.0
        return bot

    def test_env_helpers_parse_expected_values(self):
        with patch.dict(os.environ, {
            "AION_BOOL_TEST": "true",
            "AION_INT_TEST": "7",
            "AION_FLOAT_TEST": "0.125",
        }, clear=False):
            self.assertTrue(kw.get_env_bool("AION_BOOL_TEST", False))
            self.assertEqual(kw.get_env_int("AION_INT_TEST", 0), 7)
            self.assertAlmostEqual(kw.get_env_float("AION_FLOAT_TEST", 0.0), 0.125, places=6)
            self.assertFalse(kw.get_env_bool("AION_BOOL_TEST_MISSING", False))
            self.assertEqual(kw.get_env_int("AION_INT_TEST_MISSING", 9), 9)
            self.assertAlmostEqual(kw.get_env_float("AION_FLOAT_TEST_MISSING", 0.75), 0.75, places=6)

    def test_expectancy_score_produces_fastpath_signals(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXNBA_TEST",
            family="KXNBA",
            yes_bid=0.73,
            yes_ask=0.75,
        )
        result = bot._build_expectancy_score(
            market={"ticker": "KXNBA_TEST", "family": "KXNBA", "minutes_to_event": 45},
            q=q,
            family="KXNBA",
            spread=0.02,
            candidate_score=91.0,
            score_breakdown={"liquidity": 18.0, "spread": 12.0, "timing": 11.0, "confidence": 12.0},
            entry_timing_observation={"phase55_status": "trigger_wait"},
            predictive_regime_summary={"predictive_regime_score": 0.82, "volatility_state": "stable"},
            adaptive_confidence_summary={"adaptive_confidence_modifier": 0.04},
            family_rotation_summary={"family_rotation_score": 0.80},
            queue_summary={"queue_state": "near_ready"},
            microstructure_summary={
                "quote_stability_score": 0.91,
                "edge_durability_score": 0.86,
                "execution_survivability_score": 0.81,
                "liquidity_quality": 0.78,
                "volatility_state": "stable",
            },
            learning_summary={"candidate_learning_score": 0.7},
            family_learning_summary={"family_learning_score": 0.79, "liquidity_reliability": 0.82},
            persistence_summary={"adaptive_persistence_score": 0.76},
            sports_momentum={"volatility_state": "stable"},
            sports_context={"sports_edge_after_cost": 0.03, "sports_spread_bucket": "tight"},
            near_miss_count=3,
            phase55_status="trigger_wait",
        )
        self.assertIn("expectancy_score", result)
        self.assertGreater(result["expectancy_score"], 0.55)
        self.assertTrue(result["positive_expectancy"])
        self.assertTrue(result["phase55_fastpath_allowed"])
        self.assertTrue(result["micro_pullback_relax_allowed"])
        self.assertTrue(result["controlled_cooldown_bypass_allowed"])
        self.assertEqual(result["spread_bucket"], "tight")
        self.assertEqual(result["queue_state"], "near_ready")

    def test_controlled_family_cooldown_bypass_eligibility(self):
        bot = self._make_bot()
        allowed, details = bot._should_allow_controlled_family_cooldown_bypass(
            ticker="KXNBA_TEST",
            family="KXNBA",
            gate_record={
                "expectancy_score": 0.81,
                "sports_edge_after_cost": 0.03,
                "spread_bucket": "tight",
            },
            now_ts=time.time(),
        )
        self.assertTrue(allowed)
        self.assertEqual(details["family"], "KXNBA")
        self.assertGreater(details["expectancy_score"], 0.80)
        self.assertGreater(details["sports_edge_after_cost"], 0.0)

    def test_post_loss_family_cooldown_becomes_size_pressure(self):
        bot = self._make_bot()
        pressure = bot._get_post_loss_entry_pressure(
            ticker="KXNBA_TEST",
            family="KXNBA",
            now_ts=time.time(),
        )
        self.assertTrue(pressure["allowed"])
        self.assertFalse(pressure["blocked"])
        self.assertEqual(pressure["reason"], "post_loss_family_soft_pressure")
        self.assertEqual(pressure["scope"], "family")
        self.assertGreater(pressure["size_multiplier"], 0.0)
        self.assertLess(pressure["size_multiplier"], 1.0)

    def test_post_loss_ticker_cooldown_remains_hard_block(self):
        bot = self._make_bot()
        now_ts = time.time()
        bot.live_post_loss_ticker_cooldowns["KXNBA_TEST"] = {
            "ticker": "KXNBA_TEST",
            "family": "KXNBA",
            "realized_pnl": -0.11,
            "reason": "stop_loss",
            "expires_at": now_ts + 120.0,
        }
        pressure = bot._get_post_loss_entry_pressure(
            ticker="KXNBA_TEST",
            family="KXNBA",
            now_ts=now_ts,
        )
        self.assertFalse(pressure["allowed"])
        self.assertTrue(pressure["blocked"])
        self.assertEqual(pressure["reason"], "post_loss_ticker_cooldown")
        self.assertEqual(pressure["scope"], "ticker")
        self.assertEqual(pressure["size_multiplier"], 0.0)

    def test_entry_ready_buy_records_seed_final_candidate_when_ranker_empty(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBSPREAD-UNIT",
            family="KXMLBSPREAD",
            selected_side=None,
            selected_price=None,
            yes_bid=0.51,
            yes_ask=0.53,
            no_bid=0.47,
            no_ask=0.49,
            liquidity=75.0,
            volume=500.0,
        )
        market = {
            "ticker": q.ticker,
            "series_ticker": "KXMLBSPREAD",
            "family": "KXMLBSPREAD",
            "status": "open",
            "can_open": True,
            "can_close": True,
        }
        summary = bot._seed_entry_ready_candidates_from_readiness(
            trade_readiness_records=[
                {
                    "ticker": q.ticker,
                    "family": "KXMLBSPREAD",
                    "decision": "BUY",
                    "final_disposition": "entry_ready",
                    "edge_after_cost": 0.04,
                    "selected_side": "yes",
                    "selected_price": 0.53,
                    "spread": 0.02,
                    "spread_bucket": "tight",
                    "confidence": 0.78,
                    "liquidity": 75.0,
                }
            ],
            quotes_by_ticker={q.ticker: q},
            markets_by_ticker={q.ticker: market},
        )
        self.assertEqual(summary["seeded_count"], 1)
        score, seeded_q = summary["candidates"][0]
        self.assertGreater(score, 0.0)
        self.assertIs(seeded_q, q)
        self.assertEqual(q.selected_side, "yes")
        self.assertAlmostEqual(q.selected_price, 0.53, places=4)
        self.assertTrue(q.entry_ready_seeded)

    def test_entry_ready_seed_uses_gate_records_when_readiness_list_empty(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBKS-UNIT",
            family="KXMLBKS",
            selected_side="yes",
            selected_price=0.46,
            yes_bid=0.45,
            yes_ask=0.46,
            no_bid=0.54,
            no_ask=0.55,
            liquidity=25.0,
            volume=120.0,
        )
        market = {
            "ticker": q.ticker,
            "series_ticker": "KXMLBKS",
            "family": "KXMLBKS",
            "status": "open",
            "can_open": True,
            "can_close": True,
        }
        bot._get_realm_vv_live_execution_gate_record = lambda ticker, stage=None: {
            "ticker": ticker,
            "family": "KXMLBKS",
            "decision": "BUY",
            "realm_vv_status": "entry_ready",
            "edge_after_cost": 0.03,
            "selected_side": "yes",
            "selected_price": 0.46,
        }
        summary = bot._seed_entry_ready_candidates_from_readiness(
            trade_readiness_records=[],
            markets=[market],
            quotes_by_ticker={q.ticker: q},
            markets_by_ticker={q.ticker: market},
        )
        self.assertEqual(summary["seeded_count"], 1)
        self.assertEqual(summary["top"][0]["ticker"], q.ticker)

    def test_entry_ready_seed_does_not_block_success_reason(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXMLBTOTAL-UNIT",
            family="KXMLBTOTAL",
            selected_side="no",
            selected_price=0.48,
            yes_bid=0.51,
            yes_ask=0.52,
            no_bid=0.47,
            no_ask=0.48,
            liquidity=42.0,
            volume=220.0,
        )
        summary = bot._seed_entry_ready_candidates_from_readiness(
            trade_readiness_records=[
                {
                    "ticker": q.ticker,
                    "family": "KXMLBTOTAL",
                    "decision": "BUY",
                    "final_disposition": "entry_ready",
                    "edge_after_cost": 0.025,
                    "selected_side": "no",
                    "selected_price": 0.48,
                    "canonical_final_block_reason": "all_guards_passed",
                }
            ],
            quotes_by_ticker={q.ticker: q},
            markets_by_ticker={
                q.ticker: {
                    "ticker": q.ticker,
                    "series_ticker": "KXMLBTOTAL",
                    "family": "KXMLBTOTAL",
                    "status": "open",
                    "can_open": True,
                    "can_close": True,
                }
            },
        )
        self.assertEqual(summary["seeded_count"], 1)
        self.assertEqual(summary["skipped"].get("record_hard_block", 0), 0)
        self.assertEqual(q.selected_side, "no")

    def test_entry_ready_seed_preserves_atp_challenger_leak_block(self):
        bot = self._make_bot()
        q = SimpleNamespace(
            ticker="KXATPCHALLENGERMATCH-UNIT",
            family="KXATPCHALLENGERMATCH",
            selected_side="yes",
            selected_price=0.53,
            yes_bid=0.51,
            yes_ask=0.53,
            no_bid=0.47,
            no_ask=0.49,
            liquidity=75.0,
            volume=500.0,
        )
        summary = bot._seed_entry_ready_candidates_from_readiness(
            trade_readiness_records=[
                {
                    "ticker": q.ticker,
                    "family": "KXATPCHALLENGERMATCH",
                    "decision": "BUY",
                    "final_disposition": "entry_ready",
                    "edge_after_cost": 0.20,
                    "selected_side": "yes",
                    "selected_price": 0.53,
                }
            ],
            quotes_by_ticker={q.ticker: q},
            markets_by_ticker={
                q.ticker: {
                    "ticker": q.ticker,
                    "series_ticker": "KXATPCHALLENGERMATCH",
                    "family": "KXATPCHALLENGERMATCH",
                    "status": "open",
                    "can_open": True,
                    "can_close": True,
                }
            },
        )
        self.assertEqual(summary["seeded_count"], 0)
        self.assertEqual(summary["skipped"].get("recent_pnl_leak_kxatpchallenger"), 1)

    def test_expectancy_state_round_trip(self):
        bot = self._make_bot()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "aion_expectancy_state.json")
            bot._get_aion_expectancy_state_path = lambda: state_path
            bot.aion_expectancy_state_file = state_path
            bot.aion_expectancy_state = bot._build_aion_expectancy_default_state()
            bot._refresh_aion_expectancy_state(
                shadow_trade_summary={
                    "ticker": "KXNBA_TEST",
                    "family": "KXNBA",
                    "score": 0.73,
                    "decision_reason": "near_pass",
                },
                dashboard_summary={"watchlist_count": 1},
                force_save=True,
            )
            self.assertTrue(os.path.exists(state_path))
            with open(state_path) as fh:
                persisted = json.load(fh)
            self.assertIn("shadow_trades", persisted)
            self.assertEqual(persisted["shadow_trades"][0]["ticker"], "KXNBA_TEST")
            self.assertEqual(persisted["daily_dashboard"]["watchlist_count"], 1)

            reloaded = bot._load_aion_expectancy_state()
            self.assertIn("shadow_trades", reloaded)
            self.assertGreaterEqual(len(reloaded["shadow_trades"]), 1)

    def test_family_profit_memory_reads_learning_archive_trade_closes(self):
        bot = self._make_bot()
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, "aion_learning_archive.jsonl")
            bot.aion_learning_archive_file = archive_path
            bot.family_profit_memory_learning_archive_enabled = True
            events = [
                {"event_type": "candidate_seen", "ticker": "KXARCH-IGNORED", "family": "KXARCH"},
                {
                    "event_type": "trade_closed",
                    "timestamp_utc": "2026-06-03T01:00:00+00:00",
                    "close_timestamp_utc": "2026-06-03T01:00:00+00:00",
                    "ticker": "KXARCH-OLD",
                    "family": "KXARCH",
                    "pnl_dollars": 0.40,
                    "result": "win",
                },
                {
                    "event_type": "trade_closed",
                    "timestamp_utc": "2026-06-03T02:00:00+00:00",
                    "close_timestamp_utc": "2026-06-03T02:00:00+00:00",
                    "ticker": "KXARCH-NEW",
                    "family": "KXARCH",
                    "pnl_dollars": -0.25,
                    "result": "loss",
                },
            ]
            with open(archive_path, "w", encoding="utf-8") as fh:
                for event in events:
                    fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")

            stats = bot._get_family_profit_memory_stats("KXARCH", refresh=True)

        self.assertEqual(stats["trade_count"], 2)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertAlmostEqual(stats["family_pnl"], 0.15, places=6)
        self.assertAlmostEqual(stats["recent_pnl"], 0.15, places=6)


if __name__ == "__main__":
    unittest.main()

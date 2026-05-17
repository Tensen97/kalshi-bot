import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import kalshi_web as kw


class ProfitTargetAwarenessTests(unittest.TestCase):
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
        bot.live_perf_day = time.strftime("%Y-%m-%d")
        bot.live_perf_trades_today = 0
        bot.live_perf_wins_today = 0
        bot.live_perf_losses_today = 0
        bot.live_perf_day_start_ts = time.time() - 3 * 3600.0
        bot.last_live_trade_ts = time.time() - 3 * 3600.0
        bot.trade_readiness_last_no_trade_ts = time.time() - 4 * 3600.0
        bot.live_post_loss_family_cooldowns = {}
        bot.aion_expectancy_state_last_save_ts = 0.0
        bot.aion_expectancy_state = bot._build_aion_expectancy_default_state()
        return bot

    def test_profit_target_env_helpers_parse_expected_values(self):
        with patch.dict(os.environ, {
            "AION_DAILY_PROFIT_TARGET": "55",
            "AION_WEEKLY_PROFIT_TARGET": "275",
            "AION_MONTHLY_PROFIT_TARGET": "1100",
            "AION_PROFIT_TARGET_MODE": "adaptive_guidance",
            "AION_PROFIT_EXPANSION_ENABLED": "1",
        }, clear=False):
            self.assertAlmostEqual(kw.get_env_float("AION_DAILY_PROFIT_TARGET", 0.0), 55.0)
            self.assertAlmostEqual(kw.get_env_float("AION_WEEKLY_PROFIT_TARGET", 0.0), 275.0)
            self.assertAlmostEqual(kw.get_env_float("AION_MONTHLY_PROFIT_TARGET", 0.0), 1100.0)
            self.assertEqual((os.environ.get("AION_PROFIT_TARGET_MODE") or "").strip().lower(), "adaptive_guidance")
            self.assertTrue(kw.get_env_bool("AION_PROFIT_EXPANSION_ENABLED", False))

    def test_profit_target_guidance_expands_only_for_positive_expectancy(self):
        bot = self._make_bot()
        bot.live_realized_pnl_today = 11.5
        bot.live_perf_trades_today = 2
        bot.live_perf_wins_today = 2
        bot.live_perf_losses_today = 0
        guidance = bot._build_profit_target_guidance(
            trade_readiness_records=[
                {"expectancy_score": 0.78, "candidate_score": 92.0, "family": "KXNBA", "decision": "BUY"},
                {"expectancy_score": 0.66, "candidate_score": 84.0, "family": "KXNBA", "decision": "SKIP"},
            ],
            failure_summary={"reason_counts": {"edge_below_cost": 1}},
            candidate_expectancy=0.78,
            candidate_score=92.0,
            spread_bucket="tight",
            quote_stability=0.88,
            edge_durability=0.81,
            execution_survivability=0.79,
            near_miss_count=3,
            family="KXNBA",
            ticker="KXNBA_TEST",
        )
        self.assertGreater(guidance["current_expansion_level"], 0.0)
        self.assertTrue(guidance["opportunity_expansion_active"] or guidance["current_expansion_level"] > 0.0)
        self.assertGreater(guidance["profit_target_priority_adjustment"], 0.0)
        self.assertLess(guidance["realized_pnl_today"], guidance["daily_profit_target"])
        self.assertGreater(guidance["opportunity_density"], 0.0)
        self.assertEqual(guidance["profit_target_mode"], "adaptive_guidance")

    def test_profit_target_defensive_mode_tightens_expansion(self):
        bot = self._make_bot()
        bot.live_realized_pnl_today = -18.0
        bot.live_perf_trades_today = 6
        bot.live_perf_wins_today = 1
        bot.live_perf_losses_today = 5
        bot.relaxation_trades_today = 2
        bot.relaxation_trade_pnl_today = -3.0
        guidance = bot._build_profit_target_guidance(
            trade_readiness_records=[
                {"expectancy_score": 0.61, "candidate_score": 78.0, "family": "KXNBA", "decision": "SKIP"},
            ],
            failure_summary={"reason_counts": {"edge_below_cost": 2}},
            candidate_expectancy=0.61,
            candidate_score=78.0,
            spread_bucket="tight",
            quote_stability=0.60,
            edge_durability=0.55,
            execution_survivability=0.53,
            near_miss_count=1,
            family="KXNBA",
            ticker="KXNBA_TEST",
        )
        self.assertTrue(guidance["defensive_mode"])
        self.assertLessEqual(guidance["current_expansion_level"], 0.35)
        self.assertEqual(guidance["profit_target_priority_adjustment"], 0.0)

    def test_profit_target_state_round_trip(self):
        bot = self._make_bot()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "aion_expectancy_state.json")
            bot._get_aion_expectancy_state_path = lambda: state_path
            bot.aion_expectancy_state_file = state_path
            bot.aion_expectancy_state = bot._build_aion_expectancy_default_state()
            bot._refresh_aion_expectancy_state(
                profit_target_summary={
                    "snapshot_day": time.strftime("%Y-%m-%d"),
                    "realized_pnl_today": 12.25,
                    "realized_pnl_week": 19.5,
                    "realized_pnl_month": 31.75,
                    "current_expansion_level": 0.42,
                    "opportunity_expansion_active": True,
                    "negative_expectancy_block_count": 4,
                    "profit_target_mode": "adaptive_guidance",
                },
                dashboard_summary={"watchlist_count": 2},
                force_save=True,
            )
            self.assertTrue(os.path.exists(state_path))
            with open(state_path) as fh:
                persisted = json.load(fh)
            self.assertIn("profit_history", persisted)
            self.assertIn("profit_target_dashboard", persisted)
            self.assertGreaterEqual(len(persisted["profit_history"]), 1)
            reloaded = bot._load_aion_expectancy_state()
            self.assertIn("profit_history", reloaded)
            self.assertIn("profit_target_guidance", reloaded)


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

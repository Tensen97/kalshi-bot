import unittest

from personality.market_psychology_engine import MarketPsychologyEngine


class MarketPsychologyEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = MarketPsychologyEngine(
            enabled=True,
            shadow_only=False,
            hard_block=True,
            min_score=0.35,
            score_weight=0.10,
        )

    def test_no_history(self):
        result = self.engine.analyze(history=None, market={"ticker": "TEST"}, family="KXBTC15M")
        self.assertEqual(result["psychology_regime"], "unknown")
        self.assertAlmostEqual(result["psychology_score"], 0.50, places=2)
        self.assertIn("insufficient_history", result["psychology_reasons"])

    def test_flat_dead_history(self):
        result = self.engine.analyze(history=[1.0] * 8, market={"ticker": "TEST"}, family="KXBTC15M")
        self.assertEqual(result["psychology_regime"], "dead")
        self.assertLess(result["psychology_score"], 0.45)
        self.assertLess(result["volatility_score"], 0.20)

    def test_choppy_alternating_history(self):
        history = [1.00, 1.02, 0.98, 1.03, 0.97, 1.04, 0.96, 1.05]
        result = self.engine.analyze(history=history, market={"ticker": "TEST"}, family="KXBTC15M")
        self.assertEqual(result["psychology_regime"], "chop")
        self.assertGreater(result["chop_risk_score"], 0.60)

    def test_strong_upward_trend(self):
        history = [1.00, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06, 1.07, 1.08]
        result = self.engine.analyze(history=history, market={"ticker": "TEST"}, family="KXBTC15M")
        self.assertIn(result["psychology_regime"], {"trend", "strong_trend"})
        self.assertGreater(result["trend_strength_score"], 0.65)
        self.assertGreater(result["directional_persistence_score"], 0.70)

    def test_strong_downward_trend(self):
        history = [1.00, 0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92]
        result = self.engine.analyze(history=history, market={"ticker": "TEST"}, family="KXBTC15M")
        self.assertIn(result["psychology_regime"], {"trend", "strong_trend"})
        self.assertGreater(result["trend_strength_score"], 0.65)
        self.assertGreater(result["directional_persistence_score"], 0.70)

    def test_volatile_reversal(self):
        history = [1.00, 1.05, 1.09, 1.06, 1.01, 0.97, 0.99, 1.00, 0.96]
        result = self.engine.analyze(history=history, market={"ticker": "TEST"}, family="KXBTC15M")
        self.assertIn(result["psychology_regime"], {"volatile_reversal", "chop", "trend"})
        self.assertGreater(result["fake_breakout_risk"], 0.50)
        self.assertGreater(result["exhaustion_risk"], 0.40)

    def test_malformed_history(self):
        history = [
            None,
            0,
            "bad",
            {"close": 1.00},
            {"open": 1.00, "high": 1.04, "low": 0.98, "close": 1.03},
            (1, 2, 3, 4),
            [1, 2],
        ]
        result = self.engine.analyze(history=history, market={"ticker": "TEST"}, family="KXBTC15M")
        self.assertGreaterEqual(result["history_points"], 3)
        self.assertIn(result["psychology_regime"], {"unknown", "weak_trend", "trend", "strong_trend", "chop", "dead", "volatile_reversal"})

    def test_dead_market_hard_block(self):
        result = self.engine.analyze(history=[1.0] * 8, market={"ticker": "TEST"}, family="KXBTC15M")
        adjustment = self.engine.score_adjustment(result, original_score=50.0)
        self.assertTrue(adjustment["blocked"])
        self.assertEqual(adjustment["block_reason"], "dead_market_low_volatility")


if __name__ == "__main__":
    unittest.main()

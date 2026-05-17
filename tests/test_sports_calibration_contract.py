import unittest

import kalshi_web as kw
from sports import calibration as sports_calibration


class SportsCalibrationContractTests(unittest.TestCase):
    def _make_bot(self):
        bot = kw.KalshiSniperBot.__new__(kw.KalshiSniperBot)
        return bot

    def test_module_dynamic_buffer_aliases(self):
        result = sports_calibration.compute_sports_dynamic_buffer(
            bot=None,
            ticker="KXMLBSPREAD-TEST",
            family="KXMLBSPREAD",
            spread=0.01,
            spread_bucket="ultra_tight",
            calibrated_probability=0.52,
            no_vig_probability=0.50,
            momentum_score=0.20,
            directional_consistency=0.78,
            orderbook_pressure="balanced",
            repricing_velocity=0.01,
            spread_delta=-0.01,
            quote_stability_trend=0.82,
            edge_durability_trend=0.80,
            family_learning_trend=0.76,
            predictive_regime_score=0.73,
            liquidity_quality=0.84,
            market_lifecycle_state="pregame_near",
            event_urgency="normal",
            estimated_cost=0.01,
        )
        self.assertIn("dynamic_buffer", result)
        self.assertIn("sports_dynamic_buffer", result)
        self.assertAlmostEqual(result["dynamic_buffer"], result["sports_dynamic_buffer"], places=6)

    def test_module_post_cost_edge_aliases(self):
        result = sports_calibration.compute_sports_post_cost_edge(
            bot=None,
            ticker="KXMLBSPREAD-TEST",
            family="KXMLBSPREAD",
            calibrated_probability=0.52,
            no_vig_probability=0.50,
            no_vig_odds=2.0,
            dynamic_buffer=0.01,
            estimated_cost=0.01,
            spread_bucket="tight",
            market_lifecycle_state="pregame_near",
        )
        self.assertIn("dynamic_buffer", result)
        self.assertIn("sports_dynamic_buffer", result)
        self.assertIn("sports_edge_raw", result)
        self.assertIn("raw_edge", result)
        self.assertIn("sports_post_cost_edge", result)

    def test_wrapper_normalizes_aliases(self):
        bot = self._make_bot()
        result = bot.compute_sports_post_cost_edge(
            ticker="KXMLBSPREAD-TEST",
            family="KXMLBSPREAD",
            calibrated_probability=0.52,
            no_vig_probability=0.50,
            no_vig_odds=2.0,
            dynamic_buffer=0.01,
            estimated_cost=0.01,
            spread_bucket="tight",
            market_lifecycle_state="pregame_near",
        )
        self.assertIn("dynamic_buffer", result)
        self.assertIn("sports_dynamic_buffer", result)
        self.assertIn("sports_edge_raw", result)
        self.assertIn("raw_edge", result)


if __name__ == "__main__":
    unittest.main()

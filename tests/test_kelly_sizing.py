import unittest

from kalshi_sniper.candidate_quality import calculate_fractional_kelly_size


class KellySizingTests(unittest.TestCase):
    def _candidate(self, **overrides):
        candidate = {
            "ticker": "KXSPORTS-TEST",
            "family": "KXNBA",
            "selected_side": "yes",
            "selected_price": 0.40,
            "estimated_win_probability": 0.55,
            "kelly_fraction_config": 0.50,
            "kelly_max_multiplier": 100.0,
            "max_dollars_per_trade": 1_000.0,
            "max_family_exposure_dollars": 1_000.0,
            "current_family_exposure_dollars": 0.0,
        }
        candidate.update(overrides)
        return candidate

    def test_positive_edge_produces_nonzero_size(self):
        result = calculate_fractional_kelly_size(self._candidate(), bankroll=100.0, base_size=10, max_size=1_000)
        self.assertGreater(result["final_contract_size"], 0)
        self.assertAlmostEqual(result["raw_kelly_fraction"], 0.25, places=6)
        self.assertAlmostEqual(result["fractional_kelly_fraction"], 0.125, places=6)

    def test_negative_edge_produces_zero_size(self):
        result = calculate_fractional_kelly_size(
            self._candidate(selected_price=0.50, estimated_win_probability=0.45),
            bankroll=100.0,
            base_size=10,
            max_size=1_000,
        )
        self.assertEqual(result["final_contract_size"], 0)
        self.assertEqual(result["cap_reason"], "nonpositive_edge")

    def test_fractional_kelly_reduces_raw_kelly(self):
        result = calculate_fractional_kelly_size(
            self._candidate(selected_price=0.40, estimated_win_probability=0.60, kelly_fraction_config=0.50),
            bankroll=100.0,
            base_size=10,
            max_size=1_000,
        )
        self.assertAlmostEqual(result["raw_kelly_fraction"], (0.60 - 0.40) / 0.60, places=6)
        self.assertAlmostEqual(result["fractional_kelly_fraction"], result["raw_kelly_fraction"] * 0.50, places=5)

    def test_caps_are_respected(self):
        result = calculate_fractional_kelly_size(
            self._candidate(
                selected_price=0.25,
                estimated_win_probability=0.70,
                max_dollars_per_trade=5.0,
            ),
            bankroll=1_000.0,
            base_size=10,
            max_size=1_000,
        )
        self.assertLessEqual(result["final_contract_size"] * result["price"], 5.0)
        self.assertIn("max_dollars_per_trade", result["cap_reasons"])

    def test_family_performance_adjusts_smoothly(self):
        hot = calculate_fractional_kelly_size(
            self._candidate(
                family_performance_stats={
                    "trade_count": 10,
                    "family_pnl": 8.0,
                    "recent_pnl": 3.0,
                    "win_rate": 0.68,
                    "wins": 7,
                    "losses": 3,
                }
            ),
            bankroll=100.0,
            base_size=10,
            max_size=1_000,
        )
        cold = calculate_fractional_kelly_size(
            self._candidate(
                family_performance_stats={
                    "trade_count": 10,
                    "family_pnl": -5.0,
                    "recent_pnl": -2.0,
                    "win_rate": 0.35,
                    "wins": 3,
                    "losses": 7,
                }
            ),
            bankroll=100.0,
            base_size=10,
            max_size=1_000,
        )
        self.assertGreater(hot["performance_multiplier"], 1.0)
        self.assertLess(cold["performance_multiplier"], 1.0)
        self.assertGreater(hot["final_contract_size"], cold["final_contract_size"])


if __name__ == "__main__":
    unittest.main()

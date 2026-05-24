import tempfile
import unittest
from pathlib import Path

from memory.aion_persistent_intelligence import AIONPersistentIntelligence


class DistilledMemoryTests(unittest.TestCase):
    def test_observation_outcome_signal_and_audit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "aion_persistent_intelligence.json"
            engine = AIONPersistentIntelligence(state_file=state_file, enabled=True)

            obs = engine.record_distilled_observation(
                {
                    "timestamp": 1779600000.0,
                    "ticker": "KXNBA1HTOTAL-TEST",
                    "family": "KXNBA1HTOTAL",
                    "category": "sports",
                    "price": 0.34,
                    "spread": 0.02,
                    "sports_tags": ["SPORTS_UNDERDOG_VALUE", "SPORTS_GOOD_BOOK"],
                    "candidate_score": 88.0,
                    "decision": "qualified",
                },
                throttle_seconds=60,
            )
            self.assertTrue(obs["ok"])
            self.assertFalse(obs["skipped"])

            for index in range(4):
                outcome = engine.record_distilled_outcome(
                    {
                        "timestamp": 1779600001.0 + index,
                        "ticker": f"KXNBA1HTOTAL-TEST-{index}",
                        "family": "KXNBA1HTOTAL",
                        "category": "sports",
                        "entry_price": 0.34,
                        "pnl_dollars": 0.25,
                        "result": "win",
                        "sports_tags": ["SPORTS_UNDERDOG_VALUE", "SPORTS_GOOD_BOOK"],
                        "elite_signal_used": True,
                        "elite_trader_handle": "public-alpha",
                    }
                )
                self.assertTrue(outcome["ok"])

            signal = engine.get_distilled_learning_signal(
                family="KXNBA1HTOTAL",
                category="sports",
                sports_tags=["SPORTS_UNDERDOG_VALUE", "SPORTS_GOOD_BOOK"],
                odds_bucket="20_35",
                book_quality_tag="BOOK_GOOD",
                elite_trader_handle="public-alpha",
            )
            self.assertGreater(signal["sports_boost_adjustment"], 0.0)
            self.assertGreater(signal["elite_trust_weight"], 1.0)

            audit = engine.audit_distilled_learning()
            self.assertEqual(audit["total_stored_observations"], 1)
            self.assertEqual(audit["total_stored_trade_outcomes"], 4)
            self.assertEqual(audit["sports_trade_outcome_count"], 4)
            self.assertEqual(audit["elite_signal_linked_outcome_count"], 4)


if __name__ == "__main__":
    unittest.main()

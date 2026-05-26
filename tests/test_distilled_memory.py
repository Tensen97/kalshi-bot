import json
import tempfile
import time
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

    def test_sports_history_backfill_signal_and_audit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "aion_persistent_intelligence.json"
            engine = AIONPersistentIntelligence(state_file=state_file, enabled=True)
            now = time.time()

            for index in range(30):
                result = engine.record_sports_history_market(
                    {
                        "timestamp": now - index * 3600,
                        "ticker": f"KXNBA1HTOTAL-HIST-{index}",
                        "market_title": "NBA first half total test",
                        "sport_type": "nba",
                        "family": "KXNBA1HTOTAL",
                        "category": "sports",
                        "yes_price": 0.30,
                        "no_price": 0.70,
                        "spread": 0.02,
                        "liquidity": 500,
                        "volume": 1200,
                        "settlement_result": "yes",
                        "data_source": "unit_test",
                        "backfill_run_id": "test-run",
                    },
                    persist=False,
                )
                self.assertTrue(result["ok"])
                self.assertFalse(result["skipped"])

            duplicate = engine.record_sports_history_market(
                {
                    "timestamp": now,
                    "ticker": "KXNBA1HTOTAL-HIST-0",
                    "market_title": "NBA first half total test",
                    "family": "KXNBA1HTOTAL",
                    "yes_price": 0.30,
                    "no_price": 0.70,
                    "settlement_result": "yes",
                    "data_source": "unit_test",
                },
                persist=False,
            )
            self.assertTrue(duplicate["ok"])
            self.assertTrue(duplicate["skipped"])

            signal = engine.get_distilled_learning_signal(
                family="KXNBA1HTOTAL",
                category="sports",
                side="yes",
                odds_bucket="20_35",
                asymmetry_bucket="strong_asymmetry",
                liquidity_bucket="usable",
                spread_bucket="tight",
                sport_type="nba",
            )
            self.assertGreater(signal["sports_boost_adjustment"], 0.0)
            self.assertGreater(signal["sizing_confidence_multiplier"], 1.0)
            self.assertTrue(any(str(component.get("label", "")).startswith("sports_history_") for component in signal["components"]))

            audit = engine.audit_distilled_learning()
            self.assertEqual(audit["sports_history_record_count"], 30)
            self.assertIn("KXNBA1HTOTAL", audit["sports_history_families_covered"])
            self.assertGreaterEqual(audit["sports_history_family_count"], 1)
            self.assertTrue(audit["sports_history_asymmetry_bucket_performance"])

    def test_persistent_snapshot_tolerates_recursive_live_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "aion_persistent_intelligence.json"
            engine = AIONPersistentIntelligence(state_file=state_file, enabled=True)
            recursive_payload = {"ticker": "KXNBA-RECURSIVE"}
            recursive_payload["self"] = recursive_payload
            engine.distilled_learning["observations"].append(recursive_payload)

            snapshot = engine.snapshot()
            self.assertEqual(
                snapshot["distilled_learning"]["observations"][0]["self"],
                "<recursive_ref>",
            )
            self.assertTrue(engine.persist())
            with state_file.open() as handle:
                persisted = json.load(handle)
            self.assertEqual(
                persisted["distilled_learning"]["observations"][0]["self"],
                "<recursive_ref>",
            )

    def test_persist_recursion_error_uses_sanitized_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "aion_persistent_intelligence.json"
            engine = AIONPersistentIntelligence(state_file=state_file, enabled=True)
            engine.record_ticker_behavior(
                "KXNBA-RECURSION-FALLBACK",
                "KXNBA",
                spread_quality=0.5,
                stability_score=0.5,
            )

            def _raise_recursion():
                raise RecursionError("unit")

            engine.snapshot = _raise_recursion
            result = engine.persist()
            self.assertEqual(result["summary"]["snapshot_mode"], "recursion_safe")
            with state_file.open() as handle:
                persisted = json.load(handle)
            self.assertEqual(persisted["summary"]["snapshot_mode"], "recursion_safe")
            self.assertIn("sports:KXNBA-RECURSION-FALLBACK", persisted["ticker_profiles"])


if __name__ == "__main__":
    unittest.main()

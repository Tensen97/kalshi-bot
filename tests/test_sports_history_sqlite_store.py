import tempfile
import unittest
from pathlib import Path

from memory.aion_sports_history_store import AIONSportsHistoryStore


class SportsHistorySQLiteStoreTests(unittest.TestCase):
    def test_insert_dedupe_audit_and_signal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AIONSportsHistoryStore(db_path=Path(tmpdir) / "sports.sqlite")
            records = []
            for index in range(80):
                records.append(
                    {
                        "timestamp": f"2026-05-{1 + (index % 20):02d}T12:00:00Z",
                        "ticker": f"KXNBA1HTOTAL-TEST-{index}",
                        "market_title": "NBA first half total test",
                        "sport_type": "nba",
                        "family": "KXNBA1HTOTAL",
                        "category": "sports",
                        "yes_price": 0.25,
                        "yes_ask": 0.26,
                        "yes_bid": 0.24,
                        "spread": 0.02,
                        "liquidity": 350.0,
                        "volume": 1200.0,
                        "settlement_result": "yes",
                        "data_source": "unit_test",
                        "backfill_run_id": "unit-test",
                    }
                )
            result = store.insert_records(records, batch_size=25)
            self.assertEqual(result["stored"], 80)
            duplicate = store.insert_records(records, batch_size=25)
            self.assertEqual(duplicate["stored"], 0)
            self.assertEqual(duplicate["duplicates"], 80)

            audit = store.audit(recompute=True)
            self.assertEqual(audit["total_records"], 80)
            self.assertEqual(audit["duplicate_count"], 0)
            self.assertEqual(audit["wal_mode_status"], "wal")
            self.assertEqual(audit["records_by_family"][0]["key"], "KXNBA1HTOTAL")
            self.assertTrue(audit["family_performance"])

            signal = store.get_learning_signal(
                family="KXNBA1HTOTAL",
                sport_type="nba",
                odds_bucket="20_35",
                asymmetry_bucket="strong_asymmetry",
                liquidity_bucket="thin",
                spread_bucket="tight",
            )
            self.assertTrue(signal["components"])
            self.assertGreater(signal["sports_boost_adjustment"], 0.0)


if __name__ == "__main__":
    unittest.main()

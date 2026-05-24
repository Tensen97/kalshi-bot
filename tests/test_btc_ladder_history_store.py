import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memory.btc_ladder_history_store import BTCLadderHistoryStore


def _sample_btc_ladder_records(count_groups=12):
    base = datetime(2026, 5, 20, 14, 0, tzinfo=timezone.utc)
    records = []
    for group_idx in range(count_groups):
        opened = base - timedelta(hours=group_idx + 1)
        closed = opened + timedelta(hours=1)
        for strike in (70000, 70500, 71000):
            records.append(
                {
                    "ticker": f"KXBTC1H-UNIT-{group_idx}-{strike}",
                    "event_ticker": f"KXBTC1H-UNIT-{group_idx}",
                    "family": "KXBTC1H",
                    "title": f"Bitcoin above ${strike:,} at 3pm",
                    "open_time": opened.isoformat().replace("+00:00", "Z"),
                    "close_time": closed.isoformat().replace("+00:00", "Z"),
                    "source_time": closed.isoformat().replace("+00:00", "Z"),
                    "strike": strike,
                    "outcome": "yes",
                    "yes_bid": 0.33,
                    "yes_ask": 0.35,
                    "no_bid": 0.65,
                    "no_ask": 0.67,
                    "last_price": 0.34,
                    "liquidity": 250,
                    "volume": 1000,
                    "backfill_run_id": "unit-test",
                }
            )
    return records


class BTCLadderHistoryStoreTests(unittest.TestCase):
    def test_sqlite_store_dedupes_and_returns_advisory_learning_signal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = BTCLadderHistoryStore(db_path=Path(tmpdir) / "btc_ladder.sqlite")
            records = _sample_btc_ladder_records()

            first = store.insert_records(records, batch_size=10)
            second = store.insert_records(records, batch_size=10)
            audit = store.audit(recompute=True)
            read_only_store = BTCLadderHistoryStore(db_path=Path(tmpdir) / "btc_ladder.sqlite", initialize=False, read_only=True)
            signal = read_only_store.get_learning_signal(
                family="KXBTC1H",
                selected_side="yes",
                selected_price=0.34,
                spread=0.02,
                liquidity=250,
                time_to_expiry_bucket_value="hourly",
                session_bucket_value="us_morning",
                strike_distance_bucket_value="unknown",
                neighbor_gap_bucket_value="normal_strikes",
            )

            self.assertEqual(first["stored"], len(records))
            self.assertEqual(second["duplicates"], len(records))
            self.assertEqual(audit["total_records"], len(records))
            self.assertEqual(audit["wal_mode_status"], "wal")
            self.assertTrue(signal["available"])
            self.assertGreaterEqual(signal["sample_size"], 20)
            self.assertGreater(signal["historical_edge_boost"], 0.0)
            self.assertGreater(signal["sizing_confidence_multiplier"], 1.0)


if __name__ == "__main__":
    unittest.main()

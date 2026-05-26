import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aion_data_coverage import audit_kalshi_data_coverage, build_sports_data_signal


class DataCoverageAuditTests(unittest.TestCase):
    def test_audit_counts_sports_coverage_and_readiness(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_dir = root / "data"
            data_dir.mkdir(parents=True)
            archive = data_dir / "aion_learning_archive.jsonl"
            with archive.open("w", encoding="utf-8") as handle:
                for index in range(6):
                    handle.write(
                        json.dumps(
                            {
                                "event_type": "candidate_seen",
                                "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                "ticker": f"KXNBA1HTOTAL-TEST-{index}",
                                "family": "KXNBA1HTOTAL",
                                "price": 0.34,
                                "spread": 0.03,
                                "yes_bid": 0.31,
                                "yes_ask": 0.34,
                            }
                        )
                        + "\n"
                    )

            audit = audit_kalshi_data_coverage(root, include_telemetry=False)

            self.assertEqual(audit["totals"]["records"], 6)
            self.assertEqual(audit["sports"]["record_count"], 6)
            self.assertEqual(audit["sports"]["unique_markets_tickers"], 6)
            signal = build_sports_data_signal(audit, "KXNBA1HTOTAL")
            self.assertIn("SPORTS_DATA_ACTIVE", signal["tags"])
            self.assertGreater(signal["rank_boost"], 0.0)


if __name__ == "__main__":
    unittest.main()

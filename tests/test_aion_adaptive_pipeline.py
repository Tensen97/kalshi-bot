import csv
import json
import tempfile
import unittest
from pathlib import Path
from aion_adaptive.pipeline import import_closed_trades, offline_health_report, chronological_comparison, create_recovery_plan


class PipelineTests(unittest.TestCase):
    def test_import_diagnose_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            csv_path = Path(d) / "closed.csv"
            journal = Path(d) / "journal.jsonl"
            with csv_path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["trade_id","closed_at","status","strategy","family","side","gross_realized_pnl","fees"])
                writer.writeheader()
                for i in range(25):
                    writer.writerow(dict(trade_id=str(i), closed_at="2026-09-01T12:00:00Z",status="closed",
                                         strategy="original",family="MLB",side="no",gross_realized_pnl="-0.20",fees="0.01"))
            self.assertEqual(import_closed_trades(csv_path, journal)["imported"], 25)
            self.assertEqual(import_closed_trades(csv_path, journal)["duplicates"], 25)
            report = offline_health_report(journal, Path(d)/"health.json")
            self.assertEqual(report["segments"][0]["flag"], "negative_expectancy")
            self.assertFalse(report["live_changes"])
            self.assertEqual(report["proposed_actions"][0]["status"], "proposed_only")

    def test_reject_unclosed(self):
        with tempfile.TemporaryDirectory() as d:
            csv_path = Path(d)/"open.csv"
            csv_path.write_text("trade_id,closed_at,status,strategy,family,side,gross_realized_pnl,fees\na,2026-09-01T12:00:00Z,open,s,MLB,yes,1,0\n")
            with self.assertRaises(ValueError):
                import_closed_trades(csv_path, Path(d)/"journal")

    def test_comparison_no_auto_promotion(self):
        rows = []
        for i in range(40):
            day = f"2026-09-{i+1:02d}" if i < 30 else f"2026-10-{i-29:02d}"
            for strategy, pnl in [("a", 0.0), ("b", 1.0)]:
                rows.append({"strategy":strategy,"closed_at":day+"T12:00:00Z","net_pnl":pnl})
        result = chronological_comparison(rows)
        self.assertFalse(result["eligible_for_review"])
        self.assertEqual(result["reason"], "manual_review_required")

    def test_recovery_is_review_only(self):
        with tempfile.TemporaryDirectory() as d:
            result = create_recovery_plan(d, current_config={"a":1}, candidate_config={"a":2}, reason="test")
            self.assertEqual(result["status"], "human_review_required")
            self.assertFalse((Path(d)/"approved_config_snapshot.json").exists())


if __name__ == "__main__":
    unittest.main()

import csv
import tempfile
import unittest
from pathlib import Path

from tools.aion_profitability_audit import audit


class ProfitabilityAuditTests(unittest.TestCase):
    def make_csv(self, rows):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "trades.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["trade_id", "closed_at", "realized_pnl", "fees", "strategy"])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_high_win_rate_can_still_lose(self):
        rows = [{"trade_id": str(i), "closed_at": f"2026-01-{i:02d}", "realized_pnl": "2",
                 "fees": "0.25", "strategy": "A"} for i in range(1, 10)]
        rows.append({"trade_id": "10", "closed_at": "2026-01-10", "realized_pnl": "-30",
                     "fees": "0.25", "strategy": "B"})
        result = audit(self.make_csv(rows), "100")
        self.assertEqual(result["winning_trades"], 9)
        self.assertEqual(result["win_rate_pct"], 90.0)
        self.assertEqual(result["net_realized_pnl_usd"], "-14.50")
        self.assertEqual(result["closed_trade_equity_usd"], "85.50")
        self.assertEqual(result["by_strategy"]["B"]["net_pnl_usd"], "-30.25")

    def test_duplicate_ids_rejected(self):
        row = {"trade_id": "x", "closed_at": "2026-01-01", "realized_pnl": "1", "fees": "0"}
        with self.assertRaises(ValueError):
            audit(self.make_csv([row, row]))

    def test_negative_fees_rejected(self):
        row = {"trade_id": "x", "closed_at": "2026-01-01", "realized_pnl": "1", "fees": "-1"}
        with self.assertRaises(ValueError):
            audit(self.make_csv([row]))


if __name__ == "__main__":
    unittest.main()

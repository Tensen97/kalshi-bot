"""Offline adaptive intelligence regression tests."""
import tempfile
import unittest
from pathlib import Path
from aion_adaptive import TradeMemory, AdaptiveLab, assess_candidate, diagnose

class AdaptiveTests(unittest.TestCase):
    def test_memory_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            mem = TradeMemory(Path(d) / "trades.jsonl")
            row = dict(trade_id="a", closed_at="2026-10-13T00:00:00Z", strategy="s", family="MLB", side="yes", net_pnl=-0.1)
            self.assertTrue(mem.append(row))
            self.assertFalse(mem.append(row))
            self.assertEqual(len(mem.read()), 1)

    def test_bad_probability(self):
        with tempfile.TemporaryDirectory() as d:
            row = dict(trade_id="a", closed_at="2026-10-13T00:00:00Z", strategy="s", family="MLB", side="yes", net_pnl=1, model_probability=2)
            with self.assertRaises(ValueError): TradeMemory(Path(d) / "trades.jsonl").append(row)

    def test_evaluation_does_not_promote(self):
        with tempfile.TemporaryDirectory() as d:
            lab = AdaptiveLab(d)
            candidate = lab.propose("experiment", {"threshold": 0.1}, "test")
            result = lab.evaluate(candidate, [0.0]*40, [0.1]*40)
            self.assertTrue(result["eligible_for_review"])
            self.assertEqual(lab.record_experiment(candidate, result)["deployment"], "never_automatic")
            self.assertFalse(lab.register_model("prob", "v1", {"brier": 0.2}, validation_window="2026-Q3")["active"])
            self.assertEqual(lab.code_change_proposal("Improve", "test", ["a.py"], ["test_a"])["status"], "human_review_required")

    def test_small_samples_rejected(self):
        self.assertFalse(assess_candidate([1], [2])["eligible_for_review"])
        self.assertEqual(diagnose([{"family":"MLB","side":"yes","strategy":"x","net_pnl":-1}])[0]["flag"], "insufficient_data")

if __name__ == "__main__": unittest.main()

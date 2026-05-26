import json
import tempfile
import unittest
from pathlib import Path

from probability.sports_ml import (
    build_training_examples,
    get_sports_ml_probability_adjustment,
    train_sports_ml_probability_model,
)


class SportsMLProbabilityTests(unittest.TestCase):
    def test_missing_model_is_neutral_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_model = str(Path(tmpdir) / "missing.json")
            context = get_sports_ml_probability_adjustment(
                {
                    "ticker": "KXMLBMATCH-TEST",
                    "family": "KXMLBMATCH",
                    "category": "sports",
                    "side": "yes",
                    "price": 0.44,
                    "ev_score": 0.03,
                },
                model_path=missing_model,
            )
            self.assertTrue(context["allowed"])
            self.assertFalse(context["hard_reject"])
            self.assertTrue(context["fallback_used"])
            self.assertEqual(context["rank_adjustment"], 0.0)
            self.assertEqual(context["size_multiplier"], 1.0)

    def test_training_requires_real_sports_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "archive.jsonl"
            rows = [
                {
                    "event_type": "trade_closed",
                    "category": "sports",
                    "family": "KXMLBMATCH",
                    "ticker": "KXMLBMATCH-ONLY-LOSS",
                    "contracts": 1,
                    "pnl_dollars": 0.0,
                    "won": False,
                    "exit_reason": "position_reconcile_closed",
                }
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            examples = build_training_examples([path])
            self.assertEqual(examples, [])

    def test_trained_model_softly_adjusts_probability(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / "archive.jsonl"
            rows = []
            for i in range(4):
                rows.append(
                    {
                        "event_type": "candidate_seen",
                        "category": "sports",
                        "family": "KXMLBMATCH",
                        "ticker": f"KXMLBMATCH-WIN-{i}",
                        "side": "yes",
                        "price": 0.42,
                        "spread": 0.02,
                        "liquidity_dollars": 80,
                        "ev_score": 0.08,
                        "sports_execution_quality_score": 80,
                    }
                )
                rows.append(
                    {
                        "event_type": "trade_closed",
                        "category": "sports",
                        "family": "KXMLBMATCH",
                        "ticker": f"KXMLBMATCH-WIN-{i}",
                        "contracts": 1,
                        "pnl_dollars": 0.18,
                        "won": True,
                    }
                )
            for i in range(4):
                rows.append(
                    {
                        "event_type": "candidate_seen",
                        "category": "sports",
                        "family": "KXMLBMATCH",
                        "ticker": f"KXMLBMATCH-LOSS-{i}",
                        "side": "yes",
                        "price": 0.62,
                        "spread": 0.18,
                        "liquidity_dollars": 1,
                        "ev_score": -0.04,
                        "sports_execution_quality_score": 20,
                    }
                )
                rows.append(
                    {
                        "event_type": "trade_closed",
                        "category": "sports",
                        "family": "KXMLBMATCH",
                        "ticker": f"KXMLBMATCH-LOSS-{i}",
                        "contracts": 1,
                        "pnl_dollars": -0.20,
                        "won": False,
                    }
                )
            archive.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            model_path = Path(tmpdir) / "model.json"
            summary_path = Path(tmpdir) / "summary.json"
            schema_path = Path(tmpdir) / "schema.json"
            summary = train_sports_ml_probability_model(
                [archive],
                model_path=model_path,
                summary_path=summary_path,
                schema_path=schema_path,
                min_examples=4,
            )
            self.assertTrue(summary["ok"])
            strong = get_sports_ml_probability_adjustment(
                {
                    "category": "sports",
                    "family": "KXMLBMATCH",
                    "ticker": "KXMLBMATCH-LIVE-STRONG",
                    "side": "yes",
                    "price": 0.42,
                    "spread": 0.02,
                    "liquidity_dollars": 80,
                    "ev_score": 0.08,
                    "sports_execution_quality_score": 80,
                },
                model_path=model_path,
            )
            weak = get_sports_ml_probability_adjustment(
                {
                    "category": "sports",
                    "family": "KXMLBMATCH",
                    "ticker": "KXMLBMATCH-LIVE-WEAK",
                    "side": "yes",
                    "price": 0.62,
                    "spread": 0.18,
                    "liquidity_dollars": 1,
                    "ev_score": -0.04,
                    "sports_execution_quality_score": 20,
                },
                model_path=model_path,
            )
            self.assertFalse(strong["fallback_used"])
            self.assertGreaterEqual(strong["final_probability"], weak["final_probability"])
            self.assertGreaterEqual(strong["rank_adjustment"], weak["rank_adjustment"])


if __name__ == "__main__":
    unittest.main()

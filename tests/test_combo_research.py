from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import combo_research


class _Quote:
    def __init__(self, ticker, price, confidence, edge):
        self.ticker = ticker
        self.selected_side = "YES"
        self.selected_price = price
        self.yes_ask = price
        self.volume = 2000
        self.liquidity = 1000
        self.probability_confidence = confidence
        self.edge_after_cost = edge
        self.estimated_win_probability = min(0.95, price + edge)


class _Bot:
    @staticmethod
    def _is_sports_market_or_family(family):
        return str(family or "").upper() in {"NBA", "MLB", "NFL", "NHL", "SPORTS"}


def _candidate(ticker, family, price, confidence, edge):
    quote = _Quote(ticker, price, confidence, edge)
    return {
        "ticker": ticker,
        "family": family,
        "lane": "SPORTS",
        "q": quote,
        "market": {"ticker": ticker, "event_ticker": f"EV-{ticker}", "title": f"{family} market"},
        "score": 82.0,
        "candidate_score": 82.0,
        "spread": 0.04,
        "allocation": {
            "selected_side": "YES",
            "selected_price": price,
            "edge": edge,
            "confidence": confidence,
        },
        "expectancy_summary": {
            "estimated_win_probability": min(0.95, price + edge),
            "probability_confidence": confidence,
            "edge_after_cost": edge,
        },
        "decision_reason": "test_buy",
    }


class ComboResearchTests(unittest.TestCase):
    def _patch_runtime(self, output_path):
        return patch.multiple(
            combo_research,
            runtime_memory_file=lambda _name: str(output_path),
            record_persist_event=None,
            _maybe_export_obsidian_summary=lambda _summary: None,
        )

    def test_combo_research_builds_jsonl_from_sports_candidates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "aion_combo_research.jsonl"
            env = {
                "COMBO_RESEARCH_ENABLED": "true",
                "COMBO_RESEARCH_SIM_STAKE": "1",
                "COMBO_RESEARCH_MAX_LEGS": "3",
                "COMBO_RESEARCH_MIN_CANDIDATES": "2",
            }
            with patch.dict(os.environ, env, clear=False), self._patch_runtime(output_path):
                summary = combo_research.run_combo_research_cycle(
                    bot=_Bot(),
                    candidates=[
                        _candidate("KXNBA-1", "NBA", 0.54, 0.67, 0.04),
                        _candidate("KXMLB-1", "MLB", 0.50, 0.64, 0.035),
                        _candidate("KXNFL-1", "NFL", 0.58, 0.66, 0.025),
                    ],
                    trade_readiness_records=[],
                    markets=[],
                    selected_ticker="KXNBA-1",
                    source="test",
                )

            self.assertEqual(summary["built_this_cycle"], 4)
            self.assertEqual(summary["total_virtual_combos"], 4)
            self.assertTrue(output_path.exists())
            records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                {record["leg_count"] for record in records if record["event_type"] == "combo_built"},
                {2, 3},
            )
            self.assertTrue(all(record["event_type"] != "order_submit" for record in records))

    def test_combo_research_excludes_crypto_candidates(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "aion_combo_research.jsonl"
            env = {
                "COMBO_RESEARCH_ENABLED": "true",
                "COMBO_RESEARCH_MAX_LEGS": "4",
                "COMBO_RESEARCH_MIN_CANDIDATES": "2",
            }
            with patch.dict(os.environ, env, clear=False), self._patch_runtime(output_path):
                summary = combo_research.run_combo_research_cycle(
                    bot=_Bot(),
                    candidates=[
                        _candidate("KXBTC-1", "CRYPTO", 0.50, 0.70, 0.05),
                        _candidate("KXNBA-2", "NBA", 0.52, 0.64, 0.03),
                    ],
                    trade_readiness_records=[],
                    markets=[],
                    selected_ticker="KXNBA-2",
                    source="test",
                )

            self.assertEqual(summary["built_this_cycle"], 0)
            self.assertEqual(summary["candidate_count"], 1)
            self.assertEqual(summary["skip_reason"], "insufficient_sports_candidates")


if __name__ == "__main__":
    unittest.main()


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

    @staticmethod
    def _aion_select_bidirectional_side(*, q=None, quote_diag=None, preferred_side=None, **_kwargs):
        quote_diag = quote_diag if isinstance(quote_diag, dict) else {}
        selected_side = preferred_side or quote_diag.get("selected_side") or "yes"
        if str(selected_side).lower() == "no":
            selected_price = quote_diag.get("no_ask") or getattr(q, "no_ask", None)
        else:
            selected_price = quote_diag.get("yes_ask") or getattr(q, "yes_ask", None)
            selected_side = "yes"
        return {
            "selected_side": selected_side,
            "selected_price": selected_price,
            "selected_price_source": "ask",
            "reason": "test_canonical_selector",
            "yes_ask": getattr(q, "yes_ask", None),
            "no_ask": getattr(q, "no_ask", None),
        }


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


def _candidate_missing_explicit_side(ticker, family, price, confidence, edge):
    candidate = _candidate(ticker, family, price, confidence, edge)
    candidate["q"].selected_side = None
    candidate["q"].side = None
    candidate["allocation"].pop("selected_side", None)
    candidate["allocation"].pop("side", None)
    return candidate


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

    def test_combo_research_derives_missing_side_with_canonical_selector(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "aion_combo_research.jsonl"
            env = {
                "COMBO_RESEARCH_ENABLED": "true",
                "COMBO_RESEARCH_SIM_STAKE": "1",
                "COMBO_RESEARCH_MAX_LEGS": "2",
                "COMBO_RESEARCH_MIN_CANDIDATES": "2",
            }
            with patch.dict(os.environ, env, clear=False), self._patch_runtime(output_path):
                summary = combo_research.run_combo_research_cycle(
                    bot=_Bot(),
                    candidates=[
                        _candidate_missing_explicit_side("KXNBA-SIDE-1", "NBA", 0.54, 0.67, 0.04),
                        _candidate_missing_explicit_side("KXMLB-SIDE-1", "MLB", 0.50, 0.64, 0.035),
                    ],
                    trade_readiness_records=[],
                    markets=[],
                    selected_ticker="KXNBA-SIDE-1",
                    source="test",
                )

            self.assertEqual(summary["candidate_count"], 2)
            self.assertEqual(summary["built_this_cycle"], 1)
            records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            combo = next(record for record in records if record["event_type"] == "combo_built")
            self.assertTrue(all(leg["side"] == "YES" for leg in combo["legs"]))
            self.assertTrue(all(leg["side_source"] == "canonical_side_selector" for leg in combo["legs"]))

    def test_combo_research_allows_distinct_markets_from_same_event(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "aion_combo_research.jsonl"
            env = {
                "COMBO_RESEARCH_ENABLED": "true",
                "COMBO_RESEARCH_SIM_STAKE": "1",
                "COMBO_RESEARCH_MAX_LEGS": "2",
                "COMBO_RESEARCH_MIN_CANDIDATES": "2",
            }
            candidates = [
                _candidate_missing_explicit_side("KXNBA-SAME-EVENT-1", "NBA", 0.54, 0.67, 0.04),
                _candidate_missing_explicit_side("KXNBA-SAME-EVENT-2", "NBA", 0.50, 0.64, 0.035),
            ]
            for candidate in candidates:
                candidate["market"]["event_ticker"] = "EV-SAME-GAME"
                candidate["market"]["game_id"] = "nba:same_game"
            candidates[0]["market"]["title"] = "Spurs spread ladder"
            candidates[1]["market"]["title"] = "Spurs game total ladder"
            with patch.dict(os.environ, env, clear=False), self._patch_runtime(output_path):
                summary = combo_research.run_combo_research_cycle(
                    bot=_Bot(),
                    candidates=candidates,
                    trade_readiness_records=[],
                    markets=[],
                    selected_ticker="KXNBA-SAME-EVENT-1",
                    source="test",
                )

            self.assertEqual(summary["candidate_count"], 2)
            self.assertEqual(summary["built_this_cycle"], 1)
            records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            combo = next(record for record in records if record["event_type"] == "combo_built")
            self.assertEqual(combo["thesis_count"], 2)

    def test_combo_research_uses_best_representative_per_thesis(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "aion_combo_research.jsonl"
            env = {
                "COMBO_RESEARCH_ENABLED": "true",
                "COMBO_RESEARCH_SIM_STAKE": "1",
                "COMBO_RESEARCH_MAX_LEGS": "3",
                "COMBO_RESEARCH_MIN_CANDIDATES": "2",
                "AION_COMBO_REQUIRE_DISTINCT_THESIS": "true",
                "AION_COMBO_ALLOW_SMALLER_DISTINCT_COMBOS": "true",
                "AION_COMBO_MAX_LEGS_PER_THESIS": "1",
            }
            candidates = [
                _candidate("KXNBASPREAD-26MAY30SASOKC-SAS1", "NBA", 0.72, 0.62, 0.018),
                _candidate("KXNBASPREAD-26MAY30SASOKC-SAS3", "NBA", 0.56, 0.64, 0.03),
                _candidate("KXNBASPREAD-26MAY30SASOKC-SAS7", "NBA", 0.38, 0.69, 0.06),
                _candidate("KXMLBMONEYLINE-26MAY30NYYBOS-NYY", "MLB", 0.52, 0.66, 0.04),
                _candidate("KXTENNISMATCH-26MAY31ALPBET-ALP", "TENNIS", 0.48, 0.65, 0.035),
            ]
            for candidate in candidates[:3]:
                candidate["market"]["event_ticker"] = "KXNBASPREAD-26MAY30SASOKC"
                candidate["market"]["game_id"] = "nba:sas_okc"
                rung = candidate["ticker"].rsplit("SAS", 1)[-1]
                candidate["market"]["title"] = f"SAS spread +{rung}"
            candidates[3]["market"]["event_ticker"] = "KXMLBMONEYLINE-26MAY30NYYBOS"
            candidates[3]["market"]["game_id"] = "mlb:nyy_bos"
            candidates[3]["market"]["title"] = "Yankees moneyline"
            candidates[4]["market"]["event_ticker"] = "KXTENNISMATCH-26MAY31ALPBET"
            candidates[4]["market"]["game_id"] = "tennis:alp_bet"
            candidates[4]["market"]["title"] = "Alp to win tennis match"

            with patch.dict(os.environ, env, clear=False), self._patch_runtime(output_path):
                summary = combo_research.run_combo_research_cycle(
                    bot=_Bot(),
                    candidates=candidates,
                    trade_readiness_records=[],
                    markets=[],
                    selected_ticker="KXNBASPREAD-26MAY30SASOKC-SAS7",
                    source="test",
                )

            self.assertEqual(summary["candidate_count"], 5)
            self.assertEqual(summary["distinct_thesis_count"], 3)
            self.assertEqual(summary["rejected_same_thesis_count"], 2)
            self.assertGreater(summary["built_this_cycle"], 0)
            records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            built = [record for record in records if record["event_type"] == "combo_built"]
            self.assertTrue(built)
            for combo in built:
                thesis_keys = [leg["thesis_key"] for leg in combo["legs"]]
                self.assertEqual(len(thesis_keys), len(set(thesis_keys)))
            used_tickers = {leg["ticker"] for combo in built for leg in combo["legs"]}
            self.assertIn("KXNBASPREAD-26MAY30SASOKC-SAS7", used_tickers)
            self.assertNotIn("KXNBASPREAD-26MAY30SASOKC-SAS1", used_tickers)
            self.assertNotIn("KXNBASPREAD-26MAY30SASOKC-SAS3", used_tickers)


if __name__ == "__main__":
    unittest.main()

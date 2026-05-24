import json
import tempfile
import unittest
from pathlib import Path

import elite_public_signals as eps


class ElitePublicSignalTests(unittest.TestCase):
    def test_jsonl_load_resolve_and_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "elite_public_signals.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "trader_handle": "public_top_trader",
                        "source_url_or_label": "manual",
                        "observed_at": "2026-05-24T00:00:00Z",
                        "market_title": "New York vs Cleveland: First Half Total?",
                        "side": "yes",
                        "category": "sports",
                        "raw_text": "public top-trader post mentioned New York Cleveland first half total yes",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            signals, load_summary = eps.load_signal_files([path])
            self.assertEqual(load_summary["rows_loaded"], 1)
            markets = [
                {
                    "ticker": "KXNBA1HTOTAL-SAMPLE-98",
                    "title": "New York vs Cleveland: First Half Total?",
                    "series_ticker": "KXNBA1HTOTAL",
                    "category": "sports",
                }
            ]
            resolved, resolve_summary = eps.resolve_signals_to_markets(
                signals,
                markets,
                min_confidence=0.78,
            )
            self.assertEqual(resolve_summary["resolved"], 1)
            self.assertEqual(resolved[0].resolved_ticker, "KXNBA1HTOTAL-SAMPLE-98")
            match = eps.best_candidate_match(
                resolved,
                ticker="KXNBA1HTOTAL-SAMPLE-98",
                title="New York vs Cleveland: First Half Total?",
                category="sports",
                family="KXNBA1HTOTAL",
                now_ts=resolved[0].observed_at + 60,
                max_age_hours=72,
                min_confidence=0.78,
            )
            self.assertTrue(match["matched"])
            self.assertTrue(match["strong"])
            self.assertEqual(match["confidence"], 1.0)

    def test_missing_file_is_safe(self):
        signals, load_summary = eps.load_signal_files([Path("/tmp/not-a-real-elite-signal-file.jsonl")])
        self.assertEqual(signals, [])
        self.assertEqual(load_summary["files_missing"], 1)


if __name__ == "__main__":
    unittest.main()

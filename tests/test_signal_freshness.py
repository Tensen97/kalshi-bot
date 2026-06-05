import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

import kalshi_web as kw


class SignalFreshnessTests(unittest.TestCase):
    def _make_bot(self):
        bot = kw.KalshiSniperBot.__new__(kw.KalshiSniperBot)
        bot.aion_signal_freshness_by_key = {}
        bot.aion_signal_freshness_metrics = {
            "signal_ages": deque(maxlen=200),
            "stale_candidate_count": 0,
            "freshness_cancellations": 0,
            "fast_submit_count": 0,
            "freshness_reprice_count": 0,
        }
        bot.aion_candidate_trace_meta_by_ticker = {}
        bot.aion_candidate_trace_state_by_ticker = {}
        bot.realm_vv_live_execution_gate_by_ticker = {}
        bot.phase55_live_execution_gate_by_ticker = bot.realm_vv_live_execution_gate_by_ticker
        bot.cycle_count = 1
        return bot

    def test_signal_age_persists_from_first_detection(self):
        bot = self._make_bot()
        bot._aion_mark_signal_freshness(
            ticker="KXNBA-TEST",
            family="KXNBA",
            side="yes",
            signal_detected=True,
            selected_price=0.42,
            edge_after_cost=0.03,
            source="unit_signal",
            now_ts=100.0,
        )

        with patch("kalshi_web.time.time", return_value=150.0):
            context = bot._aion_signal_freshness_context(
                ticker="KXNBA-TEST",
                family="KXNBA",
                side="yes",
                source="unit_submit",
                selected=True,
                submitting=True,
                emit=False,
            )

        self.assertEqual(context["signal_age_seconds"], 50.0)
        self.assertEqual(context["max_signal_age_seconds"], kw.AION_SIGNAL_FRESHNESS_MAX_AGE_SECONDS)
        self.assertTrue(context["stale"])
        self.assertEqual(context["signal_detected_ts"], 100.0)
        self.assertEqual(context["candidate_selected_ts"], 150.0)
        self.assertEqual(context["submit_ts"], 150.0)

    def test_atp_family_uses_tighter_freshness_window(self):
        bot = self._make_bot()
        bot._aion_mark_signal_freshness(
            ticker="KXATPCHALLENGERMATCH-TEST",
            family="KXATPCHALLENGERMATCH",
            side="yes",
            signal_detected=True,
            source="unit_signal",
            now_ts=200.0,
        )

        with patch("kalshi_web.time.time", return_value=219.0):
            context = bot._aion_signal_freshness_context(
                ticker="KXATPCHALLENGERMATCH-TEST",
                family="KXATPCHALLENGERMATCH",
                side="yes",
                source="unit_submit",
                selected=True,
                submitting=True,
                emit=False,
            )

        self.assertEqual(context["max_signal_age_seconds"], kw.AION_SIGNAL_FRESHNESS_ATP_MAX_AGE_SECONDS)
        self.assertTrue(context["atp_freshness_family"])
        self.assertTrue(context["stale"])

    def test_fast_submit_requires_edge_score_spread_and_no_recovery(self):
        bot = self._make_bot()

        self.assertTrue(
            bot._aion_is_strong_freshness_signal(
                edge=0.03,
                winning_score=13.0,
                spread=0.02,
                opponent_recovery=False,
                selected_side="yes",
                winning_side="yes",
                side_strengthening=True,
                side_weakening=False,
            )
        )
        self.assertFalse(
            bot._aion_is_strong_freshness_signal(
                edge=0.03,
                winning_score=13.0,
                spread=0.06,
                opponent_recovery=False,
                selected_side="yes",
                winning_side="yes",
                side_strengthening=True,
                side_weakening=False,
            )
        )
        self.assertFalse(
            bot._aion_is_strong_freshness_signal(
                edge=0.03,
                winning_score=13.0,
                spread=0.02,
                opponent_recovery=True,
                selected_side="yes",
                winning_side="yes",
                side_strengthening=True,
                side_weakening=False,
            )
        )

    def test_material_edge_decay_only_flags_real_decay(self):
        bot = self._make_bot()

        decayed = bot._aion_freshness_edge_decay(0.04, 0.0, stale=True)
        still_viable = bot._aion_freshness_edge_decay(0.04, 0.035, stale=True)

        self.assertTrue(decayed["material"])
        self.assertEqual(decayed["reason"], "material_edge_decay")
        self.assertFalse(still_viable["material"])
        self.assertEqual(still_viable["reason"], "edge_still_viable")

    def test_final_validation_cancels_stale_material_edge_decay(self):
        bot = self._make_bot()
        bot._aion_mark_signal_freshness(
            ticker="KXATPCHALLENGERMATCH-DECAY",
            family="KXATPCHALLENGERMATCH",
            side="yes",
            signal_detected=True,
            selected=True,
            selected_price=0.26,
            edge_after_cost=0.24,
            source="unit_signal",
            now_ts=100.0,
        )
        bot.resolve_aion_orderbook_for_payload = lambda *args, **kwargs: {
            "orderbook_ready": True,
            "yes_bid": 0.24,
            "yes_ask": 0.26,
            "no_bid": 0.74,
            "no_ask": 0.76,
        }
        bot._aion_detect_winning_side = lambda *args, **kwargs: {
            "winning_side": "yes",
            "winning_score": 5.0,
            "opponent_recovery": False,
            "yes": {
                "edge_after_cost": 0.031947,
                "spread": 0.02,
                "side_strengthening": False,
                "side_weakening": False,
            },
        }
        bot.resolve_canonical_tradeability = lambda *args, **kwargs: {
            "tradeable": True,
            "edge_after_cost": 0.031947,
        }
        bot.build_live_order_payload = lambda *args, **kwargs: {
            "ticker": "KXATPCHALLENGERMATCH-DECAY",
            "side": "yes",
            "selected_price": 0.26,
            "yes_price": 26,
            "count": 3,
        }

        with patch("kalshi_web.time.time", return_value=306.0), patch("kalshi_web.builtins.print"):
            result = bot._aion_final_submit_freshness_validation(
                ticker="KXATPCHALLENGERMATCH-DECAY",
                family="KXATPCHALLENGERMATCH",
                q=SimpleNamespace(ticker="KXATPCHALLENGERMATCH-DECAY", selected_side="yes", selected_price=0.26),
                market={"ticker": "KXATPCHALLENGERMATCH-DECAY"},
                quote_diag={"selected_side": "yes", "selected_price": 0.26},
                payload={"ticker": "KXATPCHALLENGERMATCH-DECAY", "side": "yes", "price": 26, "count": 3},
                size=3,
                edge_after_cost=0.24,
                source="unit_final_submit",
                cancel_on_decay=True,
                record_metrics=False,
                emit_fast_submit=False,
            )

        self.assertFalse(result["allowed"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["reason"], "edge_materially_decayed")
        self.assertTrue(result["edge_decay"]["material"])

    def test_canonical_submit_wrapper_routes_alt_lane_through_freshness(self):
        bot = self._make_bot()
        bot.get_market_family = lambda market: "KXMLBMONEYLINE"
        bot.resolve_aion_orderbook_for_payload = lambda *args, **kwargs: {
            "orderbook_ready": True,
            "orderbook_exists": True,
            "yes_bid": 0.42,
            "yes_ask": 0.42,
            "no_bid": 0.58,
            "no_ask": 0.58,
        }
        bot._aion_detect_winning_side = lambda *args, **kwargs: {
            "winning_side": "yes",
            "winning_score": 12.0,
            "opponent_recovery": False,
            "yes": {"edge_after_cost": 0.04, "spread": 0.0, "side_strengthening": True, "side_weakening": False},
            "no": {"edge_after_cost": 0.0, "spread": 0.0},
        }
        bot.resolve_canonical_tradeability = lambda *args, **kwargs: {
            "tradeable": True,
            "edge_after_cost": 0.04,
            "probability_estimate": 0.46,
        }
        bot.already_holding = lambda ticker: False
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False}
        bot.realized_pnl = 0.0
        bot.live_realized_pnl_today = 0.0
        bot._aion_mark_signal_freshness(
            ticker="KXMLBMONEYLINE-FRESH",
            family="KXMLBMONEYLINE",
            side="yes",
            signal_detected=True,
            selected=True,
            selected_price=0.42,
            edge_after_cost=0.04,
            source="unit_signal",
            now_ts=100.0,
        )

        with patch("kalshi_web.time.time", return_value=104.0), patch("kalshi_web.builtins.print"):
            result = bot._aion_canonical_submit_freshness_gate(
                SimpleNamespace(ticker="KXMLBMONEYLINE-FRESH", family="KXMLBMONEYLINE", selected_side="yes", selected_price=0.42),
                size=1,
                lane="sports_multi_submit",
                payload={
                    "ticker": "KXMLBMONEYLINE-FRESH",
                    "family": "KXMLBMONEYLINE",
                    "side": "yes",
                    "selected_price": 0.42,
                    "count": 1,
                    "edge_after_cost": 0.04,
                },
                source="submit_live_order",
            )

        self.assertTrue(result["allowed"])
        self.assertTrue(result["payload"]["_aion_confirm_entry_passed"])
        self.assertTrue(result["payload"]["_aion_freshness_gate_checked"])
        self.assertEqual(result["payload"]["signal_age_seconds"], 4.0)
        self.assertEqual(result["payload"]["selected_side"], "yes")
        self.assertIn("target_id", result["payload"])

    def test_reduce_only_payload_is_exempt_from_entry_freshness(self):
        bot = self._make_bot()
        bot.get_market_family = lambda market: "KXMLBKS"

        with patch("kalshi_web.builtins.print"):
            result = bot._aion_canonical_submit_freshness_gate(
                SimpleNamespace(ticker="KXMLBKS-TEST", family="KXMLBKS"),
                size=1,
                lane="exit",
                payload={
                    "ticker": "KXMLBKS-TEST",
                    "family": "KXMLBKS",
                    "side": "yes",
                    "action": "sell",
                    "reduce_only": True,
                    "count": 1,
                },
                source="submit_live_order",
            )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason"], "reduce_only_exit_exempt")


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import patch

import kalshi_risk


class RiskBotStub:
    def __init__(self):
        self.live_positions = []
        self.reconcile_positions = None
        self.reconcile_ok = True
        self.reconcile_calls = 0
        self.recent_orders = {}
        self.local_state = {}
        self.trading_halted = False
        self.realized_pnl = 0.0
        self.live_realized_pnl_today = 0.0

    def to_float(self, value, default=None):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def get_market_family(self, market):
        ticker = str((market or {}).get("ticker") or "").strip()
        return ticker.split("-")[0].upper() if ticker else "UNKNOWN"

    def _read_field(self, q, field, default=None):
        if isinstance(q, dict):
            return q.get(field, default)
        return getattr(q, field, default)

    def _get_open_live_positions(self):
        return [
            position
            for position in self.live_positions
            if str(position.get("status", "open") or "open").lower() == "open"
        ]

    def _extract_sports_game_key(self, ticker, market=None):
        ticker = str(ticker or "").strip()
        family = self.get_market_family({"ticker": ticker})
        parts = ticker.split("-")
        if len(parts) >= 2:
            return f"{family}-{parts[1]}"
        return ticker

    def load_live_position_state(self):
        return dict(self.local_state)

    def reconcile_live_positions(self):
        self.reconcile_calls += 1
        if self.reconcile_positions is not None:
            self.live_positions = list(self.reconcile_positions)
        return {"ok": self.reconcile_ok, "positions": self.live_positions}

    def already_holding(self, ticker):
        ticker = str(ticker or "").strip()
        return any(position.get("ticker") == ticker for position in self._get_open_live_positions())

    def _is_sports_market_or_family(self, family):
        return True

    def recent_submitted_order_for_ticker(self, ticker):
        return self.recent_orders.get(str(ticker or "").strip())

    def recent_submitted_order_count_for_family(self, family):
        family = str(family or "").strip().upper()
        return sum(1 for ticker in self.recent_orders if str(ticker).split("-")[0].upper() == family)

    def recent_submitted_order_count_total(self):
        return len(self.recent_orders)

    def _reset_live_trade_counter_if_needed(self):
        pass


class RiskGovernorTests(unittest.TestCase):
    def _payload(self, **overrides):
        payload = {
            "ticker": "KXNCAABSPREAD-PURMICH",
            "family": "KXNCAABSPREAD",
            "side": "yes",
            "action": "buy",
            "count": 3,
            "selected_price": 0.40,
            "orderbook_ready": True,
        }
        payload.update(overrides)
        return payload

    def _evaluate(self, bot=None, payload=None, **kwargs):
        return kalshi_risk.evaluate_live_order_risk_governor(
            bot or RiskBotStub(),
            payload or self._payload(),
            max_live_order_size=5,
            max_daily_loss=0.10,
            max_dollars_per_trade=2.00,
            max_family_exposure_dollars=5.00,
            **kwargs,
        )

    def test_valid_candidate_passes(self):
        result = self._evaluate()
        self.assertTrue(result["ok"])
        self.assertEqual(result["payload"]["count"], 3)

    def test_bad_sizing_is_capped_not_blocked(self):
        result = self._evaluate(payload=self._payload(count=99))
        self.assertTrue(result["ok"])
        self.assertEqual(result["payload"]["count"], 5)
        self.assertIn("max_contracts_per_trade", result["cap_reasons"])

    def test_max_dollars_caps_size(self):
        result = self._evaluate(payload=self._payload(count=10, selected_price=0.80))
        self.assertTrue(result["ok"])
        self.assertEqual(result["payload"]["count"], 2)
        self.assertIn("max_dollars_per_trade", result["cap_reasons"])

    def test_family_exposure_blocks_only_when_capacity_exhausted(self):
        bot = RiskBotStub()
        bot.live_positions.append(
            {"ticker": "KXNCAABSPREAD-OTHER", "family": "KXNCAABSPREAD", "count": 10, "entry_price": 0.50}
        )
        result = self._evaluate(bot=bot)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "max_family_exposure")

    def test_duplicate_live_position_blocks(self):
        bot = RiskBotStub()
        bot.live_positions.append({"ticker": "KXNCAABSPREAD-PURMICH", "count": 1, "entry_price": 0.40})
        result = self._evaluate(bot=bot)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "duplicate_position_guard")

    def test_duplicate_guard_refreshes_live_api_truth_before_submit(self):
        bot = RiskBotStub()
        bot.reconcile_positions = [{"ticker": "KXNCAABSPREAD-PURMICH", "count": 1, "avg_price": 0.40}]
        result = self._evaluate(bot=bot)
        self.assertEqual(bot.reconcile_calls, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "duplicate_position_guard")

    def test_same_ticker_opposite_side_buy_is_blocked(self):
        bot = RiskBotStub()
        bot.live_positions.append({"ticker": "KXNCAABSPREAD-PURMICH", "side": "yes", "count": 1, "entry_price": 0.40})
        result = self._evaluate(bot=bot, payload=self._payload(side="no"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "opposite_side_conflict")
        self.assertEqual(result["opposite_side_guard"]["conflicts"][0]["reason"], "exact_ticker_opposite_side")

    def test_negative_position_fp_no_side_blocks_same_ticker_yes_buy(self):
        bot = RiskBotStub()
        bot.reconcile_positions = [{"ticker": "KXNCAABSPREAD-PURMICH", "position_fp": "-2.00"}]
        result = self._evaluate(bot=bot, payload=self._payload(side="yes"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "opposite_side_conflict")
        self.assertEqual(result["opposite_side_guard"]["conflicts"][0]["side"], "no")

    def test_same_match_competing_player_yes_buy_is_blocked(self):
        bot = RiskBotStub()
        bot.live_positions.append(
            {"ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-MIL", "family": "KXATPCHALLENGERMATCH", "side": "yes", "count": 2, "entry_price": 0.60}
        )
        result = self._evaluate(
            bot=bot,
            payload=self._payload(
                ticker="KXATPCHALLENGERMATCH-26JUN01MILLAN-LAN",
                family="KXATPCHALLENGERMATCH",
                side="yes",
                selected_price=0.46,
            ),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "opposite_side_conflict")
        self.assertEqual(result["opposite_side_guard"]["conflicts"][0]["reason"], "same_event_competing_outcome")

    def test_same_game_competing_team_yes_buy_is_blocked(self):
        bot = RiskBotStub()
        bot.live_positions.append(
            {"ticker": "KXMLBMONEYLINE-26JUN01NYYBOS-NYY", "family": "KXMLBMONEYLINE", "side": "yes", "count": 1, "entry_price": 0.52}
        )
        result = self._evaluate(
            bot=bot,
            payload=self._payload(
                ticker="KXMLBMONEYLINE-26JUN01NYYBOS-BOS",
                family="KXMLBMONEYLINE",
                side="yes",
                selected_price=0.49,
            ),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "opposite_side_conflict")

    def test_game_family_competing_team_yes_buy_is_blocked(self):
        bot = RiskBotStub()
        bot.live_positions.append(
            {"ticker": "KXNCAASBGAME-26JUN012200ALATTUG2-TTU", "family": "KXNCAASBGAME", "side": "yes", "count": 2, "entry_price": 0.57}
        )
        result = self._evaluate(
            bot=bot,
            payload=self._payload(
                ticker="KXNCAASBGAME-26JUN012200ALATTUG2-ALA",
                family="KXNCAASBGAME",
                side="yes",
                selected_price=0.48,
            ),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "opposite_side_conflict")
        self.assertEqual(result["opposite_side_guard"]["conflicts"][0]["reason"], "same_event_competing_outcome")

    def test_live_api_opposite_side_truth_overrides_empty_local_state(self):
        bot = RiskBotStub()
        bot.local_state = {}
        bot.reconcile_positions = [
            {"ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-MIL", "family": "KXATPCHALLENGERMATCH", "side": "yes", "count": 2, "avg_price": 0.60}
        ]
        result = self._evaluate(
            bot=bot,
            payload=self._payload(
                ticker="KXATPCHALLENGERMATCH-26JUN01MILLAN-LAN",
                family="KXATPCHALLENGERMATCH",
                side="yes",
                selected_price=0.46,
            ),
        )
        self.assertEqual(bot.reconcile_calls, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "opposite_side_conflict")

    def test_reduce_only_exit_allowed_despite_opposite_side_exposure(self):
        bot = RiskBotStub()
        bot.live_positions.append(
            {"ticker": "KXATPCHALLENGERMATCH-26JUN01MILLAN-MIL", "family": "KXATPCHALLENGERMATCH", "side": "yes", "count": 2, "entry_price": 0.60}
        )
        result = kalshi_risk.evaluate_opposite_side_pre_submit_guard(
            bot,
            self._payload(
                ticker="KXATPCHALLENGERMATCH-26JUN01MILLAN-LAN",
                family="KXATPCHALLENGERMATCH",
                side="yes",
                action="sell",
                reduce_only=True,
            ),
            source="unit_reduce_only",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "reduce_only_or_non_buy_allowed")

    def test_family_exposure_counts_api_reconciled_avg_price(self):
        bot = RiskBotStub()
        bot.reconcile_positions = [
            {"ticker": "KXNCAABSPREAD-OTHER", "family": "KXNCAABSPREAD", "count": 10, "avg_price": 0.50}
        ]
        result = self._evaluate(bot=bot)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "max_family_exposure")

    def test_daily_loss_blocks(self):
        bot = RiskBotStub()
        bot.live_realized_pnl_today = -0.10
        result = self._evaluate(bot=bot)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "daily_loss_guard")

    def test_fake_orderbook_blocks(self):
        result = self._evaluate(payload=self._payload(orderbook_source="fake_book"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "synthetic_or_fake_orderbook")

    def test_pending_family_orders_warn_do_not_block(self):
        bot = RiskBotStub()
        bot.recent_orders["KXNCAABSPREAD-OTHER"] = {"ticker": "KXNCAABSPREAD-OTHER"}
        result = self._evaluate(bot=bot)
        self.assertTrue(result["ok"])
        self.assertIn("pending_family_orders", result["warnings"])

    def test_can_open_ignores_legacy_trade_count_and_total_position_caps(self):
        bot = RiskBotStub()
        bot.live_trade_count_today = 999
        bot.live_positions.extend(
            [
                {"ticker": "KXNCAABSPREAD-OTHER1", "family": "KXNCAABSPREAD", "count": 1, "entry_price": 0.40},
                {"ticker": "KXNCAABSPREAD-OTHER2", "family": "KXNCAABSPREAD", "count": 1, "entry_price": 0.40},
            ]
        )
        q = type("Q", (), {"ticker": "KXNCAABSPREAD-PURMICH"})()
        ok, reason = kalshi_risk.can_open_new_position(
            bot,
            q,
            max_simultaneous_live_positions=1,
            max_live_trades_per_day=1,
            max_daily_loss=0.10,
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_duplicate_api_pending_capacity_warns_not_blocks(self):
        bot = RiskBotStub()
        bot.recent_orders["KXNCAABSPREAD-OTHER"] = {"ticker": "KXNCAABSPREAD-OTHER"}
        result = kalshi_risk.check_live_duplicate_position_api(
            bot,
            "KXNCAABSPREAD-PURMICH",
            max_sports_live_positions=1,
            max_simultaneous_live_positions=1,
        )
        self.assertFalse(result["blocked"])
        self.assertTrue(result["pending_family_warning"])
        self.assertTrue(result["pending_global_warning"])

    def test_emergency_kill_switch_blocks(self):
        with patch.dict(os.environ, {"AION_RISK_EMERGENCY_KILL_SWITCH": "1"}):
            result = self._evaluate()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "emergency_kill_switch")


if __name__ == "__main__":
    unittest.main()

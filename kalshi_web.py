#!/usr/bin/env python3
"""AION sports-only live trading engine."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

import kalshi_execution
import kalshi_exits
import kalshi_risk
from kalshi_orderbook import (
    Quote,
    build_quote,
    coerce_float,
    coerce_price,
    extract_markets,
    is_kxmlbks_market,
    is_sports_market,
    is_tennis_market,
    market_family,
    market_text,
    market_ticker,
    normalize_side,
    price_zone,
    valid_trade_price,
)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return int(default)


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TradeTarget:
    target_id: str
    ticker: str
    family: str
    side: str
    selected_price: float
    count: int
    confidence: float
    ranking_score: float
    spread: Optional[float]
    liquidity: float
    volume: float
    orderbook_snapshot_id: str
    price_zone: str
    source: str
    created_ts: float
    signal_detected_ts: Optional[float] = None
    target_created_ts: Optional[float] = None
    trace_id: Optional[str] = None
    entry_price: Optional[float] = None
    immediate_cashout: Optional[float] = None
    immediate_pnl_per_contract: Optional[float] = None
    edge_after_spread: Optional[float] = None
    first_cashout_quality: Optional[float] = None
    green_entry_score: Optional[float] = None
    learning_multiplier: Optional[float] = None
    concentration_penalty: Optional[float] = None
    opened_green: Optional[bool] = None
    fair_probability_for_side: Optional[float] = None
    entry_mode: Optional[str] = None
    maker_price: Optional[float] = None
    taker_entry_price: Optional[float] = None
    taker_ev: Optional[float] = None
    maker_ev: Optional[float] = None
    ev_after_all_costs: Optional[float] = None
    expected_spread_cost: Optional[float] = None
    expected_exit_cost: Optional[float] = None
    fees: Optional[float] = None
    slippage_buffer: Optional[float] = None
    model_error_buffer: Optional[float] = None
    adverse_selection_buffer: Optional[float] = None
    fill_probability: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


PositionExecutor = kalshi_exits.PositionExecutor


COMBO_CROSS_FAMILY_TOKENS = (
    "MULTIGAME",
    "CROSSCATEGORY",
    "CROSS-CATEGORY",
    "COMBO",
    "PARLAY",
    "SAMEGAME",
)


def is_combo_or_cross_market(family: Any = None, ticker: Any = None, market: Dict[str, Any] | None = None) -> bool:
    text = " ".join(
        str(value or "").upper()
        for value in (
            family,
            ticker,
            market_text(market or {}) if market else "",
        )
    )
    return any(token in text for token in COMBO_CROSS_FAMILY_TOKENS)


def event_window_key(family: Any = None, ticker: Any = None, market: Dict[str, Any] | None = None) -> str:
    market = market if isinstance(market, dict) else {}
    explicit = str(
        market.get("event_ticker")
        or market.get("event_id")
        or market.get("event")
        or ""
    ).strip().upper()
    if explicit:
        return explicit
    ticker_text = str(ticker or market_ticker(market) or "").strip().upper()
    parts = [part for part in ticker_text.split("-") if part]
    if len(parts) >= 2:
        return "-".join(parts[:2])
    family_text = str(family or market_family(market) or "UNKNOWN").strip().upper()
    return family_text or "UNKNOWN"


class GreenEntryScorer:
    """EV-after-cost scorer for maker-first entry decisions."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    @property
    def min_entry_size(self) -> float:
        return max(1.0, env_float("AION_MIN_ENTRY_SIZE", 1.0))

    @property
    def min_cashout_size(self) -> float:
        return max(1.0, env_float("AION_MIN_CASHOUT_SIZE", 1.0))

    @property
    def min_cashout_price(self) -> float:
        return min(0.10, max(0.0, env_float("AION_MIN_FIRST_CASHOUT_PRICE", 0.01)))

    @property
    def min_first_cashout_quality(self) -> float:
        return max(0.0, env_float("AION_MIN_FIRST_CASHOUT_QUALITY", 0.01))

    @property
    def combo_min_cashout_price(self) -> float:
        return min(0.20, max(self.min_cashout_price, env_float("AION_COMBO_MIN_FIRST_CASHOUT_PRICE", 0.03)))

    @property
    def combo_min_first_cashout_quality(self) -> float:
        return min(1.0, max(self.min_first_cashout_quality, env_float("AION_COMBO_MIN_FIRST_CASHOUT_QUALITY", 0.10)))

    @property
    def tick_size(self) -> float:
        return min(0.05, max(0.001, env_float("AION_ENTRY_TICK_SIZE", 0.01)))

    @property
    def strong_taker_edge_threshold(self) -> float:
        return max(0.0, env_float("AION_TAKER_STRONG_EDGE_THRESHOLD", 0.02))

    @property
    def maker_edge_threshold(self) -> float:
        return max(0.0, env_float("AION_MAKER_EDGE_THRESHOLD", 0.001))

    @property
    def combo_edge_threshold(self) -> float:
        return max(self.maker_edge_threshold, env_float("AION_COMBO_EV_THRESHOLD", 0.01))

    @property
    def min_maker_fill_probability(self) -> float:
        return min(1.0, max(0.0, env_float("AION_MIN_MAKER_FILL_PROBABILITY", 0.08)))

    @property
    def taker_fee_rate(self) -> float:
        return max(0.0, env_float("AION_TAKER_FEE_RATE", 0.07))

    @property
    def maker_fee_rate(self) -> float:
        return max(0.0, env_float("AION_MAKER_FEE_RATE", 0.0))

    @property
    def fee_rounding(self) -> bool:
        return env_bool("AION_FEE_ROUNDING", True)

    @property
    def max_red_entry_rate(self) -> float:
        return min(1.0, max(0.0, env_float("AION_MAX_RECENT_RED_ENTRY_RATE", 0.55)))

    @property
    def combo_max_red_entry_rate(self) -> float:
        return min(1.0, max(0.0, env_float("AION_COMBO_MAX_RECENT_RED_ENTRY_RATE", 0.30)))

    def _learning_bucket(self, family: str, side: str, zone: str) -> Dict[str, Any]:
        learning = (getattr(self.bot, "state", {}) or {}).get("learning") or {}
        buckets = learning.get("family_side_zone") if isinstance(learning.get("family_side_zone"), dict) else {}
        return buckets.get(self.bot._learning_bucket_key(family, side, zone), {}) if isinstance(buckets, dict) else {}

    def _recent_red_entry_rate(self, *, family: str, side: str, zone: str, combo_only: bool = False) -> Dict[str, Any]:
        learning = (getattr(self.bot, "state", {}) or {}).get("learning") or {}
        outcomes = learning.get("exit_outcomes") if isinstance(learning.get("exit_outcomes"), list) else []
        recent = []
        family_text = str(family or "").upper()
        side_text = normalize_side(side)
        for record in outcomes[-50:]:
            if not isinstance(record, dict):
                continue
            record_family = str(record.get("family") or "").upper()
            record_side = normalize_side(record.get("side"))
            record_zone = str(record.get("price_zone") or "unknown")
            record_combo = is_combo_or_cross_market(record_family, record.get("ticker"))
            if combo_only and not record_combo:
                continue
            if record_family != family_text or record_side != side_text or record_zone != zone:
                continue
            opened_green = record.get("opened_green")
            if opened_green is None:
                immediate = coerce_float(record.get("immediate_pnl_per_contract"), None)
                opened_green = immediate is not None and immediate >= 0.0
            recent.append(bool(opened_green))
        red = sum(1 for opened_green in recent if not opened_green)
        return {
            "samples": len(recent),
            "red_entries": red,
            "red_entry_rate": round(red / len(recent), 4) if recent else 0.0,
        }

    def _learning_edge(self, bucket: Dict[str, Any]) -> Dict[str, float]:
        trades = int(bucket.get("trades") or 0)
        entry_samples = int(bucket.get("entry_samples") or bucket.get("green_entries") or bucket.get("red_entries") or 0)
        avg_realized = 0.0
        avg_immediate = 0.0
        avg_mfe = 0.0
        avg_mae = 0.0
        mfe_before_mae_rate = 0.0
        if trades >= 3:
            avg_realized = float(coerce_float(bucket.get("pnl"), 0.0) or 0.0) / max(1, trades)
            avg_mfe = float(coerce_float(bucket.get("mfe_total"), bucket.get("mfe") or 0.0) or 0.0) / max(1, trades)
            avg_mae = float(coerce_float(bucket.get("mae_total"), bucket.get("mae") or 0.0) or 0.0) / max(1, trades)
        if entry_samples >= 3:
            avg_immediate = float(coerce_float(bucket.get("immediate_pnl"), 0.0) or 0.0) / max(1, entry_samples)
        mfe_samples = int(bucket.get("mfe_before_mae_samples") or 0)
        if mfe_samples >= 3:
            mfe_before_mae_rate = float(bucket.get("mfe_before_mae_positive") or 0.0) / max(1, mfe_samples)
        mfe_bias = max(0.0, avg_mfe - abs(avg_mae))
        history_edge = (
            max(0.0, avg_immediate) * 0.50
            + max(0.0, avg_realized) * 0.25
            + max(0.0, mfe_bias) * 0.10
            + max(0.0, mfe_before_mae_rate - 0.50) * 0.02
        )
        return {
            "history_edge": round(history_edge, 4),
            "avg_realized_pnl": round(avg_realized, 4),
            "avg_immediate_pnl": round(avg_immediate, 4),
            "avg_mfe": round(avg_mfe, 4),
            "avg_mae": round(avg_mae, 4),
            "mfe_before_mae_rate": round(mfe_before_mae_rate, 4),
        }

    def _explicit_fair_probability(self, quote: Quote, side: str) -> Optional[float]:
        raw = quote.raw_market if isinstance(quote.raw_market, dict) else {}
        yes_keys = (
            "aion_fair_probability_yes",
            "fair_probability_yes",
            "model_probability_yes",
            "estimated_probability_yes",
            "p_fair_yes",
        )
        no_keys = (
            "aion_fair_probability_no",
            "fair_probability_no",
            "model_probability_no",
            "estimated_probability_no",
            "p_fair_no",
        )
        for key in yes_keys if side == "yes" else no_keys:
            value = coerce_price(raw.get(key))
            if value is not None:
                return value
        if side == "no":
            for key in yes_keys:
                value = coerce_price(raw.get(key))
                if value is not None:
                    return round(1.0 - value, 4)
        else:
            for key in no_keys:
                value = coerce_price(raw.get(key))
                if value is not None:
                    return round(1.0 - value, 4)
        return None

    def _mid_probability(self, bid: Optional[float], ask: Optional[float]) -> Optional[float]:
        if bid is not None and ask is not None and ask >= bid:
            return round((float(bid) + float(ask)) / 2.0, 4)
        if bid is not None:
            return coerce_price(bid)
        if ask is not None:
            return coerce_price(ask)
        return None

    def _momentum_adjustment(self, quote: Quote, side: str, mid: Optional[float]) -> float:
        if mid is None:
            return 0.0
        memory = getattr(self.bot, "_quote_memory", None)
        if not isinstance(memory, dict):
            self.bot._quote_memory = {}
            memory = self.bot._quote_memory
        key = f"{quote.ticker}:{side}"
        previous = memory.get(key) if isinstance(memory.get(key), dict) else {}
        previous_mid = coerce_float(previous.get("mid"), None)
        memory[key] = {"mid": mid, "ts": time.time()}
        if previous_mid is None:
            return 0.0
        return round(max(-0.015, min(0.015, (float(mid) - float(previous_mid)) * 0.60)), 4)

    def _learning_adjustment(self, bucket: Dict[str, Any], entry_price: Optional[float]) -> Dict[str, Any]:
        trades = int(bucket.get("trades") or 0)
        entry_samples = int(bucket.get("entry_samples") or bucket.get("green_entries") or bucket.get("red_entries") or 0)
        wins = int(bucket.get("wins") or 0)
        losses = int(bucket.get("losses") or 0)
        avg_pnl = float(coerce_float(bucket.get("pnl"), 0.0) or 0.0) / max(1, trades)
        avg_immediate = float(coerce_float(bucket.get("immediate_pnl"), 0.0) or 0.0) / max(1, entry_samples)
        avg_mfe = float(coerce_float(bucket.get("mfe_total"), bucket.get("mfe") or 0.0) or 0.0) / max(1, trades)
        avg_mae = float(coerce_float(bucket.get("mae_total"), bucket.get("mae") or 0.0) or 0.0) / max(1, trades)
        mfe_samples = int(bucket.get("mfe_before_mae_samples") or 0)
        mfe_before_mae_rate = float(bucket.get("mfe_before_mae_positive") or 0.0) / max(1, mfe_samples)
        win_rate = wins / max(1, wins + losses)
        red_rate = float(bucket.get("red_entries") or 0.0) / max(1, entry_samples)
        adjustment = 0.0
        confidence = 0.0
        if trades >= 3:
            adjustment += max(-0.08, min(0.08, avg_pnl * 0.55))
            adjustment += max(-0.035, min(0.035, (win_rate - 0.50) * 0.07))
            adjustment += max(-0.025, min(0.025, (avg_mfe - abs(avg_mae)) * 0.25))
            confidence += min(0.50, trades / 30.0)
        if entry_samples >= 3:
            adjustment += max(-0.025, min(0.025, avg_immediate * 0.20))
            adjustment -= max(0.0, red_rate - 0.60) * 0.03
            confidence += min(0.25, entry_samples / 40.0)
        if mfe_samples >= 3:
            adjustment += max(-0.018, min(0.018, (mfe_before_mae_rate - 0.50) * 0.06))
            confidence += min(0.20, mfe_samples / 40.0)
        return {
            "learning_adjustment": round(max(-0.12, min(0.12, adjustment)), 4),
            "learning_confidence": round(max(0.0, min(1.0, confidence)), 4),
            "historical_win_rate": round(win_rate, 4) if wins + losses else 0.0,
            "historical_avg_pnl_per_contract": round(avg_pnl, 4),
            "historical_red_entry_rate": round(red_rate, 4) if entry_samples else 0.0,
        }

    def _liquidity_quality_adjustment(self, quote: Quote, side: str, spread: Optional[float]) -> float:
        bid_size = max(0.0, float(coerce_float(quote.size_for_side(side, "sell"), 0.0) or 0.0))
        ask_size = max(0.0, float(coerce_float(quote.size_for_side(side, "buy"), 0.0) or 0.0))
        if bid_size <= 0.0 or ask_size <= 0.0:
            return -0.01
        balance = bid_size / max(1.0, bid_size + ask_size)
        adjustment = (balance - 0.50) * 0.012
        if spread is not None and spread > 0.20:
            adjustment -= min(0.015, (spread - 0.20) * 0.04)
        return round(max(-0.018, min(0.010, adjustment)), 4)

    def _fee_per_contract(self, *, price: float, count: int, mode: str) -> float:
        rate = self.taker_fee_rate if mode == "taker" else self.maker_fee_rate
        if rate <= 0.0:
            return 0.0
        count = max(1, int(count or 1))
        raw = float(rate) * float(count) * float(price) * max(0.0, 1.0 - float(price))
        if self.fee_rounding:
            raw = math.ceil(raw * 100.0 - 1e-9) / 100.0
        return round(raw / count, 4)

    def _cost_buffers(
        self,
        *,
        mode: str,
        spread: Optional[float],
        entry_price: float,
        fair_probability: float,
        learning_confidence: float,
        avg_mae: float,
        count: int,
    ) -> Dict[str, float]:
        spread_value = max(0.0, float(spread or 0.0))
        if mode == "taker":
            expected_spread_cost = min(0.015, spread_value * 0.10)
            expected_exit_cost = min(0.035, max(0.002, spread_value * 0.20))
            slippage_buffer = min(0.025, max(0.002, spread_value * 0.08))
            adverse_selection_buffer = min(0.025, max(0.002, spread_value * 0.06))
        else:
            expected_spread_cost = 0.0
            expected_exit_cost = min(0.025, max(0.001, spread_value * 0.10))
            slippage_buffer = 0.001
            adverse_selection_buffer = min(0.018, max(0.0015, spread_value * 0.02))
        model_floor = max(0.0015, env_float("AION_MODEL_ERROR_BUFFER_FLOOR", 0.0015))
        model_error_buffer = max(model_floor, min(0.04, abs(float(avg_mae or 0.0)) * 0.25 + (1.0 - learning_confidence) * 0.002))
        fees = self._fee_per_contract(price=entry_price, count=count, mode=mode)
        return {
            "expected_spread_cost": round(expected_spread_cost, 4),
            "expected_exit_cost": round(expected_exit_cost, 4),
            "fees": round(fees, 4),
            "slippage_buffer": round(slippage_buffer, 4),
            "model_error_buffer": round(model_error_buffer, 4),
            "adverse_selection_buffer": round(adverse_selection_buffer, 4),
        }

    def _ev_after_costs(self, *, fair_probability: float, entry_price: float, costs: Dict[str, float]) -> float:
        return round(
            float(fair_probability)
            - float(entry_price)
            - float(costs.get("expected_spread_cost") or 0.0)
            - float(costs.get("expected_exit_cost") or 0.0)
            - float(costs.get("fees") or 0.0)
            - float(costs.get("slippage_buffer") or 0.0)
            - float(costs.get("model_error_buffer") or 0.0)
            - float(costs.get("adverse_selection_buffer") or 0.0),
            4,
        )

    def _maker_price_and_fill_probability(
        self,
        *,
        bid: Optional[float],
        ask: Optional[float],
        spread: Optional[float],
        bid_size: float,
        ask_size: float,
        fair_probability: float,
    ) -> Dict[str, Any]:
        if bid is None or ask is None or not valid_trade_price(bid) or not valid_trade_price(ask):
            return {"maker_price": None, "fill_probability": 0.0, "reason": "maker_price_unavailable"}
        tick = self.tick_size
        if ask <= bid:
            maker_price = min(float(ask), max(0.01, float(bid)))
        else:
            improved = min(float(ask) - tick, float(bid) + tick)
            maker_price = improved if improved > float(bid) else float(bid)
        maker_price = round(max(0.01, min(0.99, maker_price)), 4)
        if maker_price >= float(ask):
            maker_price = round(max(0.01, float(ask) - tick), 4)
        if not valid_trade_price(maker_price):
            return {"maker_price": None, "fill_probability": 0.0, "reason": "maker_price_out_of_bounds"}
        spread_value = max(0.0, float(spread or max(0.0, float(ask) - float(bid))))
        size_total = max(1.0, float(bid_size or 0.0) + float(ask_size or 0.0))
        size_balance = float(ask_size or 0.0) / size_total
        inside_bonus = 0.18 if maker_price > float(bid) else 0.0
        spread_bonus = min(0.18, spread_value * 1.2)
        fair_distance_penalty = max(0.0, maker_price - fair_probability) * 2.0
        fill_probability = 0.10 + inside_bonus + spread_bonus + min(0.12, size_balance * 0.16) - fair_distance_penalty
        return {
            "maker_price": maker_price,
            "fill_probability": round(max(0.0, min(0.85, fill_probability)), 4),
            "reason": "maker_price_selected",
        }

    def _concentration(self, quote: Quote) -> Dict[str, Any]:
        quote_family = str(quote.family or "UNKNOWN").upper()
        quote_event = event_window_key(quote.family, quote.ticker, quote.raw_market)
        combo_family = is_combo_or_cross_market(quote.family, quote.ticker, quote.raw_market)
        family_count = 0
        event_count = 0
        combo_conflicts = 0
        for position in kalshi_risk.open_positions(self.bot):
            position_family = str(position.get("family") or market_family({"ticker": position.get("ticker")}) or "UNKNOWN").upper()
            position_ticker = str(position.get("ticker") or "").upper()
            position_event = str(position.get("event_window_key") or event_window_key(position_family, position_ticker)).upper()
            if position_family == quote_family:
                family_count += 1
            if position_event == quote_event:
                event_count += 1
            if combo_family and is_combo_or_cross_market(position_family, position_ticker):
                if position_family == quote_family or position_event == quote_event:
                    combo_conflicts += 1
        penalty = 1.0 / (1.0 + (0.18 * family_count) + (0.10 * event_count))
        return {
            "combo_family": combo_family,
            "event_window_key": quote_event,
            "family_open_count": family_count,
            "event_open_count": event_count,
            "combo_conflicts": combo_conflicts,
            "concentration_penalty": round(max(0.35, min(1.0, penalty)), 4),
        }

    def _blocked(self, quote: Quote, side: str, reason: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "ticker": quote.ticker,
            "family": quote.family,
            "side": side,
            "reason": reason,
            "blocked": True,
            **metrics,
        }
        self.bot.log_json("AION-EV-SCORE", payload)
        self.bot.log_json("AION-EV-ENTRY-REJECT", payload)
        if reason == "overconcentrated_combo_family":
            self.bot.log_json("AION-COMBO-CONCENTRATION-REJECT", payload)
        return {**metrics, "blocked": True, "reason": reason, "side": side}

    def score_side(self, quote: Quote, side: str, *, entry_price_override: Any = None, source: str = "ev_entry_scorer") -> Optional[Dict[str, Any]]:
        side = normalize_side(side)
        if side not in {"yes", "no"}:
            return None
        taker_entry_price = coerce_price(quote.price_for_side(side, "buy"))
        cashout = coerce_price(quote.price_for_side(side, "sell"))
        executable_size = float(coerce_float(quote.size_for_side(side, "buy"), 0.0) or 0.0)
        cashout_size = float(coerce_float(quote.size_for_side(side, "sell"), 0.0) or 0.0)
        bid_size = cashout_size
        ask_size = executable_size
        spread = quote.spread_for_side(side)
        if spread is None and taker_entry_price is not None and cashout is not None:
            spread = round(max(0.0, taker_entry_price - cashout), 4)
        zone = price_zone(taker_entry_price)
        concentration = self._concentration(quote)
        bucket = self._learning_bucket(quote.family, side, zone)
        learning_edge = self._learning_edge(bucket)
        learning_adjustment = self._learning_adjustment(bucket, taker_entry_price)
        learning_multiplier = self.bot._learning_multiplier(quote.family, side, zone)
        base_metrics: Dict[str, Any] = {
            "source": source,
            "selected_price": taker_entry_price,
            "entry_price": taker_entry_price,
            "book_entry_price": taker_entry_price,
            "taker_entry_price": taker_entry_price,
            "immediate_cashout": cashout,
            "spread": spread,
            "liquidity": quote.liquidity,
            "volume": quote.volume,
            "executable_size": executable_size,
            "cashout_size": cashout_size,
            "price_zone": zone,
            "learning_multiplier": learning_multiplier,
            **learning_edge,
            **learning_adjustment,
            **concentration,
        }
        if not valid_trade_price(taker_entry_price):
            return self._blocked(quote, side, "no_executable_selected_side_price", base_metrics)
        if executable_size < self.min_entry_size:
            return self._blocked(quote, side, "selected_side_no_visible_liquidity", base_metrics)
        if cashout is None or cashout_size < self.min_cashout_size:
            return self._blocked(quote, side, "missing_first_cashout", base_metrics)
        if cashout <= self.min_cashout_price:
            return self._blocked(quote, side, "weak_cashout_quality", base_metrics)
        immediate_pnl = round(float(cashout) - float(taker_entry_price), 4)
        spread_cost = round(max(0.0, float(taker_entry_price) - float(cashout)), 4)
        first_cashout_quality = round(float(cashout) / max(0.0001, float(taker_entry_price)), 4)
        mid_probability = self._mid_probability(cashout, taker_entry_price)
        explicit_fair = self._explicit_fair_probability(quote, side)
        momentum_adjustment = self._momentum_adjustment(quote, side, mid_probability)
        liquidity_adjustment = self._liquidity_quality_adjustment(quote, side, spread)
        fair_base = explicit_fair if explicit_fair is not None else mid_probability
        if fair_base is None:
            return self._blocked(quote, side, "fair_probability_unavailable", base_metrics)
        fair_probability = round(
            max(
                0.001,
                min(
                    0.999,
                    float(fair_base)
                    + float(momentum_adjustment)
                    + float(learning_adjustment.get("learning_adjustment") or 0.0)
                    + float(liquidity_adjustment),
                ),
            ),
            4,
        )
        edge_after_spread = round(float(fair_probability) - float(taker_entry_price), 4)
        entry_samples = int(bucket.get("entry_samples") or bucket.get("green_entries") or bucket.get("red_entries") or 0)
        green_entries = int(bucket.get("green_entries") or 0)
        red_entries = int(bucket.get("red_entries") or 0)
        bucket_red_rate = round(red_entries / max(1, entry_samples), 4) if entry_samples else 0.0
        recent_red = self._recent_red_entry_rate(family=quote.family, side=side, zone=zone)
        combo_recent_red = self._recent_red_entry_rate(family=quote.family, side=side, zone=zone, combo_only=True)
        metrics = {
            **base_metrics,
            "immediate_pnl_per_contract": immediate_pnl,
            "spread_cost": spread_cost,
            "edge_after_spread": edge_after_spread,
            "first_cashout_quality": first_cashout_quality,
            "fair_probability_for_side": fair_probability,
            "market_mid_probability": mid_probability,
            "explicit_fair_probability": explicit_fair,
            "momentum_adjustment": momentum_adjustment,
            "liquidity_quality_adjustment": liquidity_adjustment,
            "entry_samples": entry_samples,
            "green_entries": green_entries,
            "red_entries": red_entries,
            "red_entry_rate": bucket_red_rate,
            "recent_red_entry_rate": recent_red.get("red_entry_rate"),
            "recent_red_entry_samples": recent_red.get("samples"),
            "combo_recent_red_entry_rate": combo_recent_red.get("red_entry_rate"),
            "combo_recent_red_entry_samples": combo_recent_red.get("samples"),
        }
        self.bot.log_json(
            "AION-ENTRY-IMMEDIATE-PNL",
            {
                "ticker": quote.ticker,
                "family": quote.family,
                "side": side,
                "entry_price": taker_entry_price,
                "immediate_cashout": cashout,
                "immediate_pnl_per_contract": immediate_pnl,
                "edge_after_spread": edge_after_spread,
                "first_cashout_quality": first_cashout_quality,
            },
        )
        if concentration.get("combo_family") and concentration.get("combo_conflicts", 0) > 0:
            return self._blocked(quote, side, "overconcentrated_combo_family", metrics)
        if concentration.get("combo_family") and (
            float(cashout) < self.combo_min_cashout_price
            or first_cashout_quality < self.combo_min_first_cashout_quality
        ):
            return self._blocked(
                quote,
                side,
                "weak_cashout_quality",
                {
                    **metrics,
                    "combo_min_cashout_price": self.combo_min_cashout_price,
                    "combo_min_first_cashout_quality": self.combo_min_first_cashout_quality,
                },
            )
        if concentration.get("combo_family") and int(combo_recent_red.get("samples") or 0) >= 2 and float(combo_recent_red.get("red_entry_rate") or 0.0) > self.combo_max_red_entry_rate:
            return self._blocked(quote, side, "recent_combo_red_entry_rate", metrics)
        if first_cashout_quality < self.min_first_cashout_quality:
            return self._blocked(quote, side, "weak_cashout_quality", metrics)
        if spread is not None and spread > float(getattr(self.bot, "max_entry_spread_hard", 0.55) or 0.55):
            return self._blocked(quote, side, "wide_spread", metrics)
        if entry_samples >= 4 and bucket_red_rate > self.max_red_entry_rate:
            return self._blocked(quote, side, "historically_red_family_side_zone", metrics)

        count = max(1, int(getattr(self.bot, "base_order_size", 1) or 1))
        taker_costs = self._cost_buffers(
            mode="taker",
            spread=spread,
            entry_price=float(taker_entry_price),
            fair_probability=fair_probability,
            learning_confidence=float(learning_adjustment.get("learning_confidence") or 0.0),
            avg_mae=float(learning_edge.get("avg_mae") or 0.0),
            count=count,
        )
        taker_ev = self._ev_after_costs(
            fair_probability=fair_probability,
            entry_price=float(taker_entry_price),
            costs=taker_costs,
        )
        maker_selection = self._maker_price_and_fill_probability(
            bid=cashout,
            ask=taker_entry_price,
            spread=spread,
            bid_size=bid_size,
            ask_size=ask_size,
            fair_probability=fair_probability,
        )
        maker_options: List[Dict[str, Any]] = []
        maker_price = coerce_price(maker_selection.get("maker_price"))
        maker_ev = None
        maker_costs: Dict[str, float] = {}
        if maker_price is not None and maker_price < float(taker_entry_price):
            maker_costs = self._cost_buffers(
                mode="maker",
                spread=spread,
                entry_price=float(maker_price),
                fair_probability=fair_probability,
                learning_confidence=float(learning_adjustment.get("learning_confidence") or 0.0),
                avg_mae=float(learning_edge.get("avg_mae") or 0.0),
                count=count,
            )
            maker_ev = self._ev_after_costs(
                fair_probability=fair_probability,
                entry_price=float(maker_price),
                costs=maker_costs,
            )
            maker_options.append(
                {
                    "maker_price": maker_price,
                    "maker_ev": maker_ev,
                    "maker_costs": maker_costs,
                    "fill_probability": float(maker_selection.get("fill_probability") or 0.0),
                    "maker_price_reason": maker_selection.get("reason"),
                }
            )
        passive_maker_price = coerce_price(cashout)
        if passive_maker_price is not None and passive_maker_price < float(taker_entry_price) and passive_maker_price != maker_price:
            passive_costs = self._cost_buffers(
                mode="maker",
                spread=spread,
                entry_price=float(passive_maker_price),
                fair_probability=fair_probability,
                learning_confidence=float(learning_adjustment.get("learning_confidence") or 0.0),
                avg_mae=float(learning_edge.get("avg_mae") or 0.0),
                count=count,
            )
            passive_ev = self._ev_after_costs(
                fair_probability=fair_probability,
                entry_price=float(passive_maker_price),
                costs=passive_costs,
            )
            passive_fill_probability = round(
                max(
                    self.min_maker_fill_probability,
                    min(0.65, float(maker_selection.get("fill_probability") or 0.0) * 0.60),
                ),
                4,
            )
            maker_options.append(
                {
                    "maker_price": passive_maker_price,
                    "maker_ev": passive_ev,
                    "maker_costs": passive_costs,
                    "fill_probability": passive_fill_probability,
                    "maker_price_reason": "passive_bid_fallback",
                }
            )
        if maker_options:
            best_maker = max(
                maker_options,
                key=lambda item: (
                    float(item.get("maker_ev") or -9.0),
                    float(item.get("fill_probability") or 0.0),
                ),
            )
            maker_price = coerce_price(best_maker.get("maker_price"))
            maker_ev = coerce_float(best_maker.get("maker_ev"), None)
            maker_costs = best_maker.get("maker_costs") if isinstance(best_maker.get("maker_costs"), dict) else {}
            maker_selection["fill_probability"] = best_maker.get("fill_probability")
            maker_selection["reason"] = best_maker.get("maker_price_reason")

        combo_family = bool(concentration.get("combo_family"))
        taker_threshold = self.strong_taker_edge_threshold + (0.02 if combo_family else 0.0)
        maker_threshold = self.combo_edge_threshold if combo_family else self.maker_edge_threshold
        entry_mode = None
        selected_price = None
        selected_ev = None
        selected_costs: Dict[str, float] = {}
        selected_fill_probability = 1.0
        selected_reason = None
        if taker_ev >= taker_threshold:
            entry_mode = "taker"
            selected_price = taker_entry_price
            selected_ev = taker_ev
            selected_costs = taker_costs
            selected_reason = "strong_taker_ev"
        elif (
            maker_ev is not None
            and maker_ev > maker_threshold
            and float(maker_selection.get("fill_probability") or 0.0) >= self.min_maker_fill_probability
        ):
            entry_mode = "maker"
            selected_price = maker_price
            selected_ev = maker_ev
            selected_costs = maker_costs
            selected_fill_probability = float(maker_selection.get("fill_probability") or 0.0)
            selected_reason = "maker_ev_positive"
        else:
            reject_metrics = {
                **metrics,
                "taker_ev": taker_ev,
                "maker_ev": maker_ev,
                "taker_edge_threshold": taker_threshold,
                "maker_edge_threshold": maker_threshold,
                "maker_price": maker_price,
                "fill_probability": maker_selection.get("fill_probability"),
                "maker_options": maker_options,
                "entry_mode": None,
                "ev_after_all_costs": max(taker_ev, maker_ev if maker_ev is not None else -9.0),
            }
            return self._blocked(quote, side, "ev_after_cost_not_positive", reject_metrics)

        spread_score = 15.0
        if spread is not None:
            max_spread = max(0.01, float(getattr(self.bot, "max_entry_spread_hard", 0.55) or 0.55))
            spread_score = max(0.0, 15.0 * (1.0 - min(spread, max_spread) / max_spread))
        cashout_quality_score = min(12.0, 3.0 + max(0.0, first_cashout_quality) * 8.0)
        ev_score = min(65.0, max(0.0, float(selected_ev or 0.0)) * 1800.0)
        edge_score = min(20.0, max(0.0, edge_after_spread) * 900.0)
        history_score = min(12.0, max(0.0, float(learning_edge.get("avg_realized_pnl") or 0.0)) * 120.0 + max(0.0, float(learning_edge.get("mfe_before_mae_rate") or 0.0) - 0.5) * 12.0)
        liquidity_score = min(5.0, math.log1p(max(0.0, min(executable_size, cashout_size))) * 1.5)
        fill_score = 5.0 if entry_mode == "taker" else min(5.0, selected_fill_probability * 8.0)
        raw_score = 12.0 + ev_score + edge_score + cashout_quality_score + spread_score + history_score + liquidity_score + fill_score
        score = round(raw_score * float(learning_multiplier) * float(concentration.get("concentration_penalty") or 1.0), 4)
        confidence = round(max(0.0, min(1.0, score / 100.0)), 4)
        result = {
            **metrics,
            "blocked": False,
            "reason": "passed",
            "selected_reason": selected_reason,
            "side": side,
            "entry_mode": entry_mode,
            "selected_price": selected_price,
            "entry_price": selected_price,
            "maker_price": maker_price,
            "taker_entry_price": taker_entry_price,
            "taker_ev": taker_ev,
            "maker_ev": maker_ev,
            "ev_after_all_costs": selected_ev,
            "maker_price_reason": maker_selection.get("reason"),
            "expected_spread_cost": selected_costs.get("expected_spread_cost"),
            "expected_exit_cost": selected_costs.get("expected_exit_cost"),
            "fees": selected_costs.get("fees"),
            "slippage_buffer": selected_costs.get("slippage_buffer"),
            "model_error_buffer": selected_costs.get("model_error_buffer"),
            "adverse_selection_buffer": selected_costs.get("adverse_selection_buffer"),
            "fill_probability": selected_fill_probability,
            "taker_costs": taker_costs,
            "maker_costs": maker_costs,
            "score": score,
            "confidence": confidence,
            "green_entry_score": score,
            "opened_green": immediate_pnl >= 0.0,
        }
        ev_payload = {"ticker": quote.ticker, "family": quote.family, **result}
        self.bot.log_json("AION-EV-AFTER-COST", ev_payload)
        self.bot.log_json("AION-EV-SCORE", ev_payload)
        return result


class GreenEntryConfirm:
    """Canonical entry confirmation with EV-after-cost economics built in."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    def _payload_limit_price(self, payload: Dict[str, Any], side: str) -> Optional[float]:
        key = "yes_price" if side == "yes" else "no_price"
        return coerce_price(payload.get(key))

    def _reject(
        self,
        target: TradeTarget,
        payload: Dict[str, Any],
        *,
        reason: str,
        extra: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        extra = extra if isinstance(extra, dict) else {}
        reject_payload = {
            "reason": reason,
            "ticker": target.ticker,
            "family": target.family,
            "side": normalize_side(target.side),
            "selected_price": target.selected_price,
            "spread": target.spread,
            "liquidity": target.liquidity,
            "volume": target.volume,
            "target_id": target.target_id,
            "trace_id": payload.get("trace_id") or target.trace_id or target.target_id,
            **extra,
        }
        self.bot.log_json("AION-EV-ENTRY-REJECT", reject_payload)
        if reason in {"side_payload_mismatch", "stale_signal"}:
            self.bot._log_confirm_entry_reject(target, payload, reason=reason, extra=extra)
        return {"allowed": False, "reason": reason, "payload": payload, **extra}

    def confirm(
        self,
        target: TradeTarget,
        current_orderbook: Quote | Dict[str, Any] | None = None,
        *,
        payload: Dict[str, Any] | None = None,
        source: str = "confirm_entry",
    ) -> Dict[str, Any]:
        if not isinstance(target, TradeTarget) or not getattr(getattr(TradeTarget, "__dataclass_params__", None), "frozen", False):
            return {"allowed": False, "reason": "target_not_immutable_trade_target"}
        payload = payload if isinstance(payload, dict) else self.bot._payload_for_target(target)
        if bool(payload.get("reduce_only")):
            return self._reject(target, payload, reason="entry_cannot_be_reduce_only")
        side = normalize_side(target.side)
        payload_side = normalize_side(payload.get("side"))
        payload_selected_side = normalize_side(payload.get("selected_side"))
        disabled_reason = self.bot._disabled_new_entry_reason(family=target.family, ticker=target.ticker, market=getattr(current_orderbook, "raw_market", None))
        if disabled_reason:
            return self._reject(target, payload, reason=disabled_reason, extra={"family_disabled": True})
        if side not in {"yes", "no"}:
            return self._reject(target, payload, reason="missing_selected_side")
        for payload_field, value in (("side", payload_side), ("selected_side", payload_selected_side)):
            if value in {"yes", "no"} and value != side:
                return self._reject(
                    target,
                    payload,
                    reason="side_payload_mismatch",
                    extra={"payload_field": payload_field, "payload_side": value},
                )
        price_keys = [key for key in ("yes_price", "no_price") if payload.get(key) not in (None, "")]
        expected_price_key = "yes_price" if side == "yes" else "no_price"
        if price_keys and price_keys != [expected_price_key]:
            return self._reject(
                target,
                payload,
                reason="side_payload_mismatch",
                extra={"price_keys": price_keys, "expected_price_key": expected_price_key},
            )
        freshness = self.bot._entry_signal_age(target, payload, now=time.time())
        max_signal_age = float(self.bot.max_signal_age_seconds)
        if freshness["signal_age_seconds"] > max_signal_age:
            return self._reject(
                target,
                payload,
                reason="stale_signal",
                extra={
                    "signal_age_seconds": freshness["signal_age_seconds"],
                    "max_signal_age_seconds": max_signal_age,
                    "signal_ts_source": freshness.get("signal_ts_source"),
                },
            )
        if not valid_trade_price(target.selected_price):
            return self._reject(target, payload, reason="selected_price_out_of_bounds")
        if target.count < 1 or target.count > self.bot.max_live_order_size:
            return self._reject(target, payload, reason="sane_max_sizing_guard")
        quote = current_orderbook if isinstance(current_orderbook, Quote) else None
        if quote is None:
            return self._reject(target, payload, reason="missing_current_orderbook")
        if quote.synthetic or not quote.orderbook_real:
            return self._reject(
                target,
                payload,
                reason="fake_or_synthetic_book_rejected",
                extra={"orderbook_source": quote.orderbook_source, "orderbook_real": quote.orderbook_real, "synthetic": quote.synthetic},
            )
        payload_limit_price = self._payload_limit_price(payload, side)
        current_entry_price = coerce_price(quote.price_for_side(side, "buy"))
        current_cashout = coerce_price(quote.price_for_side(side, "sell"))
        if not valid_trade_price(current_entry_price):
            return self._reject(target, payload, reason="no_executable_selected_side_price")
        if current_cashout is None or quote.size_for_side(side, "sell") < self.bot.green_entry_scorer.min_cashout_size:
            return self._reject(target, payload, reason="missing_first_cashout")
        if payload_limit_price is None:
            return self._reject(target, payload, reason="side_payload_mismatch", extra={"expected_price_key": expected_price_key})
        side_score = self.bot.green_entry_scorer.score_side(
            quote,
            side,
            source=source,
        )
        if not side_score or side_score.get("blocked"):
            return self._reject(
                target,
                payload,
                reason=(side_score or {}).get("reason") or "ev_entry_rejected",
                extra={key: value for key, value in (side_score or {}).items() if key not in {"blocked", "reason"}},
            )
        entry_mode = str(side_score.get("entry_mode") or "").strip().lower()
        selected_price = coerce_price(side_score.get("selected_price"))
        if entry_mode not in {"maker", "taker"} or selected_price is None:
            return self._reject(target, payload, reason="ev_entry_mode_unavailable", extra=side_score)
        if entry_mode == "taker" and selected_price + 0.0001 < float(current_entry_price):
            return self._reject(
                target,
                payload,
                reason="current_entry_price_exceeds_payload",
                extra={"current_entry_price": current_entry_price, "selected_price": selected_price},
            )
        if entry_mode == "maker" and selected_price >= float(current_entry_price):
            return self._reject(
                target,
                payload,
                reason="maker_order_would_cross_spread",
                extra={"current_entry_price": current_entry_price, "selected_price": selected_price},
            )
        cents = kalshi_execution.price_to_cents(selected_price)
        if cents is None:
            return self._reject(target, payload, reason="invalid_ev_selected_price", extra={"selected_price": selected_price})
        payload.pop("yes_price", None)
        payload.pop("no_price", None)
        payload[expected_price_key] = cents
        payload["selected_price"] = selected_price
        payload["entry_price"] = selected_price
        payload["entry_mode"] = entry_mode
        if entry_mode == "maker":
            payload["post_only"] = True
            payload["time_in_force"] = "good_till_canceled"
            payload["self_trade_prevention_type"] = "maker"
            payload["maker_price"] = selected_price
            payload["expiration_ts"] = int(time.time() + self.bot.max_pending_maker_age_seconds + 5)
        else:
            payload["post_only"] = False
            payload["time_in_force"] = os.environ.get("AION_TAKER_TIME_IN_FORCE", "immediate_or_cancel")
            payload["self_trade_prevention_type"] = "taker_at_cross"
        self.bot.reconcile_live_positions(min_interval_seconds=5.0)
        pending_risk = self.bot.evaluate_pending_order_risk(target, payload, side_score)
        if not pending_risk.get("ok"):
            return self._reject(target, payload, reason=pending_risk.get("reason") or "pending_order_guard_rejected", extra={"pending_risk": pending_risk})
        risk = kalshi_risk.evaluate_entry_risk(self.bot, target, payload)
        if not risk.get("ok"):
            return self._reject(target, payload, reason=risk.get("reason") or "risk_guard_rejected", extra={"risk": risk})
        ev_fields = {
            "entry_price": side_score.get("entry_price"),
            "immediate_cashout": side_score.get("immediate_cashout"),
            "immediate_pnl_per_contract": side_score.get("immediate_pnl_per_contract"),
            "edge_after_spread": side_score.get("edge_after_spread"),
            "first_cashout_quality": side_score.get("first_cashout_quality"),
            "spread_cost": side_score.get("spread_cost"),
            "green_entry_score": side_score.get("green_entry_score") or side_score.get("score"),
            "fair_probability_for_side": side_score.get("fair_probability_for_side"),
            "entry_mode": entry_mode,
            "maker_price": side_score.get("maker_price"),
            "taker_entry_price": side_score.get("taker_entry_price"),
            "taker_ev": side_score.get("taker_ev"),
            "maker_ev": side_score.get("maker_ev"),
            "ev_after_all_costs": side_score.get("ev_after_all_costs"),
            "expected_spread_cost": side_score.get("expected_spread_cost"),
            "expected_exit_cost": side_score.get("expected_exit_cost"),
            "fees": side_score.get("fees"),
            "slippage_buffer": side_score.get("slippage_buffer"),
            "model_error_buffer": side_score.get("model_error_buffer"),
            "adverse_selection_buffer": side_score.get("adverse_selection_buffer"),
            "fill_probability": side_score.get("fill_probability"),
            "selected_reason": side_score.get("selected_reason"),
            "learning_multiplier": side_score.get("learning_multiplier"),
            "concentration_penalty": side_score.get("concentration_penalty"),
            "event_window_key": side_score.get("event_window_key"),
            "combo_family": side_score.get("combo_family"),
            "opened_green": side_score.get("opened_green"),
            "signal_age_seconds": freshness["signal_age_seconds"],
            "max_signal_age_seconds": max_signal_age,
        }
        payload.update(ev_fields)
        payload["_aion_confirm_entry_passed"] = True
        payload["_aion_ev_entry_confirm_passed"] = True
        payload["_aion_confirm_entry_source"] = source
        self.bot.log_json(
            "AION-EV-ENTRY-PASS",
            {
                "ticker": target.ticker,
                "family": target.family,
                "side": side,
                "target_id": target.target_id,
                "trace_id": payload.get("trace_id") or target.trace_id or target.target_id,
                **ev_fields,
            },
        )
        return {"allowed": True, "reason": "passed", "payload": payload, "ev_entry": side_score}


class KalshiSniperBot:
    def __init__(self, *, live_mode: bool = False, live_armed: bool = False, live_env_confirmed: bool = False) -> None:
        self.repo_root = Path(__file__).resolve().parent
        self.runtime_root = Path(os.environ.get("AION_RUNTIME_ROOT", self.repo_root / "runtime"))
        self.state_path = Path(os.environ.get("AION_LIVE_ENGINE_STATE", self.runtime_root / "state" / "aion_live_engine.json"))
        self.session = requests.Session()
        self.live_mode = bool(live_mode)
        self.live_armed = bool(live_armed)
        self.live_env_confirmed = bool(live_env_confirmed)
        self.configured_dry_run = env_bool("DRY_RUN_LIVE_ORDERS", True)
        self.effective_live_dry_run = kalshi_execution.effective_dry_run(
            live_mode=self.live_mode,
            live_armed=self.live_armed,
            live_env_confirmed=self.live_env_confirmed,
            configured_dry_run=self.configured_dry_run,
        )

        self.scan_limit = max(1, env_int("AION_SCAN_LIMIT", 200))
        self.scan_pages = max(1, env_int("AION_SCAN_PAGES", 3))
        self.max_hydrate_per_cycle = max(1, env_int("AION_MAX_HYDRATE_PER_CYCLE", 80))
        self.loop_sleep_seconds = max(1.0, env_float("AION_LOOP_SLEEP_SECONDS", 5.0))
        self.max_live_order_size = max(1, env_int("MAX_LIVE_ORDER_SIZE", env_int("AION_MAX_LIVE_ORDER_SIZE", 3)))
        self.base_order_size = max(1, env_int("AION_BASE_ORDER_SIZE", 1))
        self.max_open_positions = max(1, env_int("AION_MAX_OPEN_POSITIONS", 8))
        self.max_daily_loss = max(0.0, env_float("AION_MAX_DAILY_LOSS", env_float("MAX_DAILY_LOSS", 50.0)))
        self.live_order_rate_limit = max(1, env_int("LIVE_ORDER_RATE_LIMIT", env_int("AION_LIVE_ORDER_RATE_LIMIT", 5)))
        self.live_order_rate_window_sec = max(1.0, env_float("LIVE_ORDER_RATE_WINDOW_SEC", 60.0))
        self.max_dollars_per_trade = max(1.0, env_float("AION_MAX_DOLLARS_PER_TRADE", 20.0))
        self.max_single_position_pct = min(1.0, max(0.001, env_float("AION_MAX_SINGLE_POSITION_PCT", 0.05)))
        self.min_confidence_to_submit = max(0.0, env_float("AION_MIN_CONFIDENCE_TO_SUBMIT", 0.15))
        self.min_score_to_submit = max(0.0, env_float("AION_MIN_SCORE_TO_SUBMIT", 8.0))
        self.max_entry_spread_hard = min(0.99, max(0.05, env_float("AION_MAX_ENTRY_SPREAD_HARD", 0.55)))
        self.max_pending_maker_age_seconds = max(5.0, env_float("AION_MAKER_ORDER_MAX_AGE_SECONDS", 30.0))
        self.max_pending_maker_orders = max(0, env_int("AION_MAX_PENDING_MAKER_ORDERS", 2))
        self.maker_cancel_fair_decay = max(0.0, env_float("AION_MAKER_FAIR_DECAY_CANCEL", 0.01))
        self.better_candidate_ev_delta = max(0.0, env_float("AION_BETTER_CANDIDATE_EV_DELTA", 0.006))
        configured_signal_age = env_float("AION_MAX_SIGNAL_AGE_SECONDS", env_float("MAX_SIGNAL_AGE_SECONDS", 45.0))
        self.max_signal_age_seconds = min(99.0, max(1.0, configured_signal_age))
        self.balance_cache_ttl = max(1.0, env_float("AION_BALANCE_CACHE_SECONDS", 20.0))
        self.exit_config = {
            "take_profit": max(0.005, env_float("AION_EXIT_TAKE_PROFIT", 0.04)),
            "stop_loss": max(0.005, env_float("AION_EXIT_STOP_LOSS", 0.04)),
            "trailing_start": max(0.005, env_float("AION_EXIT_TRAILING_START", 0.03)),
            "trailing_distance": max(0.0025, env_float("AION_EXIT_TRAILING_DISTANCE", 0.015)),
            "max_hold_seconds": max(30.0, env_float("AION_EXIT_MAX_HOLD_SECONDS", 900.0)),
        }

        self.live_positions: List[Dict[str, Any]] = []
        self.live_order_timestamps: List[float] = []
        self._balance_cache: Dict[str, Any] = {}
        self._last_reconcile_ts = 0.0
        self._quote_memory: Dict[str, Dict[str, Any]] = {}
        self.cycle_count = 0
        self.stop_requested = False
        self.state = self.load_state()
        self.live_positions = self._local_open_positions()
        self.green_entry_scorer = GreenEntryScorer(self)

    def to_float(self, value: Any, default: Optional[float] = 0.0) -> Optional[float]:
        return coerce_float(value, default)

    def normalize_contract_price(self, value: Any, **_: Any) -> Optional[float]:
        return coerce_price(value)

    def log_json(self, tag: str, payload: Dict[str, Any] | None = None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        payload.setdefault("ts", utc_now_iso())
        try:
            print(f"[{tag}] " + json.dumps(payload, sort_keys=True, default=str), flush=True)
        except Exception:
            print(f"[{tag}] {payload}", flush=True)

    def load_state(self) -> Dict[str, Any]:
        default = {
            "daily": {
                "date": time.strftime("%Y-%m-%d"),
                "realized_pnl": 0.0,
                "live_trade_count": 0,
                "submitted_order_count": 0,
            },
            "positions": {},
            "pending_orders": {},
            "learning": {
                "family_side_zone": {},
                "exit_outcomes": [],
            },
        }
        try:
            if self.state_path.exists():
                loaded = json.loads(self.state_path.read_text())
                if isinstance(loaded, dict):
                    for key, value in default.items():
                        loaded.setdefault(key, value)
                    if not isinstance(loaded.get("pending_orders"), dict):
                        loaded["pending_orders"] = {}
                    return loaded
        except Exception as exc:
            self.log_json("AION-STATE-LOAD-ERROR", {"reason": exc.__class__.__name__, "path": str(self.state_path)})
        return default

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(self.state, sort_keys=True, indent=2, default=str))
        tmp_path.replace(self.state_path)

    def _local_open_positions(self) -> List[Dict[str, Any]]:
        positions = []
        for ticker, position in (self.state.get("positions") or {}).items():
            if not isinstance(position, dict):
                continue
            row = dict(position)
            row["ticker"] = str(row.get("ticker") or ticker)
            if str(row.get("status") or "open").lower() in {"open", "exit_pending"}:
                positions.append(row)
        return positions

    def _pending_orders(self) -> Dict[str, Dict[str, Any]]:
        pending = self.state.setdefault("pending_orders", {})
        if not isinstance(pending, dict):
            self.state["pending_orders"] = {}
            pending = self.state["pending_orders"]
        return pending

    def active_pending_orders(self) -> List[Dict[str, Any]]:
        active = []
        for key, row in list(self._pending_orders().items()):
            if not isinstance(row, dict):
                self._pending_orders().pop(key, None)
                continue
            status = str(row.get("status") or "pending").strip().lower()
            if status in {"pending", "accepted", "submitted"}:
                active.append({**row, "pending_id": key})
        return active

    def evaluate_pending_order_risk(self, target: TradeTarget, payload: Dict[str, Any], side_score: Dict[str, Any]) -> Dict[str, Any]:
        entry_mode = str(payload.get("entry_mode") or side_score.get("entry_mode") or "").strip().lower()
        side = normalize_side(target.side)
        ticker = str(target.ticker or "").strip()
        target_event = str(side_score.get("event_window_key") or event_window_key(target.family, target.ticker)).upper()
        combo_family = bool(side_score.get("combo_family") or is_combo_or_cross_market(target.family, target.ticker))
        active = self.active_pending_orders()
        for pending in active:
            pending_ticker = str(pending.get("ticker") or "").strip()
            pending_side = normalize_side(pending.get("side"))
            if pending_ticker == ticker and pending_side in {"yes", "no"} and pending_side != side:
                return {
                    "ok": False,
                    "reason": "exact_same_ticker_opposite_side_guard",
                    "pending_order_id": pending.get("order_id"),
                    "pending_side": pending_side,
                    "selected_side": side,
                }
            if pending_ticker == ticker:
                return {
                    "ok": False,
                    "reason": "duplicate_pending_order_guard",
                    "pending_order_id": pending.get("order_id"),
                }
            if combo_family and is_combo_or_cross_market(pending.get("family"), pending_ticker):
                pending_event = str(pending.get("event_window_key") or event_window_key(pending.get("family"), pending_ticker)).upper()
                if pending_event == target_event or str(pending.get("family") or "").upper() == str(target.family or "").upper():
                    return {
                        "ok": False,
                        "reason": "overconcentrated_combo_family",
                        "pending_order_id": pending.get("order_id"),
                        "event_window_key": target_event,
                    }
        if entry_mode == "maker":
            maker_pending = [row for row in active if str(row.get("entry_mode") or "").lower() == "maker"]
            if len(maker_pending) >= self.max_pending_maker_orders:
                return {
                    "ok": False,
                    "reason": "max_pending_orders_reached",
                    "pending_maker_orders": len(maker_pending),
                    "max_pending_maker_orders": self.max_pending_maker_orders,
                }
        return {"ok": True, "reason": "passed", "pending_orders": len(active)}

    def _entry_log_fields(self, target: TradeTarget, payload: Dict[str, Any], result: Dict[str, Any] | None = None) -> Dict[str, Any]:
        result = result if isinstance(result, dict) else {}
        return {
            "ticker": target.ticker,
            "family": target.family,
            "side": target.side,
            "fair_probability_for_side": payload.get("fair_probability_for_side"),
            "entry_mode": payload.get("entry_mode"),
            "entry_price": payload.get("entry_price"),
            "maker_price": payload.get("maker_price"),
            "taker_entry_price": payload.get("taker_entry_price"),
            "taker_ev": payload.get("taker_ev"),
            "maker_ev": payload.get("maker_ev"),
            "EV_after_all_costs": payload.get("ev_after_all_costs"),
            "ev_after_all_costs": payload.get("ev_after_all_costs"),
            "spread": payload.get("spread"),
            "expected_exit_cost": payload.get("expected_exit_cost"),
            "expected_spread_cost": payload.get("expected_spread_cost"),
            "fees": payload.get("fees"),
            "slippage_buffer": payload.get("slippage_buffer"),
            "model_error_buffer": payload.get("model_error_buffer"),
            "adverse_selection_buffer": payload.get("adverse_selection_buffer"),
            "selected_reason": payload.get("selected_reason"),
            "fill_probability": payload.get("fill_probability"),
            "target_id": target.target_id,
            "trace_id": payload.get("trace_id") or target.trace_id or target.target_id,
            "order_id": result.get("order_id"),
            "client_order_id": payload.get("client_order_id"),
        }

    def record_pending_entry_order(self, target: TradeTarget, payload: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        order_id = str(result.get("order_id") or payload.get("client_order_id") or target.target_id)
        entry_mode = str(payload.get("entry_mode") or "taker").strip().lower()
        row = {
            **self._entry_log_fields(target, payload, result),
            "pending_id": order_id,
            "order_id": result.get("order_id"),
            "client_order_id": payload.get("client_order_id"),
            "status": "pending",
            "submitted_ts": now,
            "expires_ts": now + (self.max_pending_maker_age_seconds if entry_mode == "maker" else 90.0),
            "selected_price": payload.get("selected_price"),
            "count": payload.get("count") or target.count,
            "price_zone": payload.get("price_zone") or target.price_zone,
            "event_window_key": payload.get("event_window_key") or event_window_key(target.family, target.ticker),
            "combo_family": bool(payload.get("combo_family") or is_combo_or_cross_market(target.family, target.ticker)),
        }
        self._pending_orders()[order_id] = row
        self.log_json("AION-ORDER-ACCEPTED", row)
        self.log_json("ORDER-PENDING", row)
        if entry_mode == "maker":
            self.log_json("AION-MAKER-ORDER-SUBMIT", row)
        else:
            self.log_json("AION-TAKER-ORDER-SUBMIT", row)
        return row

    def _matching_pending_order(self, position: Dict[str, Any]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        ticker = str(position.get("ticker") or "").strip()
        side = normalize_side(position.get("side"))
        for key, pending in self._pending_orders().items():
            if not isinstance(pending, dict):
                continue
            if str(pending.get("ticker") or "").strip() != ticker:
                continue
            pending_side = normalize_side(pending.get("side"))
            if pending_side in {"yes", "no"} and side in {"yes", "no"} and pending_side != side:
                continue
            if str(pending.get("status") or "pending").lower() not in {"pending", "accepted", "submitted"}:
                continue
            return key, pending
        return None, None

    def _confirmed_position_from_pending(self, position: Dict[str, Any], pending: Dict[str, Any]) -> Dict[str, Any]:
        entry_price = coerce_price(position.get("entry_price") or pending.get("entry_price") or pending.get("selected_price"))
        count = abs(float(self.to_float(position.get("count"), pending.get("count") or 0.0) or 0.0))
        executor = PositionExecutor(
            ticker=str(position.get("ticker") or pending.get("ticker") or "").strip(),
            family=str(pending.get("family") or position.get("family") or "UNKNOWN").upper(),
            side=normalize_side(position.get("side") or pending.get("side")) or "yes",
            entry_price=entry_price or 0.0,
            count=max(1, int(count or 1)),
            entry_ts=time.time(),
            tp=self.exit_config["take_profit"],
            sl=self.exit_config["stop_loss"],
            max_hold_seconds=self.exit_config["max_hold_seconds"],
            trailing_start=self.exit_config["trailing_start"],
            trailing_distance=self.exit_config["trailing_distance"],
        )
        return {
            **pending,
            **position,
            "family": str(pending.get("family") or position.get("family") or market_family({"ticker": position.get("ticker")})).upper(),
            "side": normalize_side(position.get("side") or pending.get("side")),
            "count": count,
            "entry_price": entry_price,
            "first_cashout": coerce_price(pending.get("immediate_cashout")),
            "immediate_cashout": coerce_price(pending.get("immediate_cashout")),
            "immediate_pnl_per_contract": coerce_float(pending.get("immediate_pnl_per_contract"), None),
            "opened_green": bool(coerce_float(pending.get("immediate_pnl_per_contract"), -1.0) >= 0.0),
            "status": "open",
            "entry_ts": time.time(),
            "confirmed_ts": time.time(),
            "target_id": pending.get("target_id"),
            "price_zone": pending.get("price_zone") or price_zone(entry_price),
            "event_window_key": pending.get("event_window_key") or event_window_key(pending.get("family"), pending.get("ticker")),
            "market_type": "combo_cross" if pending.get("combo_family") else "sports",
            "position_executor": executor.to_dict(),
        }

    def cancel_pending_maker_order(self, pending: Dict[str, Any], reason: str) -> Dict[str, Any]:
        pending = pending if isinstance(pending, dict) else {}
        order_id = str(pending.get("order_id") or "").strip()
        if order_id:
            result = self.cancel_live_order(order_id)
        else:
            result = {"ok": True, "reason": "missing_exchange_order_id_local_cancel"}
        pending_id = str(pending.get("pending_id") or pending.get("order_id") or pending.get("client_order_id") or "")
        if pending_id:
            self._pending_orders().pop(pending_id, None)
        payload = {
            **pending,
            "cancel_reason": reason,
            "cancel_ok": bool(result.get("ok")),
            "cancel_result_reason": result.get("reason"),
        }
        self.log_json("AION-MAKER-ORDER-CANCEL", payload)
        self.save_state()
        return result

    def manage_pending_maker_orders(self, selected_target: TradeTarget | None = None) -> Dict[str, Any]:
        cancelled = 0
        active = self.active_pending_orders()
        selected_ev = coerce_float(getattr(selected_target, "ev_after_all_costs", None), None) if selected_target else None
        for pending in list(active):
            entry_mode = str(pending.get("entry_mode") or "").lower()
            age = time.time() - float(pending.get("submitted_ts") or 0.0)
            if entry_mode != "maker":
                if age > 120.0:
                    self._pending_orders().pop(str(pending.get("pending_id") or ""), None)
                continue
            cancel_reason = None
            if age > self.max_pending_maker_age_seconds or time.time() > float(pending.get("expires_ts") or 0.0):
                cancel_reason = "stale_maker_order"
            elif selected_ev is not None and selected_target and selected_target.ticker != pending.get("ticker"):
                pending_ev = float(coerce_float(pending.get("ev_after_all_costs"), 0.0) or 0.0)
                if selected_ev > pending_ev + self.better_candidate_ev_delta:
                    cancel_reason = "better_candidate_available"
            if cancel_reason is None:
                try:
                    market = self.get_live_market_detail(str(pending.get("ticker") or ""))
                    orderbook = self.get_live_market_orderbook(str(pending.get("ticker") or ""))
                    quote = build_quote(market, orderbook)
                    side = normalize_side(pending.get("side"))
                    current = self.green_entry_scorer.score_side(quote, side, source="maker_order_lifecycle") if side else None
                    if not current or current.get("blocked"):
                        cancel_reason = f"maker_ev_decayed:{(current or {}).get('reason') or 'score_unavailable'}"
                    elif float(coerce_float(current.get("fair_probability_for_side"), 0.0) or 0.0) < float(coerce_float(pending.get("fair_probability_for_side"), 0.0) or 0.0) - self.maker_cancel_fair_decay:
                        cancel_reason = "fair_probability_decayed"
                    else:
                        opposite = "no" if side == "yes" else "yes"
                        opposite_score = self.green_entry_scorer.score_side(quote, opposite, source="maker_order_lifecycle_opposite")
                        if opposite_score and not opposite_score.get("blocked"):
                            current_ev = float(coerce_float(current.get("ev_after_all_costs"), 0.0) or 0.0)
                            opposite_ev = float(coerce_float(opposite_score.get("ev_after_all_costs"), 0.0) or 0.0)
                            if opposite_ev > current_ev + self.better_candidate_ev_delta:
                                cancel_reason = "side_flipped"
                except Exception as exc:
                    self.log_json("AION-MAKER-ORDER-LIFECYCLE-CHECK", {"ticker": pending.get("ticker"), "reason": f"quote_error:{exc.__class__.__name__}"})
            if cancel_reason:
                self.cancel_pending_maker_order(pending, cancel_reason)
                cancelled += 1
        return {"ok": True, "cancelled": cancelled, "active_pending": len(self.active_pending_orders())}

    def get_live_base_url(self) -> str:
        return os.environ.get("KALSHI_LIVE_BASE_URL", "") or "https://api.elections.kalshi.com"

    def build_live_request_url(self, path: str) -> str:
        base_url = self.get_live_base_url().rstrip("/")
        canonical_prefix = "/trade-api/v2"
        if base_url.endswith(canonical_prefix) and path.startswith(f"{canonical_prefix}/"):
            return f"{base_url}{path[len(canonical_prefix):]}"
        return f"{base_url}{path}"

    def get_live_auth_config(self) -> Dict[str, Any]:
        api_key_source = None
        api_key = ""
        if os.environ.get("KALSHI_API_KEY_ID"):
            api_key_source = "KALSHI_API_KEY_ID"
            api_key = os.environ.get("KALSHI_API_KEY_ID", "")
        elif os.environ.get("KALSHI_API_KEY"):
            api_key_source = "KALSHI_API_KEY"
            api_key = os.environ.get("KALSHI_API_KEY", "")

        private_key_source = None
        private_key_path = ""
        for key in ("LIVE_PRIVATE_KEY_PATH", "KALSHI_PRIVATE_KEY_PATH", "KALSHI_PRIVATE_KEY_FILE"):
            if os.environ.get(key):
                private_key_source = key
                private_key_path = os.environ.get(key, "")
                break
        if not private_key_path:
            private_key_source = "DEFAULT_LIVE_PRIVATE_KEY_PATH"
            private_key_path = str(self.repo_root / "keys" / "kalshi-live.pem")
        return {
            "api_key": api_key,
            "api_key_source": api_key_source,
            "private_key_path": private_key_path,
            "private_key_path_source": private_key_source,
            "private_key_exists": bool(private_key_path and Path(private_key_path).exists()),
        }

    def get_live_api_key(self) -> str:
        return str(self.get_live_auth_config().get("api_key") or "")

    def get_live_api_key_source(self) -> Optional[str]:
        return self.get_live_auth_config().get("api_key_source")

    def get_live_private_key_path(self) -> str:
        return str(self.get_live_auth_config().get("private_key_path") or "")

    def load_live_private_key(self) -> Optional[bytes]:
        path = self.get_live_private_key_path()
        try:
            if path and Path(path).exists():
                return Path(path).read_bytes()
        except OSError:
            return None
        return None

    def can_build_live_auth(self) -> bool:
        config = self.get_live_auth_config()
        return bool(config.get("api_key") and config.get("private_key_exists") and self.load_live_private_key())

    def sign_request(self, private_key: bytes, timestamp: str, method: str, path: str) -> bytes:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        key_obj = serialization.load_pem_private_key(private_key, password=None)
        message = f"{timestamp}{method.upper()}{path}".encode("utf-8")
        return key_obj.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )

    def build_live_auth_headers(self, method: str, path: str) -> Dict[str, Any]:
        path_to_sign = str(path or "").split("?", 1)[0]
        timestamp = str(int(time.time() * 1000))
        private_key = self.load_live_private_key()
        api_key = self.get_live_api_key()
        if not api_key or not private_key or not path_to_sign.startswith("/"):
            return {"ok": False, "reason": "missing_auth_config", "headers": None}
        try:
            signature = self.sign_request(private_key, timestamp, method, path_to_sign)
            return {
                "ok": True,
                "reason": "headers_built",
                "path_to_sign": path_to_sign,
                "headers": {
                    "KALSHI-ACCESS-KEY": api_key,
                    "KALSHI-ACCESS-TIMESTAMP": timestamp,
                    "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
                },
            }
        except Exception as exc:
            return {"ok": False, "reason": f"sign_error:{exc.__class__.__name__}", "headers": None}

    def _decode_live_response_json(self, response: requests.Response) -> Dict[str, Any]:
        try:
            return response.json() if getattr(response, "text", "") else {}
        except Exception:
            return {"raw_text": str(getattr(response, "text", "") or "")[:1000]}

    def _live_request_json(self, method: str, path: str, *, json_payload: Dict[str, Any] | None = None, timeout: int = 15) -> Dict[str, Any]:
        auth = self.build_live_auth_headers(method, path)
        if not auth.get("ok"):
            return {"ok": False, "reason": auth.get("reason"), "status_code": None, "path": path, "body": None}
        headers = dict(auth.get("headers") or {})
        headers["Accept"] = "application/json"
        if json_payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self.session.request(
                str(method or "GET").upper(),
                self.build_live_request_url(path),
                headers=headers,
                json=json_payload,
                timeout=timeout,
            )
            return {
                "ok": bool(response.ok),
                "reason": "ok" if response.ok else f"http_{response.status_code}",
                "status_code": response.status_code,
                "path": path,
                "body": self._decode_live_response_json(response),
            }
        except Exception as exc:
            return {"ok": False, "reason": f"request_error:{exc.__class__.__name__}", "status_code": None, "path": path, "body": None}

    def run_live_auth_get_smoke_test(self) -> Dict[str, Any]:
        result = self._live_request_json("GET", "/trade-api/v2/portfolio/balance", timeout=15)
        self.log_json(
            "LIVE-AUTH-GET-SMOKE",
            {
                "ok": bool(result.get("ok")),
                "reason": result.get("reason"),
                "status_code": result.get("status_code"),
                "path": result.get("path"),
            },
        )
        return result

    def run_live_smoke_test(self) -> Dict[str, Any]:
        auth = self.run_live_auth_get_smoke_test()
        positions = self.reconcile_live_positions(force=True)
        preview = self.preview_live_ticker_selection()
        result = {
            "ok": bool(auth.get("ok")),
            "auth_ok": bool(auth.get("ok")),
            "positions_ok": bool(positions.get("ok")),
            "preview_candidates": preview.get("candidate_count"),
            "open_positions": len(self.live_positions),
        }
        self.log_json("LIVE-SMOKE", result)
        return result

    def get_markets(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
        extra_params: Dict[str, Any] | None = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        params: Dict[str, Any] = {"limit": int(limit or self.scan_limit), "status": "open"}
        if isinstance(extra_params, dict):
            params.update({key: value for key, value in extra_params.items() if value not in (None, "")})
        if cursor:
            params["cursor"] = cursor
        try:
            response = self.session.get(self.build_live_request_url("/trade-api/v2/markets"), params=params, timeout=15)
            response.raise_for_status()
            return extract_markets(response.json() if response.content else {})
        except Exception as exc:
            self.log_json("AION-DISCOVERY-ERROR", {"reason": exc.__class__.__name__, "cursor": cursor})
            return [], None

    def get_live_market_detail(self, ticker: str) -> Dict[str, Any]:
        ticker = str(ticker or "").strip()
        if not ticker:
            return {}
        response = self.session.get(self.build_live_request_url(f"/trade-api/v2/markets/{ticker}"), timeout=10)
        response.raise_for_status()
        payload = response.json() if response.content else {}
        return payload.get("market", payload) if isinstance(payload, dict) else {}

    def get_live_market_orderbook(self, ticker: str) -> Dict[str, Any]:
        ticker = str(ticker or "").strip()
        if not ticker:
            return {}
        response = self.session.get(self.build_live_request_url(f"/trade-api/v2/markets/{ticker}/orderbook"), timeout=10)
        response.raise_for_status()
        return response.json() if response.content else {}

    def fetch_live_available_cash(self, *, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force and self._balance_cache and now - float(self._balance_cache.get("ts", 0.0) or 0.0) < self.balance_cache_ttl:
            return dict(self._balance_cache)
        result = self._live_request_json("GET", "/trade-api/v2/portfolio/balance", timeout=10)
        body = result.get("body") if isinstance(result.get("body"), dict) else {}
        cash = self._extract_cash_from_balance(body)
        out = {
            "ok": bool(cash is not None),
            "available_cash": cash,
            "reason": "ok" if cash is not None else result.get("reason") or "cash_balance_unavailable",
            "status_code": result.get("status_code"),
            "ts": now,
        }
        self._balance_cache = out
        return out

    def _extract_cash_from_balance(self, body: Dict[str, Any]) -> Optional[float]:
        dollar_fields = (
            "available_balance_dollars",
            "available_cash_dollars",
            "buying_power_dollars",
            "cash_balance_dollars",
            "balance_dollars",
        )
        cent_fields = (
            "available_balance_cents",
            "available_cash_cents",
            "buying_power_cents",
            "cash_balance_cents",
            "balance_cents",
            "available_balance",
            "buying_power",
            "cash_balance",
            "balance",
        )
        queue = [body] if isinstance(body, dict) else []
        seen: set[int] = set()
        while queue:
            current = queue.pop(0)
            if not isinstance(current, dict):
                continue
            ident = id(current)
            if ident in seen:
                continue
            seen.add(ident)
            for field in dollar_fields:
                value = coerce_float(current.get(field), None)
                if value is not None and value >= 0:
                    return round(float(value), 4)
            for field in cent_fields:
                value = coerce_float(current.get(field), None)
                if value is not None and value >= 0:
                    return round(float(value) / 100.0, 4)
            for value in current.values():
                if isinstance(value, dict):
                    queue.append(value)
        return None

    def get_market_family(self, market: Dict[str, Any]) -> str:
        return market_family(market)

    def is_sports_family(self, family: str, ticker: str | None = None) -> bool:
        return is_sports_market(family=family, ticker=ticker)

    def _disabled_new_entry_reason(self, *, family: str, ticker: str, market: Dict[str, Any] | None = None) -> Optional[str]:
        if is_tennis_market(family=family, ticker=ticker, market=market):
            return "tennis_live_trading_disabled"
        if is_kxmlbks_market(family=family, ticker=ticker, market=market):
            return "kxmlbks_live_trading_disabled"
        if not is_sports_market(family=family, ticker=ticker, market=market):
            return "sports_only_non_sports_execution_disabled"
        return None

    def _emit_final_reject(
        self,
        *,
        ticker: str | None,
        family: str | None,
        side: str | None = None,
        selected_price: Any = None,
        spread: Any = None,
        liquidity: Any = None,
        volume: Any = None,
        reason: str,
        subsystem: str,
        extra: Dict[str, Any] | None = None,
    ) -> None:
        self.log_json(
            "AION-FINAL-REJECT",
            {
                "ticker": ticker,
                "family": family,
                "side": side,
                "selected_price": coerce_price(selected_price),
                "spread": spread,
                "liquidity": liquidity,
                "volume": volume,
                "reason": reason,
                "responsible_subsystem": subsystem,
                **(extra or {}),
            },
        )

    def _coerce_epoch_seconds(self, value: Any) -> Optional[float]:
        if value in (None, "", [], {}):
            return None
        numeric = coerce_float(value, None)
        if numeric is not None:
            if numeric > 1_000_000_000_000:
                numeric = numeric / 1000.0
            if 0.0 < numeric < 4_102_444_800.0:
                return float(numeric)
            return None
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return None

    def _entry_signal_age(self, target: TradeTarget, payload: Dict[str, Any], *, now: float) -> Dict[str, Any]:
        signal_ts = None
        signal_source = None
        for source, value in (
            ("payload.signal_detected_ts", payload.get("signal_detected_ts")),
            ("target.signal_detected_ts", getattr(target, "signal_detected_ts", None)),
        ):
            signal_ts = self._coerce_epoch_seconds(value)
            if signal_ts is not None:
                signal_source = source
                break
        if signal_ts is None:
            for source, value in (
                ("payload.target_created_ts", payload.get("target_created_ts")),
                ("target.target_created_ts", getattr(target, "target_created_ts", None)),
                ("target.created_ts", getattr(target, "created_ts", None)),
                ("payload.created_ts", payload.get("created_ts")),
            ):
                signal_ts = self._coerce_epoch_seconds(value)
                if signal_ts is not None:
                    signal_source = source
                    break
        if signal_ts is None:
            signal_ts = now
            signal_source = "confirm_entry_now"
        age = round(max(0.0, now - signal_ts), 3)
        return {"signal_age_seconds": age, "signal_ts": signal_ts, "signal_ts_source": signal_source}

    def _log_confirm_entry_reject(self, target: TradeTarget, payload: Dict[str, Any], *, reason: str, extra: Dict[str, Any] | None = None) -> None:
        self.log_json(
            "AION-CONFIRM-ENTRY-REJECT",
            {
                "reason": reason,
                "ticker": target.ticker,
                "family": target.family,
                "selected_side": normalize_side(target.side),
                "target_id": target.target_id,
                "trace_id": payload.get("trace_id") or getattr(target, "trace_id", None) or target.target_id,
                **(extra or {}),
            },
        )

    def fetch_live_sports_discovery_markets(self) -> List[Dict[str, Any]]:
        markets: List[Dict[str, Any]] = []
        for extra_params in ({}, {"category": "Sports"}):
            cursor = None
            for _ in range(self.scan_pages):
                page, cursor = self.get_markets(limit=self.scan_limit, cursor=cursor, extra_params=extra_params)
                if not page:
                    break
                markets.extend(page)
                if not cursor:
                    break
        deduped: Dict[str, Dict[str, Any]] = {}
        for market in markets:
            ticker = market_ticker(market)
            if ticker:
                deduped.setdefault(ticker, market)
        sports = [
            market for market in deduped.values()
            if is_sports_market(family=market_family(market), ticker=market_ticker(market), market=market)
        ]
        self.log_json(
            "AION-LIVE-DISCOVERY",
            {
                "raw_markets": len(markets),
                "deduped_markets": len(deduped),
                "sports_candidates": len(sports),
                "hydration_cap": self.max_hydrate_per_cycle,
                "scan_pages": self.scan_pages,
            },
        )
        return sports[: self.max_hydrate_per_cycle]

    def hydrate_quote(self, market: Dict[str, Any]) -> Optional[Quote]:
        ticker = market_ticker(market)
        if not ticker:
            return None
        try:
            detail = self.get_live_market_detail(ticker)
            merged = dict(market)
            if isinstance(detail, dict):
                merged.update(detail)
        except Exception as exc:
            merged = dict(market)
            self.log_json(
                "AION-MARKET-DETAIL-FALLBACK",
                {
                    "ticker": ticker,
                    "family": market_family(market),
                    "reason": exc.__class__.__name__,
                    "subsystem": "market_detail_hydration",
                },
            )
        try:
            orderbook = self.get_live_market_orderbook(ticker)
        except Exception as exc:
            self._emit_final_reject(
                ticker=ticker,
                family=market_family(merged),
                reason=f"orderbook_api_error:{exc.__class__.__name__}",
                subsystem="orderbook_api",
                liquidity=merged.get("liquidity"),
                volume=merged.get("volume"),
            )
            return None
        return build_quote(merged, orderbook)

    def _learning_bucket_key(self, family: str, side: str, zone: str) -> str:
        return f"{str(family or 'UNKNOWN').upper()}|{normalize_side(side) or 'unknown'}|{zone or 'unknown'}"

    def _learning_multiplier(self, family: str, side: str, zone: str) -> float:
        bucket = ((self.state.get("learning") or {}).get("family_side_zone") or {}).get(self._learning_bucket_key(family, side, zone), {})
        trades = int(bucket.get("trades") or 0)
        entry_samples = int(bucket.get("entry_samples") or bucket.get("green_entries") or bucket.get("red_entries") or 0)
        if trades < 3 and entry_samples < 3:
            return 1.0
        avg_pnl = float(bucket.get("pnl") or 0.0) / max(1, trades)
        avg_immediate = float(bucket.get("immediate_pnl") or 0.0) / max(1, entry_samples)
        red_rate = float(bucket.get("red_entries") or 0.0) / max(1, entry_samples)
        mfe_samples = int(bucket.get("mfe_before_mae_samples") or 0)
        mfe_before_mae_rate = float(bucket.get("mfe_before_mae_positive") or 0.0) / max(1, mfe_samples)
        multiplier = 1.0
        if avg_pnl > 0.10:
            multiplier += 0.20
        elif avg_pnl > 0.02:
            multiplier += 0.10
        elif avg_pnl < -0.10:
            multiplier -= 0.30
        elif avg_pnl < -0.02:
            multiplier -= 0.15
        if avg_immediate > 0.01:
            multiplier += 0.08
        elif avg_immediate < 0.0:
            multiplier -= 0.20
        if entry_samples >= 3 and red_rate > 0.50:
            multiplier -= 0.25
        elif entry_samples >= 3 and red_rate == 0.0:
            multiplier += 0.05
        if mfe_samples >= 3 and mfe_before_mae_rate > 0.60:
            multiplier += 0.08
        elif mfe_samples >= 3 and mfe_before_mae_rate < 0.40:
            multiplier -= 0.08
        return round(max(0.35, min(1.35, multiplier)), 4)

    def _score_side(self, quote: Quote, side: str) -> Optional[Dict[str, Any]]:
        return self.green_entry_scorer.score_side(quote, side)

    def _confidence_size(self, *, confidence: float, selected_price: float, family: str, side: str, zone: str) -> Tuple[int, Dict[str, Any]]:
        multiplier = self._learning_multiplier(family, side, zone)
        raw_count = self.base_order_size
        if confidence >= 0.85:
            raw_count += 2
        elif confidence >= 0.60:
            raw_count += 1
        raw_count = max(1, int(math.floor(raw_count * multiplier)))
        raw_count = min(raw_count, self.max_live_order_size)

        balance = self.fetch_live_available_cash()
        cash = balance.get("available_cash")
        if cash is None:
            if self.effective_live_dry_run:
                cash = self.max_dollars_per_trade
            else:
                return 0, {"ok": False, "reason": "cash_balance_unavailable", "balance": balance}
        max_position_dollars = min(self.max_dollars_per_trade, max(1.0, float(cash) * self.max_single_position_pct))
        affordable_count = int(max_position_dollars // max(0.01, float(selected_price)))
        account_affordable = int(float(cash) // max(0.01, float(selected_price)))
        final_count = min(raw_count, affordable_count, account_affordable, self.max_live_order_size)
        return max(0, final_count), {
            "ok": final_count >= 1,
            "reason": "passed" if final_count >= 1 else "insufficient_cash_for_one_contract",
            "available_cash": cash,
            "max_position_dollars": round(max_position_dollars, 4),
            "raw_count": raw_count,
            "affordable_count": affordable_count,
            "account_affordable": account_affordable,
            "learning_multiplier": multiplier,
        }

    def _build_target(self, quote: Quote, side_score: Dict[str, Any]) -> Optional[TradeTarget]:
        side = normalize_side(side_score.get("side"))
        price = coerce_price(side_score.get("selected_price"))
        if side not in {"yes", "no"} or price is None:
            return None
        count, sizing = self._confidence_size(
            confidence=float(side_score.get("confidence") or 0.0),
            selected_price=price,
            family=quote.family,
            side=side,
            zone=side_score.get("price_zone") or price_zone(price),
        )
        if count < 1:
            self._emit_final_reject(
                ticker=quote.ticker,
                family=quote.family,
                side=side,
                selected_price=price,
                spread=side_score.get("spread"),
                liquidity=quote.liquidity,
                volume=quote.volume,
                reason=sizing.get("reason") or "capital_routing_rejected",
                subsystem="capital_routing",
                extra=sizing,
            )
            return None
        created_ts = time.time()
        target_id = f"{quote.ticker}:{side}:{int(created_ts * 1000)}"
        return TradeTarget(
            target_id=target_id,
            ticker=quote.ticker,
            family=quote.family,
            side=side,
            selected_price=price,
            count=count,
            confidence=float(side_score.get("confidence") or 0.0),
            ranking_score=float(side_score.get("score") or 0.0),
            spread=side_score.get("spread"),
            liquidity=quote.liquidity,
            volume=quote.volume,
            orderbook_snapshot_id=quote.orderbook_snapshot_id,
            price_zone=side_score.get("price_zone") or price_zone(price),
            source="ev_entry_scorer",
            created_ts=created_ts,
            signal_detected_ts=self._coerce_epoch_seconds(side_score.get("signal_detected_ts")),
            target_created_ts=created_ts,
            trace_id=target_id,
            entry_price=side_score.get("entry_price"),
            immediate_cashout=side_score.get("immediate_cashout"),
            immediate_pnl_per_contract=side_score.get("immediate_pnl_per_contract"),
            edge_after_spread=side_score.get("edge_after_spread"),
            first_cashout_quality=side_score.get("first_cashout_quality"),
            green_entry_score=side_score.get("green_entry_score") or side_score.get("score"),
            learning_multiplier=side_score.get("learning_multiplier"),
            concentration_penalty=side_score.get("concentration_penalty"),
            opened_green=side_score.get("opened_green"),
            fair_probability_for_side=side_score.get("fair_probability_for_side"),
            entry_mode=side_score.get("entry_mode"),
            maker_price=side_score.get("maker_price"),
            taker_entry_price=side_score.get("taker_entry_price"),
            taker_ev=side_score.get("taker_ev"),
            maker_ev=side_score.get("maker_ev"),
            ev_after_all_costs=side_score.get("ev_after_all_costs"),
            expected_spread_cost=side_score.get("expected_spread_cost"),
            expected_exit_cost=side_score.get("expected_exit_cost"),
            fees=side_score.get("fees"),
            slippage_buffer=side_score.get("slippage_buffer"),
            model_error_buffer=side_score.get("model_error_buffer"),
            adverse_selection_buffer=side_score.get("adverse_selection_buffer"),
            fill_probability=side_score.get("fill_probability"),
        )

    def discover_trade_targets(self) -> List[Tuple[TradeTarget, Quote]]:
        targets: List[Tuple[TradeTarget, Quote]] = []
        for market in self.fetch_live_sports_discovery_markets():
            ticker = market_ticker(market)
            family = market_family(market)
            disabled_reason = self._disabled_new_entry_reason(family=family, ticker=ticker, market=market)
            if disabled_reason:
                self._emit_final_reject(
                    ticker=ticker,
                    family=family,
                    reason=disabled_reason,
                    subsystem="sports_admission",
                    liquidity=market.get("liquidity"),
                    volume=market.get("volume"),
                )
                continue
            quote = self.hydrate_quote(market)
            if quote is None:
                continue
            if quote.synthetic or not quote.orderbook_real:
                self._emit_final_reject(
                    ticker=quote.ticker,
                    family=quote.family,
                    reason="fake_or_synthetic_book_rejected" if quote.synthetic else "real_orderbook_required",
                    subsystem="orderbook_validation",
                    liquidity=quote.liquidity,
                    volume=quote.volume,
                    extra={"orderbook_source": quote.orderbook_source},
                )
                continue
            side_results = []
            for side in ("yes", "no"):
                side_score = self._score_side(quote, side)
                if side_score is None:
                    self._emit_final_reject(
                        ticker=quote.ticker,
                        family=quote.family,
                        side=side,
                        selected_price=quote.price_for_side(side, "buy"),
                        spread=quote.spread_for_side(side),
                        liquidity=quote.liquidity,
                        volume=quote.volume,
                        reason="no_executable_selected_side_price",
                        subsystem="side_selection",
                    )
                    continue
                if side_score.get("blocked"):
                    self._emit_final_reject(
                        ticker=quote.ticker,
                        family=quote.family,
                        side=side,
                        selected_price=side_score.get("selected_price"),
                        spread=side_score.get("spread"),
                        liquidity=quote.liquidity,
                        volume=quote.volume,
                        reason=side_score.get("reason") or "side_blocked",
                        subsystem="side_selection",
                    )
                    continue
                side_results.append(side_score)
            for side_score in side_results:
                target = self._build_target(quote, side_score)
                if target is not None:
                    targets.append((target, quote))
        return targets

    def select_best_target(self, targets: List[Tuple[TradeTarget, Quote]]) -> Optional[Tuple[TradeTarget, Quote]]:
        if not targets:
            return None
        ranked = sorted(
            targets,
            key=lambda item: (
                float(item[0].ev_after_all_costs or -9.0),
                float(item[0].edge_after_spread or -9.0),
                float(item[0].fill_probability or 0.0),
                item[0].ranking_score,
                item[0].confidence,
                item[0].liquidity,
            ),
            reverse=True,
        )
        best = ranked[0]
        for target, quote in ranked[1:]:
            self._emit_final_reject(
                ticker=target.ticker,
                family=target.family,
                side=target.side,
                selected_price=target.selected_price,
                spread=target.spread,
                liquidity=target.liquidity,
                volume=target.volume,
                reason="lower_ev_rank_candidate",
                subsystem="ev_entry_ranker",
                extra={
                    "winning_ticker": best[0].ticker,
                    "winning_score": best[0].ranking_score,
                    "ev_after_all_costs": target.ev_after_all_costs,
                    "entry_mode": target.entry_mode,
                    "edge_after_spread": target.edge_after_spread,
                    "winning_ev_after_all_costs": best[0].ev_after_all_costs,
                    "winning_entry_mode": best[0].entry_mode,
                    "winning_edge_after_spread": best[0].edge_after_spread,
                },
            )
        return best

    def _payload_for_target(self, target: TradeTarget) -> Dict[str, Any]:
        payload = kalshi_execution.canonical_order_payload(
            ticker=target.ticker,
            side=target.side,
            action="buy",
            count=target.count,
            price=target.selected_price,
            reduce_only=False,
            target_id=target.target_id,
        )
        payload.update(
            {
                "family": target.family,
                "selected_side": target.side,
                "selected_price": target.selected_price,
                "confidence": target.confidence,
                "ranking_score": target.ranking_score,
                "spread": target.spread,
                "liquidity": target.liquidity,
                "volume": target.volume,
                "orderbook_snapshot_id": target.orderbook_snapshot_id,
                "price_zone": target.price_zone,
                "target_id": target.target_id,
                "trace_id": target.trace_id or target.target_id,
                "signal_detected_ts": target.signal_detected_ts,
                "target_created_ts": target.target_created_ts or target.created_ts,
                "created_ts": target.created_ts,
                "entry_price": target.entry_price if target.entry_price is not None else target.selected_price,
                "immediate_cashout": target.immediate_cashout,
                "immediate_pnl_per_contract": target.immediate_pnl_per_contract,
                "edge_after_spread": target.edge_after_spread,
                "first_cashout_quality": target.first_cashout_quality,
                "green_entry_score": target.green_entry_score,
                "fair_probability_for_side": target.fair_probability_for_side,
                "entry_mode": target.entry_mode,
                "maker_price": target.maker_price,
                "taker_entry_price": target.taker_entry_price,
                "taker_ev": target.taker_ev,
                "maker_ev": target.maker_ev,
                "ev_after_all_costs": target.ev_after_all_costs,
                "expected_spread_cost": target.expected_spread_cost,
                "expected_exit_cost": target.expected_exit_cost,
                "fees": target.fees,
                "slippage_buffer": target.slippage_buffer,
                "model_error_buffer": target.model_error_buffer,
                "adverse_selection_buffer": target.adverse_selection_buffer,
                "fill_probability": target.fill_probability,
                "learning_multiplier": target.learning_multiplier,
                "concentration_penalty": target.concentration_penalty,
                "opened_green": target.opened_green,
            }
        )
        return payload

    def confirm_entry(self, target: TradeTarget, current_orderbook: Quote | Dict[str, Any] | None = None, *, payload: Dict[str, Any] | None = None, source: str = "confirm_entry") -> Dict[str, Any]:
        return GreenEntryConfirm(self).confirm(target, current_orderbook, payload=payload, source=source)

    def confirm_exit(self, position: Dict[str, Any], decision: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {"allowed": False, "reason": "invalid_exit_payload"}
        if not valid_trade_price(payload.get("yes_price") if payload.get("side") == "yes" else payload.get("no_price")):
            return {"allowed": False, "reason": "invalid_exit_price", "payload": payload}
        risk = kalshi_risk.evaluate_exit_risk(self, position, payload)
        if not risk.get("ok"):
            return {"allowed": False, "reason": risk.get("reason"), "payload": payload, "risk": risk}
        payload["_aion_confirm_exit_passed"] = True
        return {"allowed": True, "reason": "passed", "payload": payload}

    def submit_target(self, target: TradeTarget, quote: Quote) -> Dict[str, Any]:
        payload = self._payload_for_target(target)
        confirmation = self.confirm_entry(target, quote, payload=payload, source="submit_target")
        if not confirmation.get("allowed"):
            self._emit_final_reject(
                ticker=target.ticker,
                family=target.family,
                side=target.side,
                selected_price=target.selected_price,
                spread=target.spread,
                liquidity=target.liquidity,
                volume=target.volume,
                reason=confirmation.get("reason") or "confirm_entry_rejected",
                subsystem="confirm_entry",
                extra={"risk": confirmation.get("risk")},
            )
            return {"submitted": False, "reason": confirmation.get("reason"), "payload": payload}
        payload = confirmation.get("payload") if isinstance(confirmation.get("payload"), dict) else payload
        self.log_json("AION-QUALIFIED", target.to_dict())
        self.log_json("AION-DISPATCH", {**target.to_dict(), "lane": "canonical_submit"})
        self.log_json("LIVE-EXECUTION", {**target.to_dict(), "dry_run": self.effective_live_dry_run})
        result = kalshi_execution.submit_canonical_order(
            self,
            payload,
            dry_run=self.effective_live_dry_run,
            meta={
                "family": target.family,
                "side": target.side,
                "selected_price": target.selected_price,
                "spread": target.spread,
                "liquidity": target.liquidity,
                "volume": target.volume,
                "target_id": target.target_id,
                "entry_price": payload.get("entry_price"),
                "immediate_cashout": payload.get("immediate_cashout"),
                "immediate_pnl_per_contract": payload.get("immediate_pnl_per_contract"),
                "edge_after_spread": payload.get("edge_after_spread"),
                "first_cashout_quality": payload.get("first_cashout_quality"),
                "ev_entry_confirm_passed": payload.get("_aion_ev_entry_confirm_passed"),
                "entry_mode": payload.get("entry_mode"),
                "fair_probability_for_side": payload.get("fair_probability_for_side"),
                "maker_price": payload.get("maker_price"),
                "taker_entry_price": payload.get("taker_entry_price"),
                "taker_ev": payload.get("taker_ev"),
                "maker_ev": payload.get("maker_ev"),
                "ev_after_all_costs": payload.get("ev_after_all_costs"),
                "expected_spread_cost": payload.get("expected_spread_cost"),
                "expected_exit_cost": payload.get("expected_exit_cost"),
                "fees": payload.get("fees"),
                "slippage_buffer": payload.get("slippage_buffer"),
                "model_error_buffer": payload.get("model_error_buffer"),
                "adverse_selection_buffer": payload.get("adverse_selection_buffer"),
                "selected_reason": payload.get("selected_reason"),
                "signal_age_seconds": payload.get("signal_age_seconds"),
            },
        )
        if result.get("submitted"):
            self.live_order_timestamps.append(time.time())
            self.state.setdefault("daily", {})["submitted_order_count"] = int(self.state.get("daily", {}).get("submitted_order_count") or 0) + 1
            self.record_pending_entry_order(target, payload, result)
            self.reconcile_live_positions(force=True)
            self.save_state()
        return result

    def record_live_position(self, target: TradeTarget, *, order_id: str | None = None, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        payload = payload if isinstance(payload, dict) else {}
        entry_price = coerce_price(payload.get("entry_price") or target.entry_price or target.selected_price) or target.selected_price
        first_cashout = coerce_price(payload.get("immediate_cashout") or target.immediate_cashout)
        immediate_pnl = coerce_float(payload.get("immediate_pnl_per_contract"), target.immediate_pnl_per_contract)
        executor = PositionExecutor(
            ticker=target.ticker,
            family=target.family,
            side=target.side,
            entry_price=entry_price,
            count=target.count,
            entry_ts=time.time(),
            tp=self.exit_config["take_profit"],
            sl=self.exit_config["stop_loss"],
            max_hold_seconds=self.exit_config["max_hold_seconds"],
            trailing_start=self.exit_config["trailing_start"],
            trailing_distance=self.exit_config["trailing_distance"],
        )
        row = {
            "ticker": target.ticker,
            "family": target.family,
            "side": target.side,
            "count": target.count,
            "entry_price": entry_price,
            "first_cashout": first_cashout,
            "immediate_cashout": first_cashout,
            "immediate_pnl_per_contract": immediate_pnl,
            "edge_after_spread": coerce_float(payload.get("edge_after_spread"), target.edge_after_spread),
            "fair_probability_for_side": coerce_float(payload.get("fair_probability_for_side"), target.fair_probability_for_side),
            "entry_mode": payload.get("entry_mode") or target.entry_mode,
            "maker_price": coerce_price(payload.get("maker_price") or target.maker_price),
            "taker_entry_price": coerce_price(payload.get("taker_entry_price") or target.taker_entry_price),
            "taker_ev": coerce_float(payload.get("taker_ev"), target.taker_ev),
            "maker_ev": coerce_float(payload.get("maker_ev"), target.maker_ev),
            "ev_after_all_costs": coerce_float(payload.get("ev_after_all_costs"), target.ev_after_all_costs),
            "expected_spread_cost": coerce_float(payload.get("expected_spread_cost"), target.expected_spread_cost),
            "expected_exit_cost": coerce_float(payload.get("expected_exit_cost"), target.expected_exit_cost),
            "fees": coerce_float(payload.get("fees"), target.fees),
            "slippage_buffer": coerce_float(payload.get("slippage_buffer"), target.slippage_buffer),
            "model_error_buffer": coerce_float(payload.get("model_error_buffer"), target.model_error_buffer),
            "adverse_selection_buffer": coerce_float(payload.get("adverse_selection_buffer"), target.adverse_selection_buffer),
            "fill_probability": coerce_float(payload.get("fill_probability"), target.fill_probability),
            "first_cashout_quality": coerce_float(payload.get("first_cashout_quality"), target.first_cashout_quality),
            "opened_green": bool(payload.get("opened_green") if payload.get("opened_green") is not None else (immediate_pnl is not None and immediate_pnl >= 0.0)),
            "spread_cost": coerce_float(payload.get("spread_cost"), None),
            "event_window_key": payload.get("event_window_key") or event_window_key(target.family, target.ticker),
            "market_type": "combo_cross" if payload.get("combo_family") or is_combo_or_cross_market(target.family, target.ticker) else "sports",
            "entry_ts": time.time(),
            "status": "open",
            "order_id": order_id,
            "target_id": target.target_id,
            "confidence": target.confidence,
            "ranking_score": target.ranking_score,
            "price_zone": target.price_zone,
            "position_executor": executor.to_dict(),
        }
        self.state.setdefault("positions", {})[target.ticker] = row
        self.live_positions = [
            position for position in self.live_positions
            if str(position.get("ticker") or "") != target.ticker
        ] + [row]
        return row

    def extract_position_summary(self, row: Dict[str, Any]) -> Dict[str, Any]:
        ticker = str(row.get("ticker") or row.get("market_ticker") or row.get("market_id") or "").strip()
        yes_count = abs(float(self.to_float(row.get("yes_count") or row.get("yes_contracts"), 0.0) or 0.0))
        no_count = abs(float(self.to_float(row.get("no_count") or row.get("no_contracts"), 0.0) or 0.0))
        signed_position = float(self.to_float(row.get("position") or row.get("position_count"), 0.0) or 0.0)
        side = None
        count = 0.0
        if yes_count > 0.0:
            side = "yes"
            count = yes_count
        elif no_count > 0.0:
            side = "no"
            count = no_count
        elif signed_position != 0.0:
            side = "yes" if signed_position > 0 else "no"
            count = abs(signed_position)
        else:
            side = normalize_side(row.get("side")) or normalize_side(row.get("outcome"))
            count = abs(float(self.to_float(row.get("count"), 0.0) or 0.0))
        entry_price = coerce_price(
            row.get("avg_price")
            or row.get("average_price")
            or row.get("entry_price")
            or row.get("price")
        )
        local = (self.state.get("positions") or {}).get(ticker, {}) if ticker else {}
        return {
            **(local if isinstance(local, dict) else {}),
            "ticker": ticker,
            "family": str((local or {}).get("family") or market_family({"ticker": ticker})).upper(),
            "side": side,
            "count": count,
            "entry_price": entry_price or (local or {}).get("entry_price"),
            "status": "open" if count > 0.0 else "closed",
            "raw": row,
            "updated_ts": time.time(),
        }

    def reconcile_live_positions(self, *, force: bool = False, min_interval_seconds: float = 15.0) -> Dict[str, Any]:
        now = time.time()
        if not force and now - self._last_reconcile_ts < min_interval_seconds:
            return {"ok": True, "reason": "recent_reconcile", "positions": self.live_positions}
        self._last_reconcile_ts = now
        result = self._live_request_json("GET", "/trade-api/v2/portfolio/positions", timeout=15)
        if not result.get("ok"):
            self.live_positions = self._local_open_positions()
            self.log_json(
                "LIVE-POSITIONS-FALLBACK",
                {
                    "reason": result.get("reason"),
                    "status_code": result.get("status_code"),
                    "open_positions": len(self.live_positions),
                    "source": "local_state",
                },
            )
            return {"ok": False, "reason": result.get("reason"), "positions": self.live_positions}
        body = result.get("body") if isinstance(result.get("body"), dict) else {}
        raw_positions = body.get("market_positions") or body.get("positions") or body.get("event_positions") or body.get("data") or []
        if isinstance(raw_positions, dict):
            raw_positions = list(raw_positions.values())
        normalized = [
            self.extract_position_summary(position)
            for position in raw_positions
            if isinstance(position, dict)
        ]
        open_by_ticker = {
            position["ticker"]: position
            for position in normalized
            if position.get("ticker") and str(position.get("status")) == "open"
        }
        previous_positions = self.state.setdefault("positions", {})
        for ticker, old_position in list(previous_positions.items()):
            if ticker not in open_by_ticker and isinstance(old_position, dict) and str(old_position.get("status") or "open") == "open":
                closed = dict(old_position)
                closed["status"] = "closed"
                closed["closed_ts"] = time.time()
                previous_positions[ticker] = closed
                self.record_exit_outcome(closed, {"reason": "position_reconcile_closed"})
        for ticker, position in open_by_ticker.items():
            was_open = isinstance(previous_positions.get(ticker), dict) and str(previous_positions.get(ticker, {}).get("status") or "") == "open"
            pending_id, pending = self._matching_pending_order(position)
            confirmed = self._confirmed_position_from_pending(position, pending) if pending else position
            previous_positions[ticker] = confirmed
            if pending_id:
                self._pending_orders().pop(pending_id, None)
                if str((pending or {}).get("entry_mode") or "").lower() == "maker":
                    self.log_json("AION-MAKER-ORDER-FILLED", confirmed)
                self.state.setdefault("daily", {})["live_trade_count"] = int(self.state.get("daily", {}).get("live_trade_count") or 0) + 1
            if not was_open:
                self.log_json("AION-POSITION-CONFIRMED", confirmed)
                self.log_json("POSITION-OPEN", confirmed)
        self.live_positions = [previous_positions[ticker] for ticker in open_by_ticker if isinstance(previous_positions.get(ticker), dict)]
        self.save_state()
        self.log_json(
            "LIVE-POSITIONS-API",
            {
                "ok": True,
                "open_positions": len(self.live_positions),
                "status_code": result.get("status_code"),
            },
        )
        return {"ok": True, "positions": self.live_positions, "source": "api"}

    def record_exit_outcome(self, position: Dict[str, Any], decision: Dict[str, Any], result: Dict[str, Any] | None = None) -> Dict[str, Any]:
        record = kalshi_exits.outcome_record(position, decision, result)
        learning = self.state.setdefault("learning", {})
        outcomes = learning.setdefault("exit_outcomes", [])
        outcomes.append(record)
        del outcomes[:-500]
        key = self._learning_bucket_key(record.get("family"), record.get("side"), record.get("price_zone"))
        bucket = learning.setdefault("family_side_zone", {}).setdefault(
            key,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0.0,
                "mfe": 0.0,
                "mae": 0.0,
                "mfe_total": 0.0,
                "mae_total": 0.0,
                "entry_samples": 0,
                "green_entries": 0,
                "red_entries": 0,
                "flat_entries": 0,
                "immediate_pnl": 0.0,
                "first_cashout": 0.0,
                "mfe_before_mae_samples": 0,
                "mfe_before_mae_positive": 0,
            },
        )
        pnl = coerce_float(record.get("pnl"), 0.0) or 0.0
        bucket["trades"] = int(bucket.get("trades") or 0) + 1
        bucket["wins"] = int(bucket.get("wins") or 0) + (1 if pnl > 0 else 0)
        bucket["losses"] = int(bucket.get("losses") or 0) + (1 if pnl < 0 else 0)
        bucket["pnl"] = round(float(bucket.get("pnl") or 0.0) + pnl, 4)
        mfe = float(coerce_float(record.get("mfe"), 0.0) or 0.0)
        mae = float(coerce_float(record.get("mae"), 0.0) or 0.0)
        bucket["mfe"] = round(max(float(bucket.get("mfe") or 0.0), mfe), 4)
        bucket["mae"] = round(min(float(bucket.get("mae") or 0.0), mae), 4)
        bucket["mfe_total"] = round(float(bucket.get("mfe_total") or 0.0) + mfe, 4)
        bucket["mae_total"] = round(float(bucket.get("mae_total") or 0.0) + mae, 4)
        immediate_pnl = coerce_float(record.get("immediate_pnl_per_contract"), None)
        first_cashout = coerce_price(record.get("first_cashout") or record.get("immediate_cashout"))
        opened_green = record.get("opened_green")
        if opened_green is None and immediate_pnl is not None:
            opened_green = immediate_pnl >= 0.0
        if immediate_pnl is not None or opened_green is not None:
            bucket["entry_samples"] = int(bucket.get("entry_samples") or 0) + 1
            bucket["immediate_pnl"] = round(float(bucket.get("immediate_pnl") or 0.0) + float(immediate_pnl or 0.0), 4)
            if first_cashout is not None:
                bucket["first_cashout"] = round(float(bucket.get("first_cashout") or 0.0) + float(first_cashout), 4)
            if immediate_pnl == 0.0:
                bucket["flat_entries"] = int(bucket.get("flat_entries") or 0) + 1
            elif bool(opened_green):
                bucket["green_entries"] = int(bucket.get("green_entries") or 0) + 1
            else:
                bucket["red_entries"] = int(bucket.get("red_entries") or 0) + 1
        mfe_before_mae = record.get("mfe_before_mae")
        if mfe_before_mae is not None:
            bucket["mfe_before_mae_samples"] = int(bucket.get("mfe_before_mae_samples") or 0) + 1
            bucket["mfe_before_mae_positive"] = int(bucket.get("mfe_before_mae_positive") or 0) + (1 if bool(mfe_before_mae) else 0)
        daily = self.state.setdefault("daily", {})
        daily["realized_pnl"] = round(float(daily.get("realized_pnl") or 0.0) + pnl, 4)
        self.log_json("AION-EXIT-OUTCOME-LEARNING", {"record": record, "bucket": key, "bucket_state": bucket})
        self.save_state()
        return record

    def manage_live_positions(self) -> Dict[str, Any]:
        self.reconcile_live_positions(force=True)
        submitted = 0
        decisions = []
        for position in list(self.live_positions):
            ticker = str(position.get("ticker") or "").strip()
            if not ticker:
                continue
            try:
                market = self.get_live_market_detail(ticker)
                orderbook = self.get_live_market_orderbook(ticker)
                quote = build_quote(market, orderbook)
            except Exception as exc:
                self.log_json("LIVE-EXIT", {"ticker": ticker, "submitted": False, "reason": f"quote_error:{exc.__class__.__name__}"})
                continue
            decision = kalshi_exits.evaluate_exit(position, quote, self.exit_config)
            position["position_executor"] = decision.get("position_executor") or position.get("position_executor")
            self.state.setdefault("positions", {}).setdefault(ticker, {}).update({"position_executor": position["position_executor"]})
            self.log_json("AION-POSITION-INTELLIGENCE", decision)
            if decision.get("action") != "exit":
                decisions.append(decision)
                continue
            payload = kalshi_exits.build_exit_payload(position, decision)
            confirmation = self.confirm_exit(position, decision, payload or {})
            if not confirmation.get("allowed"):
                self.log_json("LIVE-EXIT", {"ticker": ticker, "submitted": False, "reason": confirmation.get("reason")})
                decisions.append({**decision, "submitted": False, "submit_reason": confirmation.get("reason")})
                continue
            result = kalshi_execution.submit_canonical_order(
                self,
                confirmation.get("payload"),
                dry_run=self.effective_live_dry_run,
                meta={
                    "family": decision.get("family"),
                    "side": decision.get("side"),
                    "selected_price": decision.get("exit_price"),
                    "spread": quote.spread,
                    "liquidity": quote.liquidity,
                    "volume": quote.volume,
                    "exit_reason": decision.get("reason"),
                },
            )
            if result.get("submitted"):
                submitted += 1
                self.live_order_timestamps.append(time.time())
                position["status"] = "exit_pending"
                position["exit_reason"] = decision.get("reason")
                position["exit_price"] = decision.get("exit_price")
                self.state.setdefault("positions", {})[ticker] = position
                self.record_exit_outcome(position, decision, result)
                self.log_json("EXIT_SUBMITTED", {"ticker": ticker, "side": decision.get("side"), "price": decision.get("exit_price"), "reason": decision.get("reason"), "order_id": result.get("order_id")})
            self.log_json("LIVE-EXIT", {"ticker": ticker, "submitted": bool(result.get("submitted")), "reason": result.get("reason"), "pnl": decision.get("unrealized_pnl")})
            decisions.append({**decision, "submitted": bool(result.get("submitted")), "submit_reason": result.get("reason")})
        self.save_state()
        return {"ok": True, "exit_orders_submitted": submitted, "decisions": decisions}

    def cancel_live_order(self, order_id: str) -> Dict[str, Any]:
        order_id = str(order_id or "").strip()
        if not order_id:
            return {"ok": False, "reason": "missing_order_id"}
        result = self._live_request_json("DELETE", f"/trade-api/v2/portfolio/orders/{order_id}", timeout=15)
        self.log_json("LIVE-CANCEL", {"ok": bool(result.get("ok")), "reason": result.get("reason"), "status_code": result.get("status_code"), "order_id": order_id})
        return result

    def preview_live_ticker_selection(self) -> Dict[str, Any]:
        targets = self.discover_trade_targets()
        selected = self.select_best_target(targets)
        payload = {
            "candidate_count": len(targets),
            "selected": selected[0].to_dict() if selected else None,
        }
        self.log_json("AION-PREVIEW-LIVE-TICKER", payload)
        return payload

    def run_once(self) -> Dict[str, Any]:
        self.cycle_count += 1
        self.log_json("AION-CYCLE", {"cycle": self.cycle_count, "stage": "scan"})
        self.reconcile_live_positions(min_interval_seconds=20.0)
        pending_lifecycle = self.manage_pending_maker_orders()
        targets = self.discover_trade_targets()
        selected = self.select_best_target(targets)
        submit_result = {"submitted": False, "reason": "no_candidate"}
        if selected is None:
            self.log_json("AION-LIVE-CANDIDATE-FUNNEL", {"cycle": self.cycle_count, "candidate_count": 0, "reason": "no_candidate"})
        else:
            target, quote = selected
            pending_lifecycle = self.manage_pending_maker_orders(selected_target=target)
            self.log_json("AION-LIVE-CANDIDATE-FUNNEL", {"cycle": self.cycle_count, "candidate_count": len(targets), "selected": target.to_dict()})
            submit_result = self.submit_target(target, quote)
        exit_result = self.manage_live_positions()
        self.log_json(
            "AION-CYCLE-SUMMARY",
            {
                "cycle": self.cycle_count,
                "candidate_count": len(targets),
                "submitted": bool(submit_result.get("submitted")),
                "submit_reason": submit_result.get("reason"),
                "open_positions": len(self.live_positions),
                "pending_orders": len(self.active_pending_orders()),
                "pending_cancelled": pending_lifecycle.get("cancelled"),
                "exit_orders_submitted": exit_result.get("exit_orders_submitted"),
            },
        )
        return {"submit": submit_result, "exits": exit_result}

    def submit_live_order(self, q: Any, size: int | None = None, lane: str | None = None, payload_override: Dict[str, Any] | None = None) -> Dict[str, Any]:
        payload = dict(payload_override or {})
        ticker = str(payload.get("ticker") or getattr(q, "ticker", "") or "").strip()
        if not ticker:
            return {"submitted": False, "reason": "missing_ticker"}
        try:
            market = self.get_live_market_detail(ticker)
            orderbook = self.get_live_market_orderbook(ticker)
            quote = build_quote(market, orderbook)
        except Exception as exc:
            return {"submitted": False, "reason": f"quote_error:{exc.__class__.__name__}"}
        side = normalize_side(payload.get("side") or payload.get("selected_side") or getattr(q, "selected_side", None) or getattr(q, "side", None))
        price = coerce_price(payload.get("selected_price") or payload.get("price") or getattr(q, "selected_price", None))
        if side not in {"yes", "no"}:
            side_scores = [score for score in (self._score_side(quote, "yes"), self._score_side(quote, "no")) if score and not score.get("blocked")]
            if not side_scores:
                return {"submitted": False, "reason": "ev_entry_rejected"}
            side_score = max(
                side_scores,
                key=lambda item: (
                    float(item.get("ev_after_all_costs") or -9.0),
                    float(item.get("edge_after_spread") or -9.0),
                    float(item.get("fill_probability") or 0.0),
                    float(item.get("score") or -1),
                ),
            )
        else:
            side_score = self.green_entry_scorer.score_side(quote, side, source="submit_live_order_wrapper")
        if not side_score or side_score.get("blocked"):
            return {"submitted": False, "reason": (side_score or {}).get("reason") or "no_executable_selected_side_price"}
        target = self._build_target(quote, side_score)
        if target is None:
            return {"submitted": False, "reason": "target_build_failed"}
        if size:
            target = TradeTarget(**{**target.to_dict(), "count": min(int(size), self.max_live_order_size)})
        self.log_json("AION-SUBMIT-BYPASS-REMOVED", {"ticker": ticker, "source": "submit_live_order_wrapper", "lane": lane, "decision": "canonical_submit_only"})
        return self.submit_target(target, quote)

    def place_live_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload if isinstance(payload, dict) else {}
        action = str(payload.get("action") or "buy").strip().lower()
        if action in {"sell", "exit", "close"} or payload.get("reduce_only"):
            ticker = str(payload.get("ticker") or "").strip()
            position = next((item for item in self.live_positions if str(item.get("ticker") or "") == ticker), {})
            confirmation = self.confirm_exit(position, {"reason": payload.get("exit_reason") or "direct_reduce_only_exit"}, payload)
            if not confirmation.get("allowed"):
                return {"submitted": False, "reason": confirmation.get("reason"), "payload": payload}
            return kalshi_execution.submit_canonical_order(self, confirmation.get("payload"), dry_run=self.effective_live_dry_run)
        self.log_json("AION-SUBMIT-BYPASS-REMOVED", {"ticker": payload.get("ticker"), "source": "place_live_order_wrapper", "decision": "canonical_submit_only"})
        proxy = type("PayloadProxy", (), payload)
        return self.submit_live_order(proxy, size=payload.get("count"), payload_override=payload)

    def run_live_preflight(self) -> Dict[str, Any]:
        auth = self.run_live_auth_get_smoke_test()
        preview = self.preview_live_ticker_selection()
        ok = bool((self.effective_live_dry_run or auth.get("ok")) and preview.get("candidate_count", 0) >= 0)
        payload = {
            "ready": ok,
            "effective_dry_run": self.effective_live_dry_run,
            "auth_ok": bool(auth.get("ok")),
            "candidate_count": preview.get("candidate_count"),
            "live_mode": self.live_mode,
            "live_armed": self.live_armed,
            "live_env_confirmed": self.live_env_confirmed,
        }
        self.log_json("AION-LIVE-PREFLIGHT", payload)
        return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--arm-live", action="store_true")
    parser.add_argument("--live-smoke-test", action="store_true")
    parser.add_argument("--live-auth-get-smoke-test", action="store_true")
    parser.add_argument("--live-preflight", action="store_true")
    parser.add_argument("--monitor-live-positions-once", action="store_true")
    parser.add_argument("--manage-live-positions", action="store_true")
    parser.add_argument("--preview-live-ticker", action="store_true")
    parser.add_argument("--cancel-live-order-id")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    live_env_confirmed = env_bool("KALSHI_ALLOW_LIVE", False) or env_bool("LIVE_ENV_CONFIRMED", False)
    bot = KalshiSniperBot(live_mode=args.live, live_armed=bool(args.live and args.arm_live), live_env_confirmed=live_env_confirmed)

    def _stop(_signum: int, _frame: Any) -> None:
        bot.stop_requested = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    auth_config = bot.get_live_auth_config()
    bot.log_json(
        "LIVE-CONFIG",
        {
            "live_mode": bot.live_mode,
            "live_armed": bot.live_armed,
            "live_env_confirmed": bot.live_env_confirmed,
            "configured_dry_run": bot.configured_dry_run,
            "effective_dry_run": bot.effective_live_dry_run,
            "api_key_source": auth_config.get("api_key_source"),
            "private_key_path": auth_config.get("private_key_path"),
            "private_key_exists": auth_config.get("private_key_exists"),
            "base_url": bot.get_live_base_url(),
            "max_live_order_size": bot.max_live_order_size,
            "max_open_positions": bot.max_open_positions,
            "sports_only": True,
            "tennis_shutdown": True,
            "kxmlbks_shutdown": True,
        },
    )

    if args.live_auth_get_smoke_test:
        bot.run_live_auth_get_smoke_test()
        return
    if args.live_smoke_test:
        bot.run_live_smoke_test()
        return
    if args.live_preflight:
        bot.run_live_preflight()
        return
    if args.monitor_live_positions_once:
        bot.reconcile_live_positions(force=True)
        bot.manage_live_positions()
        return
    if args.manage_live_positions:
        while not bot.stop_requested:
            bot.manage_live_positions()
            time.sleep(bot.loop_sleep_seconds)
        return
    if args.preview_live_ticker:
        bot.preview_live_ticker_selection()
        return
    if args.cancel_live_order_id:
        bot.cancel_live_order(args.cancel_live_order_id)
        return

    if not args.live:
        bot.log_json("SAFEGUARD", {"reason": "running_without_live_flag", "effective_dry_run": True})
    elif args.live and args.arm_live and bot.live_env_confirmed and not bot.effective_live_dry_run:
        bot.log_json("SAFEGUARD", {"reason": "live_trading_armed", "effective_dry_run": False})
    else:
        bot.log_json("SAFEGUARD", {"reason": "live_trading_not_fully_armed", "effective_dry_run": bot.effective_live_dry_run})

    while not bot.stop_requested:
        try:
            bot.run_once()
            if args.once:
                break
            time.sleep(bot.loop_sleep_seconds)
        except Exception as exc:
            bot.log_json("MAIN-LOOP-ERROR", {"reason": exc.__class__.__name__, "message": str(exc)[:300]})
            time.sleep(5.0)


if __name__ == "__main__":
    main()

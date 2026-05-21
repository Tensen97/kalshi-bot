"""Configurable entry and exit guard policies."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .utils import env_float, env_int, safe_float, should_emit_service_log


@dataclass
class GuardThresholds:
    min_entry_liquidity: float = 50.0
    min_entry_volume: float = 25.0
    max_entry_spread: float = 0.06
    max_yes_ask_to_enter: float = 0.93
    min_price: float = 0.10
    max_price: float = 0.90
    max_position_cost: float = 0.85
    max_total_exposure: int = 2
    max_daily_loss: float = 0.10
    max_consecutive_losses: int = 3
    take_profit_min: float = 0.03
    take_profit_max: float = 0.05
    stop_loss_min: float = 0.02
    stop_loss_max: float = 0.03
    max_hold_seconds: float = 600.0
    min_liquidity_exit: float = 20.0
    profile: str = "balanced"

    @classmethod
    def from_legacy_globals(cls, legacy: dict[str, Any]) -> "GuardThresholds":
        profile = str((legacy.get("KALSHI_ENTRY_GUARD_PROFILE") or "") or "").strip().lower()
        if not profile:
            import os

            profile = (os.environ.get("KALSHI_ENTRY_GUARD_PROFILE", "balanced") or "balanced").strip().lower()
        thresholds = cls(
            min_entry_liquidity=env_float("KALSHI_MIN_ENTRY_LIQUIDITY", float(legacy.get("MIN_ENTRY_LIQUIDITY") or 50.0)),
            min_entry_volume=env_float("KALSHI_MIN_ENTRY_VOLUME", float(legacy.get("MIN_ENTRY_VOLUME") or 25.0)),
            max_entry_spread=env_float("KALSHI_MAX_ENTRY_SPREAD", float(legacy.get("MAX_ENTRY_SPREAD") or 0.06)),
            max_yes_ask_to_enter=env_float("KALSHI_MAX_YES_ASK_TO_ENTER", float(legacy.get("MAX_YES_ASK_TO_ENTER") or 0.93)),
            min_price=env_float("KALSHI_MIN_ENTRY_PRICE", float(legacy.get("MIN_ENTRY_PRICE") or 0.10)),
            max_price=env_float("KALSHI_MAX_ENTRY_PRICE", float(legacy.get("MAX_ENTRY_PRICE") or 0.90)),
            max_position_cost=env_float("KALSHI_MAX_POSITION_COST", float(legacy.get("MAX_POSITION_COST") or 0.85)),
            max_total_exposure=env_int("KALSHI_MAX_TOTAL_EXPOSURE", int(legacy.get("MAX_TOTAL_EXPOSURE") or 2)),
            max_daily_loss=env_float("KALSHI_MAX_DAILY_LOSS", float(legacy.get("MAX_DAILY_LOSS") or 0.10)),
            max_consecutive_losses=env_int("KALSHI_MAX_CONSECUTIVE_LOSSES", int(legacy.get("MAX_CONSECUTIVE_LOSSES") or 3)),
            take_profit_min=env_float("KALSHI_TAKE_PROFIT_MIN", float(legacy.get("TAKE_PROFIT_MIN") or 0.03)),
            take_profit_max=env_float("KALSHI_TAKE_PROFIT_MAX", float(legacy.get("TAKE_PROFIT_MAX") or 0.05)),
            stop_loss_min=env_float("KALSHI_STOP_LOSS_MIN", float(legacy.get("STOP_LOSS_MIN") or 0.02)),
            stop_loss_max=env_float("KALSHI_STOP_LOSS_MAX", float(legacy.get("STOP_LOSS_MAX") or 0.03)),
            max_hold_seconds=env_float("KALSHI_MAX_HOLD_SECONDS", float(legacy.get("MAX_HOLD_SECONDS") or 600.0)),
            min_liquidity_exit=env_float("KALSHI_MIN_LIQUIDITY_EXIT", float(legacy.get("MIN_LIQUIDITY_EXIT") or 20.0)),
            profile=profile if profile in {"safe", "balanced", "opportunistic"} else "balanced",
        )
        return thresholds.adjusted_for_profile()

    def adjusted_for_profile(self) -> "GuardThresholds":
        if self.profile == "safe":
            return self
        if self.profile == "opportunistic":
            return GuardThresholds(
                min_entry_liquidity=max(1.0, self.min_entry_liquidity * 0.65),
                min_entry_volume=max(1.0, self.min_entry_volume * 0.60),
                max_entry_spread=min(0.20, self.max_entry_spread * 1.35),
                max_yes_ask_to_enter=min(0.97, self.max_yes_ask_to_enter + 0.02),
                min_price=self.min_price,
                max_price=self.max_price,
                max_position_cost=min(0.95, self.max_position_cost + 0.03),
                max_total_exposure=self.max_total_exposure,
                max_daily_loss=self.max_daily_loss,
                max_consecutive_losses=self.max_consecutive_losses,
                take_profit_min=self.take_profit_min,
                take_profit_max=self.take_profit_max,
                stop_loss_min=self.stop_loss_min,
                stop_loss_max=self.stop_loss_max,
                max_hold_seconds=self.max_hold_seconds,
                min_liquidity_exit=self.min_liquidity_exit,
                profile=self.profile,
            )
        return self


class EntryExitGuardService:
    def __init__(self, thresholds: GuardThresholds, logger=None):
        self.thresholds = thresholds
        self.logger = logger

    @classmethod
    def from_legacy_globals(cls, legacy: dict[str, Any], logger=None) -> "EntryExitGuardService":
        return cls(GuardThresholds.from_legacy_globals(legacy), logger=logger)

    def _log(self, prefix: str, **fields: Any) -> None:
        if not should_emit_service_log(prefix):
            return
        if callable(self.logger):
            self.logger(prefix, **fields)

    def passes_entry_filters(self, q: Any) -> tuple[bool, str | None]:
        t = self.thresholds
        liquidity = safe_float(getattr(q, "liquidity", 0.0), 0.0) or 0.0
        if liquidity < t.min_entry_liquidity:
            return False, "low_liquidity"
        volume = safe_float(getattr(q, "volume", 0.0), 0.0) or 0.0
        if volume < t.min_entry_volume:
            return False, "low_volume"
        bid = safe_float(getattr(q, "yes_bid", 0.0), 0.0) or 0.0
        ask = safe_float(getattr(q, "yes_ask", 0.0), 0.0) or 0.0
        if bid <= 0 or ask <= 0:
            return False, "missing_bid_ask"
        spread = ask - bid
        if spread < 0:
            return False, "crossed_or_invalid_spread"
        if spread > t.max_entry_spread:
            return False, "wide_spread"
        if ask >= t.max_yes_ask_to_enter:
            return False, "yes_too_expensive"
        return True, None

    def should_enter_yes(self, q: Any) -> bool:
        ok, _reason = self.passes_entry_filters(q)
        if not ok:
            return False
        t = self.thresholds
        bid = safe_float(getattr(q, "yes_bid", 0.0), 0.0) or 0.0
        ask = safe_float(getattr(q, "yes_ask", 0.0), 0.0) or 0.0
        last_price = safe_float(getattr(q, "last_price", 0.0), 0.0) or 0.0
        if not (t.min_price <= bid <= t.max_price):
            return False
        if last_price <= 0:
            return False
        mid = (bid + ask) / 2.0
        stretch_limit = 0.14 if t.profile == "opportunistic" else 0.12
        if last_price > ask:
            return False
        return abs(last_price - mid) <= stretch_limit

    def can_open_new_position(self, bot: Any, q: Any) -> tuple[bool, str | None]:
        t = self.thresholds
        if bool(getattr(bot, "trading_halted", False)):
            return False, "trading_halted"
        if len(getattr(bot, "positions", []) or []) >= t.max_total_exposure:
            return False, "max_exposure"
        ask = safe_float(getattr(q, "yes_ask", 0.0), 0.0) or 0.0
        if ask <= 0:
            return False, "missing_yes_ask"
        if ask > t.max_position_cost:
            return False, "position_too_expensive"
        if safe_float(getattr(bot, "realized_pnl", 0.0), 0.0) <= -abs(t.max_daily_loss):
            setattr(bot, "trading_halted", True)
            return False, "daily_loss_limit"
        if int(getattr(bot, "consecutive_losses", 0) or 0) >= t.max_consecutive_losses:
            setattr(bot, "trading_halted", True)
            return False, "loss_streak_limit"
        return True, None

    def get_position_pnl(self, position: dict[str, Any], market: Any) -> float | None:
        side = str(position.get("side", "YES") or "YES").upper()
        entry_price = safe_float(position.get("entry"), 0.0) or 0.0
        if side == "YES":
            exit_price = safe_float(getattr(market, "yes_bid", 0.0), 0.0) or safe_float(getattr(market, "last_price", 0.0), 0.0) or 0.0
            if exit_price <= 0:
                return None
            return exit_price - entry_price
        exit_price = safe_float(getattr(market, "no_bid", 0.0), 0.0) or safe_float(getattr(market, "last_price", 0.0), 0.0) or 0.0
        if exit_price <= 0:
            return None
        return entry_price - exit_price

    def should_exit_position(self, position: dict[str, Any], market: Any, now: float | None = None) -> tuple[bool, str | None, float]:
        now = time.time() if now is None else now
        pnl = self.get_position_pnl(position, market)
        if pnl is None:
            return False, None, 0.0
        t = self.thresholds
        opened_at = position.get("opened_at", now)
        hold_seconds = now - opened_at
        tp_min = t.take_profit_min
        sl_min = t.stop_loss_min
        if hold_seconds > 120:
            tp_min *= 0.8
            sl_min *= 0.8
        if pnl >= t.take_profit_max:
            return True, "take_profit", pnl
        if pnl >= tp_min and hold_seconds > 30:
            return True, "take_profit", pnl
        if pnl <= -t.stop_loss_max:
            return True, "stop_loss", pnl
        if pnl <= -sl_min:
            if hold_seconds > 20:
                return True, "stop_loss", pnl
            liquidity = safe_float(getattr(market, "liquidity", 0.0), 0.0) or 0.0
            if liquidity < t.min_liquidity_exit:
                return True, "stop_loss", pnl
        if hold_seconds >= t.max_hold_seconds * 0.75 and pnl <= 0:
            return True, "max_hold", pnl
        if hold_seconds >= t.max_hold_seconds:
            return True, "max_hold", pnl
        return False, None, pnl

    def summary(self) -> dict[str, Any]:
        t = self.thresholds
        return {
            "profile": t.profile,
            "min_entry_liquidity": t.min_entry_liquidity,
            "min_entry_volume": t.min_entry_volume,
            "max_entry_spread": t.max_entry_spread,
            "max_yes_ask_to_enter": t.max_yes_ask_to_enter,
            "max_total_exposure": t.max_total_exposure,
            "max_daily_loss": t.max_daily_loss,
            "max_consecutive_losses": t.max_consecutive_losses,
        }

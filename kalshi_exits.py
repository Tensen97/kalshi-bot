"""Minimal live position management for AION."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from kalshi_execution import canonical_order_payload
from kalshi_orderbook import Quote, coerce_price, normalize_side, price_zone


@dataclass
class PositionExecutor:
    ticker: str
    family: str
    side: str
    entry_price: float
    count: int
    entry_ts: float
    tp: float
    sl: float
    max_hold_seconds: float
    trailing_start: float
    trailing_distance: float
    current_mfe: float = 0.0
    current_mae: float = 0.0
    current_cashout: Optional[float] = None
    exit_reason: Optional[str] = None
    executor_state: str = "open"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def position_executor_from_position(position: Dict[str, Any], config: Dict[str, Any]) -> PositionExecutor:
    position = position if isinstance(position, dict) else {}
    existing = position.get("position_executor") if isinstance(position.get("position_executor"), dict) else {}
    entry_price = coerce_price(position.get("entry_price") or existing.get("entry_price")) or 0.0
    count = int(abs(float(position.get("count") or existing.get("count") or 1)))
    return PositionExecutor(
        ticker=str(position.get("ticker") or existing.get("ticker") or "").strip(),
        family=str(position.get("family") or existing.get("family") or "UNKNOWN").strip().upper(),
        side=normalize_side(position.get("side") or existing.get("side")) or "yes",
        entry_price=entry_price,
        count=max(1, count),
        entry_ts=float(position.get("entry_ts") or existing.get("entry_ts") or time.time()),
        tp=float(existing.get("tp") or config.get("take_profit") or 0.04),
        sl=float(existing.get("sl") or config.get("stop_loss") or 0.04),
        max_hold_seconds=float(existing.get("max_hold_seconds") or config.get("max_hold_seconds") or 900.0),
        trailing_start=float(existing.get("trailing_start") or config.get("trailing_start") or 0.03),
        trailing_distance=float(existing.get("trailing_distance") or config.get("trailing_distance") or 0.015),
        current_mfe=float(existing.get("current_mfe") or position.get("current_mfe") or 0.0),
        current_mae=float(existing.get("current_mae") or position.get("current_mae") or 0.0),
        current_cashout=coerce_price(existing.get("current_cashout") or position.get("current_cashout")),
        exit_reason=existing.get("exit_reason") or position.get("exit_reason"),
        executor_state=str(existing.get("executor_state") or position.get("executor_state") or "open"),
    )


def exit_price_for_position(position: Dict[str, Any], quote: Quote) -> Optional[float]:
    side = normalize_side(position.get("side"))
    if side == "yes":
        return quote.yes_bid
    if side == "no":
        return quote.no_bid
    return None


def evaluate_exit(position: Dict[str, Any], quote: Quote, config: Dict[str, Any]) -> Dict[str, Any]:
    executor = position_executor_from_position(position, config)
    side = normalize_side(executor.side)
    current_price = exit_price_for_position({"side": side}, quote)
    if current_price is None or not (0.0 < current_price < 1.0):
        return {
            "action": "hold",
            "reason": "missing_exit_bid",
            "ticker": executor.ticker,
            "family": executor.family,
            "side": side,
            "position_executor": executor.to_dict(),
        }
    pnl_per_contract = round(float(current_price) - float(executor.entry_price), 4)
    pnl = round(pnl_per_contract * executor.count, 4)
    executor.current_cashout = current_price
    executor.current_mfe = max(float(executor.current_mfe or 0.0), pnl_per_contract)
    executor.current_mae = min(float(executor.current_mae or 0.0), pnl_per_contract)
    hold_seconds = max(0.0, time.time() - float(executor.entry_ts or time.time()))

    action = "hold"
    reason = "hold"
    if pnl_per_contract >= executor.tp:
        action = "exit"
        reason = "profit_target"
    elif executor.current_mfe >= executor.trailing_start and pnl_per_contract <= executor.current_mfe - executor.trailing_distance:
        action = "exit"
        reason = "trailing_lock"
    elif pnl_per_contract <= -abs(executor.sl):
        action = "exit"
        reason = "stop_loss"
    elif executor.max_hold_seconds > 0 and hold_seconds >= executor.max_hold_seconds and pnl_per_contract >= 0.0:
        action = "exit"
        reason = "max_hold_profit_or_flat"

    if action == "exit":
        executor.exit_reason = reason
        executor.executor_state = "exit_ready"

    return {
        "action": action,
        "reason": reason,
        "ticker": executor.ticker,
        "family": executor.family,
        "side": side,
        "entry_price": executor.entry_price,
        "exit_price": current_price,
        "count": executor.count,
        "hold_seconds": round(hold_seconds, 3),
        "pnl_per_contract": pnl_per_contract,
        "unrealized_pnl": pnl,
        "mfe": round(executor.current_mfe, 4),
        "mae": round(executor.current_mae, 4),
        "price_zone": price_zone(executor.entry_price),
        "position_executor": executor.to_dict(),
    }


def build_exit_payload(position: Dict[str, Any], decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ticker = str(decision.get("ticker") or position.get("ticker") or "").strip()
    side = normalize_side(decision.get("side") or position.get("side"))
    price = coerce_price(decision.get("exit_price"))
    count = int(abs(float(decision.get("count") or position.get("count") or 0)))
    if not ticker or side not in {"yes", "no"} or price is None or count < 1:
        return None
    return canonical_order_payload(
        ticker=ticker,
        side=side,
        action="sell",
        count=count,
        price=price,
        reduce_only=True,
        target_id=str(position.get("target_id") or decision.get("target_id") or "") or None,
    )


def outcome_record(position: Dict[str, Any], decision: Dict[str, Any], result: Dict[str, Any] | None = None) -> Dict[str, Any]:
    position = position if isinstance(position, dict) else {}
    decision = decision if isinstance(decision, dict) else {}
    result = result if isinstance(result, dict) else {}
    entry_price = coerce_price(decision.get("entry_price") or position.get("entry_price"))
    exit_price = coerce_price(decision.get("exit_price") or position.get("exit_price"))
    count = int(abs(float(decision.get("count") or position.get("count") or 1)))
    pnl = None
    if entry_price is not None and exit_price is not None:
        pnl = round((exit_price - entry_price) * max(1, count), 4)
    return {
        "ticker": decision.get("ticker") or position.get("ticker"),
        "family": str(decision.get("family") or position.get("family") or "UNKNOWN").upper(),
        "side": normalize_side(decision.get("side") or position.get("side")),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "price_zone": price_zone(entry_price),
        "count": max(1, count),
        "pnl": pnl,
        "exit_reason": decision.get("reason") or position.get("exit_reason"),
        "mfe": decision.get("mfe") if decision.get("mfe") is not None else position.get("current_mfe"),
        "mae": decision.get("mae") if decision.get("mae") is not None else position.get("current_mae"),
        "order_id": result.get("order_id"),
        "ts": time.time(),
    }

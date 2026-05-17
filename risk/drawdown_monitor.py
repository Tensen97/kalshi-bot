from __future__ import annotations

from typing import Any, Dict


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def monitor_drawdown(capital_state: Dict[str, Any] | None, memory_summary: Dict[str, Any] | None) -> Dict[str, Any]:
    capital_state = capital_state if isinstance(capital_state, dict) else {}
    memory_summary = memory_summary if isinstance(memory_summary, dict) else {}
    daily_pnl = _safe_float(capital_state.get("daily_pnl"), 0.0)
    rolling_pnl = _safe_float(capital_state.get("rolling_pnl"), daily_pnl)
    drawdown_pct = _safe_float(capital_state.get("drawdown_pct"), 0.0)
    consecutive_losses = int(capital_state.get("consecutive_losses", 0) or 0)
    regime_drawdown = drawdown_pct
    short_term_drawdown = max(0.0, -daily_pnl / 25.0)
    medium_term_drawdown = max(0.0, -rolling_pnl / 50.0)
    if drawdown_pct >= 0.12 or consecutive_losses >= 5:
        soft_circuit_active = True
        soft_circuit_reason = "capital_preservation"
    elif drawdown_pct >= 0.08 or consecutive_losses >= 3:
        soft_circuit_active = True
        soft_circuit_reason = "drawdown_reduction"
    elif daily_pnl < -4.0:
        soft_circuit_active = True
        soft_circuit_reason = "short_term_loss_control"
    else:
        soft_circuit_active = False
        soft_circuit_reason = None
    return {
        "drawdown_pct": round(drawdown_pct, 4),
        "short_term_drawdown": round(short_term_drawdown, 4),
        "medium_term_drawdown": round(medium_term_drawdown, 4),
        "regime_drawdown": round(regime_drawdown, 4),
        "consecutive_losses": consecutive_losses,
        "soft_circuit_active": soft_circuit_active,
        "soft_circuit_reason": soft_circuit_reason,
        "memory_context": memory_summary,
    }


from __future__ import annotations

from typing import Any, Dict


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def detect_mispricing(implied: Dict[str, Any] | None, fair_value: Dict[str, Any] | None, edge: Dict[str, Any] | None, live_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    implied = implied if isinstance(implied, dict) else {}
    fair_value = fair_value if isinstance(fair_value, dict) else {}
    edge = edge if isinstance(edge, dict) else {}
    live_context = live_context if isinstance(live_context, dict) else {}
    divergence = _safe_float(edge.get("divergence"), 0.0)
    conviction = _safe_float(edge.get("conviction_score"), 0.0)
    spread = _safe_float(implied.get("spread"), 0.0)
    liquidity = _safe_float(implied.get("liquidity"), 0.0)
    uncertainty = _safe_float(fair_value.get("uncertainty_score"), 0.5)
    volatility = _safe_float(fair_value.get("volatility"), 0.0)
    regime = str(edge.get("regime") or fair_value.get("regime") or "CHOPPY").strip().upper()
    personality_type = str(edge.get("personality_type") or fair_value.get("personality_type") or "balanced").strip().lower()
    mispricing_type = "none"
    if conviction >= 0.72 and abs(divergence) >= 0.03:
        mispricing_type = "high_conviction_edge"
    elif conviction >= 0.50 and abs(divergence) >= 0.02:
        mispricing_type = "moderate_edge"
    elif abs(divergence) >= 0.015:
        mispricing_type = "weak_edge"
    if regime in {"DEAD", "SPIKE_EVENT"} and spread > 0.10:
        mispricing_type = "unstable_market"
    if liquidity <= 1.0:
        mispricing_type = "fake_edge_low_liquidity"
    if uncertainty >= 0.60:
        mispricing_type = "uncertain_fair_value"
    if volatility >= 0.75 and abs(divergence) < 0.02:
        mispricing_type = "momentum_overextension" if divergence > 0 else "panic_dislocation"
    if personality_type in {"unstable", "fake_breakout_prone"} and conviction < 0.65:
        mispricing_type = "personality_misaligned"
    return {
        "ticker": str(implied.get("ticker") or "").strip(),
        "mispricing_type": mispricing_type,
        "underpriced": divergence > 0.01,
        "overpriced": divergence < -0.01,
        "divergence": round(divergence, 4),
        "conviction_score": round(conviction, 4),
        "uncertainty_score": round(uncertainty, 4),
        "regime": regime,
        "personality_type": personality_type,
        "spread": round(spread, 4),
        "liquidity": round(liquidity, 4),
    }


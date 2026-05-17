from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from .edge_scoring import score_probability_edge
from .fair_value_engine import estimate_fair_value
from .implied_probability import extract_implied_probability
from .mispricing_detector import detect_mispricing
from .probability_memory import ProbabilityMemory


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


class ProbabilityEngine:
    def __init__(
        self,
        state_file: str,
        enabled: bool = False,
        history_limit: int = 400,
        summary_interval_seconds: float = 60.0,
        max_edge_aggressiveness: float = 0.85,
        allow_blocks: bool = False,
    ):
        self.state_file = state_file
        self.enabled = bool(enabled)
        self.history_limit = max(50, int(history_limit or 400))
        self.summary_interval_seconds = max(15.0, float(summary_interval_seconds or 60.0))
        self.max_edge_aggressiveness = _clamp(max_edge_aggressiveness, 0.25, 1.0)
        self.allow_blocks = bool(allow_blocks)
        self.memory = ProbabilityMemory(self.state_file, history_limit=self.history_limit)
        self.last_summary: Dict[str, Any] = {}
        self.last_emit_ts = 0.0
        self.last_context_by_family: Dict[str, Dict[str, Any]] = {}

    def _log(self, prefix: str, **fields):
        if fields:
            suffix = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
            print(f"{prefix} {suffix}")
        else:
            print(prefix)

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_file or not os.path.exists(self.state_file):
            return {"version": 1, "updated_at": None, "families": {}, "regime_stats": {}}
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                payload.setdefault("families", {})
                payload.setdefault("regime_stats", {})
                return payload
        except Exception:
            pass
        return {"version": 1, "updated_at": None, "families": {}, "regime_stats": {}}

    def _save_state(self, payload: Dict[str, Any]):
        try:
            folder = os.path.dirname(self.state_file)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
        except Exception:
            return

    def evaluate_market(
        self,
        market: Dict[str, Any] | None,
        quote: Any,
        personality_context: Dict[str, Any] | None = None,
        websocket_manager: Any | None = None,
        source: str = "live",
    ) -> Dict[str, Any]:
        market = market if isinstance(market, dict) else {}
        personality_context = personality_context if isinstance(personality_context, dict) else {}
        family = str(market.get("family") or "").strip().upper() or str(market.get("ticker") or "").strip().upper()
        ws_snapshot = websocket_manager.get_ticker_snapshot(str(getattr(quote, "ticker", "") or "").strip(), market=market) if websocket_manager is not None else {}
        implied = extract_implied_probability(market, quote, ws_snapshot=ws_snapshot)
        fair_value = estimate_fair_value(implied, personality_context=personality_context, live_context=ws_snapshot)
        edge = score_probability_edge(implied, fair_value, personality_context=personality_context, live_context=ws_snapshot)
        mispricing = detect_mispricing(implied, fair_value, edge, live_context=ws_snapshot)
        uncertainty = _safe_float(fair_value.get("uncertainty_score"), 0.5)
        if uncertainty >= 0.60:
            edge_aggressiveness = min(self.max_edge_aggressiveness, 0.45)
        elif uncertainty >= 0.40:
            edge_aggressiveness = min(self.max_edge_aggressiveness, 0.65)
        else:
            edge_aggressiveness = self.max_edge_aggressiveness
        if personality_context.get("regime", {}).get("regime") in {"DEAD", "SPIKE_EVENT"}:
            edge_aggressiveness = min(edge_aggressiveness, 0.40)
        conviction = _clamp(edge.get("conviction_score", 0.0) * edge_aggressiveness, 0.0, 1.0)
        final_signal = "weak"
        if conviction >= 0.72 and edge.get("edge_strength", 0.0) >= 0.04:
            final_signal = "high"
        elif conviction >= 0.48 and edge.get("edge_strength", 0.0) >= 0.02:
            final_signal = "moderate"
        if mispricing.get("mispricing_type") in {"uncertain_fair_value", "unstable_market", "fake_edge_low_liquidity"}:
            final_signal = "weak"
        block_reason = None
        if self.allow_blocks and final_signal == "weak" and mispricing.get("mispricing_type") in {"uncertain_fair_value", "fake_edge_low_liquidity", "unstable_market"}:
            block_reason = mispricing.get("mispricing_type")
        score_components = {
            "personality_confidence": round(_safe_float(personality_context.get("regime", {}).get("regime_confidence"), 0.5), 4),
            "liquidity_quality": edge.get("liquidity_quality"),
            "volatility_stability": round(1.0 - _safe_float(fair_value.get("volatility"), 0.0), 4),
            "spread_health": edge.get("spread_health"),
            "trend_persistence": round(_safe_float(personality_context.get("profile", {}).get("momentum_persistence"), 0.0), 4),
            "regime_alignment": edge.get("regime_alignment"),
            "probability_divergence": edge.get("divergence"),
            "execution_quality": round(_clamp((edge.get("liquidity_quality", 0.0) + edge.get("spread_health", 0.0)) / 2.0, 0.0, 1.0), 4),
        }
        result = {
            "ticker": str(market.get("ticker") or "").strip(),
            "family": family,
            "implied_probability": implied,
            "fair_value": fair_value,
            "edge": edge,
            "mispricing": mispricing,
            "score_components": score_components,
            "final_conviction_score": round(conviction, 4),
            "final_signal": final_signal,
            "edge_aggressiveness": round(edge_aggressiveness, 4),
            "block_reason": block_reason,
            "source": source,
        }
        self.memory.record(
            {
                "family": family,
                "ticker": result["ticker"],
                "mispricing_type": mispricing.get("mispricing_type"),
                "edge_category": final_signal,
                "conviction_score": conviction,
                "divergence": edge.get("divergence", 0.0),
                "regime": edge.get("regime"),
                "personality_type": edge.get("personality_type"),
            },
            persist=False,
        )
        self.last_context_by_family[family] = result
        self._log("[PROBABILITY-IMPLIED]", ticker=result["ticker"], family=family, yes_probability=implied.get("yes_probability"), spread_adjusted_probability=implied.get("spread_adjusted_probability"), probability_drift=implied.get("probability_drift"))
        self._log("[PROBABILITY-DRIFT]", ticker=result["ticker"], family=family, probability_velocity=implied.get("probability_velocity"), probability_drift=implied.get("probability_drift"), orderbook_bias=implied.get("orderbook_bias"))
        self._log("[FAIR-VALUE]", ticker=result["ticker"], family=family, fair_probability=fair_value.get("fair_probability"), model_confidence=fair_value.get("model_confidence"), regime=fair_value.get("regime"))
        self._log("[FAIR-VALUE-CONFIDENCE]", ticker=result["ticker"], family=family, confidence_interval=fair_value.get("confidence_interval"), model_confidence=fair_value.get("model_confidence"))
        self._log("[FAIR-VALUE-UNCERTAINTY]", ticker=result["ticker"], family=family, uncertainty_score=fair_value.get("uncertainty_score"))
        self._log("[MISPRICING-DETECTED]", ticker=result["ticker"], family=family, mispricing_type=mispricing.get("mispricing_type"), divergence=mispricing.get("divergence"), conviction_score=mispricing.get("conviction_score"))
        self._log("[EDGE-SCORE]", ticker=result["ticker"], family=family, edge_strength=edge.get("edge_strength"), conviction_score=edge.get("conviction_score"), edge_category=edge.get("edge_category"))
        if edge.get("edge_category") == "weak":
            self._log("[EDGE-WEAK]", ticker=result["ticker"], family=family, reason=mispricing.get("mispricing_type"))
        if edge.get("edge_category") == "high":
            self._log("[EDGE-HIGH]", ticker=result["ticker"], family=family, reason=mispricing.get("mispricing_type"))
        self._log("[CONVICTION-SCORE]", ticker=result["ticker"], family=family, final_conviction_score=result["final_conviction_score"], final_signal=final_signal)
        self._log("[EDGE-COMPONENTS]", ticker=result["ticker"], family=family, components=score_components)
        if personality_context:
            self._log("[PERSONALITY-EDGE]", ticker=result["ticker"], family=family, regime=personality_context.get("regime", {}).get("regime"), personality_type=personality_context.get("profile", {}).get("personality_type"))
            self._log("[PERSONALITY-MISPRICING]", ticker=result["ticker"], family=family, mispricing_type=mispricing.get("mispricing_type"), personality_type=personality_context.get("profile", {}).get("personality_type"))
        if block_reason:
            self._log("[EDGE-BLOCK]", ticker=result["ticker"], family=family, reason=block_reason)
        else:
            self._log("[EDGE-APPROVED]", ticker=result["ticker"], family=family, final_signal=final_signal)
        self.memory.persist()
        if (time.time() - self.last_emit_ts) >= self.summary_interval_seconds:
            self.last_emit_ts = time.time()
            summary = self.memory.summarize()
            self.last_summary = summary
            self._log("[EDGE-MEMORY]", source=source, recent_events=len(summary.get("recent_events", [])), families=len(summary.get("family_stats", {})))
            self._log("[EDGE-PERFORMANCE]", source=source, top_families=",".join(item["family"] for item in summary.get("top_families", [])[:5]) or "none")
            self._log("[EDGE-HEALTH]", source=source, weak_families=",".join(item["family"] for item in summary.get("weak_families", [])[:5]) or "none")
            self._log("[EDGE-SUMMARY]", source=source, top_families=",".join(item["family"] for item in summary.get("top_families", [])[:5]) or "none", weak_families=",".join(item["family"] for item in summary.get("weak_families", [])[:5]) or "none")
            self._log("[EDGE-TOP]", source=source, families=",".join(item["family"] for item in summary.get("top_families", [])[:5]) or "none")
            self._log("[EDGE-WEAK]", source=source, families=",".join(item["family"] for item in summary.get("weak_families", [])[:5]) or "none")
        return result

    def get_summary(self) -> Dict[str, Any]:
        return dict(self.last_summary or {})


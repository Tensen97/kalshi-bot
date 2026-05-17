from __future__ import annotations

from typing import Any, Dict

from .aggression_scaler import scale_aggression
from .drawdown_monitor import monitor_drawdown
from .exposure_manager import manage_exposure
from .risk_memory import RiskMemory


class CapitalEngine:
    def __init__(
        self,
        state_file: str,
        enabled: bool = False,
        summary_interval_seconds: float = 60.0,
        history_limit: int = 400,
        max_aggression: float = 0.85,
        allow_blocks: bool = False,
    ):
        self.state_file = state_file
        self.enabled = bool(enabled)
        self.summary_interval_seconds = max(15.0, float(summary_interval_seconds or 60.0))
        self.history_limit = max(50, int(history_limit or 400))
        self.max_aggression = max(0.25, min(1.0, float(max_aggression or 0.85)))
        self.allow_blocks = bool(allow_blocks)
        self.memory = RiskMemory(state_file, history_limit=self.history_limit)
        self.last_summary: Dict[str, Any] = {}
        self.last_emit_ts = 0.0
        self.last_context_by_family: Dict[str, Dict[str, Any]] = {}
        self.last_global_state: Dict[str, Any] = {}

    def _log(self, prefix: str, **fields):
        if fields:
            suffix = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
            print(f"{prefix} {suffix}")
        else:
            print(prefix)

    def evaluate(self, bot=None, probability_context=None, personality_context=None, external_context=None, live_select_summary=None, source="live") -> Dict[str, Any]:
        if not self.enabled:
            summary = {
                "enabled": False,
                "source": source,
                "capital_state": "normal",
                "aggression_level": 0.35,
                "drawdown_state": "stable",
                "exposure_state": "stable",
                "soft_circuit_active": False,
            }
            self.last_summary = summary
            self._log("[RISK-STATE]", enabled="no", source=source, capital_state="normal")
            return summary
        probability_context = probability_context if isinstance(probability_context, dict) else {}
        personality_context = personality_context if isinstance(personality_context, dict) else {}
        external_context = external_context if isinstance(external_context, dict) else {}
        live_select_summary = live_select_summary if isinstance(live_select_summary, dict) else {}
        capital_state = self._build_capital_state(bot=bot, probability_context=probability_context, personality_context=personality_context, external_context=external_context)
        drawdown_state = monitor_drawdown(capital_state, self.memory.summarize())
        exposure_state = manage_exposure(bot=bot, probability_context=probability_context, personality_context=personality_context, external_context=external_context, live_select_summary=live_select_summary, state_summary=self.memory.summarize())
        aggression_state = scale_aggression(capital_state, drawdown_state, exposure_state, probability_context, personality_context, external_context=external_context, max_aggression=self.max_aggression)
        risk_state = self._build_risk_state(capital_state, drawdown_state, exposure_state, aggression_state, source=source)
        self.last_global_state = risk_state
        self.last_context_by_family = exposure_state.get("family_exposure", {}) if isinstance(exposure_state, dict) else {}
        self.memory.record(
            {
                "source": source,
                "capital_state": risk_state.get("capital_state"),
                "aggression_level": risk_state.get("aggression_level"),
                "drawdown_state": risk_state.get("drawdown_state"),
                "exposure_state": risk_state.get("exposure_state"),
                "result": "observed",
            },
            persist=False,
        )
        if self.allow_blocks and risk_state.get("soft_circuit_active"):
            self._log("[SOFT-CIRCUIT]", source=source, reason=risk_state.get("soft_circuit_reason"))
        self._log("[CAPITAL-STATE]", source=source, capital_state=risk_state.get("capital_state"), daily_pnl=risk_state.get("daily_pnl"), rolling_pnl=risk_state.get("rolling_pnl"), drawdown_pct=risk_state.get("drawdown_pct"))
        self._log("[DRAWDOWN-STATE]", source=source, short_term_drawdown=risk_state.get("short_term_drawdown"), medium_term_drawdown=risk_state.get("medium_term_drawdown"), regime_drawdown=risk_state.get("regime_drawdown"), loss_streak=risk_state.get("consecutive_losses"))
        self._log("[EXPOSURE-STATE]", source=source, total_exposure=risk_state.get("total_exposure_dollars"), family_exposure=risk_state.get("family_exposure"), personality_exposure=risk_state.get("personality_exposure"), regime_exposure=risk_state.get("regime_exposure"))
        self._log("[AGGRESSION-STATE]", source=source, aggression_level=risk_state.get("aggression_level"), aggression_mode=risk_state.get("aggression_mode"), reason=risk_state.get("aggression_reason"))
        self._log("[RISK-EDGE-BALANCE]", source=source, conviction_score=probability_context.get("final_conviction_score"), aggression_level=risk_state.get("aggression_level"), risk_multiplier=risk_state.get("risk_multiplier"))
        if external_context:
            self._log("[EXTERNAL-RISK]", source=source, event_risk_level=external_context.get("event_risk", {}).get("risk_level"), execution_caution=external_context.get("execution_caution"), uncertainty_boost=external_context.get("uncertainty_boost"))
        self.memory.persist()
        if risk_state.get("aggression_level", 0.0) > 0.55:
            self._log("[AGGRESSION-UP]", source=source, aggression_level=risk_state.get("aggression_level"), reason=risk_state.get("aggression_reason"))
        elif risk_state.get("aggression_level", 0.0) < 0.30:
            self._log("[AGGRESSION-DOWN]", source=source, aggression_level=risk_state.get("aggression_level"), reason=risk_state.get("aggression_reason"))
        if (risk_state.get("soft_circuit_active") and self.allow_blocks) or risk_state.get("capital_state") in {"DEFENSIVE", "CAPITAL_PRESERVATION"}:
            self._log("[CAPITAL-PROTECTION]", source=source, reason=risk_state.get("soft_circuit_reason") or risk_state.get("capital_state"))
        if (risk_state.get("drawdown_pct") or 0.0) > 0.05:
            self._log("[DRAWDOWN-REDUCTION]", source=source, drawdown_pct=risk_state.get("drawdown_pct"), reason=risk_state.get("drawdown_reason"))
        if exposure_state.get("exposure_warning"):
            self._log("[EXPOSURE-WARNING]", source=source, reason=exposure_state.get("exposure_warning_reason"))
        if exposure_state.get("exposure_block"):
            self._log("[EXPOSURE-BLOCK]", source=source, reason=exposure_state.get("exposure_block_reason"))
        if (time_now := risk_state.get("capital_state")) in {"AGGRESSIVE", "NORMAL", "DEFENSIVE", "CAPITAL_PRESERVATION"}:
            pass
        now_ts = risk_state.get("timestamp")
        if (now_ts - self.last_emit_ts) >= self.summary_interval_seconds:
            self.last_emit_ts = now_ts
            self.last_summary = self.memory.summarize()
            self._log("[RISK-MEMORY]", source=source, events=len(self.last_summary.get("recent_events", [])), families=len(self.last_summary.get("family_stats", {})))
            self._log("[RISK-PERFORMANCE]", source=source, strongest_families=",".join(item["family"] for item in self.last_summary.get("top_families", [])[:5]) or "none")
            self._log("[RISK-SUMMARY]", source=source, capital_state=risk_state.get("capital_state"), aggression_level=risk_state.get("aggression_level"), drawdown_pct=risk_state.get("drawdown_pct"), soft_circuit_active=risk_state.get("soft_circuit_active"))
            self._log("[CAPITAL-SUMMARY]", source=source, daily_pnl=risk_state.get("daily_pnl"), rolling_pnl=risk_state.get("rolling_pnl"), consecutive_losses=risk_state.get("consecutive_losses"), consecutive_wins=risk_state.get("consecutive_wins"))
            self._log("[EXPOSURE-SUMMARY]", source=source, total_exposure=risk_state.get("total_exposure_dollars"), concentration_warnings=risk_state.get("concentration_warnings"))
            self._log("[AGGRESSION-SUMMARY]", source=source, aggression_level=risk_state.get("aggression_level"), aggression_mode=risk_state.get("aggression_mode"), risk_multiplier=risk_state.get("risk_multiplier"))
        return risk_state

    def get_summary(self) -> Dict[str, Any]:
        summary = dict(self.last_summary or {})
        summary.setdefault("enabled", self.enabled)
        summary.setdefault("last_global_state", self.last_global_state or {})
        return summary

    def get_global_state(self) -> Dict[str, Any]:
        return dict(self.last_global_state or {})

    def _build_capital_state(self, bot=None, probability_context=None, personality_context=None, external_context=None) -> Dict[str, Any]:
        probability_context = probability_context if isinstance(probability_context, dict) else {}
        personality_context = personality_context if isinstance(personality_context, dict) else {}
        external_context = external_context if isinstance(external_context, dict) else {}
        portfolio = getattr(bot, "last_realm_xviii_summary", {}) if bot is not None else {}
        daily_pnl = float((portfolio or {}).get("daily_realized_pnl", 0.0) or 0.0)
        rolling_pnl = float((portfolio or {}).get("rolling_pnl", daily_pnl) or daily_pnl)
        unrealized_pnl = float((portfolio or {}).get("unrealized_pnl", 0.0) or 0.0)
        total_exposure = float((portfolio or {}).get("total_exposure_dollars", 0.0) or 0.0)
        drawdown_pct = float((portfolio or {}).get("drawdown_pct", 0.0) or 0.0)
        consecutive_losses = int((portfolio or {}).get("consecutive_losses", 0) or 0)
        consecutive_wins = int((portfolio or {}).get("consecutive_wins", 0) or 0)
        capital_state = "NORMAL"
        if drawdown_pct >= 0.12 or daily_pnl <= -8.0:
            capital_state = "CAPITAL_PRESERVATION"
        elif drawdown_pct >= 0.07 or daily_pnl <= -4.0 or consecutive_losses >= 4:
            capital_state = "DEFENSIVE"
        elif daily_pnl >= 3.0 and drawdown_pct <= 0.03 and probability_context.get("final_signal") in {"moderate", "high"}:
            capital_state = "AGGRESSIVE"
        if external_context.get("event_risk", {}).get("risk_level") in {"HIGH", "EXTREME"}:
            capital_state = "CAPITAL_PRESERVATION" if external_context.get("event_risk", {}).get("risk_level") == "EXTREME" else "DEFENSIVE"
        family_exposure = dict((portfolio or {}).get("exposure_by_family", {}) or {})
        personality_exposure = dict((portfolio or {}).get("exposure_by_personality", {}) or {})
        regime_exposure = dict((portfolio or {}).get("exposure_by_regime", {}) or {})
        return {
            "capital_state": capital_state,
            "daily_pnl": round(daily_pnl, 4),
            "rolling_pnl": round(rolling_pnl, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "drawdown_pct": round(drawdown_pct, 4),
            "consecutive_losses": consecutive_losses,
            "consecutive_wins": consecutive_wins,
            "family_exposure": family_exposure,
            "personality_exposure": personality_exposure,
            "regime_exposure": regime_exposure,
            "total_exposure_dollars": round(total_exposure, 4),
            "timestamp": self._now(),
            "probability_signal": probability_context.get("final_signal", "weak"),
            "personality_regime": personality_context.get("regime", {}).get("regime"),
            "external_risk_level": external_context.get("event_risk", {}).get("risk_level"),
        }

    def _build_risk_state(self, capital_state, drawdown_state, exposure_state, aggression_state, source="live"):
        capital_state_name = str(capital_state.get("capital_state") or "NORMAL").upper()
        aggression_level = float(aggression_state.get("aggression_level", 0.35) or 0.35)
        soft_circuit_active = bool(drawdown_state.get("soft_circuit_active") or exposure_state.get("exposure_warning") or capital_state_name == "CAPITAL_PRESERVATION")
        soft_circuit_reason = drawdown_state.get("soft_circuit_reason") or exposure_state.get("exposure_warning_reason") or capital_state_name.lower()
        risk_multiplier = float(aggression_state.get("risk_multiplier", 1.0) or 1.0)
        return {
            "source": source,
            "timestamp": self._now(),
            "capital_state": capital_state_name,
            "aggression_level": round(max(0.0, min(1.0, aggression_level)), 4),
            "aggression_mode": aggression_state.get("aggression_mode", "normal"),
            "aggression_reason": aggression_state.get("aggression_reason", "stable"),
            "risk_multiplier": round(max(0.25, min(1.25, risk_multiplier)), 4),
            "drawdown_pct": round(float(drawdown_state.get("drawdown_pct", capital_state.get("drawdown_pct", 0.0)) or 0.0), 4),
            "short_term_drawdown": round(float(drawdown_state.get("short_term_drawdown", 0.0) or 0.0), 4),
            "medium_term_drawdown": round(float(drawdown_state.get("medium_term_drawdown", 0.0) or 0.0), 4),
            "regime_drawdown": round(float(drawdown_state.get("regime_drawdown", 0.0) or 0.0), 4),
            "consecutive_losses": int(capital_state.get("consecutive_losses", 0) or 0),
            "consecutive_wins": int(capital_state.get("consecutive_wins", 0) or 0),
            "total_exposure_dollars": capital_state.get("total_exposure_dollars", 0.0),
            "family_exposure": exposure_state.get("family_exposure", {}),
            "personality_exposure": exposure_state.get("personality_exposure", {}),
            "regime_exposure": exposure_state.get("regime_exposure", {}),
            "exposure_state": exposure_state.get("exposure_state", "stable"),
            "exposure_warning": exposure_state.get("exposure_warning", False),
            "exposure_warning_reason": exposure_state.get("exposure_warning_reason"),
            "soft_circuit_active": soft_circuit_active,
            "soft_circuit_reason": soft_circuit_reason,
            "concentration_warnings": exposure_state.get("concentration_warnings", []),
            "daily_pnl": capital_state.get("daily_pnl", 0.0),
            "rolling_pnl": capital_state.get("rolling_pnl", 0.0),
        }

    def _now(self):
        import time
        return time.time()

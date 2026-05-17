from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List

from .catalyst_detector import detect_catalyst
from .context_memory import ExternalContextMemory
from .event_risk import assess_event_risk
from .sports_context import build_sports_context
from .macro_context import build_macro_context
from .weather_context import build_weather_context
from .external_summary import build_external_summary


class ExternalAwarenessEngine:
    def __init__(
        self,
        state_file: str,
        enabled: bool = False,
        summary_interval_seconds: float = 60.0,
        history_limit: int = 200,
    ):
        self.state_file = state_file
        self.enabled = bool(enabled)
        self.summary_interval_seconds = max(15.0, float(summary_interval_seconds or 60.0))
        self.history_limit = max(20, int(history_limit or 200))
        self.memory = ExternalContextMemory(state_file, history_limit=self.history_limit)
        self.last_summary: Dict[str, Any] = {}
        self.last_state: Dict[str, Any] = {}
        self.last_emit_ts = 0.0

    def _log(self, prefix: str, **fields):
        suffix = " ".join(f"{key}={fields[key]}" for key in sorted(fields) if fields[key] is not None)
        print(f"{prefix} {suffix}".strip())

    def ingest_context(self, context: Dict[str, Any] | None = None, source: str = "manual"):
        context = context if isinstance(context, dict) else {}
        item = dict(context)
        item["source"] = str(item.get("source") or source)
        item["kind"] = item.get("kind") or "context"
        self.memory.record(item)
        self._log("[EXTERNAL-CONTEXT]", source=item["source"], category=item.get("category"), family=item.get("family"), confidence=item.get("confidence"), relevance=item.get("relevance"))

    def evaluate(self, bot=None, markets=None, quotes=None, live_select_summary=None, source="live") -> Dict[str, Any]:
        if not self.enabled:
            summary = {
                "enabled": False,
                "external_awareness_state": "inactive",
                "active_catalysts": [],
                "top_external_risks": [],
                "affected_families": [],
                "event_risk": {"risk_level": "NONE", "risk_score": 0.0, "top_risks": []},
                "summary": "external_awareness_disabled",
            }
            self.last_state = summary
            self.last_summary = summary
            self._log("[REALM-XXV-STATE]", enabled="no", source=source)
            return summary
        markets = list(markets or [])
        quotes = list(quotes or [])
        live_select_summary = live_select_summary if isinstance(live_select_summary, dict) else {}
        contexts = []
        for market in markets[:25]:
            if not isinstance(market, dict):
                continue
            contexts.extend(self._contexts_for_market(market, live_select_summary=live_select_summary))
        for quote in quotes[:25]:
            if not hasattr(quote, "ticker"):
                continue
            contexts.extend(self._contexts_for_quote(quote, live_select_summary=live_select_summary))
        contexts.extend(self.memory.summarize().get("contexts", [])[-10:])
        meaningful_contexts = [
            item
            for item in contexts
            if isinstance(item, dict)
            and str(item.get("text") or item.get("summary") or "").strip()
            and "context placeholder" not in str(item.get("text") or item.get("summary") or "").lower()
            and "live quote context" not in str(item.get("text") or item.get("summary") or "").lower()
        ]
        catalysts = [detect_catalyst(item) for item in meaningful_contexts]
        sports_context = self._build_context_layer(contexts, build_sports_context, "sports")
        macro_events = [
            item
            for item in contexts
            if isinstance(item, dict)
            and any(key in str(item.get("category") or item.get("summary") or item.get("text") or "").upper() for key in ("CPI", "FOMC", "JOBS", "FED", "INFLATION", "RATE"))
        ]
        macro_context = build_macro_context({"events": macro_events})
        weather_context = self._build_context_layer(contexts, build_weather_context, "weather")
        event_risk = assess_event_risk(catalysts)
        affected_families = sorted({str(item.get("affected_family") or item.get("family") or "UNKNOWN").strip().upper() for item in catalysts if str(item.get("affected_family") or item.get("family") or "").strip()})
        top_external_risks = event_risk.get("top_risks", [])[:5]
        confidence_levels = {
            "sports": sports_context.get("match_relevance", 0.0),
            "macro": 1.0 if macro_context.get("macro_risk_window") else 0.0,
            "weather": 1.0 if weather_context.get("risk_window") else 0.0,
            "event": event_risk.get("risk_score", 0.0),
        }
        summary = {
            "enabled": True,
            "source": source,
            "generated_at": time.time(),
            "external_awareness_state": "active" if catalysts else "monitoring",
            "active_catalysts": catalysts[:10],
            "top_external_risks": top_external_risks,
            "affected_families": affected_families,
            "sports_context": sports_context,
            "macro_context": macro_context,
            "weather_context": weather_context,
            "event_risk": event_risk,
            "confidence_levels": confidence_levels,
            "summary": self._build_summary_text(catalysts, event_risk, sports_context, macro_context, weather_context),
            "execution_caution": bool(event_risk.get("risk_level") in {"HIGH", "EXTREME"}),
            "uncertainty_boost": round(min(0.20, event_risk.get("risk_score", 0.0) * 0.20), 4),
            "conviction_cap": round(min(0.12, event_risk.get("risk_score", 0.0) * 0.10), 4),
        }
        if not meaningful_contexts:
            summary["missing_context"] = True
        self.last_state = summary
        self.last_summary = build_external_summary(summary)
        self.memory.record({"kind": "snapshot", "source": source, "family": "EXTERNAL", "summary": summary.get("summary"), "kind_state": summary.get("external_awareness_state")})
        self._log("[EXTERNAL-AWARENESS]", source=source, catalysts=len(catalysts), risk_level=event_risk.get("risk_level"), risk_score=event_risk.get("risk_score"))
        self._log("[EXTERNAL-SUMMARY]", source=source, affected_families=",".join(affected_families[:5]) or "none", execution_caution="yes" if summary["execution_caution"] else "no")
        self._log("[SPORTS-CONTEXT]", source=source, relevant="yes" if sports_context.get("sports_relevant") else "no", missing="yes" if sports_context.get("sports_context_missing") else "no", summary=sports_context.get("summary"))
        self._log("[MACRO-CONTEXT]", source=source, risk_window="yes" if macro_context.get("macro_risk_window") else "no", summary=macro_context.get("summary"))
        self._log("[WEATHER-CONTEXT]", source=source, relevant="yes" if weather_context.get("weather_relevant") else "no", missing="yes" if weather_context.get("weather_context_missing") else "no", summary=weather_context.get("summary"))
        if catalysts:
            self._log("[CATALYST-DETECTED]", source=source, catalyst_type=catalysts[0].get("catalyst_type"), family=catalysts[0].get("affected_family"), relevance=catalysts[0].get("relevance"), confidence=catalysts[0].get("confidence"))
            if float(catalysts[0].get("confidence", 0.0) or 0.0) < 0.40:
                self._log("[CATALYST-LOW-CONFIDENCE]", source=source, catalyst_type=catalysts[0].get("catalyst_type"), family=catalysts[0].get("affected_family"), confidence=catalysts[0].get("confidence"))
            if catalysts[0].get("possible_market_impact") in {"high_volatility", "uncertain_shift"}:
                self._log("[CATALYST-HIGH-IMPACT]", source=source, catalyst_type=catalysts[0].get("catalyst_type"), family=catalysts[0].get("affected_family"), impact=catalysts[0].get("possible_market_impact"))
        if event_risk.get("risk_level") in {"HIGH", "EXTREME"}:
            self._log("[EVENT-RISK-HIGH]", source=source, risk_level=event_risk.get("risk_level"), risk_score=event_risk.get("risk_score"))
        self._log("[EVENT-RISK-SUMMARY]", source=source, risk_level=event_risk.get("risk_level"), risk_score=event_risk.get("risk_score"))
        now_ts = time.time()
        if (now_ts - self.last_emit_ts) >= self.summary_interval_seconds:
            self.last_emit_ts = now_ts
            self._log("[EXTERNAL-HEALTH]", source=source, active_catalysts=len(catalysts), risk_level=event_risk.get("risk_level"), missing_context="yes" if not meaningful_contexts else "no")
            self._log("[EXTERNAL-ACTIVE]", source=source, sports="yes" if sports_context.get("sports_relevant") else "no", macro="yes" if macro_context.get("macro_risk_window") else "no", weather="yes" if weather_context.get("weather_relevant") else "no")
            if not meaningful_contexts:
                self._log("[EXTERNAL-MISSING]", source=source, reason="no_context_items")
            self._log("[EXTERNAL-TELEMETRY]", source=source, catalysts=len(catalysts), risk_level=event_risk.get("risk_level"))
        return summary

    def get_summary(self) -> Dict[str, Any]:
        return dict(self.last_summary or {})

    def get_state(self) -> Dict[str, Any]:
        return dict(self.last_state or {})

    def _contexts_for_market(self, market: Dict[str, Any], live_select_summary=None) -> List[Dict[str, Any]]:
        live_select_summary = live_select_summary if isinstance(live_select_summary, dict) else {}
        family = str(market.get("family") or market.get("market_family") or market.get("category") or "UNKNOWN").strip().upper()
        contexts = []
        for key in ("headline", "news", "news_text", "event_text", "text", "summary", "description", "manual_context"):
            value = market.get(key)
            if value:
                contexts.append({"source": "market", "category": family or "UNKNOWN_EVENT", "family": family, "ticker": market.get("ticker"), "text": value, "summary": value, "confidence": market.get("confidence", 0.5), "relevance": market.get("relevance", 0.5), "urgency": market.get("urgency", 0.5)})
        if not contexts and family != "UNKNOWN":
            contexts.append({"source": "market_family", "category": family, "family": family, "ticker": market.get("ticker"), "text": f"{family} context placeholder", "summary": f"{family} context placeholder", "confidence": 0.2, "relevance": 0.2, "urgency": 0.1})
        if live_select_summary.get("family") == family or live_select_summary.get("ticker") == market.get("ticker"):
            contexts.append({"source": "live_select", "category": family or "UNKNOWN_EVENT", "family": family, "ticker": market.get("ticker"), "text": live_select_summary.get("decision_reason") or live_select_summary.get("summary") or "", "summary": live_select_summary.get("decision_reason") or live_select_summary.get("summary") or "", "confidence": 0.35, "relevance": 0.4, "urgency": 0.3})
        return contexts

    def _contexts_for_quote(self, quote: Any, live_select_summary=None) -> List[Dict[str, Any]]:
        family = str(getattr(quote, "family", "") or getattr(quote, "market_family", "") or "").strip().upper()
        ticker = str(getattr(quote, "ticker", "") or "").strip().upper()
        contexts = []
        if family:
            contexts.append({"source": "quote", "category": family, "family": family, "ticker": ticker, "text": f"{family} live quote context", "summary": f"{family} live quote context", "confidence": 0.25, "relevance": 0.25, "urgency": 0.15})
        return contexts

    def _build_context_layer(self, contexts, builder, kind):
        selected = {}
        for item in contexts:
            if not isinstance(item, dict):
                continue
            if kind == "sports" and any(key in str(item.get("category") or item.get("family") or "").upper() for key in ("SPORT", "NBA", "MLB", "NFL", "NHL")):
                selected = item
                break
            if kind == "macro" and any(key in str(item.get("category") or item.get("summary") or "").upper() for key in ("CPI", "FOMC", "JOBS", "FED", "INFLATION", "RATE")):
                selected = item
                break
            if kind == "weather" and any(key in str(item.get("category") or item.get("family") or "").upper() for key in ("WEATHER", "RAIN", "SNOW", "STORM", "HURRICANE")):
                selected = item
                break
        return builder(selected)

    def _build_summary_text(self, catalysts, event_risk, sports_context, macro_context, weather_context):
        return (
            f"external_state={event_risk.get('risk_level')} "
            f"catalysts={len(catalysts)} "
            f"sports={sports_context.get('summary')} "
            f"macro={macro_context.get('summary')} "
            f"weather={weather_context.get('summary')}"
        )

from __future__ import annotations

from collections import deque


def _safe_str(value, default="unknown"):
    text = str(value) if value is not None else ""
    text = text.strip()
    return text or default


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _listify(value):
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, (list, tuple, set, deque)):
        return list(value)
    if value is None:
        return []
    return [value]


def _first_text(items):
    for item in _listify(items):
        if isinstance(item, dict):
            candidate = item.get("family") or item.get("name") or item.get("ticker") or item.get("id")
        else:
            candidate = item
        text = str(candidate or "").strip()
        if text:
            return text
    return None


class StateInterpreter:
    def interpret(self, state):
        state = state or {}
        telemetry = state.get("telemetry", {}) or {}
        health = state.get("health", {}) or {}
        websocket = telemetry.get("websocket_status", {}) or state.get("websocket", {}) or {}
        risk = telemetry.get("latest_risk_state", {}) or state.get("risk", {}) or {}
        external = telemetry.get("latest_external_summary", {}) or state.get("external", {}) or {}
        probability = telemetry.get("latest_probability_summary", {}) or state.get("probability", {}) or {}
        personality = telemetry.get("latest_personality_summary", {}) or state.get("personality", {}) or {}
        evolution = telemetry.get("latest_evolution_summary", {}) or state.get("evolution", {}) or {}
        command_center = telemetry.get("command_center", {}) or state.get("command_center", {}) or {}
        execution = telemetry.get("execution_posture", {}) or state.get("execution_posture", {}) or {}
        return {
            "overview": self.interpret_overview(state, telemetry=telemetry, health=health),
            "execution": self.interpret_execution_posture(state, telemetry=telemetry, execution=execution),
            "websocket": self.interpret_websocket(websocket, telemetry=telemetry),
            "personality": self.interpret_personality(personality),
            "probability": self.interpret_probability(probability),
            "risk": self.interpret_risk(risk),
            "evolution": self.interpret_evolution(evolution),
            "command_center": self.interpret_command_center(command_center, health=health),
        }

    def interpret_overview(self, state, telemetry=None, health=None):
        telemetry = telemetry or {}
        health = health or {}
        live_mode = bool((telemetry.get("live_mode") or {}).get("active") or (state.get("identity") or {}).get("portfolio_brain_active"))
        status = _safe_str(telemetry.get("status") or state.get("status"), "waiting")
        posture = _safe_str((telemetry.get("execution_posture") or {}).get("posture") or (telemetry.get("execution_posture") or {}).get("mode"), "WAITING_FOR_SIGNAL")
        selected_ticker = telemetry.get("latest_selected_ticker")
        websocket_health = _safe_str((telemetry.get("websocket_status") or {}).get("health") or (state.get("websocket") or {}).get("health"), "unknown")
        if live_mode and status == "live":
            if selected_ticker:
                summary = f"AION is live and focused on {selected_ticker}. Execution posture is {posture.lower()}."
            elif posture == "WAITING_FOR_SIGNAL":
                summary = "AION is live and scanning. No active opportunities currently satisfy Realm V.V timing conditions."
            else:
                summary = f"AION is live and scanning. Execution posture is {posture.lower()}."
        else:
            summary = "AION is online but not yet operating in a live posture."
        if websocket_health not in {"healthy", "ok", "good"}:
            summary += " WebSocket health is not yet reporting a fully healthy stream."
        if str(health.get("global_health_state") or "").upper() in {"WATCH", "DEGRADED", "CRITICAL"}:
            summary += f" Global health is {str(health.get('global_health_state')).lower()}."
        return {
            "summary": summary.strip(),
            "status": status,
            "live_mode": live_mode,
            "websocket_health": websocket_health,
            "execution_posture": posture,
            "selected_ticker": selected_ticker,
        }

    def interpret_execution_posture(self, state, telemetry=None, execution=None):
        telemetry = telemetry or {}
        execution = execution or {}
        posture = _safe_str(execution.get("posture") or execution.get("mode"), "WAITING_FOR_SIGNAL")
        phase = _safe_str(execution.get("phase") or telemetry.get("current_phase"), "unknown")
        block_reason = telemetry.get("latest_execution_block_reason")
        live_mode = bool((telemetry.get("live_mode") or {}).get("active"))
        armed = bool((telemetry.get("live_mode") or {}).get("armed"))
        if posture == "WAITING_FOR_SIGNAL":
            summary = "AION is actively monitoring markets but no valid signal currently meets entry standards."
        elif posture == "ENTRY_TIMING":
            summary = "Signal detected. Realm V.V timing layer is evaluating pullback structure."
        elif posture == "BLOCKED":
            summary = "Execution blocked due to insufficient conviction or elevated environmental risk."
        elif posture == "LIVE_READY":
            summary = "AION is armed and conditions currently satisfy execution requirements."
        elif posture in {"DEFENSIVE", "CAPITAL_PRESERVATION"}:
            summary = "Capital preservation mode is active due to unstable market conditions."
        elif posture == "EXECUTING":
            summary = "An execution path is active and the position lifecycle is being monitored."
        elif posture == "MONITORING_POSITION":
            summary = "A live position is open and the bot is monitoring it for exit conditions."
        else:
            summary = f"Operational posture is {posture.lower().replace('_', ' ')}."
        if block_reason:
            summary = f"{summary} Current block reason: {_safe_str(block_reason)}."
        if live_mode and armed and posture != "BLOCKED" and posture != "WAITING_FOR_SIGNAL":
            summary = summary + " Live arming is confirmed."
        return {
            "posture": posture,
            "phase": phase,
            "summary": summary.strip(),
            "block_reason": block_reason,
            "armed": armed,
            "live_mode": live_mode,
        }

    def interpret_websocket(self, websocket, telemetry=None):
        telemetry = telemetry or {}
        websocket = websocket or {}
        health = _safe_str(websocket.get("health") or websocket.get("connection_state") or websocket.get("status"), "unknown")
        subscriptions = websocket.get("active_subscriptions") or telemetry.get("websocket_subscription_count") or websocket.get("subscription_count") or 0
        latency = websocket.get("latency_ms") or telemetry.get("websocket_latency")
        stale = bool(websocket.get("stale_feed") or telemetry.get("websocket_stale_feed"))
        if health in {"healthy", "ok", "good"} and int(_safe_float(subscriptions, 0) or 0) > 0:
            summary = f"WebSocket nervous system stable with {int(_safe_float(subscriptions, 0) or 0)} active subscriptions."
        elif stale:
            summary = "Market stream freshness is degraded; reconnect monitoring remains active."
        else:
            summary = "WebSocket feed is connected but not yet reporting a strong operational health signal."
        if latency is not None:
            summary += f" Feed latency is {_safe_float(latency, 0.0):.1f} ms."
        return {
            "status": health,
            "summary": summary.strip(),
            "subscriptions": int(_safe_float(subscriptions, 0) or 0),
            "latency_ms": _safe_float(latency, None),
            "stale_feed": stale,
        }

    def interpret_personality(self, personality):
        personality = personality or {}
        top_families = personality.get("top_families") or []
        weak_families = personality.get("weak_families") or []
        regimes = personality.get("current_regimes") or personality.get("regimes") or []
        top_family = _first_text(top_families)
        weak_family = _first_text(weak_families)
        regime = _first_text(regimes)
        confidence = personality.get("personality_confidence")
        if top_family and weak_family:
            summary = f"{top_family} environments currently lead structural reliability while {weak_family} remains deprioritized."
        elif top_family:
            summary = f"{top_family} environments currently show the strongest structural reliability."
        elif regime:
            summary = f"Current market behavior is dominated by a {regime} regime."
        else:
            summary = "Personality telemetry is not yet populated."
        if confidence is not None:
            summary += f" Confidence is {_safe_float(confidence, 0.0):.3f}."
        return {
            "summary": summary.strip(),
            "top_family": top_family,
            "weak_family": weak_family,
            "top_regime": regime,
            "confidence": _safe_float(confidence, None),
        }

    def interpret_probability(self, probability):
        probability = probability or {}
        fair_value = probability.get("fair_value_state") or (probability.get("fair_value") or {}).get("state")
        conviction = probability.get("conviction_score")
        edge = probability.get("edge_state") or probability.get("edge_strength")
        mispricing = probability.get("mispricing_state") or (probability.get("mispricing") or {}).get("state")
        if fair_value or conviction is not None or edge or mispricing:
            parts = []
            if fair_value:
                parts.append(f"Fair value state is {_safe_str(fair_value)}.")
            if conviction is not None:
                parts.append(f"Conviction is {_safe_float(conviction, 0.0):.3f}.")
            if edge:
                parts.append(f"Edge quality is {_safe_str(edge)}.")
            if mispricing:
                parts.append(f"Mispricing state is {_safe_str(mispricing)}.")
            summary = " ".join(parts)
        else:
            summary = "Probability engine currently reports weak market inefficiencies."
        return {
            "summary": summary.strip(),
            "fair_value": fair_value,
            "conviction": _safe_float(conviction, None),
            "edge": edge,
            "mispricing": mispricing,
        }

    def interpret_risk(self, risk):
        risk = risk or {}
        last_global = risk.get("last_global_state") or {}
        capital_state = _safe_str(last_global.get("capital_state") or risk.get("capital_state") or "unknown", "unknown")
        aggression = last_global.get("aggression_level") if last_global.get("aggression_level") is not None else risk.get("aggression_level")
        drawdown = last_global.get("drawdown_pct") if last_global.get("drawdown_pct") is not None else risk.get("drawdown_pct")
        exposure = last_global.get("total_exposure_dollars") if last_global.get("total_exposure_dollars") is not None else risk.get("total_exposure_dollars")
        if capital_state in {"defensive", "capital_preservation", "risk_off"}:
            summary = f"Capital deployment is intentionally defensive under a {capital_state.replace('_', ' ')} posture."
        else:
            summary = "Risk posture is stable with conservative exposure."
        if aggression is not None:
            summary += f" Aggression is {_safe_float(aggression, 0.0):.3f}."
        if drawdown is not None:
            summary += f" Drawdown is {_safe_float(drawdown, 0.0):.3f}."
        if exposure is not None:
            summary += f" Exposure is ${_safe_float(exposure, 0.0):.2f}."
        return {
            "summary": summary.strip(),
            "capital_state": capital_state,
            "aggression_level": _safe_float(aggression, None),
            "drawdown_pct": _safe_float(drawdown, None),
            "exposure_dollars": _safe_float(exposure, None),
        }

    def interpret_evolution(self, evolution):
        evolution = evolution or {}
        health = evolution.get("health") or {}
        recommendations = evolution.get("recommendations") or []
        patterns = evolution.get("pattern_summary") or {}
        success_patterns = patterns.get("success_patterns") or []
        health_state = _safe_str(health.get("health_state"), "unknown")
        recommendation_count = len(_listify(recommendations))
        if health_state in {"healthy", "stable", "optimal"}:
            summary = "System health is stable with no critical degradation detected."
        elif health_state in {"watch", "caution"}:
            summary = "Evolution layer recommends caution during lower-conviction periods."
        else:
            summary = "Evolution layer health is not yet reporting a strong signal."
        if recommendation_count:
            summary += f" {recommendation_count} recommendations are available."
        if success_patterns:
            summary += f" Pattern memory highlights {_first_text(success_patterns)} as a positive structure."
        return {
            "summary": summary.strip(),
            "health_state": health_state,
            "health_score": _safe_float(health.get("health_score"), None),
            "recommendation_count": recommendation_count,
            "top_pattern": _first_text(success_patterns),
        }

    def interpret_command_center(self, command_center, health=None):
        command_center = command_center or {}
        health = health or {}
        global_health = command_center.get("global_health") or command_center.get("health") or {}
        state = _safe_str(global_health.get("global_health_state") or health.get("global_health_state"), "watch")
        score = _safe_float(global_health.get("global_health_score") or health.get("global_health_score"), None)
        warnings = _listify(command_center.get("warnings") or [])
        active_blocks = _listify(command_center.get("active_blocks") or [])
        if state in {"optimal", "healthy"}:
            summary = "Command center is healthy and the execution layers remain responsive."
        elif state in {"watch", "caution"}:
            summary = "Command center is operational but tracking cautionary signals."
        elif state in {"degraded", "critical"}:
            summary = "Command center health is degraded and requires conservative posture."
        else:
            summary = "Command center status is available but not yet strongly classified."
        if warnings:
            summary += f" Warnings: {', '.join(_safe_str(item) for item in warnings[:3])}."
        if active_blocks:
            summary += f" Active blocks: {', '.join(_safe_str(item) for item in active_blocks[:3])}."
        return {
            "summary": summary.strip(),
            "global_health_state": state,
            "global_health_score": score,
            "warnings": warnings[:10],
            "active_blocks": active_blocks[:10],
        }

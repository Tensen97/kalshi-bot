import argparse
import json
import os
import time
from runtime.paths import ensure_runtime_layout, record_persist_event, runtime_env_path, runtime_state_file, safe_atomic_json_write
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


REALM_XX_ENABLE = (os.environ.get("REALM_XX_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XX_SHADOW_ONLY = (os.environ.get("REALM_XX_SHADOW_ONLY", "true") or "true").strip().lower() in {"1", "true", "yes", "on"}
REALM_XX_COUNCIL_INTERVAL_SECONDS = float(os.environ.get("REALM_XX_COUNCIL_INTERVAL_SECONDS", "30") or 30)
REALM_XX_AGENT_HEARTBEAT_MAX_AGE_SECONDS = float(os.environ.get("REALM_XX_AGENT_HEARTBEAT_MAX_AGE_SECONDS", "120") or 120)
REALM_XX_MAX_AGENT_HISTORY = int(float(os.environ.get("REALM_XX_MAX_AGENT_HISTORY", "100") or 100))
REALM_XX_ALLOW_LIVE_INFLUENCE = (os.environ.get("REALM_XX_ALLOW_LIVE_INFLUENCE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XX_STATE_FILE = runtime_env_path("REALM_XX_STATE_FILE", runtime_state_file("distributed_agents.json"))


AGENT_ROLE_DEFINITIONS = {
    "market_scanner_agent": {
        "role": "market_scanner_agent",
        "title": "Market Scanner",
        "description": "Observes market families and surfaces opportunities.",
        "advisory_only": True,
    },
    "risk_guardian_agent": {
        "role": "risk_guardian_agent",
        "title": "Risk Guardian",
        "description": "Monitors exposure, drawdown, concentration, and volatility.",
        "advisory_only": True,
    },
    "simulation_agent": {
        "role": "simulation_agent",
        "title": "Simulation Agent",
        "description": "Summarizes Realm XV replay behavior and profile outcomes.",
        "advisory_only": True,
    },
    "strategy_evolution_agent": {
        "role": "strategy_evolution_agent",
        "title": "Strategy Evolution",
        "description": "Reports Realm XVII candidates and lineage health.",
        "advisory_only": True,
    },
    "portfolio_brain_agent": {
        "role": "portfolio_brain_agent",
        "title": "Portfolio Brain",
        "description": "Summarizes Realm XVIII portfolio posture.",
        "advisory_only": True,
    },
    "seraph_reporter_agent": {
        "role": "seraph_reporter_agent",
        "title": "SERAPH Reporter",
        "description": "Formats ecosystem state for Realm XIX interface layers.",
        "advisory_only": True,
    },
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value, digits=4, default=None):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return default


def _ensure_dir(path):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def _safe_json(value):
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [_safe_json(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _safe_json(value.to_dict())
        except Exception:
            return str(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _compact_list(values, limit=5):
    return list(values or [])[: max(0, int(limit))]


@dataclass
class DistributedAgent:
    agent_id: str
    agent_name: str
    agent_role: str
    status: str = "healthy"
    last_heartbeat: str = field(default_factory=_now_iso)
    last_summary: dict = field(default_factory=dict)
    confidence: float = 0.5
    warnings: list = field(default_factory=list)
    enabled: bool = True
    advisory_only: bool = True
    last_seen_ts: float = field(default_factory=time.time)

    def to_dict(self):
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "status": self.status,
            "last_heartbeat": self.last_heartbeat,
            "last_summary": _safe_json(self.last_summary),
            "confidence": _round(self.confidence, 4, 0.5),
            "warnings": list(self.warnings or []),
            "enabled": bool(self.enabled),
            "advisory_only": bool(self.advisory_only),
            "last_seen_ts": _round(self.last_seen_ts, 4, 0.0),
        }


class DistributedIntelligenceCouncil:
    def __init__(
        self,
        state_file=REALM_XX_STATE_FILE,
        enabled=REALM_XX_ENABLE,
        shadow_only=REALM_XX_SHADOW_ONLY,
        council_interval_seconds=REALM_XX_COUNCIL_INTERVAL_SECONDS,
        heartbeat_max_age_seconds=REALM_XX_AGENT_HEARTBEAT_MAX_AGE_SECONDS,
        max_agent_history=REALM_XX_MAX_AGENT_HISTORY,
        allow_live_influence=REALM_XX_ALLOW_LIVE_INFLUENCE,
    ):
        self.state_file = state_file
        self.enabled = bool(enabled)
        self.shadow_only = bool(shadow_only)
        self.council_interval_seconds = max(10.0, float(council_interval_seconds))
        self.heartbeat_max_age_seconds = max(30.0, float(heartbeat_max_age_seconds))
        self.max_agent_history = max(10, int(max_agent_history))
        self.allow_live_influence = bool(allow_live_influence)
        self.started_at = time.time()
        self.last_update_ts = 0.0
        self.last_report = {}
        self.last_council_signature = None
        self._history = deque(maxlen=self.max_agent_history)
        ensure_runtime_layout()
        self._state = self._load_state()
        self._ensure_seed_agents()
        self._persist_state()

    def _log(self, prefix, **fields):
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        print(f"{prefix} {extras}".strip())

    def _default_state(self):
        return {
            "schema_version": 1,
            "updated_at": None,
            "agents": {},
            "history": [],
            "council": {},
            "last_summary": {},
        }

    def _load_state(self):
        if not os.path.exists(self.state_file):
            return self._default_state()
        try:
            with open(self.state_file, "r") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                return self._default_state()
        except Exception:
            return self._default_state()
        state = self._default_state()
        state.update(payload)
        if not isinstance(state.get("agents"), dict):
            state["agents"] = {}
        if not isinstance(state.get("history"), list):
            state["history"] = []
        if not isinstance(state.get("council"), dict):
            state["council"] = {}
        if not isinstance(state.get("last_summary"), dict):
            state["last_summary"] = {}
        return state

    def _persist_state(self):
        try:
            _ensure_dir(self.state_file)
            self._state["updated_at"] = _now_iso()
            ok = safe_atomic_json_write(self.state_file, _safe_json(self._state), sort_keys=True, indent=2)
            record_persist_event(subsystem="REALM-XX", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed", details={"agents": len(self._state.get("agents", {}) or {})})
            if not ok:
                self._log("[REALM-XX-DEGRADED]", reason="write_failed", action="persist_state")
            return bool(ok)
        except Exception as exc:
            record_persist_event(subsystem="REALM-XX", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__)
            self._log("[REALM-XX-DEGRADED]", reason=exc.__class__.__name__, action="persist_state")
            return False

    def _seed_agent(self, agent_id):
        role = AGENT_ROLE_DEFINITIONS.get(agent_id, {})
        agent = DistributedAgent(
            agent_id=agent_id,
            agent_name=role.get("title", agent_id),
            agent_role=role.get("role", agent_id),
            status="healthy",
            confidence=0.5,
            warnings=[],
            enabled=True,
            advisory_only=bool(role.get("advisory_only", True)),
        )
        self._state["agents"][agent_id] = agent.to_dict()
        self._log("[REALM-XX-AGENT-REGISTER]", agent_id=agent_id, agent_name=agent.agent_name, agent_role=agent.agent_role, advisory_only="yes")

    def _ensure_seed_agents(self):
        for agent_id in AGENT_ROLE_DEFINITIONS:
            if agent_id not in self._state["agents"]:
                self._seed_agent(agent_id)

    def _record_history(self, entry):
        self._history.appendleft(entry)
        self._state["history"] = list(self._history)[: self.max_agent_history]

    def _heartbeat(self, agent_id, summary=None, confidence=0.5, warnings=None, status="healthy"):
        agent = self._state["agents"].get(agent_id)
        if not isinstance(agent, dict):
            self._seed_agent(agent_id)
            agent = self._state["agents"].get(agent_id, {})
        now_iso = _now_iso()
        agent.update(
            {
                "status": status,
                "last_heartbeat": now_iso,
                "last_summary": _safe_json(summary or {}),
                "confidence": _round(confidence, 4, 0.5),
                "warnings": list(warnings or []),
                "enabled": True,
                "advisory_only": True,
                "last_seen_ts": time.time(),
            }
        )
        self._state["agents"][agent_id] = agent
        self._record_history({"agent_id": agent_id, "heartbeat": now_iso, "confidence": _round(confidence, 4, 0.5), "warnings": list(warnings or [])})
        self._log("[REALM-XX-AGENT-HEARTBEAT]", agent_id=agent_id, agent_role=agent.get("agent_role"), status=status, confidence=_round(confidence, 4, 0.5))
        self._log("[REALM-XX-AGENT-STATUS]", agent_id=agent_id, enabled="yes", advisory_only="yes", warnings=len(list(warnings or [])))
        return agent

    def _is_recent(self, agent):
        last_seen_ts = _safe_float(agent.get("last_seen_ts"), 0.0) or 0.0
        return (time.time() - last_seen_ts) <= self.heartbeat_max_age_seconds

    def _role_summary(self, bot, agent_id):
        if agent_id == "market_scanner_agent":
            xvi = dict(getattr(bot, "last_realm_xvi_summary", {}) or {})
            market = {
                "regime": xvi.get("regime_label") or xvi.get("dominant_regime") or "unknown",
                "strongest_correlations": _compact_list((xvi.get("relationships") or {}).keys(), limit=5),
                "family_profiles": _compact_list((xvi.get("family_profiles") or {}).keys(), limit=5),
                "high_risk_families": _compact_list(xvi.get("high_risk_families"), limit=5),
            }
            warnings = []
            if market["regime"] in {"high_volatility", "unstable_spread_conditions", "choppy"}:
                warnings.append(f"regime:{market['regime']}")
            if market["high_risk_families"]:
                warnings.append("high_risk_families")
            confidence = 0.55 if not warnings else 0.45
            return market, confidence, warnings
        if agent_id == "risk_guardian_agent":
            xviii = dict(getattr(bot, "last_realm_xviii_summary", {}) or {})
            state = {
                "portfolio_health_score": xviii.get("portfolio_health_score", 1.0),
                "portfolio_mode": xviii.get("portfolio_mode", "normal"),
                "family_concentration": xviii.get("family_concentration", 0.0),
                "correlated_concentration": xviii.get("correlated_concentration", 0.0),
                "risk_of_ruin_guard": xviii.get("risk_of_ruin_guard", "clear"),
            }
            warnings = []
            if _safe_float(state["portfolio_health_score"], 1.0) < 0.60:
                warnings.append("portfolio_health_below_caution")
            if _safe_float(state["family_concentration"], 0.0) >= 0.60:
                warnings.append("family_concentration_high")
            if _safe_float(state["correlated_concentration"], 0.0) >= 0.55:
                warnings.append("correlated_exposure_high")
            confidence = _safe_float(state["portfolio_health_score"], 1.0) or 1.0
            return state, confidence, warnings
        if agent_id == "simulation_agent":
            xv = dict(getattr(bot, "last_realm_xv_summary", {}) or {})
            xviii = dict(getattr(bot, "last_realm_xviii_summary", {}) or {})
            summary = {
                "last_replay_status": xv.get("last_replay_status") or xv.get("reason") or "unknown",
                "best_profile": (xv.get("conclusion") or {}).get("best_profile") if isinstance(xv.get("conclusion"), dict) else xv.get("best_profile"),
                "portfolio_overlay": xviii.get("portfolio_mode", "normal"),
            }
            warnings = []
            if summary["last_replay_status"] in {"degraded", "failed"}:
                warnings.append("replay_degraded")
            confidence = 0.50 if not warnings else 0.40
            return summary, confidence, warnings
        if agent_id == "strategy_evolution_agent":
            xvii = dict(getattr(bot, "last_realm_xvii_summary", {}) or {})
            strategies = xvii.get("strategies") or []
            elite = [item for item in strategies if str(item.get("status") or "").strip().lower() == "elite_candidate"]
            rejected = [item for item in strategies if str(item.get("status") or "").strip().lower() == "rejected"]
            summary = {
                "strategies_tested": xvii.get("strategies_tested", len(strategies)),
                "elite_candidates": len(elite),
                "rejected": len(rejected),
                "best_environment": xvii.get("best_performing_environment") or "unknown",
            }
            warnings = []
            if summary["rejected"] and summary["rejected"] > summary["elite_candidates"]:
                warnings.append("more_rejections_than_elites")
            confidence = 0.55 if elite else 0.45
            return summary, confidence, warnings
        if agent_id == "portfolio_brain_agent":
            xviii = dict(getattr(bot, "last_realm_xviii_summary", {}) or {})
            summary = {
                "portfolio_health_score": xviii.get("portfolio_health_score", 1.0),
                "portfolio_mode": xviii.get("portfolio_mode", "normal"),
                "recommendation": xviii.get("recommendation", "continue normal trading"),
                "total_exposure_dollars": xviii.get("total_exposure_dollars", 0.0),
            }
            warnings = []
            if str(summary["portfolio_mode"]).lower() in {"defensive", "risk_off"}:
                warnings.append(f"mode:{summary['portfolio_mode']}")
            confidence = _safe_float(summary["portfolio_health_score"], 1.0) or 1.0
            return summary, confidence, warnings
        if agent_id == "seraph_reporter_agent":
            xix = dict(getattr(bot, "last_seraph_state", {}) or {})
            summary = {
                "summary_ready": bool(xix),
                "state_keys": list(xix.keys())[:10] if isinstance(xix, dict) else [],
                "message": getattr(getattr(bot, "seraph_interface", None), "get_summary_text", lambda: "")(),
            }
            warnings = []
            confidence = 0.70 if xix else 0.40
            return summary, confidence, warnings
        return {}, 0.5, []

    def _council_mode(self, scores, warnings, guardian_veto=False):
        if guardian_veto:
            return "risk_off"
        if any("risk_off" in str(item).lower() for item in warnings):
            return "risk_off"
        if any("defensive" in str(item).lower() for item in warnings):
            return "defensive"
        if scores < 0.60:
            return "cautious"
        if scores < 0.75:
            return "normal"
        return "normal"

    def _build_conflicts(self, agent_outputs):
        conflicts = []
        market_warnings = (agent_outputs.get("market_scanner_agent") or {}).get("warnings", [])
        risk_warnings = (agent_outputs.get("risk_guardian_agent") or {}).get("warnings", [])
        simulation_warnings = (agent_outputs.get("simulation_agent") or {}).get("warnings", [])
        strategy_warnings = (agent_outputs.get("strategy_evolution_agent") or {}).get("warnings", [])
        if market_warnings and risk_warnings:
            conflicts.append({"type": "opportunity_vs_risk", "market": market_warnings, "risk": risk_warnings})
            self._log("[REALM-XX-CONFLICT]", type="opportunity_vs_risk", market=market_warnings, risk=risk_warnings)
        if strategy_warnings and simulation_warnings:
            conflicts.append({"type": "strategy_vs_simulation", "strategy": strategy_warnings, "simulation": simulation_warnings})
            self._log("[REALM-XX-CONFLICT]", type="strategy_vs_simulation", strategy=strategy_warnings, simulation=simulation_warnings)
        return conflicts

    def synthesize_council(self, bot=None, source="live"):
        if not self.enabled:
            self._log("[REALM-XX-DEGRADED]", reason="disabled")
            return self.last_report
        bot = bot or self.bot
        if bot is None:
            self._log("[REALM-XX-DEGRADED]", reason="missing_bot")
            return self.last_report
        agent_outputs = {}
        agent_order = list(AGENT_ROLE_DEFINITIONS.keys())
        for agent_id in agent_order:
            summary, confidence, warnings = self._role_summary(bot, agent_id)
            agent_outputs[agent_id] = self._heartbeat(agent_id, summary=summary, confidence=confidence, warnings=warnings, status="healthy" if confidence >= 0.45 else "watch")
        active_agents = {agent_id: agent for agent_id, agent in self._state["agents"].items() if agent.get("enabled") and self._is_recent(agent)}
        market_conf = _safe_float((agent_outputs.get("market_scanner_agent") or {}).get("confidence"), 0.5) or 0.5
        risk_conf = _safe_float((agent_outputs.get("risk_guardian_agent") or {}).get("confidence"), 0.5) or 0.5
        sim_conf = _safe_float((agent_outputs.get("simulation_agent") or {}).get("confidence"), 0.5) or 0.5
        strategy_conf = _safe_float((agent_outputs.get("strategy_evolution_agent") or {}).get("confidence"), 0.5) or 0.5
        portfolio_conf = _safe_float((agent_outputs.get("portfolio_brain_agent") or {}).get("confidence"), 0.5) or 0.5
        reporter_conf = _safe_float((agent_outputs.get("seraph_reporter_agent") or {}).get("confidence"), 0.5) or 0.5
        xvi = dict(getattr(bot, "last_realm_xvi_summary", {}) or {})
        regime_penalty = 0.0
        if str(xvi.get("regime_label") or xvi.get("dominant_regime") or "").lower() in {"high_volatility", "unstable_spread_conditions", "choppy"}:
            regime_penalty += 0.08
        guardian_veto = bool(getattr(bot, "last_safety_alert_reason", None))
        conflicts = self._build_conflicts(agent_outputs)
        warning_pool = []
        for agent_id, payload in agent_outputs.items():
            warning_pool.extend(list(payload.get("warnings", []) or []))
        if guardian_veto:
            warning_pool.append("guardian_veto")
        if str((getattr(bot, "last_realm_xviii_summary", {}) or {}).get("portfolio_mode") or "").lower() in {"defensive", "risk_off"}:
            warning_pool.append("portfolio_defensive")
        if any("correlated" in str(warning).lower() for warning in warning_pool):
            warning_pool.append("correlated_exposure_warning")
        base_confidence = (
            market_conf * 0.18
            + risk_conf * 0.22
            + sim_conf * 0.14
            + strategy_conf * 0.16
            + portfolio_conf * 0.20
            + reporter_conf * 0.10
        )
        base_confidence -= regime_penalty
        if guardian_veto:
            base_confidence -= 0.12
        if conflicts:
            base_confidence -= min(0.10, 0.02 * len(conflicts))
        council_confidence = max(0.0, min(1.0, base_confidence))
        portfolio_state = dict(getattr(bot, "last_realm_xviii_summary", {}) or {})
        portfolio_mode = str(portfolio_state.get("portfolio_mode") or "normal").strip().lower()
        if guardian_veto:
            council_mode = "risk_off"
        elif portfolio_mode in {"risk_off", "defensive"}:
            council_mode = portfolio_mode
        elif council_confidence < 0.45:
            council_mode = "defensive"
        elif council_confidence < 0.65:
            council_mode = "cautious"
        else:
            council_mode = "normal"
        advisory_reason = "guardian priority" if guardian_veto else "portfolio health" if portfolio_mode != "normal" else "balanced council"
        recommendation = "Continue observing."
        if council_mode == "normal":
            recommendation = "Trade normally, but stay within existing safety limits."
        elif council_mode == "cautious":
            recommendation = "Continue observing. Avoid aggressive entries."
        elif council_mode == "defensive":
            recommendation = "Reduce aggression. Favor portfolio preservation."
        elif council_mode == "risk_off":
            recommendation = "Risk-off mode. Preserve capital and avoid new exposure."
        council_summary = {
            "council_confidence": _round(council_confidence, 4, 0.0),
            "council_mode": council_mode,
            "advisory_reason": advisory_reason,
            "recommendation": recommendation,
            "warnings": list(dict.fromkeys(warning_pool))[:10],
            "conflicts": conflicts,
            "active_agents": [agent for agent in active_agents.values()],
            "agent_outputs": agent_outputs,
            "source": source,
            "updated_at": _now_iso(),
            "guardian_veto": guardian_veto,
            "shadow_only": self.shadow_only,
        }
        self.last_report = council_summary
        self._state["council"] = council_summary
        self._state["last_summary"] = council_summary
        self._record_history({"type": "council", "created_at": council_summary["updated_at"], "mode": council_mode, "confidence": council_summary["council_confidence"]})
        self._log(
            "[REALM-XX-CONFIDENCE]",
            council_confidence=council_summary["council_confidence"],
            council_mode=council_mode,
            advisory_reason=advisory_reason,
        )
        self._log("[REALM-XX-MODE]", council_mode=council_mode, shadow_only="yes" if self.shadow_only else "no", allow_live_influence="yes" if self.allow_live_influence else "no")
        self._log("[REALM-XX-COUNCIL]", mode=council_mode, confidence=council_summary["council_confidence"], warnings=len(council_summary["warnings"]))
        self._log("[REALM-XX-COUNCIL-SUMMARY]", recommendation=recommendation, guardian_veto="yes" if guardian_veto else "no")
        self._log(
            "[REALM-XX-ADVISORY]",
            market=f"{(agent_outputs.get('market_scanner_agent') or {}).get('summary', {})}",
            risk=f"{(agent_outputs.get('risk_guardian_agent') or {}).get('summary', {})}",
            simulation=f"{(agent_outputs.get('simulation_agent') or {}).get('summary', {})}",
            strategy=f"{(agent_outputs.get('strategy_evolution_agent') or {}).get('summary', {})}",
            portfolio=f"{(agent_outputs.get('portfolio_brain_agent') or {}).get('summary', {})}",
        )
        source_label = str(source or "").strip().lower()
        if source_label in {"live", "live_cycle", "startup"}:
            self._log("[REALM-XX-LIVE-ADVISORY]", council_mode=council_mode, council_confidence=council_summary["council_confidence"])
        if source_label.startswith("realm_xv") or source_label.startswith("replay"):
            self._log("[REALM-XX-REPLAY]", source=source, council_mode=council_mode, council_confidence=council_summary["council_confidence"])
            self._log("[REALM-XX-REPLAY-COUNCIL]", recommendation=recommendation, warnings=len(council_summary["warnings"]))
        if guardian_veto:
            self._log("[REALM-XX-GUARDIAN-VETO]", reason=getattr(bot, "last_safety_alert_reason", None))
            self._log("[REALM-XX-GUARDIAN-WINS]", reason=getattr(bot, "last_safety_alert_reason", None))
        if self.shadow_only:
            self._log("[REALM-XX-SHADOW-ONLY]", mode=council_mode)
        self._persist_state()
        self.last_update_ts = time.time()
        return council_summary

    def maybe_refresh(self, bot=None, force=False, source="live"):
        now_ts = time.time()
        if force or (now_ts - float(self.last_update_ts or 0.0) >= self.council_interval_seconds):
            return self.synthesize_council(bot=bot, source=source)
        return self.last_report or self._state.get("council", {})

    def get_registry(self):
        return _safe_json(self._state.get("agents", {}))

    def get_state(self):
        return _safe_json(self._state)

    def build_report_text(self, council=None):
        council = council or self.last_report or self._state.get("council", {})
        agent_rows = []
        for agent_id, agent in (self._state.get("agents", {}) or {}).items():
            status = "healthy" if agent.get("enabled") and self._is_recent(agent) else "stale"
            agent_rows.append(f"- {agent_id}: {status}")
        warnings = council.get("warnings", []) or []
        warning_lines = [f"- {warning}" for warning in warnings[:10]] if warnings else ["- none"]
        lines = [
            "REALM XX - DISTRIBUTED INTELLIGENCE REPORT",
            "",
            "Active Agents:",
            *agent_rows,
            "",
            f"Council Mode: {council.get('council_mode', 'unknown')}",
            f"Council Confidence: {council.get('council_confidence', 0.0)}",
            "Current Warnings:",
            *warning_lines,
            "",
            "Council Recommendation:",
            f"- {council.get('recommendation', 'Continue observing.')}",
            "",
            "SERAPH Summary:",
            f"- {self._seraph_summary(council)}",
        ]
        return "\n".join(lines)

    def _seraph_summary(self, council):
        mode = str(council.get("council_mode") or "normal").strip().lower()
        if mode == "risk_off":
            return "AION is preserving capital under risk-off conditions."
        if mode == "defensive":
            return "AION is cautious and protecting the portfolio."
        if mode == "cautious":
            return "AION is stable, cautious, and monitoring elevated risk."
        return "AION is balanced and observing the current market climate."

    def report_summary(self):
        council = self.maybe_refresh(force=True)
        print()
        print("REALM XX - DISTRIBUTED INTELLIGENCE REPORT")
        print("Active Agents:")
        for agent_id, agent in (self._state.get("agents", {}) or {}).items():
            status = "healthy" if agent.get("enabled") and self._is_recent(agent) else "stale"
            print(f"- {agent_id}: {status}")
        print(f"Council Mode: {council.get('council_mode', 'unknown')}")
        print(f"Council Confidence: {council.get('council_confidence', 0.0)}")
        print("Current Warnings:")
        warnings = council.get("warnings", []) or []
        if warnings:
            for warning in warnings[:10]:
                print(f"- {warning}")
        else:
            print("- none")
        print("Council Recommendation:")
        print(f"- {council.get('recommendation', 'Continue observing.')}")
        print("SERAPH Summary:")
        print(f"- {self._seraph_summary(council)}")
        return council

    def get_response_payload(self, path):
        council = self.maybe_refresh(source="api")
        path = str(path or "/seraph/council").strip().lower()
        if path in {"/seraph/council", "/seraph/distributed"}:
            return {"ok": True, "path": path, "data": council}
        if path == "/seraph/agents":
            return {"ok": True, "path": path, "data": self.get_registry()}
        if path == "/seraph/state":
            return {"ok": True, "path": path, "data": self.get_state()}
        return {"ok": False, "path": path, "reason": "not_found"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--state-file", default=REALM_XX_STATE_FILE)
    args = parser.parse_args()
    council = DistributedIntelligenceCouncil(state_file=args.state_file)
    if args.report:
        council.report_summary()
        return
    council.report_summary()


if __name__ == "__main__":
    main()

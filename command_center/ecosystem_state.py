import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import deque

try:
    from runtime.paths import runtime_state_file
except Exception:
    runtime_state_file = None

try:
    from telemetry.state_registry import get_live_state_registry
except Exception:
    get_live_state_registry = None


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value, digits=4, default=0.0):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return default


def _safe_json(value):
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [_safe_json(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _section(bot, attr_name, default=None):
    value = getattr(bot, attr_name, None) if bot is not None else None
    if isinstance(value, dict):
        return value
    return default or {}


def _extract_top_family(items, key="family"):
    items = list(items or [])
    for item in items:
        if isinstance(item, dict) and item.get(key):
            return str(item.get(key))
    return "none"


@dataclass
class UnifiedEcosystemState:
    generated_at: str = field(default_factory=_now_iso)
    service_status: dict = field(default_factory=dict)
    live_mode: dict = field(default_factory=dict)
    websocket: dict = field(default_factory=dict)
    personalities: dict = field(default_factory=dict)
    probability: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    external: dict = field(default_factory=dict)
    evolution: dict = field(default_factory=dict)
    realm_status_map: dict = field(default_factory=dict)
    execution_posture: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    active_blocks: list = field(default_factory=list)
    suggested_next_actions: list = field(default_factory=list)
    current_market_count: int = 0
    active_subscriptions: int = 0
    health: dict = field(default_factory=dict)
    command_center_health: dict = field(default_factory=dict)
    command_center: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "generated_at": self.generated_at,
            "service_status": _safe_json(self.service_status or {}),
            "live_mode": _safe_json(self.live_mode or {}),
            "websocket": _safe_json(self.websocket or {}),
            "personalities": _safe_json(self.personalities or {}),
            "probability": _safe_json(self.probability or {}),
            "risk": _safe_json(self.risk or {}),
            "external": _safe_json(self.external or {}),
            "evolution": _safe_json(self.evolution or {}),
            "realm_status_map": _safe_json(self.realm_status_map or {}),
            "execution_posture": _safe_json(self.execution_posture or {}),
            "warnings": _safe_json(self.warnings or []),
            "active_blocks": _safe_json(self.active_blocks or []),
            "suggested_next_actions": _safe_json(self.suggested_next_actions or []),
            "current_market_count": int(self.current_market_count or 0),
            "active_subscriptions": int(self.active_subscriptions or 0),
            "health": _safe_json(self.health or {}),
            "command_center_health": _safe_json(self.command_center_health or {}),
            "command_center": _safe_json(self.command_center or {}),
        }


def build_ecosystem_state(bot=None, interface=None, source="live"):
    bot = bot or None
    interface = interface or None
    telemetry_snapshot = {}
    if get_live_state_registry is not None:
        try:
            registry = getattr(bot, "realm_xxix_engine", None) if bot is not None else None
            if registry is None:
                registry = getattr(interface, "live_telemetry_registry", None)
            if registry is None:
                registry = get_live_state_registry(
                    enabled=bool(getattr(interface, "enabled", False) or getattr(interface, "api_enable", False) or getattr(interface, "ui_effective_enable", False)),
                    state_file=getattr(
                        interface,
                        "state_file",
                        runtime_state_file("realm_xxix_live_state.json") if callable(runtime_state_file) else "/opt/kalshi-bot/runtime/state/realm_xxix_live_state.json",
                    ),
                    history_limit=int(getattr(interface, "alert_history_limit", 100) or 100),
                )
            if registry is not None and hasattr(registry, "snapshot"):
                telemetry_snapshot = registry.snapshot() or {}
        except Exception:
            telemetry_snapshot = {}
    live_mode = bool(telemetry_snapshot.get("live_mode", {}).get("active", getattr(bot, "live_mode", False) if bot is not None else False))
    websocket_manager = getattr(bot, "websocket_manager", None) if bot is not None else None
    websocket = dict(telemetry_snapshot.get("websocket_status") or {})
    if websocket_manager is not None:
        websocket = websocket or dict(getattr(bot, "last_websocket_summary", {}) or {})
    personalities = dict(telemetry_snapshot.get("latest_personality_summary") or _section(bot, "last_realm_xxi_summary") or {})
    probability = dict(telemetry_snapshot.get("latest_probability_summary") or _section(bot, "last_realm_xxiii_summary") or {})
    risk = dict(telemetry_snapshot.get("latest_risk_state") or _section(bot, "last_realm_xxiv_summary") or {})
    external = dict(telemetry_snapshot.get("latest_external_summary") or _section(bot, "last_realm_xxv_summary") or {})
    evolution = dict(telemetry_snapshot.get("latest_evolution_summary") or _section(bot, "last_realm_xxvi_summary") or {})
    consciousness = dict(telemetry_snapshot.get("consciousness") or _section(bot, "last_realm_xxx_summary") or {})
    command_center = dict(telemetry_snapshot.get("command_center") or {})
    if interface is not None:
        interface_command_center = dict(getattr(interface, "last_command_center_state", {}) or {})
        if interface_command_center:
            command_center = {**command_center, **interface_command_center}
    live_select_summary = dict(getattr(bot, "last_live_select_summary", {}) or {}) if bot is not None else {}
    websocket_health = str(websocket.get("feed_health") or websocket.get("health") or "unknown").strip().lower()
    stale_feed = bool(websocket.get("stale_feed") or websocket.get("feed_stale"))
    subscription_count = int(_safe_float(websocket.get("active_subscriptions"), 0.0) or 0)
    current_market_count = int(_safe_float(live_select_summary.get("original_market_count"), 0.0) or 0)
    health_state = str(((command_center.get("health") or {}).get("state")) or "watch").strip().lower()
    warnings = []
    active_blocks = []
    if getattr(bot, "last_safety_alert_reason", None):
        warnings.append(f"guardian:{bot.last_safety_alert_reason}")
        active_blocks.append(str(bot.last_safety_alert_reason))
    if websocket_health not in {"healthy", "ok", "good"}:
        warnings.append(f"websocket:{websocket_health}")
    if stale_feed:
        warnings.append("websocket_stale")
    if str((risk.get("last_global_state") or {}).get("capital_state") or "").lower() in {"defensive", "capital_preservation"}:
        warnings.append("risk:defensive")
    if str(external.get("event_risk", {}).get("risk_level") or "NONE").upper() in {"HIGH", "EXTREME"}:
        warnings.append("external:event_risk_high")
    if str((evolution.get("health") or {}).get("health_state") or "").upper() in {"DEGRADED", "UNSTABLE"}:
        warnings.append("evolution:weak")
    realm_status_map = {
        "XX": {
            "websocket_health": websocket_health,
            "stale_feed": stale_feed,
            "subscription_count": subscription_count,
            "latency_ms": telemetry_snapshot.get("websocket_latency"),
        },
        "XXI": {
            "enabled": bool(personalities),
            "top_personality": _extract_top_family(personalities.get("top_families")),
            "weak_personality": _extract_top_family(personalities.get("weak_families")),
        },
        "XXIII": {
            "enabled": bool(probability),
            "edge_activity": len(probability.get("top_families") or []),
            "conviction_status": str(probability.get("final_signal") or probability.get("signal") or "unknown"),
        },
        "XXIV": {
            "risk_state": (risk.get("last_global_state") or {}).get("capital_state", "unknown"),
            "capital_posture": (risk.get("last_global_state") or {}).get("aggression_mode", "unknown"),
            "aggression_level": _round((risk.get("last_global_state") or {}).get("aggression_level"), 4, 0.0),
        },
        "XXV": {
            "external_catalysts": len(external.get("active_catalysts", []) or []),
            "event_risk": (external.get("event_risk") or {}).get("risk_level", "NONE"),
        },
        "XXVI": {
            "ecosystem_health": (evolution.get("health") or {}).get("health_state", "unknown"),
            "recommendation_count": len(evolution.get("recommendations", []) or []),
        },
        "XXVII": {
            "global_health": (command_center.get("health") or command_center.get("global_health") or {}).get("global_health_state", "unknown"),
            "command_router": "read_only",
        },
        "XXX": {
            "narrative_state": consciousness.get("readiness", "monitoring"),
            "operational_summary": consciousness.get("summary", "unavailable"),
        },
    }
    execution_posture = {
        "live_mode": live_mode,
        "portfolio_mode": (risk.get("last_global_state") or {}).get("capital_state") or "normal",
        "risk_mode": (risk.get("last_global_state") or {}).get("aggression_mode") or "normal",
        "probability_signal": probability.get("final_signal") or "weak",
        "council_mode": (command_center.get("council", {}) or {}).get("council_mode", "unknown"),
        "command_center_mode": command_center.get("council_mode", "unknown"),
        "current_phase": telemetry_snapshot.get("current_phase", "unknown"),
        "latest_selected_ticker": telemetry_snapshot.get("latest_selected_ticker"),
        "latest_execution_block_reason": telemetry_snapshot.get("latest_execution_block_reason"),
    }
    suggested_next_actions = [
        "continue_scanning",
        "monitor_websocket_health" if stale_feed else "websocket_healthy",
        "respect_existing_safety_limits",
    ]
    if warnings:
        suggested_next_actions.insert(0, "maintain_caution")
    if any("risk" in warning for warning in warnings):
        suggested_next_actions.insert(0, "favor_capital_preservation")
    command_center_health = {
        "state": "OPTIMAL",
        "score": 0.85,
        "notes": [],
    }
    state = UnifiedEcosystemState(
        generated_at=_now_iso(),
        service_status={
            "service_active": bool(bot is not None),
            "enabled": bool(getattr(interface, "enabled", False)),
            "source": source,
        },
        live_mode={
            "active": live_mode,
            "armed": bool(telemetry_snapshot.get("armed_live", getattr(bot, "live_armed", False)) if bot is not None else False),
            "env_confirmed": bool(getattr(bot, "live_env_confirmed", False)) if bot is not None else False,
            "posture": execution_posture.get("portfolio_mode", "normal"),
            "current_phase": execution_posture.get("current_phase", "startup"),
        },
        websocket={
            "summary": websocket,
            "health": websocket_health,
            "stale_feed": stale_feed,
            "active_subscriptions": subscription_count,
            "connection_state": websocket.get("connection_state") or websocket.get("status") or websocket_health,
            "latency_ms": telemetry_snapshot.get("websocket_latency"),
        },
        personalities=personalities,
        probability=probability,
        risk=risk,
        external=external,
        evolution=evolution,
        realm_status_map=realm_status_map,
        execution_posture=execution_posture,
        warnings=list(dict.fromkeys(warnings))[:10],
        active_blocks=list(dict.fromkeys(active_blocks))[:10],
        suggested_next_actions=suggested_next_actions[:10],
        current_market_count=current_market_count,
        active_subscriptions=subscription_count,
        health={
            "summary": "healthy" if websocket_health in {"healthy", "ok", "good"} else "watch",
            "portfolio_health_score": _safe_float((risk.get("last_global_state") or {}).get("portfolio_health_score"), 1.0),
            "alert_count": len(warnings),
        },
        command_center_health=command_center_health,
        command_center=command_center,
    )
    rendered = state.to_dict()
    rendered["telemetry"] = telemetry_snapshot or {}
    rendered["status"] = telemetry_snapshot.get("status", "waiting") if telemetry_snapshot else "waiting"
    rendered["consciousness"] = _safe_json(consciousness or {})
    rendered["operational_summary"] = consciousness.get("summary", "")
    return rendered

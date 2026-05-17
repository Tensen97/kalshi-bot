from __future__ import annotations

from copy import deepcopy

from .state_registry import get_live_state_registry


def build_hydrated_seraph_state(bot=None, interface=None, source="api"):
    registry = get_live_state_registry(
        enabled=bool(getattr(interface, "enabled", False) or getattr(interface, "api_enable", False) or getattr(interface, "ui_effective_enable", False)),
        state_file=getattr(interface, "state_file", "/opt/kalshi-bot/data/realm_xxix_live_state.json"),
        history_limit=int(getattr(interface, "alert_history_limit", 100) or 100),
    )
    snapshot = registry.snapshot()
    state = deepcopy(snapshot)
    if bot is not None:
        state.setdefault("service_status", {})
        state["service_status"].update(
            {
                "service_active": True,
                "bot_started_at": getattr(bot, "bot_started_iso", None),
                "live_mode": bool(getattr(bot, "live_mode", False)),
                "live_armed": bool(getattr(bot, "live_armed", False)),
            }
        )
    if not state.get("health"):
        state["health"] = snapshot.get("health", {})
    if not state.get("status"):
        state["status"] = snapshot.get("status", "waiting")
    return state


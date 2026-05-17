import json
import os
import time
from runtime.paths import record_persist_event, runtime_env_path, runtime_state_file, safe_atomic_json_write, ensure_runtime_layout
from urllib.parse import parse_qs, urlparse

from .command_router import CommandRouter
from .ecosystem_state import build_ecosystem_state
from .health_aggregator import HealthAggregator
from .summary_builder import SummaryBuilder


REALM_XXVII_ENABLE = (os.environ.get("REALM_XXVII_ENABLE", "false") or "false").strip().lower() in {"1", "true", "yes", "on"}
REALM_XXVII_STATE_FILE = runtime_env_path("REALM_XXVII_STATE_FILE", runtime_state_file("realm_xxvii_command_center.json"))
REALM_XXVII_SUMMARY_INTERVAL_SECONDS = float(os.environ.get("REALM_XXVII_SUMMARY_INTERVAL_SECONDS", "60") or 60)


def _ensure_dir(path):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


class SeraphCommandCenterBridge:
    def __init__(
        self,
        bot=None,
        interface=None,
        enabled=REALM_XXVII_ENABLE,
        state_file=REALM_XXVII_STATE_FILE,
        summary_interval_seconds=REALM_XXVII_SUMMARY_INTERVAL_SECONDS,
    ):
        self.bot = bot
        self.interface = interface
        self.enabled = bool(enabled)
        self.state_file = state_file
        self.summary_interval_seconds = max(15.0, float(summary_interval_seconds))
        self.started_at = time.time()
        self.last_update_ts = 0.0
        self.last_state = {}
        self.last_summary_text = ""
        self.health_aggregator = HealthAggregator()
        self.summary_builder = SummaryBuilder()
        ensure_runtime_layout()
        self.command_router = CommandRouter(logger=self._log)
        if self.bot is not None:
            setattr(self.bot, "realm_xxvii_engine", self)
            setattr(self.bot, "last_realm_xxvii_summary", {})
        self._persist_state(self.get_state())
        self._log(
            "[REALM-XXVII-STATE]",
            enabled="yes" if self.enabled else "no",
            state_file=self.state_file,
            summary_interval_seconds=self.summary_interval_seconds,
        )

    def _log(self, prefix, **fields):
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        print(f"{prefix} {extras}".strip())

    def _persist_state(self, state):
        try:
            _ensure_dir(self.state_file)
            ok = safe_atomic_json_write(self.state_file, _json_safe(state), sort_keys=True, indent=2)
            record_persist_event(subsystem="REALM-XXVII", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed", details={"enabled": self.enabled})
            if not ok:
                self._log("[REALM-XXVII-DEGRADED]", reason="write_failed", action="persist_state", state_file=self.state_file)
        except Exception as exc:
            record_persist_event(subsystem="REALM-XXVII", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__, details={"enabled": self.enabled})
            self._log("[REALM-XXVII-DEGRADED]", reason=exc.__class__.__name__, action="persist_state", state_file=self.state_file)

    def collect_state(self, bot=None, source="live"):
        bot = bot or self.bot
        interface = self.interface
        if bot is None:
            state = build_ecosystem_state(bot=None, interface=interface, source=source)
        else:
            state = build_ecosystem_state(bot=bot, interface=interface, source=source)
        command_center_health = self.health_aggregator.compute(state)
        state["command_center_health"] = command_center_health
        state["command_center"] = {
            "enabled": bool(self.enabled),
            "state_file": self.state_file,
            "summary_interval_seconds": self.summary_interval_seconds,
            "global_health": command_center_health,
            "summary_text": self.summary_builder.build({**state, "command_center_health": command_center_health}),
        }
        state["summary_text"] = state["command_center"]["summary_text"]
        state["health"]["summary"] = command_center_health.get("global_health_state", state["health"].get("summary"))
        state["health"]["global_health_score"] = command_center_health.get("global_health_score")
        state["health"]["global_health_state"] = command_center_health.get("global_health_state")
        state["health"]["command_center_notes"] = command_center_health.get("notes", [])
        self._log(
            "[GLOBAL-HEALTH]",
            global_health_score=command_center_health.get("global_health_score"),
            global_health_state=command_center_health.get("global_health_state"),
        )
        self._log(
            "[GLOBAL-HEALTH-SUMMARY]",
            notes=len(command_center_health.get("notes", []) or []),
            active_blocks=len(state.get("active_blocks", []) or []),
            warnings=len(state.get("warnings", []) or []),
        )
        self._log(
            "[COMMAND-CENTER-STATE]",
            global_health_score=command_center_health.get("global_health_score"),
            global_health_state=command_center_health.get("global_health_state"),
            warnings=len(state.get("warnings", []) or []),
        )
        self._log(
            "[ECOSYSTEM-STATE]",
            live_mode=state.get("live_mode", {}).get("active"),
            websocket_health=state.get("websocket", {}).get("health"),
            risk_state=(state.get("risk", {}) or {}).get("last_global_state", {}).get("capital_state"),
            evolution_health=(state.get("evolution", {}) or {}).get("health", {}).get("health_state"),
        )
        self._log(
            "[SERAPH-STATE-AGGREGATE]",
            active_subscriptions=state.get("active_subscriptions"),
            current_market_count=state.get("current_market_count"),
            execution_posture=state.get("execution_posture", {}).get("portfolio_mode"),
        )
        self._log(
            "[REALM-STATUS-MAP]",
            realm_xx=state.get("realm_status_map", {}).get("XX", {}),
            realm_xxi=state.get("realm_status_map", {}).get("XXI", {}),
            realm_xxiii=state.get("realm_status_map", {}).get("XXIII", {}),
            realm_xxiv=state.get("realm_status_map", {}).get("XXIV", {}),
            realm_xxv=state.get("realm_status_map", {}).get("XXV", {}),
            realm_xxvi=state.get("realm_status_map", {}).get("XXVI", {}),
            realm_xxvii=state.get("realm_status_map", {}).get("XXVII", {}),
        )
        self.last_state = state
        self.last_summary_text = state.get("summary_text", "")
        self.last_update_ts = time.time()
        if self.bot is not None:
            setattr(self.bot, "last_realm_xxvii_summary", state)
        self._persist_state(state)
        return state

    def maybe_refresh(self, bot=None, force=False, source="live"):
        now_ts = time.time()
        if force or not self.last_state or (now_ts - float(self.last_update_ts or 0.0) >= self.summary_interval_seconds):
            return self.collect_state(bot=bot, source=source)
        return self.last_state

    def get_state(self):
        return _json_safe(self.last_state or {})

    def get_summary(self):
        state = self.maybe_refresh(force=False)
        return {
            "summary_text": self.last_summary_text or state.get("summary_text") or self.summary_builder.build(state),
            "health": state.get("command_center_health", {}),
            "warnings": state.get("warnings", []),
            "active_blocks": state.get("active_blocks", []),
            "execution_posture": state.get("execution_posture", {}),
            "suggested_next_actions": state.get("suggested_next_actions", []),
        }

    def get_health(self):
        state = self.maybe_refresh(force=False)
        return _json_safe(state.get("command_center_health", {}))

    def route_command(self, command, context=None):
        state = self.maybe_refresh(force=False)
        context = context or {}
        self._log("[COMMAND-ROUTER]", command=command, enabled="yes" if self.enabled else "no")
        result = self.command_router.route(command, {
            **state,
            "summary_text": state.get("summary_text"),
            "summary": state.get("summary_text"),
        })
        self._persist_state(state)
        return {
            "ok": bool(result.get("ok")),
            "command": command,
            "context": _json_safe(context),
            "result": _json_safe(result),
            "timestamp": state.get("generated_at"),
            "state": self.get_state(),
        }

    def maybe_emit_summary(self, force=False, source="live"):
        state = self.maybe_refresh(force=force, source=source)
        summary = self.get_summary()
        self._log(
            "[SUMMARY-BUILDER]",
            global_health_state=summary.get("health", {}).get("global_health_state"),
            global_health_score=summary.get("health", {}).get("global_health_score"),
        )
        self._log(
            "[SERAPH-SUMMARY]",
            summary=summary.get("summary_text", "")[:160],
        )
        self._log(
            "[COMMAND-CENTER-SUMMARY]",
            global_health_state=summary.get("health", {}).get("global_health_state"),
            warnings=len(summary.get("warnings", []) or []),
            next_actions=len(summary.get("suggested_next_actions", []) or []),
        )
        self._log(
            "[SERAPH-BRIDGE-SUMMARY]",
            execution_posture=summary.get("execution_posture", {}).get("portfolio_mode"),
            command_center_health=summary.get("health", {}).get("global_health_state"),
        )
        self._log(
            "[REALM-XXVII-STATE]",
            enabled="yes" if self.enabled else "no",
            global_health_state=summary.get("health", {}).get("global_health_state"),
        )
        return summary

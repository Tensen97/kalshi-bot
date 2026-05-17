from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone

from .health_snapshot import compute_global_health_snapshot
from .live_state import LiveTelemetryState


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            return str(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _ensure_dir(path):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


class LiveTelemetryRegistry:
    def __init__(self, enabled=False, state_file="/opt/kalshi-bot/data/realm_xxix_live_state.json", history_limit=100):
        self.enabled = bool(enabled)
        self.state_file = str(state_file or "/opt/kalshi-bot/data/realm_xxix_live_state.json")
        self.history_limit = max(10, int(history_limit or 100))
        self._lock = threading.RLock()
        self._state = LiveTelemetryState()
        self._history = []
        self._last_summary_ts = 0.0
        self._summary_interval_seconds = 60.0

    def _log(self, prefix, **fields):
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        print(f"{prefix} {extras}".strip())

    def _persist(self):
        try:
            _ensure_dir(self.state_file)
            tmp_path = f"{self.state_file}.tmp"
            with open(tmp_path, "w") as handle:
                json.dump(self.snapshot(), handle, sort_keys=True, indent=2)
            os.replace(tmp_path, self.state_file)
        except Exception as exc:
            self._log("[REALM-XXIX-DEGRADED]", reason=exc.__class__.__name__, action="persist")

    def _update_history(self, entry):
        self._history.insert(0, entry)
        if len(self._history) > self.history_limit:
            del self._history[self.history_limit :]

    def snapshot(self):
        with self._lock:
            state = deepcopy(asdict(self._state))
            state["health"] = compute_global_health_snapshot(state)
            state["status"] = state.get("status") or "waiting"
            state["history"] = deepcopy(self._history[: self.history_limit])
            return _json_safe(state)

    def update_many(self, payload=None, source="live"):
        payload = payload if isinstance(payload, dict) else {}
        with self._lock:
            changed = []
            for key, value in payload.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, deepcopy(value))
                    changed.append(key)
            self._state.last_updated = payload.get("last_updated") or _now_iso()
            self._state.last_source = source
            self._state.health = compute_global_health_snapshot(asdict(self._state))
            self._state.status = payload.get("status") or self._state.status or "live"
            snapshot = self.snapshot()
            self._update_history({
                "event_type": "update",
                "source": source,
                "changed": changed,
                "created_at": self._state.last_updated,
            })
            self._log(
                "[LIVE-STATE-UPDATE]",
                source=source,
                changed=len(changed),
                status=snapshot.get("status"),
            )
            self._log(
                "[LIVE-STATE-SNAPSHOT]",
                status=snapshot.get("status"),
                health_score=snapshot.get("health", {}).get("global_health_score"),
                last_updated=snapshot.get("last_updated"),
            )
            self._persist()
            return snapshot

    def update_section(self, section, data=None, source="live"):
        payload = {section: _json_safe(data or {})}
        return self.update_many(payload, source=source)

    def merge_fields(self, source="live", **fields):
        return self.update_many(fields, source=source)

    def record_missing(self, field_name, source="live"):
        self._log("[LIVE-STATE-MISSING]", field=field_name, source=source)

    def maybe_emit_summary(self, force=False, source="live"):
        now_ts = time.time()
        if force or now_ts - float(self._last_summary_ts or 0.0) >= self._summary_interval_seconds:
            self._last_summary_ts = now_ts
            snapshot = self.snapshot()
            self._log(
                "[REALM-XXIX-STATE]",
                status=snapshot.get("status"),
                active_realms=len(snapshot.get("active_realms") or []),
                websocket_status=(snapshot.get("websocket_status") or {}).get("health"),
                health_score=(snapshot.get("health") or {}).get("global_health_score"),
            )
            return snapshot
        return self.snapshot()


_REGISTRY = None
_REGISTRY_LOCK = threading.RLock()


def get_live_state_registry(enabled=False, state_file="/opt/kalshi-bot/data/realm_xxix_live_state.json", history_limit=100):
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = LiveTelemetryRegistry(enabled=enabled, state_file=state_file, history_limit=history_limit)
        else:
            _REGISTRY.enabled = bool(enabled or _REGISTRY.enabled)
            if state_file:
                _REGISTRY.state_file = str(state_file)
            if history_limit:
                _REGISTRY.history_limit = max(10, int(history_limit))
        return _REGISTRY

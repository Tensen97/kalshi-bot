from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List
from runtime.paths import ensure_runtime_layout, record_persist_event, runtime_state_file, safe_atomic_json_write


class ExternalContextMemory:
    def __init__(self, state_file: str | None = None, history_limit: int = 200):
        self.state_file = str(state_file or runtime_state_file("external_context_memory.json"))
        ensure_runtime_layout()
        self.history_limit = max(20, int(history_limit or 200))
        self._lock = threading.RLock()
        self.state = {"updated_at": None, "contexts": [], "catalysts": [], "families": {}, "risk_windows": {}}
        self._load()

    def _load(self):
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                self.state.update(payload)
        except Exception:
            return

    def _save(self):
        try:
            ok = safe_atomic_json_write(self.state_file, self.state, indent=2, sort_keys=True)
            record_persist_event(subsystem="REALM-XXV-CONTEXT", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed")
        except Exception as exc:
            record_persist_event(subsystem="REALM-XXV-CONTEXT", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__)
            return

    def record(self, item: Dict[str, Any]):
        if not isinstance(item, dict):
            return
        with self._lock:
            item = dict(item)
            item.setdefault("ts", time.time())
            self.state.setdefault("contexts", []).append(item)
            self.state["contexts"] = self.state["contexts"][-self.history_limit :]
            if item.get("kind") == "catalyst":
                self.state.setdefault("catalysts", []).append(item)
                self.state["catalysts"] = self.state["catalysts"][-self.history_limit :]
            family = str(item.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"
            self.state.setdefault("families", {}).setdefault(family, {"count": 0})
            self.state["families"][family]["count"] += 1
            self.state["updated_at"] = item["ts"]
            self._save()

    def summarize(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "updated_at": self.state.get("updated_at"),
                "contexts": list(self.state.get("contexts", [])[-20:]),
                "catalysts": list(self.state.get("catalysts", [])[-20:]),
                "families": dict(self.state.get("families", {})),
                "risk_windows": dict(self.state.get("risk_windows", {})),
            }


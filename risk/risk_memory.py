from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict
from runtime.paths import record_persist_event, runtime_state_file, safe_atomic_json_write, ensure_runtime_layout


class RiskMemory:
    def __init__(self, state_file: str | None = None, history_limit: int = 400):
        self.state_file = str(state_file or runtime_state_file("realm_xxiv_risk_memory.json"))
        ensure_runtime_layout()
        self.history_limit = max(50, int(history_limit or 400))
        self._lock = threading.Lock()
        self.state = {
            "version": 1,
            "updated_at": None,
            "events": [],
            "family_stats": {},
            "regime_stats": {},
        }
        self._load()

    def _load(self):
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                self.state.update(payload)
                self.state.setdefault("events", [])
                self.state.setdefault("family_stats", {})
                self.state.setdefault("regime_stats", {})
        except Exception:
            return

    def _save(self):
        try:
            ok = safe_atomic_json_write(self.state_file, self.state, indent=2, sort_keys=True)
            record_persist_event(subsystem="REALM-XXIV-MEMORY", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed")
        except Exception as exc:
            record_persist_event(subsystem="REALM-XXIV-MEMORY", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__)
            return

    def record(self, event: Dict[str, Any], persist: bool = False):
        if not isinstance(event, dict):
            return
        now_ts = time.time()
        entry = dict(event)
        entry.setdefault("ts", now_ts)
        entry.setdefault("capital_state", "NORMAL")
        entry.setdefault("aggression_level", 0.35)
        entry.setdefault("drawdown_state", "stable")
        entry.setdefault("exposure_state", "stable")
        with self._lock:
            events = self.state.setdefault("events", [])
            events.append(entry)
            if len(events) > self.history_limit:
                self.state["events"] = events[-self.history_limit :]
            family = str(entry.get("family") or "UNKNOWN").strip() or "UNKNOWN"
            family_stats = self.state.setdefault("family_stats", {})
            stats = family_stats.setdefault(
                family,
                {
                    "observations": 0,
                    "capital_states": {},
                    "drawdown_states": {},
                    "exposure_states": {},
                    "aggression_total": 0.0,
                    "last_seen_at": None,
                },
            )
            stats["observations"] += 1
            stats["aggression_total"] += float(entry.get("aggression_level", 0.35) or 0.35)
            stats["capital_states"][entry.get("capital_state", "NORMAL")] = stats["capital_states"].get(entry.get("capital_state", "NORMAL"), 0) + 1
            stats["drawdown_states"][entry.get("drawdown_state", "stable")] = stats["drawdown_states"].get(entry.get("drawdown_state", "stable"), 0) + 1
            stats["exposure_states"][entry.get("exposure_state", "stable")] = stats["exposure_states"].get(entry.get("exposure_state", "stable"), 0) + 1
            stats["last_seen_at"] = now_ts
            regime = str(entry.get("personality_regime") or "CHOPPY")
            regime_stats = self.state.setdefault("regime_stats", {})
            regime_stats[regime] = regime_stats.get(regime, 0) + 1
            self.state["updated_at"] = now_ts
            if persist:
                self._save()

    def summarize(self) -> Dict[str, Any]:
        with self._lock:
            events = list(self.state.get("events", []))
            family_stats = dict(self.state.get("family_stats", {}))
            regime_stats = dict(self.state.get("regime_stats", {}))
        top_families = []
        weak_families = []
        for family, stats in family_stats.items():
            observations = int(stats.get("observations", 0) or 0)
            top_families.append(
                {
                    "family": family,
                    "observations": observations,
                    "average_aggression": round(float(stats.get("aggression_total", 0.0) or 0.0) / float(max(1, observations)), 4),
                    "capital_states": stats.get("capital_states", {}) or {},
                    "drawdown_states": stats.get("drawdown_states", {}) or {},
                }
            )
            weak_families.append(
                {
                    "family": family,
                    "observations": observations,
                    "defensive_rate": round(float(stats.get("capital_states", {}).get("DEFENSIVE", 0) + stats.get("capital_states", {}).get("CAPITAL_PRESERVATION", 0)) / float(max(1, observations)), 4),
                }
            )
        top_families.sort(key=lambda item: (item["average_aggression"], item["observations"]), reverse=True)
        weak_families.sort(key=lambda item: (item["defensive_rate"], item["observations"]), reverse=True)
        return {
            "total_events": len(events),
            "recent_events": events[-20:],
            "family_stats": family_stats,
            "regime_stats": regime_stats,
            "top_families": top_families[:5],
            "weak_families": weak_families[:5],
            "updated_at": self.state.get("updated_at"),
        }

    def persist(self):
        with self._lock:
            self.state["updated_at"] = time.time()
            self._save()


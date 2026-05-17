from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List
from runtime.paths import record_persist_event, runtime_state_file, safe_atomic_json_write, ensure_runtime_layout


class PersonalityMemory:
    def __init__(self, state_file: str | None = None, history_limit: int = 300):
        self.state_file = str(state_file or runtime_state_file("realm_xxi_personality_memory.json"))
        ensure_runtime_layout()
        self.history_limit = max(50, int(history_limit or 300))
        self._lock = threading.Lock()
        self.state = {
            "version": 1,
            "updated_at": None,
            "events": [],
            "family_stats": {},
            "regime_counts": {},
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
                self.state.setdefault("regime_counts", {})
        except Exception:
            return

    def _save(self):
        try:
            ok = safe_atomic_json_write(self.state_file, self.state, indent=2, sort_keys=True)
            record_persist_event(subsystem="REALM-XXI-MEMORY", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed")
        except Exception as exc:
            record_persist_event(subsystem="REALM-XXI-MEMORY", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__)
            return

    def record(self, event: Dict[str, Any], persist: bool = False):
        if not isinstance(event, dict):
            return
        now_ts = time.time()
        normalized = dict(event)
        normalized.setdefault("ts", now_ts)
        normalized.setdefault("family", "UNKNOWN")
        normalized.setdefault("regime", "CHOPPY")
        normalized.setdefault("personality_type", "balanced")
        normalized.setdefault("result", "observed")
        normalized.setdefault("priority_score", 0.5)
        normalized.setdefault("signal_reliability", 0.5)
        normalized.setdefault("spread", None)
        normalized.setdefault("liquidity", None)
        with self._lock:
            events = self.state.setdefault("events", [])
            events.append(normalized)
            if len(events) > self.history_limit:
                self.state["events"] = events[-self.history_limit :]
            family = str(normalized.get("family") or "UNKNOWN").strip() or "UNKNOWN"
            family_stats = self.state.setdefault("family_stats", {})
            stats = family_stats.setdefault(
                family,
                {
                    "observations": 0,
                    "entry_ready": 0,
                    "blocked": 0,
                    "regime_counts": {},
                    "personality_types": {},
                    "last_seen_at": None,
                    "best_priority_score": 0.0,
                },
            )
            stats["observations"] += 1
            if str(normalized.get("result")) == "entry_ready":
                stats["entry_ready"] += 1
            elif str(normalized.get("result")) in {"blocked", "reject"}:
                stats["blocked"] += 1
            stats["last_seen_at"] = now_ts
            stats["best_priority_score"] = max(float(stats.get("best_priority_score", 0.0) or 0.0), float(normalized.get("priority_score", 0.0) or 0.0))
            regime = str(normalized.get("regime") or "CHOPPY")
            stats["regime_counts"][regime] = stats["regime_counts"].get(regime, 0) + 1
            personality_type = str(normalized.get("personality_type") or "balanced")
            stats["personality_types"][personality_type] = stats["personality_types"].get(personality_type, 0) + 1
            regime_counts = self.state.setdefault("regime_counts", {})
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            self.state["updated_at"] = now_ts
            if persist:
                self._save()

    def summarize_family(self, family: str) -> Dict[str, Any]:
        family = str(family or "UNKNOWN").strip() or "UNKNOWN"
        with self._lock:
            stats = dict(self.state.get("family_stats", {}).get(family, {}) or {})
            events = [event for event in self.state.get("events", []) if str(event.get("family") or "UNKNOWN").strip() == family]
        observations = int(stats.get("observations", len(events)) or 0)
        entry_ready = int(stats.get("entry_ready", 0) or 0)
        blocked = int(stats.get("blocked", 0) or 0)
        entry_ready_rate = round((entry_ready / observations), 4) if observations else 0.0
        blocked_rate = round((blocked / observations), 4) if observations else 0.0
        regimes = stats.get("regime_counts", {}) or {}
        top_regime = max(regimes.items(), key=lambda item: item[1])[0] if regimes else "unknown"
        personality_types = stats.get("personality_types", {}) or {}
        top_personality = max(personality_types.items(), key=lambda item: item[1])[0] if personality_types else "balanced"
        recent_events = events[-20:]
        recent_regimes = [str(event.get("regime") or "CHOPPY") for event in recent_events]
        recent_blocks = len([event for event in recent_events if str(event.get("result")) in {"blocked", "reject"}])
        recent_entry_ready = len([event for event in recent_events if str(event.get("result")) == "entry_ready"])
        return {
            "family": family,
            "observations": observations,
            "entry_ready_rate": entry_ready_rate,
            "blocked_rate": blocked_rate,
            "top_regime": top_regime,
            "top_personality": top_personality,
            "recent_regimes": recent_regimes[-10:],
            "recent_blocks": recent_blocks,
            "recent_entry_ready": recent_entry_ready,
            "regime_counts": regimes,
            "personality_types": personality_types,
            "last_seen_at": stats.get("last_seen_at"),
            "best_priority_score": round(float(stats.get("best_priority_score", 0.0) or 0.0), 4),
        }

    def persist(self):
        with self._lock:
            self.state["updated_at"] = time.time()
            self._save()

    def summarize(self) -> Dict[str, Any]:
        with self._lock:
            events = list(self.state.get("events", []))
            family_stats = dict(self.state.get("family_stats", {}))
            regime_counts = dict(self.state.get("regime_counts", {}))
        total_events = len(events)
        recent_events = events[-self.history_limit :]
        medium_events = events[-min(len(events), self.history_limit * 3) :]
        family_order = sorted(family_stats.items(), key=lambda item: int(item[1].get("observations", 0) or 0), reverse=True)
        top_families = [
            {
                "family": family,
                "observations": int(stats.get("observations", 0) or 0),
                "entry_ready_rate": round(float(stats.get("entry_ready", 0) or 0) / float(max(1, int(stats.get("observations", 0) or 0))), 4),
                "blocked_rate": round(float(stats.get("blocked", 0) or 0) / float(max(1, int(stats.get("observations", 0) or 0))), 4),
                "top_regime": max((stats.get("regime_counts", {}) or {}).items(), key=lambda item: item[1])[0] if stats.get("regime_counts") else "unknown",
            }
            for family, stats in family_order[:5]
        ]
        weak_families = sorted(
            (
                {
                    "family": family,
                    "observations": int(stats.get("observations", 0) or 0),
                    "blocked_rate": round(float(stats.get("blocked", 0) or 0) / float(max(1, int(stats.get("observations", 0) or 0))), 4),
                    "entry_ready_rate": round(float(stats.get("entry_ready", 0) or 0) / float(max(1, int(stats.get("observations", 0) or 0))), 4),
                }
                for family, stats in family_stats.items()
            ),
            key=lambda item: (item["blocked_rate"], -item["entry_ready_rate"]),
            reverse=True,
        )[:5]
        return {
            "total_events": total_events,
            "recent_events": recent_events[-20:],
            "medium_events": medium_events[-100:],
            "family_stats": family_stats,
            "regime_counts": regime_counts,
            "top_families": top_families,
            "weak_families": weak_families,
            "updated_at": self.state.get("updated_at"),
        }

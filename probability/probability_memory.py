from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict
from runtime.paths import record_persist_event, runtime_state_file, safe_atomic_json_write, ensure_runtime_layout


class ProbabilityMemory:
    def __init__(self, state_file: str | None = None, history_limit: int = 400):
        self.state_file = str(state_file or runtime_state_file("realm_xxiii_probability_memory.json"))
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
            record_persist_event(subsystem="REALM-XXIII-MEMORY", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed")
        except Exception as exc:
            record_persist_event(subsystem="REALM-XXIII-MEMORY", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__)
            return

    def record(self, event: Dict[str, Any], persist: bool = False):
        if not isinstance(event, dict):
            return
        now_ts = time.time()
        entry = dict(event)
        entry.setdefault("ts", now_ts)
        entry.setdefault("family", "UNKNOWN")
        entry.setdefault("ticker", "")
        entry.setdefault("mispricing_type", "none")
        entry.setdefault("edge_category", "weak")
        entry.setdefault("conviction_score", 0.0)
        entry.setdefault("divergence", 0.0)
        entry.setdefault("regime", "CHOPPY")
        entry.setdefault("personality_type", "balanced")
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
                    "high_conviction": 0,
                    "moderate_edge": 0,
                    "weak_edge": 0,
                    "failed_edge": 0,
                    "total_conviction": 0.0,
                    "total_divergence": 0.0,
                    "regime_counts": {},
                    "personality_counts": {},
                    "last_seen_at": None,
                },
            )
            stats["observations"] += 1
            stats["total_conviction"] += float(entry.get("conviction_score", 0.0) or 0.0)
            stats["total_divergence"] += float(entry.get("divergence", 0.0) or 0.0)
            if str(entry.get("edge_category")) == "high":
                stats["high_conviction"] += 1
            elif str(entry.get("edge_category")) == "moderate":
                stats["moderate_edge"] += 1
            elif str(entry.get("edge_category")) == "weak":
                stats["weak_edge"] += 1
            else:
                stats["failed_edge"] += 1
            regime = str(entry.get("regime") or "CHOPPY")
            stats["regime_counts"][regime] = stats["regime_counts"].get(regime, 0) + 1
            personality = str(entry.get("personality_type") or "balanced")
            stats["personality_counts"][personality] = stats["personality_counts"].get(personality, 0) + 1
            stats["last_seen_at"] = now_ts
            regime_stats = self.state.setdefault("regime_stats", {})
            regime_stats[regime] = regime_stats.get(regime, 0) + 1
            self.state["updated_at"] = now_ts
            if persist:
                self._save()

    def summarize_family(self, family: str) -> Dict[str, Any]:
        family = str(family or "UNKNOWN").strip() or "UNKNOWN"
        with self._lock:
            stats = dict(self.state.get("family_stats", {}).get(family, {}) or {})
        observations = int(stats.get("observations", 0) or 0)
        return {
            "family": family,
            "observations": observations,
            "high_conviction_rate": round(float(stats.get("high_conviction", 0) or 0) / float(max(1, observations)), 4),
            "moderate_edge_rate": round(float(stats.get("moderate_edge", 0) or 0) / float(max(1, observations)), 4),
            "weak_edge_rate": round(float(stats.get("weak_edge", 0) or 0) / float(max(1, observations)), 4),
            "failed_edge_rate": round(float(stats.get("failed_edge", 0) or 0) / float(max(1, observations)), 4),
            "average_conviction": round(float(stats.get("total_conviction", 0.0) or 0.0) / float(max(1, observations)), 4),
            "average_divergence": round(float(stats.get("total_divergence", 0.0) or 0.0) / float(max(1, observations)), 4),
            "regime_counts": stats.get("regime_counts", {}) or {},
            "personality_counts": stats.get("personality_counts", {}) or {},
            "last_seen_at": stats.get("last_seen_at"),
        }

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
                    "average_conviction": round(float(stats.get("total_conviction", 0.0) or 0.0) / float(max(1, observations)), 4),
                    "high_conviction_rate": round(float(stats.get("high_conviction", 0) or 0) / float(max(1, observations)), 4),
                }
            )
            weak_families.append(
                {
                    "family": family,
                    "observations": observations,
                    "failed_edge_rate": round(float(stats.get("failed_edge", 0) or 0) / float(max(1, observations)), 4),
                    "weak_edge_rate": round(float(stats.get("weak_edge", 0) or 0) / float(max(1, observations)), 4),
                }
            )
        top_families.sort(key=lambda item: (item["high_conviction_rate"], item["average_conviction"]), reverse=True)
        weak_families.sort(key=lambda item: (item["failed_edge_rate"], item["weak_edge_rate"]), reverse=True)
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


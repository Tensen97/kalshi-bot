from __future__ import annotations

import builtins
import json
import math
import os
import threading
import time
from copy import deepcopy
from runtime.paths import ensure_runtime_layout, record_persist_event, runtime_memory_file, safe_atomic_json_write
from dataclasses import dataclass, asdict
from pathlib import Path

from risk.helpers import bounded_delta, clamp, safe_float, safe_ratio
from runtime.paths import ensure_runtime_layout, legacy_runtime_artifact_paths, runtime_memory_file


def _now():
    return time.time()


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def _load_json_file(path):
    try:
        with open(path, "r") as handle:
            return json.load(handle)
    except Exception:
        return None


def _load_legacy_payload():
    candidates = []
    for path in legacy_runtime_artifact_paths():
        if os.path.exists(path):
            try:
                candidates.append((os.path.getmtime(path), path))
            except Exception:
                continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    for _, path in candidates:
        payload = _load_json_file(path)
        if isinstance(payload, dict):
            return payload
    return None


def _ensure_profile_defaults(key=None, kind="ticker"):
    now = _now()
    return {
        "key": key,
        "kind": kind,
        "first_seen_time": now,
        "last_seen_time": now,
        "total_observations": 0,
        "near_pass_count": 0,
        "improving_edge_count": 0,
        "decaying_edge_count": 0,
        "recovery_count": 0,
        "spread_quality_history": [],
        "stability_history": [],
        "quote_stability_history": [],
        "directional_consistency_history": [],
        "volatility_regime_history": [],
        "execution_outcome_history": [],
        "recent_observations": [],
        "persistence_score": 0.5,
        "quote_stability_trend": 0.5,
        "edge_behavior_trend": 0.5,
        "volatility_tendency": 0.5,
        "directional_consistency": 0.5,
        "execution_outcome_memory": {},
        "last_summary": {},
    }


def _trim_list(values, limit):
    if not isinstance(values, list):
        return []
    if len(values) <= limit:
        return values
    return values[-limit:]


def _weighted_average(items, key, fallback=0.5, decay_window=86400.0):
    if not isinstance(items, list) or not items:
        return safe_float(fallback, 0.5)
    now = _now()
    numerator = 0.0
    denominator = 0.0
    for item in items:
        if not isinstance(item, dict):
            continue
        value = safe_float(item.get(key), fallback)
        age = max(0.0, now - safe_float(item.get("ts"), now))
        weight = math.exp(-age / max(decay_window, 1.0))
        numerator += value * weight
        denominator += weight
    if denominator <= 0.0:
        return safe_float(fallback, 0.5)
    return numerator / denominator


@dataclass
class AIONObservation:
    ticker: str
    family: str
    market_type: str
    spread_quality: float = 0.0
    stability_score: float = 0.0
    near_pass: bool = False
    improving_edge: bool = False
    decaying_edge: bool = False
    quote_stability: float = 0.0
    directional_consistency: float = 0.0
    volatility_regime: float = 0.0
    execution_outcome: str = "unknown"
    outcome_strength: float = 0.0
    blocker_reason: str = "unknown"
    timestamp: float = 0.0


class AIONPersistentIntelligence:
    def __init__(self, state_file=None, history_limit=500, ttl_seconds=7 * 86400.0, enabled=True):
        ensure_runtime_layout()
        self.state_file = str(state_file or runtime_memory_file("aion_persistent_intelligence.json"))
        self.history_limit = max(25, int(history_limit or 500))
        self.ttl_seconds = max(3600.0, safe_float(ttl_seconds, 7 * 86400.0))
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        self.ticker_profiles = {}
        self.family_profiles = {}
        self.last_summary_ts = 0.0
        self.summary_interval_seconds = 120.0
        self.load()
        builtins.print("[AION-MODULE-LOAD] " + json.dumps({"module": "memory.aion_persistent_intelligence", "state_file": self.state_file}, sort_keys=True))

    def _profile(self, container, key, kind):
        if key not in container or not isinstance(container.get(key), dict):
            container[key] = _ensure_profile_defaults(key=key, kind=kind)
        profile = container[key]
        profile.setdefault("key", key)
        profile.setdefault("kind", kind)
        profile.setdefault("recent_observations", [])
        profile.setdefault("execution_outcome_history", [])
        profile.setdefault("spread_quality_history", [])
        profile.setdefault("stability_history", [])
        profile.setdefault("quote_stability_history", [])
        profile.setdefault("directional_consistency_history", [])
        profile.setdefault("volatility_regime_history", [])
        profile.setdefault("execution_outcome_memory", {})
        profile.setdefault("last_summary", {})
        return profile

    def _prune_profile(self, profile):
        now = _now()
        recent = []
        for entry in profile.get("recent_observations", []) or []:
            if not isinstance(entry, dict):
                continue
            age = now - safe_float(entry.get("ts"), now)
            if age <= self.ttl_seconds:
                recent.append(entry)
        profile["recent_observations"] = _trim_list(recent, self.history_limit)
        for history_key in ("spread_quality_history", "stability_history", "quote_stability_history", "directional_consistency_history", "volatility_regime_history", "execution_outcome_history"):
            profile[history_key] = _trim_list(profile.get(history_key, []), self.history_limit)
        return profile

    def _score_profile(self, profile):
        observations = profile.get("recent_observations", []) or []
        if not observations:
            return {
                "persistence_score": 0.5,
                "near_pass_frequency": 0.0,
                "quote_stability_trend": 0.5,
                "edge_behavior_trend": 0.5,
                "volatility_tendency": 0.5,
                "directional_consistency": 0.5,
                "execution_outcome_memory": dict(profile.get("execution_outcome_memory", {}) or {}),
                "recovery_velocity": 0.0,
                "decay_velocity": 0.0,
            }
        near_pass_frequency = safe_ratio(sum(1 for item in observations if item.get("near_pass")), len(observations), 0.0)
        improving_edge_rate = safe_ratio(sum(1 for item in observations if item.get("improving_edge")), len(observations), 0.0)
        decaying_edge_rate = safe_ratio(sum(1 for item in observations if item.get("decaying_edge")), len(observations), 0.0)
        quote_stability_trend = _weighted_average(observations, "quote_stability", fallback=0.5, decay_window=self.ttl_seconds)
        directional_consistency = _weighted_average(observations, "directional_consistency", fallback=0.5, decay_window=self.ttl_seconds)
        volatility_tendency = _weighted_average(observations, "volatility_regime", fallback=0.5, decay_window=self.ttl_seconds)
        spread_quality = _weighted_average(observations, "spread_quality", fallback=0.5, decay_window=self.ttl_seconds)
        stability_score = _weighted_average(observations, "stability_score", fallback=0.5, decay_window=self.ttl_seconds)
        recovery_velocity = safe_ratio(sum(1 for item in observations if item.get("improving_edge") and not item.get("decaying_edge")), len(observations), 0.0)
        decay_velocity = safe_ratio(sum(1 for item in observations if item.get("decaying_edge")), len(observations), 0.0)
        persistence_score = clamp(
            0.30
            + near_pass_frequency * 0.12
            + improving_edge_rate * 0.18
            - decaying_edge_rate * 0.15
            + quote_stability_trend * 0.12
            + directional_consistency * 0.10
            + spread_quality * 0.10
            + stability_score * 0.08
            + recovery_velocity * 0.06
            - decay_velocity * 0.05,
            0.0,
            1.0,
        )
        return {
            "persistence_score": round(persistence_score, 4),
            "near_pass_frequency": round(near_pass_frequency, 4),
            "quote_stability_trend": round(quote_stability_trend, 4),
            "edge_behavior_trend": round((improving_edge_rate + spread_quality + stability_score) / 3.0, 4),
            "volatility_tendency": round(volatility_tendency, 4),
            "directional_consistency": round(directional_consistency, 4),
            "execution_outcome_memory": dict(profile.get("execution_outcome_memory", {}) or {}),
            "recovery_velocity": round(recovery_velocity, 4),
            "decay_velocity": round(decay_velocity, 4),
        }

    def _record(self, kind, key, observation):
        observation = observation if isinstance(observation, dict) else {}
        container = self.ticker_profiles if kind == "ticker" else self.family_profiles
        profile = self._profile(container, key, kind)
        now = safe_float(observation.get("timestamp"), _now())
        profile["first_seen_time"] = profile.get("first_seen_time") or now
        profile["last_seen_time"] = now
        profile["total_observations"] = int(profile.get("total_observations", 0) or 0) + 1
        profile["near_pass_count"] = int(profile.get("near_pass_count", 0) or 0) + (1 if observation.get("near_pass") else 0)
        profile["improving_edge_count"] = int(profile.get("improving_edge_count", 0) or 0) + (1 if observation.get("improving_edge") else 0)
        profile["decaying_edge_count"] = int(profile.get("decaying_edge_count", 0) or 0) + (1 if observation.get("decaying_edge") else 0)
        profile["recovery_count"] = int(profile.get("recovery_count", 0) or 0) + (1 if observation.get("improving_edge") and not observation.get("decaying_edge") else 0)
        profile.setdefault("spread_quality_history", []).append({"ts": now, "value": safe_float(observation.get("spread_quality"), 0.0)})
        profile.setdefault("stability_history", []).append({"ts": now, "value": safe_float(observation.get("stability_score"), 0.0)})
        profile.setdefault("quote_stability_history", []).append({"ts": now, "value": safe_float(observation.get("quote_stability"), 0.0)})
        profile.setdefault("directional_consistency_history", []).append({"ts": now, "value": safe_float(observation.get("directional_consistency"), 0.0)})
        profile.setdefault("volatility_regime_history", []).append({"ts": now, "value": safe_float(observation.get("volatility_regime"), 0.0)})
        profile.setdefault("execution_outcome_history", []).append({"ts": now, "value": str(observation.get("execution_outcome") or "unknown")})
        outcome = str(observation.get("execution_outcome") or "unknown").strip().lower()
        outcome_memory = profile.setdefault("execution_outcome_memory", {})
        outcome_memory[outcome] = int(outcome_memory.get(outcome, 0) or 0) + 1
        profile["recent_observations"] = _trim_list(profile.get("recent_observations", []) + [observation], self.history_limit)
        profile = self._prune_profile(profile)
        score_summary = self._score_profile(profile)
        profile.update(score_summary)
        profile["last_summary"] = deepcopy(score_summary)
        profile["last_summary"]["key"] = key
        profile["last_summary"]["kind"] = kind
        profile["last_summary"]["total_observations"] = profile.get("total_observations", 0)
        profile["last_summary"]["near_pass_count"] = profile.get("near_pass_count", 0)
        profile["last_summary"]["improving_edge_count"] = profile.get("improving_edge_count", 0)
        profile["last_summary"]["decaying_edge_count"] = profile.get("decaying_edge_count", 0)
        profile["last_summary"]["recovery_count"] = profile.get("recovery_count", 0)
        profile["last_summary"]["last_seen_time"] = profile.get("last_seen_time")
        return deepcopy(profile["last_summary"])

    def record_ticker_behavior(self, ticker, family, market_type="sports", **fields):
        key = f"{market_type}:{ticker}"
        observation = {
            "ticker": ticker,
            "family": family,
            "market_type": market_type,
            "spread_quality": fields.get("spread_quality", fields.get("spread_stability", 0.0)),
            "stability_score": fields.get("stability_score", fields.get("quote_stability", 0.0)),
            "near_pass": bool(fields.get("near_pass")),
            "improving_edge": bool(fields.get("improving_edge")),
            "decaying_edge": bool(fields.get("decaying_edge")),
            "quote_stability": fields.get("quote_stability", fields.get("quote_stability_trend", 0.0)),
            "directional_consistency": fields.get("directional_consistency", fields.get("directional_bias", 0.0)),
            "volatility_regime": fields.get("volatility_regime", fields.get("volatility", 0.0)),
            "execution_outcome": fields.get("execution_outcome", "unknown"),
            "outcome_strength": fields.get("outcome_strength", 0.0),
            "blocker_reason": fields.get("blocker_reason", "unknown"),
            "timestamp": fields.get("timestamp", _now()),
        }
        with self._lock:
            summary = self._record("ticker", key, observation)
            summary.update({"ticker": ticker, "family": family, "market_type": market_type})
            builtins.print("[AION-TICKER-BEHAVIOR] " + json.dumps(summary, sort_keys=True, default=str))
            return summary

    def record_family_profile(self, family, market_type="sports", **fields):
        key = f"{market_type}:{family}"
        observation = {
            "ticker": fields.get("ticker"),
            "family": family,
            "market_type": market_type,
            "spread_quality": fields.get("spread_quality", fields.get("spread_stability", 0.0)),
            "stability_score": fields.get("stability_score", fields.get("quote_stability", 0.0)),
            "near_pass": bool(fields.get("near_pass")),
            "improving_edge": bool(fields.get("improving_edge")),
            "decaying_edge": bool(fields.get("decaying_edge")),
            "quote_stability": fields.get("quote_stability", fields.get("quote_stability_trend", 0.0)),
            "directional_consistency": fields.get("directional_consistency", fields.get("directional_bias", 0.0)),
            "volatility_regime": fields.get("volatility_regime", fields.get("volatility", 0.0)),
            "execution_outcome": fields.get("execution_outcome", "unknown"),
            "outcome_strength": fields.get("outcome_strength", 0.0),
            "blocker_reason": fields.get("blocker_reason", "unknown"),
            "timestamp": fields.get("timestamp", _now()),
        }
        with self._lock:
            summary = self._record("family", key, observation)
            summary.update({"family": family, "market_type": market_type})
            builtins.print("[AION-FAMILY-PROFILE] " + json.dumps(summary, sort_keys=True, default=str))
            return summary

    def record_execution_placeholder(self, ticker, family, market_type="sports", outcome="unknown", **fields):
        return self.record_ticker_behavior(
            ticker,
            family,
            market_type=market_type,
            execution_outcome=outcome,
            **fields,
        )

    def summarize_ticker(self, ticker, market_type="sports"):
        key = f"{market_type}:{ticker}"
        with self._lock:
            profile = self.ticker_profiles.get(key)
            if not isinstance(profile, dict):
                return {}
            self._prune_profile(profile)
            summary = self._score_profile(profile)
            summary.update({"ticker": ticker, "market_type": market_type, "family": profile.get("recent_observations", [{}])[-1].get("family") if profile.get("recent_observations") else None})
            return summary

    def summarize_family(self, family, market_type="sports"):
        key = f"{market_type}:{family}"
        with self._lock:
            profile = self.family_profiles.get(key)
            if not isinstance(profile, dict):
                return {}
            self._prune_profile(profile)
            summary = self._score_profile(profile)
            summary.update({"family": family, "market_type": market_type})
            return summary

    def build_overall_summary(self):
        with self._lock:
            ticker_profiles = [profile for profile in self.ticker_profiles.values() if isinstance(profile, dict)]
            family_profiles = [profile for profile in self.family_profiles.values() if isinstance(profile, dict)]
            if not ticker_profiles and not family_profiles:
                return {
                    "module": "aion_persistent_intelligence",
                    "ticker_profile_count": 0,
                    "family_profile_count": 0,
                    "strongest_ticker": None,
                    "strongest_family": None,
                    "strongest_score": 0.0,
                    "average_persistence_score": 0.0,
                    "average_near_pass_frequency": 0.0,
                    "timestamp": _now(),
                }
            best_ticker_key, best_ticker = max(
                self.ticker_profiles.items(),
                key=lambda item: safe_float(item[1].get("persistence_score"), 0.0),
            ) if ticker_profiles else (None, {})
            best_family_key, best_family = max(
                self.family_profiles.items(),
                key=lambda item: safe_float(item[1].get("persistence_score"), 0.0),
            ) if family_profiles else (None, {})
            average_persistence = 0.0
            average_near_pass = 0.0
            if ticker_profiles:
                average_persistence = sum(safe_float(profile.get("persistence_score"), 0.0) for profile in ticker_profiles) / max(len(ticker_profiles), 1)
                average_near_pass = sum(safe_float(profile.get("near_pass_count"), 0.0) for profile in ticker_profiles) / max(len(ticker_profiles), 1)
            summary = {
                "module": "aion_persistent_intelligence",
                "ticker_profile_count": int(len(ticker_profiles)),
                "family_profile_count": int(len(family_profiles)),
                "strongest_ticker": str(best_ticker_key or "").split(":", 1)[-1] if best_ticker_key else None,
                "strongest_family": str(best_family_key or "").split(":", 1)[-1] if best_family_key else None,
                "strongest_score": round(max(safe_float(best_ticker.get("persistence_score"), 0.0), safe_float(best_family.get("persistence_score"), 0.0)), 4),
                "average_persistence_score": round(average_persistence, 4),
                "average_near_pass_frequency": round(average_near_pass, 4),
                "timestamp": _now(),
            }
            return summary

    def snapshot(self):
        with self._lock:
            return _json_safe(
                {
                    "state_file": self.state_file,
                    "ticker_profiles": deepcopy(self.ticker_profiles),
                    "family_profiles": deepcopy(self.family_profiles),
                    "summary": self.build_overall_summary(),
                }
            )

    def persist(self):
        if not self.enabled:
            return self.snapshot()
        with self._lock:
            try:
                ok = safe_atomic_json_write(self.state_file, self.snapshot(), indent=2, sort_keys=True, default=str)
                record_persist_event(subsystem="AION-PERSIST", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed")
            except Exception as exc:
                record_persist_event(subsystem="AION-PERSIST", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__)
                builtins.print("[AION-INTELLIGENCE-MEMORY] " + json.dumps({"reason": exc.__class__.__name__, "state_file": self.state_file, "action": "persist"}, sort_keys=True))
            return self.snapshot()

    def load(self):
        payload = None
        if os.path.exists(self.state_file):
            payload = _load_json_file(self.state_file)
        if payload is None:
            payload = _load_legacy_payload()
        if not isinstance(payload, dict):
            return self
        with self._lock:
            ticker_profiles = payload.get("ticker_profiles") or {}
            family_profiles = payload.get("family_profiles") or {}
            if isinstance(ticker_profiles, dict):
                self.ticker_profiles = ticker_profiles
            if isinstance(family_profiles, dict):
                self.family_profiles = family_profiles
        return self

    def maybe_emit_summary(self, force=False):
        now = _now()
        if force or (now - safe_float(self.last_summary_ts, 0.0)) >= self.summary_interval_seconds:
            self.last_summary_ts = now
            summary = self.build_overall_summary()
            builtins.print("[AION-INTELLIGENCE-MEMORY] " + json.dumps(summary, sort_keys=True, default=str))
            self.persist()
            return summary
        return self.build_overall_summary()


builtins.print("[AION-MODULE-LOAD] " + json.dumps({"module": "memory.aion_persistent_intelligence", "class": "AIONPersistentIntelligence"}, sort_keys=True))


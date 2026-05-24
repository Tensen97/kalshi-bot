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


def _ensure_distilled_learning_defaults(payload=None):
    payload = payload if isinstance(payload, dict) else {}
    summaries = payload.get("summaries") if isinstance(payload.get("summaries"), dict) else {}
    return {
        "version": int(safe_float(payload.get("version"), 1) or 1),
        "observations": _trim_list(payload.get("observations", []), 1200),
        "outcomes": _trim_list(payload.get("outcomes", []), 1200),
        "last_observation_by_key": payload.get("last_observation_by_key") if isinstance(payload.get("last_observation_by_key"), dict) else {},
        "summaries": {
            "families": summaries.get("families") if isinstance(summaries.get("families"), dict) else {},
            "categories": summaries.get("categories") if isinstance(summaries.get("categories"), dict) else {},
            "sports_tags": summaries.get("sports_tags") if isinstance(summaries.get("sports_tags"), dict) else {},
            "odds_buckets": summaries.get("odds_buckets") if isinstance(summaries.get("odds_buckets"), dict) else {},
            "book_quality": summaries.get("book_quality") if isinstance(summaries.get("book_quality"), dict) else {},
            "elite_traders": summaries.get("elite_traders") if isinstance(summaries.get("elite_traders"), dict) else {},
            "elite_trader_categories": summaries.get("elite_trader_categories") if isinstance(summaries.get("elite_trader_categories"), dict) else {},
        },
        "audit": payload.get("audit") if isinstance(payload.get("audit"), dict) else {},
    }


def _distilled_iso_to_ts(value):
    if isinstance(value, (int, float)):
        return safe_float(value, _now())
    text = str(value or "").strip()
    if not text:
        return _now()
    try:
        return float(text)
    except Exception:
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return _now()


def _distilled_price_bucket(price):
    value = safe_float(price, None)
    if value is None:
        return "unknown"
    if value > 1.0 and value <= 100.0:
        value = value / 100.0
    if value < 0.20:
        return "under_20"
    if value <= 0.35:
        return "20_35"
    if value <= 0.45:
        return "35_45"
    if value <= 0.60:
        return "45_60"
    if value <= 0.80:
        return "60_80"
    return "over_80"


def _distilled_book_quality_tag(record):
    record = record if isinstance(record, dict) else {}
    explicit = str(record.get("orderbook_quality_tag") or record.get("book_quality_tag") or "").strip().upper()
    if explicit:
        return explicit
    spread = safe_float(record.get("spread"), None)
    liquidity = safe_float(record.get("liquidity_dollars", record.get("liquidity")), None)
    if spread is not None and spread <= 0.03 and (liquidity is None or liquidity >= 0.0):
        return "BOOK_GOOD"
    if spread is not None and spread >= 0.12:
        return "BOOK_WIDE"
    return "BOOK_UNKNOWN"


def _distilled_update_perf_stats(stats, pnl, outcome=None, timestamp_ts=None):
    stats = stats if isinstance(stats, dict) else {}
    pnl = safe_float(pnl, 0.0) or 0.0
    outcome = str(outcome or "").strip().lower()
    stats["count"] = int(stats.get("count", 0) or 0) + 1
    stats["pnl"] = round(safe_float(stats.get("pnl"), 0.0) + pnl, 6)
    if pnl > 0.0 or outcome in {"win", "won"}:
        stats["wins"] = int(stats.get("wins", 0) or 0) + 1
    elif pnl < 0.0 or outcome in {"loss", "lost"}:
        stats["losses"] = int(stats.get("losses", 0) or 0) + 1
    else:
        stats["breakevens"] = int(stats.get("breakevens", 0) or 0) + 1
    recent = stats.get("recent_pnls") if isinstance(stats.get("recent_pnls"), list) else []
    recent = _trim_list(recent + [round(pnl, 6)], 40)
    stats["recent_pnls"] = recent
    count = int(stats.get("count", 0) or 0)
    wins = int(stats.get("wins", 0) or 0)
    losses = int(stats.get("losses", 0) or 0)
    stats["avg_pnl"] = round(stats["pnl"] / max(count, 1), 6)
    stats["win_rate"] = round(wins / max(wins + losses, 1), 4) if wins or losses else None
    stats["recent_pnl"] = round(sum(safe_float(value, 0.0) or 0.0 for value in recent[-10:]), 6)
    if timestamp_ts is not None:
        stats["last_ts"] = max(safe_float(stats.get("last_ts"), 0.0) or 0.0, safe_float(timestamp_ts, 0.0) or 0.0)
    return stats


def _distilled_bias_from_stats(stats, min_count=3):
    stats = stats if isinstance(stats, dict) else {}
    count = int(stats.get("count", 0) or 0)
    if count < max(1, int(min_count or 1)):
        return 0.0
    avg_pnl = safe_float(stats.get("avg_pnl"), 0.0) or 0.0
    recent_pnl = safe_float(stats.get("recent_pnl"), 0.0) or 0.0
    win_rate = safe_float(stats.get("win_rate"), None)
    sample_factor = min(1.0, max(0.0, count / 12.0))
    pnl_signal = max(-1.0, min(1.0, avg_pnl * 20.0))
    recent_signal = max(-1.0, min(1.0, recent_pnl * 4.0))
    win_signal = 0.0 if win_rate is None else max(-1.0, min(1.0, (win_rate - 0.5) * 2.0))
    return round(((pnl_signal * 0.55) + (recent_signal * 0.25) + (win_signal * 0.20)) * sample_factor, 6)


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
        self.distilled_learning = _ensure_distilled_learning_defaults({})
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
            distilled_audit = self.audit_distilled_learning()
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
                    "distilled_memory": distilled_audit,
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
                "distilled_memory": distilled_audit,
                "timestamp": _now(),
            }
            return summary

    def _normalize_distilled_record(self, record, *, record_type):
        record = record if isinstance(record, dict) else {}
        timestamp = record.get("timestamp") or record.get("timestamp_utc") or record.get("observed_at") or _now()
        timestamp_ts = _distilled_iso_to_ts(timestamp)
        ticker = str(record.get("ticker") or "").strip()
        family = str(record.get("family") or record.get("category") or "UNKNOWN").strip().upper() or "UNKNOWN"
        sports_tags = record.get("sports_tags") or record.get("sports_edge_tags") or []
        if isinstance(sports_tags, str):
            sports_tags = [tag.strip() for tag in sports_tags.replace(";", ",").split(",") if tag.strip()]
        elif isinstance(sports_tags, (tuple, set)):
            sports_tags = list(sports_tags)
        sports_tags = [str(tag).strip() for tag in sports_tags if str(tag or "").strip()][:12] if isinstance(sports_tags, list) else []
        elite_handle = str(record.get("elite_trader_handle") or record.get("trader_handle") or "").strip()
        price = record.get("entry_price") if record.get("entry_price") is not None else record.get("price")
        normalized = {
            "timestamp": timestamp,
            "timestamp_ts": round(timestamp_ts, 6),
            "record_type": record_type,
            "ticker": ticker or None,
            "market_title": record.get("market_title") or record.get("title"),
            "category": str(record.get("category") or "unknown").strip().lower() or "unknown",
            "family": family,
            "side": str(record.get("side") or "unknown").strip().lower(),
            "yes_price": safe_float(record.get("yes_price", record.get("yes_ask")), None),
            "no_price": safe_float(record.get("no_price", record.get("no_ask")), None),
            "price": safe_float(price, None),
            "entry_price": safe_float(record.get("entry_price"), None),
            "exit_price": safe_float(record.get("exit_price"), None),
            "spread": safe_float(record.get("spread"), None),
            "liquidity_dollars": safe_float(record.get("liquidity_dollars", record.get("liquidity")), None),
            "depth_summary": record.get("depth_summary"),
            "recent_price_movement": safe_float(record.get("recent_price_movement", record.get("price_delta")), None),
            "orderbook_quality": safe_float(record.get("orderbook_quality"), None),
            "orderbook_quality_tag": _distilled_book_quality_tag(record),
            "sports_data_tag": record.get("sports_data_tag") or record.get("sports_family_activity_level"),
            "sports_tags": sports_tags,
            "sports_edge_score": safe_float(record.get("sports_edge_score", record.get("sports_score_at_entry")), None),
            "asymmetry_score": safe_float(record.get("asymmetry_score", record.get("sports_payout_multiple")), None),
            "odds_bucket": record.get("odds_bucket") or _distilled_price_bucket(price),
            "elite_signal_used": bool(record.get("elite_signal_used") or record.get("elite_public_signal_match")),
            "elite_trader_handle": elite_handle or None,
            "elite_boost_used": safe_float(record.get("elite_boost_used", record.get("elite_public_signal_boost")), None),
            "candidate_score": safe_float(record.get("candidate_score", record.get("confidence")), None),
            "score_at_entry": safe_float(record.get("score_at_entry", record.get("sports_score_at_entry")), None),
            "position_size": safe_float(record.get("position_size", record.get("contracts")), None),
            "decision": str(record.get("decision") or record.get("action") or record.get("entry_reason") or "unknown").strip().lower(),
            "sizing_reason": record.get("sizing_reason"),
            "pnl": safe_float(record.get("pnl", record.get("pnl_dollars")), None),
            "result": str(record.get("result") or record.get("outcome") or "unknown").strip().lower(),
        }
        return _json_safe({key: value for key, value in normalized.items() if value not in (None, "", [], {})})

    def record_distilled_observation(self, record, *, throttle_seconds=900.0, max_observations=1200):
        if not self.enabled:
            return {"ok": False, "reason": "disabled"}
        normalized = self._normalize_distilled_record(record, record_type="observation")
        ticker = str(normalized.get("ticker") or "").strip()
        if not ticker:
            return {"ok": False, "reason": "missing_ticker"}
        key = "|".join(
            [
                ticker,
                str(normalized.get("side") or "unknown"),
                str(normalized.get("decision") or "unknown"),
                str(normalized.get("odds_bucket") or "unknown"),
            ]
        )
        timestamp_ts = safe_float(normalized.get("timestamp_ts"), _now()) or _now()
        with self._lock:
            memory = _ensure_distilled_learning_defaults(self.distilled_learning)
            last_by_key = memory.setdefault("last_observation_by_key", {})
            previous_ts = safe_float(last_by_key.get(key), None)
            if previous_ts is not None and timestamp_ts - previous_ts < max(60.0, safe_float(throttle_seconds, 900.0)):
                self.distilled_learning = memory
                return {"ok": True, "skipped": True, "reason": "throttled", "key": key}
            last_by_key[key] = timestamp_ts
            if len(last_by_key) > 600:
                keep = sorted(last_by_key.items(), key=lambda item: safe_float(item[1], 0.0), reverse=True)[:600]
                memory["last_observation_by_key"] = dict(keep)
            observations = memory.get("observations") if isinstance(memory.get("observations"), list) else []
            memory["observations"] = _trim_list(observations + [normalized], max_observations)
            summaries = memory.setdefault("summaries", {})
            family = str(normalized.get("family") or "UNKNOWN")
            families = summaries.setdefault("families", {})
            family_stats = families.setdefault(family, {})
            family_stats["observations"] = int(family_stats.get("observations", 0) or 0) + 1
            family_stats["last_observation_ts"] = timestamp_ts
            categories = summaries.setdefault("categories", {})
            category = str(normalized.get("category") or "unknown")
            category_stats = categories.setdefault(category, {})
            category_stats["observations"] = int(category_stats.get("observations", 0) or 0) + 1
            category_stats["last_observation_ts"] = timestamp_ts
            self.distilled_learning = memory
            self.persist()
        return {"ok": True, "skipped": False, "key": key, "record": normalized}

    def record_distilled_outcome(self, record, *, max_outcomes=1200):
        if not self.enabled:
            return {"ok": False, "reason": "disabled"}
        normalized = self._normalize_distilled_record(record, record_type="outcome")
        ticker = str(normalized.get("ticker") or "").strip()
        if not ticker:
            return {"ok": False, "reason": "missing_ticker"}
        timestamp_ts = safe_float(normalized.get("timestamp_ts"), _now()) or _now()
        pnl = safe_float(normalized.get("pnl"), 0.0) or 0.0
        outcome = normalized.get("result") or "unknown"
        with self._lock:
            memory = _ensure_distilled_learning_defaults(self.distilled_learning)
            outcomes = memory.get("outcomes") if isinstance(memory.get("outcomes"), list) else []
            memory["outcomes"] = _trim_list(outcomes + [normalized], max_outcomes)
            summaries = memory.setdefault("summaries", {})

            def update_bucket(section, key):
                if not key:
                    return None
                bucket = summaries.setdefault(section, {})
                stats = bucket.setdefault(str(key), {})
                bucket[str(key)] = _distilled_update_perf_stats(stats, pnl, outcome=outcome, timestamp_ts=timestamp_ts)
                return bucket[str(key)]

            family_stats = update_bucket("families", normalized.get("family") or "UNKNOWN")
            update_bucket("categories", normalized.get("category") or "unknown")
            update_bucket("odds_buckets", normalized.get("odds_bucket") or "unknown")
            update_bucket("book_quality", normalized.get("orderbook_quality_tag") or "BOOK_UNKNOWN")
            for tag in normalized.get("sports_tags") or []:
                update_bucket("sports_tags", tag)
            if normalized.get("elite_signal_used") and normalized.get("elite_trader_handle"):
                handle = str(normalized.get("elite_trader_handle") or "").strip()
                update_bucket("elite_traders", handle)
                update_bucket("elite_trader_categories", f"{handle}|{normalized.get('category') or 'unknown'}")
            memory["audit"] = self.audit_distilled_learning(memory=memory)
            self.distilled_learning = memory
            self.persist()
        return {"ok": True, "record": normalized, "family_stats": family_stats or {}}

    def get_distilled_learning_signal(self, *, family=None, category=None, sports_tags=None, odds_bucket=None, book_quality_tag=None, elite_trader_handle=None):
        memory = _ensure_distilled_learning_defaults(self.distilled_learning)
        summaries = memory.get("summaries") if isinstance(memory.get("summaries"), dict) else {}
        sports_tags = sports_tags if isinstance(sports_tags, list) else []
        components = []

        def add_component(label, section, key, weight, min_count=3):
            if not key:
                return
            bucket = summaries.get(section) if isinstance(summaries.get(section), dict) else {}
            stats = bucket.get(str(key)) if isinstance(bucket.get(str(key)), dict) else {}
            bias = _distilled_bias_from_stats(stats, min_count=min_count)
            if bias:
                components.append({"label": label, "key": str(key), "bias": bias, "weight": weight, "count": stats.get("count"), "pnl": stats.get("pnl")})

        add_component("family", "families", str(family or "").strip().upper(), 0.42, min_count=3)
        add_component("category", "categories", str(category or "").strip().lower(), 0.12, min_count=5)
        for tag in sports_tags[:8]:
            add_component("sports_tag", "sports_tags", tag, 0.18, min_count=3)
        add_component("odds_bucket", "odds_buckets", odds_bucket, 0.18, min_count=4)
        add_component("book_quality", "book_quality", book_quality_tag, 0.12, min_count=4)
        add_component("elite_trader", "elite_traders", elite_trader_handle, 0.28, min_count=2)
        if elite_trader_handle and category:
            add_component("elite_trader_category", "elite_trader_categories", f"{elite_trader_handle}|{str(category).strip().lower()}", 0.18, min_count=2)

        raw_bias = sum(safe_float(item.get("bias"), 0.0) * safe_float(item.get("weight"), 0.0) for item in components)
        raw_bias = max(-1.0, min(1.0, raw_bias))
        return {
            "rank_adjustment": round(max(-0.75, min(1.25, raw_bias * 1.15)), 4),
            "sports_boost_adjustment": round(max(-0.45, min(0.75, raw_bias * 0.70)), 4),
            "sizing_confidence_multiplier": round(max(0.85, min(1.12, 1.0 + raw_bias * 0.10)), 4),
            "elite_trust_weight": round(max(0.65, min(1.35, 1.0 + raw_bias * 0.22)), 4),
            "components": components[:12],
        }

    def audit_distilled_learning(self, memory=None):
        memory = _ensure_distilled_learning_defaults(memory if isinstance(memory, dict) else self.distilled_learning)
        observations = [item for item in memory.get("observations", []) if isinstance(item, dict)]
        outcomes = [item for item in memory.get("outcomes", []) if isinstance(item, dict)]
        timestamps = [
            safe_float(item.get("timestamp_ts"), None)
            for item in observations + outcomes
            if safe_float(item.get("timestamp_ts"), None) is not None
        ]
        sports_observations = [
            item for item in observations
            if str(item.get("category") or "").lower() == "sports" or str(item.get("family") or "").upper().startswith(("KXNBA", "KXMLB", "KXNFL", "KXNHL", "KXATP", "KXWTA", "KXUFC"))
        ]
        sports_outcomes = [
            item for item in outcomes
            if str(item.get("category") or "").lower() == "sports" or item.get("sports_tags")
        ]
        elite_outcomes = [item for item in outcomes if item.get("elite_signal_used")]
        summaries = memory.get("summaries") if isinstance(memory.get("summaries"), dict) else {}
        families = summaries.get("families") if isinstance(summaries.get("families"), dict) else {}
        top_by_count = sorted(
            (
                {"family": key, "records": int((value or {}).get("observations", 0) or 0) + int((value or {}).get("count", 0) or 0)}
                for key, value in families.items()
                if isinstance(value, dict)
            ),
            key=lambda item: item["records"],
            reverse=True,
        )[:8]
        top_by_pnl = sorted(
            (
                {"family": key, "pnl": round(safe_float((value or {}).get("pnl"), 0.0) or 0.0, 6), "outcomes": int((value or {}).get("count", 0) or 0)}
                for key, value in families.items()
                if isinstance(value, dict)
            ),
            key=lambda item: item["pnl"],
            reverse=True,
        )[:8]
        return {
            "total_stored_observations": len(observations),
            "total_stored_trade_outcomes": len(outcomes),
            "oldest_timestamp_ts": min(timestamps) if timestamps else None,
            "newest_timestamp_ts": max(timestamps) if timestamps else None,
            "sports_observation_count": len(sports_observations),
            "sports_trade_outcome_count": len(sports_outcomes),
            "elite_signal_linked_outcome_count": len(elite_outcomes),
            "top_families_by_record_count": top_by_count,
            "top_families_by_pnl": top_by_pnl,
        }

    def snapshot(self):
        with self._lock:
            return _json_safe(
                {
                    "state_file": self.state_file,
                    "ticker_profiles": deepcopy(self.ticker_profiles),
                    "family_profiles": deepcopy(self.family_profiles),
                    "distilled_learning": deepcopy(_ensure_distilled_learning_defaults(self.distilled_learning)),
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
            self.distilled_learning = _ensure_distilled_learning_defaults(payload.get("distilled_learning"))
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

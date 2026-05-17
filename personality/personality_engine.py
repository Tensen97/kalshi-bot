from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from .adaptive_thresholds import compute_adaptive_thresholds
from .personality_memory import PersonalityMemory
from .personality_profiles import build_personality_state, normalize_personality_profile
from .regime_detector import detect_market_regime


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


class PersonalityEngine:
    def __init__(
        self,
        state_file: str,
        enabled: bool = False,
        history_limit: int = 300,
        summary_interval_seconds: float = 60.0,
        max_threshold_shift: float = 0.08,
    ):
        self.state_file = state_file
        self.enabled = bool(enabled)
        self.history_limit = max(50, int(history_limit or 300))
        self.summary_interval_seconds = max(15.0, float(summary_interval_seconds or 60.0))
        self.max_threshold_shift = max(0.01, float(max_threshold_shift or 0.08))
        self.memory = PersonalityMemory(self.state_file, history_limit=self.history_limit)
        self.last_summary: Dict[str, Any] = {}
        self.last_emit_ts = 0.0
        self.last_regime_by_family: Dict[str, str] = {}
        self.last_profile_by_family: Dict[str, Dict[str, Any]] = {}
        self.last_context_by_family: Dict[str, Dict[str, Any]] = {}

    def _log(self, prefix: str, **fields):
        if fields:
            suffix = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
            print(f"{prefix} {suffix}")
        else:
            print(prefix)

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_file or not os.path.exists(self.state_file):
            return build_personality_state()
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                base = build_personality_state()
                base.update(payload)
                base.setdefault("families", {})
                base.setdefault("regime_history", {})
                base.setdefault("memory", {})
                return base
        except Exception:
            pass
        return build_personality_state()

    def _save_state(self, payload: Dict[str, Any]):
        if not self.state_file:
            return
        try:
            folder = os.path.dirname(self.state_file)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
        except Exception:
            return

    def _family_metrics(self, market: Dict[str, Any], quote: Any, ws_snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
        ws_snapshot = ws_snapshot if isinstance(ws_snapshot, dict) else {}
        yes_bid = _safe_float(getattr(quote, "yes_bid", None), 0.0)
        yes_ask = _safe_float(getattr(quote, "yes_ask", None), 0.0)
        last_price = _safe_float(getattr(quote, "last_price", None), 0.0)
        spread = max(0.0, yes_ask - yes_bid) if yes_ask > 0 and yes_bid > 0 else _safe_float(ws_snapshot.get("spread"), 0.0)
        liquidity = _safe_float(getattr(quote, "liquidity", None), 0.0)
        ws_liquidity = _safe_float(ws_snapshot.get("liquidity"), 0.0)
        if ws_liquidity > 0:
            liquidity = ws_liquidity
        volume = _safe_float(getattr(quote, "volume", None), 0.0)
        ws_volume = _safe_float(ws_snapshot.get("volume"), 0.0)
        if ws_volume > 0:
            volume = ws_volume
        orderbook_imbalance = _safe_float(ws_snapshot.get("orderbook_imbalance"), 0.0)
        price_velocity = _safe_float(ws_snapshot.get("price_velocity"), 0.0)
        spread_delta = _safe_float(ws_snapshot.get("spread_delta"), 0.0)
        liquidity_delta = _safe_float(ws_snapshot.get("liquidity_delta"), 0.0)
        directional_persistence = _safe_float(ws_snapshot.get("directional_persistence"), 0.0)
        volatility = _safe_float(ws_snapshot.get("volatility"), min(1.0, abs(spread_delta) * 10.0 + abs(price_velocity) * 2.0))
        title = str(market.get("title") or market.get("subtitle") or "").strip()
        return {
            "ticker": str(market.get("ticker") or "").strip(),
            "family": str(market.get("family") or "").strip().upper() or str(market.get("ticker") or "").strip(),
            "category": str(market.get("category") or market.get("event_category") or market.get("market_category") or "unknown").strip().lower() or "unknown",
            "title": title,
            "spread": spread,
            "liquidity": liquidity,
            "volume": volume,
            "last_price": last_price,
            "price_velocity": price_velocity,
            "spread_delta": spread_delta,
            "liquidity_delta": liquidity_delta,
            "orderbook_imbalance": orderbook_imbalance,
            "directional_persistence": directional_persistence,
            "volatility": volatility,
            "ws_age_seconds": _safe_float(ws_snapshot.get("age_seconds"), 0.0),
            "ws_stale": bool(ws_snapshot.get("stale")),
            "ws_source": str(ws_snapshot.get("source") or "rest").strip() or "rest",
        }

    def observe_market_cycle(
        self,
        markets: List[Dict[str, Any]] | None = None,
        quotes: List[Any] | None = None,
        market_personality_profiles: Dict[str, Any] | None = None,
        websocket_manager: Any | None = None,
        live_select_summary: Dict[str, Any] | None = None,
        source: str = "live",
        force: bool = False,
    ) -> Dict[str, Any]:
        if not self.enabled:
            summary = {
                "enabled": False,
                "source": source,
                "reason": "disabled",
                "families": {},
                "top_families": [],
                "weak_families": [],
                "regime_counts": {},
            }
            self.last_summary = summary
            self._log("[PERSONALITY-SUMMARY]", enabled="no", source=source, reason="disabled")
            return summary
        markets = markets or []
        quotes = quotes or []
        live_select_summary = live_select_summary or {}
        profile_payload = market_personality_profiles if isinstance(market_personality_profiles, dict) else {}
        family_profiles = dict(profile_payload.get("families", {}) or {})
        quote_lookup = {str(getattr(q, "ticker", "") or "").strip(): q for q in quotes if str(getattr(q, "ticker", "") or "").strip()}
        family_observations: Dict[str, Dict[str, Any]] = {}
        for market in markets:
            ticker = str(market.get("ticker") or "").strip()
            if not ticker:
                continue
            family = str(market.get("family") or "").strip().upper() or str(market.get("event_ticker") or ticker).strip().upper()
            quote = quote_lookup.get(ticker)
            if quote is None:
                continue
            ws_snapshot = websocket_manager.get_ticker_snapshot(ticker, market=market) if websocket_manager is not None else {}
            metrics = self._family_metrics(market, quote, ws_snapshot=ws_snapshot)
            observation = family_observations.setdefault(
                family,
                {
                    "family": family,
                    "category": str(metrics.get("category") or "unknown").strip().lower() or "unknown",
                    "count": 0,
                    "spread_total": 0.0,
                    "liquidity_total": 0.0,
                    "volume_total": 0.0,
                    "volatility_total": 0.0,
                    "price_velocity_total": 0.0,
                    "spread_delta_total": 0.0,
                    "liquidity_delta_total": 0.0,
                    "persistence_total": 0.0,
                    "imbalance_total": 0.0,
                    "ws_stale_count": 0,
                    "latest_ticker": ticker,
                    "ws_source": metrics["ws_source"],
                    "last_seen_at": time.time(),
                },
            )
            observation["count"] += 1
            observation["spread_total"] += metrics["spread"]
            observation["liquidity_total"] += metrics["liquidity"]
            observation["volume_total"] += metrics["volume"]
            observation["volatility_total"] += metrics["volatility"]
            observation["price_velocity_total"] += metrics["price_velocity"]
            observation["spread_delta_total"] += metrics["spread_delta"]
            observation["liquidity_delta_total"] += metrics["liquidity_delta"]
            observation["persistence_total"] += metrics["directional_persistence"]
            observation["imbalance_total"] += metrics["orderbook_imbalance"]
            if metrics["ws_stale"]:
                observation["ws_stale_count"] += 1
            observation["latest_ticker"] = ticker
            observation["ws_source"] = metrics["ws_source"]
        current_state = self._load_state()
        families_state = current_state.setdefault("families", {})
        regime_history = current_state.setdefault("regime_history", {})
        now_ts = time.time()
        family_summaries: Dict[str, Dict[str, Any]] = {}
        top_log_candidates = []
        weak_log_candidates = []
        current_regime_counts: Dict[str, int] = {}
        for family, observation in family_observations.items():
            count = max(1, int(observation.get("count", 0) or 0))
            profile = normalize_personality_profile(family_profiles.get(family, {}), family=family, category=observation.get("category", "unknown"))
            avg_spread = observation["spread_total"] / count
            avg_liquidity = observation["liquidity_total"] / count
            avg_volume = observation["volume_total"] / count
            avg_volatility = observation["volatility_total"] / count
            avg_velocity = observation["price_velocity_total"] / count
            avg_spread_delta = observation["spread_delta_total"] / count
            avg_liquidity_delta = observation["liquidity_delta_total"] / count
            avg_persistence = observation["persistence_total"] / count
            avg_imbalance = observation["imbalance_total"] / count
            family_context = {
                "spread": avg_spread,
                "liquidity": avg_liquidity,
                "momentum": avg_persistence,
                "volatility": avg_volatility,
                "price_velocity": avg_velocity,
                "spread_delta": avg_spread_delta,
                "liquidity_delta": avg_liquidity_delta,
                "directional_persistence": avg_persistence,
                "orderbook_imbalance": avg_imbalance,
            }
            regime = detect_market_regime(family_context, profile=profile, ws_snapshot={"orderbook_imbalance": avg_imbalance})
            thresholds = compute_adaptive_thresholds(
                regime,
                profile=profile,
                base_spread_limit=0.06 if observation.get("category") != "sports" else 0.05,
                min_liquidity=max(1.0, avg_liquidity * 0.40),
                required_history_points=6,
                cooldown_seconds=120,
                exit_patience_seconds=180,
                max_shift=self.max_threshold_shift,
            )
            family_summary = {
                "family": family,
                "category": observation.get("category", "unknown"),
                "personality_type": profile.get("personality_type", "balanced"),
                "regime": regime.get("regime"),
                "regime_confidence": regime.get("regime_confidence"),
                "volatility_state": regime.get("volatility_state"),
                "liquidity_state": regime.get("liquidity_state"),
                "momentum_state": regime.get("momentum_state"),
                "entry_priority_score": thresholds.get("priority_boost", 0.5),
                "thresholds": thresholds,
                "profile": profile,
                "observations": count,
                "avg_spread": round(avg_spread, 4),
                "avg_liquidity": round(avg_liquidity, 4),
                "avg_volume": round(avg_volume, 4),
                "avg_volatility": round(avg_volatility, 4),
                "avg_velocity": round(avg_velocity, 4),
                "avg_spread_delta": round(avg_spread_delta, 4),
                "avg_liquidity_delta": round(avg_liquidity_delta, 4),
                "avg_persistence": round(avg_persistence, 4),
                "avg_imbalance": round(avg_imbalance, 4),
                "ws_stale_count": int(observation.get("ws_stale_count", 0) or 0),
                "latest_ticker": observation.get("latest_ticker"),
                "ws_source": observation.get("ws_source"),
            }
            family_summaries[family] = family_summary
            current_regime_counts[regime.get("regime")] = current_regime_counts.get(regime.get("regime"), 0) + 1
            prev_regime = self.last_regime_by_family.get(family)
            if prev_regime is None:
                self._log(
                    "[PERSONALITY-CREATE]",
                    family=family,
                    category=family_summary.get("category", "unknown"),
                    personality_type=family_summary["personality_type"],
                    regime=family_summary["regime"],
                )
            elif prev_regime != family_summary["regime"]:
                self._log(
                    "[REGIME-SHIFT]",
                    family=family,
                    previous=prev_regime,
                    current=family_summary["regime"],
                    confidence=family_summary["regime_confidence"],
                )
            self._log(
                "[REGIME-DETECTED]",
                family=family,
                regime=family_summary["regime"],
                confidence=family_summary["regime_confidence"],
                volatility_state=family_summary["volatility_state"],
                liquidity_state=family_summary["liquidity_state"],
                momentum_state=family_summary["momentum_state"],
            )
            self._log(
                "[ADAPTIVE-THRESHOLD]",
                family=family,
                regime=family_summary["regime"],
                spread_limit=thresholds.get("spread_limit"),
                min_liquidity=thresholds.get("min_liquidity"),
                required_history_points=thresholds.get("required_history_points"),
                cooldown_seconds=thresholds.get("cooldown_seconds"),
                exit_patience_seconds=thresholds.get("exit_patience_seconds"),
                pullback_strictness=thresholds.get("pullback_strictness"),
                priority_boost=thresholds.get("priority_boost"),
            )
            if float(thresholds.get("priority_boost", 0.0) or 0.0) >= 0.60:
                self._log(
                    "[PERSONALITY-PRIORITY]",
                    family=family,
                    regime=family_summary["regime"],
                    priority_score=thresholds.get("priority_boost"),
                    personality_type=family_summary["personality_type"],
                )
            elif float(thresholds.get("priority_boost", 0.0) or 0.0) <= 0.30:
                self._log(
                    "[PERSONALITY-DEPRIORITIZED]",
                    family=family,
                    regime=family_summary["regime"],
                    priority_score=thresholds.get("priority_boost"),
                    personality_type=family_summary["personality_type"],
                )
            self._log(
                "[PERSONALITY-UPDATE]",
                family=family,
                personality_type=family_summary["personality_type"],
                entry_priority_score=thresholds.get("priority_boost"),
                avg_spread=family_summary["avg_spread"],
                avg_liquidity=family_summary["avg_liquidity"],
                avg_volatility=family_summary["avg_volatility"],
                observations=family_summary["observations"],
            )
            family_stats = current_state.setdefault("memory", {}).setdefault("family_stats", {})
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
            stats["observations"] += count
            stats["last_seen_at"] = now_ts
            stats["regime_counts"][family_summary["regime"]] = stats["regime_counts"].get(family_summary["regime"], 0) + 1
            stats["personality_types"][family_summary["personality_type"]] = stats["personality_types"].get(family_summary["personality_type"], 0) + 1
            stats["best_priority_score"] = max(float(stats.get("best_priority_score", 0.0) or 0.0), float(thresholds.get("priority_boost", 0.0) or 0.0))
            if family_summary["regime"] == "DEAD" or thresholds.get("priority_boost", 0.0) <= 0.30:
                weak_log_candidates.append((float(thresholds.get("priority_boost", 0.0) or 0.0), family))
            else:
                top_log_candidates.append((float(thresholds.get("priority_boost", 0.0) or 0.0), family))
            self.memory.record(
                {
                    "family": family,
                    "regime": family_summary["regime"],
                    "personality_type": family_summary["personality_type"],
                    "result": "observed",
                    "priority_score": thresholds.get("priority_boost", 0.0),
                    "signal_reliability": profile.get("signal_reliability", 0.5),
                    "spread": family_summary["avg_spread"],
                    "liquidity": family_summary["avg_liquidity"],
                },
                persist=False,
            )
            self.last_regime_by_family[family] = family_summary["regime"]
            self.last_profile_by_family[family] = profile
            self.last_context_by_family[family] = {
                "family": family,
                "profile": profile,
                "regime": regime,
                "thresholds": thresholds,
                "family_summary": family_summary,
            }

        if not family_summaries and family_profiles:
            for family, profile_raw in family_profiles.items():
                profile = normalize_personality_profile(profile_raw, family=family, category=str((profile_raw or {}).get("category") or "unknown"))
                family_summary = {
                    "family": family,
                    "category": profile.get("category", "unknown"),
                    "personality_type": profile.get("personality_type", "balanced"),
                    "regime": "CHOPPY",
                    "regime_confidence": 0.45,
                    "volatility_state": "calm",
                    "liquidity_state": "mixed",
                    "momentum_state": "mixed",
                    "entry_priority_score": 0.35,
                    "thresholds": compute_adaptive_thresholds({"regime": "CHOPPY"}, profile=profile, max_shift=self.max_threshold_shift),
                    "profile": profile,
                    "observations": 0,
                    "avg_spread": profile.get("avg_spread", 0.0),
                    "avg_liquidity": profile.get("avg_liquidity", 0.0),
                    "avg_volume": 0.0,
                    "avg_volatility": profile.get("volatility", 0.0),
                    "avg_velocity": 0.0,
                    "avg_spread_delta": 0.0,
                    "avg_liquidity_delta": 0.0,
                    "avg_persistence": profile.get("momentum_persistence", 0.0),
                    "avg_imbalance": 0.0,
                    "ws_stale_count": 0,
                    "latest_ticker": None,
                    "ws_source": "profile_only",
                }
                family_summaries[family] = family_summary
                top_log_candidates.append((0.35, family))
                self.last_context_by_family[family] = {
                    "family": family,
                    "profile": profile,
                    "regime": {"regime": "CHOPPY", "regime_confidence": 0.45},
                    "thresholds": family_summary["thresholds"],
                    "family_summary": family_summary,
                }

        if family_summaries:
            self.memory.persist()
            current_state["version"] = int(current_state.get("version", 1) or 1)
            current_state["updated_at"] = now_ts
            current_state.setdefault("memory", {})
            current_state["memory"]["updated_at"] = now_ts
            current_state["memory"]["regime_counts"] = current_regime_counts
            current_state["memory"]["events"] = self.memory.state.get("events", [])[-self.history_limit :]
            current_state["memory"]["family_stats"] = self.memory.state.get("family_stats", {})
            self._save_state(current_state)

        top_families = sorted(top_log_candidates, key=lambda item: item[0], reverse=True)[:5]
        weak_families = sorted(weak_log_candidates, key=lambda item: item[0])[:5]
        regime_counts = current_regime_counts or self.memory.state.get("regime_counts", {})
        summary = {
            "enabled": True,
            "source": source,
            "families": family_summaries,
            "regime_counts": regime_counts,
            "top_families": [
                {"family": family, "priority": round(priority, 4), "regime": family_summaries.get(family, {}).get("regime")}
                for priority, family in top_families
            ],
            "weak_families": [
                {"family": family, "priority": round(priority, 4), "regime": family_summaries.get(family, {}).get("regime")}
                for priority, family in weak_families
            ],
            "live_select_summary": live_select_summary,
            "updated_at": now_ts,
        }
        self.last_summary = summary
        if force or (now_ts - self.last_emit_ts) >= self.summary_interval_seconds:
            self.last_emit_ts = now_ts
            self._log(
                "[PERSONALITY-MEMORY]",
                source=source,
                recent_events=len(self.memory.state.get("events", [])),
                medium_events=len(summary.get("medium_events", [])),
                families=len(family_summaries),
            )
            self._log(
                "[PERSONALITY-PERFORMANCE]",
                source=source,
                top_families=",".join(item["family"] for item in summary["top_families"][:5]) or "none",
                weak_families=",".join(item["family"] for item in summary["weak_families"][:5]) or "none",
            )
            self._log(
                "[REGIME-SUMMARY]",
                source=source,
                regimes=",".join(f"{key}:{value}" for key, value in sorted(regime_counts.items(), key=lambda item: item[1], reverse=True)[:5]) or "none",
            )
            self._log(
                "[PERSONALITY-HEALTH]",
                source=source,
                total_families=len(family_summaries),
                top_families=",".join(item["family"] for item in summary["top_families"][:5]) or "none",
                weak_families=",".join(item["family"] for item in summary["weak_families"][:5]) or "none",
                regime_counts=regime_counts,
            )
            self._log(
                "[PERSONALITY-TOP]",
                source=source,
                families=",".join(item["family"] for item in summary["top_families"][:5]) or "none",
            )
            self._log(
                "[PERSONALITY-WEAK]",
                source=source,
                families=",".join(item["family"] for item in summary["weak_families"][:5]) or "none",
            )
            self._log(
                "[PERSONALITY-SUMMARY]",
                source=source,
                total_families=len(family_summaries),
                regimes=",".join(f"{key}:{value}" for key, value in sorted(regime_counts.items(), key=lambda item: item[1], reverse=True)[:5]) or "none",
            )
        return summary

    def get_entry_context(
        self,
        market: Dict[str, Any] | None,
        quote: Any,
        family: str,
        market_personality_profiles: Dict[str, Any] | None = None,
        ws_snapshot: Dict[str, Any] | None = None,
        live_select_summary: Dict[str, Any] | None = None,
        stage_label: str = "entry_timing",
    ) -> Dict[str, Any]:
        if not self.enabled:
            profile = normalize_personality_profile({}, family=family, category=str((market or {}).get("category") or "unknown"))
            regime = detect_market_regime({}, profile=profile, ws_snapshot=ws_snapshot)
            thresholds = compute_adaptive_thresholds(regime, profile=profile, max_shift=self.max_threshold_shift)
            return {
                "enabled": False,
                "family": family,
                "profile": profile,
                "regime": regime,
                "adaptive_thresholds": thresholds,
                "memory": self.memory.summarize_family(family),
                "priority_score": thresholds.get("priority_boost", 0.5),
                "advisory_reason": "disabled",
            }
        profile_payload = market_personality_profiles if isinstance(market_personality_profiles, dict) else {}
        family_profile = normalize_personality_profile((profile_payload.get("families", {}) or {}).get(family, {}), family=family, category=str((market or {}).get("category") or "unknown"))
        metrics = self._family_metrics(market or {}, quote, ws_snapshot=ws_snapshot)
        regime = detect_market_regime(metrics, profile=family_profile, ws_snapshot=ws_snapshot)
        thresholds = compute_adaptive_thresholds(
            regime,
            profile=family_profile,
            base_spread_limit=0.06 if (market or {}).get("category") != "sports" else 0.05,
            min_liquidity=max(1.0, metrics.get("liquidity", 0.0) * 0.40),
            required_history_points=6,
            cooldown_seconds=120,
            exit_patience_seconds=180,
            max_shift=self.max_threshold_shift,
        )
        memory = self.memory.summarize_family(family)
        personality_bias = family_profile.get("personality_type", "balanced")
        priority_score = _clamp(
            thresholds.get("priority_boost", 0.5)
            + (0.08 if personality_bias == "momentum_heavy" and regime.get("regime") in {"TRENDING", "MOMENTUM_CONTINUATION"} else 0.0)
            - (0.10 if personality_bias in {"unstable", "fake_breakout_prone"} else 0.0)
            - (0.08 if regime.get("regime") in {"DEAD", "SPIKE_EVENT"} else 0.0)
            + (0.04 if memory.get("entry_ready_rate", 0.0) >= 0.60 else 0.0),
        )
        advisory_block_reason = None
        if regime.get("regime") == "DEAD":
            advisory_block_reason = "dead_liquidity_personality"
        elif family_profile.get("fake_breakout_frequency", 0.0) >= 0.70:
            advisory_block_reason = "fake_breakout_prone"
        elif family_profile.get("signal_reliability", 0.5) < 0.30:
            advisory_block_reason = "low_signal_reliability"
        result = {
            "enabled": True,
            "family": family,
            "profile": family_profile,
            "metrics": metrics,
            "regime": regime,
            "adaptive_thresholds": thresholds,
            "memory": memory,
            "priority_score": round(priority_score, 4),
            "advisory_block_reason": advisory_block_reason,
            "advisory_reason": thresholds.get("adaptive_reason", "balanced"),
            "summary": {
                "family": family,
                "regime": regime.get("regime"),
                "personality_type": family_profile.get("personality_type"),
                "priority_score": round(priority_score, 4),
                "adaptive_reason": thresholds.get("adaptive_reason", "balanced"),
            },
        }
        self.last_context_by_family[family] = result
        return result

    def get_summary(self) -> Dict[str, Any]:
        return dict(self.last_summary or {})

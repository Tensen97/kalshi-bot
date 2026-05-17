import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from runtime.paths import ensure_runtime_layout, record_persist_event, runtime_env_path, runtime_state_file, safe_atomic_json_write


REALM_XVI_DEFAULT_STATE_FILE = runtime_env_path("REALM_XVI_STATE_FILE", runtime_state_file("cross_market_intelligence.json"))
REALM_XVI_ENABLE = (os.environ.get("REALM_XVI_ENABLE", "true") or "true").strip().lower() in {"1", "true", "yes", "on"}
REALM_XVI_MAX_SCORE_BOOST = float(os.environ.get("REALM_XVI_MAX_SCORE_BOOST", "0.05") or 0.05)
REALM_XVI_MAX_SCORE_PENALTY = float(os.environ.get("REALM_XVI_MAX_SCORE_PENALTY", "0.05") or 0.05)
REALM_XVI_MAX_RELATIONSHIPS = int(float(os.environ.get("REALM_XVI_MAX_RELATIONSHIPS", "120") or 120))
REALM_XVI_MAX_FAMILIES = int(float(os.environ.get("REALM_XVI_MAX_FAMILIES", "80") or 80))

CATEGORY_FAMILY_HINTS = {
    "crypto": ("BTC", "ETH", "KXBTC", "KXETH"),
    "sports": ("KXNBA", "KXWNBA", "KXMLB", "KXNHL", "KXNFL", "KXATP", "KXEPL", "KXTENNIS"),
    "weather": ("KXTEMP", "KXRAIN", "KXSNOW", "KXWEATHER"),
}


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_round(value, digits=4, default=None):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return default


def _clamp(value, minimum=-1.0, maximum=1.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def _utc_hour():
    return int(datetime.now(timezone.utc).hour)


def _family_from_market(market=None, ticker=None):
    market = market if isinstance(market, dict) else {}
    ticker = str(ticker or market.get("ticker") or market.get("series_ticker") or market.get("event_ticker") or "").strip().upper()
    if not ticker:
        return "UNKNOWN"
    return ticker.split("-", 1)[0] if "-" in ticker else ticker


class CrossMarketIntelligence:
    def __init__(self, state_file=REALM_XVI_DEFAULT_STATE_FILE, enabled=REALM_XVI_ENABLE):
        self.state_file = state_file
        self.enabled = bool(enabled)
        ensure_runtime_layout()
        self.state = self._load_state()
        self.last_cycle_summary = {}
        self.last_cycle_context = {}

    def _log(self, prefix, **fields):
        extras = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        print(f"{prefix} {extras}".strip())

    def _default_state(self):
        return {
            "schema_version": 1,
            "updated_at": None,
            "families": {},
            "relationships": {},
            "regime_history": [],
            "last_summary": {},
            "replay_context": {},
        }

    def _load_state(self):
        if not os.path.exists(self.state_file):
            return self._default_state()
        try:
            with open(self.state_file, "r") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                return self._default_state()
        except (OSError, ValueError, TypeError):
            return self._default_state()
        state = self._default_state()
        state.update(payload)
        if not isinstance(state.get("families"), dict):
            state["families"] = {}
        if not isinstance(state.get("relationships"), dict):
            state["relationships"] = {}
        if not isinstance(state.get("regime_history"), list):
            state["regime_history"] = []
        if not isinstance(state.get("last_summary"), dict):
            state["last_summary"] = {}
        if not isinstance(state.get("replay_context"), dict):
            state["replay_context"] = {}
        return state

    def _ensure_parent_dir(self):
        directory = os.path.dirname(self.state_file)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

    def _prune_state(self):
        families = self.state.get("families", {})
        if isinstance(families, dict) and len(families) > REALM_XVI_MAX_FAMILIES:
            ordered = sorted(families.items(), key=lambda item: (item[1].get("sample_count", 0), item[1].get("last_updated_ts", 0.0)), reverse=True)
            self.state["families"] = dict(ordered[:REALM_XVI_MAX_FAMILIES])
        relationships = self.state.get("relationships", {})
        if isinstance(relationships, dict) and len(relationships) > REALM_XVI_MAX_RELATIONSHIPS:
            ordered = sorted(relationships.items(), key=lambda item: (item[1].get("sample_count", 0), item[1].get("last_updated_ts", 0.0)), reverse=True)
            self.state["relationships"] = dict(ordered[:REALM_XVI_MAX_RELATIONSHIPS])
        history = list(self.state.get("regime_history", []) or [])
        if len(history) > 50:
            self.state["regime_history"] = history[-50:]

    def _save_state(self):
        try:
            self._prune_state()
            self.state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._ensure_parent_dir()
            ok = safe_atomic_json_write(self.state_file, self.state, sort_keys=True, indent=2)
            record_persist_event(subsystem="REALM-XVI", path=self.state_file, action="persist_state", success=bool(ok), reason=None if ok else "write_failed", details={"families": len(self.state.get("families", {}) or {}), "relationships": len(self.state.get("relationships", {}) or {})})
            if not ok:
                self._log("[REALM-XVI-DEGRADED]", reason="write_failed")
            return bool(ok)
        except Exception as exc:
            record_persist_event(subsystem="REALM-XVI", path=self.state_file, action="persist_state", success=False, reason=exc.__class__.__name__)
            self._log("[REALM-XVI-DEGRADED]", reason=exc.__class__.__name__)
            return False

    def _quote_mid(self, quote):
        yes_bid = _safe_float(getattr(quote, "yes_bid", None), None)
        yes_ask = _safe_float(getattr(quote, "yes_ask", None), None)
        if yes_bid is not None and yes_ask is not None:
            return (yes_bid + yes_ask) / 2.0
        if yes_ask is not None:
            return yes_ask
        if yes_bid is not None:
            return yes_bid
        return None

    def _market_metrics(self, market=None, quote=None):
        market = market if isinstance(market, dict) else {}
        family = _family_from_market(market, getattr(quote, "ticker", None))
        yes_bid = _safe_float(getattr(quote, "yes_bid", None), None)
        yes_ask = _safe_float(getattr(quote, "yes_ask", None), None)
        no_bid = _safe_float(getattr(quote, "no_bid", None), None)
        no_ask = _safe_float(getattr(quote, "no_ask", None), None)
        last_price = _safe_float(getattr(quote, "last_price", None), None)
        liquidity = _safe_float(getattr(quote, "liquidity", None), None)
        volume = _safe_float(getattr(quote, "volume", None), None)
        spread = None
        if yes_bid is not None and yes_ask is not None:
            spread = max(0.0, yes_ask - yes_bid)
        if liquidity is None:
            liquidity = _safe_float(market.get("liquidity_dollars", market.get("liquidity")), 0.0)
        if volume is None:
            volume = _safe_float(market.get("volume_fp", market.get("volume")), 0.0)
        mid = self._quote_mid(quote)
        directional_pressure = 0.0
        if mid is not None and last_price is not None:
            directional_pressure = _clamp((last_price - mid) * 10.0)
        spread_penalty = 0.0 if spread is None else _clamp(spread / 0.06)
        liquidity_bonus = _clamp((liquidity or 0.0) / 150.0)
        volume_bonus = _clamp((volume or 0.0) / 200.0)
        confidence = _clamp(0.55 + (liquidity_bonus * 0.20) + (volume_bonus * 0.10) - (spread_penalty * 0.30) + abs(directional_pressure) * 0.10, 0.0, 1.0)
        sentiment_score = _clamp(directional_pressure + liquidity_bonus * 0.10 - spread_penalty * 0.10)
        if spread is not None and spread >= 0.06:
            volatility_state = "high"
        elif spread is not None and spread >= 0.035:
            volatility_state = "elevated"
        elif abs(directional_pressure) >= 0.30:
            volatility_state = "elevated"
        else:
            volatility_state = "low"
        regime_label = "normal"
        if spread is not None and spread >= 0.07:
            regime_label = "unstable_spread_conditions"
        elif (liquidity or 0.0) <= 15.0:
            regime_label = "low_liquidity"
        elif volatility_state == "high" and abs(directional_pressure) < 0.15:
            regime_label = "choppy"
        elif volatility_state == "high" and abs(directional_pressure) >= 0.30:
            regime_label = "high_volatility"
        elif abs(directional_pressure) >= 0.40 and (liquidity or 0.0) >= 30.0:
            regime_label = "trending"
        elif (liquidity or 0.0) < 30.0 and _utc_hour() in {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}:
            regime_label = "thin_overnight_markets"
        elif abs(directional_pressure) >= 0.22:
            regime_label = "strong_directional_momentum"
        return {
            "family": family,
            "ticker": str(getattr(quote, "ticker", "") or market.get("ticker", "") or "").strip(),
            "category": str(market.get("category") or market.get("market_category") or "unknown").strip().lower() or "unknown",
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "spread": spread,
            "liquidity": liquidity,
            "volume": volume,
            "last_price": last_price,
            "mid": mid,
            "directional_pressure": directional_pressure,
            "sentiment_score": sentiment_score,
            "confidence": confidence,
            "volatility_state": volatility_state,
            "regime_label": regime_label,
        }

    def _aggregate_family_profile(self, family, metrics_list):
        if not metrics_list:
            return {
                "family": family,
                "sample_count": 0,
                "avg_confidence": 0.0,
                "avg_spread": 0.0,
                "avg_liquidity": 0.0,
                "direction_balance": 0,
                "sentiment": "neutral",
                "volatility_state": "low",
                "regime_label": "unknown",
                "last_updated_ts": time.time(),
            }
        sample_count = len(metrics_list)
        avg_confidence = sum(item.get("confidence", 0.0) or 0.0 for item in metrics_list) / sample_count
        spread_values = [item.get("spread") for item in metrics_list if item.get("spread") is not None]
        avg_spread = sum(spread_values) / len(spread_values) if spread_values else 0.0
        liquidity_values = [item.get("liquidity") for item in metrics_list if item.get("liquidity") is not None]
        avg_liquidity = sum(liquidity_values) / len(liquidity_values) if liquidity_values else 0.0
        pressure_values = [item.get("directional_pressure", 0.0) or 0.0 for item in metrics_list]
        pressure_avg = sum(pressure_values) / sample_count
        bullish = sum(1 for value in pressure_values if value > 0.10)
        bearish = sum(1 for value in pressure_values if value < -0.10)
        direction_balance = bullish - bearish
        sentiment = "neutral"
        if direction_balance >= 2:
            sentiment = "bullish"
        elif direction_balance <= -2:
            sentiment = "bearish"
        volatility_hits = sum(1 for item in metrics_list if item.get("volatility_state") in {"high", "elevated"})
        volatility_state = "high" if volatility_hits / sample_count >= 0.60 else ("elevated" if volatility_hits / sample_count >= 0.30 else "low")
        regime_label = "normal"
        if avg_liquidity < 15.0:
            regime_label = "low_liquidity"
        elif avg_spread >= 0.06:
            regime_label = "unstable_spread_conditions"
        elif volatility_state == "high" and abs(pressure_avg) < 0.15:
            regime_label = "choppy"
        elif volatility_state == "high":
            regime_label = "high_volatility"
        elif abs(pressure_avg) >= 0.40:
            regime_label = "trending"
        elif abs(pressure_avg) >= 0.22:
            regime_label = "strong_directional_momentum"
        elif _utc_hour() in {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11} and avg_liquidity < 40.0:
            regime_label = "thin_overnight_markets"
        profile = {
            "family": family,
            "sample_count": sample_count,
            "avg_confidence": _safe_round(avg_confidence, 4, 0.0),
            "avg_spread": _safe_round(avg_spread, 4, 0.0),
            "avg_liquidity": _safe_round(avg_liquidity, 4, 0.0),
            "direction_balance": direction_balance,
            "pressure_average": _safe_round(pressure_avg, 4, 0.0),
            "sentiment": sentiment,
            "volatility_state": volatility_state,
            "regime_label": regime_label,
            "last_updated_ts": time.time(),
            "last_ticker": metrics_list[-1].get("ticker"),
        }
        self.state.setdefault("families", {})[family] = profile
        self._log(
            "[REALM-XVI-FAMILY]",
            family=family,
            sentiment=sentiment,
            avg_confidence=profile["avg_confidence"],
            avg_spread=profile["avg_spread"],
            avg_liquidity=profile["avg_liquidity"],
            volatility_state=volatility_state,
            regime_label=regime_label,
        )
        self._log(
            "[REALM-XVI-SENTIMENT]",
            family=family,
            directional_pressure=profile["pressure_average"],
            sentiment_score=_safe_round(sum(item.get("sentiment_score", 0.0) or 0.0 for item in metrics_list) / sample_count, 4, 0.0),
            confidence=profile["avg_confidence"],
            volatility_state=volatility_state,
        )
        return profile

    def _relation_key(self, family_a, family_b):
        ordered = sorted([str(family_a or "UNKNOWN").strip().upper(), str(family_b or "UNKNOWN").strip().upper()])
        return "|".join(ordered)

    def _update_relationship(self, family_a, family_b, sentiment_a, sentiment_b, volatility_a=None, volatility_b=None):
        if family_a == family_b:
            return None
        key = self._relation_key(family_a, family_b)
        record = dict(self.state.get("relationships", {}).get(key, {}) or {})
        sample_count = int(record.get("sample_count", 0) or 0) + 1
        positive_strength = float(record.get("positive_strength", 0.0) or 0.0)
        inverse_strength = float(record.get("inverse_strength", 0.0) or 0.0)
        if sentiment_a == 0 or sentiment_b == 0:
            agreement = 0.5
            inverse = 0.0
        else:
            agreement = 1.0 if sentiment_a * sentiment_b > 0 else 0.0
            inverse = 1.0 if sentiment_a * sentiment_b < 0 else 0.0
        volatility_alignment = 1.0 if volatility_a == volatility_b and volatility_a is not None else 0.0
        positive_strength = (positive_strength * 0.9) + (agreement * 0.1)
        inverse_strength = (inverse_strength * 0.9) + (inverse * 0.1)
        record.update({
            "family_a": str(family_a),
            "family_b": str(family_b),
            "sample_count": sample_count,
            "positive_strength": _safe_round(positive_strength, 4, 0.0),
            "inverse_strength": _safe_round(inverse_strength, 4, 0.0),
            "volatility_alignment": _safe_round((float(record.get("volatility_alignment", 0.0) or 0.0) * 0.9) + (volatility_alignment * 0.1), 4, 0.0),
            "confidence": _safe_round(min(1.0, sample_count / 20.0), 4, 0.0),
            "last_updated_ts": time.time(),
        })
        self.state.setdefault("relationships", {})[key] = record
        self._log(
            "[REALM-XVI-CORRELATION]",
            pair=f"{family_a}↔{family_b}",
            positive_strength=record["positive_strength"],
            inverse_strength=record["inverse_strength"],
            confidence=record["confidence"],
            samples=sample_count,
        )
        return record

    def _classify_cycle_regime(self, family_profiles, summary=None):
        summary = summary if isinstance(summary, dict) else {}
        if not family_profiles:
            return {
                "regime_label": "unknown",
                "regime_confidence": 0.0,
                "regime_risk_level": "low",
                "dominant_regime": "unknown",
                "overall_volatility": "unknown",
                "overall_direction": 0.0,
                "avg_confidence": 0.0,
                "avg_spread": 0.0,
                "avg_liquidity": 0.0,
                "high_risk_families": [],
            }
        family_items = list(family_profiles.values())
        avg_confidence = sum(item.get("avg_confidence", 0.0) or 0.0 for item in family_items) / len(family_items)
        avg_spread = sum(item.get("avg_spread", 0.0) or 0.0 for item in family_items) / len(family_items)
        avg_liquidity = sum(item.get("avg_liquidity", 0.0) or 0.0 for item in family_items) / len(family_items)
        bullish = sum(1 for item in family_items if item.get("sentiment") == "bullish")
        bearish = sum(1 for item in family_items if item.get("sentiment") == "bearish")
        neutral = len(family_items) - bullish - bearish
        overall_direction = (bullish - bearish) / max(len(family_items), 1)
        spread_values = [item.get("avg_spread", 0.0) or 0.0 for item in family_items]
        spread_mean = sum(spread_values) / len(spread_values)
        spread_variance = sum((value - spread_mean) ** 2 for value in spread_values) / len(spread_values) if len(spread_values) > 1 else 0.0
        volatility_ratio = sum(1 for item in family_items if item.get("volatility_state") in {"high", "elevated"}) / len(family_items)
        high_risk_families = [item.get("family") for item in family_items if (item.get("avg_liquidity", 0.0) or 0.0) < 20.0 or (item.get("avg_spread", 0.0) or 0.0) >= 0.06]
        regime_label = "neutral"
        if avg_liquidity < 15.0:
            regime_label = "low_liquidity"
        elif avg_spread >= 0.06 or spread_variance >= 0.0009:
            regime_label = "unstable_spread_conditions"
        elif volatility_ratio >= 0.60:
            regime_label = "high_volatility"
        elif abs(overall_direction) >= 0.35:
            regime_label = "trending"
        elif abs(overall_direction) <= 0.15 and spread_variance >= 0.0004:
            regime_label = "choppy"
        elif _utc_hour() in {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11} and avg_liquidity < 40.0:
            regime_label = "thin_overnight_markets"
        elif summary.get("news_like_volatility"):
            regime_label = "news_spike_behavior"
        if regime_label in {"low_liquidity", "unstable_spread_conditions", "news_spike_behavior"}:
            risk_level = "high"
        elif regime_label in {"high_volatility", "choppy", "thin_overnight_markets"}:
            risk_level = "medium"
        elif regime_label == "trending" and avg_confidence < 0.50:
            risk_level = "medium"
        else:
            risk_level = "low"
        regime_confidence = _clamp((abs(overall_direction) * 0.40) + (avg_confidence * 0.40) + (min(1.0, avg_liquidity / 100.0) * 0.20), 0.0, 1.0)
        overall_volatility = "elevated" if volatility_ratio >= 0.50 or avg_spread >= 0.04 else "normal"
        return {
            "regime_label": regime_label,
            "regime_confidence": _safe_round(regime_confidence, 4, 0.0),
            "regime_risk_level": risk_level,
            "dominant_regime": regime_label,
            "overall_volatility": overall_volatility,
            "overall_direction": _safe_round(overall_direction, 4, 0.0),
            "avg_confidence": _safe_round(avg_confidence, 4, 0.0),
            "avg_spread": _safe_round(avg_spread, 4, 0.0),
            "avg_liquidity": _safe_round(avg_liquidity, 4, 0.0),
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
            "high_risk_families": [family for family in high_risk_families if family],
        }

    def _strongest_alignment(self):
        strongest = None
        for key, record in self.state.get("relationships", {}).items():
            positive = _safe_float(record.get("positive_strength"), 0.0) or 0.0
            confidence = _safe_float(record.get("confidence"), 0.0) or 0.0
            score = positive * confidence
            if strongest is None or score > strongest[0]:
                strongest = (score, key)
        return strongest[1] if strongest else None

    def _largest_divergence(self, family_profiles):
        weakest = None
        for family, profile in family_profiles.items():
            direction_balance = int(profile.get("direction_balance", 0) or 0)
            confidence = _safe_float(profile.get("avg_confidence"), 0.0) or 0.0
            score = abs(direction_balance) * confidence
            if weakest is None or score > weakest[0]:
                weakest = (score, family)
        return weakest[1] if weakest else None

    def _family_matches_category(self, family, category):
        family = str(family or "").strip().upper()
        return any(family.startswith(prefix) for prefix in CATEGORY_FAMILY_HINTS.get(category, ()))

    def _pick_category_sentiment(self, family_profiles, category):
        families = [profile for family, profile in family_profiles.items() if self._family_matches_category(family, category)]
        if not families:
            return "unknown"
        score = sum(1 if profile.get("sentiment") == "bullish" else -1 if profile.get("sentiment") == "bearish" else 0 for profile in families)
        if score > 0:
            return "bullish"
        if score < 0:
            return "bearish"
        return "neutral"

    def _context_reason(self, profile, cycle_regime, relationship_bias, divergence_penalty):
        reasons = []
        if profile.get("sentiment") == "bullish":
            reasons.append("family_bullish")
        elif profile.get("sentiment") == "bearish":
            reasons.append("family_bearish")
        if cycle_regime.get("regime_label") == "trending":
            reasons.append("trend_support")
        elif cycle_regime.get("regime_label") in {"low_liquidity", "unstable_spread_conditions", "choppy", "high_volatility", "thin_overnight_markets"}:
            reasons.append("risk_environment")
        if relationship_bias > 0:
            reasons.append("aligned_pairing")
        if divergence_penalty > 0:
            reasons.append("divergence_penalty")
        return ",".join(reasons) if reasons else "neutral"

    def _shadow_influence(self, profile, cycle_regime, relationship_bias=0.0, divergence_penalty=0.0):
        sentiment = str(profile.get("sentiment") or "neutral")
        regime_label = str(cycle_regime.get("regime_label") or "neutral")
        avg_confidence = _safe_float(profile.get("avg_confidence"), 0.0) or 0.0
        avg_spread = _safe_float(profile.get("avg_spread"), 0.0) or 0.0
        avg_liquidity = _safe_float(profile.get("avg_liquidity"), 0.0) or 0.0
        raw = 0.0
        if sentiment == "bullish":
            raw += 0.015
        elif sentiment == "bearish":
            raw -= 0.015
        if regime_label == "trending":
            raw += 0.010
        elif regime_label in {"low_liquidity", "unstable_spread_conditions", "high_volatility", "choppy", "thin_overnight_markets"}:
            raw -= 0.010
        raw += relationship_bias * 0.010
        raw -= divergence_penalty * 0.020
        raw += max(0.0, avg_confidence - 0.55) * 0.010
        raw -= max(0.0, avg_spread - 0.04) * 0.05
        raw += min(0.01, avg_liquidity / 1000.0)
        confidence_adjustment = _clamp(raw, -REALM_XVI_MAX_SCORE_PENALTY, REALM_XVI_MAX_SCORE_BOOST)
        score_delta = confidence_adjustment * 20.0
        return confidence_adjustment, score_delta

    def analyze_cycle(self, markets=None, quotes=None, candidates=None, selected_quote=None, live_select_summary=None, realm_xv_snapshots=None, source="live"):
        if not self.enabled:
            self._log("[REALM-XVI-DEGRADED-MODE]", reason="disabled")
            self.last_cycle_summary = {
                "regime_label": "disabled",
                "regime_confidence": 0.0,
                "regime_risk_level": "low",
                "dominant_regime": "disabled",
                "overall_volatility": "unknown",
                "high_risk_families": [],
                "family_profiles": {},
                "relationships": {},
                "context_influences": {},
            }
            return self.last_cycle_summary
        markets = markets or []
        quotes = quotes or []
        live_select_summary = live_select_summary or {}
        markets_by_ticker = {str((market or {}).get("ticker", "") or "").strip(): market for market in markets if isinstance(market, dict)}
        quote_by_ticker = {str(getattr(quote, "ticker", "") or "").strip(): quote for quote in quotes if getattr(quote, "ticker", None)}
        family_metrics = defaultdict(list)
        for ticker, quote in quote_by_ticker.items():
            market = markets_by_ticker.get(ticker, {})
            metrics = self._market_metrics(market=market, quote=quote)
            family_metrics[metrics["family"]].append(metrics)
        family_profiles = {}
        for family, metrics_list in family_metrics.items():
            family_profiles[family] = self._aggregate_family_profile(family, metrics_list)
            self.state.setdefault("families", {})[family] = family_profiles[family]
        family_names = list(family_profiles.keys())
        for i, family_a in enumerate(family_names):
            for family_b in family_names[i + 1:]:
                sentiment_a = 1 if family_profiles[family_a].get("sentiment") == "bullish" else -1 if family_profiles[family_a].get("sentiment") == "bearish" else 0
                sentiment_b = 1 if family_profiles[family_b].get("sentiment") == "bullish" else -1 if family_profiles[family_b].get("sentiment") == "bearish" else 0
                self._update_relationship(family_a, family_b, sentiment_a, sentiment_b, family_profiles[family_a].get("volatility_state"), family_profiles[family_b].get("volatility_state"))
        cycle_regime = self._classify_cycle_regime(family_profiles, summary=live_select_summary)
        self.state.setdefault("regime_history", []).append({
            "ts": time.time(),
            "regime_label": cycle_regime.get("regime_label"),
            "regime_confidence": cycle_regime.get("regime_confidence"),
            "risk_level": cycle_regime.get("regime_risk_level"),
        })
        self.state["last_summary"] = cycle_regime
        strongest_alignment = self._strongest_alignment()
        largest_divergence = self._largest_divergence(family_profiles)
        context_influences = {}
        for ticker, quote in quote_by_ticker.items():
            market = markets_by_ticker.get(ticker, {})
            metrics = self._market_metrics(market=market, quote=quote)
            family = metrics.get("family", "UNKNOWN")
            profile = family_profiles.get(family, metrics)
            relation_bias = 0.0
            if strongest_alignment:
                relation = self.state.get("relationships", {}).get(strongest_alignment, {})
                if family in {relation.get("family_a"), relation.get("family_b")}:
                    relation_bias += (_safe_float(relation.get("positive_strength"), 0.0) or 0.0) - (_safe_float(relation.get("inverse_strength"), 0.0) or 0.0)
            divergence_penalty = 0.0
            if largest_divergence and family == largest_divergence:
                divergence_penalty = abs(_safe_float(profile.get("direction_balance"), 0.0) or 0.0) / max(profile.get("sample_count", 1), 1)
            confidence_adjustment, score_delta = self._shadow_influence(profile, cycle_regime, relationship_bias=relation_bias, divergence_penalty=divergence_penalty)
            reason = self._context_reason(profile, cycle_regime, relation_bias, divergence_penalty)
            context_influences[ticker] = {
                "family": family,
                "confidence_adjustment": _safe_round(confidence_adjustment, 4, 0.0),
                "score_delta": _safe_round(score_delta, 4, 0.0),
                "reason": reason,
            }
            self._log(
                "[REALM-XVI-CONTEXT-INFLUENCE]",
                ticker=ticker,
                family=family,
                confidence_adjustment=_safe_round(confidence_adjustment, 4, 0.0),
                score_delta=_safe_round(score_delta, 4, 0.0),
                reason=reason,
            )
            if confidence_adjustment > 0:
                self._log("[REALM-XVI-SHADOW-BOOST]", ticker=ticker, family=family, confidence_adjustment=_safe_round(confidence_adjustment, 4, 0.0), score_delta=_safe_round(score_delta, 4, 0.0))
            elif confidence_adjustment < 0:
                self._log("[REALM-XVI-SHADOW-PENALTY]", ticker=ticker, family=family, confidence_adjustment=_safe_round(confidence_adjustment, 4, 0.0), score_delta=_safe_round(score_delta, 4, 0.0))
        if largest_divergence:
            self._log("[REALM-XVI-DIVERGENCE]", family=largest_divergence, reason="peer_disagreement_or_isolated_move", regime_label=cycle_regime.get("regime_label"), risk_level=cycle_regime.get("regime_risk_level"))
            self._log("[REALM-XVI-CONTEXT-WARNING]", family=largest_divergence, warning="divergence_detected", safety="soft_penalty_only")
        if cycle_regime.get("regime_label") == "high_volatility":
            self._log("[REALM-XVI-HIGH-VOL]", overall_volatility=cycle_regime.get("overall_volatility"), regime_confidence=cycle_regime.get("regime_confidence"))
        elif cycle_regime.get("regime_label") == "low_liquidity":
            self._log("[REALM-XVI-LOW-LIQ]", overall_volatility=cycle_regime.get("overall_volatility"), regime_confidence=cycle_regime.get("regime_confidence"))
        elif cycle_regime.get("regime_label") == "choppy":
            self._log("[REALM-XVI-CHOPPY]", overall_volatility=cycle_regime.get("overall_volatility"), regime_confidence=cycle_regime.get("regime_confidence"))
        elif cycle_regime.get("regime_label") == "trending":
            self._log("[REALM-XVI-TRENDING]", overall_volatility=cycle_regime.get("overall_volatility"), regime_confidence=cycle_regime.get("regime_confidence"))
        if realm_xv_snapshots:
            self.ingest_realm_xv_snapshots(realm_xv_snapshots)
        self.last_cycle_context = {
            "family_profiles": family_profiles,
            "cycle_regime": cycle_regime,
            "context_influences": context_influences,
            "strongest_alignment": strongest_alignment,
            "largest_divergence": largest_divergence,
            "source": source,
        }
        self.last_cycle_summary = {
            **cycle_regime,
            "family_count": len(family_profiles),
            "markets_count": len(markets),
            "quote_count": len(quotes),
            "strongest_alignment": strongest_alignment,
            "largest_divergence": largest_divergence,
            "high_risk_families": cycle_regime.get("high_risk_families", []),
            "summary_lines": [
                {
                    "family": family,
                    "sentiment": profile.get("sentiment", "neutral"),
                    "avg_confidence": profile.get("avg_confidence", 0.0),
                    "avg_spread": profile.get("avg_spread", 0.0),
                    "avg_liquidity": profile.get("avg_liquidity", 0.0),
                    "regime_label": profile.get("regime_label", "unknown"),
                    "volatility_state": profile.get("volatility_state", "unknown"),
                }
                for family, profile in sorted(family_profiles.items(), key=lambda item: (item[1].get("sample_count", 0), item[0]), reverse=True)
            ],
        }
        self._log(
            "[REALM-XVI-SUMMARY]",
            crypto_sentiment=self._pick_category_sentiment(family_profiles, "crypto"),
            nba_sentiment=self._pick_category_sentiment(family_profiles, "sports"),
            overall_volatility=cycle_regime.get("overall_volatility"),
            dominant_regime=cycle_regime.get("dominant_regime"),
            high_risk_families=";".join(cycle_regime.get("high_risk_families", [])[:5]) or "none",
            strongest_alignment=strongest_alignment or "none",
            largest_divergence=largest_divergence or "none",
        )
        if strongest_alignment:
            self._log("[REALM-XVI-CORRELATION]", pair=strongest_alignment.replace("|", "↔"), mode="top_alignment", confidence=cycle_regime.get("regime_confidence"))
        self._save_state()
        return self.last_cycle_summary

    def _pick_category_sentiment(self, family_profiles, category):
        families = [profile for family, profile in family_profiles.items() if self._family_matches_category(family, category)]
        if not families:
            return "unknown"
        score = sum(1 if profile.get("sentiment") == "bullish" else -1 if profile.get("sentiment") == "bearish" else 0 for profile in families)
        if score > 0:
            return "bullish"
        if score < 0:
            return "bearish"
        return "neutral"

    def ingest_realm_xv_snapshots(self, snapshots):
        snapshots = [snapshot for snapshot in (snapshots or []) if isinstance(snapshot, dict)]
        if not snapshots:
            return {"ok": False, "reason": "no_snapshots"}
        grouped = defaultdict(list)
        for snapshot in snapshots:
            bucket = int((_safe_float(snapshot.get("timestamp_ts"), 0.0) or 0.0) // 60)
            family = str(snapshot.get("market_family") or snapshot.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"
            grouped[bucket].append((family, snapshot))
        for _, items in grouped.items():
            family_metrics = defaultdict(list)
            for family, snapshot in items:
                proxy = type("QuoteProxy", (), {})()
                for key in ("yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "volume", "liquidity"):
                    setattr(proxy, key, snapshot.get(key))
                market = {
                    "ticker": snapshot.get("ticker"),
                    "category": snapshot.get("category"),
                    "market_category": snapshot.get("category"),
                }
                family_metrics[family].append(self._market_metrics(market=market, quote=proxy))
            family_profiles = {family: self._aggregate_family_profile(family, metrics_list) for family, metrics_list in family_metrics.items()}
            family_names = list(family_profiles.keys())
            for i, family_a in enumerate(family_names):
                for family_b in family_names[i + 1:]:
                    sentiment_a = 1 if family_profiles[family_a].get("sentiment") == "bullish" else -1 if family_profiles[family_a].get("sentiment") == "bearish" else 0
                    sentiment_b = 1 if family_profiles[family_b].get("sentiment") == "bullish" else -1 if family_profiles[family_b].get("sentiment") == "bearish" else 0
                    self._update_relationship(family_a, family_b, sentiment_a, sentiment_b, family_profiles[family_a].get("volatility_state"), family_profiles[family_b].get("volatility_state"))
        self.state["replay_context"] = {
            "snapshots": len(snapshots),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._save_state()
        self._log("[REALM-XVI-REPLAY-CONTEXT]", snapshots=len(snapshots), families=len(self.state.get("families", {})), relationships=len(self.state.get("relationships", {})))
        return {"ok": True, "snapshots": len(snapshots)}

    def _influence_for_market(self, family_profile, cycle_regime, relation_bias=0.0, divergence_penalty=0.0):
        sentiment = str(family_profile.get("sentiment") or "neutral")
        regime_label = str(cycle_regime.get("regime_label") or "neutral")
        avg_confidence = _safe_float(family_profile.get("avg_confidence"), 0.0) or 0.0
        avg_spread = _safe_float(family_profile.get("avg_spread"), 0.0) or 0.0
        avg_liquidity = _safe_float(family_profile.get("avg_liquidity"), 0.0) or 0.0
        raw = 0.0
        if sentiment == "bullish":
            raw += 0.015
        elif sentiment == "bearish":
            raw -= 0.015
        if regime_label == "trending":
            raw += 0.010
        elif regime_label in {"low_liquidity", "unstable_spread_conditions", "high_volatility", "choppy", "thin_overnight_markets"}:
            raw -= 0.010
        raw += relation_bias * 0.010
        raw -= divergence_penalty * 0.020
        raw += max(0.0, avg_confidence - 0.55) * 0.010
        raw -= max(0.0, avg_spread - 0.04) * 0.05
        raw += min(0.01, avg_liquidity / 1000.0)
        confidence_adjustment = _clamp(raw, -REALM_XVI_MAX_SCORE_PENALTY, REALM_XVI_MAX_SCORE_BOOST)
        return confidence_adjustment, confidence_adjustment * 20.0

    def get_score_adjustment(self, ticker=None, market=None, quote=None, base_score=None):
        if not self.enabled:
            return {
                "family": _family_from_market(market, ticker),
                "confidence_adjustment": 0.0,
                "score_delta": 0.0,
                "reason": "disabled",
                "regime_label": "disabled",
                "regime_confidence": 0.0,
                "regime_risk_level": "low",
                "family_sentiment": "neutral",
                "volatility_state": "low",
                "would_influence": False,
                "shadow_mode": True,
            }
        family = _family_from_market(market, ticker or getattr(quote, "ticker", None))
        metrics = self._market_metrics(market=market, quote=quote)
        family_profile = dict(self.state.get("families", {}).get(family, {}) or {})
        regime = dict(self.state.get("last_summary", {}) or {})
        relation_bias = 0.0
        for record in self.state.get("relationships", {}).values():
            families = {str(record.get("family_a") or "").upper(), str(record.get("family_b") or "").upper()}
            if family in families:
                relation_bias += (_safe_float(record.get("positive_strength"), 0.0) or 0.0) - (_safe_float(record.get("inverse_strength"), 0.0) or 0.0)
        divergence_penalty = 0.0
        if str(regime.get("regime_label") or "") in {"low_liquidity", "unstable_spread_conditions", "high_volatility", "choppy", "thin_overnight_markets"}:
            divergence_penalty += 0.5
        if str(regime.get("dominant_regime") or "") == "trending":
            relation_bias += 0.25
        confidence_adjustment, score_delta = self._influence_for_market(family_profile or metrics, regime, relation_bias=relation_bias, divergence_penalty=divergence_penalty)
        safety_blocked = bool((metrics.get("spread") is not None and metrics.get("spread") >= 0.08) or (metrics.get("liquidity") or 0.0) <= 0.0)
        if safety_blocked and (confidence_adjustment or score_delta):
            self._log("[REALM-XVI-GUARDIAN-OVERRIDE]", ticker=ticker or metrics.get("ticker") or "UNKNOWN", family=family, reason="hard_safety_prevents_cross_market_override", safety="wins")
            confidence_adjustment = 0.0
            score_delta = 0.0
        return {
            "family": family,
            "confidence_adjustment": _safe_round(confidence_adjustment, 4, 0.0),
            "score_delta": _safe_round(score_delta, 4, 0.0),
            "reason": self._context_reason(family_profile or metrics, regime, relation_bias, divergence_penalty),
            "regime_label": regime.get("regime_label", "unknown"),
            "regime_confidence": regime.get("regime_confidence", 0.0),
            "regime_risk_level": regime.get("regime_risk_level", "low"),
            "family_sentiment": family_profile.get("sentiment", metrics.get("sentiment", "neutral")),
            "volatility_state": family_profile.get("volatility_state", metrics.get("volatility_state", "low")),
            "would_influence": bool(confidence_adjustment or score_delta),
            "shadow_mode": True,
        }

    def _context_reason(self, profile, cycle_regime, relation_bias, divergence_penalty):
        reasons = []
        if profile.get("sentiment") == "bullish":
            reasons.append("family_bullish")
        elif profile.get("sentiment") == "bearish":
            reasons.append("family_bearish")
        if cycle_regime.get("regime_label") == "trending":
            reasons.append("trend_support")
        elif cycle_regime.get("regime_label") in {"low_liquidity", "unstable_spread_conditions", "choppy", "high_volatility", "thin_overnight_markets"}:
            reasons.append("risk_environment")
        if relation_bias > 0:
            reasons.append("aligned_pairing")
        if divergence_penalty > 0:
            reasons.append("divergence_penalty")
        return ",".join(reasons) if reasons else "neutral"

    def report_summary(self):
        summary = dict(self.state.get("last_summary", {}) or {})
        families = self.state.get("families", {}) or {}
        relationships = self.state.get("relationships", {}) or {}
        report = {
            "overall_climate": summary.get("dominant_regime", "unknown"),
            "overall_volatility": summary.get("overall_volatility", "unknown"),
            "strongest_alignment": self._strongest_alignment(),
            "largest_divergence": self._largest_divergence(families),
            "high_risk_families": summary.get("high_risk_families", []),
            "highest_confidence_families": sorted(
                (
                    {
                        "family": family,
                        "avg_confidence": profile.get("avg_confidence", 0.0),
                        "sentiment": profile.get("sentiment", "neutral"),
                    }
                    for family, profile in families.items()
                ),
                key=lambda item: item["avg_confidence"],
                reverse=True,
            )[:5],
            "relationship_count": len(relationships),
            "family_count": len(families),
        }
        print("\nREALM XVI — CROSS-MARKET INTELLIGENCE REPORT")
        print(f"Overall Market Climate: {report['overall_climate']}")
        print(f"Overall Volatility: {report['overall_volatility']}")
        print(f"Strongest Correlations: {report['strongest_alignment'] or 'none'}")
        print(f"Weakest Conditions: {', '.join(report['high_risk_families']) if report['high_risk_families'] else 'none'}")
        print("Highest Confidence Families:")
        for item in report["highest_confidence_families"]:
            print(f"- {item['family']} ({item['sentiment']}, avg_confidence={item['avg_confidence']})")
        print(f"Context Warnings: {report['largest_divergence'] or 'none'}")
        print("Recommended Interpretation: favor aligned momentum, reduce aggression during divergence")
        return report


def _family_from_snapshot(snapshot):
    return str(snapshot.get("market_family") or snapshot.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--state-file", default=REALM_XVI_DEFAULT_STATE_FILE)
    args = parser.parse_args()
    engine = CrossMarketIntelligence(state_file=args.state_file)
    if args.report:
        engine.report_summary()
        return
    print("Use --report to print the Realm XVI cross-market report.")


if __name__ == "__main__":
    main()

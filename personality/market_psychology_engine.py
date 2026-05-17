from __future__ import annotations

from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Tuple


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def _sign(value: float, threshold: float = 1e-9) -> int:
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


class MarketPsychologyEngine:
    def __init__(
        self,
        enabled: bool = True,
        shadow_only: bool = False,
        hard_block: bool = True,
        min_score: float = 0.35,
        score_weight: float = 0.10,
        summary_interval_seconds: float = 180.0,
    ):
        self.enabled = bool(enabled)
        self.shadow_only = bool(shadow_only)
        self.hard_block = bool(hard_block)
        self.min_score = _clamp(min_score)
        self.score_weight = max(0.0, float(score_weight or 0.0))
        self.summary_interval_seconds = max(30.0, float(summary_interval_seconds or 180.0))
        self.last_summary_ts = 0.0
        self.last_summary: Dict[str, Any] = {}

    def _extract_samples(self, history: Any = None, market: Dict[str, Any] | None = None) -> Tuple[List[Dict[str, float]], str, str, bool]:
        market = market if isinstance(market, dict) else {}
        samples: List[Dict[str, float]] = []
        history_source = "unavailable"
        history_reason = "no_history"
        has_ohlc = False

        def append_sample(close_value=None, open_value=None, high_value=None, low_value=None):
            nonlocal has_ohlc
            close_float = _safe_float(close_value, 0.0)
            if close_float <= 0.0:
                return
            sample = {"close": close_float}
            if open_value is not None:
                open_float = _safe_float(open_value, 0.0)
                if open_float > 0.0:
                    sample["open"] = open_float
            if high_value is not None:
                high_float = _safe_float(high_value, 0.0)
                if high_float > 0.0:
                    sample["high"] = high_float
                    has_ohlc = True
            if low_value is not None:
                low_float = _safe_float(low_value, 0.0)
                if low_float > 0.0:
                    sample["low"] = low_float
                    has_ohlc = True
            samples.append(sample)

        def parse_list(items: Iterable[Any], source_label: str):
            nonlocal history_source, history_reason
            parsed = 0
            for item in items:
                if isinstance(item, (int, float, str)):
                    close_value = _safe_float(item, 0.0)
                    if close_value > 0.0:
                        append_sample(close_value)
                        parsed += 1
                    continue
                if isinstance(item, dict):
                    close_value = item.get("close")
                    if close_value is None:
                        close_value = (
                            item.get("close_price")
                            or item.get("last_price")
                            or item.get("last_price_dollars")
                            or item.get("price")
                            or item.get("price_dollars")
                            or item.get("value")
                        )
                    open_value = item.get("open") or item.get("open_price") or item.get("open_price_dollars")
                    high_value = item.get("high") or item.get("high_price") or item.get("high_price_dollars")
                    low_value = item.get("low") or item.get("low_price") or item.get("low_price_dollars")
                    if close_value is None and isinstance(item.get("c"), (int, float, str)):
                        close_value = item.get("c")
                    if close_value is None and isinstance(item.get("last"), (int, float, str)):
                        close_value = item.get("last")
                    if close_value is not None:
                        append_sample(close_value, open_value=open_value, high_value=high_value, low_value=low_value)
                        parsed += 1
                    continue
                if isinstance(item, (list, tuple)) and item:
                    numeric_values = [_safe_float(value, 0.0) for value in item if _safe_float(value, 0.0) > 0.0]
                    if not numeric_values:
                        continue
                    if len(item) >= 4:
                        tail = item[-4:]
                        tail_numeric = [_safe_float(value, 0.0) for value in tail]
                        if all(value > 0.0 for value in tail_numeric):
                            append_sample(tail_numeric[-1], open_value=tail_numeric[0], high_value=tail_numeric[1], low_value=tail_numeric[2])
                            parsed += 1
                            continue
                    append_sample(numeric_values[-1])
                    parsed += 1
            if parsed > 0:
                history_source = source_label
                history_reason = source_label
            return parsed

        if isinstance(history, list):
            parse_list(history, "history_argument")
        elif isinstance(history, tuple):
            parse_list(list(history), "history_argument")
        elif isinstance(history, dict):
            for key in (
                "history",
                "series",
                "price_history",
                "last_price_history",
                "recent_prices",
                "prices",
                "closes",
                "close_prices",
                "candles",
                "ohlc",
                "ohlc_history",
                "_realm_xxx_history",
            ):
                value = history.get(key)
                if isinstance(value, list) and parse_list(value, f"history_argument:{key}") > 0:
                    break
            if not samples:
                raw_close = (
                    history.get("last_price")
                    or history.get("close")
                    or history.get("price")
                    or history.get("yes_ask")
                    or history.get("yes_bid")
                )
                if raw_close is not None:
                    append_sample(raw_close)
                    history_source = "history_argument:scalar"
                    history_reason = "scalar_price"
        if not samples and isinstance(market, dict):
            for key in (
                "candles",
                "ohlc",
                "ohlc_history",
                "history",
                "price_history",
                "last_price_history",
                "recent_prices",
                "prices",
                "closes",
                "close_prices",
                "series",
            ):
                value = market.get(key)
                if isinstance(value, list) and parse_list(value, f"market:{key}") > 0:
                    break
            if not samples:
                raw_close = (
                    market.get("last_price")
                    or market.get("close")
                    or market.get("price")
                    or market.get("yes_ask")
                    or market.get("yes_bid")
                )
                if raw_close is not None:
                    append_sample(raw_close)
                    history_source = "market:scalar"
                    history_reason = "scalar_price"
        if not samples:
            history_source = "unavailable"
            history_reason = "no_history"
        return samples, history_source, history_reason, has_ohlc

    def analyze(
        self,
        market: Dict[str, Any] | None = None,
        history: Any = None,
        family: str = "UNKNOWN",
        side: str | None = None,
        source: str = "unknown",
    ) -> Dict[str, Any]:
        market = market if isinstance(market, dict) else {}
        ticker = str(market.get("ticker") or "").strip() or "unknown"
        family = str(family or market.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"
        side = str(side or market.get("side") or "").strip().lower() or None
        samples, history_source, history_reason, has_ohlc = self._extract_samples(history=history, market=market)
        history_points = len(samples)
        reasons: List[str] = []
        if history_points < 2:
            reasons.append("insufficient_history")
            return {
                "enabled": self.enabled,
                "ticker": ticker,
                "family": family,
                "side": side,
                "source": source,
                "history_source": history_source,
                "history_reason": history_reason,
                "history_points": history_points,
                "has_ohlc": has_ohlc,
                "psychology_score": 0.50,
                "psychology_regime": "unknown",
                "trend_strength_score": 0.50,
                "chop_risk_score": 0.50,
                "volatility_score": 0.50,
                "fake_breakout_risk": 0.50,
                "exhaustion_risk": 0.50,
                "directional_persistence_score": 0.50,
                "psychology_reasons": reasons,
                "net_return": 0.0,
                "avg_abs_return": 0.0,
                "realized_volatility": 0.0,
                "atr_ratio": None,
                "movement_efficiency": 0.0,
                "alternation_rate": 0.0,
                "wick_ratio": 0.0,
                "blocked": False,
                "block_reason": None,
            }

        closes = [sample["close"] for sample in samples if sample.get("close", 0.0) > 0.0]
        if len(closes) < 2:
            reasons.append("insufficient_clean_history")
            return {
                "enabled": self.enabled,
                "ticker": ticker,
                "family": family,
                "side": side,
                "source": source,
                "history_source": history_source,
                "history_reason": history_reason,
                "history_points": history_points,
                "has_ohlc": has_ohlc,
                "psychology_score": 0.50,
                "psychology_regime": "unknown",
                "trend_strength_score": 0.50,
                "chop_risk_score": 0.50,
                "volatility_score": 0.50,
                "fake_breakout_risk": 0.50,
                "exhaustion_risk": 0.50,
                "directional_persistence_score": 0.50,
                "psychology_reasons": reasons,
                "net_return": 0.0,
                "avg_abs_return": 0.0,
                "realized_volatility": 0.0,
                "atr_ratio": None,
                "movement_efficiency": 0.0,
                "alternation_rate": 0.0,
                "wick_ratio": 0.0,
                "blocked": False,
                "block_reason": None,
            }

        returns: List[float] = []
        for idx in range(1, len(closes)):
            previous = closes[idx - 1]
            current = closes[idx]
            if previous <= 0.0:
                continue
            returns.append((current - previous) / abs(previous))
        if not returns:
            reasons.append("insufficient_return_history")
            return {
                "enabled": self.enabled,
                "ticker": ticker,
                "family": family,
                "side": side,
                "source": source,
                "history_source": history_source,
                "history_reason": history_reason,
                "history_points": history_points,
                "has_ohlc": has_ohlc,
                "psychology_score": 0.50,
                "psychology_regime": "unknown",
                "trend_strength_score": 0.50,
                "chop_risk_score": 0.50,
                "volatility_score": 0.50,
                "fake_breakout_risk": 0.50,
                "exhaustion_risk": 0.50,
                "directional_persistence_score": 0.50,
                "psychology_reasons": reasons,
                "net_return": 0.0,
                "avg_abs_return": 0.0,
                "realized_volatility": 0.0,
                "atr_ratio": None,
                "movement_efficiency": 0.0,
                "alternation_rate": 0.0,
                "wick_ratio": 0.0,
                "blocked": False,
                "block_reason": None,
            }

        abs_returns = [abs(value) for value in returns]
        avg_abs_return = mean(abs_returns) if abs_returns else 0.0
        realized_volatility = pstdev(returns) if len(returns) >= 2 else 0.0
        net_return = (closes[-1] - closes[0]) / abs(closes[0]) if closes[0] > 0.0 else 0.0
        movement_total = sum(abs_returns)
        movement_efficiency = abs(net_return) / max(movement_total, 1e-9)
        movement_efficiency = _clamp(movement_efficiency)
        signs = [_sign(value) for value in returns]
        non_zero_signs = [value for value in signs if value != 0]
        alternation_count = 0
        for idx in range(1, len(non_zero_signs)):
            if non_zero_signs[idx] != non_zero_signs[idx - 1]:
                alternation_count += 1
        alternation_rate = alternation_count / max(len(non_zero_signs) - 1, 1) if non_zero_signs else 0.0
        if non_zero_signs:
            positive_count = sum(1 for value in non_zero_signs if value > 0)
            negative_count = sum(1 for value in non_zero_signs if value < 0)
            majority_sign = max(positive_count, negative_count)
            sign_balance = majority_sign / float(len(non_zero_signs))
        else:
            sign_balance = 0.5
        directional_persistence = _clamp(0.5 * sign_balance + 0.5 * (1.0 - alternation_rate))

        true_ranges: List[float] = []
        wick_ratios: List[float] = []
        plus_dm_total = 0.0
        minus_dm_total = 0.0
        prev_high = None
        prev_low = None
        prev_close = None
        ohlc_count = 0
        for sample in samples:
            close_value = sample.get("close")
            high_value = sample.get("high")
            low_value = sample.get("low")
            open_value = sample.get("open")
            if high_value is not None and low_value is not None and prev_close is not None:
                tr_candidates = [
                    abs(high_value - low_value),
                    abs(high_value - prev_close),
                    abs(low_value - prev_close),
                ]
                true_ranges.append(max(tr_candidates))
                ohlc_count += 1
            if (
                prev_high is not None
                and prev_low is not None
                and high_value is not None
                and low_value is not None
            ):
                up_move = high_value - prev_high
                down_move = prev_low - low_value
                if up_move > down_move and up_move > 0.0:
                    plus_dm_total += up_move
                elif down_move > up_move and down_move > 0.0:
                    minus_dm_total += down_move
            if open_value is not None and high_value is not None and low_value is not None:
                body_high = max(open_value, close_value)
                body_low = min(open_value, close_value)
                upper_wick = max(0.0, high_value - body_high)
                lower_wick = max(0.0, body_low - low_value)
                candle_range = max(high_value - low_value, 1e-9)
                wick_ratios.append((upper_wick + lower_wick) / candle_range)
            prev_high = high_value if high_value is not None else prev_high
            prev_low = low_value if low_value is not None else prev_low
            prev_close = close_value if close_value is not None else prev_close

        atr_ratio = None
        if true_ranges and closes[-1] > 0.0:
            atr_ratio = mean(true_ranges) / max(closes[-1], 1e-9)
        wick_ratio = mean(wick_ratios) if wick_ratios else 0.0
        dm_total = plus_dm_total + minus_dm_total
        dm_strength = abs(plus_dm_total - minus_dm_total) / max(dm_total, 1e-9) if dm_total > 0.0 else 0.0
        net_movement_component = min(1.0, abs(net_return) * 20.0)
        trend_strength = _clamp(
            0.30 * dm_strength
            + 0.29 * directional_persistence
            + 0.20 * movement_efficiency
            + 0.18 * net_movement_component
        )

        if atr_ratio is not None:
            volatility_raw = (atr_ratio * 12.0) + (realized_volatility * 18.0) + (avg_abs_return * 12.0)
        else:
            volatility_raw = (realized_volatility * 18.0) + (avg_abs_return * 12.0) + (abs(net_return) * 4.0)
        volatility_score = _clamp(volatility_raw)

        chop_risk = _clamp(
            0.45 * alternation_rate
            + 0.35 * (1.0 - movement_efficiency)
            + 0.20 * (1.0 - directional_persistence)
        )
        if abs(net_return) < 0.004 and avg_abs_return < 0.003:
            chop_risk = _clamp(chop_risk + 0.10)

        recent_window = returns[-3:]
        earlier_window = returns[:-3] if len(returns) > 3 else []
        recent_abs = mean([abs(value) for value in recent_window]) if recent_window else 0.0
        earlier_abs = mean([abs(value) for value in earlier_window]) if earlier_window else recent_abs
        deceleration = 0.0
        if earlier_abs > 0.0:
            deceleration = _clamp((earlier_abs - recent_abs) / max(earlier_abs, 1e-9))
        recent_signs = [_sign(value) for value in recent_window if _sign(value) != 0]
        earlier_signs = [_sign(value) for value in earlier_window if _sign(value) != 0]
        reversal_after_run = 0.0
        if recent_signs and earlier_signs:
            recent_majority = 1 if recent_signs.count(1) >= recent_signs.count(-1) else -1
            earlier_majority = 1 if earlier_signs.count(1) >= earlier_signs.count(-1) else -1
            if recent_majority != earlier_majority:
                reversal_after_run = 1.0
        exhaustion_risk = _clamp(
            0.45 * deceleration
            + 0.35 * reversal_after_run
            + 0.20 * (1.0 - directional_persistence)
        )
        if has_ohlc and wick_ratio > 0.0:
            exhaustion_risk = _clamp(exhaustion_risk + min(0.15, wick_ratio * 0.10))

        fake_breakout_risk = _clamp(
            0.35 * volatility_score
            + 0.35 * (1.0 - trend_strength)
            + 0.15 * (1.0 - directional_persistence)
            + 0.15 * wick_ratio
        )

        if volatility_score <= 0.15 and abs(net_return) <= 0.01 and avg_abs_return <= 0.003:
            dead_market = True
            reasons.append("dead_market_low_movement")
        else:
            dead_market = False
        if alternation_rate >= 0.60 and trend_strength <= 0.45:
            reasons.append("alternating_direction_chop")
        if trend_strength >= 0.65:
            reasons.append("trend_strength_supportive")
        if has_ohlc:
            reasons.append("ohlc_proxy_available")
        else:
            reasons.append("close_only_proxy")
        if atr_ratio is not None:
            reasons.append("atr_proxy_available")
        if reversal_after_run > 0.0:
            reasons.append("recent_reversal_detected")
        if deceleration >= 0.35:
            reasons.append("momentum_deceleration")

        if dead_market:
            psychology_regime = "dead"
        elif volatility_score >= 0.60 and fake_breakout_risk >= 0.62 and exhaustion_risk >= 0.55 and trend_strength <= 0.55:
            psychology_regime = "volatile_reversal"
        elif chop_risk >= 0.72 and trend_strength <= 0.35:
            psychology_regime = "chop"
        elif trend_strength >= 0.78 and directional_persistence >= 0.70 and chop_risk <= 0.40:
            psychology_regime = "strong_trend"
        elif trend_strength >= 0.60 and directional_persistence >= 0.55:
            psychology_regime = "trend"
        elif trend_strength >= 0.42:
            psychology_regime = "weak_trend"
        elif chop_risk >= 0.50:
            psychology_regime = "chop"
        else:
            psychology_regime = "unknown"

        psychology_score = 0.50
        psychology_score += (trend_strength - 0.50) * 0.30
        psychology_score += (directional_persistence - 0.50) * 0.20
        psychology_score += (volatility_score - 0.50) * 0.08
        psychology_score -= chop_risk * 0.20
        psychology_score -= fake_breakout_risk * 0.14
        psychology_score -= exhaustion_risk * 0.12
        if psychology_regime in {"trend", "strong_trend"}:
            psychology_score += 0.04
        elif psychology_regime in {"chop", "dead", "volatile_reversal"}:
            psychology_score -= 0.06
        psychology_score = _clamp(psychology_score)

        if psychology_regime == "unknown" and history_points >= 3:
            reasons.append("ambiguous_regime")

        return {
            "enabled": self.enabled,
            "ticker": ticker,
            "family": family,
            "side": side,
            "source": source,
            "history_source": history_source,
            "history_reason": history_reason,
            "history_points": history_points,
            "has_ohlc": has_ohlc,
            "psychology_score": round(psychology_score, 4),
            "psychology_regime": psychology_regime,
            "trend_strength_score": round(trend_strength, 4),
            "chop_risk_score": round(chop_risk, 4),
            "volatility_score": round(volatility_score, 4),
            "fake_breakout_risk": round(fake_breakout_risk, 4),
            "exhaustion_risk": round(exhaustion_risk, 4),
            "directional_persistence_score": round(directional_persistence, 4),
            "psychology_reasons": list(dict.fromkeys(reasons)),
            "net_return": round(net_return, 6),
            "avg_abs_return": round(avg_abs_return, 6),
            "realized_volatility": round(realized_volatility, 6),
            "atr_ratio": round(atr_ratio, 6) if atr_ratio is not None else None,
            "movement_efficiency": round(movement_efficiency, 4),
            "alternation_rate": round(alternation_rate, 4),
            "wick_ratio": round(wick_ratio, 4),
            "blocked": False,
            "block_reason": None,
        }

    def score_adjustment(self, psychology: Dict[str, Any] | None = None, original_score: float | None = None) -> Dict[str, Any]:
        psychology = psychology if isinstance(psychology, dict) else {}
        psychology_score = _clamp(_safe_float(psychology.get("psychology_score"), 0.5))
        regime = str(psychology.get("psychology_regime") or "unknown").strip().lower()
        trend_strength = _clamp(_safe_float(psychology.get("trend_strength_score"), 0.5))
        chop_risk = _clamp(_safe_float(psychology.get("chop_risk_score"), 0.5))
        volatility_score = _clamp(_safe_float(psychology.get("volatility_score"), 0.5))
        fake_breakout_risk = _clamp(_safe_float(psychology.get("fake_breakout_risk"), 0.5))
        exhaustion_risk = _clamp(_safe_float(psychology.get("exhaustion_risk"), 0.5))
        persistence = _clamp(_safe_float(psychology.get("directional_persistence_score"), 0.5))
        reasons = list(psychology.get("psychology_reasons") or [])

        if not self.enabled:
            reasons.append("engine_disabled")
            return {
                "enabled": False,
                "adjustment": 0.0,
                "adjusted_score": None,
                "blocked": False,
                "block_reason": None,
                "shadow_only": self.shadow_only,
                "hard_block_enabled": self.hard_block,
                "reasons": reasons,
            }

        delta = (psychology_score - 0.5) * 50.0 * self.score_weight
        if regime in {"trend", "strong_trend"} and persistence >= 0.60:
            delta += min(1.5, (trend_strength - 0.5) * 3.0)
            reasons.append("trend_bonus")
        elif regime in {"chop", "dead", "volatile_reversal"}:
            delta -= min(1.5, (chop_risk - 0.5) * 2.5 if chop_risk > 0.5 else 0.5)
            reasons.append("chop_penalty")
        if fake_breakout_risk >= 0.70:
            delta -= 0.75
            reasons.append("fake_breakout_penalty")
        if exhaustion_risk >= 0.70:
            delta -= 0.75
            reasons.append("exhaustion_penalty")
        if volatility_score >= 0.65 and trend_strength >= 0.65:
            delta += 0.35
            reasons.append("quality_volatility_bonus")

        block_reason = None
        blocked = False
        if self.hard_block and not self.shadow_only:
            if regime == "dead" and volatility_score <= self.min_score and trend_strength <= self.min_score:
                blocked = True
                block_reason = "dead_market_low_volatility"
            elif chop_risk >= 0.85 and trend_strength <= 0.20:
                blocked = True
                block_reason = "extreme_chop_low_trend_strength"
            elif psychology_score < self.min_score and regime in {"dead", "chop", "volatile_reversal"}:
                blocked = True
                block_reason = "psychology_score_below_minimum"
        if self.shadow_only:
            blocked = False

        adjusted_score = None if original_score is None else round(_safe_float(original_score, 0.0) + delta, 4)
        return {
            "enabled": True,
            "adjustment": round(delta, 4),
            "adjusted_score": adjusted_score,
            "blocked": blocked,
            "block_reason": block_reason,
            "shadow_only": self.shadow_only,
            "hard_block_enabled": self.hard_block,
            "reasons": list(dict.fromkeys(reasons)),
        }

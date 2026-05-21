from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _family_tier(avg_ev: float, avg_conf: float, sample_count: int, execution_rate: float, spread_quality: float, liquidity_quality: float, drawdown_risk: float) -> str:
    if sample_count < 10:
        return "C"
    if execution_rate <= 0.0 or drawdown_risk >= 0.75:
        return "D"
    if avg_ev > 0.15 and avg_conf >= 0.78 and spread_quality >= 0.65 and liquidity_quality >= 0.65 and sample_count >= 30:
        return "S"
    if avg_ev > 0.06 and avg_conf >= 0.68 and spread_quality >= 0.55 and liquidity_quality >= 0.50 and sample_count >= 20:
        return "A"
    if avg_ev > 0.0 and avg_conf >= 0.60 and sample_count >= 15:
        return "B"
    if avg_ev <= 0.0 or avg_conf < 0.60:
        return "C"
    return "D"


def compute_family_heatmap(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    family_stats: Dict[str, Dict[str, Any]] = {}
    for record in records:
        family = str(record.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"
        tier = str(record.get("profitability_tier") or record.get("tier") or "C").strip().upper() or "C"
        stats = family_stats.setdefault(
            family,
            {
                "family": family,
                "count": 0,
                "executed": 0,
                "phase55_success": 0,
                "hard_block_count": 0,
                "strict_reject_count": 0,
                "expected_profit_sum": 0.0,
                "expected_loss_sum": 0.0,
                "expected_value_sum": 0.0,
                "confidence_sum": 0.0,
                "liquidity_sum": 0.0,
                "spread_sum": 0.0,
                "volatility_sum": 0.0,
                "hold_time_sum": 0.0,
                "slippage_sum": 0.0,
                "risk_reward_sum": 0.0,
                "rejection_reasons": defaultdict(int),
                "regimes": defaultdict(int),
                "tier_counts": defaultdict(int),
            },
        )
        stats["count"] += 1
        stats["executed"] += int(str(record.get("relaxed_live_result") or "").upper() == "EXECUTE" or str(record.get("strict_live_result") or "").upper() == "ALLOW")
        stats["phase55_success"] += int(bool(record.get("phase55_success")))
        stats["hard_block_count"] += int(bool(record.get("hard_block")) or tier == "HARD_BLOCK")
        stats["strict_reject_count"] += int(bool(record.get("strict_live_would_reject")))
        stats["expected_profit_sum"] += safe_float(record.get("expected_profit"))
        stats["expected_loss_sum"] += safe_float(record.get("expected_loss"))
        stats["expected_value_sum"] += safe_float(record.get("expected_value"))
        stats["confidence_sum"] += safe_float(record.get("confidence_score"))
        stats["liquidity_sum"] += safe_float(record.get("liquidity_score"))
        stats["spread_sum"] += safe_float(record.get("spread_cost"))
        stats["volatility_sum"] += safe_float(record.get("volatility_score"))
        stats["hold_time_sum"] += safe_float(record.get("simulated_hold_time"))
        stats["slippage_sum"] += safe_float(record.get("simulated_slippage"))
        stats["risk_reward_sum"] += safe_float((record.get("metrics") or {}).get("simulated_risk_reward"))
        stats["tier_counts"][tier] += 1
        regime = str(record.get("regime_classification") or record.get("profitability_signal") or "unknown").strip().lower() or "unknown"
        stats["regimes"][regime] += 1
        for reason in record.get("loosened_rules_used") or []:
            stats["rejection_reasons"][str(reason)] += 1
    family_rows: List[Dict[str, Any]] = []
    for family, stats in family_stats.items():
        count = max(1, int(stats["count"] or 0))
        avg_ev = stats["expected_value_sum"] / count
        avg_profit = stats["expected_profit_sum"] / count
        avg_loss = stats["expected_loss_sum"] / count
        avg_conf = stats["confidence_sum"] / count
        avg_liquidity = stats["liquidity_sum"] / count
        avg_spread = stats["spread_sum"] / count
        avg_vol = stats["volatility_sum"] / count
        avg_hold = stats["hold_time_sum"] / count
        avg_slippage = stats["slippage_sum"] / count
        execution_rate = stats["executed"] / count
        spread_quality = max(0.0, 1.0 - min(1.0, avg_spread))
        liquidity_quality = max(0.0, min(1.0, avg_liquidity))
        stability = max(0.0, 1.0 - min(1.0, abs(avg_conf - 0.7)))
        drawdown_risk = max(0.0, min(1.0, avg_loss / (abs(avg_profit) + 1.0)))
        tier = _family_tier(avg_ev, avg_conf, count, execution_rate, spread_quality, liquidity_quality, drawdown_risk)
        score = max(
            0.0,
            min(
                100.0,
                50.0
                + avg_ev * 120.0
                + avg_conf * 20.0
                + spread_quality * 10.0
                + liquidity_quality * 10.0
                + stability * 5.0
                - drawdown_risk * 20.0
                - avg_slippage * 15.0
                - min(1.0, avg_vol) * 10.0,
            ),
        )
        family_rows.append(
            {
                "family": family,
                "sample_count": count,
                "execution_rate": round(execution_rate, 4),
                "expected_profit": round(avg_profit, 6),
                "expected_loss": round(avg_loss, 6),
                "expected_value": round(avg_ev, 6),
                "confidence": round(avg_conf, 4),
                "spread_quality": round(spread_quality, 4),
                "liquidity_quality": round(liquidity_quality, 4),
                "stability": round(stability, 4),
                "drawdown_risk": round(drawdown_risk, 4),
                "avg_hold_time": round(avg_hold, 4),
                "avg_slippage": round(avg_slippage, 6),
                "tier": tier,
                "score": round(score, 2),
                "rejection_reasons": dict(sorted(stats["rejection_reasons"].items(), key=lambda kv: (-kv[1], kv[0]))),
                "regimes": dict(sorted(stats["regimes"].items(), key=lambda kv: (-kv[1], kv[0]))),
                "tier_counts": dict(stats["tier_counts"]),
                "phase55_success_rate": round(stats["phase55_success"] / count, 4),
                "hard_block_rate": round(stats["hard_block_count"] / count, 4),
                "strict_reject_rate": round(stats["strict_reject_count"] / count, 4),
            }
        )
    family_rows.sort(key=lambda row: (row["score"], row["expected_value"], row["confidence"]), reverse=True)
    heatmap = {
        "families": family_rows,
        "best_families": [row["family"] for row in family_rows[:10]],
        "worst_families": [row["family"] for row in family_rows[-10:]],
        "profitable_families": [row["family"] for row in family_rows if row["expected_value"] > 0.0 and row["confidence"] >= 0.60],
        "dangerous_families": [row["family"] for row in family_rows if row["expected_value"] <= 0.0 or row["hard_block_rate"] > 0.20],
        "generated_at": None,
    }
    return heatmap


def summarize_family_promotion(heatmap: Dict[str, Any]) -> Dict[str, Any]:
    families = heatmap.get("families") if isinstance(heatmap, dict) else []
    families = families if isinstance(families, list) else []
    promoted = {}
    for row in families:
        family = str(row.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"
        tier = str(row.get("tier") or "C").strip().upper() or "C"
        promoted[family] = {
            "tier": tier,
            "score": row.get("score", 0.0),
            "sample_count": row.get("sample_count", 0),
            "expected_value": row.get("expected_value", 0.0),
            "confidence": row.get("confidence", 0.0),
            "execution_rate": row.get("execution_rate", 0.0),
        }
    return promoted

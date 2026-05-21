from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def _candidate_paths() -> list[Path]:
    return [
        Path("/opt/kalshi-bot/runtime/profit_engine/latest_family_heatmap.json"),
        Path("/opt/kalshi-bot/runtime/profit_engine/latest_summary.json"),
        Path("/tmp/kalshi-bot/runtime/profit_engine/latest_family_heatmap.json"),
        Path("/tmp/kalshi-bot/runtime/profit_engine/latest_summary.json"),
    ]


def load_latest_family_promotion_policy() -> Dict[str, Any]:
    for path in _candidate_paths():
        try:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
        except Exception:
            continue
    return {}


def _family_map(policy: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    families = policy.get("families") if isinstance(policy, dict) else None
    if isinstance(families, list):
        mapping: Dict[str, Dict[str, Any]] = {}
        for row in families:
            if not isinstance(row, dict):
                continue
            family = str(row.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"
            mapping[family] = row
        return mapping
    summary = policy.get("recommended_live_policy") if isinstance(policy, dict) else None
    if isinstance(summary, dict):
        profitable = summary.get("profitable_rules") if isinstance(summary.get("profitable_rules"), list) else []
        mapping = {}
        for family in profitable:
            if isinstance(family, str):
                mapping[family.upper()] = {"tier": "B"}
        return mapping
    return {}


def get_family_live_policy(family: str, *, policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    family = str(family or "").strip().upper() or "UNKNOWN"
    policy = policy if isinstance(policy, dict) else load_latest_family_promotion_policy()
    mapping = _family_map(policy)
    row = mapping.get(family)
    if not isinstance(row, dict):
        return {"family": family, "tier": None, "max_count": None, "max_concurrency": None, "live_enabled": False}
    tier = str(row.get("tier") or "C").strip().upper() or "C"
    if tier == "S":
        return {"family": family, "tier": tier, "max_count": 5, "max_concurrency": 3, "live_enabled": True}
    if tier == "A":
        return {"family": family, "tier": tier, "max_count": 3, "max_concurrency": 2, "live_enabled": True}
    if tier == "B":
        return {"family": family, "tier": tier, "max_count": 1, "max_concurrency": 1, "live_enabled": True}
    if tier == "C":
        return {"family": family, "tier": tier, "max_count": 1, "max_concurrency": 0, "live_enabled": False}
    return {"family": family, "tier": "D", "max_count": 0, "max_concurrency": 0, "live_enabled": False}

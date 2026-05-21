#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from aion_telemetry import emit_compact_event
from runtime.paths import ensure_runtime_layout, safe_atomic_json_write

REPO_ROOT = Path("/opt/kalshi-bot").resolve()
MEMORY_ROOT = REPO_ROOT / "aion_memory"
MEMORY_FILE = MEMORY_ROOT / "sentinel_memory.jsonl"
LATEST_SUMMARY_FILE = MEMORY_ROOT / "latest_memory_summary.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_memory_layout() -> None:
    ensure_runtime_layout()
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _read_lines() -> List[Dict[str, Any]]:
    ensure_memory_layout()
    records: List[Dict[str, Any]] = []
    if not MEMORY_FILE.exists():
        return records
    try:
        for raw in MEMORY_FILE.read_text(encoding="utf-8").splitlines():
            payload = _safe_json(raw)
            if isinstance(payload, dict):
                records.append(payload)
    except Exception:
        return records
    return records


def _issue_fingerprint(issue: Dict[str, Any]) -> str:
    issue = issue if isinstance(issue, dict) else {}
    parts = [
        str(issue.get("issue_type") or "").strip().lower(),
        str(issue.get("variable") or "").strip().lower(),
        str(issue.get("function_name") or issue.get("function") or "").strip().lower(),
        str(issue.get("file_path") or issue.get("file") or "").strip().lower(),
        str(issue.get("remaining_blocker") or issue.get("blocker_label") or "").strip().lower(),
    ]
    return "|".join(parts)


def find_match(issue: Dict[str, Any], *, emit: bool = True) -> Optional[Dict[str, Any]]:
    fingerprint = _issue_fingerprint(issue)
    if not fingerprint.strip("|"):
        return None
    for record in reversed(_read_lines()):
        if str(record.get("fingerprint") or "") == fingerprint:
            if emit:
                emit_compact_event(
                    "AION_MEMORY_MATCH_FOUND",
                    "memory match found",
                    {
                        "fingerprint": fingerprint,
                        "issue_type": issue.get("issue_type"),
                        "variable": issue.get("variable"),
                        "function_name": issue.get("function_name") or issue.get("function"),
                        "recurrence_count": record.get("recurrence_count"),
                    },
                    component="sentinel",
                    actionable=True,
                )
            return record
    return None


def record_issue(issue: Dict[str, Any], *, verification: Optional[Dict[str, Any]] = None, proposal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ensure_memory_layout()
    issue = issue if isinstance(issue, dict) else {}
    fingerprint = _issue_fingerprint(issue)
    existing = find_match(issue, emit=False) or {}
    recurrence_count = int(existing.get("recurrence_count", 0) or 0) + 1
    record = {
        "timestamp": _now_iso(),
        "fingerprint": fingerprint,
        "issue_type": issue.get("issue_type"),
        "severity": issue.get("severity"),
        "variable": issue.get("variable"),
        "function_name": issue.get("function_name") or issue.get("function"),
        "file_path": issue.get("file_path") or issue.get("file"),
        "remaining_blocker": issue.get("remaining_blocker") or issue.get("blocker_label"),
        "root_cause": issue.get("root_cause") or issue.get("reason"),
        "affected_files": issue.get("affected_files") or [issue.get("file_path") or issue.get("file")] if (issue.get("file_path") or issue.get("file")) else [],
        "proposed_fixes": issue.get("proposed_fixes") or [],
        "approved_fixes": issue.get("approved_fixes") or [],
        "rejected_fixes": issue.get("rejected_fixes") or [],
        "verification_results": verification or issue.get("verification_results") or {},
        "proposal": proposal or issue.get("proposal") or {},
        "recurrence_count": recurrence_count,
        "patch_history": issue.get("patch_history") or [],
        "safety_state": issue.get("safety_state") or {},
    }
    try:
        with MEMORY_FILE.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        safe_atomic_json_write(LATEST_SUMMARY_FILE, summarize_memory(), sort_keys=True, indent=2)
        emit_compact_event(
            "AION_MEMORY_RECORD_CREATED",
            "memory record created",
            {
                "fingerprint": fingerprint,
                "issue_type": record.get("issue_type"),
                "variable": record.get("variable"),
                "function_name": record.get("function_name"),
                "recurrence_count": recurrence_count,
            },
            component="sentinel",
            actionable=True,
        )
        if recurrence_count > 1:
            emit_compact_event(
                "AION_MEMORY_RECURRING_FAILURE",
                "recurring issue detected",
                {
                    "fingerprint": fingerprint,
                    "issue_type": record.get("issue_type"),
                    "variable": record.get("variable"),
                    "function_name": record.get("function_name"),
                    "recurrence_count": recurrence_count,
                },
                component="sentinel",
                level="WARN",
                actionable=True,
            )
    except Exception:
        pass
    return record


def summarize_memory(limit: int = 50) -> Dict[str, Any]:
    records = _read_lines()
    issue_counts = Counter(str(item.get("fingerprint") or "unknown") for item in records)
    recurring = [item for item in records if int(item.get("recurrence_count", 0) or 0) > 1]
    return {
        "timestamp": _now_iso(),
        "record_count": len(records),
        "recurring_count": len(recurring),
        "top_fingerprints": [{"fingerprint": fp, "count": count} for fp, count in issue_counts.most_common(limit)],
        "latest_records": records[-limit:],
    }


def latest_memory_summary() -> Dict[str, Any]:
    if LATEST_SUMMARY_FILE.exists():
        payload = _safe_json(LATEST_SUMMARY_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    return summarize_memory()

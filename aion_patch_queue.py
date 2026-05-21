#!/usr/bin/env python3
from __future__ import annotations

import difflib
import json
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from aion_telemetry import emit_compact_event, redact_sensitive_fields
from runtime.paths import ensure_runtime_layout, safe_atomic_json_write

REPO_ROOT = Path("/opt/kalshi-bot").resolve()
QUEUE_ROOT = REPO_ROOT / "aion_patch_queue"
PROPOSALS_ROOT = QUEUE_ROOT / "proposals"
AUDIT_ROOT = REPO_ROOT / "aion_audit"
LATEST_INDEX_FILE = QUEUE_ROOT / "latest_index.json"
LATEST_SUMMARY_FILE = QUEUE_ROOT / "latest_summary.json"
LATEST_PENDING_FILE = QUEUE_ROOT / "latest_pending.json"

PATCH_DENYLIST_PHRASES = (
    "delete critical files",
    "remove risk guards",
    "disable telemetry",
    "disable phase gates",
    "disable duplicate protections",
    "live order execution changes",
    "automatic service restarts",
    "change credentials",
    "change secrets",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_dirs() -> None:
    ensure_runtime_layout()
    QUEUE_ROOT.mkdir(parents=True, exist_ok=True)
    PROPOSALS_ROOT.mkdir(parents=True, exist_ok=True)
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _proposal_path(proposal_id: str) -> Path:
    return PROPOSALS_ROOT / f"{proposal_id}.json"


def _audit_dir(proposal_id: str) -> Path:
    return AUDIT_ROOT / proposal_id


def _proposal_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"


def _fingerprint(issue: Dict[str, Any]) -> str:
    issue = issue if isinstance(issue, dict) else {}
    parts = [
        str(issue.get("issue_type") or "").strip().lower(),
        str(issue.get("variable") or "").strip().lower(),
        str(issue.get("function_name") or issue.get("function") or "").strip().lower(),
        str(issue.get("file_path") or issue.get("file") or "").strip().lower(),
        str(issue.get("remaining_blocker") or issue.get("blocker_label") or "").strip().lower(),
    ]
    return "|".join(parts)


def _coerce_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _truncate(text: Any, limit: int = 1200) -> str:
    rendered = str(text or "")
    if len(rendered) <= limit:
        return rendered
    return rendered[: max(0, limit - 12)] + " ...[truncated]"


def _proposal_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "proposal_id": record.get("proposal_id"),
        "timestamp": record.get("timestamp"),
        "status": record.get("status"),
        "severity": record.get("severity"),
        "issue_fingerprint": record.get("issue_fingerprint"),
        "diagnosis": record.get("diagnosis"),
        "root_cause": record.get("root_cause"),
        "affected_files": record.get("affected_files") or [],
        "function_name": record.get("function_name"),
        "variable": record.get("variable"),
        "risk_score": record.get("risk_score"),
        "confidence_score": record.get("confidence_score"),
        "safety_score": record.get("safety_score"),
        "auto_apply_allowed": bool(record.get("auto_apply_allowed")),
        "require_human_confirm": bool(record.get("require_human_confirm")),
    }


def _append_audit_entry(proposal_id: str, entry: Dict[str, Any]) -> None:
    audit_dir = _audit_dir(proposal_id)
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "audit.jsonl"
    with audit_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def _refresh_indexes() -> Dict[str, Any]:
    proposals = list_proposals()
    counts = Counter(str(item.get("status") or "unknown") for item in proposals)
    latest_pending = next((item for item in proposals if str(item.get("status") or "").lower() == "pending"), {})
    summary = {
        "timestamp": _now_iso(),
        "proposal_count": len(proposals),
        "status_counts": dict(counts),
        "pending_count": counts.get("pending", 0),
        "approved_count": counts.get("approved", 0),
        "rejected_count": counts.get("rejected", 0),
        "applied_count": counts.get("applied", 0),
        "verified_count": counts.get("verified", 0),
        "rolled_back_count": counts.get("rolled_back", 0),
        "failed_count": counts.get("failed", 0),
        "latest_pending": _proposal_summary(latest_pending),
        "top_proposals": [_proposal_summary(item) for item in proposals[:10]],
    }
    safe_atomic_json_write(LATEST_INDEX_FILE, summary, sort_keys=True, indent=2)
    safe_atomic_json_write(LATEST_SUMMARY_FILE, summary, sort_keys=True, indent=2)
    pending = next((item for item in reversed(proposals) if str(item.get("status")) == "pending"), None)
    if pending:
        safe_atomic_json_write(LATEST_PENDING_FILE, _proposal_summary(pending), sort_keys=True, indent=2)
    return summary


def check_denylist(payload: Dict[str, Any]) -> List[str]:
    payload = payload if isinstance(payload, dict) else {}
    text = json.dumps(redact_sensitive_fields(payload), sort_keys=True, default=str).lower()
    return [phrase for phrase in PATCH_DENYLIST_PHRASES if phrase in text]


def create_proposal(
    *,
    issue: Dict[str, Any],
    ai_bridge: Optional[Dict[str, Any]] = None,
    structured: Optional[Dict[str, Any]] = None,
    proposal: Optional[Dict[str, Any]] = None,
    verification: Optional[Dict[str, Any]] = None,
    unified_diff_patch: str = "",
    patch_preview: str = "",
    source: str = "sentinel",
    status: str = "pending",
    incident_dir: Optional[Path] = None,
    current_safety_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _ensure_dirs()
    issue = issue if isinstance(issue, dict) else {}
    ai_bridge = ai_bridge if isinstance(ai_bridge, dict) else {}
    structured = structured if isinstance(structured, dict) else {}
    proposal = proposal if isinstance(proposal, dict) else {}
    verification = verification if isinstance(verification, dict) else {}
    proposal_id = str(proposal.get("proposal_id") or ai_bridge.get("proposal_id") or issue.get("proposal_id") or _proposal_id()).strip()
    audit_dir = _audit_dir(proposal_id)
    audit_dir.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    verification_commands = _coerce_list(structured.get("verification_commands")) or _coerce_list(ai_bridge.get("verification_commands"))
    affected_files = _coerce_list(proposal.get("proposed_files")) or _coerce_list(structured.get("proposed_files"))
    if not affected_files:
        affected_files = [p for p in [issue.get("file_path") or issue.get("file")] if p]
    record = {
        "proposal_id": proposal_id,
        "timestamp": now,
        "issue_fingerprint": _fingerprint(issue),
        "issue_type": issue.get("issue_type"),
        "severity": issue.get("severity"),
        "diagnosis": structured.get("diagnosis") or proposal.get("diagnosis") or ai_bridge.get("diagnosis") or "unknown",
        "root_cause": structured.get("root_cause") or proposal.get("root_cause") or ai_bridge.get("root_cause") or issue.get("root_cause") or "unknown",
        "affected_files": affected_files,
        "affected_files_functions": {
            "file_path": issue.get("file_path") or issue.get("file"),
            "function_name": issue.get("function_name") or issue.get("function"),
            "variable": issue.get("variable"),
            "nearby_scope": issue.get("traceback_context") or issue.get("nearby_scope") or {},
        },
        "unified_diff_patch": unified_diff_patch or proposal.get("unified_diff_patch") or "",
        "patch_preview": patch_preview or proposal.get("patch_preview") or "",
        "verification_commands": verification_commands,
        "rollback_plan": proposal.get("rollback_plan") or structured.get("rollback_plan") or issue.get("rollback_plan") or "restore backup and re-verify",
        "risk_score": float(verification.get("risk_score") or proposal.get("risk_score") or 0.0),
        "confidence_score": float(verification.get("confidence_score") or proposal.get("confidence_score") or 0.0),
        "safety_score": float(verification.get("safety_score") or proposal.get("safety_score") or 0.0),
        "status": status,
        "status_history": [{"timestamp": now, "status": status, "actor": source, "reason": "created"}],
        "created_by": source,
        "last_updated": now,
        "auto_apply_allowed": False,
        "require_human_confirm": bool(structured.get("require_human_confirm", True)),
        "patch_type": structured.get("patch_type") or proposal.get("patch_type") or ai_bridge.get("patch_type") or "other",
        "variable": structured.get("variable") or proposal.get("variable") or issue.get("variable"),
        "function_name": structured.get("function_name") or proposal.get("function_name") or issue.get("function_name") or issue.get("function"),
        "file_path": structured.get("file_path") or proposal.get("file_path") or issue.get("file_path") or issue.get("file"),
        "initializer_strategy": structured.get("initializer_strategy") or proposal.get("initializer_strategy") or "unknown",
        "default_value": structured.get("default_value") if "default_value" in structured else proposal.get("default_value"),
        "notes": structured.get("notes") or proposal.get("notes") or ai_bridge.get("notes") or "",
        "requires_compile": bool(proposal.get("requires_compile", structured.get("requires_compile", True))),
        "requires_restart": bool(proposal.get("requires_restart", structured.get("requires_restart", True))),
        "rollback_on_failure": bool(proposal.get("rollback_on_failure", structured.get("rollback_on_failure", True))),
        "issue": redact_sensitive_fields(issue),
        "ai_bridge": redact_sensitive_fields(ai_bridge),
        "structured": redact_sensitive_fields(structured),
        "verification": redact_sensitive_fields(verification),
        "proposal": redact_sensitive_fields(proposal),
        "current_safety_state": redact_sensitive_fields(current_safety_state or issue.get("current_safety_state") or {}),
        "backup_dir": None,
        "applied_at": None,
        "verified_at": None,
        "rolled_back_at": None,
        "failed_at": None,
        "actor": source,
    }
    safe_atomic_json_write(_proposal_path(proposal_id), record, sort_keys=True, indent=2)
    safe_atomic_json_write(audit_dir / "proposal.json", record, sort_keys=True, indent=2)
    _append_audit_entry(proposal_id, {"timestamp": now, "action": "created", "actor": source, "status": status, "summary": _proposal_summary(record)})
    summary = _refresh_indexes()
    emit_compact_event(
        "AION_PATCH_PROPOSAL_CREATED",
        "patch proposal queued",
        {
            "proposal_id": proposal_id,
            "issue_type": record.get("issue_type"),
            "severity": record.get("severity"),
            "status": status,
            "risk_score": record.get("risk_score"),
            "confidence_score": record.get("confidence_score"),
            "safety_score": record.get("safety_score"),
            "files": record.get("affected_files"),
        },
        component="sentinel",
        actionable=True,
    )
    if status == "pending":
        emit_compact_event(
            "AION_PATCH_QUEUE_PENDING",
            "patch proposal queued pending human approval",
            _proposal_summary(record),
            component="sentinel",
            actionable=True,
        )
    return record


def load_proposal(proposal_id: str) -> Optional[Dict[str, Any]]:
    _ensure_dirs()
    proposal_id = str(proposal_id or "").strip()
    if not proposal_id:
        return None
    path = _proposal_path(proposal_id)
    if not path.exists():
        return None
    try:
        payload = _safe_json(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def list_proposals(status: Optional[str] = None) -> List[Dict[str, Any]]:
    _ensure_dirs()
    proposals: List[Dict[str, Any]] = []
    for path in sorted(PROPOSALS_ROOT.glob("*.json")):
        try:
            payload = _safe_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        payload.setdefault("proposal_id", path.stem)
        payload.setdefault("_path", str(path))
        proposals.append(payload)
    proposals.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    if status:
        status = str(status or "").strip().lower()
        proposals = [item for item in proposals if str(item.get("status") or "").strip().lower() == status]
    return proposals


def update_proposal_status(
    proposal_id: str,
    status: str,
    *,
    actor: str = "sentinel",
    reason: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    proposal = load_proposal(proposal_id)
    if not proposal:
        return None
    now = _now_iso()
    status = str(status or "").strip().lower()
    proposal["status"] = status
    proposal["last_updated"] = now
    proposal["actor"] = actor
    proposal["status_history"] = list(proposal.get("status_history") or [])
    proposal["status_history"].append({"timestamp": now, "status": status, "actor": actor, "reason": reason or status})
    if isinstance(details, dict):
        proposal.update({key: value for key, value in details.items() if value is not None})
    if status == "approved":
        proposal["approved_at"] = now
    elif status == "rejected":
        proposal["rejected_at"] = now
    elif status == "applied":
        proposal["applied_at"] = now
    elif status == "verified":
        proposal["verified_at"] = now
    elif status == "rolled_back":
        proposal["rolled_back_at"] = now
    elif status == "failed":
        proposal["failed_at"] = now
    path = _proposal_path(proposal_id)
    safe_atomic_json_write(path, proposal, sort_keys=True, indent=2)
    audit_dir = _audit_dir(proposal_id)
    audit_dir.mkdir(parents=True, exist_ok=True)
    safe_atomic_json_write(audit_dir / "proposal.json", proposal, sort_keys=True, indent=2)
    _append_audit_entry(proposal_id, {"timestamp": now, "action": "status_update", "actor": actor, "status": status, "reason": reason, "details": redact_sensitive_fields(details or {}), "summary": _proposal_summary(proposal)})
    _refresh_indexes()
    telemetry_event = {
        "proposal_id": proposal_id,
        "status": status,
        "reason": reason,
        "summary": _proposal_summary(proposal),
    }
    event_map = {
        "approved": ("AION_PATCH_APPROVED", "patch proposal approved"),
        "rejected": ("AION_PATCH_REJECTED", "patch proposal rejected"),
        "applied": ("AION_PATCH_APPLY_SUCCESS", "patch proposal applied"),
        "verified": ("AION_PATCH_VERIFIED", "patch proposal verified"),
        "rolled_back": ("AION_PATCH_ROLLBACK", "patch proposal rolled back"),
        "failed": ("AION_PATCH_APPLY_FAILED", "patch proposal failed"),
    }
    if status in event_map:
        event_name, summary = event_map[status]
        emit_compact_event(event_name, summary, telemetry_event, component="sentinel", level="WARN" if status in {"rejected", "failed", "rolled_back"} else "INFO", actionable=True)
    return proposal


def summarize_queue() -> Dict[str, Any]:
    proposals = list_proposals()
    counts = Counter(str(item.get("status") or "unknown") for item in proposals)
    pending = [item for item in proposals if str(item.get("status") or "").lower() == "pending"]
    approved = [item for item in proposals if str(item.get("status") or "").lower() == "approved"]
    rejected = [item for item in proposals if str(item.get("status") or "").lower() == "rejected"]
    applied = [item for item in proposals if str(item.get("status") or "").lower() == "applied"]
    verified = [item for item in proposals if str(item.get("status") or "").lower() == "verified"]
    rolled_back = [item for item in proposals if str(item.get("status") or "").lower() == "rolled_back"]
    failed = [item for item in proposals if str(item.get("status") or "").lower() == "failed"]
    summary = {
        "timestamp": _now_iso(),
        "queue_root": str(QUEUE_ROOT),
        "audit_root": str(AUDIT_ROOT),
        "proposal_count": len(proposals),
        "status_counts": dict(counts),
        "pending_count": len(pending),
        "approved_count": len(approved),
        "rejected_count": len(rejected),
        "applied_count": len(applied),
        "verified_count": len(verified),
        "rolled_back_count": len(rolled_back),
        "failed_count": len(failed),
        "latest_pending": _proposal_summary(pending[0]) if pending else {},
        "recent_proposals": [_proposal_summary(item) for item in proposals[:20]],
    }
    safe_atomic_json_write(LATEST_SUMMARY_FILE, summary, sort_keys=True, indent=2)
    safe_atomic_json_write(LATEST_INDEX_FILE, summary, sort_keys=True, indent=2)
    if pending:
        safe_atomic_json_write(LATEST_PENDING_FILE, _proposal_summary(pending[0]), sort_keys=True, indent=2)
    return summary


def build_mobile_summary(record: Dict[str, Any]) -> str:
    record = record if isinstance(record, dict) else {}
    files = record.get("affected_files") or []
    commands = record.get("verification_commands") or []
    return "\n".join(
        [
            "PENDING PATCH" if str(record.get("status") or "").lower() == "pending" else "PATCH STATUS",
            f"ID: {record.get('proposal_id') or 'unknown'}",
            f"Risk: {str(record.get('risk_score', 'n/a')).upper() if record.get('risk_score') is not None else 'n/a'}",
            f"Confidence: {record.get('confidence_score', 'n/a')}",
            "Files:",
            *[f"- {item}" for item in (files[:6] or ["none"])],
            "Summary:",
            f"{_truncate(record.get('diagnosis') or record.get('root_cause') or 'unknown', 200)}",
            "Approve?",
            f"python3 tools/aion_patch_control.py --approve {record.get('proposal_id')}",
        ]
    )

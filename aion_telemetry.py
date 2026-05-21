#!/usr/bin/env python3
from __future__ import annotations

import builtins
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from runtime.paths import ensure_runtime_layout, safe_atomic_json_write

REPO_ROOT = Path("/opt/kalshi-bot").resolve()
TELEMETRY_ROOT = REPO_ROOT / "runtime" / "telemetry"
TELEMETRY_FALLBACK_ROOT = Path(os.environ.get("AION_TELEMETRY_RUNTIME_FALLBACK_ROOT", "/tmp/kalshi-bot/runtime/telemetry")).expanduser().resolve()
TELEMETRY_JSONL = TELEMETRY_ROOT / "aion_events.jsonl"
HUMAN_SUMMARY_FILE = TELEMETRY_ROOT / "latest_human_summary.txt"
CODEX_DIAGNOSTIC_FILE = TELEMETRY_ROOT / "latest_codex_diagnostic.txt"
MAX_RUNTIME_LOG_MB = int(os.environ.get("AION_MAX_RUNTIME_LOG_MB", "100") or 100)
MAX_ARTIFACT_AGE_DAYS = int(os.environ.get("AION_MAX_ARTIFACT_AGE_DAYS", "7") or 7)
EVENT_DEDUPE_SECONDS = int(os.environ.get("AION_EVENT_DEDUPE_SECONDS", "60") or 60)
SUMMARY_INTERVAL_SECONDS = int(os.environ.get("AION_SUMMARY_INTERVAL_SECONDS", "60") or 60)
DEBUG_VERBOSE = str(os.environ.get("AION_DEBUG_VERBOSE", "false")).strip().lower() in {"1", "true", "yes", "on"}
TELEMETRY_LEVEL = str(os.environ.get("AION_TELEMETRY_LEVEL", "INFO") or "INFO").strip().upper()

IMPORTANT_EVENTS = {
    "AION-CANDIDATE-SELECTED",
    "AION-CANONICAL-OUTCOME-ASSIGNED",
    "AION-FINAL-SUBMIT-PREFLIGHT",
    "AION-FINAL-SUBMIT-CALL",
    "LIVE-EXECUTION-ORDER-SUBMITTED",
    "LIVE-EXECUTION-ORDER-ERROR",
    "AION-LIVE-TRUST-APPROVED-BY-SIMULATION",
    "AION-LIVE-TRUST-BLOCKED-BY-SIMULATION",
    "AION-SIMULATION-START",
    "AION-SIMULATION-PASS",
    "AION-SIMULATION-FAIL",
    "AION-SIMULATION-BUG-CAUGHT",
    "AION-SIMULATION-SUMMARY",
    "AION-PROFIT-DISCOVERY-START",
    "AION-PROFIT-DISCOVERY-SUMMARY",
    "AION-FAMILY-HEATMAP-WRITTEN",
    "AION-LIVE-POLICY-RECOMMENDATION",
    "SENTINEL-DETERMINISTIC-FIX-START",
    "SENTINEL-AUTO-PATCH-APPLIED",
    "SENTINEL-AUTO-PATCH-FAILED",
    "SENTINEL-AI-ESCALATION-PACKAGE-CREATED",
    "SENTINEL-GUARDIAN-CLEARED",
    "AION-DAILY-INTEGRITY-BUILD",
    "AION-DAILY-INTEGRITY-SENT",
    "AION-DAILY-INTEGRITY-ERROR",
    "AION-GUARDIAN-SUMMARY",
    "AION-BLOCKER-SUMMARY",
    "AION-TELEMETRY-SUPPRESSED",
    "AION-STATE-TRANSITION",
    "AION-ERROR-EVENT",
    "AION-CRITICAL-EVENT",
    "AION-SCOPE-STABILIZED",
    "AION_SENTINEL_AI_ESCALATION",
    "AION_SENTINEL_AI_REQUEST",
    "AION_SENTINEL_AI_RESPONSE",
    "AION_SENTINEL_AI_PATCH_PROPOSAL",
    "AION_SENTINEL_AI_VERIFICATION",
    "AION_SENTINEL_AI_REJECTED",
    "AION_SENTINEL_AI_APPROVED",
    "AION_SENTINEL_AI_SIMULATION",
    "AION_SENTINEL_AI_ERROR",
    "AION_SENTINEL_AI_DISABLED",
    "AION_SENTINEL_AI_KEY_MISSING",
    "AION_SENTINEL_AI_REQUIRE_HUMAN_CONFIRM",
    "AION_MEMORY_RECORD_CREATED",
    "AION_MEMORY_MATCH_FOUND",
    "AION_MEMORY_RECURRING_FAILURE",
    "AION_SANDBOX_REPLAY_START",
    "AION_SANDBOX_REPLAY_RESULT",
    "AION_SANDBOX_REPLAY_SAFE",
    "AION_SANDBOX_REPLAY_FAILED",
    "AION_GUARDIAN_SENTINEL_HANDOFF",
    "AION_GUARDIAN_INTEGRITY_ALERT",
    "AION_PATCH_PROPOSAL_CREATED",
    "AION_PATCH_QUEUE_PENDING",
    "AION_PATCH_APPROVED",
    "AION_PATCH_REJECTED",
    "AION_PATCH_APPLY_START",
    "AION_PATCH_APPLY_SUCCESS",
    "AION_PATCH_APPLY_FAILED",
    "AION_PATCH_ROLLBACK",
    "AION_PATCH_VERIFIED",
    "AION_PATCH_DENYLIST_BLOCK",
    "AION_PATCH_QUEUE_STATUS",
    "AION-BLOCKER-NUKE-START",
    "AION-BLOCKER-SANITIZED",
    "AION-LEGACY-PHASE55-PURGED",
    "AION-FINAL-HANDOFF-TRACE",
    "AION-BLOCKER-EXTINCTION-CHECK",
    "AION-BLOCKER-NUKE-COMPLETE",
    "AION-CANONICAL-OUTCOME-ASSIGNED",
}

_last_event_ts: Dict[Tuple[str, str, str, str], float] = {}
_last_stacktrace_ts: Dict[str, float] = {}
_event_counters: Counter = Counter()
_summary_counters: Counter = Counter()
_suppressed_counters: Counter = Counter()
_last_state_by_key: Dict[str, str] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_dirs() -> None:
    ensure_runtime_layout()
    global TELEMETRY_ROOT, TELEMETRY_JSONL, HUMAN_SUMMARY_FILE, CODEX_DIAGNOSTIC_FILE
    try:
        TELEMETRY_ROOT.mkdir(parents=True, exist_ok=True)
    except Exception:
        TELEMETRY_ROOT = TELEMETRY_FALLBACK_ROOT
        TELEMETRY_ROOT.mkdir(parents=True, exist_ok=True)
        TELEMETRY_JSONL = TELEMETRY_ROOT / "aion_events.jsonl"
        HUMAN_SUMMARY_FILE = TELEMETRY_ROOT / "latest_human_summary.txt"
        CODEX_DIAGNOSTIC_FILE = TELEMETRY_ROOT / "latest_codex_diagnostic.txt"


def _rotate_logs() -> None:
    try:
        if TELEMETRY_JSONL.exists() and TELEMETRY_JSONL.stat().st_size > MAX_RUNTIME_LOG_MB * 1024 * 1024:
            backup = TELEMETRY_JSONL.with_suffix(f".jsonl.{int(time.time())}.old")
            TELEMETRY_JSONL.replace(backup)
    except Exception:
        pass
    cutoff = time.time() - (MAX_ARTIFACT_AGE_DAYS * 86400)
    for path in TELEMETRY_ROOT.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff and path.name not in {TELEMETRY_JSONL.name}:
                path.unlink()
        except Exception:
            continue


def redact_sensitive_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    sensitive_keys = {"api_key", "token", "secret", "authorization", "auth", "password", "private_key"}
    redacted = {}
    for key, value in (payload or {}).items():
        if str(key).lower() in sensitive_keys:
            redacted[key] = "***REDACTED***"
        elif isinstance(value, dict):
            redacted[key] = redact_sensitive_fields(value)
        elif isinstance(value, list):
            redacted[key] = [redact_sensitive_fields(v) if isinstance(v, dict) else v for v in value]
        else:
            redacted[key] = value
    return redacted


def _write_jsonl(event: Dict[str, Any]) -> None:
    global TELEMETRY_ROOT, TELEMETRY_JSONL, HUMAN_SUMMARY_FILE, CODEX_DIAGNOSTIC_FILE
    _ensure_dirs()
    _rotate_logs()
    line = json.dumps(event, sort_keys=True, default=str)
    try:
        with TELEMETRY_JSONL.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except Exception:
        TELEMETRY_ROOT = TELEMETRY_FALLBACK_ROOT
        TELEMETRY_ROOT.mkdir(parents=True, exist_ok=True)
        TELEMETRY_JSONL = TELEMETRY_ROOT / "aion_events.jsonl"
        HUMAN_SUMMARY_FILE = TELEMETRY_ROOT / "latest_human_summary.txt"
        CODEX_DIAGNOSTIC_FILE = TELEMETRY_ROOT / "latest_codex_diagnostic.txt"
        with TELEMETRY_JSONL.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")


def _event_level_priority(level: str) -> int:
    return {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}.get(str(level or "INFO").upper(), 20)


def _should_print_stdout(event: Dict[str, Any]) -> bool:
    if DEBUG_VERBOSE:
        return True
    level = str(event.get("level") or "INFO").upper()
    event_name = str(event.get("event") or "")
    if event_name in IMPORTANT_EVENTS:
        return True
    return _event_level_priority(level) >= _event_level_priority(TELEMETRY_LEVEL) and level in {"WARN", "ERROR", "CRITICAL"}


def _normalize_payload(event: str, payload: Optional[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
    base = dict(payload or {})
    base.update(kwargs)
    base["ts"] = base.get("ts") or _now_iso()
    base["event"] = event
    base["component"] = str(base.get("component") or "unknown")
    base["level"] = str(base.get("level") or "INFO").upper()
    base["trace_id"] = base.get("trace_id") or None
    base["ticker"] = base.get("ticker") or None
    base["family"] = base.get("family") or None
    base["cycle_id"] = base.get("cycle_id") or None
    base["state_from"] = base.get("state_from") or None
    base["state_to"] = base.get("state_to") or None
    base["reason"] = base.get("reason") or None
    base["counts"] = base.get("counts") if isinstance(base.get("counts"), dict) else {}
    base["metrics"] = base.get("metrics") if isinstance(base.get("metrics"), dict) else {}
    base["sample"] = base.get("sample") if isinstance(base.get("sample"), dict) else {}
    base["actionable"] = bool(base.get("actionable"))
    return redact_sensitive_fields(base)


def emit_event(event: str, payload: Optional[Dict[str, Any]] = None, *, level: str = "INFO", component: str = "unknown", trace_id: Optional[str] = None, ticker: Optional[str] = None, family: Optional[str] = None, cycle_id: Optional[str] = None, state_from: Optional[str] = None, state_to: Optional[str] = None, reason: Optional[str] = None, counts: Optional[Dict[str, Any]] = None, metrics: Optional[Dict[str, Any]] = None, sample: Optional[Dict[str, Any]] = None, actionable: bool = False, dedupe_key: Optional[str] = None, stdout: bool = True, **kwargs: Any) -> Dict[str, Any]:
    event_payload = _normalize_payload(
        event,
        payload,
        level=level,
        component=component,
        trace_id=trace_id,
        ticker=ticker,
        family=family,
        cycle_id=cycle_id,
        state_from=state_from,
        state_to=state_to,
        reason=reason,
        counts=counts,
        metrics=metrics,
        sample=sample,
        actionable=actionable,
        **kwargs,
    )
    key = (event, str(event_payload.get("ticker") or ""), str(event_payload.get("reason") or ""), str(event_payload.get("state_to") or ""))
    now = time.time()
    if dedupe_key:
        key = (event, str(event_payload.get("ticker") or ""), str(event_payload.get("reason") or ""), dedupe_key)
    if key in _last_event_ts and (now - _last_event_ts[key]) < EVENT_DEDUPE_SECONDS:
        _suppressed_counters[event] += 1
        return event_payload
    _last_event_ts[key] = now
    _event_counters[event] += 1
    _write_jsonl(event_payload)
    if stdout and _should_print_stdout(event_payload):
        builtins.print("[" + event + "] " + json.dumps(event_payload, sort_keys=True, default=str))
    return event_payload


def emit_once_per_key(event: str, payload: Optional[Dict[str, Any]] = None, *, key: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    return emit_event(event, payload, dedupe_key=key or "default", **kwargs)


def emit_rate_limited(event: str, payload: Optional[Dict[str, Any]] = None, *, dedupe_key: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
    return emit_event(event, payload, dedupe_key=dedupe_key or "rate_limited", **kwargs)


def emit_summary_counter(name: str, value: int = 1, *, component: str = "unknown", **kwargs: Any) -> Dict[str, Any]:
    _summary_counters[name] += int(value or 0)
    return emit_event(
        "AION-SUMMARY-COUNTER",
        {"counter": name, "value": _summary_counters[name]},
        component=component,
        **kwargs,
    )


def emit_state_transition(key: str, new_state: str, *, component: str = "unknown", ticker: Optional[str] = None, family: Optional[str] = None, reason: Optional[str] = None, trace_id: Optional[str] = None, cycle_id: Optional[str] = None, sample: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    old_state = _last_state_by_key.get(key)
    if old_state == new_state:
        return {"event": "AION-STATE-TRANSITION", "state_from": old_state, "state_to": new_state, "suppressed": True}
    _last_state_by_key[key] = new_state
    return emit_event(
        "AION-STATE-TRANSITION",
        {
            "state_from": old_state,
            "state_to": new_state,
            "key": key,
            "sample": sample or {},
        },
        component=component,
        ticker=ticker,
        family=family,
        reason=reason,
        trace_id=trace_id,
        cycle_id=cycle_id,
        actionable=True,
    )


def emit_error_event(event: str, payload: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    return emit_event(event, payload, level="ERROR", actionable=True, **kwargs)


def _truncate_for_compact(value: Any, *, max_len: int = 300, max_items: int = 12) -> Any:
    if isinstance(value, dict):
        compact: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= max_items:
                compact["..."] = f"+{max(0, len(value) - max_items)} more"
                break
            compact[str(key)] = _truncate_for_compact(item, max_len=max_len, max_items=max_items)
        return compact
    if isinstance(value, list):
        compact_list = [_truncate_for_compact(item, max_len=max_len, max_items=max_items) for item in value[:max_items]]
        if len(value) > max_items:
            compact_list.append(f"... +{len(value) - max_items} more")
        return compact_list
    if isinstance(value, str):
        if len(value) <= max_len:
            return value
        return value[: max(0, max_len - 18)] + f"...<{len(value) - max_len} more chars>"
    return value


def emit_compact_event(
    event: str,
    summary: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    level: str = "INFO",
    component: str = "unknown",
    trace_id: Optional[str] = None,
    ticker: Optional[str] = None,
    family: Optional[str] = None,
    cycle_id: Optional[str] = None,
    reason: Optional[str] = None,
    actionable: bool = False,
    stdout: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    compact_payload = _truncate_for_compact(payload or {}, max_len=350, max_items=10)
    event_payload = emit_event(
        event,
        {
            "summary": summary,
            "payload": compact_payload,
        },
        level=level,
        component=component,
        trace_id=trace_id,
        ticker=ticker,
        family=family,
        cycle_id=cycle_id,
        reason=reason,
        actionable=actionable,
        stdout=False,
        **kwargs,
    )
    if stdout and _should_print_stdout(event_payload):
        builtins.print(f"[{event}] {summary} :: {json.dumps(compact_payload, sort_keys=True, default=str)}")
    return event_payload


def flush_cycle_summary(*, component: str, summary: Optional[Dict[str, Any]] = None, human_summary: Optional[str] = None, codex_diagnostic: Optional[str] = None, cycle_id: Optional[str] = None) -> Dict[str, Any]:
    global TELEMETRY_ROOT, TELEMETRY_JSONL, HUMAN_SUMMARY_FILE, CODEX_DIAGNOSTIC_FILE
    summary = summary or {}
    payload = {
        "ts": _now_iso(),
        "component": component,
        "cycle_id": cycle_id,
        "summary": redact_sensitive_fields(summary),
        "event_counters": dict(_event_counters),
        "summary_counters": dict(_summary_counters),
        "suppressed_counters": dict(_suppressed_counters),
    }
    _ensure_dirs()
    try:
        safe_atomic_json_write(TELEMETRY_ROOT / "latest_summary.json", payload, sort_keys=True, indent=2)
    except Exception:
        TELEMETRY_ROOT = TELEMETRY_FALLBACK_ROOT
        TELEMETRY_ROOT.mkdir(parents=True, exist_ok=True)
        TELEMETRY_JSONL = TELEMETRY_ROOT / "aion_events.jsonl"
        HUMAN_SUMMARY_FILE = TELEMETRY_ROOT / "latest_human_summary.txt"
        CODEX_DIAGNOSTIC_FILE = TELEMETRY_ROOT / "latest_codex_diagnostic.txt"
        safe_atomic_json_write(TELEMETRY_ROOT / "latest_summary.json", payload, sort_keys=True, indent=2)
    if human_summary is not None:
        try:
            HUMAN_SUMMARY_FILE.write_text(human_summary.rstrip() + "\n", encoding="utf-8")
        except Exception:
            TELEMETRY_ROOT.mkdir(parents=True, exist_ok=True)
            HUMAN_SUMMARY_FILE.write_text(human_summary.rstrip() + "\n", encoding="utf-8")
    if codex_diagnostic is not None:
        try:
            CODEX_DIAGNOSTIC_FILE.write_text(codex_diagnostic.rstrip() + "\n", encoding="utf-8")
        except Exception:
            TELEMETRY_ROOT.mkdir(parents=True, exist_ok=True)
            CODEX_DIAGNOSTIC_FILE.write_text(codex_diagnostic.rstrip() + "\n", encoding="utf-8")
    if sum(_suppressed_counters.values()) > 0:
        emit_event(
            "AION-TELEMETRY-SUPPRESSED",
            {
                "suppressed": dict(_suppressed_counters),
                "window_seconds": SUMMARY_INTERVAL_SECONDS,
            },
            component=component,
            cycle_id=cycle_id,
            stdout=False,
        )
    emit_event("AION-SUMMARY-FLUSH", payload, component=component, cycle_id=cycle_id, stdout=False)
    return payload


def telemetry_print(*args: Any, **kwargs: Any) -> None:
    sep = kwargs.pop("sep", " ")
    end = kwargs.pop("end", "\n")
    file = kwargs.pop("file", None)
    flush = kwargs.pop("flush", False)
    text = sep.join(str(arg) for arg in args)
    stripped = text.strip()
    if stripped.startswith("[") and "]" in stripped:
        tag = stripped[1:stripped.index("]")]
        rest = stripped[stripped.index("]") + 1 :].strip()
        payload: Dict[str, Any]
        if rest.startswith("{") and rest.endswith("}"):
            try:
                payload = json.loads(rest)
            except Exception:
                payload = {"raw": rest}
        else:
            payload = {"raw": rest}
        emit_event(tag, payload, stdout=False)
        if _should_print_stdout({"event": tag, "level": "INFO", "ticker": payload.get("ticker"), "reason": payload.get("reason")}):
            builtins.print(text, sep=sep, end=end, file=file, flush=flush)
        return
    builtins.print(*args, sep=sep, end=end, file=file, flush=flush)

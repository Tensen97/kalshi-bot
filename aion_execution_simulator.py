#!/usr/bin/env python3
from __future__ import annotations

import argparse
import builtins
import csv
import io
import json
import os
import random
import re
import shutil
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from types import SimpleNamespace

from aion_telemetry import emit_compact_event, emit_event, emit_error_event, emit_once_per_key, emit_rate_limited, emit_state_transition, emit_summary_counter, flush_cycle_summary, redact_sensitive_fields, telemetry_print as print
from runtime.paths import ensure_runtime_layout, safe_atomic_json_write

from kalshi_web import (
    AION_AGGRESSIVE_MAX_CONTRACTS,
    DEFAULT_LIVE_ORDER_ACTION,
    KalshiSniperBot,
)


REPO_ROOT = Path("/opt/kalshi-bot").resolve()
RUNTIME_SENTINEL_ROOT = REPO_ROOT / "runtime" / "sentinel"
INCIDENTS_ROOT = RUNTIME_SENTINEL_ROOT / "incidents"
SIMULATION_ROOT = REPO_ROOT / "runtime" / "simulation"
SIMULATION_ISSUES_ROOT = SIMULATION_ROOT / "issues"
SIMULATION_LATEST_RUN_FILE = SIMULATION_ROOT / "latest_run.json"
SIMULATION_LATEST_ISSUE_FILE = SIMULATION_ROOT / "latest_issue.json"
SIMULATION_MAX_ISSUES = int(os.environ.get("AION_SIMULATION_MAX_ISSUES", "240") or 240)
PAPER30_ROOT = SIMULATION_ROOT / "paper30"
PROFIT_ENGINE_ROOT = REPO_ROOT / "runtime" / "profit_engine"

HARD_BLOCKER_TOKENS = {
    "auth failure",
    "auth_failed",
    "duplicate live position",
    "duplicate_position",
    "malformed payload",
    "payload_builder_rejected",
    "selected price <= 0",
    "selected_price<=0",
    "selected price >= 100",
    "selected_price>=100",
    "missing selected-side executable price",
    "no selected side",
    "no executable selected-side price",
    "daily cap",
    "max live positions",
    "kalshi api rejection",
    "dead orderbook",
    "corrupt orderbook",
    "orderbook_corrupt",
}

SOFT_WARNING_TOKENS = {
    "unsafe_spread",
    "unsafe_price",
    "spread_above_threshold",
    "spread_wait",
    "edge_below_threshold",
    "base_sports_threshold",
    "insufficient_liquidity",
    "quote_missing",
    "quote_not_usable",
    "orderbook_not_usable",
    "selected_price_above_0_99",
    "selected_price_below_0_01",
    "phase55",
    "placeholder_phase55_record",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_dirs() -> None:
    global RUNTIME_SENTINEL_ROOT, INCIDENTS_ROOT

    def _can_write(directory: Path) -> bool:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / f".simulation_write_test_{os.getpid()}_{int(time.time() * 1000)}"
            probe.write_text("ok", encoding="utf-8")
            try:
                probe.unlink()
            except Exception:
                pass
            return True
        except Exception:
            return False

    ensure_runtime_layout()
    if _can_write(RUNTIME_SENTINEL_ROOT) and _can_write(INCIDENTS_ROOT):
        return
    fallback_root = Path(os.environ.get("AION_SENTINEL_RUNTIME_FALLBACK_ROOT", "/tmp/kalshi-bot/runtime/sentinel")).expanduser().resolve()
    if fallback_root != RUNTIME_SENTINEL_ROOT:
        RUNTIME_SENTINEL_ROOT = fallback_root
        INCIDENTS_ROOT = RUNTIME_SENTINEL_ROOT / "incidents"
    if not (_can_write(RUNTIME_SENTINEL_ROOT) and _can_write(INCIDENTS_ROOT)):
        raise PermissionError(f"unwritable:{RUNTIME_SENTINEL_ROOT}")


def _ensure_simulation_dirs() -> None:
    global PAPER30_ROOT
    SIMULATION_ROOT.mkdir(parents=True, exist_ok=True)
    SIMULATION_ISSUES_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        PAPER30_ROOT.mkdir(parents=True, exist_ok=True)
    except Exception:
        PAPER30_ROOT = Path(os.environ.get("AION_PAPER30_RUNTIME_FALLBACK_ROOT", "/tmp/kalshi-bot/runtime/simulation/paper30")).expanduser().resolve()
        PAPER30_ROOT.mkdir(parents=True, exist_ok=True)


def _prune_simulation_issues() -> None:
    try:
        issue_files = sorted(
            [path for path in SIMULATION_ISSUES_ROOT.glob("*.json") if path.is_file()],
            key=lambda path: path.stat().st_mtime,
        )
    except Exception:
        return
    excess = max(0, len(issue_files) - max(1, SIMULATION_MAX_ISSUES))
    for path in issue_files[:excess]:
        try:
            path.unlink()
        except Exception:
            pass


def _count_simulation_issue_repeats(issue_type: str, ticker: Optional[str], failed_phase: Optional[str]) -> int:
    issue_type = str(issue_type or "").strip()
    ticker = str(ticker or "").strip()
    failed_phase = str(failed_phase or "").strip()
    if not issue_type and not ticker and not failed_phase:
        return 0
    count = 0
    try:
        for path in SIMULATION_ISSUES_ROOT.glob("*.json"):
            if not path.is_file():
                continue
            payload = _safe_json(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            if issue_type and str(payload.get("issue_type") or "").strip() != issue_type:
                continue
            if ticker and str(payload.get("ticker") or "").strip() != ticker:
                continue
            if failed_phase and str(payload.get("failed_phase") or "").strip() != failed_phase:
                continue
            count += 1
    except Exception:
        return 0
    return count


def _simulation_issue_file_path(issue_type: str, failed_phase: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_issue_type = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(issue_type or "simulation_issue").strip()) or "simulation_issue"
    safe_phase = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(failed_phase or "unknown").strip()) or "unknown"
    return SIMULATION_ISSUES_ROOT / f"{stamp}_{safe_issue_type}_{safe_phase}_{os.getpid()}.json"


def _write_simulation_issue(issue: Dict[str, Any]) -> Optional[Path]:
    try:
        _ensure_simulation_dirs()
        _prune_simulation_issues()
        path = _simulation_issue_file_path(issue.get("issue_type") or "simulation_issue", issue.get("failed_phase") or "unknown")
        safe_atomic_json_write(path, issue, sort_keys=True, indent=2)
        safe_atomic_json_write(SIMULATION_LATEST_ISSUE_FILE, issue, sort_keys=True, indent=2)
        return path
    except Exception:
        return None


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _ensure_paper30_dir(timestamp: Optional[str] = None) -> Path:
    global PAPER30_ROOT
    try:
        PAPER30_ROOT.mkdir(parents=True, exist_ok=True)
    except Exception:
        PAPER30_ROOT = Path(os.environ.get("AION_PAPER30_RUNTIME_FALLBACK_ROOT", "/tmp/kalshi-bot/runtime/simulation/paper30")).expanduser().resolve()
        PAPER30_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = PAPER30_ROOT / stamp
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        PAPER30_ROOT = Path(os.environ.get("AION_PAPER30_RUNTIME_FALLBACK_ROOT", "/tmp/kalshi-bot/runtime/simulation/paper30")).expanduser().resolve()
        PAPER30_ROOT.mkdir(parents=True, exist_ok=True)
        run_dir = PAPER30_ROOT / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _ensure_profit_engine_dir(timestamp: Optional[str] = None) -> Path:
    global PROFIT_ENGINE_ROOT
    try:
        PROFIT_ENGINE_ROOT.mkdir(parents=True, exist_ok=True)
    except Exception:
        PROFIT_ENGINE_ROOT = Path(os.environ.get("AION_PROFIT_ENGINE_RUNTIME_FALLBACK_ROOT", "/tmp/kalshi-bot/runtime/profit_engine")).expanduser().resolve()
        PROFIT_ENGINE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = PROFIT_ENGINE_ROOT / stamp
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        PROFIT_ENGINE_ROOT = Path(os.environ.get("AION_PROFIT_ENGINE_RUNTIME_FALLBACK_ROOT", "/tmp/kalshi-bot/runtime/profit_engine")).expanduser().resolve()
        PROFIT_ENGINE_ROOT.mkdir(parents=True, exist_ok=True)
        run_dir = PROFIT_ENGINE_ROOT / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _paper30_latest_summary_path() -> Path:
    return PAPER30_ROOT / "latest_summary.json"


def _paper30_latest_candidates_path() -> Path:
    return PAPER30_ROOT / "latest_candidates.json"


def _paper30_latest_results_path() -> Path:
    return PAPER30_ROOT / "latest_results.json"


def _paper30_latest_recommendations_path() -> Path:
    return PAPER30_ROOT / "latest_recommendations.txt"


def _profit_engine_latest_summary_path() -> Path:
    return PROFIT_ENGINE_ROOT / "latest_summary.json"


def _profit_engine_latest_candidates_path() -> Path:
    return PROFIT_ENGINE_ROOT / "latest_candidates.json"


def _profit_engine_latest_results_path() -> Path:
    return PROFIT_ENGINE_ROOT / "latest_results.csv"


def _profit_engine_latest_family_path() -> Path:
    return PROFIT_ENGINE_ROOT / "latest_family_rankings.json"


def _profit_engine_latest_rules_path() -> Path:
    return PROFIT_ENGINE_ROOT / "latest_rule_profitability.json"


def _load_latest_replay_issue() -> Dict[str, Any]:
    candidates = [
        SIMULATION_LATEST_ISSUE_FILE,
        SIMULATION_ISSUES_ROOT / "latest_issue.json",
        Path("/tmp") / "kalshi-bot" / "runtime" / "simulation" / "latest_issue.json",
        Path("/tmp") / "kalshi-bot" / "runtime" / "simulation" / "issues" / "latest_issue.json",
    ]
    for path in candidates:
        try:
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
        except Exception:
            continue
    return {}


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            try:
                stream.write(data)
            except Exception:
                pass
        return len(data)

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


class _DedupingTee:
    def __init__(
        self,
        *streams,
        max_exact_repeats: int = 3,
        always_allow_prefixes: Optional[Tuple[str, ...]] = None,
    ):
        self.streams = streams
        self.max_exact_repeats = max(1, int(max_exact_repeats or 1))
        self.always_allow_prefixes = tuple(always_allow_prefixes or ())
        self._buffer = ""
        self._line_counts: Dict[str, int] = {}

    def _emit(self, data: str) -> None:
        if not data:
            return
        if any(data.startswith(prefix) for prefix in self.always_allow_prefixes):
            for stream in self.streams:
                try:
                    stream.write(data)
                except Exception:
                    pass
            return
        count = self._line_counts.get(data, 0) + 1
        self._line_counts[data] = count
        if count > self.max_exact_repeats:
            return
        for stream in self.streams:
            try:
                stream.write(data)
            except Exception:
                pass

    def write(self, data):
        if not data:
            return 0
        self._buffer += data
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line + "\n")
        return len(data)

    def flush(self):
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


def _serialize_quote(q: Any) -> Dict[str, Any]:
    if q is None:
        return {}
    fields = [
        "ticker",
        "family",
        "status",
        "tradeable",
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "yes_bid_dollars",
        "yes_ask_dollars",
        "no_bid_dollars",
        "no_ask_dollars",
        "selected_side",
        "side",
        "selected_price",
        "selected_price_dollars",
        "selected_price_cents",
        "selected_price_source",
        "selected_price_source_used",
        "quote_ready",
        "quote_missing",
        "quote_ready_reason",
        "source_of_quote_data",
        "orderbook_ready",
        "orderbook_missing_reason",
        "spread_bucket",
        "spread_classification",
        "spread_severity",
        "final_submit_allowed_by_realm_vv",
        "decision",
        "entry_ready",
        "payload_ready",
        "auth_ready",
        "price_bounds_pass",
        "selected_side_has_price",
        "selected_side_has_liquidity",
        "depth_available",
        "canonical_execution_state",
        "canonical_tradeability",
    ]
    payload: Dict[str, Any] = {}
    for field in fields:
        value = getattr(q, field, None)
        if value is not None:
            payload[field] = value
    try:
        payload.update({k: v for k, v in vars(q).items() if v not in (None, "", [], {}) and k not in payload})
    except Exception:
        pass
    return payload


def _normalize_canonical_state(candidate: Any) -> Dict[str, Any]:
    if not isinstance(candidate, dict):
        return {}
    canonical = candidate.get("canonical_execution_state")
    if isinstance(canonical, dict) and canonical:
        return dict(canonical)
    canonical_tradeability = candidate.get("canonical_tradeability")
    if isinstance(canonical_tradeability, dict) and canonical_tradeability:
        mapped = dict(canonical_tradeability)
        return {
            "canonical_final_submit_allowed_by_realm_vv": bool(
                mapped.get("canonical_final_submit_allowed_by_realm_vv")
                if mapped.get("canonical_final_submit_allowed_by_realm_vv") is not None
                else mapped.get("final_submit_allowed_by_realm_vv")
                if mapped.get("final_submit_allowed_by_realm_vv") is not None
                else mapped.get("canonical_final_submit_allowed")
                if mapped.get("canonical_final_submit_allowed") is not None
                else mapped.get("final_submit_allowed")
                if mapped.get("final_submit_allowed") is not None
                else False
            ),
            "canonical_final_submit_allowed": bool(
                mapped.get("canonical_final_submit_allowed")
                if mapped.get("canonical_final_submit_allowed") is not None
                else mapped.get("final_submit_allowed")
                if mapped.get("final_submit_allowed") is not None
                else mapped.get("canonical_final_submit_allowed_by_realm_vv")
                if mapped.get("canonical_final_submit_allowed_by_realm_vv") is not None
                else mapped.get("final_submit_allowed_by_realm_vv")
                if mapped.get("final_submit_allowed_by_realm_vv") is not None
                else False
            ),
            "final_submit_allowed": bool(
                mapped.get("final_submit_allowed")
                if mapped.get("final_submit_allowed") is not None
                else mapped.get("canonical_final_submit_allowed")
                if mapped.get("canonical_final_submit_allowed") is not None
                else mapped.get("canonical_final_submit_allowed_by_realm_vv")
                if mapped.get("canonical_final_submit_allowed_by_realm_vv") is not None
                else mapped.get("final_submit_allowed_by_realm_vv")
                if mapped.get("final_submit_allowed_by_realm_vv") is not None
                else False
            ),
            "canonical_near_miss_recoverable": bool(mapped.get("canonical_near_miss_recoverable")),
            "canonical_selected_side_has_price": bool(
                mapped.get("canonical_selected_side_has_price")
                if mapped.get("canonical_selected_side_has_price") is not None
                else mapped.get("selected_side_has_price")
                if mapped.get("selected_side_has_price") is not None
                else False
            ),
            "canonical_selected_side_has_liquidity": bool(
                mapped.get("canonical_selected_side_has_liquidity")
                if mapped.get("canonical_selected_side_has_liquidity") is not None
                else mapped.get("selected_side_has_liquidity")
                if mapped.get("selected_side_has_liquidity") is not None
                else False
            ),
            "canonical_selected_price_cents": mapped.get("canonical_selected_price_cents", mapped.get("selected_price_cents")),
            "canonical_selected_price_dollars": mapped.get("canonical_selected_price_dollars", mapped.get("selected_price_dollars", mapped.get("selected_price"))),
            "canonical_price_bounds_pass": bool(
                mapped.get("canonical_price_bounds_pass")
                if mapped.get("canonical_price_bounds_pass") is not None
                else mapped.get("price_bounds_pass")
                if mapped.get("price_bounds_pass") is not None
                else False
            ),
            "canonical_execution_decision": mapped.get("canonical_execution_decision", mapped.get("decision")),
            "canonical_spread_classification": mapped.get("canonical_spread_classification", mapped.get("spread_classification")),
            "canonical_spread_severity": mapped.get("canonical_spread_severity", mapped.get("spread_severity")),
            "canonical_unsafe_price_status": mapped.get("canonical_unsafe_price_status", mapped.get("unsafe_price_status")),
            "trace_id": mapped.get("trace_id"),
            "relaxed_beast_mode": bool(mapped.get("relaxed_beast_mode", True)),
        }
    return {}


def _classify_trace_line(line: str) -> str:
    lowered = line.lower()
    if any(token in lowered for token in ("aion-hard-blocker-confirmed", "selected price <= 0", "selected price >= 100", "malformed payload", "duplicate live position", "auth failure", "kalshi api rejection", "corrupt orderbook", "dead orderbook")):
        return "hard_blocker"
    if any(token in lowered for token in ("aion-simulation-soft-warning", "quote_missing", "quote_not_usable", "orderbook_not_usable", "unsafe_spread", "unsafe_price", "spread_above_threshold", "edge_below_threshold", "base_sports_threshold", "phase55", "placeholder_phase55_record", "selected_price_above_0_99")):
        return "soft_warning"
    if any(token in lowered for token in ("all_guards_passed", "entry_ready", "tradeable", "eligible", "liquidity_soft_pass", "targeted_any_size_present", "recent_volume+priority_sports_family")):
        return "positive_state"
    if "telemetry" in lowered or "compatibility" in lowered or "alias" in lowered:
        return "telemetry_only"
    return "unknown"


def _parse_tagged_events(stdout_text: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in (stdout_text or "").splitlines():
        if not line.startswith("[") or "] " not in line:
            continue
        tag, payload_text = line[1:].split("] ", 1)
        payload = _safe_json(payload_text)
        events.append(
            {
                "tag": tag,
                "classification": _classify_trace_line(line),
                "payload": payload if payload is not None else payload_text,
                "raw": line,
            }
        )
    return events


def _read_recent_journal_text() -> str:
    fallback_paths = [
        Path(os.environ.get("AION_SIMULATION_JOURNAL_FALLBACK", "")).expanduser().resolve()
        if os.environ.get("AION_SIMULATION_JOURNAL_FALLBACK")
        else None,
        Path("/tmp/latest_live_execution_trace.txt"),
    ]
    for fallback_path in fallback_paths:
        if fallback_path and fallback_path.exists():
            try:
                text = fallback_path.read_text(encoding="utf-8")
                if any(
                    tag in text
                    for tag in (
                        "[AION-ORDERBOOK-PAYLOAD-TRACE]",
                        "[AION-CANONICAL-TRADEABILITY]",
                        "[AION-ORDERBOOK-USABILITY]",
                        "[AION-REALM-VV-ENTRY]",
                        "[AION-REALM-VV-BUY]",
                    )
                ):
                    return text
            except Exception:
                pass
    cmd = [
        "sudo",
        "journalctl",
        "-u",
        "kalshi-bot.service",
        "--since",
        "15 minutes ago",
        "--no-pager",
        "-n",
        "4000",
    ]
    try:
        proc = __import__("subprocess").run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            return proc.stdout or ""
        return (proc.stdout or "") + "\n" + (proc.stderr or "")
    except Exception:
        return ""


def _load_latest_frozen_candidate_from_journal() -> Tuple[Optional[Any], Dict[str, Any], Optional[str], Dict[str, Any]]:
    journal_text = _read_recent_journal_text()
    selected_payload = None
    for line in reversed(journal_text.splitlines()):
        if "[AION-ORDERBOOK-PAYLOAD-TRACE] " not in line:
            continue
        payload = _safe_json(line.split("] ", 1)[1])
        if not isinstance(payload, dict):
            continue
        canonical = payload.get("canonical_execution_state")
        canonical = canonical if isinstance(canonical, dict) else {}
        if not bool(canonical.get("canonical_final_submit_allowed_by_realm_vv")):
            continue
        if not bool(canonical.get("canonical_selected_side_has_price")):
            continue
        if int(canonical.get("canonical_selected_price_cents") or payload.get("selected_price_cents") or 0) not in range(1, 100):
            continue
        selected_payload = payload
        break
    if not isinstance(selected_payload, dict):
        for line in reversed(journal_text.splitlines()):
            if "[AION-CANONICAL-TRADEABILITY] " not in line:
                continue
            payload = _safe_json(line.split("] ", 1)[1])
            if not isinstance(payload, dict):
                continue
            if not bool(payload.get("selected_side_has_price")):
                continue
            selected_price_cents = payload.get("selected_price_cents")
            try:
                selected_price_cents = int(round(float(selected_price_cents))) if selected_price_cents not in (None, "", []) else None
            except Exception:
                selected_price_cents = None
            if selected_price_cents not in range(1, 100):
                continue
            selected_price = payload.get("selected_price_dollars", payload.get("selected_price"))
            selected_price = None if selected_price in (None, "", []) else selected_price
            selected_edge = None
            for candidate_edge in (
                payload.get("edge_after_cost"),
                payload.get("final_edge_value"),
                payload.get("final_edge"),
                payload.get("edge"),
            ):
                try:
                    candidate_edge_value = float(candidate_edge)
                except Exception:
                    candidate_edge_value = None
                if candidate_edge_value is not None and candidate_edge_value > 0.0:
                    selected_edge = candidate_edge_value
                    break
            final_submit_allowed = bool(payload.get("final_submit_allowed_by_realm_vv"))
            decision = str(payload.get("decision") or "").strip().upper()
            spread_classification = str(payload.get("spread_classification") or "").strip().upper()
            near_miss_recoverable = bool(
                not final_submit_allowed
                and selected_edge is not None
                and selected_edge > 0.0
                and spread_classification in {"CAUTION_WAIT", "AGGRESSIVE_BUT_TRADEABLE"}
                and decision in {"", "WAIT", "SKIP"}
            )
            if not final_submit_allowed and not near_miss_recoverable and decision not in {"BUY", "LIVE_PROMOTION"}:
                continue
            canonical_final_submit_allowed = bool(payload.get("final_submit_allowed_by_realm_vv") or near_miss_recoverable or decision == "BUY")
            selected_payload = {
                "ticker": payload.get("ticker"),
                "family": payload.get("family"),
                "selected_side": payload.get("selected_side"),
                "side": payload.get("selected_side") or payload.get("side"),
                "selected_price": selected_price,
                "selected_price_dollars": selected_price,
                "selected_price_cents": selected_price_cents,
                "yes_bid": payload.get("yes_bid"),
                "yes_ask": payload.get("yes_ask"),
                "no_bid": payload.get("no_bid"),
                "no_ask": payload.get("no_ask"),
                "yes_bid_dollars": payload.get("yes_bid_dollars", payload.get("yes_bid")),
                "yes_ask_dollars": payload.get("yes_ask_dollars", payload.get("yes_ask")),
                "no_bid_dollars": payload.get("no_bid_dollars", payload.get("no_bid")),
                "no_ask_dollars": payload.get("no_ask_dollars", payload.get("no_ask")),
                "orderbook_ready": bool(payload.get("orderbook_ready", True)),
                "quote_ready": bool(payload.get("quote_ready", True)),
                "quote_missing": bool(payload.get("quote_missing", False)),
                "quote_ready_reason": payload.get("unsafe_resolution_reason") or payload.get("blocker_detail") or "canonical_tradeability",
                "source_of_quote_data": payload.get("source") or "canonical_tradeability",
                "spread_bucket": payload.get("spread_bucket"),
                "spread_classification": payload.get("spread_classification"),
                "spread_severity": payload.get("spread_severity"),
                "final_submit_allowed_by_realm_vv": canonical_final_submit_allowed,
                "final_submit_allowed": canonical_final_submit_allowed,
                "decision": payload.get("decision") or ("BUY" if near_miss_recoverable else "BUY"),
                "entry_ready": canonical_final_submit_allowed,
                "payload_ready": bool(payload.get("orderbook_ready", True)),
                "auth_ready": True,
                "price_bounds_pass": bool(selected_price_cents is not None and 0 < int(selected_price_cents) < 100),
                "selected_side_has_price": bool(payload.get("selected_side_has_price")),
                "selected_side_has_liquidity": bool(payload.get("selected_side_has_liquidity")),
                "depth_available": bool(payload.get("depth_available")),
                "unsafe_price_status": payload.get("unsafe_price_status"),
                "trace_id": payload.get("trace_id"),
                "canonical_execution_state": {
                    "canonical_final_submit_allowed_by_realm_vv": canonical_final_submit_allowed,
                    "canonical_final_submit_allowed": canonical_final_submit_allowed,
                    "final_submit_allowed": canonical_final_submit_allowed,
                    "canonical_near_miss_recoverable": bool(near_miss_recoverable),
                    "canonical_selected_side_has_price": bool(payload.get("selected_side_has_price")),
                    "canonical_selected_side_has_liquidity": bool(payload.get("selected_side_has_liquidity")),
                    "canonical_selected_price_cents": selected_price_cents,
                    "canonical_selected_price_dollars": payload.get("selected_price_dollars", payload.get("selected_price")),
                    "canonical_price_bounds_pass": bool(selected_price_cents is not None and 0 < int(selected_price_cents) < 100),
                    "canonical_execution_decision": payload.get("decision") or ("BUY" if near_miss_recoverable else "BUY"),
                    "canonical_spread_classification": payload.get("spread_classification"),
                    "canonical_spread_severity": payload.get("spread_severity"),
                    "canonical_unsafe_price_status": payload.get("unsafe_price_status"),
                    "trace_id": payload.get("trace_id"),
                    "relaxed_beast_mode": True,
                },
            }
            break
    if not isinstance(selected_payload, dict):
        return None, {}, None, {"source": "journal_backed", "journal_found": False, "reason": "no_candidate_trace"}
    canonical = selected_payload.get("canonical_execution_state")
    canonical = canonical if isinstance(canonical, dict) else {}
    quote = SimpleNamespace(
        ticker=selected_payload.get("ticker"),
        family=selected_payload.get("family"),
        side=selected_payload.get("selected_side"),
        selected_side=selected_payload.get("selected_side"),
        selected_price=selected_payload.get("selected_price"),
        selected_price_dollars=selected_payload.get("selected_price_dollars"),
        selected_price_cents=selected_payload.get("selected_price_cents"),
        yes_bid=selected_payload.get("yes_bid"),
        yes_ask=selected_payload.get("yes_ask"),
        no_bid=selected_payload.get("no_bid"),
        no_ask=selected_payload.get("no_ask"),
        yes_bid_dollars=selected_payload.get("yes_bid_dollars"),
        yes_ask_dollars=selected_payload.get("yes_ask_dollars"),
        no_bid_dollars=selected_payload.get("no_bid_dollars"),
        no_ask_dollars=selected_payload.get("no_ask_dollars"),
        orderbook_ready=selected_payload.get("orderbook_ready"),
        quote_ready=selected_payload.get("quote_ready"),
        quote_missing=selected_payload.get("quote_missing"),
        quote_ready_reason=selected_payload.get("quote_ready_reason"),
        source_of_quote_data=selected_payload.get("quote_source"),
        spread_bucket=selected_payload.get("spread_bucket"),
        spread_classification=canonical.get("canonical_spread_classification") or selected_payload.get("spread_classification"),
        spread_severity=canonical.get("canonical_spread_severity") or selected_payload.get("spread_severity"),
        final_submit_allowed_by_realm_vv=canonical.get("canonical_final_submit_allowed_by_realm_vv"),
        decision=canonical.get("canonical_execution_decision") or selected_payload.get("decision"),
        entry_ready=canonical.get("canonical_phase55_ready"),
        payload_ready=selected_payload.get("payload_ready"),
        auth_ready=canonical.get("canonical_auth_ready"),
        price_bounds_pass=canonical.get("canonical_price_bounds_pass"),
        selected_side_has_price=canonical.get("canonical_selected_side_has_price"),
        selected_side_has_liquidity=canonical.get("canonical_selected_side_has_liquidity"),
        depth_available=canonical.get("canonical_depth_available"),
        unsafe_price_status=canonical.get("canonical_unsafe_price_status"),
        trace_id=canonical.get("trace_id") or selected_payload.get("trace_id"),
    )
    simulation_state = {
        "source": "journal_backed",
        "journal_found": True,
        "trace_id": canonical.get("trace_id") or selected_payload.get("trace_id"),
        "ticker": selected_payload.get("ticker"),
        "selected_price_cents": selected_payload.get("selected_price_cents"),
        "final_submit_allowed_by_realm_vv": bool(canonical.get("canonical_final_submit_allowed_by_realm_vv")),
        "candidate_trace": selected_payload,
    }
    return quote, selected_payload, selected_payload.get("ticker"), simulation_state


PAPER30_CANDIDATE_TAGS = {
    "AION-ORDERBOOK-PAYLOAD-TRACE",
    "AION-CANONICAL-TRADEABILITY",
    "AION-ORDERBOOK-USABILITY",
    "AION-CANONICAL-SIDE-PRICE",
    "AION-SIDE-PRICE-SELECTION-TRACE",
    "AION-PRICE-BOUNDS-TRACE",
    "AION-CANDIDATE-LIFECYCLE",
    "AION-PIPELINE-CANONICAL-READY",
    "AION-PIPELINE-TRADEABLE",
    "AION-PIPELINE-PAYLOAD-READY",
    "AION-REALM-VV-ENTRY",
    "AION-REALM-VV-BUY",
    "AION-REALM-VV-SPREAD-POLICY",
}


def _candidate_family_key(candidate: Dict[str, Any]) -> str:
    family = str(candidate.get("family") or "").strip()
    if family:
        return family
    ticker = str(candidate.get("ticker") or "").strip()
    if ticker:
        return ticker.split("-")[0]
    return "UNKNOWN"


def _candidate_category_key(candidate: Dict[str, Any]) -> str:
    return str(
        candidate.get("category")
        or candidate.get("market_type")
        or candidate.get("market_kind")
        or "unknown"
    ).strip().lower() or "unknown"


def _extract_candidate_record(tag: str, payload: Dict[str, Any], *, source: str) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    canonical = _normalize_canonical_state(payload)
    ticker = str(payload.get("ticker") or canonical.get("ticker") or "").strip() or None
    selected_side = payload.get("selected_side") or payload.get("side")
    selected_price = payload.get("selected_price")
    if selected_price in (None, "", []):
        selected_price = payload.get("selected_price_dollars")
    selected_price_cents = payload.get("selected_price_cents")
    try:
        if selected_price_cents in (None, "", []):
            if selected_price not in (None, "", []):
                selected_price_cents = int(round(float(selected_price) * 100))
        else:
            selected_price_cents = int(round(float(selected_price_cents)))
    except Exception:
        selected_price_cents = None
    quote_ready = payload.get("quote_ready")
    orderbook_ready = payload.get("orderbook_ready")
    if ticker is None and not selected_side and selected_price_cents is None:
        return None
    record = {
        "source": source,
        "source_tag": tag,
        "ticker": ticker,
        "family": payload.get("family") or _candidate_family_key(payload),
        "trace_id": payload.get("trace_id") or canonical.get("trace_id"),
        "selected_side": selected_side,
        "selected_price": selected_price if selected_price not in (None, "", []) else payload.get("selected_price_dollars"),
        "selected_price_cents": selected_price_cents,
        "selected_price_source": payload.get("selected_price_source") or payload.get("selected_price_source_used") or payload.get("source_of_quote_data"),
        "yes_bid": payload.get("yes_bid"),
        "yes_ask": payload.get("yes_ask"),
        "no_bid": payload.get("no_bid"),
        "no_ask": payload.get("no_ask"),
        "selected_side_has_price": payload.get("selected_side_has_price"),
        "selected_side_has_liquidity": payload.get("selected_side_has_liquidity"),
        "depth_available": payload.get("depth_available"),
        "quote_ready": quote_ready,
        "quote_missing": payload.get("quote_missing"),
        "orderbook_ready": orderbook_ready,
        "payload_ready": payload.get("payload_ready"),
        "final_submit_allowed": payload.get("final_submit_allowed"),
        "final_submit_allowed_by_realm_vv": payload.get("final_submit_allowed_by_realm_vv"),
        "decision": payload.get("decision"),
        "entry_ready": payload.get("entry_ready"),
        "blocker": payload.get("blocker") or payload.get("block_reason"),
        "spread_bucket": payload.get("spread_bucket"),
        "spread_classification": payload.get("spread_classification"),
        "spread_severity": payload.get("spread_severity"),
        "unsafe_price_status": payload.get("unsafe_price_status"),
        "canonical_execution_state": canonical,
        "raw_fields": payload,
    }
    record["selected_price_cents_valid"] = bool(selected_price_cents is not None and 0 < int(selected_price_cents) < 100)
    return record


def _collect_recent_candidate_pool(limit: int = 200) -> List[Dict[str, Any]]:
    journal_text = _read_recent_journal_text()
    records: List[Dict[str, Any]] = []
    seen: set = set()
    for line in journal_text.splitlines():
        if not line.startswith("[") or "] " not in line:
            continue
        tag = line[1:].split("]", 1)[0]
        if tag not in PAPER30_CANDIDATE_TAGS:
            continue
        payload = _safe_json(line.split("] ", 1)[1])
        if not isinstance(payload, dict):
            continue
        record = _extract_candidate_record(tag, payload, source="journal")
        if not record:
            continue
        dedupe_key = (
            record.get("ticker"),
            record.get("trace_id"),
            record.get("source_tag"),
            record.get("selected_side"),
            record.get("selected_price_cents"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        records.append(record)
        if len(records) >= limit:
            break
    if records:
        return records
    try:
        incident_dirs = sorted([path for path in INCIDENTS_ROOT.glob("*") if path.is_dir()], key=lambda path: path.stat().st_mtime)
    except Exception:
        incident_dirs = []
    for incident_dir in reversed(incident_dirs):
        for bundle_name in ("trace_tree.json", "simulation_state.json"):
            bundle_path = incident_dir / bundle_name
            if not bundle_path.exists():
                continue
            try:
                bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(bundle, dict):
                continue
            candidate_data = bundle.get("selected_quote") or bundle.get("candidate_trace") or bundle.get("simulation_state", {}).get("selected_quote")
            if isinstance(candidate_data, dict):
                record = _extract_candidate_record("incident_bundle", candidate_data, source="incident_bundle")
                if record:
                    records.append(record)
                    if len(records) >= limit:
                        return records
            simulation_state = bundle.get("simulation_state")
            if isinstance(simulation_state, dict):
                attempt_candidate = simulation_state.get("attempts", [])
                if isinstance(attempt_candidate, list):
                    for attempt in attempt_candidate:
                        if not isinstance(attempt, dict):
                            continue
                        frozen = attempt.get("frozen_trace") or {}
                        if not isinstance(frozen, dict):
                            continue
                        record = _extract_candidate_record("incident_bundle", frozen.get("selected_quote") or frozen.get("candidate_trace") or {}, source="incident_bundle")
                        if record:
                            records.append(record)
                            if len(records) >= limit:
                                return records
    quote, selected_payload, _, simulation_state = _load_latest_frozen_candidate_from_journal()
    if selected_payload:
        fallback_record = _extract_candidate_record("AION-ORDERBOOK-PAYLOAD-TRACE", selected_payload, source=simulation_state.get("source") or "fallback")
        if fallback_record:
            records.append(fallback_record)
    if not records and quote is not None:
        quote_payload = _serialize_quote(quote)
        fallback_record = _extract_candidate_record("AION-SIMULATION-FALLBACK", quote_payload, source="simulation_fallback")
        if fallback_record:
            records.append(fallback_record)
    return records


def _normalize_market_universe(bot: KalshiSniperBot, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        item = dict(market)
        try:
            item["family"] = bot.get_market_family(market)
        except Exception:
            item["family"] = item.get("family")
        try:
            item["category"] = bot.get_market_discovery_category(market)
        except Exception:
            item["category"] = item.get("category") or item.get("market_type") or "unknown"
        item["market_type"] = str(item.get("category") or item.get("market_type") or "unknown").strip().lower() or "unknown"
        normalized.append(item)
    return normalized


def _discover_open_market_universe(bot: KalshiSniperBot, limit: int = 250, *, force_family_diversity: bool = False) -> List[Dict[str, Any]]:
    markets: List[Dict[str, Any]] = []
    seen_tickers: set = set()
    status_priority = ["open", "active"] if force_family_diversity else ["open"]
    for status in status_priority:
        cursor = None
        page_count = 0
        while len(markets) < int(limit or 250) and page_count < 12:
            try:
                fetched, cursor = bot.get_markets(limit=max(50, min(250, int(limit or 250))), status=status, cursor=cursor, return_cursor=True)
            except Exception:
                fetched = []
                cursor = None
            normalized = _normalize_market_universe(bot, [market for market in fetched if isinstance(market, dict)])
            for market in normalized:
                ticker = str(market.get("ticker") or "").strip().upper()
                if not ticker or ticker in seen_tickers:
                    continue
                seen_tickers.add(ticker)
                markets.append(market)
                if len(markets) >= int(limit or 250):
                    break
            page_count += 1
            if not cursor or not normalized:
                break
        if len(markets) >= int(limit or 250):
            break
    if force_family_diversity and len(markets) < int(limit or 250):
        try:
            targeted_markets = bot.fetch_targeted_series_markets(scan_depth=2)
        except Exception:
            targeted_markets = []
        normalized_targeted = _normalize_market_universe(bot, [market for market in targeted_markets if isinstance(market, dict)])
        for market in normalized_targeted:
            ticker = str(market.get("ticker") or "").strip().upper()
            if not ticker or ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)
            markets.append(market)
            if len(markets) >= int(limit or 250):
                break
    if markets:
        return markets
    fallback = _collect_recent_candidate_pool(limit=max(50, int(limit or 250)))
    return _normalize_market_universe(bot, fallback)


def _market_to_candidate_record(bot: KalshiSniperBot, market: Dict[str, Any], quote: Optional[SimpleNamespace] = None) -> Dict[str, Any]:
    market = market if isinstance(market, dict) else {}
    quote = quote if quote is not None else None
    ticker = str(market.get("ticker") or getattr(quote, "ticker", "") or "").strip()
    family = str(bot.get_market_family(market) if isinstance(market, dict) else getattr(quote, "family", "") or market.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN"
    if quote is not None and ticker and not getattr(quote, "ticker", None):
        quote.ticker = ticker
    yes_bid = bot.to_float(getattr(quote, "yes_bid", None), market.get("yes_bid"))
    yes_ask = bot.to_float(getattr(quote, "yes_ask", None), market.get("yes_ask"))
    no_bid = bot.to_float(getattr(quote, "no_bid", None), market.get("no_bid"))
    no_ask = bot.to_float(getattr(quote, "no_ask", None), market.get("no_ask"))
    selected_side = str(getattr(quote, "selected_side", None) or market.get("selected_side") or market.get("side") or ("yes" if yes_ask is not None else "no" if no_ask is not None else "unknown")).strip().lower() or "unknown"
    selected_price = bot.to_float(getattr(quote, "selected_price", None), None)
    if selected_price is None:
        selected_price = bot.to_float(market.get("selected_price"), bot.to_float(market.get("selected_price_dollars"), None))
    selected_price_cents = market.get("selected_price_cents")
    try:
        if selected_price_cents in (None, "", []):
            selected_price_cents = int(round(float(selected_price) * 100.0)) if selected_price not in (None, "", []) else None
    except Exception:
        selected_price_cents = None
    return {
        "ticker": ticker,
        "family": family,
        "category": str(market.get("category") or bot.get_market_discovery_category(market) or "unknown").strip().lower() or "unknown",
        "market_type": "sports" if family.startswith("KX") else str(market.get("market_type") or market.get("market_kind") or "unknown").strip().lower() or "unknown",
        "selected_side": selected_side,
        "selected_price": selected_price,
        "selected_price_dollars": selected_price,
        "selected_price_cents": selected_price_cents,
        "selected_side_has_price": bool(selected_price not in (None, "", [])),
        "selected_side_has_liquidity": bool(bot.to_float(getattr(quote, "liquidity", None), market.get("liquidity")) or bot.to_float(getattr(quote, "volume", None), market.get("volume"))),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "quote_ready": bool(quote is not None),
        "orderbook_ready": bool(quote is not None),
        "payload_ready": bool(quote is not None),
        "final_submit_allowed_by_realm_vv": bool(selected_price is not None and 0.0 < float(selected_price) < 1.0),
        "decision": "BUY" if selected_side in {"yes", "no"} else "SKIP",
        "entry_ready": bool(selected_price is not None and 0.0 < float(selected_price) < 1.0 and selected_side in {"yes", "no"}),
        "blocker": None,
        "spread_bucket": "unknown",
        "spread_classification": "unknown",
        "spread_severity": "unknown",
        "unsafe_price_status": "unknown",
        "canonical_execution_state": {},
        "raw_fields": market,
        "source_of_quote_data": "family_discovery_market",
    }


def _candidate_to_quote(candidate: Dict[str, Any]) -> SimpleNamespace:
    canonical = candidate.get("canonical_execution_state") if isinstance(candidate, dict) else {}
    canonical = canonical if isinstance(canonical, dict) else {}
    raw = dict(candidate.get("raw_fields") or {})
    selected_side = candidate.get("selected_side") or raw.get("selected_side") or raw.get("side")
    selected_price_cents = candidate.get("selected_price_cents")
    selected_price = candidate.get("selected_price")
    if selected_price in (None, "", []):
        selected_price = raw.get("selected_price") or raw.get("selected_price_dollars")
    if selected_price_cents in (None, "", []):
        try:
            selected_price_cents = int(round(float(selected_price) * 100)) if selected_price not in (None, "", []) else None
        except Exception:
            selected_price_cents = None
    merged = {
        **raw,
        "ticker": candidate.get("ticker") or raw.get("ticker"),
        "family": candidate.get("family") or raw.get("family"),
        "selected_side": selected_side,
        "side": selected_side,
        "selected_price": selected_price,
        "selected_price_dollars": selected_price,
        "selected_price_cents": selected_price_cents,
        "yes_bid": candidate.get("yes_bid", raw.get("yes_bid")),
        "yes_ask": candidate.get("yes_ask", raw.get("yes_ask")),
        "no_bid": candidate.get("no_bid", raw.get("no_bid")),
        "no_ask": candidate.get("no_ask", raw.get("no_ask")),
        "yes_bid_dollars": candidate.get("yes_bid", raw.get("yes_bid_dollars", raw.get("yes_bid"))),
        "yes_ask_dollars": candidate.get("yes_ask", raw.get("yes_ask_dollars", raw.get("yes_ask"))),
        "no_bid_dollars": candidate.get("no_bid", raw.get("no_bid_dollars", raw.get("no_bid"))),
        "no_ask_dollars": candidate.get("no_ask", raw.get("no_ask_dollars", raw.get("no_ask"))),
        "orderbook_ready": bool(candidate.get("orderbook_ready") if candidate.get("orderbook_ready") is not None else raw.get("orderbook_ready", True)),
        "quote_ready": bool(candidate.get("quote_ready") if candidate.get("quote_ready") is not None else raw.get("quote_ready", True)),
        "quote_missing": bool(candidate.get("quote_missing") if candidate.get("quote_missing") is not None else raw.get("quote_missing", False)),
        "quote_ready_reason": raw.get("quote_ready_reason") or raw.get("unsafe_resolution_reason") or raw.get("blocker_detail") or "paper30_candidate",
        "source_of_quote_data": raw.get("source_of_quote_data") or raw.get("source") or "paper30_candidate",
        "spread_bucket": candidate.get("spread_bucket") or raw.get("spread_bucket"),
        "spread_classification": (canonical.get("canonical_spread_classification") or candidate.get("spread_classification") or raw.get("spread_classification")),
        "spread_severity": (canonical.get("canonical_spread_severity") or candidate.get("spread_severity") or raw.get("spread_severity")),
        "final_submit_allowed_by_realm_vv": bool(
            canonical.get("canonical_final_submit_allowed_by_realm_vv")
            if canonical.get("canonical_final_submit_allowed_by_realm_vv") is not None
            else candidate.get("final_submit_allowed_by_realm_vv")
            if candidate.get("final_submit_allowed_by_realm_vv") is not None
            else raw.get("final_submit_allowed_by_realm_vv", False)
        ),
        "decision": canonical.get("canonical_execution_decision") or candidate.get("decision") or raw.get("decision") or "BUY",
        "entry_ready": bool(
            canonical.get("canonical_buy_ready")
            if canonical.get("canonical_buy_ready") is not None
            else candidate.get("entry_ready")
            if candidate.get("entry_ready") is not None
            else raw.get("entry_ready", False)
        ),
        "payload_ready": bool(candidate.get("payload_ready") if candidate.get("payload_ready") is not None else raw.get("payload_ready", True)),
        "auth_ready": bool(candidate.get("auth_ready") if candidate.get("auth_ready") is not None else raw.get("auth_ready", True)),
        "price_bounds_pass": bool(
            canonical.get("canonical_price_bounds_pass")
            if canonical.get("canonical_price_bounds_pass") is not None
            else candidate.get("price_bounds_pass")
            if candidate.get("price_bounds_pass") is not None
            else raw.get("price_bounds_pass", False)
        ),
        "selected_side_has_price": bool(
            canonical.get("canonical_selected_side_has_price")
            if canonical.get("canonical_selected_side_has_price") is not None
            else candidate.get("selected_side_has_price")
            if candidate.get("selected_side_has_price") is not None
            else raw.get("selected_side_has_price", False)
        ),
        "selected_side_has_liquidity": bool(
            canonical.get("canonical_selected_side_has_liquidity")
            if canonical.get("canonical_selected_side_has_liquidity") is not None
            else candidate.get("selected_side_has_liquidity")
            if candidate.get("selected_side_has_liquidity") is not None
            else raw.get("selected_side_has_liquidity", False)
        ),
        "depth_available": bool(candidate.get("depth_available") if candidate.get("depth_available") is not None else raw.get("depth_available", False)),
        "unsafe_price_status": canonical.get("canonical_unsafe_price_status") or candidate.get("unsafe_price_status") or raw.get("unsafe_price_status"),
        "trace_id": candidate.get("trace_id") or raw.get("trace_id") or canonical.get("trace_id"),
        "canonical_execution_state": canonical,
        "raw_fields": raw,
    }
    return SimpleNamespace(**merged)


def _paper30_recover_selected_side(candidate: Dict[str, Any], quote: SimpleNamespace) -> Tuple[SimpleNamespace, Dict[str, Any], bool, Optional[str]]:
    raw = dict(candidate.get("raw_fields") or {})
    selected_side = str(getattr(quote, "selected_side", None) or getattr(quote, "side", None) or raw.get("selected_side") or raw.get("side") or "").strip().lower()
    selected_price_cents = getattr(quote, "selected_price_cents", None)
    selected_price = getattr(quote, "selected_price", None)
    selected_side_has_price = bool(getattr(quote, "selected_side_has_price", None))
    if selected_side in {"yes", "no"} and selected_price_cents not in (None, "", []) and 0 < int(selected_price_cents) < 100 and selected_side_has_price:
        return quote, candidate, False, None
    yes_ask = raw.get("yes_ask", getattr(quote, "yes_ask", None))
    no_ask = raw.get("no_ask", getattr(quote, "no_ask", None))
    yes_bid = raw.get("yes_bid", getattr(quote, "yes_bid", None))
    no_bid = raw.get("no_bid", getattr(quote, "no_bid", None))
    executable_prices = {
        "yes": yes_ask,
        "no": no_ask,
    }
    chosen_side = None
    chosen_price = None
    for side in (selected_side, "yes" if selected_side == "no" else "no"):
        price = executable_prices.get(side)
        if price in (None, "", []):
            continue
        try:
            price_value = float(price)
        except Exception:
            continue
        price_cents = int(round(price_value * 100))
        if 0 < price_cents < 100:
            chosen_side = side
            chosen_price = price_value
            selected_price_cents = price_cents
            break
    if chosen_side is None:
        return quote, candidate, False, None
    recovered = SimpleNamespace(**{**vars(quote)})
    recovered.selected_side = chosen_side
    recovered.side = chosen_side
    recovered.selected_price = chosen_price
    recovered.selected_price_dollars = chosen_price
    recovered.selected_price_cents = selected_price_cents
    recovered.selected_side_has_price = True
    recovered.selected_side_has_liquidity = bool(getattr(quote, "selected_side_has_liquidity", True))
    recovered.price_bounds_pass = True
    recovered.quote_ready = True
    recovered.quote_missing = False
    recovered.orderbook_ready = bool(getattr(quote, "orderbook_ready", True))
    recovered.selected_price_source = "side_recovery"
    recovered.selected_price_source_used = "side_recovery"
    recovered.final_submit_allowed_by_realm_vv = True if bool(getattr(quote, "final_submit_allowed_by_realm_vv", False)) or bool(candidate.get("final_submit_allowed_by_realm_vv")) else getattr(quote, "final_submit_allowed_by_realm_vv", False)
    recovered.decision = "BUY" if str(DEFAULT_LIVE_ORDER_ACTION or "").strip().lower() == "buy" else getattr(recovered, "decision", "BUY")
    candidate = dict(candidate)
    candidate["selected_side"] = chosen_side
    candidate["selected_price"] = chosen_price
    candidate["selected_price_cents"] = selected_price_cents
    candidate["selected_price_cents_valid"] = True
    candidate["selected_side_has_price"] = True
    candidate["selected_side_has_liquidity"] = bool(getattr(quote, "selected_side_has_liquidity", True))
    candidate["quote_ready"] = True
    candidate["quote_missing"] = False
    candidate["orderbook_ready"] = bool(getattr(quote, "orderbook_ready", True))
    candidate["price_bounds_pass"] = True
    return recovered, candidate, True, chosen_side


def _collect_blocker_samples(stdout_text: str) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    for line in (stdout_text or "").splitlines():
        if not line.startswith("["):
            continue
        lowered = line.lower()
        if not any(token in lowered for token in HARD_BLOCKER_TOKENS.union(SOFT_WARNING_TOKENS)):
            continue
        tag = line[1:].split("]", 1)[0]
        payload = _safe_json(line.split("] ", 1)[1]) if "] " in line else None
        samples.append(
            {
                "tag": tag,
                "classification": _classify_trace_line(line),
                "payload": payload if payload is not None else {"raw": line},
                "raw": line,
            }
        )
    return samples


def _extract_selected_candidate(bot: KalshiSniperBot) -> Tuple[Optional[Any], Dict[str, Any], Optional[str]]:
    selected_quote = getattr(bot, "last_runonce_reachability_selected_quote", None)
    summary = dict(getattr(bot, "last_live_select_summary", {}) or {})
    ticker = None
    if selected_quote is not None:
        ticker = str(getattr(selected_quote, "ticker", "") or "").strip() or None
    if not ticker:
        ticker = (
            summary.get("best_candidate_ticker")
            or summary.get("best_ticker")
            or summary.get("selected_ticker")
            or summary.get("ticker")
        )
        ticker = str(ticker or "").strip() or None
    return selected_quote, summary, ticker


def _simulate_submission(bot: KalshiSniperBot, *, selected_quote: Any, payload_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = dict(payload_override) if isinstance(payload_override, dict) else {}
    ticker = str(getattr(selected_quote, "ticker", "") or "").strip() or None
    side = payload.get("side") or getattr(selected_quote, "selected_side", None) or getattr(selected_quote, "side", None)
    if payload and not payload.get("count"):
        payload["count"] = int(AION_AGGRESSIVE_MAX_CONTRACTS)
    event = {
        "ticker": ticker,
        "side": side,
        "action": DEFAULT_LIVE_ORDER_ACTION,
        "count": int(payload.get("count") or AION_AGGRESSIVE_MAX_CONTRACTS),
        "price": payload.get("yes_price") if payload.get("yes_price") is not None else payload.get("no_price"),
        "submitted": True,
        "simulated": True,
    }
    print("[LIVE-EXECUTION-ORDER-SUBMITTED] " + json.dumps(event, sort_keys=True, default=str))
    return {
        "ok": True,
        "status_code": 200,
        "response": {"simulated": True, "ticker": ticker, "side": side},
        "reason": "submitted",
        "submitted": True,
        "payload": payload,
    }


def _build_frozen_trace(bot: KalshiSniperBot, selected_quote: Any, ticker: Optional[str]) -> Dict[str, Any]:
    quote_snapshot = _serialize_quote(selected_quote)
    family = bot.get_market_family({"ticker": ticker}) if ticker else "UNKNOWN"
    quote_diag = bot._diagnose_quote_readiness_context(
        market={"ticker": ticker} if ticker else {},
        q=selected_quote,
        family=family,
        sports_context={},
        source="simulation_frozen",
    ) if selected_quote is not None else {}
    payload_preflight = bot._diagnose_live_order_payload_context(
        selected_quote,
        size=AION_AGGRESSIVE_MAX_CONTRACTS,
        price_source="ask" if str(DEFAULT_LIVE_ORDER_ACTION or "").strip().lower() == "buy" else "bid",
        selected_ticker=ticker,
        selection_stage="simulation_final_gate",
        market={"ticker": ticker} if ticker else {},
        phase55_gate_record=bot._get_phase55_live_execution_gate_record(ticker, stage="simulation_final_gate") if ticker else {},
        quote_diag=quote_diag,
    ) if selected_quote is not None else {}
    payload = payload_preflight.get("payload") if isinstance(payload_preflight, dict) else None
    canonical = payload_preflight.get("canonical_execution_state") if isinstance(payload_preflight, dict) else {}
    canonical = canonical if isinstance(canonical, dict) else {}
    if not canonical:
        canonical = _normalize_canonical_state(quote_diag)
    if not canonical:
        canonical = _normalize_canonical_state(quote_snapshot)
    if not canonical and isinstance(payload_preflight, dict):
        canonical = _normalize_canonical_state(payload_preflight)
    if canonical and not canonical.get("canonical_final_submit_allowed_by_realm_vv") and quote_snapshot.get("final_submit_allowed_by_realm_vv") is not None:
        canonical["canonical_final_submit_allowed_by_realm_vv"] = bool(quote_snapshot.get("final_submit_allowed_by_realm_vv"))
    if canonical and canonical.get("canonical_execution_decision") is None and quote_snapshot.get("decision") is not None:
        canonical["canonical_execution_decision"] = quote_snapshot.get("decision")
    selected_side_has_price = bool(
        canonical.get("canonical_selected_side_has_price")
        if canonical.get("canonical_selected_side_has_price") is not None
        else payload_preflight.get("selected_side_has_price")
        if payload_preflight.get("selected_side_has_price") is not None
        else quote_snapshot.get("selected_side_has_price")
    )
    selected_side_has_liquidity = bool(
        canonical.get("canonical_selected_side_has_liquidity")
        if canonical.get("canonical_selected_side_has_liquidity") is not None
        else payload_preflight.get("selected_side_has_liquidity")
        if payload_preflight.get("selected_side_has_liquidity") is not None
        else quote_snapshot.get("selected_side_has_liquidity")
    )
    price_bounds_pass = bool(
        canonical.get("canonical_price_bounds_pass")
        if canonical.get("canonical_price_bounds_pass") is not None
        else payload_preflight.get("price_bounds_pass")
        if payload_preflight.get("price_bounds_pass") is not None
        else quote_snapshot.get("price_bounds_pass")
    )
    selected_price_cents = canonical.get("canonical_selected_price_cents")
    if selected_price_cents is None:
        selected_price_cents = payload_preflight.get("selected_price_cents")
    if selected_price_cents is None:
        selected_price_cents = quote_snapshot.get("selected_price_cents")
    decision = str(
        canonical.get("canonical_execution_decision")
        or payload_preflight.get("decision")
        or quote_snapshot.get("decision")
        or "SKIP"
    ).strip().upper() or "SKIP"
    entry_ready = bool(
        canonical.get("canonical_buy_ready")
        if canonical.get("canonical_buy_ready") is not None
        else payload_preflight.get("entry_ready")
        if payload_preflight.get("entry_ready") is not None
        else quote_snapshot.get("entry_ready")
    )
    relaxed_near_miss = bool(
        decision in {"BUY", "LIVE_PROMOTION"}
        and entry_ready
        and selected_side_has_price
        and price_bounds_pass
        and (selected_side_has_liquidity or bool(payload_preflight.get("orderbook_ready")) or bool(quote_snapshot.get("orderbook_ready")))
    )
    blocker_reason = canonical.get("canonical_final_block_reason") or payload_preflight.get("block_reason") or quote_diag.get("block_reason")
    hard_block_reason = blocker_reason if blocker_reason not in (None, "", "all_guards_passed", "unknown") else None
    final_submit_allowed_source = (
        canonical.get("canonical_final_submit_allowed_by_realm_vv")
        if canonical.get("canonical_final_submit_allowed_by_realm_vv") is not None
        else canonical.get("canonical_final_submit_allowed")
        if canonical.get("canonical_final_submit_allowed") is not None
        else canonical.get("final_submit_allowed")
        if canonical.get("final_submit_allowed") is not None
        else payload_preflight.get("final_submit_allowed_by_realm_vv")
        if payload_preflight.get("final_submit_allowed_by_realm_vv") is not None
        else payload_preflight.get("final_submit_allowed")
        if payload_preflight.get("final_submit_allowed") is not None
        else quote_snapshot.get("final_submit_allowed")
        if quote_snapshot.get("final_submit_allowed") is not None
        else quote_snapshot.get("final_submit_allowed_by_realm_vv")
        if quote_snapshot.get("final_submit_allowed_by_realm_vv") is not None
        else None
    )
    final_submit_allowed = bool(final_submit_allowed_source or relaxed_near_miss)
    return {
        "ticker": ticker,
        "family": family,
        "selected_quote": quote_snapshot,
        "quote_diag": quote_diag,
        "payload_preflight": payload_preflight,
        "payload": payload,
        "canonical_execution_state": canonical,
        "hard_block_reason": hard_block_reason,
        "final_submit_allowed": final_submit_allowed,
        "selected_side_exists": bool(quote_snapshot.get("selected_side") or quote_snapshot.get("side")),
        "selected_price_cents_valid": bool(
            selected_price_cents is not None
            and 0 < int(float(selected_price_cents)) < 100
        ) if selected_price_cents is not None else False,
        "selected_side_has_price": selected_side_has_price,
        "selected_side_has_liquidity": selected_side_has_liquidity,
        "price_bounds_pass": price_bounds_pass,
        "payload_ready": bool(payload_preflight.get("payload_ready") if payload_preflight.get("payload_ready") is not None else quote_snapshot.get("payload_ready") if quote_snapshot.get("payload_ready") is not None else True),
        "orderbook_ready": bool(payload_preflight.get("orderbook_ready") if payload_preflight.get("orderbook_ready") is not None else quote_snapshot.get("orderbook_ready") if quote_snapshot.get("orderbook_ready") is not None else True),
        "quote_ready": bool(payload_preflight.get("quote_ready") if payload_preflight.get("quote_ready") is not None else quote_snapshot.get("quote_ready") if quote_snapshot.get("quote_ready") is not None else True),
        "quote_missing": bool(payload_preflight.get("quote_missing") if payload_preflight.get("quote_missing") is not None else quote_snapshot.get("quote_missing") if quote_snapshot.get("quote_missing") is not None else False),
    }


def _write_trace_bundle(incident_dir: Path, *, stdout_text: str, stderr_text: str, selected_quote: Any, selected_ticker: Optional[str], simulation_state: Dict[str, Any]) -> None:
    events = _parse_tagged_events(stdout_text)
    blocker_samples = _collect_blocker_samples(stdout_text)
    trace_tree = {
        "timestamp": _now_iso(),
        "selected_ticker": selected_ticker,
        "selected_quote": _serialize_quote(selected_quote),
        "simulation_state": simulation_state,
        "events": events,
        "blocker_samples": blocker_samples,
        "summary": {
            "final_submit_preflight_seen": any(event["tag"] == "AION-FINAL-SUBMIT-PREFLIGHT" for event in events),
            "final_submit_call_seen": any(event["tag"] == "AION-FINAL-SUBMIT-CALL" for event in events),
            "order_submitted_seen": any(event["tag"] == "LIVE-EXECUTION-ORDER-SUBMITTED" for event in events),
            "hard_blockers": [sample for sample in blocker_samples if sample["classification"] == "hard_blocker"],
            "soft_warnings": [sample for sample in blocker_samples if sample["classification"] == "soft_warning"],
        },
    }
    safe_atomic_json_write(incident_dir / "trace_tree.json", trace_tree, sort_keys=True, indent=2)
    safe_atomic_json_write(incident_dir / "simulation_state.json", simulation_state, sort_keys=True, indent=2)
    (incident_dir / "stdout.log").write_text(stdout_text, encoding="utf-8")
    (incident_dir / "stderr.log").write_text(stderr_text, encoding="utf-8")
    (incident_dir / "candidate.json").write_text(json.dumps(_serialize_quote(selected_quote), sort_keys=True, default=str, indent=2), encoding="utf-8")


def _paper30_strategy_metrics(candidate: Dict[str, Any], frozen_trace: Dict[str, Any]) -> Dict[str, Any]:
    quote = frozen_trace.get("selected_quote") if isinstance(frozen_trace, dict) else {}
    quote = quote if isinstance(quote, dict) else {}
    canonical = frozen_trace.get("canonical_execution_state") if isinstance(frozen_trace, dict) else {}
    canonical = canonical if isinstance(canonical, dict) else {}
    blocker = str(frozen_trace.get("hard_block_reason") or quote.get("blocker") or quote.get("block_reason") or "").strip()
    selected_price_cents = candidate.get("selected_price_cents")
    if selected_price_cents in (None, "", []):
        selected_price_cents = frozen_trace.get("selected_price_cents") or quote.get("selected_price_cents")
    try:
        selected_price_cents = int(round(float(selected_price_cents))) if selected_price_cents not in (None, "", []) else None
    except Exception:
        selected_price_cents = None
    selected_price = candidate.get("selected_price")
    if selected_price in (None, "", []):
        selected_price = frozen_trace.get("selected_price") or quote.get("selected_price")
    try:
        selected_price = float(selected_price) if selected_price not in (None, "", []) else None
    except Exception:
        selected_price = None
    spread_classification = str(canonical.get("canonical_spread_classification") or quote.get("spread_classification") or candidate.get("spread_classification") or "unknown").upper()
    spread_severity = str(canonical.get("canonical_spread_severity") or quote.get("spread_severity") or candidate.get("spread_severity") or "unknown").lower()
    selected_side_has_price = bool(frozen_trace.get("selected_side_has_price"))
    selected_side_has_liquidity = bool(frozen_trace.get("selected_side_has_liquidity"))
    price_bounds_pass = bool(frozen_trace.get("price_bounds_pass"))
    final_submit_allowed = bool(frozen_trace.get("final_submit_allowed"))
    quote_ready = bool(frozen_trace.get("quote_ready"))
    orderbook_ready = bool(frozen_trace.get("orderbook_ready"))
    depth_available = bool(candidate.get("depth_available") if candidate.get("depth_available") is not None else quote.get("depth_available"))
    confidence_score = 0.35
    if final_submit_allowed:
        confidence_score += 0.22
    if selected_side_has_price:
        confidence_score += 0.15
    if selected_side_has_liquidity:
        confidence_score += 0.1
    if price_bounds_pass:
        confidence_score += 0.08
    if quote_ready and orderbook_ready:
        confidence_score += 0.05
    if "CAUTION_WAIT" in spread_classification:
        confidence_score += 0.05
    if "AGGRESSIVE_BUT_TRADEABLE" in spread_classification:
        confidence_score += 0.1
    if "UNSAFE" in spread_classification:
        confidence_score -= 0.12
    if "catastrophic" in spread_severity:
        confidence_score -= 0.18
    if not depth_available:
        confidence_score -= 0.08
    confidence_score = max(0.0, min(1.0, confidence_score))
    edge_after_cost = canonical.get("canonical_final_edge_value")
    if edge_after_cost in (None, "", []):
        edge_after_cost = canonical.get("canonical_edge_after_cost")
    if edge_after_cost in (None, "", []):
        edge_after_cost = candidate.get("edge_after_cost")
    if edge_after_cost in (None, "", []):
        edge_after_cost = 0.0
    try:
        edge_after_cost = float(edge_after_cost)
    except Exception:
        edge_after_cost = 0.0
    spread_penalty = 0.0
    if "UNSAFE" in spread_classification:
        spread_penalty += 0.18
    if "catastrophic" in spread_severity:
        spread_penalty += 0.12
    if "WIDE" in spread_classification:
        spread_penalty += 0.05
    liquidity_penalty = 0.0 if selected_side_has_liquidity else 0.12
    volatility_penalty = 0.0 if selected_side_has_price and price_bounds_pass else 0.18
    family_quality_adjustment = 0.05 if str(candidate.get("family") or "").startswith("KX") else 0.0
    penalties_total = round(spread_penalty + liquidity_penalty + volatility_penalty - family_quality_adjustment, 6)
    simulated_size = max(1, min(int(AION_AGGRESSIVE_MAX_CONTRACTS), int(round(AION_AGGRESSIVE_MAX_CONTRACTS * max(0.1, confidence_score)))))
    simulated_entry_price = selected_price if selected_price is not None else (selected_price_cents / 100.0 if selected_price_cents is not None else None)
    simulated_exit_policy = "fast_exit" if confidence_score < 0.55 or spread_penalty > 0.15 else "managed_exit"
    simulated_expected_profit = round((edge_after_cost * simulated_size) - (penalties_total * simulated_size), 6)
    simulated_expected_loss = round(max(0.0, penalties_total - edge_after_cost) * simulated_size, 6)
    risk_reward = round((edge_after_cost + 1e-9) / (penalties_total + 1e-9), 4)
    strict_would_reject = bool(
        blocker
        or not final_submit_allowed
        or not selected_side_has_price
        or not selected_price_cents
        or selected_price_cents <= 0
        or selected_price_cents >= 100
        or str(quote.get("quote_missing")).lower() == "true"
        or str(quote.get("orderbook_ready")).lower() == "false" and not selected_side_has_price
    )
    if not strict_would_reject and any(token in str(candidate.get("blocker") or "").lower() for token in ("unsafe_spread", "unsafe_price", "quote_not_usable", "orderbook_not_usable", "spread_above_threshold", "edge_below_threshold", "base_sports_threshold")):
        strict_would_reject = True
    if not final_submit_allowed and selected_side_has_price and selected_price_cents and 0 < int(selected_price_cents) < 100:
        strict_would_reject = True
    selected_side = str(frozen_trace.get("selected_quote", {}).get("selected_side") or frozen_trace.get("selected_quote", {}).get("side") or candidate.get("selected_side") or "").strip().lower() or "unknown"
    relaxed_live_result = bool(
        not bool(frozen_trace.get("hard_block_reason"))
        and selected_side in {"yes", "no"}
        and selected_side_has_price
        and selected_price_cents is not None
        and 0 < int(selected_price_cents) < 100
        and final_submit_allowed
    )
    return {
        "confidence_score": confidence_score,
        "expected_edge_after_cost": edge_after_cost,
        "spread_penalty": round(spread_penalty, 6),
        "liquidity_penalty": round(liquidity_penalty, 6),
        "volatility_penalty": round(volatility_penalty, 6),
        "family_quality_adjustment": round(family_quality_adjustment, 6),
        "simulated_size": simulated_size,
        "simulated_entry_price": simulated_entry_price,
        "simulated_exit_policy": simulated_exit_policy,
        "simulated_expected_profit": simulated_expected_profit,
        "simulated_expected_loss": simulated_expected_loss,
        "simulated_risk_reward": risk_reward,
        "expected_value": round(simulated_expected_profit - simulated_expected_loss, 6),
        "strict_would_reject": strict_would_reject,
        "strict_live_would_reject": strict_would_reject,
        "strict_live_result": "REJECT" if strict_would_reject else "ALLOW",
        "relaxed_live_result": "EXECUTE" if relaxed_live_result else "NO_EXECUTE",
        "soft_penalties": [token for token in (
            "unsafe_spread" if "UNSAFE" in spread_classification else None,
            "unsafe_price" if "unsafe" in str(candidate.get("unsafe_price_status") or "").lower() else None,
            "spread_above_threshold" if "UNSAFE" in spread_classification else None,
            "edge_below_threshold" if edge_after_cost <= 0 else None,
            "base_sports_threshold" if str(candidate.get("blocker") or "").startswith("base_sports") else None,
            "quote_not_usable" if not quote_ready else None,
            "orderbook_not_usable" if not orderbook_ready else None,
        ) if token],
        "hard_block": bool(
            (blocker and any(token in blocker.lower() for token in ("selected_price<=0", "selected_price>=100", "malformed", "duplicate", "auth", "corrupt", "dead")))
            or not bool(candidate.get("selected_side") or quote.get("selected_side") or quote.get("side"))
            or not selected_side_has_price
            or selected_price_cents in (None, 0)
            or selected_price_cents >= 100
            or (not orderbook_ready and not selected_side_has_price)
        ),
        "selected_side": selected_side,
        "selected_price": selected_price,
        "selected_price_cents": selected_price_cents,
    }


def _paper30_tier(metrics: Dict[str, Any], frozen_trace: Dict[str, Any]) -> str:
    if metrics.get("hard_block"):
        return "HARD BLOCK"
    confidence = float(metrics.get("confidence_score") or 0.0)
    selected_side_has_price = bool(frozen_trace.get("selected_side_has_price"))
    selected_side_has_liquidity = bool(frozen_trace.get("selected_side_has_liquidity"))
    final_submit_allowed = bool(frozen_trace.get("final_submit_allowed"))
    if confidence >= 0.78 and selected_side_has_price and selected_side_has_liquidity and final_submit_allowed:
        return "S"
    if confidence >= 0.62 and selected_side_has_price and final_submit_allowed:
        return "A"
    if confidence >= 0.42 and selected_side_has_price:
        return "B"
    return "C"


def _profit_tier(metrics: Dict[str, Any], frozen_trace: Dict[str, Any]) -> str:
    if metrics.get("hard_block"):
        return "HARD_BLOCK"
    confidence = float(metrics.get("confidence_score") or 0.0)
    expected_value = float(metrics.get("expected_value") or 0.0)
    selected_side_has_price = bool(frozen_trace.get("selected_side_has_price"))
    selected_side_has_liquidity = bool(frozen_trace.get("selected_side_has_liquidity"))
    final_submit_allowed = bool(frozen_trace.get("final_submit_allowed"))
    dangerous = bool(
        expected_value < -0.05
        or float(metrics.get("spread_penalty") or 0.0) > 0.22
        or float(metrics.get("liquidity_penalty") or 0.0) > 0.15
        or str(frozen_trace.get("hard_block_reason") or "").lower() in {"dead_orderbook", "corrupt_orderbook"}
    )
    if expected_value > 0.06 and confidence >= 0.78 and selected_side_has_price and selected_side_has_liquidity and final_submit_allowed:
        return "S"
    if expected_value > 0.02 and confidence >= 0.68 and selected_side_has_price and final_submit_allowed and not dangerous:
        return "A"
    if expected_value > 0.0 and confidence >= 0.60 and selected_side_has_price and not dangerous:
        return "B"
    if dangerous or expected_value < 0.0:
        return "D"
    return "C"


def _run_relaxed_survivable_aggressive_regression(bot: KalshiSniperBot) -> Dict[str, Any]:
    quote, candidate, ticker, simulation_state = _load_latest_frozen_candidate_from_journal()
    if quote is None:
        recent_candidates = _collect_recent_candidate_pool(limit=1)
        if recent_candidates:
            quote = _candidate_to_quote(recent_candidates[0])
            candidate = dict(recent_candidates[0])
            ticker = candidate.get("ticker") or getattr(quote, "ticker", None)
    if quote is None:
        return {"pass": False, "reason": "no_frozen_candidate"}
    base_market = {"ticker": ticker or getattr(quote, "ticker", None)}
    forced_context = {
        "ticker": ticker or getattr(quote, "ticker", None),
        "selected_side": "yes",
        "price_source": "ask",
        "allow_refresh": True,
        "final_edge": 0.01,
        "sports_edge_after_cost": 0.01,
        "quote_diag": {
            "quote_ready": True,
            "orderbook_ready": False,
            "selected_side_has_price": True,
            "selected_side_has_liquidity": True,
            "depth_available": True,
            "selected_price_dollars": 0.99,
            "selected_price_cents": 99,
            "selected_price_source": "ask",
            "spread_classification": "AGGRESSIVE_BUT_TRADEABLE",
            "spread_bucket": "wide_but_possible",
            "final_submit_allowed_by_realm_vv": False,
            "final_edge": 0.01,
            "unsafe_price_status": "caution_wide_spread",
        },
    }
    invalid_no_side = SimpleNamespace(
        ticker=getattr(quote, "ticker", None),
        family=getattr(quote, "family", None),
        side=None,
        selected_side=None,
        selected_price=None,
        selected_price_dollars=None,
        selected_price_cents=None,
        yes_bid=None,
        yes_ask=None,
        no_bid=None,
        no_ask=None,
        selected_side_has_price=False,
        selected_side_has_liquidity=False,
        depth_available=False,
        price_bounds_pass=False,
        quote_ready=False,
        quote_missing=True,
        orderbook_ready=False,
        final_submit_allowed_by_realm_vv=False,
        decision="BUY",
        trace_id=getattr(quote, "trace_id", None),
    )
    invalid_price = SimpleNamespace(
        ticker=getattr(quote, "ticker", None),
        family=getattr(quote, "family", None),
        side="yes",
        selected_side="yes",
        selected_price=1.0,
        selected_price_dollars=1.0,
        selected_price_cents=100,
        yes_bid=None,
        yes_ask=None,
        no_bid=None,
        no_ask=None,
        selected_side_has_price=False,
        selected_side_has_liquidity=False,
        depth_available=False,
        price_bounds_pass=False,
        quote_ready=True,
        quote_missing=False,
        orderbook_ready=True,
        final_submit_allowed_by_realm_vv=False,
        decision="BUY",
        trace_id=getattr(quote, "trace_id", None),
    )
    regression = {
        "pass": False,
        "ticker": ticker,
        "forced_branch": None,
        "hard_safety_ok": False,
        "valid_near_miss_ok": False,
        "invalid_no_side_blocked": False,
        "invalid_price_blocked": False,
        "error": None,
    }
    print("[AION-RELAXED-SURVIVABLE-REGRESSION-START] " + json.dumps({"ticker": ticker, "source": simulation_state.get("source")}, sort_keys=True, default=str))
    try:
        forced_tradeability = bot.resolve_canonical_tradeability(base_market, quote=quote, context=forced_context, source="regression_relaxed_survivable_aggressive")
        regression["forced_branch"] = forced_tradeability
        regression["valid_near_miss_ok"] = bool(
            forced_tradeability.get("selected_side") in {"yes", "no"}
            and forced_tradeability.get("selected_side_has_price")
            and forced_tradeability.get("selected_side_has_liquidity")
            and forced_tradeability.get("selected_price_cents") is not None
            and 0 < int(forced_tradeability.get("selected_price_cents")) < 100
        )
        print("[AION-RELAXED-SURVIVABLE-REGRESSION-BRANCH] " + json.dumps({"ticker": ticker, "tradeability": forced_tradeability}, sort_keys=True, default=str))
    except Exception as exc:
        regression["error"] = {"type": exc.__class__.__name__, "reason": str(exc), "phase": "forced_branch"}
        print("[AION-SIMULATION-BUG-CAUGHT] " + json.dumps({"bug_type": exc.__class__.__name__, "function": "resolve_canonical_tradeability", "line": None, "ticker": ticker, "trace_id": getattr(quote, "trace_id", None), "canonical_state": getattr(quote, "canonical_execution_state", {}), "raw_fields": _serialize_quote(quote), "suspected_root_cause": "relaxed_survivable_aggressive_scope", "suggested_patch_area": "resolve_canonical_tradeability root initializer"}, sort_keys=True, default=str))
        print("[AION-RELAXED-SURVIVABLE-REGRESSION-FAIL] " + json.dumps(regression, sort_keys=True, default=str))
        return regression
    try:
        no_side_result = bot.resolve_canonical_tradeability(base_market, quote=invalid_no_side, context={
            **forced_context,
            "selected_side": None,
            "quote_diag": {**forced_context["quote_diag"], "selected_side_has_price": False, "selected_side_has_liquidity": False, "selected_price_cents": None, "selected_price_dollars": None, "final_submit_allowed_by_realm_vv": False},
        }, source="regression_invalid_no_side")
        price_result = bot.resolve_canonical_tradeability(base_market, quote=invalid_price, context={
            **forced_context,
            "quote_diag": {**forced_context["quote_diag"], "selected_price_cents": 100, "selected_price_dollars": 1.0, "selected_side_has_price": False, "final_submit_allowed_by_realm_vv": False},
        }, source="regression_invalid_price")
        regression["invalid_no_side_blocked"] = bool(
            str(no_side_result.get("selected_side") or "").strip() not in {"yes", "no"}
            or int(no_side_result.get("selected_price_cents") or 0) <= 0
            or not bool(no_side_result.get("selected_side_has_price"))
        )
        regression["invalid_price_blocked"] = bool(
            int(price_result.get("selected_price_cents") or 0) >= 100
            or int(price_result.get("selected_price_cents") or 0) <= 0
            or not bool(price_result.get("selected_side_has_price"))
        )
        regression["hard_safety_ok"] = bool(regression["invalid_no_side_blocked"] and regression["invalid_price_blocked"])
        regression["pass"] = bool(regression["valid_near_miss_ok"] and regression["hard_safety_ok"])
        print(
            "[AION-RELAXED-SURVIVABLE-REGRESSION-PASS] "
            + json.dumps(regression, sort_keys=True, default=str)
            if regression["pass"]
            else "[AION-RELAXED-SURVIVABLE-REGRESSION-FAIL] " + json.dumps(regression, sort_keys=True, default=str)
        )
        return regression
    except Exception as exc:
        regression["error"] = {"type": exc.__class__.__name__, "reason": str(exc), "phase": "hard_safety_checks"}
        print("[AION-SIMULATION-BUG-CAUGHT] " + json.dumps({"bug_type": exc.__class__.__name__, "function": "resolve_canonical_tradeability", "line": None, "ticker": ticker, "trace_id": getattr(quote, "trace_id", None), "canonical_state": getattr(quote, "canonical_execution_state", {}), "raw_fields": _serialize_quote(quote), "suspected_root_cause": "relaxed_survivable_aggressive_regression", "suggested_patch_area": "resolve_canonical_tradeability and regression harness"}, sort_keys=True, default=str))
        print("[AION-RELAXED-SURVIVABLE-REGRESSION-FAIL] " + json.dumps(regression, sort_keys=True, default=str))
        return regression


def _paper30_family_rank(candidate: Dict[str, Any], metrics: Dict[str, Any]) -> float:
    family = str(candidate.get("family") or "").upper()
    base = 0.5
    if family.startswith("KX"):
        base += 0.08
    if family.endswith("MATCH"):
        base += 0.05
    if metrics.get("confidence_score", 0.0) >= 0.7:
        base += 0.08
    if metrics.get("strict_would_reject"):
        base -= 0.05
    return max(0.0, min(1.0, base))


def _profit_candidate_record(
    *,
    candidate: Dict[str, Any],
    frozen_trace: Dict[str, Any],
    metrics: Dict[str, Any],
    tier: str,
    batch_index: int,
    candidate_index: int,
    submit_result: Dict[str, Any],
    recovered: bool,
    recovered_side: Optional[str],
) -> Dict[str, Any]:
    quote = frozen_trace.get("selected_quote") if isinstance(frozen_trace, dict) else {}
    quote = quote if isinstance(quote, dict) else {}
    loosened_rules: List[str] = []
    for rule in metrics.get("soft_penalties", []) or []:
        if rule and rule not in loosened_rules:
            loosened_rules.append(rule)
    for rule, enabled in (
        ("thin_book_override", bool(frozen_trace.get("thin_book_override_applied"))),
        ("beast_spread_override", bool(frozen_trace.get("beast_spread_override_applied"))),
        ("survivable_aggressive_override", bool(frozen_trace.get("survivable_aggressive_override"))),
        ("base_sports_threshold", bool(frozen_trace.get("base_sports_threshold_softened"))),
    ):
        if enabled and rule not in loosened_rules:
            loosened_rules.append(rule)
    if recovered and "orderbook_not_usable" not in loosened_rules and "quote_not_usable" not in loosened_rules:
        loosened_rules.append("side_recovery")
    selected_side = str(frozen_trace.get("selected_quote", {}).get("selected_side") or quote.get("selected_side") or candidate.get("selected_side") or "").strip().lower() or "unknown"
    selected_price = frozen_trace.get("selected_quote", {}).get("selected_price")
    if selected_price is None:
        selected_price = frozen_trace.get("selected_quote", {}).get("selected_price_dollars")
    if selected_price is None:
        selected_price = metrics.get("simulated_entry_price")
    try:
        selected_price = float(selected_price) if selected_price not in (None, "", []) else None
    except Exception:
        selected_price = None
    selected_price_cents = frozen_trace.get("selected_quote", {}).get("selected_price_cents")
    if selected_price_cents is None:
        selected_price_cents = metrics.get("selected_price_cents")
    try:
        selected_price_cents = int(round(float(selected_price_cents))) if selected_price_cents not in (None, "", []) else None
    except Exception:
        selected_price_cents = None
    time_to_settlement = candidate.get("time_to_settlement_minutes")
    if time_to_settlement in (None, "", []):
        time_to_settlement = candidate.get("minutes_to_event")
    if time_to_settlement in (None, "", []):
        time_to_settlement = candidate.get("time_to_settlement")
    try:
        time_to_settlement = float(time_to_settlement) if time_to_settlement not in (None, "", []) else None
    except Exception:
        time_to_settlement = None
    simulated_hold_time = 4.0 if str(metrics.get("simulated_exit_policy") or "").strip().lower() == "fast_exit" else 12.0
    if time_to_settlement is not None:
        simulated_hold_time = max(2.0, min(float(time_to_settlement), simulated_hold_time))
    expected_profit = float(metrics.get("simulated_expected_profit") or 0.0)
    expected_loss = float(metrics.get("simulated_expected_loss") or 0.0)
    expected_value = float(metrics.get("expected_value") or (expected_profit - expected_loss))
    liquidity_penalty = float(metrics.get("liquidity_penalty") or 0.0)
    spread_penalty = float(metrics.get("spread_penalty") or 0.0)
    volatility_penalty = float(metrics.get("volatility_penalty") or 0.0)
    edge_penalty = round(max(0.0, spread_penalty + volatility_penalty - max(0.0, expected_value)), 6)
    simulated_fill_quality = round(max(0.0, min(1.0, float(metrics.get("confidence_score") or 0.0) * (1.0 - min(0.5, spread_penalty + liquidity_penalty)))), 4)
    liquidity_score = round(max(0.0, min(1.0, 1.0 - liquidity_penalty)), 4)
    volatility_score = round(max(0.0, min(1.0, 1.0 - volatility_penalty)), 4)
    time_decay_risk = round(max(0.0, min(1.0, 1.0 - (simulated_hold_time / max(2.0, (time_to_settlement or 20.0))))), 4)
    orderbook_depth = candidate.get("orderbook_depth")
    if orderbook_depth in (None, "", []):
        orderbook_depth = frozen_trace.get("selected_quote", {}).get("depth_available")
    if isinstance(orderbook_depth, bool):
        orderbook_depth = 1 if orderbook_depth else 0
    try:
        orderbook_depth = float(orderbook_depth) if orderbook_depth not in (None, "", []) else None
    except Exception:
        orderbook_depth = None
    strict_live_would_reject = bool(metrics.get("strict_live_would_reject"))
    relaxed_live_result = bool(submit_result.get("ok") and submit_result.get("submitted"))
    profitable = expected_value > 0.0 and relaxed_live_result
    dangerous = tier == "D"
    market_type = str(candidate.get("market_type") or candidate.get("market_kind") or candidate.get("type") or ("sports" if str(candidate.get("family") or "").startswith("KX") else "unknown")).strip().lower() or "unknown"
    return {
        "batch_index": batch_index,
        "candidate_index": candidate_index,
        "ticker": frozen_trace.get("ticker"),
        "family": candidate.get("family") or _candidate_family_key(candidate),
        "market_type": market_type,
        "confidence_score": float(metrics.get("confidence_score") or 0.0),
        "expected_profit": expected_profit,
        "expected_loss": expected_loss,
        "expected_value": round(expected_value, 6),
        "simulated_fill_quality": simulated_fill_quality,
        "spread_cost": spread_penalty,
        "liquidity_score": liquidity_score,
        "volatility_score": volatility_score,
        "time_decay_risk": time_decay_risk,
        "orderbook_depth": orderbook_depth,
        "selected_side": selected_side,
        "selected_price": selected_price,
        "selected_price_cents": selected_price_cents,
        "simulated_size": metrics.get("simulated_size"),
        "simulated_exit_policy": metrics.get("simulated_exit_policy"),
        "simulated_hold_time": round(simulated_hold_time, 4),
        "simulated_slippage": round(max(0.0, spread_penalty * 0.4 + liquidity_penalty * 0.3 + volatility_penalty * 0.3), 6),
        "strict_live_would_reject": strict_live_would_reject,
        "strict_live_result": "REJECT" if strict_live_would_reject else "ALLOW",
        "relaxed_live_result": "EXECUTE" if relaxed_live_result else "NO_EXECUTE",
        "profitability_tier": tier,
        "loosened_rules_used": loosened_rules,
        "exit_quality_projection": round(max(0.0, min(1.0, float(metrics.get("simulated_risk_reward") or 0.0) / (1.0 + abs(float(metrics.get("simulated_risk_reward") or 0.0))))), 4),
        "profitability_signal": "profitable" if profitable else "dangerous" if dangerous else "neutral",
        "recovered_side": recovered_side,
        "candidate": candidate,
        "frozen_trace": frozen_trace,
        "metrics": metrics,
        "submit_result": submit_result,
    }


def _configure_simulation_bot(bot: KalshiSniperBot) -> KalshiSniperBot:
    bot.maybe_send_telegram_heartbeat = lambda *args, **kwargs: None  # type: ignore[assignment]
    bot.record_live_position = lambda *args, **kwargs: None  # type: ignore[assignment]
    bot.run_live_auth_get_smoke_test = lambda: {"ok": True, "reason": "simulated", "status_code": 200}  # type: ignore[assignment]
    bot.has_live_order_api_path = lambda: True  # type: ignore[assignment]
    bot.confirm_live_order_submit = lambda payload: True  # type: ignore[assignment]
    bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False, "reason": "simulated", "position_size": 0}  # type: ignore[assignment]
    bot.get_live_submit_authorization = lambda payload: {  # type: ignore[assignment]
        "ok": True,
        "reason": "simulated",
        "flags": {
            "live_mode": True,
            "live_armed": True,
            "live_env_confirmed": True,
            "effective_live_dry_run": False,
        },
        "normal_live_service_mode": True,
        "authorized_canary_ticker": None,
        "supplied_ticker": payload.get("ticker") if isinstance(payload, dict) else None,
    }
    bot.check_live_exposure_limits = lambda ticker, family=None: (True, "simulated", {})  # type: ignore[assignment]
    bot._check_sports_correlated_entry_guard = lambda *args, **kwargs: (True, "simulated", {})  # type: ignore[assignment]
    bot._check_post_loss_reentry_block = lambda *args, **kwargs: (True, "simulated")  # type: ignore[assignment]
    bot.place_live_order = lambda payload: {  # type: ignore[assignment]
        "ok": True,
        "status_code": 200,
        "response": {"simulated": True, "order_id": f"SIM-{payload.get('ticker') or 'UNKNOWN'}"},
        "reason": "submitted",
    }
    bot.verify_live_order = lambda order_id: {  # type: ignore[assignment]
        "ok": True,
        "order": {"status": "executed", "order_id": order_id},
    }
    return bot


def _aggregate_profit_batches(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    rule_stats: Dict[str, Dict[str, Any]] = {}
    family_stats: Dict[str, Dict[str, Any]] = {}
    tier_counts = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0, "HARD_BLOCK": 0}
    total_expected_value = 0.0
    total_expected_profit = 0.0
    total_expected_loss = 0.0
    total_confidence = 0.0
    total_spread = 0.0
    profitable_count = 0
    executed_count = 0
    strict_reject_count = 0
    hard_block_count = 0
    for row in rows:
        tier = str(row.get("profitability_tier") or "C")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        expected_value = float(row.get("expected_value") or 0.0)
        expected_profit = float(row.get("expected_profit") or 0.0)
        expected_loss = float(row.get("expected_loss") or 0.0)
        confidence_score = float(row.get("confidence_score") or 0.0)
        spread_cost = float(row.get("spread_cost") or 0.0)
        liquidity_score = float(row.get("liquidity_score") or 0.0)
        volatility_score = float(row.get("volatility_score") or 0.0)
        family = str(row.get("family") or "UNKNOWN")
        rules = list(row.get("loosened_rules_used") or [])
        total_expected_value += expected_value
        total_expected_profit += expected_profit
        total_expected_loss += expected_loss
        total_confidence += confidence_score
        total_spread += spread_cost
        executed = str(row.get("relaxed_live_result") or "").upper() == "EXECUTE"
        strict_reject = bool(row.get("strict_live_would_reject"))
        hard_block = tier == "HARD_BLOCK"
        if executed:
            executed_count += 1
        if strict_reject:
            strict_reject_count += 1
        if hard_block:
            hard_block_count += 1
        if expected_value > 0.0 and executed:
            profitable_count += 1
        fam = family_stats.setdefault(
            family,
            {
                "family": family,
                "count": 0,
                "executed": 0,
                "strict_reject_count": 0,
                "hard_block_count": 0,
                "expected_value_sum": 0.0,
                "confidence_sum": 0.0,
                "spread_sum": 0.0,
                "liquidity_sum": 0.0,
                "volatility_sum": 0.0,
                "hold_time_sum": 0.0,
                "slippage_sum": 0.0,
                "positive_expected_value_count": 0,
                "negative_expected_value_count": 0,
                "tier_counts": {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0, "HARD_BLOCK": 0},
            },
        )
        fam["count"] += 1
        fam["executed"] += int(executed)
        fam["strict_reject_count"] += int(strict_reject)
        fam["hard_block_count"] += int(hard_block)
        fam["expected_value_sum"] += expected_value
        fam["confidence_sum"] += confidence_score
        fam["spread_sum"] += spread_cost
        fam["liquidity_sum"] += liquidity_score
        fam["volatility_sum"] += volatility_score
        fam["hold_time_sum"] += float(row.get("simulated_hold_time") or 0.0)
        fam["slippage_sum"] += float(row.get("simulated_slippage") or 0.0)
        fam["positive_expected_value_count"] += int(expected_value > 0.0)
        fam["negative_expected_value_count"] += int(expected_value < 0.0)
        fam["tier_counts"][tier] = fam["tier_counts"].get(tier, 0) + 1
        for rule in rules:
            stats = rule_stats.setdefault(
                rule,
                {
                    "rule": rule,
                    "count": 0,
                    "execution_count": 0,
                    "strict_reject_count": 0,
                    "expected_value_sum": 0.0,
                    "expected_profit_sum": 0.0,
                    "expected_loss_sum": 0.0,
                    "confidence_sum": 0.0,
                    "risk_reward_sum": 0.0,
                    "spread_sum": 0.0,
                    "liquidity_sum": 0.0,
                    "volatility_sum": 0.0,
                    "positive_count": 0,
                    "negative_count": 0,
                    "families": Counter(),
                },
            )
            stats["count"] += 1
            stats["execution_count"] += int(executed)
            stats["strict_reject_count"] += int(strict_reject)
            stats["expected_value_sum"] += expected_value
            stats["expected_profit_sum"] += expected_profit
            stats["expected_loss_sum"] += expected_loss
            stats["confidence_sum"] += confidence_score
            stats["risk_reward_sum"] += float((row.get("metrics") or {}).get("simulated_risk_reward") or 0.0)
            stats["spread_sum"] += spread_cost
            stats["liquidity_sum"] += liquidity_score
            stats["volatility_sum"] += volatility_score
            stats["positive_count"] += int(expected_value > 0.0)
            stats["negative_count"] += int(expected_value < 0.0)
            stats["families"][family] += 1
    candidate_count = len(rows)
    def _finalize_family(item: Dict[str, Any]) -> Dict[str, Any]:
        count = max(1, int(item.get("count") or 0))
        avg_ev = item["expected_value_sum"] / count
        avg_conf = item["confidence_sum"] / count
        avg_spread = item["spread_sum"] / count
        avg_liquidity = item["liquidity_sum"] / count
        avg_volatility = item["volatility_sum"] / count
        avg_hold = item["hold_time_sum"] / count
        avg_slippage = item["slippage_sum"] / count
        return {
            **item,
            "execution_rate": round(item["executed"] / count, 4),
            "avg_expected_value": round(avg_ev, 6),
            "avg_confidence": round(avg_conf, 4),
            "confidence_consistency": round(max(0.0, min(1.0, 1.0 - abs(avg_conf - 0.7))), 4),
            "spread_quality": round(max(0.0, 1.0 - avg_spread), 4),
            "liquidity_quality": round(max(0.0, avg_liquidity), 4),
            "simulated_profitability": round(avg_ev, 6),
            "average_hold_quality": round(max(0.0, 1.0 - min(1.0, avg_hold / 30.0)), 4),
            "slippage_risk": round(avg_slippage, 6),
            "avg_spread_penalty": round(avg_spread, 6),
            "avg_liquidity_penalty": round(max(0.0, 1.0 - avg_liquidity), 6),
            "avg_volatility_penalty": round(max(0.0, 1.0 - avg_volatility), 6),
            "profit_quality_score": round(avg_ev + avg_conf * 0.2 - avg_spread * 0.1, 6),
            "tier_counts": dict(item["tier_counts"]),
            "tier_rankings": [tier for tier, _ in sorted(item["tier_counts"].items(), key=lambda kv: (kv[1], kv[0]), reverse=True)],
        }
    def _finalize_rule(item: Dict[str, Any]) -> Dict[str, Any]:
        count = max(1, int(item.get("count") or 0))
        avg_ev = item["expected_value_sum"] / count
        avg_conf = item["confidence_sum"] / count
        avg_rr = item["risk_reward_sum"] / count
        avg_spread = item["spread_sum"] / count
        avg_liquidity = item["liquidity_sum"] / count
        avg_volatility = item["volatility_sum"] / count
        families = Counter(item["families"])
        helper_families = [fam for fam, _ in families.most_common(5)]
        return {
            **item,
            "execution_gain": int(item["execution_count"] - item["strict_reject_count"]),
            "average_expected_value": round(avg_ev, 6),
            "average_confidence": round(avg_conf, 4),
            "average_risk_reward": round(avg_rr, 4),
            "win_quality_score": round(item["positive_count"] / count, 4),
            "loss_quality_score": round(item["negative_count"] / count, 4),
            "profit_factor": round((item["positive_count"] + 1e-9) / (item["negative_count"] + 1e-9), 4),
            "family_compatibility": helper_families,
            "volatility_sensitivity": round(avg_volatility, 6),
            "spread_sensitivity": round(avg_spread, 6),
            "liquidity_sensitivity": round(avg_liquidity, 6),
            "overtrading_risk": round(max(0.0, 1.0 - (item["execution_count"] / count)) + max(0.0, -avg_ev), 6),
            "families": dict(families),
        }
    family_rankings = [_finalize_family(item) for item in family_stats.values()]
    family_rankings.sort(key=lambda item: (item.get("avg_expected_value", 0.0), item.get("avg_confidence", 0.0)), reverse=True)
    rule_profitability = [_finalize_rule(item) for item in rule_stats.values()]
    rule_profitability.sort(key=lambda item: (item.get("average_expected_value", 0.0), item.get("average_confidence", 0.0)), reverse=True)
    avg_confidence = round(total_confidence / candidate_count, 4) if candidate_count else 0.0
    avg_expected_profit = round(total_expected_profit / candidate_count, 6) if candidate_count else 0.0
    avg_expected_loss = round(total_expected_loss / candidate_count, 6) if candidate_count else 0.0
    avg_expected_value = round(total_expected_value / candidate_count, 6) if candidate_count else 0.0
    avg_spread_penalty = round(total_spread / candidate_count, 6) if candidate_count else 0.0
    profitable_execution_rate = round(profitable_count / candidate_count, 4) if candidate_count else 0.0
    current_state = "profitable" if avg_expected_value > 0 and avg_confidence >= 0.60 else "balanced" if avg_expected_value >= -0.02 else "reckless"
    profitable_rules = [item["rule"] for item in rule_profitability if item["average_expected_value"] > 0.0 and item["average_confidence"] >= 0.60]
    dangerous_rules = [item["rule"] for item in rule_profitability if item["average_expected_value"] < 0.0 or item.get("overtrading_risk", 0.0) > 0.35]
    best_tiers = [tier for tier, count in sorted(tier_counts.items(), key=lambda item: (item[1], item[0]), reverse=True) if tier in {"S", "A", "B"} and count > 0]
    recommended_live_policy = {
        "s_tier": "fully_live",
        "a_tier": "live_reduced_size",
        "b_tier": "aggressive_live_micro_size",
        "c_tier": "paper_only",
        "d_tier": "blocked",
        "profitable_rules": profitable_rules[:10],
        "dangerous_rules": dangerous_rules[:10],
        "best_live_candidate_tiers": best_tiers[:3],
        "current_profitability_state": current_state,
    }
    summary = {
        "timestamp": _now_iso(),
        "mode": "profit_discovery",
        "candidates_analyzed": candidate_count,
        "candidate_count": candidate_count,
        "batches": None,
        "sample_size": None,
        "execution_rate": round(executed_count / candidate_count, 4) if candidate_count else 0.0,
        "profitable_execution_rate": profitable_execution_rate,
        "tier_counts": tier_counts,
        "total_expected_value": round(total_expected_value, 6),
        "average_expected_value": avg_expected_value,
        "average_confidence": avg_confidence,
        "total_expected_profit": round(total_expected_profit, 6),
        "total_expected_loss": round(total_expected_loss, 6),
        "average_expected_profit": avg_expected_profit,
        "average_expected_loss": avg_expected_loss,
        "average_spread_penalty": avg_spread_penalty,
        "best_families": [item["family"] for item in family_rankings[:5]],
        "worst_families": [item["family"] for item in family_rankings[-5:]],
        "profitable_loosened_rules": profitable_rules[:10],
        "dangerous_loosened_rules": dangerous_rules[:10],
        "best_live_candidate_tiers": best_tiers[:3],
        "recommended_live_policy": recommended_live_policy,
        "current_profitability_state": current_state,
        "execution_vs_profit_balance": {
            "execution_rate": round(executed_count / candidate_count, 4) if candidate_count else 0.0,
            "profitable_execution_rate": profitable_execution_rate,
            "avg_expected_value": avg_expected_value,
            "avg_confidence": avg_confidence,
        },
        "overtrading_risk": round(max(0.0, 1.0 - profitable_execution_rate) + max(0.0, -avg_expected_value), 6),
        "hard_block_rate": round(hard_block_count / candidate_count, 4) if candidate_count else 0.0,
        "strict_rejected_count": strict_reject_count,
        "hard_blocked_count": hard_block_count,
        "simulation_pass": candidate_count > 0 and hard_block_count < candidate_count,
        "rule_profitability": rule_profitability,
        "family_rankings": family_rankings,
    }
    return summary, family_rankings, rule_profitability


def run_profit_discovery(*, batches: int = 25, sample_size: int = 30, random_real_candidates: bool = True, family_discovery_mode: bool = False, force_family_diversity: bool = False) -> Dict[str, Any]:
    _ensure_dirs()
    _ensure_simulation_dirs()
    run_dir = _ensure_profit_engine_dir()
    bot = _configure_simulation_bot(KalshiSniperBot())
    rows: List[Dict[str, Any]] = []
    strict_rejected = 0
    executed = 0
    batch_count = max(1, int(batches or 1))
    sample_count = max(1, int(sample_size or 30))
    family_seen_counts: Dict[str, int] = {}
    family_discovery_mode = bool(family_discovery_mode or force_family_diversity)
    market_universe = _discover_open_market_universe(bot, limit=max(800, sample_count * 16), force_family_diversity=family_discovery_mode) if family_discovery_mode else []
    discovered_families = sorted({_candidate_family_key(market) for market in market_universe}) if family_discovery_mode else []
    discovered_categories = sorted({_candidate_category_key(market) for market in market_universe}) if family_discovery_mode else []
    diversity_issue_reason = None
    if family_discovery_mode:
        if not market_universe:
            diversity_issue_reason = "api_returns_no_markets"
        elif len(discovered_families) <= 1:
            diversity_issue_reason = "api_returns_only_one_family"
        elif len(discovered_categories) < 3:
            diversity_issue_reason = "categories_removed_too_early_or_universe_too_narrow"
    profit_discovery_allow_prefixes = (
        "[AION-FAMILY-DIVERSITY]",
        "[AION-FAMILY-ROTATION]",
        "[AION-FAMILY-SUPPRESSION]",
        "[AION-FAMILY-PROFIT]",
        "[AION-FAMILY-HEATMAP]",
        "[AION-PROFIT-DISCOVERY-SUMMARY]",
        "[AION-PAPER30-",
        "[AION-SIMULATION-",
        "[AION-REACHABILITY-RECOVERED]",
        "[AION-RAW-BLOCKER-SAMPLE]",
        "[AION-SELECTED-PRICE-RESOLVED]",
        "[AION-SELECTED-PRICE-REJECTED]",
        "[AION-SIDE-INVERSION-DETECTED]",
        "[AION-BASE-SPORTS-THRESHOLD-SOFTENED]",
    )
    with io.StringIO() as stdout_buffer, redirect_stdout(_DedupingTee(sys.stdout, stdout_buffer, max_exact_repeats=2, always_allow_prefixes=profit_discovery_allow_prefixes)):
        print(
            "[AION-PROFIT-DISCOVERY-START] "
            + json.dumps(
                {
                    "timestamp": _now_iso(),
                    "batches": batch_count,
                    "sample_size": sample_count,
                    "random_real_candidates": bool(random_real_candidates),
                    "target_candidates": batch_count * sample_count,
                    "family_discovery_mode": bool(family_discovery_mode),
                    "force_family_diversity": bool(force_family_diversity),
                },
                sort_keys=True,
                default=str,
            )
        )
        if family_discovery_mode:
            print(
                "[AION-FAMILY-DIVERSITY] "
                + json.dumps(
                    {
                        "candidate_universe": len(market_universe),
                        "discovered_family_count": len(discovered_families),
                        "discovered_families": discovered_families[:100],
                        "discovered_category_count": len(discovered_categories),
                        "discovered_categories": discovered_categories[:100],
                        "max_family_share": 0.15,
                        "min_distinct_families": 8,
                        "min_distinct_categories": 3,
                        "diversity_issue_reason": diversity_issue_reason,
                    },
                    sort_keys=True,
                    default=str,
                )
            )
        global_index = 0
        category_seen_counts: Dict[str, int] = {}
        for batch_index in range(1, batch_count + 1):
            if family_discovery_mode:
                batch_markets = random.sample(market_universe, min(sample_count, len(market_universe))) if market_universe else []
                family_rotation = {
                    "selected_family_counts": dict(Counter(_candidate_family_key(market) for market in batch_markets)),
                    "selected_category_counts": dict(Counter(_candidate_category_key(market) for market in batch_markets)),
                    "reason": "simulator_inline_random_sample",
                }
                for fam, count in (family_rotation.get("selected_family_counts") or {}).items():
                    family_seen_counts[fam] = int(family_seen_counts.get(fam, 0) + int(count or 0))
                for category, count in (family_rotation.get("selected_category_counts") or {}).items():
                    category_seen_counts[category] = int(category_seen_counts.get(category, 0) + int(count or 0))
                print(
                    "[AION-FAMILY-ROTATION] "
                    + json.dumps(
                        {
                            "batch_index": batch_index,
                            "family_order": family_rotation.get("family_order", []),
                            "category_order": family_rotation.get("category_order", []),
                            "quota_per_family": family_rotation.get("quota_per_family"),
                            "selected_family_counts": family_rotation.get("selected_family_counts", {}),
                            "selected_category_counts": family_rotation.get("selected_category_counts", {}),
                            "rotated_families": family_rotation.get("rotated_families", []),
                            "rotated_categories": family_rotation.get("rotated_categories", []),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                print(
                    "[AION-FAMILY-SUPPRESSION] "
                    + json.dumps(
                    {
                        "batch_index": batch_index,
                        "suppressed_families": family_rotation.get("suppressed_families", []),
                        "suppressed_categories": family_rotation.get("suppressed_categories", []),
                        "family_seen_counts": family_seen_counts,
                        "category_seen_counts": category_seen_counts,
                    },
                    sort_keys=True,
                    default=str,
                    )
                )
                batch_candidates = []
                for market in batch_markets:
                    if not isinstance(market, dict):
                        continue
                    try:
                        quote = bot.build_quote(market)
                    except Exception:
                        quote = None
                    batch_candidates.append(_market_to_candidate_record(bot, market, quote))
                if not batch_candidates:
                    batch_candidates = _paper30_select_candidates(sample_count if random_real_candidates else min(sample_count, 1))
            else:
                batch_candidates = _paper30_select_candidates(sample_count if random_real_candidates else min(sample_count, 1))
            print(
                "[AION-PROFIT-DISCOVERY-BATCH] "
                + json.dumps(
                    {
                        "batch_index": batch_index,
                        "batch_candidates": len(batch_candidates),
                        "family_discovery_mode": bool(family_discovery_mode),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            for candidate_index, candidate in enumerate(batch_candidates[:sample_count], start=1):
                global_index += 1
                quote = _candidate_to_quote(candidate)
                quote, candidate, recovered, recovered_side = _paper30_recover_selected_side(candidate, quote)
                ticker = str(candidate.get("ticker") or getattr(quote, "ticker", "") or "").strip() or None
                if not ticker:
                    ticker = f"UNKNOWN-{batch_index}-{candidate_index}"
                print(
                    "[AION-PROFIT-DISCOVERY-CANDIDATE] "
                    + json.dumps(
                        {
                            "batch_index": batch_index,
                            "candidate_index": candidate_index,
                            "global_index": global_index,
                            "ticker": ticker,
                            "family": candidate.get("family"),
                            "selected_side": candidate.get("selected_side"),
                            "selected_price_cents": candidate.get("selected_price_cents"),
                            "trace_id": candidate.get("trace_id"),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                if recovered:
                    print(
                        "[AION-SIDE-RECOVERY-APPLIED] "
                        + json.dumps(
                            {
                                "batch_index": batch_index,
                                "candidate_index": candidate_index,
                                "ticker": ticker,
                                "recovered_side": recovered_side,
                                "selected_price_cents": getattr(quote, "selected_price_cents", None),
                                "selected_price": getattr(quote, "selected_price", None),
                            },
                            sort_keys=True,
                            default=str,
                        )
                    )
                frozen_trace = _build_frozen_trace(bot, quote, ticker)
                metrics = _paper30_strategy_metrics(candidate, frozen_trace)
                tier = _profit_tier(metrics, frozen_trace)
                submit_payload = frozen_trace.get("payload")
                if not isinstance(submit_payload, dict):
                    submit_payload = {}
                if tier in {"B", "C", "D"} or float(metrics.get("confidence_score") or 0.0) < 0.6:
                    submit_payload = dict(submit_payload)
                    submit_payload["count"] = max(1, min(2, int(metrics.get("simulated_size") or 1)))
                    submit_payload["post_only"] = False
                if metrics.get("hard_block"):
                    print(
                        "[AION-PROFIT-DISCOVERY-HARD-BLOCK] "
                        + json.dumps(
                            {
                                "batch_index": batch_index,
                                "candidate_index": candidate_index,
                                "ticker": ticker,
                                "blocker": frozen_trace.get("hard_block_reason") or candidate.get("blocker"),
                                "canonical_execution_state": frozen_trace.get("canonical_execution_state"),
                                "raw_candidate": candidate.get("raw_fields"),
                                "reason": "hard_blocker",
                            },
                            sort_keys=True,
                            default=str,
                        )
                    )
                else:
                    print(
                        "[AION-PROFIT-DISCOVERY-SOFT-PENALTY] "
                        + json.dumps(
                            {
                                "batch_index": batch_index,
                                "candidate_index": candidate_index,
                                "ticker": ticker,
                                "tier": tier,
                                "soft_penalties": metrics.get("soft_penalties"),
                                "spread_penalty": metrics.get("spread_penalty"),
                                "liquidity_penalty": metrics.get("liquidity_penalty"),
                                "volatility_penalty": metrics.get("volatility_penalty"),
                                "family_quality_adjustment": metrics.get("family_quality_adjustment"),
                                "strict_would_reject": metrics.get("strict_would_reject"),
                            },
                            sort_keys=True,
                            default=str,
                        )
                    )
                    print(
                        "[AION-PROFIT-DISCOVERY-SIMULATED-ENTRY] "
                        + json.dumps(
                            {
                                "batch_index": batch_index,
                                "candidate_index": candidate_index,
                                "ticker": ticker,
                                "tier": tier,
                                "selected_side": frozen_trace.get("selected_quote", {}).get("selected_side"),
                                "selected_price_cents": frozen_trace.get("selected_quote", {}).get("selected_price_cents"),
                                "simulated_size": metrics.get("simulated_size"),
                                "simulated_entry_price": metrics.get("simulated_entry_price"),
                                "simulated_exit_policy": metrics.get("simulated_exit_policy"),
                                "expected_value": metrics.get("expected_value"),
                            },
                            sort_keys=True,
                            default=str,
                        )
                    )
                strict_live_would_reject = bool(metrics.get("strict_live_would_reject"))
                strict_result = "REJECT" if strict_live_would_reject else "ALLOW"
                relaxed_result = "NO_EXECUTE"
                submit_result = {"ok": False, "submitted": False, "reason": "hard_blocker"} if metrics.get("hard_block") else _simulate_submission(bot, selected_quote=quote, payload_override=submit_payload)
                relaxed_result = "EXECUTE" if bool(submit_result.get("ok") and submit_result.get("submitted")) else "NO_EXECUTE"
                print(
                    "[AION-PROFIT-DISCOVERY-SIMULATED-EXIT] "
                    + json.dumps(
                        {
                            "batch_index": batch_index,
                            "candidate_index": candidate_index,
                            "ticker": ticker,
                            "simulated_exit_policy": metrics.get("simulated_exit_policy"),
                            "simulated_expected_profit": metrics.get("simulated_expected_profit"),
                            "simulated_expected_loss": metrics.get("simulated_expected_loss"),
                            "submit_result_ok": bool(submit_result.get("ok")),
                            "submitted": bool(submit_result.get("submitted")),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                row = _profit_candidate_record(
                    candidate=candidate,
                    frozen_trace=frozen_trace,
                    metrics=metrics,
                    tier=tier,
                    batch_index=batch_index,
                    candidate_index=candidate_index,
                    submit_result=submit_result,
                    recovered=recovered,
                    recovered_side=recovered_side,
                )
                row["strict_live_result"] = strict_result
                row["strict_live_would_reject"] = strict_live_would_reject
                row["relaxed_live_result"] = relaxed_result
                row["profitability_tier"] = tier
                row["strict_live_reject_reason"] = "strict_rule_reject" if strict_live_would_reject else None
                rows.append(row)
                if strict_live_would_reject:
                    strict_rejected += 1
                if relaxed_result == "EXECUTE":
                    executed += 1
                print(
                    "[AION-PROFIT-DISCOVERY-RESULT] "
                    + json.dumps(
                        {
                            "batch_index": batch_index,
                            "candidate_index": candidate_index,
                            "ticker": ticker,
                            "tier": tier,
                            "strict_live_result": strict_result,
                            "relaxed_live_result": relaxed_result,
                            "expected_value": row.get("expected_value"),
                            "simulated_expected_profit": row.get("expected_profit"),
                            "submit_ok": bool(submit_result.get("ok")),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
    summary, family_rankings, rule_profitability = _aggregate_profit_batches(rows)
    profitable_families = [
        str(row.get("family") or "UNKNOWN")
        for row in family_rankings
        if float(row.get("avg_expected_value") or 0.0) > 0.0
    ]
    dangerous_families = [
        str(row.get("family") or "UNKNOWN")
        for row in reversed(family_rankings)
        if float(row.get("avg_expected_value") or 0.0) < 0.0 or int(row.get("hard_block_count") or 0) > 0
    ]
    family_heatmap = {
        "families": family_rankings,
        "best_families": [str(row.get("family") or "UNKNOWN") for row in family_rankings[:10]],
        "worst_families": [str(row.get("family") or "UNKNOWN") for row in list(reversed(family_rankings))[:10]],
        "profitable_families": profitable_families[:25],
        "dangerous_families": dangerous_families[:25],
    }
    family_heatmap["generated_at"] = _now_iso()
    family_heatmap["live_policy"] = {
        "source": "simulator_inline_family_summary",
        "promote_families": profitable_families[:10],
        "avoid_families": dangerous_families[:10],
    }
    family_counts = Counter(str(row.get("family") or "UNKNOWN").strip().upper() or "UNKNOWN" for row in rows)
    category_counts = Counter(str(row.get("category") or row.get("market_type") or "unknown").strip().lower() or "unknown" for row in rows)
    top_family_share = round((max(family_counts.values()) / max(1, len(rows))), 4) if family_counts else 0.0
    family_diversity_score = round(
        min(100.0, max(0.0, 20.0 * min(len(family_counts), 10) + 10.0 * min(len(category_counts), 10) + (1.0 - top_family_share) * 50.0)),
        2,
    ) if rows else 0.0
    family_heatmap["family_diversity_score"] = family_diversity_score
    family_heatmap["families_sampled"] = sorted(family_counts.keys())
    family_heatmap["categories_sampled"] = sorted(category_counts.keys())
    family_heatmap["top_family_share"] = top_family_share
    family_heatmap["diversity_sufficient_for_live_promotion"] = bool(len(family_counts) >= 8 and len(category_counts) >= 3 and top_family_share <= 0.15)
    family_heatmap["diversity_issue_reason"] = diversity_issue_reason
    latest_family_heatmap_exists = False
    summary.update(
        {
            "mode": "profit_discovery",
            "batches": batch_count,
            "sample_size": sample_count,
            "candidates_analyzed": len(rows),
            "candidates_target": batch_count * sample_count,
            "simulated_executions": executed,
            "strict_rejected_count": strict_rejected,
            "profitable_execution_rate": round(
                sum(1 for row in rows if float(row.get("expected_value") or 0.0) > 0.0 and str(row.get("relaxed_live_result") or "").upper() == "EXECUTE") / len(rows),
                4,
            )
            if rows
            else 0.0,
            "best_live_candidate_tiers": summary.get("best_live_candidate_tiers") or [],
            "profitable_loosened_rules": summary.get("profitable_loosened_rules") or [],
            "dangerous_loosened_rules": summary.get("dangerous_loosened_rules") or [],
            "recommended_live_policy": summary.get("recommended_live_policy") or {},
            "current_profitability_state": summary.get("current_profitability_state") or "balanced",
            "family_discovery_mode": bool(family_discovery_mode),
            "family_diversity_score": family_diversity_score,
            "families_sampled": sorted(family_counts.keys()),
            "categories_sampled": sorted(category_counts.keys()),
            "top_family_share": top_family_share,
            "latest_family_heatmap_exists": False,
            "diversity_issue_reason": diversity_issue_reason,
            "diversity_sufficient_for_live_promotion": bool(len(family_counts) >= 8 and len(category_counts) >= 3 and top_family_share <= 0.15),
        }
    )
    candidate_results = rows
    results_path = run_dir / "simulation_results.csv"
    candidate_results_path = run_dir / "candidate_results.csv"
    with results_path.open("w", encoding="utf-8", newline="") as fp:
        fieldnames = [
            "batch_index",
            "candidate_index",
            "ticker",
            "family",
            "market_type",
            "confidence_score",
            "expected_profit",
            "expected_loss",
            "expected_value",
            "simulated_fill_quality",
            "spread_cost",
            "liquidity_score",
            "volatility_score",
            "time_decay_risk",
            "orderbook_depth",
            "selected_side",
            "selected_price",
            "selected_price_cents",
            "simulated_size",
            "simulated_exit_policy",
            "simulated_hold_time",
            "simulated_slippage",
            "strict_live_result",
            "strict_live_would_reject",
            "relaxed_live_result",
            "profitability_tier",
            "loosened_rules_used",
            "exit_quality_projection",
            "profitability_signal",
        ]
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for item in candidate_results:
            row = {field: item.get(field) for field in fieldnames}
            row["loosened_rules_used"] = json.dumps(row.get("loosened_rules_used") or [])
            writer.writerow(row)
    shutil.copy2(results_path, candidate_results_path)
    family_rankings_path = run_dir / "family_rankings.json"
    family_heatmap_path = run_dir / "family_heatmap.json"
    rule_profitability_path = run_dir / "rule_profitability.json"
    candidate_tiers_path = run_dir / "candidate_tiers.json"
    live_policy_recommendations_path = run_dir / "live_policy_recommendations.json"
    summary_path = run_dir / "calibration_summary.json"
    profit_summary_text = [
        "AION PROFIT DISCOVERY",
        f"- candidates_analyzed: {summary.get('candidates_analyzed', 0)}",
        f"- execution_rate: {summary.get('execution_rate', 0.0)}",
        f"- profitable_execution_rate: {summary.get('profitable_execution_rate', 0.0)}",
        f"- tier_counts: {json.dumps(summary.get('tier_counts', {}), sort_keys=True)}",
        f"- total_expected_value: {summary.get('total_expected_value', 0.0)}",
        f"- best_families: {', '.join(summary.get('best_families', []) or ['none'])}",
        f"- worst_families: {', '.join(summary.get('worst_families', []) or ['none'])}",
        f"- profitable_loosened_rules: {', '.join(summary.get('profitable_loosened_rules', []) or ['none'])}",
        f"- dangerous_loosened_rules: {', '.join(summary.get('dangerous_loosened_rules', []) or ['none'])}",
        f"- current_profitability_state: {summary.get('current_profitability_state', 'balanced')}",
        f"- recommended_live_policy: {json.dumps(summary.get('recommended_live_policy', {}), sort_keys=True)}",
    ]
    safe_atomic_json_write(summary_path, summary, sort_keys=True, indent=2)
    safe_atomic_json_write(run_dir / "calibration_summary.json", summary, sort_keys=True, indent=2)
    safe_atomic_json_write(run_dir / "family_rankings.json", {"family_rankings": family_rankings}, sort_keys=True, indent=2)
    try:
        safe_atomic_json_write(family_heatmap_path, family_heatmap, sort_keys=True, indent=2)
    except Exception:
        safe_atomic_json_write(
            family_heatmap_path,
            {
                "families": family_heatmap.get("families", []),
                "best_families": family_heatmap.get("best_families", []),
                "worst_families": family_heatmap.get("worst_families", []),
                "profitable_families": family_heatmap.get("profitable_families", []),
                "dangerous_families": family_heatmap.get("dangerous_families", []),
                "generated_at": family_heatmap.get("generated_at"),
                "live_policy": family_heatmap.get("live_policy", {}),
            },
            sort_keys=True,
            indent=2,
        )
    safe_atomic_json_write(run_dir / "rule_profitability.json", {"rule_profitability": rule_profitability}, sort_keys=True, indent=2)
    safe_atomic_json_write(run_dir / "candidate_tiers.json", {"candidates": candidate_results}, sort_keys=True, indent=2)
    safe_atomic_json_write(run_dir / "live_policy_recommendations.json", summary.get("recommended_live_policy", {}), sort_keys=True, indent=2)
    (run_dir / "AION_FAMILY_PROFIT_REPORT.md").write_text(
        "\n".join(
            [
                "# AION FAMILY PROFIT REPORT",
                "",
                f"- mode: {'family_discovery' if family_discovery_mode else 'profit_discovery'}",
                f"- candidates_analyzed: {summary.get('candidates_analyzed', 0)}",
                f"- execution_rate: {summary.get('execution_rate', 0.0)}",
                f"- profitable_execution_rate: {summary.get('profitable_execution_rate', 0.0)}",
                f"- total_expected_value: {summary.get('total_expected_value', 0.0)}",
                f"- best_families: {', '.join(summary.get('best_families', []) or ['none'])}",
                f"- worst_families: {', '.join(summary.get('worst_families', []) or ['none'])}",
                f"- profitable_families: {', '.join(family_heatmap.get('profitable_families', []) or ['none'])}",
                f"- dangerous_families: {', '.join(family_heatmap.get('dangerous_families', []) or ['none'])}",
                "",
                "## Family Tiers",
            ]
            + [
                f"- {row.get('family')}: tier={row.get('tier')} score={row.get('score')} EV={row.get('expected_value')} confidence={row.get('confidence')} sample_count={row.get('sample_count')}"
                for row in family_heatmap.get("families", [])
                if isinstance(row, dict)
            ]
            + [
                "",
                "## Recommended Live Policy",
                json.dumps(family_heatmap.get("live_policy", {}), sort_keys=True, default=str),
                "",
            ]
        ),
        encoding="utf-8",
    )
    try:
        (REPO_ROOT / "AION_FAMILY_PROFIT_REPORT.md").write_text((run_dir / "AION_FAMILY_PROFIT_REPORT.md").read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
    (run_dir / "profit_summary.txt").write_text("\n".join(profit_summary_text) + "\n", encoding="utf-8")
    try:
        safe_atomic_json_write(_profit_engine_latest_candidates_path(), {"path": str(candidate_results_path)}, sort_keys=True, indent=2)
        safe_atomic_json_write(_profit_engine_latest_results_path(), {"path": str(results_path)}, sort_keys=True, indent=2)
        safe_atomic_json_write(_profit_engine_latest_family_path(), {"path": str(family_rankings_path)}, sort_keys=True, indent=2)
        safe_atomic_json_write(_profit_engine_latest_rules_path(), {"path": str(rule_profitability_path)}, sort_keys=True, indent=2)
        safe_atomic_json_write(PROFIT_ENGINE_ROOT / "latest_family_heatmap.json", family_heatmap, sort_keys=True, indent=2)
    except Exception:
        pass
    latest_family_heatmap_exists = bool((PROFIT_ENGINE_ROOT / "latest_family_heatmap.json").exists() or family_heatmap_path.exists())
    summary["latest_family_heatmap_exists"] = latest_family_heatmap_exists
    summary["family_diversity_score"] = family_diversity_score
    summary["families_sampled"] = sorted(family_counts.keys())
    summary["categories_sampled"] = sorted(category_counts.keys())
    summary["top_family_share"] = top_family_share
    summary["diversity_sufficient_for_live_promotion"] = bool(len(family_counts) >= 8 and len(category_counts) >= 3 and top_family_share <= 0.15)
    safe_atomic_json_write(summary_path, summary, sort_keys=True, indent=2)
    safe_atomic_json_write(_profit_engine_latest_summary_path(), summary, sort_keys=True, indent=2)
    print("[AION-PROFIT-DISCOVERY-SUMMARY] " + json.dumps(summary, sort_keys=True, default=str))
    return {
        "summary": summary,
        "results": candidate_results,
        "run_dir": str(run_dir),
        "candidate_count": len(candidate_results),
        "executed": executed,
        "hard_blocked": summary.get("hard_blocked_count", 0),
        "profit_discovery_pass": len(candidate_results) > 0,
        "family_rankings": family_rankings,
        "rule_profitability": rule_profitability,
        "family_heatmap": family_heatmap,
    }


def _paper30_candidate_prefilter(candidate: Dict[str, Any]) -> Tuple[bool, str]:
    candidate = candidate if isinstance(candidate, dict) else {}
    canonical_tradeability = candidate.get("canonical_tradeability") if isinstance(candidate.get("canonical_tradeability"), dict) else {}

    def _num(*keys, default=0.0):
        for key in keys:
            value = candidate.get(key)
            if value not in (None, "", [], {}):
                try:
                    return float(value)
                except Exception:
                    continue
            value = canonical_tradeability.get(key)
            if value not in (None, "", [], {}):
                try:
                    return float(value)
                except Exception:
                    continue
        return float(default)

    selected_price_cents = candidate.get("selected_price_cents")
    try:
        selected_price_cents = int(round(float(selected_price_cents))) if selected_price_cents not in (None, "", [], {}) else None
    except Exception:
        selected_price_cents = None
    selected_price = _num("selected_price", "selected_price_dollars")
    if selected_price_cents in {0, 100} or selected_price in {0.0, 1.0}:
        return False, "boundary_price"

    liquidity = _num("liquidity", "liquidity_score", "liquidity_depth")
    volume = _num("volume", "recent_volume", "volume_fp")
    orderbook_depth = _num("orderbook_depth", "yes_top_size", "no_top_size")
    if max(liquidity, volume, orderbook_depth) <= 0.0:
        return False, "no_liquidity_or_volume"

    yes_bid = _num("yes_bid", "yes_bid_dollars")
    yes_ask = _num("yes_ask", "yes_ask_dollars")
    no_bid = _num("no_bid", "no_bid_dollars")
    no_ask = _num("no_ask", "no_ask_dollars")
    if max(yes_bid, yes_ask, no_bid, no_ask) <= 0.0:
        return False, "no_executable_quote"

    spread_bucket = str(candidate.get("spread_bucket") or canonical_tradeability.get("spread_bucket") or candidate.get("spread_classification") or "unknown").strip().lower() or "unknown"
    edge_after_cost = _num("edge_after_cost", "final_edge", "sports_edge_after_cost")
    if spread_bucket in {"unsafe", "extreme_wide"} and edge_after_cost <= 0.0:
        return False, "wide_spread_no_edge"

    return True, "ok"


def _paper30_select_candidates(sample_size: int) -> List[Dict[str, Any]]:
    pool = _collect_recent_candidate_pool(limit=max(200, sample_size * 4))
    if not pool:
        return []
    filtered_pool: List[Dict[str, Any]] = []
    rejected_counts: Counter[str] = Counter()
    for candidate in pool:
        ok, reason = _paper30_candidate_prefilter(candidate)
        if ok:
            filtered_pool.append(candidate)
        else:
            rejected_counts[reason] += 1
    if filtered_pool:
        pool = filtered_pool
    random.shuffle(pool)
    families: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in pool:
        families.setdefault(_candidate_family_key(candidate), []).append(candidate)
    family_keys = list(families.keys())
    random.shuffle(family_keys)
    selected: List[Dict[str, Any]] = []
    family_index = 0
    while len(selected) < sample_size and families:
        family = family_keys[family_index % len(family_keys)]
        bucket = families.get(family, [])
        if not bucket:
            family_keys.remove(family)
            if not family_keys:
                break
            family_index = 0
            continue
        candidate = random.choice(bucket)
        selected.append(candidate)
        family_index += 1
    if len(selected) < sample_size:
        selected.extend(random.choices(pool, k=sample_size - len(selected)))
    return selected[:sample_size]


def run_paper30_lab(*, sample_size: int = 30, random_real_candidates: bool = True) -> Dict[str, Any]:
    _ensure_dirs()
    _ensure_simulation_dirs()
    run_dir = _ensure_paper30_dir()
    candidate_pool = _paper30_select_candidates(sample_size if random_real_candidates else min(sample_size, 1))
    if not candidate_pool:
        candidate_pool = []
    bot = _configure_simulation_bot(KalshiSniperBot())
    results: List[Dict[str, Any]] = []
    summary_counts = {"S": 0, "A": 0, "B": 0, "C": 0, "HARD BLOCK": 0}
    soft_reasons: Dict[str, int] = {}
    hard_reasons: Dict[str, int] = {}
    family_profit: Dict[str, float] = {}
    family_counts: Dict[str, int] = {}
    total_confidence = 0.0
    total_entry_price = 0.0
    total_spread_penalty = 0.0
    total_expected_profit = 0.0
    total_expected_loss = 0.0
    strict_rejected = 0
    executed = 0
    with io.StringIO() as stdout_buffer, redirect_stdout(_DedupingTee(sys.stdout, stdout_buffer, max_exact_repeats=2, always_allow_prefixes=profit_discovery_allow_prefixes if 'profit_discovery_allow_prefixes' in globals() else ("[AION-PAPER30-", "[AION-SIMULATION-"))):
        print("[AION-PAPER30-START] " + json.dumps({"sample_size": int(sample_size), "random_real_candidates": bool(random_real_candidates), "timestamp": _now_iso()}, sort_keys=True, default=str))
        for idx, candidate in enumerate(candidate_pool[:sample_size], start=1):
            quote = _candidate_to_quote(candidate)
            quote, candidate, recovered, recovered_side = _paper30_recover_selected_side(candidate, quote)
            ticker = str(candidate.get("ticker") or getattr(quote, "ticker", "") or "").strip() or None
            if not ticker:
                ticker = str(getattr(quote, "ticker", "") or "").strip() or None
            if ticker is None:
                ticker = f"UNKNOWN-{idx}"
            print("[AION-PAPER30-CANDIDATE] " + json.dumps({"index": idx, "ticker": ticker, "family": candidate.get("family"), "selected_side": candidate.get("selected_side"), "selected_price_cents": candidate.get("selected_price_cents"), "source_tag": candidate.get("source_tag"), "trace_id": candidate.get("trace_id")}, sort_keys=True, default=str))
            if recovered:
                print("[AION-SIDE-RECOVERY-APPLIED] " + json.dumps({"index": idx, "ticker": ticker, "recovered_side": recovered_side, "selected_price_cents": getattr(quote, "selected_price_cents", None), "selected_price": getattr(quote, "selected_price", None)}, sort_keys=True, default=str))
            frozen_trace = _build_frozen_trace(bot, quote, ticker)
            metrics = _paper30_strategy_metrics(candidate, frozen_trace)
            tier = _paper30_tier(metrics, frozen_trace)
            summary_counts[tier] = summary_counts.get(tier, 0) + 1
            print("[AION-PAPER30-TIER] " + json.dumps({"index": idx, "ticker": ticker, "tier": tier, "confidence_score": metrics.get("confidence_score"), "strict_would_reject": metrics.get("strict_would_reject"), "simulated_expected_profit": metrics.get("simulated_expected_profit"), "simulated_risk_reward": metrics.get("simulated_risk_reward")}, sort_keys=True, default=str))
            for reason in metrics.get("soft_penalties", []) or []:
                soft_reasons[reason] = soft_reasons.get(reason, 0) + 1
            if metrics.get("hard_block"):
                hard_reasons[str(frozen_trace.get("hard_block_reason") or candidate.get("blocker") or "hard_block")] = hard_reasons.get(str(frozen_trace.get("hard_block_reason") or candidate.get("blocker") or "hard_block"), 0) + 1
                print("[AION-PAPER30-HARD-BLOCK] " + json.dumps({"index": idx, "ticker": ticker, "blocker": frozen_trace.get("hard_block_reason") or candidate.get("blocker"), "canonical_execution_state": frozen_trace.get("canonical_execution_state"), "raw_candidate": candidate.get("raw_fields"), "reason": "hard_blocker"}, sort_keys=True, default=str))
                results.append({"index": idx, "ticker": ticker, "tier": tier, "hard_block": True, "metrics": metrics, "frozen_trace": frozen_trace})
                continue
            if metrics.get("strict_would_reject"):
                strict_rejected += 1
            print("[AION-PAPER30-SOFT-PENALTY] " + json.dumps({"index": idx, "ticker": ticker, "soft_penalties": metrics.get("soft_penalties"), "spread_penalty": metrics.get("spread_penalty"), "liquidity_penalty": metrics.get("liquidity_penalty"), "volatility_penalty": metrics.get("volatility_penalty"), "family_quality_adjustment": metrics.get("family_quality_adjustment"), "strict_would_reject": metrics.get("strict_would_reject")}, sort_keys=True, default=str))
            payload = frozen_trace.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            if tier in {"B", "C"} or metrics.get("confidence_score", 0.0) < 0.6:
                payload = dict(payload)
                payload["count"] = max(1, min(2, int(metrics.get("simulated_size") or 1)))
                payload["post_only"] = False
            print("[AION-PAPER30-SIMULATED-ENTRY] " + json.dumps({"index": idx, "ticker": ticker, "tier": tier, "selected_side": frozen_trace.get("selected_quote", {}).get("selected_side"), "selected_price_cents": frozen_trace.get("selected_quote", {}).get("selected_price_cents"), "simulated_size": metrics.get("simulated_size"), "simulated_entry_price": metrics.get("simulated_entry_price"), "simulated_exit_policy": metrics.get("simulated_exit_policy")}, sort_keys=True, default=str))
            submit_result = _simulate_submission(bot, selected_quote=quote, payload_override=payload)
            print("[AION-PAPER30-SIMULATED-EXIT] " + json.dumps({"index": idx, "ticker": ticker, "simulated_exit_policy": metrics.get("simulated_exit_policy"), "simulated_expected_profit": metrics.get("simulated_expected_profit"), "simulated_expected_loss": metrics.get("simulated_expected_loss"), "submit_result_ok": bool(submit_result.get("ok")), "submitted": bool(submit_result.get("submitted"))}, sort_keys=True, default=str))
            result = {
                "index": idx,
                "ticker": ticker,
                "family": candidate.get("family") or _candidate_family_key(candidate),
                "tier": tier,
                "strict_would_reject": bool(metrics.get("strict_would_reject")),
                "executed": True,
                "hard_block": False,
                "confidence_score": metrics.get("confidence_score"),
                "selected_side": frozen_trace.get("selected_quote", {}).get("selected_side"),
                "selected_price_cents": frozen_trace.get("selected_quote", {}).get("selected_price_cents"),
                "selected_price": frozen_trace.get("selected_quote", {}).get("selected_price"),
                "simulated_size": metrics.get("simulated_size"),
                "simulated_entry_price": metrics.get("simulated_entry_price"),
                "simulated_exit_policy": metrics.get("simulated_exit_policy"),
                "simulated_expected_profit": metrics.get("simulated_expected_profit"),
                "simulated_expected_loss": metrics.get("simulated_expected_loss"),
                "simulated_risk_reward": metrics.get("simulated_risk_reward"),
                "submit_result": submit_result,
                "frozen_trace": frozen_trace,
                "metrics": metrics,
            }
            results.append(result)
            executed += 1
            total_confidence += float(metrics.get("confidence_score") or 0.0)
            if metrics.get("simulated_entry_price") is not None:
                total_entry_price += float(metrics.get("simulated_entry_price") or 0.0)
            total_spread_penalty += float(metrics.get("spread_penalty") or 0.0)
            total_expected_profit += float(metrics.get("simulated_expected_profit") or 0.0)
            total_expected_loss += float(metrics.get("simulated_expected_loss") or 0.0)
            fam = result["family"]
            family_profit[fam] = family_profit.get(fam, 0.0) + float(metrics.get("simulated_expected_profit") or 0.0)
            family_counts[fam] = family_counts.get(fam, 0) + 1
            print("[AION-PAPER30-RESULT] " + json.dumps({"index": idx, "ticker": ticker, "tier": tier, "strict_would_reject": bool(metrics.get("strict_would_reject")), "executed": True, "simulated_expected_profit": metrics.get("simulated_expected_profit"), "submit_ok": bool(submit_result.get("ok"))}, sort_keys=True, default=str))
        candidate_count = len(candidate_pool[:sample_size])
        avg_confidence = round(total_confidence / executed, 4) if executed else 0.0
        avg_entry_price = round(total_entry_price / executed, 4) if executed else 0.0
        avg_spread_penalty = round(total_spread_penalty / executed, 4) if executed else 0.0
        summary = {
            "timestamp": _now_iso(),
            "mode": "aggressive_paper_trade",
            "candidates_sampled": candidate_count,
            "simulated_executions": executed,
            "hard_blocked_count": summary_counts.get("HARD BLOCK", 0),
            "soft_allowed_count": executed,
            "execution_rate": round(executed / candidate_count, 4) if candidate_count else 0.0,
            "hard_block_rate": round(summary_counts.get("HARD BLOCK", 0) / candidate_count, 4) if candidate_count else 0.0,
            "tier_counts": summary_counts,
            "average_confidence": avg_confidence,
            "average_entry_price": avg_entry_price,
            "average_spread_penalty": avg_spread_penalty,
            "total_simulated_expected_profit": round(total_expected_profit, 6),
            "total_simulated_expected_loss": round(total_expected_loss, 6),
            "strict_rejected_count": strict_rejected,
            "best_families": [fam for fam, _ in sorted(family_profit.items(), key=lambda item: item[1], reverse=True)[:5]],
            "worst_families": [fam for fam, _ in sorted(family_profit.items(), key=lambda item: item[1])[:5]],
            "top_fail_reasons": sorted(hard_reasons.items(), key=lambda item: item[1], reverse=True)[:10],
            "top_soft_penalties": sorted(soft_reasons.items(), key=lambda item: item[1], reverse=True)[:10],
            "top_loosened_rules": sorted(soft_reasons.items(), key=lambda item: item[1], reverse=True)[:10],
        }
        recommendations = [
            "demote spread_above_threshold / unsafe_spread to penalties in aggressive paper mode",
            "keep auth failure, duplicate position, malformed payload, invalid price, and dead selected-side book as hard blockers",
            "prioritize families with positive simulated_expected_profit and confidence_score >= 0.6",
            "keep reckless C-tier candidates paper-only until their exit policy is tightened",
        ]
        if summary_counts.get("HARD BLOCK", 0) > 0:
            recommendations.append("investigate hard-block sources separately; do not loosen live safety automatically")
        recommendation_text = "\n".join(f"- {line}" for line in recommendations)
        summary["recommended_live_policy"] = recommendations[0]
        summary["recommendations"] = recommendations
        safe_atomic_json_write(run_dir / "paper30_summary.json", summary, sort_keys=True, indent=2)
        safe_atomic_json_write(PAPER30_ROOT / "latest_summary.json", summary, sort_keys=True, indent=2)
        safe_atomic_json_write(run_dir / "paper30_candidates.json", candidate_pool[:sample_size], sort_keys=True, indent=2)
        safe_atomic_json_write(PAPER30_ROOT / "latest_candidates.json", candidate_pool[:sample_size], sort_keys=True, indent=2)
        with (run_dir / "paper30_results.csv").open("w", encoding="utf-8", newline="") as fp:
            fieldnames = [
                "index",
                "ticker",
                "family",
                "tier",
                "strict_would_reject",
                "executed",
                "hard_block",
                "confidence_score",
                "selected_side",
                "selected_price_cents",
                "selected_price",
                "simulated_size",
                "simulated_entry_price",
                "simulated_exit_policy",
                "simulated_expected_profit",
                "simulated_expected_loss",
                "simulated_risk_reward",
            ]
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            writer.writeheader()
            for item in results:
                writer.writerow({field: item.get(field) for field in fieldnames})
        (run_dir / "paper30_recommendations.txt").write_text(recommendation_text + "\n", encoding="utf-8")
        (PAPER30_ROOT / "latest_recommendations.txt").write_text(recommendation_text + "\n", encoding="utf-8")
        safe_atomic_json_write(_paper30_latest_results_path(), {"path": str(run_dir / "paper30_results.csv")}, sort_keys=True, indent=2)
        human_summary = "\n".join([
            "AION PAPER30 SUMMARY",
            f"- candidates_sampled: {summary.get('candidates_sampled', 0)}",
            f"- simulated_executions: {summary.get('simulated_executions', 0)}",
            f"- hard_blocked_count: {summary.get('hard_blocked_count', 0)}",
            f"- average_confidence: {summary.get('average_confidence', 0)}",
            f"- total_expected_profit: {summary.get('total_simulated_expected_profit', 0)}",
            f"- best_families: {', '.join(summary.get('best_families') or ['none'])}",
            f"- worst_families: {', '.join(summary.get('worst_families') or ['none'])}",
        ])
        flush_cycle_summary(component="profit_engine", summary=summary, human_summary=human_summary, codex_diagnostic=recommendation_text)
        emit_event("AION-PAPER30-SUMMARY", summary, component="profit_engine", stdout=False)
    return {
        "summary": summary,
        "results": results,
        "run_dir": str(run_dir),
        "candidate_count": candidate_count,
        "executed": executed,
        "hard_blocked": summary_counts.get("HARD BLOCK", 0),
        "paper30_pass": executed > 0 and summary_counts.get("HARD BLOCK", 0) < candidate_count,
    }


def run_simulation_cycle(*, max_attempts: int = 1, full_execution: bool = True, replay_issue: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _ensure_dirs()
    _ensure_simulation_dirs()
    incident_dir = INCIDENTS_ROOT / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{os.getpid()}"
    incident_dir.mkdir(parents=True, exist_ok=True)
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    tee_out = _Tee(sys.stdout, stdout_buffer)
    tee_err = _Tee(sys.stderr, stderr_buffer)
    selected_quote = None
    selected_ticker = None
    simulation_state: Dict[str, Any] = {
        "compile_ok": True,
        "candidate_frozen": False,
        "canonical_state_created": False,
        "decision": None,
        "selected_side_exists": False,
        "selected_price_cents_valid": False,
        "selected_side_has_price": False,
        "selected_side_has_liquidity": False,
        "payload_ready": False,
        "final_submit_allowed": False,
        "attempts": [],
        "final_submit_preflight_seen": False,
        "final_submit_call_seen": False,
        "order_submitted_seen": False,
        "position_confirmed_seen": False,
        "exit_handoff_ready": False,
        "simulation_failed_phase": None,
        "simulation_issue_count": 0,
        "simulation_proven_ticker": None,
        "relaxed_survivable_aggressive_regression": None,
        "simulation_pass": False,
        "sandbox_replay": bool(replay_issue),
        "sandbox_replay_issue": replay_issue if isinstance(replay_issue, dict) else None,
    }
    with redirect_stdout(tee_out), redirect_stderr(tee_err):
        print("[AION-SIMULATION-START] " + json.dumps({"timestamp": _now_iso(), "max_attempts": int(max_attempts), "full_execution": bool(full_execution), "sandbox_replay": bool(replay_issue)}, sort_keys=True, default=str))
        if replay_issue:
            emit_compact_event(
                "AION_SANDBOX_REPLAY_START",
                "sandbox replay started",
                {
                    "issue_type": replay_issue.get("issue_type"),
                    "failed_phase": replay_issue.get("failed_phase"),
                    "ticker": replay_issue.get("ticker"),
                    "blocker_label": replay_issue.get("blocker_label"),
                },
                component="simulator",
                actionable=True,
            )
        bot = KalshiSniperBot()
        bot.maybe_send_telegram_heartbeat = lambda *args, **kwargs: None  # type: ignore[assignment]
        bot.record_live_position = lambda *args, **kwargs: None  # type: ignore[assignment]
        bot.run_live_auth_get_smoke_test = lambda: {"ok": True, "reason": "simulated", "status_code": 200}  # type: ignore[assignment]
        bot.has_live_order_api_path = lambda: True  # type: ignore[assignment]
        bot.confirm_live_order_submit = lambda payload: True  # type: ignore[assignment]
        bot.check_live_duplicate_position_api = lambda ticker: {"blocked": False, "reason": "simulated", "position_size": 0}  # type: ignore[assignment]
        bot.get_live_submit_authorization = lambda payload: {  # type: ignore[assignment]
            "ok": True,
            "reason": "simulated",
            "flags": {
                "live_mode": True,
                "live_armed": True,
                "live_env_confirmed": True,
                "effective_live_dry_run": False,
            },
            "normal_live_service_mode": True,
            "authorized_canary_ticker": None,
            "supplied_ticker": payload.get("ticker") if isinstance(payload, dict) else None,
        }
        bot.check_live_exposure_limits = lambda ticker, family=None: (True, "simulated", {})  # type: ignore[assignment]
        bot._check_sports_correlated_entry_guard = lambda *args, **kwargs: (True, "simulated", {})  # type: ignore[assignment]
        bot._check_post_loss_reentry_block = lambda *args, **kwargs: (True, "simulated")  # type: ignore[assignment]
        bot.place_live_order = lambda payload: {  # type: ignore[assignment]
            "ok": True,
            "status_code": 200,
            "response": {"simulated": True, "order_id": f"SIM-{payload.get('ticker') or 'UNKNOWN'}"},
            "reason": "submitted",
        }
        bot.verify_live_order = lambda order_id: {  # type: ignore[assignment]
            "ok": True,
            "order": {"status": "executed", "order_id": order_id},
        }
        def _simulate_submit_live_order(q, size=None, lane=None, payload_override=None):  # type: ignore[assignment]
            payload = dict(payload_override) if isinstance(payload_override, dict) else bot.build_live_order_payload(q, size=size)
            if not isinstance(payload, dict):
                return {"ok": False, "reason": "simulation_invalid_payload", "submitted": False}
            ticker = getattr(q, "ticker", None) or payload.get("ticker")
            selected_price = payload.get("yes_price")
            if selected_price is None:
                selected_price = payload.get("no_price")
            print(
                "[AION-FINAL-SUBMIT-PREFLIGHT] "
                + json.dumps(
                    {
                        "ticker": ticker,
                        "side": payload.get("side"),
                        "action": payload.get("action"),
                        "price": selected_price,
                        "count": payload.get("count"),
                        "live_mode": True,
                        "arm_live": True,
                        "dry_run": False,
                        "duplicate_check_result": {"blocked": False, "reason": "simulated"},
                        "daily_trade_count": int(getattr(bot, "live_trade_count_today", 0) or 0),
                        "max_daily_trades": int(getattr(bot, "MAX_LIVE_TRADES_PER_DAY", AION_AGGRESSIVE_MAX_CONTRACTS) or AION_AGGRESSIVE_MAX_CONTRACTS),
                        "open_position_count": 0,
                        "reason": "all_gates_passed",
                        "blocker": None,
                        "order_payload_keys": sorted(list(payload.keys())),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            print(
                "[AION-FINAL-SUBMIT-CALL] "
                + json.dumps(
                    {
                        "ticker": ticker,
                        "side": payload.get("side"),
                        "action": payload.get("action"),
                        "price": selected_price,
                        "count": payload.get("count"),
                        "live_mode": True,
                        "arm_live": True,
                        "dry_run": False,
                        "duplicate_check_result": {"blocked": False, "reason": "simulated"},
                        "daily_trade_count": int(getattr(bot, "live_trade_count_today", 0) or 0),
                        "max_daily_trades": int(getattr(bot, "MAX_LIVE_TRADES_PER_DAY", AION_AGGRESSIVE_MAX_CONTRACTS) or AION_AGGRESSIVE_MAX_CONTRACTS),
                        "open_position_count": 0,
                        "reason": "submit_live_order",
                        "blocker": None,
                        "order_payload_keys": sorted(list(payload.keys())),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            print(
                "[LIVE-EXECUTION-ORDER-SUBMITTED] "
                + json.dumps(
                    {
                        "ticker": ticker,
                        "side": payload.get("side"),
                        "action": payload.get("action"),
                        "price": selected_price,
                        "count": payload.get("count"),
                        "submitted": True,
                        "simulated": True,
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            try:
                print(
                    "[AION-SIMULATION-POSITION-CONFIRMED] "
                    + json.dumps(
                        {
                            "ticker": ticker,
                            "side": payload.get("side"),
                            "order_id": f"SIM-{ticker or 'UNKNOWN'}",
                            "status": "executed",
                            "simulated": True,
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                print(
                    "[AION-SIMULATION-EXIT-HANDOFF] "
                    + json.dumps(
                        {
                            "ticker": ticker,
                            "side": payload.get("side"),
                            "exit_monitor_ready": True,
                            "simulated": True,
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
            except Exception:
                pass
            return {
                "ok": True,
                "status_code": 200,
                "response": {"simulated": True, "order_id": f"SIM-{ticker or 'UNKNOWN'}"},
                "reason": "submitted",
                "submitted": True,
                "payload": payload,
        }
        bot.submit_live_order = _simulate_submit_live_order  # type: ignore[assignment]
        regression = _run_relaxed_survivable_aggressive_regression(bot)
        simulation_state["relaxed_survivable_aggressive_regression"] = regression
        if not bool(regression.get("pass")):
            simulation_state["simulation_failed_phase"] = "relaxed_survivable_aggressive_regression"
            simulation_state["simulation_issue_count"] = int(simulation_state.get("simulation_issue_count") or 0) + 1
            issue = {
                "timestamp": _now_iso(),
                "issue_type": "simulation_failure",
                "ticker": regression.get("ticker") or None,
                "trace_id": None,
                "failed_phase": "relaxed_survivable_aggressive_regression",
                "blocker_label": "relaxed_survivable_aggressive_regression",
                "bug_type": (regression.get("error") or {}).get("type") if isinstance(regression.get("error"), dict) else None,
                "function": "_run_relaxed_survivable_aggressive_regression",
                "file": str(Path(__file__).resolve()),
                "raw_candidate": _serialize_quote(selected_quote) if selected_quote is not None else {},
                "canonical_execution_state": getattr(selected_quote, "canonical_execution_state", {}) if selected_quote is not None else {},
                "payload": {},
                "stacktrace": None,
                "suggested_fix": "stabilize relaxed_survivable_aggressive branch and preserve hard safety",
                "severity": "high",
                "repeat_count": _count_simulation_issue_repeats("simulation_failure", regression.get("ticker") or None, "relaxed_survivable_aggressive_regression") + 1,
            }
            _write_simulation_issue(issue)
            print(
                "[AION-SIMULATION-BUG-CAUGHT] "
                + json.dumps(
                    {
                        "bug_type": issue.get("bug_type") or issue.get("issue_type"),
                        "function": issue.get("function"),
                        "line": issue.get("stacktrace"),
                        "ticker": issue.get("ticker"),
                        "trace_id": issue.get("trace_id"),
                        "canonical_state": issue.get("canonical_execution_state"),
                        "raw_fields": issue.get("raw_candidate"),
                        "suspected_root_cause": issue.get("failed_phase"),
                        "suggested_patch_area": issue.get("suggested_fix"),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
        else:
            print("[AION-RELAXED-SURVIVABLE-REGRESSION-PASS] " + json.dumps(regression, sort_keys=True, default=str))
        for attempt in range(1, max(1, int(max_attempts)) + 1):
            attempt_payload = {"attempt": attempt, "timestamp": _now_iso()}
            print("[AION-SIMULATION-CANDIDATE] " + json.dumps(attempt_payload, sort_keys=True, default=str))
            live_run_error = None
            try:
                bot.run_once()
                selected_quote, live_summary, selected_ticker = _extract_selected_candidate(bot)
            except Exception as exc:
                live_run_error = {
                    "type": exc.__class__.__name__,
                    "reason": str(exc),
                }
                simulation_state["last_live_error"] = live_run_error
                print("[AION-SIMULATION-LIVE-ERROR] " + json.dumps(live_run_error, sort_keys=True, default=str))
                journal_quote, journal_payload, journal_ticker, journal_state = _load_latest_frozen_candidate_from_journal()
                if journal_quote is not None:
                    selected_quote = journal_quote
                    selected_ticker = journal_ticker
                    live_summary = {"source": "journal_backed", "journal_state": journal_state, "candidate": journal_payload}
                else:
                    selected_quote = None
                    selected_ticker = None
                    live_summary = {"source": "journal_backed", "journal_state": journal_state, "candidate": None}
            frozen_trace = _build_frozen_trace(bot, selected_quote, selected_ticker)
            payload = frozen_trace.get("payload")
            canonical = frozen_trace.get("canonical_execution_state") if isinstance(frozen_trace.get("canonical_execution_state"), dict) else {}
            simulation_state["candidate_frozen"] = bool(selected_quote is not None)
            simulation_state["canonical_state_created"] = bool(canonical)
            simulation_state["decision"] = frozen_trace.get("selected_quote", {}).get("decision") if isinstance(frozen_trace.get("selected_quote"), dict) else None
            simulation_state["selected_side_exists"] = bool(frozen_trace.get("selected_side_exists"))
            simulation_state["selected_price_cents_valid"] = bool(frozen_trace.get("selected_price_cents_valid"))
            simulation_state["selected_side_has_price"] = bool(frozen_trace.get("selected_side_has_price"))
            simulation_state["selected_side_has_liquidity"] = bool(frozen_trace.get("selected_side_has_liquidity"))
            simulation_state["payload_ready"] = bool(frozen_trace.get("payload_ready"))
            simulation_state["final_submit_allowed"] = bool(frozen_trace.get("final_submit_allowed"))
            simulation_state["simulation_proven_ticker"] = frozen_trace.get("ticker")
            print(
                "[AION-SIMULATION-CANDIDATE-FROZEN] "
                + json.dumps(
                    {
                        "ticker": frozen_trace.get("ticker"),
                        "selected_side": frozen_trace.get("selected_quote", {}).get("selected_side"),
                        "selected_price_cents": frozen_trace.get("selected_quote", {}).get("selected_price_cents"),
                        "final_submit_allowed": bool(frozen_trace.get("final_submit_allowed")),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            print(
                "[AION-SIMULATION-CANONICAL-STATE] "
                + json.dumps(
                    {
                        "ticker": frozen_trace.get("ticker"),
                        "canonical_execution_state": canonical,
                        "decision": canonical.get("canonical_execution_decision") if canonical else None,
                        "final_submit_allowed": bool(frozen_trace.get("final_submit_allowed")),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            print(
                "[AION-SIMULATION-PAYLOAD-BUILT] "
                + json.dumps(
                    {
                        "ticker": frozen_trace.get("ticker"),
                        "final_submit_allowed": bool(frozen_trace.get("final_submit_allowed")),
                        "payload_exists": payload is not None,
                        "selected_side_exists": bool(frozen_trace.get("selected_side_exists")),
                        "selected_price_cents_valid": bool(frozen_trace.get("selected_price_cents_valid")),
                        "hard_block_reason": frozen_trace.get("hard_block_reason"),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            tagged_events = _parse_tagged_events(stdout_buffer.getvalue())
            final_submit_preflight_seen = any(event["tag"] == "AION-FINAL-SUBMIT-PREFLIGHT" for event in tagged_events)
            final_submit_call_seen = any(event["tag"] == "AION-FINAL-SUBMIT-CALL" for event in tagged_events)
            order_submitted_seen = any(event["tag"] == "LIVE-EXECUTION-ORDER-SUBMITTED" for event in tagged_events)
            position_confirmed_seen = any(event["tag"] == "AION-SIMULATION-POSITION-CONFIRMED" for event in tagged_events)
            exit_handoff_ready = any(event["tag"] == "AION-SIMULATION-EXIT-HANDOFF" for event in tagged_events)
            submit_result = None
            if (
                selected_quote is not None
                and payload is not None
                and bool(frozen_trace.get("final_submit_allowed"))
                and not bool(frozen_trace.get("hard_block_reason"))
            ):
                print(
                    "[AION-SIMULATION-FINAL-PREFLIGHT] "
                    + json.dumps(
                        {
                            "ticker": frozen_trace.get("ticker"),
                            "selected_side": frozen_trace.get("selected_quote", {}).get("selected_side"),
                            "selected_price_cents": frozen_trace.get("selected_quote", {}).get("selected_price_cents"),
                            "final_submit_allowed": bool(frozen_trace.get("final_submit_allowed")),
                        },
                        sort_keys=True,
                        default=str,
                    )
                )
                try:
                    submit_result = bot.submit_live_order(
                        selected_quote,
                        size=AION_AGGRESSIVE_MAX_CONTRACTS,
                        lane="aggressive_positive_edge",
                        payload_override=payload,
                    )
                except Exception as exc:
                    submit_result = {
                        "ok": False,
                        "reason": f"simulation_submit_exception:{exc.__class__.__name__}",
                        "exception": str(exc),
                    }
                    print(
                        "[AION-SIMULATION-LIVE-ERROR] "
                        + json.dumps(
                            {
                                "type": exc.__class__.__name__,
                                "reason": str(exc),
                                "stage": "submit_live_order",
                            },
                            sort_keys=True,
                            default=str,
                        )
                    )
            tagged_events = _parse_tagged_events(stdout_buffer.getvalue())
            final_submit_preflight_seen = any(event["tag"] == "AION-FINAL-SUBMIT-PREFLIGHT" for event in tagged_events)
            final_submit_call_seen = any(event["tag"] == "AION-FINAL-SUBMIT-CALL" for event in tagged_events)
            order_submitted_seen = any(event["tag"] == "LIVE-EXECUTION-ORDER-SUBMITTED" for event in tagged_events)
            position_confirmed_seen = any(event["tag"] == "AION-SIMULATION-POSITION-CONFIRMED" for event in tagged_events)
            exit_handoff_ready = any(event["tag"] == "AION-SIMULATION-EXIT-HANDOFF" for event in tagged_events)
            simulation_state["attempts"].append(
                {
                    "attempt": attempt,
                    "selected_ticker": selected_ticker,
                    "live_select_summary": live_summary,
                    "frozen_trace": frozen_trace,
                    "live_run_error": live_run_error,
                    "payload_ready": bool(frozen_trace.get("payload_ready")),
                    "payload_exists": payload is not None,
                    "final_submit_allowed": bool(frozen_trace.get("final_submit_allowed")),
                    "final_submit_preflight_seen": final_submit_preflight_seen,
                    "final_submit_call_seen": final_submit_call_seen,
                    "order_submitted_seen": order_submitted_seen,
                    "position_confirmed_seen": position_confirmed_seen,
                    "exit_handoff_ready": exit_handoff_ready,
                    "hard_block_reason": frozen_trace.get("hard_block_reason"),
                    "submit_result": submit_result,
                }
            )
            simulation_state["final_submit_preflight_seen"] = final_submit_preflight_seen
            simulation_state["final_submit_call_seen"] = final_submit_call_seen
            simulation_state["order_submitted_seen"] = order_submitted_seen
            simulation_state["position_confirmed_seen"] = position_confirmed_seen
            simulation_state["exit_handoff_ready"] = exit_handoff_ready
            if (
                bool(frozen_trace.get("selected_side_exists"))
                and bool(frozen_trace.get("selected_price_cents_valid"))
                and bool(frozen_trace.get("canonical_execution_state"))
                and payload is not None
                and bool(frozen_trace.get("final_submit_allowed"))
                and not bool(frozen_trace.get("hard_block_reason"))
                and final_submit_preflight_seen
                and final_submit_call_seen
                and order_submitted_seen
                and position_confirmed_seen
                and exit_handoff_ready
            ):
                simulation_state["simulation_pass"] = True
                break
        tag = "AION-SIMULATION-PASS" if simulation_state["simulation_pass"] else "AION-SIMULATION-FAIL"
        print("[" + tag + "] " + json.dumps(simulation_state, sort_keys=True, default=str))
        if replay_issue:
            replay_result = {
                "issue_type": replay_issue.get("issue_type"),
                "failed_phase": replay_issue.get("failed_phase"),
                "ticker": replay_issue.get("ticker"),
                "simulation_pass": bool(simulation_state.get("simulation_pass")),
                "final_submit_preflight_seen": bool(simulation_state.get("final_submit_preflight_seen")),
                "final_submit_call_seen": bool(simulation_state.get("final_submit_call_seen")),
                "order_submitted_seen": bool(simulation_state.get("order_submitted_seen")),
                "position_confirmed_seen": bool(simulation_state.get("position_confirmed_seen")),
                "exit_handoff_ready": bool(simulation_state.get("exit_handoff_ready")),
            }
            emit_compact_event(
                "AION_SANDBOX_REPLAY_RESULT",
                "sandbox replay completed",
                replay_result,
                component="simulator",
                actionable=True,
            )
            emit_compact_event(
                "AION_SANDBOX_REPLAY_SAFE" if simulation_state.get("simulation_pass") else "AION_SANDBOX_REPLAY_FAILED",
                "sandbox replay safe" if simulation_state.get("simulation_pass") else "sandbox replay failed",
                replay_result,
                component="simulator",
                level="INFO" if simulation_state.get("simulation_pass") else "ERROR",
                actionable=True,
            )
    stdout_text = stdout_buffer.getvalue()
    stderr_text = stderr_buffer.getvalue()
    _write_trace_bundle(incident_dir, stdout_text=stdout_text, stderr_text=stderr_text, selected_quote=selected_quote, selected_ticker=selected_ticker, simulation_state=simulation_state)
    if not simulation_state.get("simulation_pass"):
        failed_phase = simulation_state.get("simulation_failed_phase") or (
            "final_submit_preflight" if simulation_state.get("final_submit_allowed") and not simulation_state.get("final_submit_preflight_seen")
            else "final_submit_call" if simulation_state.get("final_submit_preflight_seen") and not simulation_state.get("final_submit_call_seen")
            else "order_submitted" if simulation_state.get("final_submit_call_seen") and not simulation_state.get("order_submitted_seen")
            else "position_confirmation" if simulation_state.get("order_submitted_seen") and not simulation_state.get("position_confirmed_seen")
            else "exit_handoff" if simulation_state.get("position_confirmed_seen") and not simulation_state.get("exit_handoff_ready")
            else "candidate_discovery"
        )
        simulation_state["simulation_failed_phase"] = failed_phase
        issue_type = "simulation_failure"
        raw_candidate = _serialize_quote(selected_quote)
        issue = {
            "timestamp": _now_iso(),
            "issue_type": issue_type,
            "ticker": simulation_state.get("simulation_proven_ticker") or selected_ticker,
            "trace_id": raw_candidate.get("trace_id"),
            "failed_phase": failed_phase,
            "blocker_label": simulation_state.get("hard_block_reason") or failed_phase,
            "bug_type": (simulation_state.get("live_run_error") or {}).get("type") if isinstance(simulation_state.get("live_run_error"), dict) else None,
            "function": "run_simulation_cycle",
            "file": str(Path(__file__).resolve()),
            "raw_candidate": raw_candidate,
            "canonical_execution_state": raw_candidate.get("canonical_execution_state", {}) if isinstance(raw_candidate, dict) else {},
            "payload": payload if isinstance(payload, dict) else {},
            "stacktrace": stderr_text[-4000:] if stderr_text else None,
            "suggested_fix": "reconcile simulation proof lane with final_submit_allowed propagation" if failed_phase != "candidate_discovery" else "improve candidate discovery / frozen journal selection",
            "severity": "high" if failed_phase != "candidate_discovery" else "medium",
            "repeat_count": _count_simulation_issue_repeats(issue_type, selected_ticker or simulation_state.get("simulation_proven_ticker"), failed_phase) + 1,
        }
        _write_simulation_issue(issue)
        simulation_state["simulation_issue_count"] = int(issue.get("repeat_count") or 0)
        print(
            "[AION-SIMULATION-BUG-CAUGHT] "
            + json.dumps(
                {
                    "bug_type": issue.get("bug_type") or issue.get("issue_type"),
                    "function": issue.get("function"),
                    "line": issue.get("stacktrace"),
                    "ticker": issue.get("ticker"),
                    "trace_id": issue.get("trace_id"),
                    "canonical_state": issue.get("canonical_execution_state"),
                    "raw_fields": issue.get("raw_candidate"),
                    "suspected_root_cause": issue.get("failed_phase"),
                    "suggested_patch_area": issue.get("suggested_fix"),
                },
                sort_keys=True,
                default=str,
            )
        )
    safe_atomic_json_write(SIMULATION_LATEST_RUN_FILE, simulation_state, sort_keys=True, indent=2)
    flush_cycle_summary(
        component="simulator",
        summary=simulation_state,
        human_summary="\n".join([
            "AION SIMULATION SUMMARY",
            f"- simulation_pass: {simulation_state.get('simulation_pass')}",
            f"- ticker: {simulation_state.get('simulation_proven_ticker')}",
            f"- final_submit_preflight_seen: {simulation_state.get('final_submit_preflight_seen')}",
            f"- final_submit_call_seen: {simulation_state.get('final_submit_call_seen')}",
            f"- order_submitted_seen: {simulation_state.get('order_submitted_seen')}",
            f"- position_confirmed_seen: {simulation_state.get('position_confirmed_seen')}",
            f"- exit_handoff_ready: {simulation_state.get('exit_handoff_ready')}",
        ]),
        codex_diagnostic=json.dumps({
            "failed_phase": simulation_state.get("simulation_failed_phase"),
            "issue_count": simulation_state.get("simulation_issue_count"),
            "proven_ticker": simulation_state.get("simulation_proven_ticker"),
        }, sort_keys=True, default=str),
    )
    return simulation_state


def run_sandbox_replay(*, replay_issue: Optional[Dict[str, Any]] = None, max_attempts: int = 1) -> Dict[str, Any]:
    issue = replay_issue if isinstance(replay_issue, dict) else _load_latest_replay_issue()
    if not issue and SIMULATION_LATEST_RUN_FILE.exists():
        try:
            latest_run = _safe_json(SIMULATION_LATEST_RUN_FILE.read_text(encoding="utf-8"))
        except Exception:
            latest_run = {}
        if isinstance(latest_run, dict) and latest_run:
            issue = {
                "issue_type": "sandbox_replay_snapshot",
                "ticker": latest_run.get("simulation_proven_ticker"),
                "trace_id": latest_run.get("simulation_proven_ticker"),
                "failed_phase": latest_run.get("simulation_failed_phase") or "snapshot_replay",
                "blocker_label": latest_run.get("simulation_failed_phase") or "snapshot_replay",
                "severity": "info",
                "file": str(Path(__file__).resolve()),
                "function": "run_sandbox_replay",
                "raw_candidate": latest_run,
                "canonical_execution_state": latest_run.get("sandbox_replay_issue") or {},
                "payload": {},
                "stacktrace": None,
                "suggested_fix": "replay recent simulation state safely",
                "repeat_count": 1,
            }
    if not issue:
        emit_compact_event(
            "AION_SANDBOX_REPLAY_START",
            "sandbox replay requested but no replay issue was found",
            {"issue_found": False},
            component="simulator",
            level="WARN",
            actionable=True,
        )
        emit_compact_event(
            "AION_SANDBOX_REPLAY_FAILED",
            "sandbox replay failed because no issue was available",
            {"issue_found": False},
            component="simulator",
            level="ERROR",
            actionable=True,
        )
        return {"replay_pass": False, "reason": "missing_replay_issue", "issue_found": False}
    simulation_state = run_simulation_cycle(max_attempts=max(1, int(max_attempts or 1)), full_execution=True, replay_issue=issue)
    replay_summary = {
        "issue_type": issue.get("issue_type"),
        "ticker": issue.get("ticker"),
        "failed_phase": issue.get("failed_phase"),
        "simulation_pass": bool(simulation_state.get("simulation_pass")),
        "final_submit_preflight_seen": bool(simulation_state.get("final_submit_preflight_seen")),
        "final_submit_call_seen": bool(simulation_state.get("final_submit_call_seen")),
        "order_submitted_seen": bool(simulation_state.get("order_submitted_seen")),
        "position_confirmed_seen": bool(simulation_state.get("position_confirmed_seen")),
        "exit_handoff_ready": bool(simulation_state.get("exit_handoff_ready")),
    }
    return {"replay_pass": bool(simulation_state.get("simulation_pass")), "issue": issue, "simulation_state": simulation_state, "summary": replay_summary}


def main() -> int:
    parser = argparse.ArgumentParser(description="AION execution simulator")
    parser.add_argument("--max-attempts", type=int, default=int(os.environ.get("AION_SIMULATION_MAX_ATTEMPTS", "1") or 1))
    parser.add_argument("--full-execution", action="store_true", help="Run the full canonical simulation lifecycle.")
    parser.add_argument("--aggressive-paper-trade", action="store_true", help="Run the 30-candidate aggressive paper-trading lab.")
    parser.add_argument("--profit-discovery", action="store_true", help="Run the profit-calibrated discovery batches over real candidates.")
    parser.add_argument("--family-discovery-mode", action="store_true", help="Run diversity-enforced family discovery batches.")
    parser.add_argument("--force-family-diversity", action="store_true", help="Force the family diversity sampler even if discovery mode is otherwise disabled.")
    parser.add_argument("--replay-latest-issue", action="store_true", help="Replay the latest simulation issue safely without touching live trading.")
    parser.add_argument("--batches", type=int, default=int(os.environ.get("AION_PROFIT_DISCOVERY_BATCHES", "25") or 25), help="Number of batches for profit discovery mode.")
    parser.add_argument("--sample-size", type=int, default=30, help="Number of candidates to sample for paper-trading mode.")
    parser.add_argument("--random-real-candidates", action="store_true", help="Prefer random real candidates from recent journal/runtime traces.")
    args = parser.parse_args()
    if bool(args.replay_latest_issue):
        result = run_sandbox_replay(max_attempts=max(1, int(args.max_attempts or 1)))
        print(json.dumps(result, sort_keys=True, default=str))
        return 0 if result.get("replay_pass") else 1
    if bool(args.profit_discovery):
        result = run_profit_discovery(
            batches=max(1, int(args.batches or 25)),
            sample_size=max(1, int(args.sample_size or 30)),
            random_real_candidates=bool(args.random_real_candidates),
            family_discovery_mode=bool(args.family_discovery_mode),
            force_family_diversity=bool(args.force_family_diversity),
        )
        print(json.dumps(result, sort_keys=True, default=str))
        return 0 if result.get("profit_discovery_pass") else 1
    if bool(args.aggressive_paper_trade):
        result = run_paper30_lab(sample_size=max(1, int(args.sample_size or 30)), random_real_candidates=bool(args.random_real_candidates))
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    result = run_simulation_cycle(max_attempts=max(1, int(args.max_attempts or 1)), full_execution=bool(args.full_execution or True))
    print(json.dumps(result, sort_keys=True, default=str))
    return 0 if result.get("simulation_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())

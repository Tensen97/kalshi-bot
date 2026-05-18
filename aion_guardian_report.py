#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SERVICE_NAME = "kalshi-bot"
REPORT_COOLDOWN_SECONDS = 3600
LOOKBACK_MINUTES = 60
STATE_FILE_NAME = "aion_guardian_report_state.json"
LAST_REPORT_FILE_NAME = "aion_guardian_report_last.json"

PHASE55_BLOCKERS = {
    "trigger_wait",
    "pullback_wait",
    "immediate_reject",
    "insufficient_data",
    "probability_block",
    "capital_block",
    "psychology_block",
    "phase55_not_ready",
}

BODY_BLOCKERS = {
    "payload_missing",
    "missing_orderbook",
    "missing_price",
    "missing_side",
    "payload_builder_rejected",
    "order_submission_failed",
    "orderbook_fetch_failed",
    "api_error",
}

SENSES_BLOCKERS = {
    "quote_missing",
    "quote_mapping_blocked",
    "orderbook_fetch_failed",
    "empty_orderbook",
    "stale_orderbook",
    "ticker_key_mismatch",
    "orderbook_shape_unrecognized",
    "missing_orderbook",
}

SAFETY_BLOCKERS = {
    "duplicate_position",
    "cooldown_active",
    "unsafe_price",
    "unsafe_spread",
    "daily_trade_cap",
    "max_live_positions",
    "dry_run",
    "live_arm",
    "auth_not_ready",
    "live_trading_not_armed",
}


def _load_env_file(path: str) -> None:
    try:
        p = Path(path)
        if not p.exists():
            return
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def _runtime_state_path() -> str:
    candidates = [
        "/opt/kalshi-bot/runtime/state",
        str(Path(__file__).resolve().parent / "runtime" / "state"),
    ]
    for base in candidates:
        try:
            Path(base).mkdir(parents=True, exist_ok=True)
            return str(Path(base) / STATE_FILE_NAME)
        except Exception:
            continue
    return str(Path("/tmp") / STATE_FILE_NAME)


def _runtime_last_report_path() -> str:
    candidates = [
        "/opt/kalshi-bot/runtime/state",
        str(Path(__file__).resolve().parent / "runtime" / "state"),
    ]
    for base in candidates:
        try:
            Path(base).mkdir(parents=True, exist_ok=True)
            return str(Path(base) / LAST_REPORT_FILE_NAME)
        except Exception:
            continue
    return str(Path("/tmp") / LAST_REPORT_FILE_NAME)


def _safe_atomic_json_write(path: str, payload: dict) -> bool:
    try:
        from runtime.paths import safe_atomic_json_write as runtime_safe_atomic_json_write

        return bool(runtime_safe_atomic_json_write(path, payload))
    except Exception:
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            os.replace(tmp_path, path)
            return True
        except Exception:
            return False


def _telegram_config() -> Tuple[str, str]:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
    return token, chat_id


def _send_telegram_message(text: str) -> bool:
    token, chat_id = _telegram_config()
    if not token or not chat_id or not text:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3900]}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= getattr(resp, "status", 0) < 300
    except Exception:
        return False


def _journal_lines(minutes: int = LOOKBACK_MINUTES) -> List[str]:
    cmd = [
        "journalctl",
        "-u",
        SERVICE_NAME,
        "--since",
        f"{minutes} minutes ago",
        "--no-pager",
        "--all",
        "-o",
        "short-iso-precise",
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            return []
        return [line for line in (completed.stdout or "").splitlines() if line.strip()]
    except Exception:
        return []


def _extract_message(line: str) -> Tuple[Optional[str], str]:
    if not line:
        return None, ""
    parts = line.split(" ", 1)
    if len(parts) < 2:
        return None, line
    ts = parts[0].strip()
    remainder = parts[1].strip()
    message = remainder
    colon_idx = remainder.find(": ")
    if colon_idx != -1:
        message = remainder[colon_idx + 2 :].strip()
    return ts, message


def _extract_prefix_and_payload(message: str) -> Tuple[str, str]:
    if not message.startswith("["):
        return "", message
    end = message.find("]")
    if end == -1:
        return "", message
    prefix = message[1:end]
    rest = message[end + 1 :].strip()
    return prefix, rest


def _parse_inline_payload(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    if text.startswith("{") or text.startswith("["):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    result: Dict[str, Any] = {}
    for match in re.finditer(r'([A-Za-z0-9_]+)=((?:"[^"]*"|\'[^\']*\'|\[[^\]]*\]|\{[^{}]*\}|[^ ]+))', text):
        key = match.group(1)
        value = match.group(2)
        if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        result[key] = value
    return result


def _normalize_blocker_reason(reason: Any) -> str:
    text = str(reason or "").strip().lower()
    return text


def _classify_blocker(entry: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    prefix = str(entry.get("prefix") or "").strip()
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    reason = (
        payload.get("final_block_reason")
        or payload.get("payload_missing_reason")
        or payload.get("payload_rejection_reason")
        or payload.get("block_reason")
        or payload.get("reason")
        or payload.get("entry_ready_reason")
        or ""
    )
    reason = _normalize_blocker_reason(reason)
    ticker = str(payload.get("ticker") or payload.get("selected_ticker") or payload.get("raw_ticker") or "").strip() or None
    if reason in {"complete_current_quote", "partial_quote_complete"}:
        return None, reason, ticker
    if prefix == "AION-SPORTS-THRESHOLD-TRACE":
        threshold_pass = payload.get("threshold_pass")
        if str(threshold_pass).strip().lower() in {"true", "1", "yes"} or threshold_pass is True:
            return None, reason or "base_sports_threshold", ticker
        threshold_reason = _normalize_blocker_reason(payload.get("block_reason") or reason or "base_sports_threshold")
        return "mind", threshold_reason, ticker
    category = None
    if prefix.startswith("AION-PHASE55") or prefix == "PHASE55":
        category = "mind"
    elif prefix.startswith("AION-READINESS") or prefix.startswith("AION-FINAL"):
        category = "mind"
    elif prefix.startswith("AION-ORDERBOOK") or prefix.startswith("AION-QUOTE") or prefix.startswith("AION-PAYLOAD"):
        category = "body" if ("payload" in reason or "order" in reason or "missing" in reason) else "senses"
    elif prefix.startswith("LIVE-EXECUTION") or prefix.startswith("AION-LIVE-EXECUTION"):
        category = "body"
    elif reason in SAFETY_BLOCKERS or any(token in reason for token in ("duplicate", "cooldown", "unsafe", "armed", "live_arm", "auth_not_ready", "max_live_positions", "daily_trade_cap")):
        category = "safety"
    elif reason in BODY_BLOCKERS or reason.startswith("payload_missing:") or reason in {"payload_builder_rejected", "missing_orderbook", "missing_price", "missing_side"}:
        category = "body"
    elif reason in SENSES_BLOCKERS or any(token in reason for token in ("quote_", "orderbook_", "ticker_key_mismatch")):
        category = "senses"
    elif reason in PHASE55_BLOCKERS or reason.startswith("phase55_") or reason in {"normalized_ready=false", "entry_timing_wait", "trigger_wait", "pullback_wait"}:
        category = "mind"
    elif reason:
        category = "memory"
    return category, reason or None, ticker


def _parse_journal(minutes: int = LOOKBACK_MINUTES) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for line in _journal_lines(minutes=minutes):
        ts, message = _extract_message(line)
        if not message.startswith("["):
            continue
        prefix, rest = _extract_prefix_and_payload(message)
        if not prefix:
            continue
        payload = _parse_inline_payload(rest)
        entries.append({
            "ts": ts,
            "prefix": prefix,
            "payload": payload,
            "raw": message,
        })
    return entries


def _latest_matching(entries: List[Dict[str, Any]], prefixes: Iterable[str]) -> Optional[Dict[str, Any]]:
    prefix_set = tuple(prefixes)
    for entry in reversed(entries):
        if any(str(entry.get("prefix") or "").startswith(prefix) for prefix in prefix_set):
            return entry
    return None


def _count_matches(entries: List[Dict[str, Any]], prefixes: Iterable[str], predicate=None) -> int:
    prefix_set = tuple(prefixes)
    count = 0
    for entry in entries:
        if not any(str(entry.get("prefix") or "").startswith(prefix) for prefix in prefix_set):
            continue
        if predicate is None or predicate(entry):
            count += 1
    return count


def _last_timestamps(entries: List[Dict[str, Any]], predicates: Iterable[Tuple[str, callable]]) -> Dict[str, Optional[str]]:
    out = {}
    for key, pred in predicates:
        latest = None
        for entry in reversed(entries):
            if pred(entry):
                latest = entry.get("ts")
                break
        out[key] = latest
    return out


def _system_status() -> Dict[str, Any]:
    try:
        active = subprocess.run(["systemctl", "is-active", SERVICE_NAME], capture_output=True, text=True, check=False).stdout.strip()
    except Exception:
        active = "unknown"
    try:
        mainpid = subprocess.run(["systemctl", "show", SERVICE_NAME, "-p", "MainPID"], capture_output=True, text=True, check=False).stdout.strip().split("=", 1)[-1]
        mainpid = int(mainpid or 0)
    except Exception:
        mainpid = 0
    cmdline = None
    try:
        if mainpid > 0:
            cmdline = subprocess.run(["ps", "-p", str(mainpid), "-o", "cmd="], capture_output=True, text=True, check=False).stdout.strip() or None
    except Exception:
        cmdline = None
    live_mode = bool(cmdline and "--live" in cmdline)
    armed = bool(cmdline and "--arm-live" in cmdline)
    return {"active": active, "mainpid": mainpid, "cmdline": cmdline, "live_mode": live_mode, "armed": armed}


def _load_state(path: str) -> Dict[str, Any]:
    try:
        if Path(path).exists():
            with open(path, "r", encoding="utf-8") as handle:
                obj = json.load(handle)
                if isinstance(obj, dict):
                    return obj
    except Exception:
        pass
    return {}


def _store_state(path: str, state: Dict[str, Any]) -> None:
    try:
        _safe_atomic_json_write(path, state)
    except Exception:
        pass


def _summarize(entries: List[Dict[str, Any]], state: Dict[str, Any]) -> Dict[str, Any]:
    blocker_counts = Counter()
    blocker_tickers = Counter()
    blocker_categories = Counter()
    prefix_counts = Counter()
    event_counts = Counter()
    phase55_ready_count = 0
    payload_ready_count = 0
    live_exec_count = 0
    exit_count = 0
    candidate_tickers = set()
    final_candidate_tickers = set()
    payload_candidate_tickers = set()
    phase55_candidate_tickers = set()
    blockers_this_hour = set()
    heartbeat_ts = None
    heartbeat_prefixes = ("AION-TELEGRAM", "LIVE-MODE-STATUS", "AION-AGGRESSIVE-CANDIDATE-SEARCH-HEARTBEAT", "AION-OPEN-MARKET-SCAN")

    for entry in entries:
        prefix = str(entry.get("prefix") or "")
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        prefix_counts[prefix] += 1
        event_counts[prefix] += 1
        ticker = str(payload.get("ticker") or payload.get("selected_ticker") or payload.get("raw_ticker") or "").strip() or None
        if ticker:
            if any(token in prefix for token in ("CANDIDATE", "QUOTE", "PAYLOAD", "FINAL", "PHASE55", "SELECTOR")):
                candidate_tickers.add(ticker)
        category, reason, reason_ticker = _classify_blocker(entry)
        if reason and (category in {"mind", "body", "senses", "safety", "memory"}):
            blockers_this_hour.add(reason)
            blocker_counts[reason] += 1
            blocker_categories[category] += 1
            if reason_ticker:
                blocker_tickers[reason_ticker] += 1
        if prefix == "AION-FINAL-BLOCKER-TRACE":
            final_candidate_tickers.add(ticker or "")
        if prefix == "AION-ORDERBOOK-PAYLOAD-TRACE":
            if bool(payload.get("payload_ready")):
                payload_ready_count += 1
                if ticker:
                    payload_candidate_tickers.add(ticker)
        if prefix == "AION-FINAL-READINESS-SYNC":
            if bool(payload.get("phase55_ready")):
                phase55_ready_count += 1
                if ticker:
                    phase55_candidate_tickers.add(ticker)
        if prefix in {"AION-LIVE-EXECUTION", "LIVE-EXECUTION", "AION-OPEN-MARKET-EXECUTION"}:
            live_exec_count += 1
        if prefix in {"LIVE-EXIT", "AION-LIVE-EXIT", "LIVE-EXIT-ATTEMPT"}:
            exit_count += 1
        if any(prefix.startswith(h) for h in heartbeat_prefixes) or payload.get("heartbeat") is True:
            heartbeat_ts = entry.get("ts") or heartbeat_ts

    top_blockers = [reason for reason, _ in blocker_counts.most_common(5)]
    top_tickers = [ticker for ticker, _ in blocker_tickers.most_common(5)]
    historical_blockers = set(state.get("seen_blocker_types", []) or [])
    new_blocker_types = sorted(blockers_this_hour - historical_blockers)
    repeated_blocker_types = sorted(blockers_this_hour & historical_blockers)
    prev_counts = state.get("last_counts", {}) if isinstance(state.get("last_counts", {}), dict) else {}
    prev_blocker_total = int(prev_counts.get("blocker_total", 0) or 0)
    current_blocker_total = sum(blocker_counts.values())
    if prev_blocker_total:
        delta = current_blocker_total - prev_blocker_total
        if delta <= -max(1, int(prev_blocker_total * 0.1)):
            trend = "improving"
        elif delta >= max(1, int(prev_blocker_total * 0.1)):
            trend = "worsening"
        else:
            trend = "flat"
    else:
        trend = "first_sample"

    return {
        "blocker_counts": blocker_counts,
        "blocker_tickers": blocker_tickers,
        "blocker_categories": blocker_categories,
        "prefix_counts": prefix_counts,
        "blocker_total": current_blocker_total,
        "top_blockers": top_blockers,
        "top_tickers": top_tickers,
        "new_blocker_types": new_blocker_types,
        "repeated_blocker_types": repeated_blocker_types,
        "trend": trend,
        "candidate_tickers": candidate_tickers,
        "final_candidate_tickers": final_candidate_tickers,
        "payload_candidate_tickers": payload_candidate_tickers,
        "phase55_candidate_tickers": phase55_candidate_tickers,
        "payload_ready_count": payload_ready_count,
        "phase55_ready_count": phase55_ready_count,
        "live_exec_count": live_exec_count,
        "exit_count": exit_count,
        "heartbeat_ts": heartbeat_ts,
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y", "on", "ok", "ready", "passed", "pass", "executed", "submitted"}


def _extract_funnel_ticker(payload: Dict[str, Any]) -> Optional[str]:
    ticker = str(
        payload.get("ticker")
        or payload.get("selected_ticker")
        or payload.get("raw_ticker")
        or payload.get("normalized_ticker")
        or ""
    ).strip()
    return ticker or None


def _execution_funnel_summary(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    stage_rank_by_ticker: Dict[str, int] = {}
    stage_label_by_ticker: Dict[str, str] = {}
    stage_event_by_ticker: Dict[str, Dict[str, Any]] = {}
    blockers: Dict[str, Dict[str, Any]] = {"entry_ready": {}, "phase55_handoff": {}, "live_execution": {}}
    counts = {
        "quote_orderbook_ready": set(),
        "price_valid": set(),
        "payload_ready": set(),
        "entry_ready": set(),
        "phase55_handoff": set(),
        "live_execution_attempts": set(),
        "executed_orders": set(),
    }

    def _record_blocker(stage_key: str, entry: Dict[str, Any], ticker: str, reason: str, source: str) -> None:
        if not reason:
            return
        current = blockers.get(stage_key) or {}
        ts = entry.get("ts") or ""
        if not current or str(ts) >= str(current.get("ts") or ""):
            blockers[stage_key] = {
                "ticker": ticker,
                "reason": reason,
                "source": source,
                "trace": entry.get("raw") or "",
                "ts": ts,
            }

    def _advance_stage(ticker: str, rank: int, label: str, entry: Dict[str, Any]) -> None:
        current_rank = int(stage_rank_by_ticker.get(ticker, 0) or 0)
        if rank < current_rank:
            return
        if rank > current_rank or ticker not in stage_event_by_ticker:
            stage_rank_by_ticker[ticker] = rank
            stage_label_by_ticker[ticker] = label
            stage_event_by_ticker[ticker] = entry

    for entry in entries:
        prefix = str(entry.get("prefix") or "")
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        ticker = _extract_funnel_ticker(payload)
        if not ticker:
            continue
        raw_line = entry.get("raw") or ""

        if prefix == "AION-ORDERBOOK-PAYLOAD-TRACE":
            quote_ready = _truthy(payload.get("quote_ready"))
            orderbook_ready = _truthy(payload.get("orderbook_ready"))
            if quote_ready and orderbook_ready:
                counts["quote_orderbook_ready"].add(ticker)
                _advance_stage(ticker, 1, "quote_orderbook_ready", entry)
            else:
                reason = (
                    payload.get("orderbook_missing_reason")
                    or payload.get("payload_missing_reason")
                    or payload.get("payload_rejection_reason")
                    or ("quote_not_ready" if not quote_ready else "orderbook_not_ready")
                    or "quote_not_usable"
                )
                _record_blocker("entry_ready", entry, ticker, str(reason), prefix)
            if _truthy(payload.get("bounds_pass")):
                counts["price_valid"].add(ticker)
                _advance_stage(ticker, 2, "price_valid", entry)
            else:
                price_reason = (
                    payload.get("block_reason")
                    or payload.get("failure_stage")
                    or payload.get("orderbook_missing_reason")
                    or "selected_price_out_of_bounds"
                )
                _record_blocker("entry_ready", entry, ticker, str(price_reason), prefix)
            continue

        if prefix == "AION-PRICE-BOUNDS-TRACE":
            if _truthy(payload.get("bounds_pass")):
                counts["price_valid"].add(ticker)
                _advance_stage(ticker, 2, "price_valid", entry)
            else:
                reason = (
                    payload.get("block_reason")
                    or payload.get("failure_stage")
                    or "selected_price_out_of_bounds"
                )
                _record_blocker("entry_ready", entry, ticker, str(reason), prefix)
            continue

        if prefix == "AION-PAYLOAD-USABILITY-TRACE":
            if _truthy(payload.get("payload_ready")):
                counts["payload_ready"].add(ticker)
                _advance_stage(ticker, 3, "payload_ready", entry)
            else:
                reason = payload.get("payload_reject_reason") or payload.get("payload_missing_reason") or "payload_missing"
                _record_blocker("entry_ready", entry, ticker, str(reason), prefix)
            continue

        if prefix == "AION-ENTRY-READINESS":
            if _truthy(payload.get("entry_ready")):
                counts["entry_ready"].add(ticker)
                _advance_stage(ticker, 4, "entry_ready", entry)
            else:
                reason = payload.get("block_reason") or payload.get("entry_ready_reason") or "entry_not_ready"
                _record_blocker("entry_ready", entry, ticker, str(reason), prefix)
            continue

        if prefix == "AION-ENTRY-READINESS-BLOCKED":
            reason = payload.get("block_reason") or payload.get("entry_ready_reason") or payload.get("reason") or "entry_not_ready"
            _record_blocker("entry_ready", entry, ticker, str(reason), prefix)
            continue

        if prefix == "AION-PHASE55-HANDOFF":
            if _truthy(payload.get("phase55_handoff")):
                counts["phase55_handoff"].add(ticker)
                _advance_stage(ticker, 5, "phase55_handoff", entry)
            else:
                reason = payload.get("block_reason") or payload.get("reason") or "phase55_not_ready"
                _record_blocker("phase55_handoff", entry, ticker, str(reason), prefix)
            continue

        if prefix == "AION-PHASE55-HANDOFF-BLOCKED":
            reason = payload.get("block_reason") or payload.get("reason") or "phase55_not_ready"
            _record_blocker("phase55_handoff", entry, ticker, str(reason), prefix)
            continue

        if prefix == "AION-LIVE-PROMOTION":
            counts["phase55_handoff"].add(ticker)
            _advance_stage(ticker, 5, "live_promotion", entry)
            continue

        if prefix.startswith("LIVE-EXECUTION") or prefix.startswith("AION-LIVE-EXECUTION"):
            counts["live_execution_attempts"].add(ticker)
            _advance_stage(ticker, 6, "live_execution_attempt", entry)
            decision = str(payload.get("decision") or payload.get("status") or payload.get("result") or "").strip().upper()
            order_submitted = prefix.endswith("ORDER-SUBMITTED") or _truthy(payload.get("submitted")) or decision == "EXECUTED"
            if order_submitted:
                counts["executed_orders"].add(ticker)
                _advance_stage(ticker, 7, "executed", entry)
            else:
                reason = payload.get("reason") or payload.get("block_reason") or payload.get("final_block_reason") or decision or "live_execution_blocked"
                _record_blocker("live_execution", entry, ticker, str(reason), prefix)
            continue

        if prefix in {"AION-FINAL-BLOCKER-TRACE", "AION-FINAL-READY-EXPLAIN", "AION-FINAL-READINESS-SYNC"}:
            reason = (
                payload.get("final_block_reason")
                or payload.get("because")
                or payload.get("final_ready")
                or payload.get("final_blocker")
                or ""
            )
            if reason and str(reason).strip().lower() not in {"true", "false", "none"}:
                _record_blocker("live_execution", entry, ticker, str(reason), prefix)
            continue

    closest_ticker = None
    closest_rank = -1
    closest_event = None
    for ticker, rank in stage_rank_by_ticker.items():
        event = stage_event_by_ticker.get(ticker) or {}
        event_ts = str(event.get("ts") or "")
        closest_ts = str(closest_event.get("ts") or "") if closest_event else ""
        if rank > closest_rank or (rank == closest_rank and event_ts >= closest_ts):
            closest_ticker = ticker
            closest_rank = rank
            closest_event = event

    if closest_event:
        proof_trace = str(closest_event.get("raw") or "")
        proof_source = str(closest_event.get("prefix") or "")
    else:
        proof_trace = ""
        proof_source = ""

    def _blocker_for(stage_key: str) -> Dict[str, Any]:
        return blockers.get(stage_key) or {}

    return {
        "quote_orderbook_ready_count": len(counts["quote_orderbook_ready"]),
        "price_valid_count": len(counts["price_valid"]),
        "payload_ready_count": len(counts["payload_ready"]),
        "entry_ready_count": len(counts["entry_ready"]),
        "phase55_handoff_count": len(counts["phase55_handoff"]),
        "live_execution_attempt_count": len(counts["live_execution_attempts"]),
        "executed_orders_count": len(counts["executed_orders"]),
        "last_blocker_before_entry_ready": _blocker_for("entry_ready"),
        "last_blocker_before_phase55_handoff": _blocker_for("phase55_handoff"),
        "last_blocker_before_live_execution": _blocker_for("live_execution"),
        "closest_ticker": closest_ticker,
        "closest_rank": closest_rank if closest_rank >= 0 else None,
        "closest_source": proof_source,
        "closest_trace": proof_trace,
        "stage_rank_by_ticker": stage_rank_by_ticker,
        "stage_label_by_ticker": stage_label_by_ticker,
    }


def _build_report(summary: Dict[str, Any], system: Dict[str, Any], state: Dict[str, Any], entries: List[Dict[str, Any]]) -> str:
    blocker_counts: Counter = summary["blocker_counts"]
    blocker_tickers: Counter = summary["blocker_tickers"]
    blocker_categories: Counter = summary["blocker_categories"]
    prefix_counts: Counter = summary["prefix_counts"]
    top_blockers = summary["top_blockers"] or ["none"]
    top_tickers = summary["top_tickers"] or ["none"]
    new_blocker_types = summary["new_blocker_types"] or ["none"]
    repeated_blocker_types = summary["repeated_blocker_types"] or ["none"]
    total_candidates = len(summary["candidate_tickers"])
    live_exec_count = summary["live_exec_count"]
    exit_count = summary["exit_count"]
    payload_ready_count = summary["payload_ready_count"]
    phase55_ready_count = summary["phase55_ready_count"]
    blocker_total = summary["blocker_total"]
    trend = summary["trend"]
    last_hb = summary["heartbeat_ts"] or "unknown"
    funnel = _execution_funnel_summary(entries)
    funnel_counts = {
        "quote_orderbook_ready": funnel["quote_orderbook_ready_count"],
        "price_valid": funnel["price_valid_count"],
        "payload_ready": funnel["payload_ready_count"],
        "entry_ready": funnel["entry_ready_count"],
        "phase55_handoff": funnel["phase55_handoff_count"],
        "live_execution_attempts": funnel["live_execution_attempt_count"],
        "executed_orders": funnel["executed_orders_count"],
    }
    closest_ticker = funnel["closest_ticker"] or "none"
    closest_source = funnel["closest_source"] or "none"
    closest_trace = (funnel["closest_trace"] or "none").strip()
    entry_blocker = funnel["last_blocker_before_entry_ready"] or {}
    phase55_blocker = funnel["last_blocker_before_phase55_handoff"] or {}
    exec_blocker = funnel["last_blocker_before_live_execution"] or {}
    current_final_blocker = exec_blocker or phase55_blocker or entry_blocker or {}
    current_final_block_reason = current_final_blocker.get("reason") or "all_guards_passed"
    current_final_block_ticker = current_final_blocker.get("ticker") or closest_ticker

    critical_blockers = ", ".join(top_blockers[:3]) if top_blockers else "none"
    best_sign = (
        f"payload_ready={payload_ready_count}, phase55_ready={phase55_ready_count}, live_exec={live_exec_count}"
        if (payload_ready_count or phase55_ready_count or live_exec_count)
        else "no positive execution signals observed"
    )
    if top_blockers[0] == "payload_missing:missing_orderbook" or top_blockers[0] == "missing_orderbook":
        next_fix = "fix orderbook-to-payload resolution and shape normalization"
    elif any(t.startswith("phase55_") or t in PHASE55_BLOCKERS for t in top_blockers):
        next_fix = "fix Phase 5.5 handoff and entry-timing propagation"
    elif any("quote" in t or "orderbook" in t for t in top_blockers):
        next_fix = "fix quote/orderbook mapping and cache freshness"
    elif any("unsafe" in t or "duplicate" in t or "cooldown" in t for t in top_blockers):
        next_fix = "audit safety gating and blocker priority labeling"
    else:
        next_fix = "inspect the top blocker journal samples directly"

    service_status = system.get("active", "unknown")
    mode = "LIVE" if system.get("live_mode") else "SIM"
    report = [
        "🛡️ AION GUARDIAN — Hourly System Report",
        "",
        "Status:",
        f"- Service: {service_status}",
        f"- Mode: {mode}",
        f"- Live executions: {live_exec_count}",
        f"- Exits: {exit_count}",
        f"- Critical blockers: {critical_blockers}",
        "",
        "🧠 Mind:",
        f"- Phase 5.5 blockers: {', '.join([b for b in top_blockers if b.startswith('phase55') or b in PHASE55_BLOCKERS] ) or 'none'}",
        f"- trigger_wait: {blocker_counts.get('trigger_wait', 0)}",
        f"- pullback_wait: {blocker_counts.get('pullback_wait', 0)}",
        f"- immediate_reject: {blocker_counts.get('immediate_reject', 0)}",
        f"- insufficient_data: {blocker_counts.get('insufficient_data', 0)}",
        f"- probability_block: {blocker_counts.get('probability_block', 0)}",
        f"- capital_block: {blocker_counts.get('capital_block', 0)}",
        f"- psychology_block: {blocker_counts.get('psychology_block', 0)}",
        f"- edge_below_threshold: {blocker_counts.get('edge_below_threshold', 0)}",
        f"- normalized_ready=false: {blocker_counts.get('normalized_ready=false', 0)}",
        f"- base_sports_threshold: {blocker_counts.get('base_sports_threshold', 0)}",
        "",
        "🫀 Body:",
        f"- payload_missing: {sum(v for k, v in blocker_counts.items() if str(k).startswith('payload_missing'))}",
        f"- missing_orderbook: {blocker_counts.get('missing_orderbook', 0)}",
        f"- missing_price: {blocker_counts.get('missing_price', 0)}",
        f"- missing_side: {blocker_counts.get('missing_side', 0)}",
        f"- payload_builder_rejected: {blocker_counts.get('payload_builder_rejected', 0)}",
        f"- order submission failures: {sum(v for k, v in blocker_counts.items() if 'submission' in str(k) or 'submit' in str(k))}",
        f"- API errors: {sum(v for k, v in blocker_counts.items() if 'api' in str(k) or 'fetch_failed' in str(k))}",
        f"- live execution blocks: {sum(v for k, v in blocker_counts.items() if 'live' in str(k) or 'execution' in str(k))}",
        "",
        "👁️ Senses:",
        f"- quote_missing: {blocker_counts.get('quote_missing', 0)}",
        f"- quote_mapping_blocked: {prefix_counts.get('AION-QUOTE-MAPPING-BLOCKED', 0)}",
        f"- incomplete_quote_fields*: {sum(v for k, v in blocker_counts.items() if str(k).startswith('incomplete_quote_fields'))}",
        f"- partial_quote_complete: {sum(v for k, v in blocker_counts.items() if str(k) == 'partial_quote_complete' or str(k).startswith('partial_quote_complete:'))}",
        f"- orderbook_fetch_failed: {blocker_counts.get('orderbook_fetch_failed', 0)}",
        f"- empty_orderbook: {blocker_counts.get('empty_orderbook', 0)}",
        f"- stale_orderbook: {blocker_counts.get('stale_orderbook', 0)}",
        f"- ticker_key_mismatch: {blocker_counts.get('ticker_key_mismatch', 0)}",
        f"- orderbook_shape_unrecognized: {blocker_counts.get('orderbook_shape_unrecognized', 0)}",
        f"- complete_current_quote: {blocker_counts.get('complete_current_quote', 0)}",
        "",
        "🛡️ Safety:",
        f"- duplicate_position: {blocker_counts.get('duplicate_position', 0)}",
        f"- cooldown_active: {blocker_counts.get('cooldown_active', 0)}",
        f"- unsafe_price: {blocker_counts.get('unsafe_price', 0)}",
        f"- unsafe spread: {blocker_counts.get('unsafe_spread', 0)}",
        f"- daily trade cap: {blocker_counts.get('daily_trade_cap', 0)}",
        f"- max live positions: {blocker_counts.get('max_live_positions', 0)}",
        f"- dry-run / live-arm state: {'live_armed' if system.get('armed') else 'not_armed'}",
        "",
        "📜 Memory:",
        f"- new blocker types seen this hour: {', '.join(new_blocker_types[:5])}",
        f"- repeated blocker types seen before: {', '.join(repeated_blocker_types[:5])}",
        f"- top 5 blocker reasons: {', '.join(top_blockers[:5])}",
        f"- top 5 affected tickers: {', '.join(top_tickers[:5])}",
        f"- blocker trend: {trend}",
        "",
        "⚔️ Execution Funnel",
        f"- Quote/Orderbook ready: {funnel_counts['quote_orderbook_ready']}",
        f"- Price valid: {funnel_counts['price_valid']}",
        f"- Payload ready: {funnel_counts['payload_ready']}",
        f"- Entry ready: {funnel_counts['entry_ready']}",
        f"- Phase 5.5 handoff: {funnel_counts['phase55_handoff']}",
        f"- Live execution attempts: {funnel_counts['live_execution_attempts']}",
        f"- Executed orders: {funnel_counts['executed_orders']}",
        f"- Last blocker before entry_ready: {entry_blocker.get('reason') or 'none'} ({entry_blocker.get('ticker') or 'none'})",
        f"- Last blocker before phase55_handoff: {phase55_blocker.get('reason') or 'none'} ({phase55_blocker.get('ticker') or 'none'})",
        f"- Last blocker before LIVE-EXECUTION: {exec_blocker.get('reason') or 'none'} ({exec_blocker.get('ticker') or 'none'})",
        f"- Closest ticker: {closest_ticker}",
        f"- Current final blocker: {current_final_block_reason} ({current_final_block_ticker})",
        f"- Proof: {closest_source} {closest_trace[:300] if len(closest_trace) > 300 else closest_trace}",
        "",
        "🔥 Top blocker:",
        f"- {top_blockers[0]}",
        "",
        "✅ Best sign:",
        f"- {best_sign}",
        "",
        "⚠️ Next recommended fix:",
        f"- {next_fix}",
        "",
        "Health:",
        f"- Service active/running: {service_status}",
        f"- Last heartbeat time: {last_hb}",
        f"- Live mode status: {mode}",
        f"- Candidates scanned: {total_candidates}",
        f"- Final candidates: {len(summary['final_candidate_tickers'])}",
        f"- Payload-ready candidates: {payload_ready_count}",
        f"- Phase 5.5-ready candidates: {phase55_ready_count}",
        f"- Telegram status: {'configured' if _telegram_config()[0] and _telegram_config()[1] else 'missing'}",
    ]
    return "\n".join(report).strip() + "\n"


def _can_send_now(state: Dict[str, Any], force: bool) -> Tuple[bool, str]:
    if force:
        return True, "forced"
    now = time.time()
    last_sent = float(state.get("last_sent_ts", 0.0) or 0.0)
    if (now - last_sent) < REPORT_COOLDOWN_SECONDS:
        remaining = int(REPORT_COOLDOWN_SECONDS - (now - last_sent))
        return False, f"cooldown_active:{remaining}"
    return True, "ok"


def build_and_send_report(*, force: bool = False, dry_run: bool = False) -> int:
    _load_env_file("/opt/kalshi-bot/.env")
    state_path = _runtime_state_path()
    last_report_path = _runtime_last_report_path()
    state = _load_state(state_path)
    can_send, send_gate_reason = _can_send_now(state, force=force)
    entries = _parse_journal(minutes=LOOKBACK_MINUTES)
    system = _system_status()
    summary = _summarize(entries, state)
    report = _build_report(summary, system, state, entries)
    funnel = _execution_funnel_summary(entries)
    report_payload = {
        "created_at": time.time(),
        "service": SERVICE_NAME,
        "system": system,
        "summary": {
            "blocker_total": summary["blocker_total"],
            "top_blockers": summary["top_blockers"],
            "top_tickers": summary["top_tickers"],
            "trend": summary["trend"],
            "payload_ready_count": summary["payload_ready_count"],
            "phase55_ready_count": summary["phase55_ready_count"],
            "live_exec_count": summary["live_exec_count"],
            "exit_count": summary["exit_count"],
            "execution_funnel": {
                "quote_orderbook_ready": funnel["quote_orderbook_ready_count"],
                "price_valid": funnel["price_valid_count"],
                "payload_ready": funnel["payload_ready_count"],
                "entry_ready": funnel["entry_ready_count"],
                "phase55_handoff": funnel["phase55_handoff_count"],
                "live_execution_attempts": funnel["live_execution_attempt_count"],
                "executed_orders": funnel["executed_orders_count"],
                "closest_ticker": funnel["closest_ticker"] or "none",
                "current_final_block_reason": funnel["last_blocker_before_live_execution"].get("reason")
                or funnel["last_blocker_before_phase55_handoff"].get("reason")
                or funnel["last_blocker_before_entry_ready"].get("reason")
                or "all_guards_passed",
            },
        },
        "report": report,
    }
    print(
        "[AION-GUARDIAN-REPORT-BUILD] "
        + json.dumps(
            {
                "service": SERVICE_NAME,
                "lookback_minutes": LOOKBACK_MINUTES,
                "report_cooldown_seconds": REPORT_COOLDOWN_SECONDS,
                "send_gate_reason": send_gate_reason,
                "blocker_total": summary["blocker_total"],
                "payload_ready_count": summary["payload_ready_count"],
                "phase55_ready_count": summary["phase55_ready_count"],
                "live_exec_count": summary["live_exec_count"],
                "exit_count": summary["exit_count"],
                "top_blockers": summary["top_blockers"][:5],
                "top_tickers": summary["top_tickers"][:5],
                "trend": summary["trend"],
            },
            sort_keys=True,
            default=str,
        )
    )
    if dry_run:
        print(report)
        _safe_atomic_json_write(last_report_path, report_payload)
        return 0
    if not can_send:
        print(
            "[AION-GUARDIAN-REPORT-ERROR] "
            + json.dumps(
                {
                    "reason": send_gate_reason,
                    "message": "report suppressed by hourly cooldown",
                },
                sort_keys=True,
                default=str,
            )
        )
        return 0
    sent = _send_telegram_message(report)
    if sent:
        state["last_sent_ts"] = time.time()
        state["last_sent_reason"] = send_gate_reason
        state["seen_blocker_types"] = sorted(set(state.get("seen_blocker_types", []) or []) | set(summary["blocker_counts"].keys()))
        state["last_counts"] = {
            "blocker_total": summary["blocker_total"],
            "payload_ready_count": summary["payload_ready_count"],
            "phase55_ready_count": summary["phase55_ready_count"],
            "live_exec_count": summary["live_exec_count"],
            "exit_count": summary["exit_count"],
        }
        _store_state(state_path, state)
        _safe_atomic_json_write(last_report_path, report_payload)
        print(
            "[AION-GUARDIAN-REPORT-SENT] "
            + json.dumps(
                {
                    "telegram_configured": bool(_telegram_config()[0] and _telegram_config()[1]),
                    "blocker_total": summary["blocker_total"],
                    "top_blocker": summary["top_blockers"][0] if summary["top_blockers"] else "none",
                    "live_exec_count": summary["live_exec_count"],
                },
                sort_keys=True,
                default=str,
            )
        )
        return 0
    print(
        "[AION-GUARDIAN-REPORT-ERROR] "
        + json.dumps(
            {
                "reason": "telegram_send_failed",
                "telegram_configured": bool(_telegram_config()[0] and _telegram_config()[1]),
                "blocker_total": summary["blocker_total"],
            },
            sort_keys=True,
            default=str,
        )
    )
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    global LOOKBACK_MINUTES
    parser = argparse.ArgumentParser(description="AION Guardian hourly report agent")
    parser.add_argument("--send-now", action="store_true", help="Send the report immediately (ignores cooldown).")
    parser.add_argument("--dry-run", action="store_true", help="Print the report and persist state without sending Telegram.")
    parser.add_argument("--minutes", type=int, default=LOOKBACK_MINUTES, help="Lookback window in minutes.")
    args = parser.parse_args(argv)
    LOOKBACK_MINUTES = max(1, int(args.minutes or LOOKBACK_MINUTES))
    return build_and_send_report(force=bool(args.send_now), dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())

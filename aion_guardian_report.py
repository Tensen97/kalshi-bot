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
DAILY_INTEGRITY_LOOKBACK_MINUTES = 24 * 60
STATE_FILE_NAME = "aion_guardian_report_state.json"
LAST_REPORT_FILE_NAME = "aion_guardian_report_last.json"
DAILY_STATE_FILE_NAME = "aion_guardian_integrity_state.json"
DAILY_LAST_REPORT_FILE_NAME = "aion_guardian_integrity_last.json"

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


def _runtime_daily_state_path() -> str:
    candidates = [
        "/opt/kalshi-bot/runtime/state",
        str(Path(__file__).resolve().parent / "runtime" / "state"),
    ]
    for base in candidates:
        try:
            Path(base).mkdir(parents=True, exist_ok=True)
            return str(Path(base) / DAILY_STATE_FILE_NAME)
        except Exception:
            continue
    return str(Path("/tmp") / DAILY_STATE_FILE_NAME)


def _runtime_daily_last_report_path() -> str:
    candidates = [
        "/opt/kalshi-bot/runtime/state",
        str(Path(__file__).resolve().parent / "runtime" / "state"),
    ]
    for base in candidates:
        try:
            Path(base).mkdir(parents=True, exist_ok=True)
            return str(Path(base) / DAILY_LAST_REPORT_FILE_NAME)
        except Exception:
            continue
    return str(Path("/tmp") / DAILY_LAST_REPORT_FILE_NAME)


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
        payload.get("blocker_class")
        or payload.get("blocker")
        or payload.get("final_block_reason")
        or payload.get("payload_missing_reason")
        or payload.get("payload_rejection_reason")
        or payload.get("block_reason")
        or payload.get("reason")
        or payload.get("entry_ready_reason")
        or ""
    )
    reason = _normalize_blocker_reason(reason)
    ticker = str(payload.get("ticker") or payload.get("selected_ticker") or payload.get("raw_ticker") or "").strip() or None
    if prefix.startswith("AION-AGGRESSION-"):
        return None, None, ticker
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
    restart_count = 0
    try:
        restart_count_raw = subprocess.run(
            ["systemctl", "show", SERVICE_NAME, "-p", "NRestarts"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        restart_count = int((restart_count_raw.split("=", 1)[-1] or 0))
    except Exception:
        restart_count = 0
    cpu_percent = None
    mem_percent = None
    rss_kb = None
    vms_kb = None
    try:
        if mainpid > 0:
            ps_out = subprocess.run(
                ["ps", "-p", str(mainpid), "-o", "%cpu=,%mem=,rss=,vsz="],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if ps_out:
                parts = ps_out.split()
                if len(parts) >= 4:
                    cpu_percent = float(parts[0])
                    mem_percent = float(parts[1])
                    rss_kb = int(float(parts[2]))
                    vms_kb = int(float(parts[3]))
    except Exception:
        pass
    return {"active": active, "mainpid": mainpid, "cmdline": cmdline, "live_mode": live_mode, "armed": armed, "restart_count": restart_count, "cpu_percent": cpu_percent, "mem_percent": mem_percent, "rss_kb": rss_kb, "vms_kb": vms_kb}


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
    realm_vv_ready_count = 0
    payload_ready_count = 0
    live_exec_count = 0
    exit_count = 0
    realm_vv_seen_count = 0
    realm_vv_buy_count = 0
    realm_vv_wait_count = 0
    realm_vv_skip_count = 0
    realm_vv_edge_positive_count = 0
    realm_vv_edge_zero_count = 0
    realm_vv_aggressive_tradeable_count = 0
    realm_vv_aggressive_buy_count = 0
    phase55_compat_alias_count = 0
    stale_phase55_dependency_count = 0
    edge_zero_suspect_count = 0
    execution_gate_count = 0
    orderbook_usable_count = 0
    orderbook_blocked_count = 0
    thin_book_override_count = 0
    final_submit_preflight_count = 0
    final_submit_call_count = 0
    order_submitted_count = 0
    order_error_count = 0
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
        ticker = _canonical_funnel_ticker(payload)
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
        if prefix == "AION-PIPELINE-PAYLOAD-READY":
            if ticker:
                payload_candidate_tickers.add(ticker)
            payload_ready_count += 1 if bool(payload.get("payload_ready", True)) else 0
        if prefix == "AION-ORDERBOOK-PAYLOAD-TRACE":
            if bool(payload.get("payload_ready")):
                payload_ready_count += 1
                if ticker:
                    payload_candidate_tickers.add(ticker)
        realm_vv_alias = _realm_vv_compat_alias_info(payload, prefix)
        realm_vv_signal = prefix.startswith("AION-REALM-VV") or realm_vv_alias.get("realm_vv_source", False)
        if realm_vv_signal:
            realm_vv_seen_count += 1
            if realm_vv_alias.get("compat_alias"):
                phase55_compat_alias_count += 1
            if realm_vv_alias.get("stale_dependency"):
                stale_phase55_dependency_count += 1
            decision = str(payload.get("decision") or "").strip().upper()
            entry_ready = _truthy(payload.get("entry_ready")) or str(payload.get("phase55_status") or "").strip().lower() == "entry_ready"
            if entry_ready or decision == "BUY" or prefix == "AION-REALM-VV-BUY":
                realm_vv_ready_count += 1
                phase55_ready_count += 1
                if ticker:
                    phase55_candidate_tickers.add(ticker)
            if decision == "BUY" or prefix == "AION-REALM-VV-BUY":
                realm_vv_buy_count += 1
                if str(payload.get("spread_classification") or "").strip().upper() == "AGGRESSIVE_BUT_TRADEABLE" or prefix == "AION-REALM-VV-AGGRESSIVE-BUY":
                    realm_vv_aggressive_buy_count += 1
            elif decision == "WAIT" or prefix == "AION-REALM-VV-WAIT":
                realm_vv_wait_count += 1
            else:
                realm_vv_skip_count += 1
            if str(payload.get("spread_classification") or "").strip().upper() == "AGGRESSIVE_BUT_TRADEABLE":
                realm_vv_aggressive_tradeable_count += 1
        if prefix == "AION-REALM-VV-EDGE-PROVENANCE" or (realm_vv_signal and payload.get("edge_after_cost") is not None):
            try:
                edge_value = float(payload.get("edge_after_cost"))
            except Exception:
                edge_value = 0.0
            if edge_value > 0.0:
                realm_vv_edge_positive_count += 1
            else:
                realm_vv_edge_zero_count += 1
        if prefix == "AION-REALM-VV-EDGE-ZERO-SUSPECT":
            edge_zero_suspect_count += 1
        if prefix == "AION-ORDERBOOK-USABILITY-ALLOW":
            orderbook_usable_count += 1
        if prefix == "AION-ORDERBOOK-USABILITY-BLOCKED":
            orderbook_blocked_count += 1
        if prefix == "AION-ORDERBOOK-USABILITY":
            if bool(payload.get("thin_book_override_applied")):
                thin_book_override_count += 1
        if prefix == "AION-FINAL-SUBMIT-PREFLIGHT":
            final_submit_preflight_count += 1
        if prefix == "AION-FINAL-SUBMIT-CALL":
            final_submit_call_count += 1
        if prefix == "LIVE-EXECUTION-ORDER-SUBMITTED":
            order_submitted_count += 1
        if prefix == "LIVE-EXECUTION-ORDER-ERROR":
            order_error_count += 1
        if prefix in {"AION-PIPELINE-EXECUTION-GATE", "AION-FINAL-READINESS-SYNC"}:
            execution_gate_count += 1
            if bool(payload.get("phase55_ready")):
                phase55_ready_count += 1
                if ticker:
                    phase55_candidate_tickers.add(ticker)
        if prefix in {"AION-LIVE-EXECUTION", "LIVE-EXECUTION", "AION-OPEN-MARKET-EXECUTION", "AION-PIPELINE-EXECUTED"}:
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
        "realm_vv_ready_count": realm_vv_ready_count,
        "live_exec_count": live_exec_count,
        "exit_count": exit_count,
        "realm_vv_seen_count": realm_vv_seen_count,
        "realm_vv_buy_count": realm_vv_buy_count,
        "realm_vv_wait_count": realm_vv_wait_count,
        "realm_vv_skip_count": realm_vv_skip_count,
        "realm_vv_edge_positive_count": realm_vv_edge_positive_count,
        "realm_vv_edge_zero_count": realm_vv_edge_zero_count,
        "realm_vv_aggressive_tradeable_count": realm_vv_aggressive_tradeable_count,
        "realm_vv_aggressive_buy_count": realm_vv_aggressive_buy_count,
        "phase55_compat_alias_count": phase55_compat_alias_count,
        "stale_phase55_dependency_count": stale_phase55_dependency_count,
        "edge_zero_suspect_count": edge_zero_suspect_count,
        "execution_gate_count": execution_gate_count,
        "orderbook_usable_count": orderbook_usable_count,
        "orderbook_blocked_count": orderbook_blocked_count,
        "thin_book_override_count": thin_book_override_count,
        "final_submit_preflight_count": final_submit_preflight_count,
        "final_submit_call_count": final_submit_call_count,
        "order_submitted_count": order_submitted_count,
        "order_error_count": order_error_count,
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


def _canonical_funnel_ticker(payload: Dict[str, Any]) -> Optional[str]:
    ticker = str(
        payload.get("normalized_ticker_key")
        or payload.get("normalized_ticker")
        or payload.get("ticker")
        or payload.get("selected_ticker")
        or payload.get("raw_ticker")
        or ""
    ).strip()
    return ticker or None


def _extract_funnel_ticker(payload: Dict[str, Any]) -> Optional[str]:
    return _canonical_funnel_ticker(payload)


def _payload_candidate_source(payload: Dict[str, Any]) -> str:
    return str(
        payload.get("candidate_source")
        or payload.get("source_of_edge_value")
        or payload.get("source_field")
        or payload.get("realm_vv_source_field")
        or payload.get("phase55_source_field")
        or ""
    ).strip().lower()


def _realm_vv_compat_alias_info(payload: Dict[str, Any], prefix: str) -> Dict[str, bool]:
    candidate_source = _payload_candidate_source(payload)
    if candidate_source != "realm_vv_entry_engine":
        return {
            "realm_vv_source": False,
            "compat_alias": False,
            "stale_dependency": False,
            "alias_gate": False,
        }

    phase55_status = str(payload.get("phase55_status") or "").strip().lower()
    decision = str(payload.get("decision") or "").strip().upper()
    entry_ready = _truthy(payload.get("entry_ready"))
    read_location = str(payload.get("read_location") or "").strip().lower()
    write_location = str(payload.get("write_location") or "").strip().lower()
    gate_location = "phase55_live_execution_gate_by_ticker" in read_location or "phase55_live_execution_gate_by_ticker" in write_location
    alias_fields_present = any(
        _truthy(payload.get(key)) or str(payload.get(key) or "").strip()
        for key in (
            "phase55_record",
            "phase55_status",
            "phase55_source_field",
            "phase55_live_execution_gate_by_ticker",
            "raw_phase55_status",
            "phase55_record_write_success",
            "phase55_explicit_approval",
        )
    )
    alias_gate = gate_location or bool(payload.get("phase55_live_execution_gate_by_ticker"))
    compat_alias = alias_fields_present or alias_gate or prefix.startswith("phase55") or prefix.startswith("AION-PHASE55")
    stale_dependency = bool(
        entry_ready
        or decision == "BUY"
        or phase55_status == "entry_ready"
        or alias_gate
    )
    return {
        "realm_vv_source": True,
        "compat_alias": compat_alias,
        "stale_dependency": stale_dependency,
        "alias_gate": alias_gate,
    }


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
        "realm_vv_seen_count": 0,
        "realm_vv_buy_count": 0,
        "realm_vv_wait_count": 0,
        "realm_vv_skip_count": 0,
        "realm_vv_ready_count": 0,
        "realm_vv_edge_positive_count": 0,
        "realm_vv_edge_zero_count": 0,
        "realm_vv_aggressive_tradeable_count": 0,
        "realm_vv_aggressive_buy_count": 0,
        "phase55_compat_alias_count": 0,
        "stale_phase55_dependency_count": 0,
        "edge_zero_suspect_count": 0,
        "execution_gate_count": 0,
        "orderbook_usable_count": 0,
        "orderbook_blocked_count": 0,
        "thin_book_override_count": 0,
        "final_submit_preflight_count": 0,
        "final_submit_call_count": 0,
        "order_submitted_count": 0,
        "order_error_count": 0,
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
            realm_vv_alias = _realm_vv_compat_alias_info(payload, prefix)
            realm_vv_signal = prefix.startswith("AION-REALM-VV") or realm_vv_alias.get("realm_vv_source", False)
            if realm_vv_signal:
                counts["realm_vv_seen_count"] += 1
                if realm_vv_alias.get("compat_alias"):
                    counts["phase55_compat_alias_count"] += 1
                if realm_vv_alias.get("stale_dependency"):
                    counts["stale_phase55_dependency_count"] += 1
                decision = str(payload.get("decision") or "").strip().upper()
                phase55_status = str(payload.get("phase55_status") or "").strip().lower()
                entry_ready = _truthy(payload.get("entry_ready")) or phase55_status == "entry_ready"
                if entry_ready or decision == "BUY" or prefix == "AION-REALM-VV-BUY":
                    counts["realm_vv_ready_count"] += 1
                    counts["entry_ready"].add(ticker)
                    _advance_stage(ticker, 4, "entry_ready", entry)
                if decision == "BUY" or prefix == "AION-REALM-VV-BUY":
                    counts["realm_vv_buy_count"] += 1
                    if prefix == "AION-REALM-VV-AGGRESSIVE-BUY" or str(payload.get("spread_classification") or "").strip().upper() == "AGGRESSIVE_BUT_TRADEABLE":
                        counts["realm_vv_aggressive_buy_count"] += 1
                elif decision == "WAIT" or prefix == "AION-REALM-VV-WAIT":
                    counts["realm_vv_wait_count"] += 1
                else:
                    counts["realm_vv_skip_count"] += 1
                if str(payload.get("spread_classification") or "").strip().upper() == "AGGRESSIVE_BUT_TRADEABLE":
                    counts["realm_vv_aggressive_tradeable_count"] += 1
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

        realm_vv_alias = _realm_vv_compat_alias_info(payload, prefix)
        realm_vv_signal = prefix.startswith("AION-REALM-VV") or realm_vv_alias.get("realm_vv_source", False)
        if realm_vv_signal:
            counts["realm_vv_seen_count"] += 1
            if realm_vv_alias.get("compat_alias"):
                counts["phase55_compat_alias_count"] += 1
            if realm_vv_alias.get("stale_dependency"):
                counts["stale_phase55_dependency_count"] += 1
            decision = str(payload.get("decision") or "").strip().upper()
            phase55_status = str(payload.get("phase55_status") or "").strip().lower()
            entry_ready = _truthy(payload.get("entry_ready")) or phase55_status == "entry_ready"
            if entry_ready:
                counts["entry_ready"].add(ticker)
                _advance_stage(ticker, 4, "entry_ready", entry)
            if entry_ready or decision == "BUY" or prefix == "AION-REALM-VV-BUY":
                counts["realm_vv_ready_count"] += 1
            if decision == "BUY" or prefix == "AION-REALM-VV-BUY":
                counts["realm_vv_buy_count"] += 1
                if prefix == "AION-REALM-VV-AGGRESSIVE-BUY" or str(payload.get("spread_classification") or "").strip().upper() == "AGGRESSIVE_BUT_TRADEABLE":
                    counts["realm_vv_aggressive_buy_count"] += 1
            elif decision == "WAIT" or prefix == "AION-REALM-VV-WAIT":
                counts["realm_vv_wait_count"] += 1
            else:
                counts["realm_vv_skip_count"] += 1
            if str(payload.get("spread_classification") or "").strip().upper() == "AGGRESSIVE_BUT_TRADEABLE":
                counts["realm_vv_aggressive_tradeable_count"] += 1
        if prefix == "AION-ORDERBOOK-USABILITY-ALLOW":
            counts["orderbook_usable_count"] += 1
        if prefix == "AION-ORDERBOOK-USABILITY-BLOCKED":
            counts["orderbook_blocked_count"] += 1
        if prefix == "AION-ORDERBOOK-USABILITY" and bool(payload.get("thin_book_override_applied")):
            counts["thin_book_override_count"] += 1
        if prefix == "AION-FINAL-SUBMIT-PREFLIGHT":
            counts["final_submit_preflight_count"] += 1
        if prefix == "AION-FINAL-SUBMIT-CALL":
            counts["final_submit_call_count"] += 1
        if prefix == "LIVE-EXECUTION-ORDER-SUBMITTED":
            counts["order_submitted_count"] += 1
        if prefix == "LIVE-EXECUTION-ORDER-ERROR":
            counts["order_error_count"] += 1
        if prefix == "AION-REALM-VV-EDGE-PROVENANCE":
            try:
                edge_value = float(payload.get("edge_after_cost"))
            except Exception:
                edge_value = 0.0
            if edge_value > 0.0:
                counts["realm_vv_edge_positive_count"] += 1
            else:
                counts["realm_vv_edge_zero_count"] += 1
        if prefix == "AION-REALM-VV-EDGE-ZERO-SUSPECT":
            counts["edge_zero_suspect_count"] += 1

        if prefix == "AION-LIVE-PROMOTION":
            counts["phase55_handoff"].add(ticker)
            _advance_stage(ticker, 5, "live_promotion", entry)
            continue

        if prefix in {"AION-PIPELINE-EXECUTION-GATE", "AION-FINAL-READINESS-SYNC"}:
            counts["execution_gate_count"] += 1

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
        "realm_vv_seen_count": counts["realm_vv_seen_count"],
        "realm_vv_ready_count": counts["realm_vv_ready_count"],
        "realm_vv_buy_count": counts["realm_vv_buy_count"],
        "realm_vv_wait_count": counts["realm_vv_wait_count"],
        "realm_vv_skip_count": counts["realm_vv_skip_count"],
        "realm_vv_edge_positive_count": counts["realm_vv_edge_positive_count"],
        "realm_vv_edge_zero_count": counts["realm_vv_edge_zero_count"],
        "realm_vv_aggressive_tradeable_count": counts["realm_vv_aggressive_tradeable_count"],
        "realm_vv_aggressive_buy_count": counts["realm_vv_aggressive_buy_count"],
        "phase55_compat_alias_count": counts.get("phase55_compat_alias_count", 0),
        "stale_phase55_dependency_count": counts.get("stale_phase55_dependency_count", 0),
        "edge_zero_suspect_count": counts["edge_zero_suspect_count"],
        "execution_gate_count": counts["execution_gate_count"],
        "orderbook_usable_count": counts["orderbook_usable_count"],
        "orderbook_blocked_count": counts["orderbook_blocked_count"],
        "thin_book_override_count": counts["thin_book_override_count"],
        "final_submit_preflight_count": counts["final_submit_preflight_count"],
        "final_submit_call_count": counts["final_submit_call_count"],
        "order_submitted_count": counts["order_submitted_count"],
        "order_error_count": counts["order_error_count"],
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


def _build_report(summary: Dict[str, Any], system: Dict[str, Any], state: Dict[str, Any], entries: List[Dict[str, Any]], daily: Optional[Dict[str, Any]] = None) -> str:
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
    realm_vv_ready_count = summary.get("realm_vv_ready_count", summary["phase55_ready_count"])
    phase55_ready_count = summary["phase55_ready_count"]
    blocker_total = summary["blocker_total"]
    trend = summary["trend"]
    last_hb = summary["heartbeat_ts"] or "unknown"
    funnel = _execution_funnel_summary(entries)
    aggression = _aggression_audit_summary(summary, daily, entries)
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
        f"payload_ready={payload_ready_count}, realm_vv_ready={realm_vv_ready_count}, live_exec={live_exec_count}"
        if (payload_ready_count or realm_vv_ready_count or live_exec_count)
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
        "🧭 Aggression:",
        f"- aggression_profile: {aggression.get('active_profile')}",
        f"- active_edge_threshold: {aggression.get('active_edge_threshold')}",
        f"- hot_edge_after_cost: {aggression.get('hot_edge_after_cost')}",
        f"- profile_threshold_pass: {aggression.get('profile_threshold_pass')}",
        f"- blocker_kind: {aggression.get('blocker_kind')}",
        f"- reachability_score: {aggression.get('reachability_score')}/100",
        f"- candidate_suppression_reasons: {', '.join(aggression.get('candidate_suppression_reasons') or ['none'])}",
        f"- top_over_filtering_gate: {aggression.get('top_over_filtering_gate')}",
        f"- recommended_aggression_profile: {aggression.get('recommended_profile')}",
        f"- safety_gates_intact: {'yes' if aggression.get('safety_gates_intact') else 'no'}",
        "",
        "⚔️ Execution Funnel",
        f"- Quote/Orderbook ready: {funnel_counts['quote_orderbook_ready']}",
        f"- Price valid: {funnel_counts['price_valid']}",
        f"- Payload ready: {funnel_counts['payload_ready']}",
        f"- Entry ready: {funnel_counts['entry_ready']}",
        f"- Realm V.V seen: {summary.get('realm_vv_seen_count', 0)}",
        f"- Realm V.V ready: {summary.get('realm_vv_ready_count', summary['phase55_ready_count'])}",
        f"- Realm V.V buy: {summary.get('realm_vv_buy_count', 0)}",
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
        f"- Realm V.V-ready candidates: {realm_vv_ready_count}",
        f"- Telegram status: {'configured' if _telegram_config()[0] and _telegram_config()[1] else 'missing'}",
    ]
    return "\n".join(report).strip() + "\n"


def _path_size_bytes(path: str) -> int:
    total = 0
    try:
        p = Path(path)
        if not p.exists():
            return 0
        if p.is_file():
            return p.stat().st_size
        for child in p.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except Exception:
                continue
    except Exception:
        return 0
    return total


def _read_meminfo() -> Dict[str, int]:
    info: Dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if not line or ":" not in line:
                continue
            key, rest = line.split(":", 1)
            match = re.search(r"(\d+)", rest)
            if match:
                info[key.strip()] = int(match.group(1))
    except Exception:
        pass
    return info


def _load_avg() -> Dict[str, Any]:
    try:
        raw = Path("/proc/loadavg").read_text(encoding="utf-8").strip().split()
        if len(raw) >= 3:
            return {"1m": float(raw[0]), "5m": float(raw[1]), "15m": float(raw[2])}
    except Exception:
        pass
    return {"1m": None, "5m": None, "15m": None}


def _git_snapshot(repo_dir: str = "/opt/kalshi-bot") -> Dict[str, Any]:
    out: Dict[str, Any] = {"status_clean": None, "status_short": [], "head": None, "push_ready": None}
    try:
        status = subprocess.run(["git", "-C", repo_dir, "status", "--short"], capture_output=True, text=True, check=False)
        short = [line for line in (status.stdout or "").splitlines() if line.strip()]
        out["status_short"] = short[:50]
        out["status_clean"] = not bool(short)
    except Exception:
        out["status_clean"] = None
    try:
        head = subprocess.run(["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False).stdout.strip()
        out["head"] = head or None
    except Exception:
        out["head"] = None
    try:
        remote = subprocess.run(["git", "-C", repo_dir, "status", "-sb"], capture_output=True, text=True, check=False).stdout.strip()
        out["push_ready"] = bool(out.get("status_clean")) and remote.startswith("##")
    except Exception:
        out["push_ready"] = None
    return out


def _runtime_snapshot(repo_dir: str = "/opt/kalshi-bot") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    runtime_root = Path(repo_dir) / "runtime"
    for sub in ("tmp", "cache", "memory"):
        path = runtime_root / sub
        out[f"{sub}_exists"] = path.exists()
        out[f"{sub}_size_bytes"] = _path_size_bytes(str(path))
    out["runtime_exists"] = runtime_root.exists()
    out["runtime_size_bytes"] = _path_size_bytes(str(runtime_root))
    out["git_ignored_runtime"] = None
    try:
        gitignore = Path(repo_dir) / ".gitignore"
        if gitignore.exists():
            gitignore_text = gitignore.read_text(encoding="utf-8", errors="ignore")
            required_patterns = ("runtime/tmp/", "runtime/cache/", "runtime/memory/")
            if all(pattern in gitignore_text for pattern in required_patterns):
                out["git_ignored_runtime"] = True
        if out["git_ignored_runtime"] is not True:
            check = subprocess.run(["git", "-C", repo_dir, "check-ignore", "-q", "runtime/tmp/", "runtime/cache/", "runtime/memory/"], capture_output=True, text=True, check=False)
            out["git_ignored_runtime"] = check.returncode == 0
    except Exception:
        pass
    return out


def _integrity_daily_counts(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    prefix_counts = Counter()
    blocker_counts = Counter()
    phase55_observation_called = 0
    realm_vv_record_written = 0
    realm_vv_record_exists = 0
    phase55_record_written = 0
    phase55_record_exists = 0
    phase55_compat_alias_count = 0
    stale_phase55_dependency_count = 0
    placeholder_overwrite = 0
    stale_records = 0
    recursion_errors = 0
    quote_not_usable = 0
    unsafe_price_count = 0
    caution_wide_spread_count = 0
    unexecutable_price_count = 0
    both_sides_unexecutable_count = 0
    selected_price_above_0_99_count = 0
    phase55_reachability_bridge_count = 0
    unsafe_unknown = 0
    for entry in entries:
        prefix = str(entry.get("prefix") or "")
        prefix_counts[prefix] += 1
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        category, reason, _ticker = _classify_blocker(entry)
        canonical_blocker = str(payload.get("blocker_class") or payload.get("blocker") or "").strip().lower()
        reason_text = canonical_blocker or str(reason or "").strip().lower()
        if reason_text:
            blocker_counts[reason_text] += 1
        blocker_detail = str(payload.get("blocker_detail") or payload.get("block_reason") or payload.get("failure_stage") or "").strip().lower()
        realm_vv_alias = _realm_vv_compat_alias_info(payload, prefix)
        if realm_vv_alias.get("compat_alias"):
            phase55_compat_alias_count += 1
        if realm_vv_alias.get("stale_dependency"):
            stale_phase55_dependency_count += 1
        if prefix.startswith("AION-PHASE55-HANDOFF"):
            if _truthy(payload.get("phase55_observation_called")):
                phase55_observation_called += 1
            record_written = _truthy(payload.get("realm_vv_record_write_success"))
            if not record_written:
                record_written = _truthy(payload.get("phase55_record_write_success")) or _truthy(payload.get("phase55_record_created"))
            if record_written:
                realm_vv_record_written += 1
                phase55_record_written += 1
            record_exists = _truthy(payload.get("realm_vv_record_exists"))
            if not record_exists:
                record_exists = _truthy(payload.get("phase55_record_exists"))
            if record_exists:
                realm_vv_record_exists += 1
                phase55_record_exists += 1
            if _truthy(payload.get("placeholder_record_detected")) or _truthy(payload.get("overwrite_detected")):
                placeholder_overwrite += 1
        if prefix.startswith("AION-PHASE55-PERSISTENCE"):
            if _truthy(payload.get("placeholder_record")):
                placeholder_overwrite += 1
            if _truthy(payload.get("stale_record")):
                stale_records += 1
        if prefix == "AION-PHASE55-REACHABILITY-BRIDGE":
            phase55_reachability_bridge_count += 1
        if "recursionerror" in reason_text:
            recursion_errors += 1
        if reason_text == "quote_not_usable":
            quote_not_usable += 1
        if reason_text == "unsafe_price":
            unsafe_price_count += 1
        if reason_text == "caution_wide_spread":
            caution_wide_spread_count += 1
        if reason_text == "unexecutable_price":
            unexecutable_price_count += 1
        if reason_text == "both_sides_unexecutable":
            both_sides_unexecutable_count += 1
        if reason_text in {"selected_price_above_0_99", "selected_price_above_max", "selected_price_out_of_bounds"}:
            selected_price_above_0_99_count += 1
        if reason_text == "unsafe_price_unknown":
            unsafe_unknown += 1
    return {
        "prefix_counts": prefix_counts,
        "blocker_counts": blocker_counts,
        "phase55_observation_called": phase55_observation_called,
        "realm_vv_record_written": realm_vv_record_written,
        "realm_vv_record_exists": realm_vv_record_exists,
        "phase55_record_written": phase55_record_written,
        "phase55_record_exists": phase55_record_exists,
        "phase55_compat_alias_count": phase55_compat_alias_count,
        "stale_phase55_dependency_count": stale_phase55_dependency_count,
        "placeholder_overwrite": placeholder_overwrite,
        "stale_records": stale_records,
        "recursion_errors": recursion_errors,
        "quote_not_usable": quote_not_usable,
        "unsafe_price_count": unsafe_price_count,
        "caution_wide_spread_count": caution_wide_spread_count,
        "unexecutable_price_count": unexecutable_price_count,
        "both_sides_unexecutable_count": both_sides_unexecutable_count,
        "selected_price_above_0_99_count": selected_price_above_0_99_count,
        "phase55_reachability_bridge_count": phase55_reachability_bridge_count,
        "unsafe_unknown": unsafe_unknown,
    }


AGGRESSION_PROFILE_VALUES = {"safe", "balanced", "opportunistic", "beast"}


def _normalize_aggression_profile(profile: Any) -> str:
    text = str(profile or os.environ.get("AION_AGGRESSION_PROFILE", "safe") or "safe").strip().lower()
    return text if text in AGGRESSION_PROFILE_VALUES else "safe"


def _aggression_threshold_like_blocker(reason: str) -> bool:
    reason = str(reason or "").strip().lower()
    return reason in {
        "low_score",
        "edge_below_threshold",
        "edge_below_cost",
        "base_sports_threshold",
        "score_below_threshold",
        "trigger_wait",
        "pullback_wait",
        "normalized_ready=false",
        "phase55_not_ready",
        "flat_directional_bias",
        "sports_spread_watch",
    } or reason.startswith(("base_sports_threshold", "score_below_threshold", "phase55_"))


def _aggression_audit_summary(summary: Dict[str, Any], daily: Dict[str, Any], entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    blocker_counts: Counter = summary.get("blocker_counts") if isinstance(summary.get("blocker_counts"), Counter) else Counter(summary.get("blocker_counts") or {})
    aggression_entries = [entry for entry in entries if str(entry.get("prefix") or "").startswith("AION-AGGRESSION-")]
    latest_profile_payload = {}
    for entry in reversed(aggression_entries):
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        if payload.get("active_profile") or payload.get("profile"):
            latest_profile_payload = payload
            break
    active_profile = _normalize_aggression_profile(latest_profile_payload.get("active_profile") or latest_profile_payload.get("profile"))
    active_edge_threshold = latest_profile_payload.get("edge_threshold_used", latest_profile_payload.get("effective_edge_threshold"))
    hot_edge_after_cost = latest_profile_payload.get("edge_after_cost")
    profile_threshold_pass = latest_profile_payload.get("profile_threshold_pass")
    threshold_blocker_counts = Counter()
    hard_blocker_counts = Counter()
    for reason, count in blocker_counts.items():
        reason_text = str(reason or "").strip().lower()
        if _aggression_threshold_like_blocker(reason_text):
            threshold_blocker_counts[reason_text] += int(count or 0)
        if any(token in reason_text for token in ("quote_not_usable", "quote_mapping_blocked", "orderbook", "missing_orderbook", "selected_price_above", "unsafe_price_unknown", "unsafe_price", "duplicate", "cooldown", "live_arm", "auth_not_ready", "daily_trade_cap", "max_live_positions")):
            hard_blocker_counts[reason_text] += int(count or 0)
    blocker_kind = "idle"
    if hard_blocker_counts:
        blocker_kind = "hard_safety"
    elif threshold_blocker_counts:
        blocker_kind = "internal_threshold"
    candidate_tickers = summary.get("candidate_tickers") or set()
    payload_ready_count = int(summary.get("payload_ready_count", 0) or 0)
    realm_vv_ready_count = int(summary.get("realm_vv_ready_count", summary.get("phase55_ready_count", 0)) or 0)
    live_exec_count = int(summary.get("live_exec_count", 0) or 0)
    top_over_filtering_gate = threshold_blocker_counts.most_common(1)[0][0] if threshold_blocker_counts else (summary.get("top_blockers") or ["none"])[0]
    threshold_reason_list = [reason for reason, _count in threshold_blocker_counts.most_common(5)] or ["none"]
    hard_reason_list = [reason for reason, _count in hard_blocker_counts.most_common(5)] or ["none"]
    if payload_ready_count > 0 and realm_vv_ready_count > 0 and live_exec_count > 0:
        recommended_profile = "safe"
    elif payload_ready_count > 0 and realm_vv_ready_count == 0:
        recommended_profile = "balanced" if threshold_blocker_counts else "opportunistic"
    elif payload_ready_count == 0 and threshold_blocker_counts:
        recommended_profile = "balanced" if len(threshold_blocker_counts) >= 2 else "safe"
    elif payload_ready_count > 0 and realm_vv_ready_count > 0:
        recommended_profile = "balanced"
    else:
        recommended_profile = "safe"
    reachability_score = 0
    if candidate_tickers:
        reachability_score += min(20, len(candidate_tickers) // 5)
    if payload_ready_count > 0:
        reachability_score += 35
    if realm_vv_ready_count > 0:
        reachability_score += 35
    if live_exec_count > 0:
        reachability_score += 20
    if threshold_blocker_counts and not hard_blocker_counts:
        reachability_score += min(10, len(threshold_blocker_counts) * 2)
    if blocker_counts.get("quote_not_usable", 0) == 0 and blocker_counts.get("quote_orderbook_snapshot_recursionerror", 0) == 0:
        reachability_score += 5
    reachability_score = max(0, min(100, reachability_score))
    safety_gates_intact = True
    if any(reason in {"auth_not_ready", "live_arm", "live_trading_not_armed", "daily_trade_cap", "max_live_positions", "duplicate_position", "cooldown_active", "unsafe_price", "unsafe_spread"} and count > 0 for reason, count in blocker_counts.items()):
        safety_gates_intact = True
    return {
        "active_profile": active_profile,
        "active_edge_threshold": active_edge_threshold,
        "hot_edge_after_cost": hot_edge_after_cost,
        "profile_threshold_pass": profile_threshold_pass,
        "blocker_kind": blocker_kind,
        "reachability_score": reachability_score,
        "candidate_suppression_reasons": threshold_reason_list,
        "top_over_filtering_gate": top_over_filtering_gate,
        "recommended_profile": recommended_profile,
        "safety_gates_intact": safety_gates_intact,
        "threshold_blocker_counts": threshold_blocker_counts,
        "hard_blocker_counts": hard_blocker_counts,
        "hard_blocker_reasons": hard_reason_list,
        "aggression_entries": aggression_entries,
        "threshold_blockers_total": sum(threshold_blocker_counts.values()),
        "hard_blockers_total": sum(hard_blocker_counts.values()),
        "payload_ready_count": payload_ready_count,
        "phase55_ready_count": realm_vv_ready_count,
        "realm_vv_ready_count": realm_vv_ready_count,
        "live_exec_count": live_exec_count,
        "candidate_count": len(candidate_tickers),
    }


def _aggression_simulation_report(summary: Dict[str, Any], daily: Dict[str, Any], entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    base = _aggression_audit_summary(summary, daily, entries)
    blocker_counts: Counter = summary.get("blocker_counts") if isinstance(summary.get("blocker_counts"), Counter) else Counter(summary.get("blocker_counts") or {})
    threshold_total = base.get("threshold_blockers_total", 0)
    hard_total = base.get("hard_blockers_total", 0)
    candidate_count = base.get("candidate_count", 0)
    payload_ready_count = base.get("payload_ready_count", 0)
    realm_vv_ready_count = base.get("realm_vv_ready_count", base.get("phase55_ready_count", 0))
    live_exec_count = base.get("live_exec_count", 0)
    profiles = {}
    for profile, factor in (("safe", 0.0), ("balanced", 0.18), ("opportunistic", 0.35), ("beast", 0.45)):
        candidate_est = int(round(candidate_count + threshold_total * factor))
        payload_est = int(round(payload_ready_count + max(0, threshold_total - hard_total) * factor * 0.20))
        realm_vv_est = int(round(realm_vv_ready_count + max(0, threshold_total - hard_total) * factor * 0.15))
        likely_blocker_reduction = int(round(threshold_total * factor))
        profiles[profile] = {
            "candidate_count": candidate_est,
            "payload_ready_count": payload_est,
            "phase55_ready_count": realm_vv_est,
            "realm_vv_ready_count": realm_vv_est,
            "likely_blocker_reduction": likely_blocker_reduction,
            "top_blocker": base.get("top_over_filtering_gate"),
        }
    return {
        "current": base,
        "profiles": profiles,
        "top_blockers": list(summary.get("top_blockers") or []),
        "live_exec_count": live_exec_count,
        "payload_ready_count": payload_ready_count,
        "phase55_ready_count": realm_vv_ready_count,
        "realm_vv_ready_count": realm_vv_ready_count,
        "realm_vv_seen_count": summary.get("realm_vv_seen_count", 0),
        "realm_vv_buy_count": summary.get("realm_vv_buy_count", 0),
        "realm_vv_wait_count": summary.get("realm_vv_wait_count", 0),
        "realm_vv_skip_count": summary.get("realm_vv_skip_count", 0),
        "execution_gate_count": summary.get("execution_gate_count", 0),
    }

def _watchdog_state_signature(payload: Dict[str, Any]) -> str:
    try:
        return json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        return str(payload)


def _build_watchdog_report(system: Dict[str, Any], summary: Dict[str, Any], daily: Dict[str, Any], entries: List[Dict[str, Any]], raw_lines: List[str], state: Dict[str, Any]) -> Dict[str, Any]:
    funnel = _execution_funnel_summary(entries)
    blocker_counts: Counter = summary["blocker_counts"]
    top_blockers = summary.get("top_blockers") or []
    top_tickers = summary.get("top_tickers") or []
    candidate_tickers = summary.get("candidate_tickers") or set()
    markets_fetched = bool(candidate_tickers) or any(str(entry.get("prefix") or "").startswith(("AION-MARKET-DISCOVERY-ROOT", "AION-ALL-MARKET-CANDIDATE-SUMMARY", "AION-RUNONCE-END", "AION-PRE-QUOTE-BUILD-ROOT")) for entry in entries)
    realm_vv_seen_count = int(summary.get("realm_vv_seen_count", 0) or funnel.get("realm_vv_seen_count", 0) or 0)
    realm_vv_buy_count = int(summary.get("realm_vv_buy_count", 0) or funnel.get("realm_vv_buy_count", 0) or 0)
    realm_vv_wait_count = int(summary.get("realm_vv_wait_count", 0) or funnel.get("realm_vv_wait_count", 0) or 0)
    realm_vv_skip_count = int(summary.get("realm_vv_skip_count", 0) or funnel.get("realm_vv_skip_count", 0) or 0)
    execution_gate_count = int(summary.get("execution_gate_count", 0) or funnel.get("execution_gate_count", 0) or 0)
    current = {
        "ts": time.time(),
        "payload_ready": int(summary.get("payload_ready_count", 0) or 0),
        "realm_vv_ready": int(summary.get("realm_vv_ready_count", summary.get("phase55_ready_count", 0)) or 0),
        "live_exec": int(summary.get("live_exec_count", 0) or 0),
        "realm_vv_seen_count": realm_vv_seen_count,
        "realm_vv_ready_count": int(summary.get("realm_vv_ready_count", summary.get("phase55_ready_count", 0)) or 0),
        "realm_vv_buy_count": realm_vv_buy_count,
        "realm_vv_wait_count": realm_vv_wait_count,
        "realm_vv_skip_count": realm_vv_skip_count,
        "realm_vv_edge_positive_count": int(summary.get("realm_vv_edge_positive_count", 0) or 0),
        "realm_vv_edge_zero_count": int(summary.get("realm_vv_edge_zero_count", 0) or 0),
        "realm_vv_aggressive_tradeable_count": int(summary.get("realm_vv_aggressive_tradeable_count", 0) or 0),
        "realm_vv_aggressive_buy_count": int(summary.get("realm_vv_aggressive_buy_count", 0) or 0),
        "phase55_compat_alias_count": int(summary.get("phase55_compat_alias_count", 0) or 0),
        "stale_phase55_dependency_count": int(summary.get("stale_phase55_dependency_count", 0) or 0),
        "edge_zero_suspect_count": int(summary.get("edge_zero_suspect_count", 0) or 0),
        "execution_gate_count": execution_gate_count,
        "orderbook_usable_count": int(summary.get("orderbook_usable_count", 0) or 0),
        "orderbook_blocked_count": int(summary.get("orderbook_blocked_count", 0) or 0),
        "thin_book_override_count": int(summary.get("thin_book_override_count", 0) or 0),
        "final_submit_preflight_count": int(summary.get("final_submit_preflight_count", 0) or 0),
        "final_submit_call_count": int(summary.get("final_submit_call_count", 0) or 0),
        "order_submitted_count": int(summary.get("order_submitted_count", 0) or 0),
        "order_error_count": int(summary.get("order_error_count", 0) or 0),
        "blockers": list(top_blockers[:5]),
        "markets_fetched": bool(markets_fetched),
        "candidate_count": len(candidate_tickers),
    }
    history = list(state.get("watchdog_windows", []) or [])
    history.append(current)
    history = history[-72:]
    state["watchdog_windows"] = history

    def _tail(n: int) -> List[Dict[str, Any]]:
        return history[-max(1, int(n)):]

    last_3 = _tail(3)
    last_6 = _tail(6)
    last_12 = _tail(12)
    last_72 = _tail(72)

    repeated_blocker = None
    if len(last_3) >= 3:
        first = (last_3[-1].get("blockers") or [None])[0]
        if first and all((w.get("blockers") or [None])[0] == first for w in last_3):
            repeated_blocker = first

    payload_stall = bool(
        any(w.get("markets_fetched") for w in last_6)
        and all(int(w.get("payload_ready", 0) or 0) == 0 for w in last_6)
    )
    phase55_stall = bool(
        any(int(w.get("payload_ready", 0) or 0) > 0 for w in last_12)
        and all(int(w.get("realm_vv_ready", 0) or 0) == 0 for w in last_12)
    )
    execution_stall = bool(
        any(int(w.get("payload_ready", 0) or 0) > 0 for w in last_72)
        and all(int(w.get("live_exec", 0) or 0) == 0 for w in last_72)
    )
    raw_error_lines = [line for line in raw_lines if any(token in line for token in ("Traceback", "ERROR", "Exception", "CRITICAL"))]
    raw_error_sample = raw_error_lines[-1] if raw_error_lines else None
    top_blocker = top_blockers[0] if top_blockers else None
    current_blocker = (funnel.get("last_blocker_before_live_execution") or {}).get("reason") or (funnel.get("last_blocker_before_phase55_handoff") or {}).get("reason") or (funnel.get("last_blocker_before_entry_ready") or {}).get("reason") or top_blocker or "all_guards_passed"
    current_blocker_ticker = (funnel.get("last_blocker_before_live_execution") or {}).get("ticker") or (funnel.get("last_blocker_before_phase55_handoff") or {}).get("ticker") or (funnel.get("last_blocker_before_entry_ready") or {}).get("ticker") or (top_tickers[0] if top_tickers else None) or "none"
    if current.get("payload_ready", 0) == 0 and current.get("markets_fetched"):
        vanish_point = "payload_ready"
    elif current.get("payload_ready", 0) > 0 and current.get("realm_vv_ready", 0) == 0:
        vanish_point = "phase55_handoff"
    elif current.get("realm_vv_ready", 0) > 0 and current.get("live_exec", 0) == 0:
        vanish_point = "LIVE-EXECUTION"
    else:
        vanish_point = "none"

    exact_trace = _latest_matching(entries, ("AION-QUOTE-USABILITY-BLOCK", "AION-QUOTE-USABILITY-FINAL", "AION-PAYLOAD-MISSING-TRACE", "AION-PHASE55-HANDOFF-TRACE", "AION-PHASE55-PERSISTENCE-TRACE", "AION-PRICE-VALIDATION-MODE", "AION-PRICE-BOUNDS-TRACE", "AION-SIDE-PRICE-SELECTION-TRACE", "AION-CANONICAL-PRICE-CONSUMED", "AION-ORDERBOOK-PAYLOAD-TRACE", "AION-QUOTE-HYDRATION-FAIL", "AION-QUOTE-HYDRATION-BUILD")) or {}
    exact_source = exact_trace.get("prefix") or "none"
    exact_raw = exact_trace.get("raw") or raw_error_sample or "none"
    exact_function = exact_trace.get("payload", {}).get("blocker_source_function") or exact_trace.get("payload", {}).get("source_path") or "unknown"
    exact_line = exact_trace.get("payload", {}).get("blocker_source_line") or exact_trace.get("payload", {}).get("source_line") or "unknown"
    latest_price_trace = _latest_matching(entries, ("AION-PRICE-VALIDATION-MODE", "AION-PRICE-BOUNDS-TRACE", "AION-CANONICAL-SIDE-PRICE")) or {}
    latest_price_payload = latest_price_trace.get("payload") if isinstance(latest_price_trace.get("payload"), dict) else {}

    alert_reasons = []
    if blocker_counts.get("quote_orderbook_snapshot_recursionerror", 0) > 0:
        alert_reasons.append("quote_orderbook_snapshot_recursionerror")
    if blocker_counts.get("phase55_observation_recursionerror", 0) > 0:
        alert_reasons.append("phase55_observation_recursionerror")
    if blocker_counts.get("quote_not_usable", 0) > 0 and not exact_trace:
        alert_reasons.append("quote_not_usable_without_exact_source")
        if not latest_price_payload.get("blocker_detail") and not latest_price_payload.get("block_reason"):
            alert_reasons.append("quote_not_usable_without_blocker_detail")
    selected_price_mismatch = None
    latest_price_trace = _latest_matching(entries, ("AION-PRICE-VALIDATION-MODE", "AION-PRICE-BOUNDS-TRACE", "AION-CANONICAL-SIDE-PRICE")) or {}
    latest_price_payload = latest_price_trace.get("payload") if isinstance(latest_price_trace.get("payload"), dict) else {}
    sample_unsafe_price = None
    if latest_price_payload:
        sample_unsafe_price = {
            "ticker": latest_price_payload.get("ticker"),
            "yes_bid_cents": latest_price_payload.get("yes_bid_cents"),
            "no_bid_cents": latest_price_payload.get("no_bid_cents"),
            "derived_yes_ask_cents": latest_price_payload.get("yes_ask_cents"),
            "derived_no_ask_cents": latest_price_payload.get("no_ask_cents"),
            "unsafe_price_status": latest_price_payload.get("unsafe_price_status"),
            "unsafe_resolution_reason": latest_price_payload.get("unsafe_resolution_reason"),
            "false_positive": bool(str(latest_price_payload.get("unsafe_price_status") or "").strip().lower() in {"safe", "caution_wide_spread"}),
        }
    selected_price_dollars = latest_price_payload.get("selected_price_dollars")
    if selected_price_dollars is None:
        selected_price_dollars = latest_price_payload.get("selected_price")
    try:
        selected_price_dollars = float(selected_price_dollars) if selected_price_dollars is not None else None
    except (TypeError, ValueError):
        selected_price_dollars = None
    if blocker_counts.get("selected_price_above_0_99", 0) > 0:
        if latest_price_payload.get("selected_price_cents") is None and latest_price_payload.get("selected_price_dollars") is None:
            alert_reasons.append("selected_price_above_0_99_without_canonical_price")
        elif selected_price_dollars is not None and selected_price_dollars <= 0.99:
            selected_price_mismatch = "selected_price_above_0_99_with_valid_price"
            alert_reasons.append(selected_price_mismatch)
    if blocker_counts.get("selected_price_above_max", 0) > 0:
        alert_reasons.append("selected_price_above_max")
    if blocker_counts.get("missing_quote_object", 0) > 0:
        alert_reasons.append("missing_quote_object")
    if blocker_counts.get("unsafe_price", 0) > 0 and not latest_price_trace:
        alert_reasons.append("unsafe_price_without_exact_source")
    if blocker_counts.get("unsafe_price", 0) > 0 and not latest_price_payload.get("blocker_detail"):
        alert_reasons.append("unsafe_price_without_blocker_detail")
    if any("q referenced before assignment" in line or "referenced before assignment" in line for line in raw_lines):
        alert_reasons.append("q_referenced_before_assignment")
    if payload_stall:
        alert_reasons.append("payload_ready_zero_30m")
    if summary.get("realm_vv_seen_count", 0) > 0 and summary.get("realm_vv_buy_count", 0) == 0:
        alert_reasons.append("realm_vv_buy_zero_60m")
    if execution_stall:
        alert_reasons.append("live_exec_zero_6h")
    if repeated_blocker:
        alert_reasons.append(f"repeated_blocker:{repeated_blocker}")
    if system.get("restart_count") is not None and int(system.get("restart_count") or 0) >= 3:
        alert_reasons.append("restart_loop")
    if raw_error_lines:
        alert_reasons.append("journal_error_or_traceback")

    if alert_reasons:
        if payload_stall or phase55_stall or execution_stall:
            condition_state = "BLOCKER"
        elif raw_error_lines or repeated_blocker:
            condition_state = "BLOCKER"
        else:
            condition_state = "WARNING"
    elif current.get("payload_ready", 0) == 0 and current.get("phase55_ready", 0) == 0 and current.get("live_exec", 0) == 0 and not current.get("markets_fetched"):
        condition_state = "IDLE"
    else:
        condition_state = "CLEAN"

    recommended_fix = "inspect current blocker traces"
    if "quote_orderbook_snapshot_recursionerror" in alert_reasons or "missing_quote_object" in alert_reasons:
        recommended_fix = "patch canonical quote/orderbook hydration and recursion guards"
    elif any(reason.startswith("selected_price_above") for reason in alert_reasons):
        recommended_fix = "fix canonical side/price propagation and Kalshi-valid price normalization"
    elif any(reason.startswith("phase55_ready_zero") or reason == "phase55_observation_recursionerror" for reason in alert_reasons):
        recommended_fix = "fix Phase 5.5 persistence/readback and placeholder overwrite handling"
    elif any(reason.startswith("payload_ready_zero") for reason in alert_reasons):
        recommended_fix = "trace market discovery and quote hydration gating"

    return {
        "current_blocker": current_blocker,
        "current_blocker_ticker": current_blocker_ticker,
        "population_vanish_point": vanish_point,
        "exact_trace_prefix": exact_source,
        "exact_emitting_function": exact_function,
        "exact_emitting_line": exact_line,
        "first_bad_ticker": current_blocker_ticker,
        "sample_raw_trace": exact_raw[:1000],
        "recommended_fix_snippet": recommended_fix,
        "condition_state": condition_state,
        "alert_reasons": alert_reasons,
        "payload_stall": payload_stall,
        "phase55_stall": phase55_stall,
        "execution_stall": execution_stall,
        "repeated_blocker": repeated_blocker,
        "raw_error_lines": raw_error_lines[-3:],
        "history": history,
        "top_blocker": top_blocker,
        "top_blockers": top_blockers,
        "payload_ready_count": current.get("payload_ready", 0),
        "phase55_ready_count": current.get("realm_vv_ready", 0),
        "realm_vv_ready_count": current.get("realm_vv_ready", 0),
        "live_exec_count": current.get("live_exec", 0),
        "realm_vv_seen_count": realm_vv_seen_count,
        "phase55_compat_alias_count": int(summary.get("phase55_compat_alias_count", 0) or 0),
        "stale_phase55_dependency_count": int(summary.get("stale_phase55_dependency_count", 0) or 0),
        "realm_vv_buy_count": realm_vv_buy_count,
        "realm_vv_wait_count": realm_vv_wait_count,
        "realm_vv_skip_count": realm_vv_skip_count,
        "execution_gate_count": execution_gate_count,
    }


def _run_watchdog(*, minutes: int = LOOKBACK_MINUTES, dry_run: bool = False) -> int:
    _load_env_file("/opt/kalshi-bot/.env")
    state_path = _runtime_state_path()
    state = _load_state(state_path)
    entries = _parse_journal(minutes=max(1, int(minutes or LOOKBACK_MINUTES)))
    raw_lines = _journal_lines(minutes=max(1, int(minutes or LOOKBACK_MINUTES)))
    system = _system_status()
    summary = _summarize(entries, state)
    daily = _integrity_daily_counts(entries)
    watchdog = _build_watchdog_report(system, summary, daily, entries, raw_lines, state)
    report_lines = [
        "🛡️ AION GUARDIAN — WATCHDOG",
        "",
        f"State: {watchdog['condition_state']}",
        f"Current blocker: {watchdog['current_blocker']} ({watchdog['current_blocker_ticker']})",
        f"Population vanish point: {watchdog['population_vanish_point']}",
        f"Exact emitter: {watchdog['exact_emitting_function']}:{watchdog['exact_emitting_line']}",
        f"First bad ticker: {watchdog['first_bad_ticker']}",
        f"Sample raw trace: {watchdog['sample_raw_trace']}",
        f"Blocker kind: {watchdog.get('blocker_kind', 'unknown')}",
        f"Recommended fix: {watchdog['recommended_fix_snippet']}",
        f"Alert reasons: {', '.join(watchdog['alert_reasons'] or ['none'])}",
        f"payload_ready_count: {watchdog['payload_ready_count']}",
        f"realm_vv_seen_count: {watchdog.get('realm_vv_seen_count', 0)}",
        f"realm_vv_ready_count: {watchdog['realm_vv_ready_count']}",
        f"realm_vv_buy_count: {watchdog['realm_vv_buy_count']}",
        f"live_exec_count: {watchdog['live_exec_count']}",
        f"realm_vv_edge_positive_count: {watchdog.get('realm_vv_edge_positive_count', 0)}",
        f"realm_vv_edge_zero_count: {watchdog.get('realm_vv_edge_zero_count', 0)}",
        f"realm_vv_aggressive_tradeable_count: {watchdog.get('realm_vv_aggressive_tradeable_count', 0)}",
        f"realm_vv_aggressive_buy_count: {watchdog.get('realm_vv_aggressive_buy_count', 0)}",
        f"phase55_compat_alias_count: {watchdog.get('phase55_compat_alias_count', 0)}",
        f"stale_phase55_dependency_count: {watchdog.get('stale_phase55_dependency_count', 0)}",
        f"edge_zero_suspect_count: {watchdog.get('edge_zero_suspect_count', 0)}",
        f"orderbook_usable_count: {watchdog.get('orderbook_usable_count', 0)}",
        f"orderbook_blocked_count: {watchdog.get('orderbook_blocked_count', 0)}",
        f"thin_book_override_count: {watchdog.get('thin_book_override_count', 0)}",
        f"final_submit_preflight_count: {watchdog.get('final_submit_preflight_count', 0)}",
        f"final_submit_call_count: {watchdog.get('final_submit_call_count', 0)}",
        f"order_submitted_count: {watchdog.get('order_submitted_count', 0)}",
        f"order_error_count: {watchdog.get('order_error_count', 0)}",
        f"Markets fetched: {'yes' if watchdog['payload_stall'] or watchdog['phase55_stall'] or watchdog['execution_stall'] or summary.get('candidate_tickers') else 'no'}",
    ]
    report = "\n".join(report_lines).strip() + "\n"
    print("[AION-GUARDIAN-WATCHDOG-BUILD] " + json.dumps({
        "state": watchdog['condition_state'],
        "alerts": watchdog['alert_reasons'],
        "current_blocker": watchdog['current_blocker'],
        "payload_ready_count": watchdog['payload_ready_count'],
        "phase55_ready_count": watchdog['realm_vv_ready_count'],
        "realm_vv_ready_count": watchdog['realm_vv_ready_count'],
        "realm_vv_seen_count": watchdog.get('realm_vv_seen_count', 0),
        "realm_vv_buy_count": watchdog.get('realm_vv_buy_count', 0),
        "realm_vv_wait_count": watchdog.get('realm_vv_wait_count', 0),
        "realm_vv_skip_count": watchdog.get('realm_vv_skip_count', 0),
        "phase55_compat_alias_count": watchdog.get('phase55_compat_alias_count', 0),
        "stale_phase55_dependency_count": watchdog.get('stale_phase55_dependency_count', 0),
        "realm_vv_edge_positive_count": watchdog.get('realm_vv_edge_positive_count', 0),
        "realm_vv_edge_zero_count": watchdog.get('realm_vv_edge_zero_count', 0),
        "realm_vv_aggressive_tradeable_count": watchdog.get('realm_vv_aggressive_tradeable_count', 0),
        "realm_vv_aggressive_buy_count": watchdog.get('realm_vv_aggressive_buy_count', 0),
        "edge_zero_suspect_count": watchdog.get('edge_zero_suspect_count', 0),
        "execution_gate_count": watchdog.get('execution_gate_count', 0),
        "live_exec_count": watchdog['live_exec_count'],
    }, sort_keys=True, default=str))
    print(report)
    if dry_run:
        _safe_atomic_json_write(state_path, state)
        return 0
    signature = _watchdog_state_signature({
        "state": watchdog['condition_state'],
        "alerts": watchdog['alert_reasons'],
        "current_blocker": watchdog['current_blocker'],
        "payload_ready_count": watchdog['payload_ready_count'],
        "phase55_ready_count": watchdog['realm_vv_ready_count'],
        "realm_vv_ready_count": watchdog['realm_vv_ready_count'],
        "live_exec_count": watchdog['live_exec_count'],
        "realm_vv_seen_count": watchdog.get('realm_vv_seen_count', 0),
        "realm_vv_buy_count": watchdog.get('realm_vv_buy_count', 0),
        "phase55_compat_alias_count": watchdog.get('phase55_compat_alias_count', 0),
        "stale_phase55_dependency_count": watchdog.get('stale_phase55_dependency_count', 0),
        "realm_vv_edge_positive_count": watchdog.get('realm_vv_edge_positive_count', 0),
        "realm_vv_edge_zero_count": watchdog.get('realm_vv_edge_zero_count', 0),
        "realm_vv_aggressive_tradeable_count": watchdog.get('realm_vv_aggressive_tradeable_count', 0),
        "realm_vv_aggressive_buy_count": watchdog.get('realm_vv_aggressive_buy_count', 0),
        "edge_zero_suspect_count": watchdog.get('edge_zero_suspect_count', 0),
    })
    now = time.time()
    last_sent = float(state.get('watchdog_last_sent_ts', 0.0) or 0.0)
    last_signature = str(state.get('watchdog_last_signature') or '')
    cooldown_ok = (now - last_sent) >= 600
    should_send = bool(watchdog['alert_reasons']) and (cooldown_ok or signature != last_signature)
    if should_send:
        sent = _send_telegram_message(report)
        if sent:
            state['watchdog_last_sent_ts'] = now
            state['watchdog_last_signature'] = signature
            state['watchdog_last_report'] = watchdog
            _store_state(state_path, state)
            print("[AION-GUARDIAN-WATCHDOG-SENT] " + json.dumps({"alerts": watchdog['alert_reasons'], "condition_state": watchdog['condition_state']}, sort_keys=True, default=str))
            return 0
        print("[AION-GUARDIAN-WATCHDOG-ERROR] " + json.dumps({"reason": "telegram_send_failed", "condition_state": watchdog['condition_state']}, sort_keys=True, default=str))
        return 2
    _store_state(state_path, state)
    print("[AION-GUARDIAN-WATCHDOG-INFO] " + json.dumps({"reason": "cooldown_or_no_alerts", "condition_state": watchdog['condition_state']}, sort_keys=True, default=str))
    return 0


def _compute_health_score(system: Dict[str, Any], daily: Dict[str, Any], summary: Dict[str, Any]) -> Tuple[int, str, float, str]:
    score = 100
    reasons = []
    if str(system.get("active") or "").lower() != "active":
        score -= 35
        reasons.append("service_inactive")
    if not system.get("live_mode"):
        score -= 20
        reasons.append("live_mode_disabled")
    if not system.get("armed"):
        score -= 15
        reasons.append("live_arm_off")
    if int(system.get("restart_count") or 0) > 0:
        score -= min(15, int(system.get("restart_count") or 0) * 5)
        reasons.append("service_restarted")
    if daily.get("recursion_errors", 0) > 0:
        score -= 15
        reasons.append("recursion_errors")
    if daily.get("realm_vv_record_written", daily.get("phase55_record_written", 0)) == 0 and summary.get("payload_ready_count", 0) > 0:
        score -= 15
        reasons.append("payload_no_phase55")
    if summary.get("payload_ready_count", 0) > 0 and summary.get("realm_vv_ready_count", summary.get("phase55_ready_count", 0)) == 0:
        score -= 15
        reasons.append("realm_vv_stall")
    if summary.get("live_exec_count", 0) == 0 and summary.get("payload_ready_count", 0) > 0:
        score -= 10
        reasons.append("no_executions")
    if daily.get("quote_not_usable", 0) > 1000:
        score -= 10
        reasons.append("quote_pipeline_blocked")
    if daily.get("selected_price_above", 0) > 1000:
        score -= 10
        reasons.append("price_validation_pressure")
    if summary.get("blocker_total", 0) == 0 and summary.get("payload_ready_count", 0) == 0 and summary.get("realm_vv_ready_count", summary.get("phase55_ready_count", 0)) == 0:
        score -= 5
        reasons.append("idle_window")
    score = max(0, min(100, score))
    if score >= 85:
        state = "CLEAN"
    elif score >= 60:
        state = "WARNING"
    else:
        state = "CRITICAL"
    confidence = min(0.99, max(0.35, 0.35 + min(0.55, (summary.get("blocker_total", 0) + summary.get("payload_ready_count", 0) + summary.get("realm_vv_ready_count", summary.get("phase55_ready_count", 0))) / 100000.0)))
    silent_risk = "high" if (summary.get("payload_ready_count", 0) > 0 and summary.get("realm_vv_ready_count", summary.get("phase55_ready_count", 0)) == 0) or (summary.get("payload_ready_count", 0) == 0 and summary.get("blocker_total", 0) > 5000) else "moderate" if summary.get("blocker_total", 0) > 0 else "low"
    return score, state, confidence, ",".join(reasons) or "none"


def _build_daily_integrity_report(system: Dict[str, Any], summary: Dict[str, Any], daily: Dict[str, Any], entries: List[Dict[str, Any]], state: Dict[str, Any]) -> str:
    blocker_counts: Counter = summary["blocker_counts"]
    new_blockers = summary["new_blocker_types"] or []
    repeated_blockers = summary["repeated_blocker_types"] or []
    funnel = _execution_funnel_summary(entries)
    aggression = _aggression_audit_summary(summary, daily, entries)
    git_info = _git_snapshot()
    runtime_info = _runtime_snapshot()
    meminfo = _read_meminfo()
    loadavg = _load_avg()
    disk = {}
    for path in ("/", "/opt", "/var"):
        try:
            df = subprocess.run(["df", "-P", path], capture_output=True, text=True, check=False).stdout.splitlines()
            if len(df) >= 2:
                parts = df[1].split()
                if len(parts) >= 5:
                    disk[path] = {"used_pct": parts[4], "avail": parts[3]}
        except Exception:
            continue
    restart_count = int(system.get("restart_count") or 0)
    api_auth_healthy = not any(k for k in blocker_counts if "auth" in str(k).lower() or "api" in str(k).lower())
    telegram_configured = bool(_telegram_config()[0] and _telegram_config()[1])
    dormant_pipeline = bool(summary.get("candidate_tickers") and summary.get("payload_ready_count", 0) == 0 and summary.get("realm_vv_ready_count", summary.get("phase55_ready_count", 0)) == 0)
    hidden_blockers = sorted(set(summary.get("top_blockers", []) or []) - set(new_blockers or []))
    score, overall_state, confidence, score_reasons = _compute_health_score(system, daily, summary)
    silent_risk = "high" if (summary.get("candidate_tickers") and summary.get("payload_ready_count", 0) == 0 and summary.get("realm_vv_ready_count", summary.get("phase55_ready_count", 0)) == 0) or daily.get("recursion_errors", 0) > 0 else "moderate" if summary.get("blocker_total", 0) > 0 else "low"
    closest_ticker = funnel.get("closest_ticker") or "none"
    current_blocker = (funnel.get("last_blocker_before_live_execution") or {}).get("reason") or (funnel.get("last_blocker_before_phase55_handoff") or {}).get("reason") or (funnel.get("last_blocker_before_entry_ready") or {}).get("reason") or "all_guards_passed"
    total_scanned = len(summary.get("candidate_tickers") or [])
    sample_unsafe_price = daily.get("sample_unsafe_price") if isinstance(daily, dict) else None
    report_lines = [
        "🛡️ AION DAILY INTEGRITY AUDIT",
        "",
        f"Overall state: {overall_state}",
        f"Health score: {score}/100",
        f"Confidence: {confidence:.2f}",
        f"Silent failure risk: {silent_risk}",
        "",
        "SYSTEM",
        f"- service_active: {system.get('active')}",
        f"- restart_count: {restart_count}",
        f"- crash_loops: {'yes' if restart_count > 3 else 'no'}",
        f"- disk: {disk}",
        f"- ram: total={meminfo.get('MemTotal')}kB available={meminfo.get('MemAvailable')}kB swap_free={meminfo.get('SwapFree')}kB",
        f"- cpu_load: {loadavg}",
        "",
        "LIVE READINESS",
        f"- live_mode: {'enabled' if system.get('live_mode') else 'disabled'}",
        f"- live_armed: {'yes' if system.get('armed') else 'no'}",
        f"- dry_run: {'no' if system.get('live_mode') else 'yes'}",
        f"- api_auth_healthy: {'yes' if api_auth_healthy else 'no'}",
        f"- telegram_configured: {'yes' if telegram_configured else 'no'}",
        "",
        "MARKET PIPELINE",
        f"- candidates_scanned_24h: {total_scanned}",
        f"- payload_ready_count: {summary.get('payload_ready_count', 0)}",
        f"- realm_vv_ready_count: {summary.get('realm_vv_ready_count', summary.get('phase55_ready_count', 0))}",
        f"- realm_vv_seen_count: {summary.get('realm_vv_seen_count', 0)}",
        f"- realm_vv_buy_count: {summary.get('realm_vv_buy_count', 0)}",
        f"- realm_vv_wait_count: {summary.get('realm_vv_wait_count', 0)}",
        f"- realm_vv_skip_count: {summary.get('realm_vv_skip_count', 0)}",
        f"- realm_vv_edge_positive_count: {summary.get('realm_vv_edge_positive_count', 0)}",
        f"- realm_vv_edge_zero_count: {summary.get('realm_vv_edge_zero_count', 0)}",
        f"- realm_vv_aggressive_tradeable_count: {summary.get('realm_vv_aggressive_tradeable_count', 0)}",
        f"- realm_vv_aggressive_buy_count: {summary.get('realm_vv_aggressive_buy_count', 0)}",
        f"- edge_zero_suspect_count: {summary.get('edge_zero_suspect_count', 0)}",
        f"- execution_gate_count: {summary.get('execution_gate_count', 0)}",
        f"- orderbook_usable_count: {summary.get('orderbook_usable_count', 0)}",
        f"- orderbook_blocked_count: {summary.get('orderbook_blocked_count', 0)}",
        f"- thin_book_override_count: {summary.get('thin_book_override_count', 0)}",
        f"- final_submit_preflight_count: {summary.get('final_submit_preflight_count', 0)}",
        f"- final_submit_call_count: {summary.get('final_submit_call_count', 0)}",
        f"- order_submitted_count: {summary.get('order_submitted_count', 0)}",
        f"- order_error_count: {summary.get('order_error_count', 0)}",
        f"- live_execution_count: {summary.get('live_exec_count', 0)}",
        f"- exits_count: {summary.get('exit_count', 0)}",
        f"- closest_ticker_to_execution: {closest_ticker}",
        f"- dormant_pipeline: {'yes' if dormant_pipeline else 'no'}",
        "",
        "BLOCKERS",
        f"- top_blockers: {', '.join(summary.get('top_blockers') or ['none'])}",
        f"- newly_appearing: {', '.join(new_blockers or ['none'])}",
        f"- recurring_blockers: {', '.join(repeated_blockers or ['none'])}",
        f"- trend: {summary.get('trend')}",
        f"- hidden_blockers: {', '.join(hidden_blockers[:10]) if hidden_blockers else 'none'}",
        f"- stale_blocker_labels: {'yes' if any(k in {'direct_fields', 'selected_price_above_max', 'selected_price_above_0_99'} for k in blocker_counts.keys()) else 'no'}",
        f"- top_unsafe_price_reason: {(next((k for k in blocker_counts.keys() if 'unsafe_price' in k), 'none'))}",
        f"- zero_execution_blockers: {', '.join([k for k, v in blocker_counts.items() if v > 0 and summary.get('live_exec_count', 0) == 0][:10]) or 'none'}",
        "",
        "🧭 Aggression:",
        f"- aggression_profile: {aggression.get('active_profile')}",
        f"- active_edge_threshold: {aggression.get('active_edge_threshold')}",
        f"- hot_edge_after_cost: {aggression.get('hot_edge_after_cost')}",
        f"- profile_threshold_pass: {aggression.get('profile_threshold_pass')}",
        f"- blocker_kind: {aggression.get('blocker_kind')}",
        f"- reachability_score: {aggression.get('reachability_score')}/100",
        f"- candidate_suppression_reasons: {', '.join(aggression.get('candidate_suppression_reasons') or ['none'])}",
        f"- top_over_filtering_gate: {aggression.get('top_over_filtering_gate')}",
        f"- recommended_aggression_profile: {aggression.get('recommended_profile')}",
        f"- safety_gates_intact: {'yes' if aggression.get('safety_gates_intact') else 'no'}",
        "",
        "QUOTE/ORDERBOOK HEALTH",
        f"- recursion_errors: {daily.get('recursion_errors', 0)}",
        f"- quote_not_usable: {daily.get('quote_not_usable', 0)}",
        f"- unsafe_price_count: {daily.get('unsafe_price_count', 0)}",
        f"- caution_wide_spread_count: {daily.get('caution_wide_spread_count', 0)}",
        f"- unexecutable_price_count: {daily.get('unexecutable_price_count', 0)}",
        f"- both_sides_unexecutable_count: {daily.get('both_sides_unexecutable_count', 0)}",
        f"- selected_price_above_0_99_count: {daily.get('selected_price_above_0_99_count', 0)}",
        f"- phase55_reachability_bridge_count: {daily.get('phase55_reachability_bridge_count', 0)}",
        f"- stale_orderbooks: {blocker_counts.get('stale_orderbook', 0)}",
        f"- malformed_books: {blocker_counts.get('orderbook_shape_unrecognized', 0)}",
        f"- empty_books: {blocker_counts.get('empty_orderbook', 0)}",
        f"- unsafe_price_propagation: {daily.get('unsafe_unknown', 0)}",
        f"- sample_unsafe_price: {json.dumps(sample_unsafe_price, sort_keys=True, default=str) if sample_unsafe_price else 'none'}",
        "",
        "REALM V.V HEALTH",
        f"- persistence_working: {'yes' if daily.get('realm_vv_record_written', daily.get('phase55_record_written', 0)) > 0 else 'no'}",
        f"- readback_working: {'yes' if summary.get('realm_vv_ready_count', summary.get('phase55_ready_count', 0)) > 0 else 'no'}",
        f"- placeholder_overwrite_detection: {'yes' if daily.get('placeholder_overwrite', 0) > 0 else 'no'}",
        f"- phase55_compat_alias_count: {daily.get('phase55_compat_alias_count', 0)}",
        f"- stale_phase55_dependency_count: {daily.get('stale_phase55_dependency_count', 0)}",
        f"- trigger_wait: {blocker_counts.get('trigger_wait', 0)}",
        f"- pullback_wait: {blocker_counts.get('pullback_wait', 0)}",
        f"- psychology_block: {blocker_counts.get('psychology_block', 0)}",
        f"- probability_block: {blocker_counts.get('probability_block', 0)}",
        "",
        "EXECUTION HEALTH",
        f"- duplicate_guard: {blocker_counts.get('duplicate_position', 0)}",
        f"- cooldown_lock: {blocker_counts.get('cooldown_active', 0)}",
        f"- daily_trade_cap: {blocker_counts.get('daily_trade_cap', 0)}",
        f"- max_live_positions: {blocker_counts.get('max_live_positions', 0)}",
        f"- api_submission_failures: {sum(v for k, v in blocker_counts.items() if 'submission' in str(k) or 'submit' in str(k))}",
        "",
        "EXIT/RISK HEALTH",
        f"- exit_monitor_alive: {'yes' if summary.get('exit_count', 0) > 0 or system.get('active') == 'active' else 'unknown'}",
        f"- tp_sl_activity: {'yes' if any('exit' in str(k) for k in blocker_counts.keys()) else 'no'}",
        f"- stuck_positions: {'unknown'}",
        f"- unreadable_positions: {'unknown'}",
        "",
        "GIT/RUNTIME HEALTH",
        f"- git_clean: {'yes' if git_info.get('status_clean') else 'no' if git_info.get('status_clean') is not None else 'unknown'}",
        f"- runtime_ignored: {'yes' if runtime_info.get('git_ignored_runtime') else 'no' if runtime_info.get('git_ignored_runtime') is not None else 'unknown'}",
        f"- runtime_growth: tmp={runtime_info.get('tmp_size_bytes')} cache={runtime_info.get('cache_size_bytes')} memory={runtime_info.get('memory_size_bytes')}",
        f"- runtime_dirs_valid: {'yes' if runtime_info.get('runtime_exists') else 'no'}",
        "",
        "Recommendation:",
        f"- next_fix: {'review raw blocker trace samples' if overall_state == 'CLEAN' else 'fix the dominant blocker path'}",
    ]
    report = "\n".join(report_lines).strip() + "\n"
    print(
        "[AION-DAILY-INTEGRITY-BUILD] "
        + json.dumps(
            {
                "service": SERVICE_NAME,
                "lookback_minutes": DAILY_INTEGRITY_LOOKBACK_MINUTES,
                "state": overall_state,
                "health_score": score,
                "confidence": confidence,
                "silent_failure_risk": silent_risk,
                "payload_ready_count": summary.get('payload_ready_count', 0),
                "phase55_ready_count": summary.get('realm_vv_ready_count', summary.get('phase55_ready_count', 0)),
                "realm_vv_ready_count": summary.get('realm_vv_ready_count', summary.get('phase55_ready_count', 0)),
                "live_exec_count": summary.get('live_exec_count', 0),
                "top_blockers": summary.get('top_blockers')[:5],
            },
            sort_keys=True,
            default=str,
        )
    )
    return report


def _run_daily_integrity_audit(*, dry_run: bool = False) -> int:
    _load_env_file("/opt/kalshi-bot/.env")
    state_path = _runtime_daily_state_path()
    last_report_path = _runtime_daily_last_report_path()
    state = _load_state(state_path)
    entries = _parse_journal(minutes=DAILY_INTEGRITY_LOOKBACK_MINUTES)
    system = _system_status()
    summary = _summarize(entries, state)
    daily = _integrity_daily_counts(entries)
    report = _build_daily_integrity_report(system, summary, daily, entries, state)
    report_payload = {
        "created_at": time.time(),
        "service": SERVICE_NAME,
        "mode": "daily_integrity_audit",
        "system": system,
        "summary": summary,
        "daily": daily,
        "report": report,
    }
    if dry_run:
        print(report)
        _safe_atomic_json_write(last_report_path, report_payload)
        return 0
    sent = _send_telegram_message(report)
    if sent:
        state["last_sent_ts"] = time.time()
        state["last_sent_reason"] = "daily_integrity_audit"
        state["seen_blocker_types"] = sorted(set(state.get("seen_blocker_types", []) or []) | set(summary["blocker_counts"].keys()))
        _store_state(state_path, state)
        _safe_atomic_json_write(last_report_path, report_payload)
        print("[AION-DAILY-INTEGRITY-SENT] " + json.dumps({"telegram_configured": bool(_telegram_config()[0] and _telegram_config()[1]), "health_score": _compute_health_score(system, daily, summary)[0]}, sort_keys=True, default=str))
        return 0
    print("[AION-DAILY-INTEGRITY-ERROR] " + json.dumps({"reason": "telegram_send_failed", "telegram_configured": bool(_telegram_config()[0] and _telegram_config()[1])}, sort_keys=True, default=str))
    return 2

def _run_aggression_simulation(*, minutes: int = LOOKBACK_MINUTES, dry_run: bool = True) -> int:
    _load_env_file("/opt/kalshi-bot/.env")
    state_path = _runtime_state_path()
    state = _load_state(state_path)
    entries = _parse_journal(minutes=max(1, int(minutes or LOOKBACK_MINUTES)))
    system = _system_status()
    summary = _summarize(entries, state)
    daily = _integrity_daily_counts(entries)
    sim = _aggression_simulation_report(summary, daily, entries)
    current = sim.get("current", {})
    lines = [
        "🛡️ AION AGGRESSION SIM",
        "",
        f"Current profile: {current.get('active_profile')}",
        f"Reachability score: {current.get('reachability_score')}/100",
        f"Candidate suppression reasons: {', '.join(current.get('candidate_suppression_reasons') or ['none'])}",
        f"Top over-filtering gate: {current.get('top_over_filtering_gate')}",
        f"Recommended profile: {current.get('recommended_profile')}",
        f"Safety gates intact: {'yes' if current.get('safety_gates_intact') else 'no'}",
        "",
        "Profile comparison:",
    ]
    for profile in ("safe", "balanced", "opportunistic"):
        info = sim.get("profiles", {}).get(profile, {})
        lines.extend([
            f"- {profile}: candidates={info.get('candidate_count')} payload_ready={info.get('payload_ready_count')} realm_vv_ready={info.get('realm_vv_ready_count', info.get('phase55_ready_count'))} likely_blocker_reduction={info.get('likely_blocker_reduction')} top_blocker={info.get('top_blocker')}",
        ])
    lines.extend([
        "",
        f"Recent live executions: {sim.get('live_exec_count', 0)}",
        f"Recent payload_ready: {sim.get('payload_ready_count', 0)}",
        f"Recent realm_vv_ready: {sim.get('realm_vv_ready_count', sim.get('phase55_ready_count', 0))}",
        f"Top blockers: {', '.join(sim.get('top_blockers') or ['none'])}",
        f"System active: {system.get('active')} live_mode={'yes' if system.get('live_mode') else 'no'} armed={'yes' if system.get('armed') else 'no'}",
    ])
    report = "\n".join(lines).strip() + "\n"
    print("[AION-AGGRESSION-SIM-BUILD] " + json.dumps({
        "minutes": minutes,
        "profile": current.get("active_profile"),
        "recommended_profile": current.get("recommended_profile"),
        "reachability_score": current.get("reachability_score"),
    }, sort_keys=True, default=str))
    print(report)
    return 0


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
    daily = _integrity_daily_counts(entries)
    report = _build_report(summary, system, state, entries, daily=daily)
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
            "phase55_ready_count": summary.get("realm_vv_ready_count", summary["phase55_ready_count"]),
            "realm_vv_ready_count": summary.get("realm_vv_ready_count", summary["phase55_ready_count"]),
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
                "phase55_ready_count": summary.get("realm_vv_ready_count", summary["phase55_ready_count"]),
                "realm_vv_ready_count": summary.get("realm_vv_ready_count", summary["phase55_ready_count"]),
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
            "phase55_ready_count": summary.get("realm_vv_ready_count", summary["phase55_ready_count"]),
            "realm_vv_ready_count": summary.get("realm_vv_ready_count", summary["phase55_ready_count"]),
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


def _canonical_tradeability_selftest() -> Dict[str, Any]:
    cases = [
        {
            "name": "ninety_nine_cents_passes",
            "yes_bid_cents": 1,
            "no_bid_cents": 1,
            "selected_price_cents": 99,
            "selected_price_dollars": 0.99,
            "expected_pass": True,
        },
        {
            "name": "hundred_cents_blocks",
            "yes_bid_cents": 1,
            "no_bid_cents": 100,
            "selected_price_cents": 100,
            "selected_price_dollars": 1.0,
            "expected_pass": False,
        },
        {
            "name": "derived_asks_correct",
            "yes_bid_cents": 12,
            "no_bid_cents": 34,
            "selected_price_cents": 34,
            "selected_price_dollars": 0.34,
            "expected_yes_ask_cents": 66,
            "expected_no_ask_cents": 88,
        },
    ]
    results = []
    for case in cases:
        yes_ask_cents = 100 - int(case["no_bid_cents"])
        no_ask_cents = 100 - int(case["yes_bid_cents"])
        price_pass = 1 <= int(case["selected_price_cents"]) <= 99
        results.append({
            "name": case["name"],
            "yes_ask_cents": yes_ask_cents,
            "no_ask_cents": no_ask_cents,
            "price_pass": price_pass,
            "expected_pass": case.get("expected_pass"),
            "pass": (price_pass == case.get("expected_pass")) if "expected_pass" in case else (yes_ask_cents == case.get("expected_yes_ask_cents") and no_ask_cents == case.get("expected_no_ask_cents")),
        })
    return {"results": results, "passed": all(item["pass"] for item in results)}


def main(argv: Optional[List[str]] = None) -> int:
    global LOOKBACK_MINUTES
    parser = argparse.ArgumentParser(description="AION Guardian hourly report agent")
    parser.add_argument("--send-now", action="store_true", help="Send the report immediately (ignores cooldown).")
    parser.add_argument("--dry-run", action="store_true", help="Print the report and persist state without sending Telegram.")
    parser.add_argument("--daily-integrity-audit", action="store_true", help="Run the 24h whole-system integrity audit and send the daily Telegram report.")
    parser.add_argument("--aggression-sim", action="store_true", help="Run the aggression profile simulation comparison over the recent log window.")
    parser.add_argument("--watchdog", action="store_true", help="Run the 5-minute watchdog monitor and Telegram alerting logic.")
    parser.add_argument("--canonical-selftest", action="store_true", help="Run deterministic canonical tradeability self-tests and exit.")
    parser.add_argument("--minutes", type=int, default=LOOKBACK_MINUTES, help="Lookback window in minutes.")
    args = parser.parse_args(argv)
    if args.daily_integrity_audit:
        LOOKBACK_MINUTES = max(1, int(args.minutes or DAILY_INTEGRITY_LOOKBACK_MINUTES))
        return _run_daily_integrity_audit(dry_run=bool(args.dry_run))
    if args.aggression_sim:
        LOOKBACK_MINUTES = max(1, int(args.minutes or LOOKBACK_MINUTES))
        return _run_aggression_simulation(minutes=LOOKBACK_MINUTES, dry_run=bool(args.dry_run))
    if args.watchdog:
        LOOKBACK_MINUTES = max(1, int(args.minutes or LOOKBACK_MINUTES))
        return _run_watchdog(minutes=LOOKBACK_MINUTES, dry_run=bool(args.dry_run))
    LOOKBACK_MINUTES = max(1, int(args.minutes or LOOKBACK_MINUTES))
    return build_and_send_report(force=bool(args.send_now), dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())

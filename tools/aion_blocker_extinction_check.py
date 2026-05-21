#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aion_canonical_execution import (
    CandidateState,
    PASS_LABELS,
    classify_candidate_state,
)


REPO_ROOT = Path("/opt/kalshi-bot")


def _assert(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _check_classification(samples: list[tuple[str, dict, dict]], failures: list[str], results: list[dict]) -> None:
    for name, candidate, context in samples:
        state = classify_candidate_state(candidate, context=context)
        results.append({"name": name, **state.as_dict()})
        if name == "pass_labels":
            _assert(state.final_state in {"WAITING", "SKIPPED"}, f"{name} returned {state.final_state}", failures)
            _assert(not state.block_labels, f"{name} leaked block labels: {state.block_labels}", failures)
        elif name == "unknown":
            _assert(state.final_state == "ERROR", f"{name} expected ERROR got {state.final_state}", failures)
        elif name == "spread_wait":
            _assert(state.final_state == "WAITING", f"{name} expected WAITING got {state.final_state}", failures)
        elif name == "liquidity_soft_pass":
            _assert(state.final_state == "WAITING", f"{name} expected WAITING got {state.final_state}", failures)
        elif name == "placeholder_phase55_record":
            _assert(state.final_state != "BLOCKED", f"{name} unexpectedly BLOCKED", failures)
        elif name == "recoverable_orderbook":
            _assert(state.final_state == "WAITING", f"{name} expected WAITING got {state.final_state}", failures)
        elif name == "hard_orderbook":
            _assert(state.final_state == "BLOCKED", f"{name} expected BLOCKED got {state.final_state}", failures)
        elif name == "invalid_price":
            _assert(state.final_state == "BLOCKED", f"{name} expected BLOCKED got {state.final_state}", failures)


def _load_latest_guardian_audit() -> dict:
    candidates = [
        REPO_ROOT / "runtime" / "state" / "aion_guardian_integrity_last.json",
        REPO_ROOT / "runtime" / "state" / "aion_guardian_report_last.json",
        Path("/tmp") / "kalshi-bot" / "runtime" / "state" / "aion_guardian_integrity_last.json",
        Path("/tmp") / "kalshi-bot" / "runtime" / "state" / "aion_guardian_report_last.json",
    ]
    for path in candidates:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def main() -> int:
    failures: list[str] = []
    results: list[dict] = []
    print("[AION-BLOCKER-NUKE-START] " + json.dumps({"cwd": str(Path.cwd()), "repo_root": str(ROOT)}, sort_keys=True, default=str))
    samples = [
        ("pass_labels", {"blocker": "all_guards_passed", "payload_ready": True}, {}),
        ("unknown", {"blocker": "unknown"}, {}),
        ("spread_wait", {"blocker": "spread_wait"}, {}),
        ("liquidity_soft_pass", {"blocker": "liquidity_soft_pass"}, {}),
        ("placeholder_phase55_record", {"blocker": "placeholder_phase55_record"}, {}),
        ("recoverable_orderbook", {"blocker": "orderbook_not_usable", "selected_side_has_price": True, "selected_side_has_liquidity": True, "final_submit_allowed": True}, {}),
        ("hard_orderbook", {"blocker": "orderbook_not_usable", "selected_side_has_price": False, "selected_side_has_liquidity": False, "final_submit_allowed": False}, {}),
        ("invalid_price", {"blocker": "unsafe_price", "selected_price_cents": 105}, {}),
    ]
    _check_classification(samples, failures, results)

    guardian = _load_latest_guardian_audit()
    invariant = None
    if guardian:
        live_exec = int(guardian.get("summary", {}).get("live_exec_count", 0) or 0)
        preflight = int(guardian.get("summary", {}).get("final_submit_preflight_count", 0) or 0)
        execution_candidate = int(guardian.get("summary", {}).get("execution_candidate_count", 0) or 0)
        invariant = {
            "live_exec_count": live_exec,
            "final_submit_preflight_count": preflight,
            "execution_candidate_count": execution_candidate,
            "submit_handoff_contradiction": bool(execution_candidate > 0 and preflight == 0),
        }
        _assert(not invariant["submit_handoff_contradiction"], "submit handoff contradiction remains", failures)
        _assert(live_exec <= preflight, "live_exec_count exceeds final_submit_preflight_count", failures)
    else:
        invariant = {"note": "guardian audit snapshot unavailable"}

    payload = {
        "checked": True,
        "pass_labels_count": len(PASS_LABELS),
        "failures": failures,
        "results": results,
        "guardian_invariant": invariant,
    }
    print("[AION-BLOCKER-EXTINCTION-CHECK] " + json.dumps(payload, sort_keys=True, default=str))
    if failures:
        print("[AION-BLOCKER-SANITIZED]" + " " + json.dumps({"failed_checks": failures}, sort_keys=True, default=str))
        print("[AION-BLOCKER-NUKE-COMPLETE] " + json.dumps({"status": "failed", "failures": failures}, sort_keys=True, default=str))
        return 1
    print("[AION-BLOCKER-SANITIZED] " + json.dumps({"failed_checks": []}, sort_keys=True, default=str))
    print("[AION-BLOCKER-NUKE-COMPLETE] " + json.dumps({"status": "passed", "failures": []}, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

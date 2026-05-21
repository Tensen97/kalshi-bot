#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aion_patch_queue as patch_queue
from aion_sentinel_agent import SentinelAgent


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True, default=str))


def _proposal_mobile_summary(record: dict) -> str:
    files = record.get("affected_files") or []
    lines = [
        "PENDING PATCH" if str(record.get("status") or "").lower() == "pending" else "PATCH STATUS",
        f"ID: {record.get('proposal_id') or 'unknown'}",
        f"Risk: {record.get('risk_score', 'n/a')}",
        f"Confidence: {record.get('confidence_score', 'n/a')}",
        f"Safety: {record.get('safety_score', 'n/a')}",
        "Files:",
    ]
    lines.extend([f"- {item}" for item in (files[:8] or ["none"])])
    lines.extend(
        [
            "Summary:",
            f"{record.get('diagnosis') or record.get('root_cause') or 'unknown'}",
            "Approve?",
            f"python3 tools/aion_patch_control.py --approve {record.get('proposal_id')}",
        ]
    )
    return "\n".join(lines)


def _load_or_fail(proposal_id: str) -> dict:
    proposal = patch_queue.load_proposal(proposal_id)
    if not proposal:
        raise SystemExit(f"proposal_not_found: {proposal_id}")
    return proposal


def _cmd_list_pending() -> int:
    proposals = patch_queue.list_proposals(status="pending")
    if not proposals:
        print("No pending patch proposals.")
        return 0
    for proposal in proposals:
        print(_proposal_mobile_summary(proposal))
        print()
    return 0


def _cmd_status(proposal_id: str) -> int:
    proposal = _load_or_fail(proposal_id)
    print(_proposal_mobile_summary(proposal))
    print("\nFull status:\n")
    _print_json(proposal)
    return 0


def _cmd_approve(proposal_id: str) -> int:
    proposal = _load_or_fail(proposal_id)
    denied = patch_queue.check_denylist(proposal)
    if denied:
        patch_queue.update_proposal_status(proposal_id, "rejected", actor="human", reason="denylist_block", details={"denylist_hits": denied})
        print(f"PATCH REJECTED BY DENYLIST: {proposal_id}")
        _print_json({"proposal_id": proposal_id, "status": "rejected", "denylist_hits": denied})
        return 1
    updated = patch_queue.update_proposal_status(proposal_id, "approved", actor="human", reason="human_approved")
    print(_proposal_mobile_summary(updated or proposal))
    print("\nApproved. Apply with:\n")
    print(f"python3 tools/aion_patch_control.py --apply {proposal_id}")
    return 0


def _cmd_reject(proposal_id: str) -> int:
    proposal = _load_or_fail(proposal_id)
    updated = patch_queue.update_proposal_status(proposal_id, "rejected", actor="human", reason="human_rejected")
    print(_proposal_mobile_summary(updated or proposal))
    print("\nRejected.\n")
    return 0


def _cmd_apply(proposal_id: str) -> int:
    agent = SentinelAgent(dry_run=False, autopatch=False)
    result = agent._apply_patch_proposal(proposal_id)
    _print_json(result)
    return 0 if result.get("applied") and not result.get("rolled_back") else 1


def _cmd_rollback(proposal_id: str) -> int:
    agent = SentinelAgent(dry_run=False, autopatch=False)
    result = agent._rollback_patch_proposal(proposal_id)
    _print_json(result)
    return 0 if result.get("rolled_back") else 1


def _build_demo_proposal() -> dict:
    demo_root = patch_queue.QUEUE_ROOT / "simulations"
    demo_root.mkdir(parents=True, exist_ok=True)
    proposal_id = f"demo_{Path().resolve().name}_{int(time.time())}"
    sample_dir = demo_root / proposal_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_path = sample_dir / "demo_scope.py"
    before = (
        "def demo():\n"
        "    if demo_ready and demo_mode == 'safe':\n"
        "        return True\n"
        "    return False\n"
    )
    after = (
        "def demo():\n"
        "    demo_ready = False\n"
        "    if demo_ready and demo_mode == 'safe':\n"
        "        return True\n"
        "    return False\n"
    )
    sample_path.write_text(before, encoding="utf-8")
    issue = {
        "issue_type": "scope_bug",
        "severity": "medium",
        "variable": "demo_ready",
        "file_path": str(sample_path),
        "function_name": "demo",
        "remaining_blocker": "runtime_scope_crash",
    }
    proposal = {
        "name": "demo_demo_ready_initializer",
        "patch_type": "scope_initializer",
        "variable": "demo_ready",
        "function_name": "demo",
        "file_path": str(sample_path),
        "initializer_strategy": "false",
        "default_value": False,
        "notes": "demo proposal for safe human approval and rollback",
        "proposed_files": [str(sample_path)],
        "verification_commands": [f"python3 -m py_compile {sample_path}"],
        "rollback_plan": "restore backup and re-verify",
        "risk_level": "low",
        "requires_restart": False,
        "requires_compile": True,
    }
    diff_preview = "".join(
        __import__("difflib").unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            fromfile=f"a/{sample_path}",
            tofile=f"b/{sample_path}",
        )
    )
    record = patch_queue.create_proposal(
        issue=issue,
        ai_bridge={"simulation": True, "prompt": "demo"},
        structured={
            "diagnosis": "missing_boolean_initializer",
            "root_cause": "demo_ready referenced before assignment in demo()",
            "proposed_files": [str(sample_path)],
            "patch_plan": ["initialize demo_ready at function top"],
            "verification_commands": [f"python3 -m py_compile {sample_path}"],
            "risk_level": "low",
            "action": "approve",
            "patch_type": "scope_initializer",
            "variable": "demo_ready",
            "function_name": "demo",
            "file_path": str(sample_path),
            "initializer_strategy": "false",
            "default_value": False,
            "notes": "demo proposal",
            "require_human_confirm": True,
            "requires_restart": False,
            "requires_compile": True,
        },
        proposal=proposal,
        verification={"risk_score": 0.12, "confidence_score": 0.93, "safety_score": 0.94, "approved": False, "auto_apply_allowed": False, "require_human_confirm": True},
        unified_diff_patch=diff_preview,
        patch_preview=diff_preview,
        source="demo",
        status="pending",
        incident_dir=sample_dir,
        current_safety_state={"demo": True},
    )
    record["demo_file"] = str(sample_path)
    return record


def _cmd_simulate_proposal() -> int:
    record = _build_demo_proposal()
    print(_proposal_mobile_summary(record))
    print("\nPending demo proposal created.\n")
    _print_json(record)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AION patch queue control")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-pending", action="store_true")
    group.add_argument("--approve")
    group.add_argument("--reject")
    group.add_argument("--status")
    group.add_argument("--apply")
    group.add_argument("--rollback")
    group.add_argument("--simulate-proposal", action="store_true")
    args = parser.parse_args()
    if args.list_pending:
        return _cmd_list_pending()
    if args.approve:
        return _cmd_approve(args.approve)
    if args.reject:
        return _cmd_reject(args.reject)
    if args.status:
        return _cmd_status(args.status)
    if args.apply:
        return _cmd_apply(args.apply)
    if args.rollback:
        return _cmd_rollback(args.rollback)
    if args.simulate_proposal:
        return _cmd_simulate_proposal()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

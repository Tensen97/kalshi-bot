#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PASS_LABELS = {
    "all_guards_passed",
    "entry_ready",
    "tradeable",
    "eligible",
    "liquidity_soft_pass",
    "targeted_any_size_present",
    "priority_sports_family",
    "controlled_sports_promotion",
    "recent_volume",
    "recent_volume+priority_sports_family",
    "open_interest_present",
    "open_interest_present+recent_volume+event_in_target_window+priority_sports_family",
    "event_in_target_window",
    "event_in_target_window+priority_sports_family",
}

WAIT_LABELS = {
    "spread_wait",
    "refresh",
    "disabled",
    "base_sports_threshold",
    "edge_below_threshold",
    "insufficient_liquidity",
    "quote_not_usable",
    "orderbook_not_usable",
}

SOFT_WARNING_LABELS = {
    "unsafe_price",
    "unsafe_spread",
    "spread_above_threshold",
    "orderbook_not_usable",
    "quote_not_usable",
    "insufficient_liquidity",
    "base_sports_threshold",
    "edge_below_threshold",
    "spread_wait",
}

HARD_BLOCK_LABELS = {
    "auth_failure",
    "auth_not_ready",
    "duplicate_live_position",
    "duplicate_position",
    "malformed_payload",
    "invalid_payload",
    "no_selected_side",
    "missing_selected_side",
    "no_executable_selected_side_price",
    "selected_price_le_0",
    "selected_price_ge_100",
    "selected_price_above_0_99",
    "selected_price_above_max",
    "selected_price_out_of_bounds",
    "daily_cap",
    "daily_trade_cap",
    "max_live_positions",
    "kalshi_api_rejection",
    "api_error",
    "dead_orderbook",
    "corrupt_orderbook",
    "both_sides_unexecutable",
}

OBSOLETE_LABELS = {
    "phase55",
    "phase55_block",
    "phase55_not_ready",
    "placeholder_phase55_record",
    "phase55_reachability_bridge",
    "phase55_handoff",
    "phase55_ready",
    "phase55_status",
    "phase55_source_field",
    "phase55_live_execution_gate_by_ticker",
    "raw_phase55_status",
    "phase55_record",
    "phase55_record_write_success",
    "phase55_explicit_approval",
    "direct_fields",
    "legacy bridge",
    "compatibility alias",
    "unknown",
}

ERROR_LABELS = {
    "unknown",
    "error",
    "exception",
    "runtime_error",
    "telemetry_bug",
}

SKIP_LABELS = {
    "skip",
    "skipped",
    "prefilter_rejected",
    "no_candidate",
    "not_selected",
}


def _normalize_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"true", "1", "yes", "y", "on", "ok", "ready", "pass", "passed", "submitted", "executed"}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _labels_from_candidate(candidate: Dict[str, Any], context: Dict[str, Any]) -> List[str]:
    labels: List[str] = []
    for source in (
        candidate,
        context,
    ):
        if not isinstance(source, dict):
            continue
        for key in (
            "label",
            "labels",
            "blocker",
            "block_reason",
            "final_block_reason",
            "reason",
            "entry_ready_reason",
            "payload_missing_reason",
            "payload_rejection_reason",
            "skip_reason",
            "canonical_reason",
            "blocker_class",
            "hard_block_reason",
            "soft_warning_reason",
        ):
            value = source.get(key)
            if isinstance(value, (list, tuple, set)):
                labels.extend(_normalize_label(item) for item in value if str(item or "").strip())
            elif value is not None:
                labels.append(_normalize_label(value))
    if isinstance(candidate.get("blockers"), (list, tuple, set)):
        labels.extend(_normalize_label(item) for item in candidate.get("blockers") if str(item or "").strip())
    if isinstance(candidate.get("labels"), (list, tuple, set)):
        labels.extend(_normalize_label(item) for item in candidate.get("labels") if str(item or "").strip())
    return [label for label in labels if label]


@dataclass
class CandidateState:
    final_state: str
    canonical_reason: str
    pass_labels: List[str] = field(default_factory=list)
    wait_labels: List[str] = field(default_factory=list)
    block_labels: List[str] = field(default_factory=list)
    error_labels: List[str] = field(default_factory=list)
    obsolete_labels_detected: List[str] = field(default_factory=list)
    submit_path_reached: bool = False
    submit_preflight_reached: bool = False
    submit_call_reached: bool = False
    selected_side_exists: bool = False
    selected_price_cents: Optional[int] = None
    selected_side_has_price: bool = False
    selected_side_has_liquidity: bool = False
    payload_ready: bool = False
    quote_ready: bool = False
    orderbook_ready: bool = False
    final_submit_allowed: bool = False
    raw_labels: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "final_state": self.final_state,
            "canonical_reason": self.canonical_reason,
            "pass_labels": list(self.pass_labels),
            "wait_labels": list(self.wait_labels),
            "block_labels": list(self.block_labels),
            "error_labels": list(self.error_labels),
            "obsolete_labels_detected": list(self.obsolete_labels_detected),
            "submit_path_reached": self.submit_path_reached,
            "submit_preflight_reached": self.submit_preflight_reached,
            "submit_call_reached": self.submit_call_reached,
            "selected_side_exists": self.selected_side_exists,
            "selected_price_cents": self.selected_price_cents,
            "selected_side_has_price": self.selected_side_has_price,
            "selected_side_has_liquidity": self.selected_side_has_liquidity,
            "payload_ready": self.payload_ready,
            "quote_ready": self.quote_ready,
            "orderbook_ready": self.orderbook_ready,
            "final_submit_allowed": self.final_submit_allowed,
            "raw_labels": list(self.raw_labels),
        }


def classify_candidate_state(candidate: Any, context: Optional[Dict[str, Any]] = None) -> CandidateState:
    candidate_dict = _as_dict(candidate)
    context_dict = _as_dict(context)
    labels = _labels_from_candidate(candidate_dict, context_dict)
    pass_labels = [label for label in labels if label in PASS_LABELS]
    wait_labels = [label for label in labels if label in WAIT_LABELS]
    obsolete_labels = [label for label in labels if label in OBSOLETE_LABELS]
    error_labels = [label for label in labels if label in ERROR_LABELS or "error" in label or "exception" in label or "traceback" in label]

    selected_side_exists = _truthy(candidate_dict.get("selected_side")) or _truthy(context_dict.get("selected_side")) or any(
        str(candidate_dict.get(key) or context_dict.get(key) or "").strip().lower() in {"yes", "no"} for key in ("selected_side", "side", "resolved_side")
    )
    selected_side_has_price = _truthy(candidate_dict.get("selected_side_has_price")) or _truthy(context_dict.get("selected_side_has_price"))
    selected_side_has_liquidity = _truthy(candidate_dict.get("selected_side_has_liquidity")) or _truthy(context_dict.get("selected_side_has_liquidity"))
    payload_ready = _truthy(candidate_dict.get("payload_ready")) or _truthy(context_dict.get("payload_ready"))
    quote_ready = _truthy(candidate_dict.get("quote_ready")) or _truthy(context_dict.get("quote_ready"))
    orderbook_ready = _truthy(candidate_dict.get("orderbook_ready")) or _truthy(context_dict.get("orderbook_ready"))
    final_submit_allowed = _truthy(candidate_dict.get("final_submit_allowed")) or _truthy(candidate_dict.get("final_submit_allowed_by_realm_vv")) or _truthy(context_dict.get("final_submit_allowed")) or _truthy(context_dict.get("final_submit_allowed_by_realm_vv"))
    submit_preflight_reached = _truthy(candidate_dict.get("final_submit_preflight_reached")) or _truthy(candidate_dict.get("submit_preflight_reached")) or _truthy(context_dict.get("final_submit_preflight_reached"))
    submit_call_reached = _truthy(candidate_dict.get("final_submit_call_reached")) or _truthy(candidate_dict.get("submit_call_reached")) or _truthy(context_dict.get("final_submit_call_reached"))
    submitted = _truthy(candidate_dict.get("submitted")) or _truthy(candidate_dict.get("order_submitted")) or _truthy(context_dict.get("submitted"))
    skipped = _truthy(candidate_dict.get("skipped")) or _truthy(candidate_dict.get("skip")) or any(label in SKIP_LABELS for label in labels)

    selected_price_cents = candidate_dict.get("selected_price_cents", context_dict.get("selected_price_cents"))
    try:
        if selected_price_cents is not None:
            raw_price = float(selected_price_cents)
            if 0.0 < raw_price < 1.5 and not float(raw_price).is_integer():
                selected_price_cents = int(round(raw_price * 100.0))
            else:
                selected_price_cents = int(round(raw_price))
    except Exception:
        selected_price_cents = None

    hard_labels = [label for label in labels if label in HARD_BLOCK_LABELS]
    block_labels: List[str] = []
    for label in hard_labels:
        if label in {"no_selected_side", "missing_selected_side", "no_executable_selected_side_price"}:
            if selected_side_exists or selected_side_has_price or selected_side_has_liquidity or final_submit_allowed or submit_preflight_reached:
                wait_labels.append(label)
                continue
            block_labels.append(label)
            continue
        if label in {"selected_price_le_0", "selected_price_ge_100", "selected_price_above_0_99", "selected_price_above_max", "selected_price_out_of_bounds"}:
            if selected_price_cents is not None and 0 < selected_price_cents < 100:
                wait_labels.append(label)
                continue
            block_labels.append(label)
            continue
        if label == "orderbook_not_usable" and (selected_side_has_price or selected_side_has_liquidity or final_submit_allowed or submit_preflight_reached):
            wait_labels.append(label)
            continue
        if label == "unsafe_spread" and final_submit_allowed:
            wait_labels.append(label)
            continue
        if label == "insufficient_liquidity" and selected_side_has_price and selected_side_has_liquidity:
            wait_labels.append(label)
            continue
        block_labels.append(label)

    for label in labels:
        if label == "unsafe_price":
            recoverable = selected_price_cents is not None and 0 < selected_price_cents < 100
            if recoverable:
                if label not in wait_labels:
                    wait_labels.append(label)
                if label in block_labels:
                    block_labels = [item for item in block_labels if item != label]
            else:
                if label not in block_labels:
                    block_labels.append(label)
                if label in wait_labels:
                    wait_labels = [item for item in wait_labels if item != label]
        elif label in {"orderbook_not_usable", "quote_not_usable", "insufficient_liquidity"}:
            recoverable = bool(selected_side_has_price or selected_side_has_liquidity or final_submit_allowed or submit_preflight_reached)
            if recoverable:
                if label not in wait_labels:
                    wait_labels.append(label)
                if label in block_labels:
                    block_labels = [item for item in block_labels if item != label]
            else:
                if label not in block_labels:
                    block_labels.append(label)
                if label in wait_labels:
                    wait_labels = [item for item in wait_labels if item != label]

    for label in labels:
        if label in SOFT_WARNING_LABELS and label not in block_labels and label not in wait_labels:
            wait_labels.append(label)

    canonical_reason = "unknown"
    final_state = "WAITING"
    if error_labels and not submitted:
        final_state = "ERROR"
        canonical_reason = error_labels[0]
    elif submitted:
        final_state = "SUBMITTED"
        canonical_reason = "submitted"
    elif block_labels:
        final_state = "BLOCKED"
        canonical_reason = block_labels[0]
    elif skipped:
        final_state = "SKIPPED"
        canonical_reason = labels[0] if labels else "skipped"
    elif wait_labels or pass_labels or payload_ready or quote_ready or orderbook_ready or final_submit_allowed or submit_preflight_reached or submit_call_reached:
        final_state = "WAITING"
        canonical_reason = (wait_labels[0] if wait_labels else pass_labels[0] if pass_labels else "waiting")
    else:
        final_state = "WAITING"
        canonical_reason = "waiting"

    if block_labels and not canonical_reason:
        canonical_reason = block_labels[0]
    if final_state == "ERROR" and not canonical_reason:
        canonical_reason = "error"

    return CandidateState(
        final_state=final_state,
        canonical_reason=canonical_reason,
        pass_labels=pass_labels,
        wait_labels=wait_labels,
        block_labels=block_labels,
        error_labels=error_labels,
        obsolete_labels_detected=obsolete_labels,
        submit_path_reached=bool(submit_preflight_reached or submit_call_reached or submitted),
        submit_preflight_reached=bool(submit_preflight_reached),
        submit_call_reached=bool(submit_call_reached),
        selected_side_exists=selected_side_exists,
        selected_price_cents=selected_price_cents,
        selected_side_has_price=selected_side_has_price,
        selected_side_has_liquidity=selected_side_has_liquidity,
        payload_ready=payload_ready,
        quote_ready=quote_ready,
        orderbook_ready=orderbook_ready,
        final_submit_allowed=final_submit_allowed,
        raw_labels=labels,
    )


def classify_label_role(label: Any) -> str:
    text = _normalize_label(label)
    if text in PASS_LABELS:
        return "pass"
    if text in WAIT_LABELS:
        return "wait"
    if text in SOFT_WARNING_LABELS:
        return "soft_warning"
    if text in HARD_BLOCK_LABELS:
        return "hard_block"
    if text in OBSOLETE_LABELS:
        return "obsolete"
    if text in ERROR_LABELS or "error" in text or "exception" in text:
        return "error"
    if text in SKIP_LABELS:
        return "skip"
    return "unknown"


def sanitize_blocker_reason(reason: Any, *, candidate: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    text = _normalize_label(reason)
    state = classify_candidate_state(candidate or {"blocker": text}, context=context or {})
    if text in PASS_LABELS:
        return "pass", state.canonical_reason
    if text in WAIT_LABELS or text in SOFT_WARNING_LABELS:
        return "wait", state.canonical_reason
    if text in OBSOLETE_LABELS:
        return "obsolete", state.canonical_reason
    if text in HARD_BLOCK_LABELS:
        return "hard_block", state.canonical_reason
    if text in ERROR_LABELS or "error" in text or "exception" in text:
        return "error", state.canonical_reason
    if not text:
        return "unknown", state.canonical_reason
    return "unknown", state.canonical_reason or text

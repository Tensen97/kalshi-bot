from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class LiveTelemetryState:
    service_status: dict = field(default_factory=dict)
    live_mode: dict = field(default_factory=dict)
    armed_live: bool = False
    execution_posture: dict = field(default_factory=dict)
    current_phase: str = "startup"
    websocket_status: dict = field(default_factory=dict)
    websocket_subscription_count: int = 0
    websocket_latency: float | None = None
    websocket_stale_feed: bool = False
    latest_market_scan: dict = field(default_factory=dict)
    latest_candidate_count: int = 0
    latest_selected_ticker: str | None = None
    latest_execution_decision: dict = field(default_factory=dict)
    latest_execution_block_reason: str | None = None
    latest_entry_timing_status: dict = field(default_factory=dict)
    latest_risk_state: dict = field(default_factory=dict)
    latest_aggression_state: dict = field(default_factory=dict)
    latest_drawdown_state: dict = field(default_factory=dict)
    latest_exposure_state: dict = field(default_factory=dict)
    latest_personality_summary: dict = field(default_factory=dict)
    latest_probability_summary: dict = field(default_factory=dict)
    latest_external_summary: dict = field(default_factory=dict)
    latest_evolution_summary: dict = field(default_factory=dict)
    latest_recommendations: list = field(default_factory=list)
    latest_narrative_summary: dict = field(default_factory=dict)
    latest_microstructure_summary: dict = field(default_factory=dict)
    latest_microstructure_family_summary: dict = field(default_factory=dict)
    latest_microstructure_readiness_state: str | None = None
    latest_reactivation_summary: dict = field(default_factory=dict)
    latest_reactivation_family_summary: dict = field(default_factory=dict)
    latest_reactivation_overall_summary: dict = field(default_factory=dict)
    latest_adaptive_priority_summary: dict = field(default_factory=dict)
    latest_adaptive_priority_family_summary: dict = field(default_factory=dict)
    latest_adaptive_priority_overall_summary: dict = field(default_factory=dict)
    latest_adaptive_confidence_summary: dict = field(default_factory=dict)
    latest_adaptive_confidence_family_summary: dict = field(default_factory=dict)
    latest_adaptive_confidence_overall_summary: dict = field(default_factory=dict)
    latest_family_rotation_summary: dict = field(default_factory=dict)
    latest_family_rotation_family_summary: dict = field(default_factory=dict)
    latest_family_rotation_overall_summary: dict = field(default_factory=dict)
    latest_candidate_learning_summary: dict = field(default_factory=dict)
    latest_candidate_learning_family_summary: dict = field(default_factory=dict)
    latest_candidate_learning_overall_summary: dict = field(default_factory=dict)
    latest_predictive_regime_summary: dict = field(default_factory=dict)
    latest_predictive_regime_family_summary: dict = field(default_factory=dict)
    latest_predictive_regime_overall_summary: dict = field(default_factory=dict)
    latest_safe_opportunity_queue_summary: dict = field(default_factory=dict)
    latest_safe_opportunity_queue_family_summary: dict = field(default_factory=dict)
    latest_safe_opportunity_queue_overall_summary: dict = field(default_factory=dict)
    latest_entry_timing_fusion_summary: dict = field(default_factory=dict)
    latest_entry_timing_fusion_family_summary: dict = field(default_factory=dict)
    latest_entry_timing_fusion_overall_summary: dict = field(default_factory=dict)
    latest_canary_audit_summary: dict = field(default_factory=dict)
    latest_canary_audit_family_summary: dict = field(default_factory=dict)
    latest_canary_audit_overall_summary: dict = field(default_factory=dict)
    latest_persistence_summary: dict = field(default_factory=dict)
    latest_persistence_family_summary: dict = field(default_factory=dict)
    latest_persistence_overall_summary: dict = field(default_factory=dict)
    latest_sports_harvest_summary: dict = field(default_factory=dict)
    latest_sports_harvest_family_summary: dict = field(default_factory=dict)
    latest_sports_harvest_overall_summary: dict = field(default_factory=dict)
    consciousness: dict = field(default_factory=dict)
    operational_summary: str = ""
    active_realms: list = field(default_factory=list)
    command_center: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)
    status: str = "waiting"
    health_reason: list = field(default_factory=list)
    last_updated: str = field(default_factory=_now_iso)
    last_source: str = "bootstrap"

    def to_dict(self):
        return {
            "service_status": _json_safe(self.service_status or {}),
            "live_mode": _json_safe(self.live_mode or {}),
            "armed_live": bool(self.armed_live),
            "execution_posture": _json_safe(self.execution_posture or {}),
            "current_phase": self.current_phase,
            "websocket_status": _json_safe(self.websocket_status or {}),
            "websocket_subscription_count": int(self.websocket_subscription_count or 0),
            "websocket_latency": self.websocket_latency,
            "websocket_stale_feed": bool(self.websocket_stale_feed),
            "latest_market_scan": _json_safe(self.latest_market_scan or {}),
            "latest_candidate_count": int(self.latest_candidate_count or 0),
            "latest_selected_ticker": self.latest_selected_ticker,
            "latest_execution_decision": _json_safe(self.latest_execution_decision or {}),
            "latest_execution_block_reason": self.latest_execution_block_reason,
            "latest_entry_timing_status": _json_safe(self.latest_entry_timing_status or {}),
            "latest_risk_state": _json_safe(self.latest_risk_state or {}),
            "latest_aggression_state": _json_safe(self.latest_aggression_state or {}),
            "latest_drawdown_state": _json_safe(self.latest_drawdown_state or {}),
            "latest_exposure_state": _json_safe(self.latest_exposure_state or {}),
            "latest_personality_summary": _json_safe(self.latest_personality_summary or {}),
            "latest_probability_summary": _json_safe(self.latest_probability_summary or {}),
            "latest_external_summary": _json_safe(self.latest_external_summary or {}),
            "latest_evolution_summary": _json_safe(self.latest_evolution_summary or {}),
            "latest_recommendations": _json_safe(self.latest_recommendations or []),
            "latest_narrative_summary": _json_safe(self.latest_narrative_summary or {}),
            "latest_microstructure_summary": _json_safe(self.latest_microstructure_summary or {}),
            "latest_microstructure_family_summary": _json_safe(self.latest_microstructure_family_summary or {}),
            "latest_microstructure_readiness_state": self.latest_microstructure_readiness_state,
            "latest_reactivation_summary": _json_safe(self.latest_reactivation_summary or {}),
            "latest_reactivation_family_summary": _json_safe(self.latest_reactivation_family_summary or {}),
            "latest_reactivation_overall_summary": _json_safe(self.latest_reactivation_overall_summary or {}),
            "latest_adaptive_priority_summary": _json_safe(self.latest_adaptive_priority_summary or {}),
            "latest_adaptive_priority_family_summary": _json_safe(self.latest_adaptive_priority_family_summary or {}),
            "latest_adaptive_priority_overall_summary": _json_safe(self.latest_adaptive_priority_overall_summary or {}),
            "latest_adaptive_confidence_summary": _json_safe(self.latest_adaptive_confidence_summary or {}),
            "latest_adaptive_confidence_family_summary": _json_safe(self.latest_adaptive_confidence_family_summary or {}),
            "latest_adaptive_confidence_overall_summary": _json_safe(self.latest_adaptive_confidence_overall_summary or {}),
            "latest_family_rotation_summary": _json_safe(self.latest_family_rotation_summary or {}),
            "latest_family_rotation_family_summary": _json_safe(self.latest_family_rotation_family_summary or {}),
            "latest_family_rotation_overall_summary": _json_safe(self.latest_family_rotation_overall_summary or {}),
            "latest_candidate_learning_summary": _json_safe(self.latest_candidate_learning_summary or {}),
            "latest_candidate_learning_family_summary": _json_safe(self.latest_candidate_learning_family_summary or {}),
            "latest_candidate_learning_overall_summary": _json_safe(self.latest_candidate_learning_overall_summary or {}),
            "latest_predictive_regime_summary": _json_safe(self.latest_predictive_regime_summary or {}),
            "latest_predictive_regime_family_summary": _json_safe(self.latest_predictive_regime_family_summary or {}),
            "latest_predictive_regime_overall_summary": _json_safe(self.latest_predictive_regime_overall_summary or {}),
            "latest_safe_opportunity_queue_summary": _json_safe(self.latest_safe_opportunity_queue_summary or {}),
            "latest_safe_opportunity_queue_family_summary": _json_safe(self.latest_safe_opportunity_queue_family_summary or {}),
            "latest_safe_opportunity_queue_overall_summary": _json_safe(self.latest_safe_opportunity_queue_overall_summary or {}),
            "latest_entry_timing_fusion_summary": _json_safe(self.latest_entry_timing_fusion_summary or {}),
            "latest_entry_timing_fusion_family_summary": _json_safe(self.latest_entry_timing_fusion_family_summary or {}),
            "latest_entry_timing_fusion_overall_summary": _json_safe(self.latest_entry_timing_fusion_overall_summary or {}),
            "latest_canary_audit_summary": _json_safe(self.latest_canary_audit_summary or {}),
            "latest_canary_audit_family_summary": _json_safe(self.latest_canary_audit_family_summary or {}),
            "latest_canary_audit_overall_summary": _json_safe(self.latest_canary_audit_overall_summary or {}),
            "latest_persistence_summary": _json_safe(self.latest_persistence_summary or {}),
            "latest_persistence_family_summary": _json_safe(self.latest_persistence_family_summary or {}),
            "latest_persistence_overall_summary": _json_safe(self.latest_persistence_overall_summary or {}),
            "latest_sports_harvest_summary": _json_safe(self.latest_sports_harvest_summary or {}),
            "latest_sports_harvest_family_summary": _json_safe(self.latest_sports_harvest_family_summary or {}),
            "latest_sports_harvest_overall_summary": _json_safe(self.latest_sports_harvest_overall_summary or {}),
            "consciousness": _json_safe(self.consciousness or {}),
            "operational_summary": self.operational_summary,
            "active_realms": _json_safe(self.active_realms or []),
            "command_center": _json_safe(self.command_center or {}),
            "health": _json_safe(self.health or {}),
            "status": self.status,
            "health_reason": _json_safe(self.health_reason or []),
            "last_updated": self.last_updated,
            "last_source": self.last_source,
        }

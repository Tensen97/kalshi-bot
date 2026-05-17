from __future__ import annotations

from copy import deepcopy

from .state_registry import get_live_state_registry


def _safe_dict(value):
    if isinstance(value, dict):
        return deepcopy(value)
    return {}


def _safe_list(value):
    if isinstance(value, list):
        return deepcopy(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def export_live_telemetry_snapshot(
    bot=None,
    source="live",
    registry=None,
    markets=None,
    quotes=None,
    candidates=None,
    selected_quote=None,
    live_select_summary=None,
    execution_block_reason=None,
    entry_timing_status=None,
):
    registry = registry or get_live_state_registry()
    bot = bot or None
    live_select_summary = _safe_dict(live_select_summary or getattr(bot, "last_live_select_summary", {}) or {})
    selected_ticker = getattr(selected_quote, "ticker", None) if selected_quote is not None else live_select_summary.get("selected_ticker")
    current_phase = live_select_summary.get("phase", live_select_summary.get("current_phase", "live_cycle"))
    websocket_summary = _safe_dict(getattr(bot, "last_websocket_summary", {}) or {})
    personality_summary = _safe_dict(getattr(bot, "last_realm_xxi_summary", {}) or {})
    probability_summary = _safe_dict(getattr(bot, "last_realm_xxiii_summary", {}) or {})
    risk_summary = _safe_dict(getattr(bot, "last_realm_xxiv_summary", {}) or {})
    external_summary = _safe_dict(getattr(bot, "last_realm_xxv_summary", {}) or {})
    evolution_summary = _safe_dict(getattr(bot, "last_realm_xxvi_summary", {}) or {})
    command_center_summary = _safe_dict(getattr(bot, "last_realm_xxvii_summary", {}) or {})
    consciousness_summary = _safe_dict(getattr(bot, "last_realm_xxx_summary", {}) or {})
    live_mode = bool(getattr(bot, "live_mode", False)) if bot is not None else False
    live_armed = bool(getattr(bot, "live_armed", False)) if bot is not None else False
    execution_posture = _safe_dict(entry_timing_status or live_select_summary.get("execution_posture") or {})
    if not execution_posture:
        execution_posture = {
            "posture": "WAITING_FOR_SIGNAL" if live_mode else "OFFLINE",
            "phase": current_phase,
        }
    latest_market_scan = {
        "market_count": len(markets or []),
        "candidate_count": len(candidates or []),
        "selected_ticker": selected_ticker,
        "selected_family": live_select_summary.get("selected_family") or live_select_summary.get("family"),
        "selected_score": live_select_summary.get("selected_score"),
    }
    latest_execution_decision = {
        "decision": live_select_summary.get("decision") or live_select_summary.get("final_decision") or live_select_summary.get("decision_result"),
        "reason": live_select_summary.get("decision_reason") or live_select_summary.get("reason"),
        "confidence": live_select_summary.get("confidence") or live_select_summary.get("decision_confidence"),
    }
    snapshot = registry.update_many(
        {
            "service_status": {
                "service_active": bool(bot is not None),
                "bot_attached": bool(bot is not None),
                "source": source,
            },
            "live_mode": {
                "active": live_mode,
                "armed": live_armed,
                "env_confirmed": bool(getattr(bot, "live_env_confirmed", False)) if bot is not None else False,
                "posture": execution_posture.get("posture") or execution_posture.get("mode") or "WAITING_FOR_SIGNAL",
            },
            "armed_live": live_armed,
            "execution_posture": execution_posture,
            "current_phase": current_phase,
            "websocket_status": websocket_summary,
            "websocket_subscription_count": websocket_summary.get("subscription_count", websocket_summary.get("active_subscriptions", 0) or 0),
            "websocket_latency": websocket_summary.get("latency_ms", websocket_summary.get("latency")),
            "websocket_stale_feed": bool(websocket_summary.get("stale_feed") or websocket_summary.get("feed_stale")),
            "latest_market_scan": latest_market_scan,
            "latest_candidate_count": len(candidates or []),
            "latest_selected_ticker": selected_ticker,
            "latest_execution_decision": latest_execution_decision,
            "latest_execution_block_reason": execution_block_reason or live_select_summary.get("execution_block_reason") or live_select_summary.get("block_reason"),
            "latest_entry_timing_status": _safe_dict(entry_timing_status or live_select_summary.get("entry_timing_status") or {}),
            "latest_risk_state": _safe_dict(risk_summary.get("last_global_state") or risk_summary),
            "latest_aggression_state": {
                "aggression_level": (risk_summary.get("last_global_state") or risk_summary or {}).get("aggression_level"),
                "capital_state": (risk_summary.get("last_global_state") or risk_summary or {}).get("capital_state"),
            },
            "latest_drawdown_state": {
                "drawdown_pct": (risk_summary.get("last_global_state") or risk_summary or {}).get("drawdown_pct"),
                "soft_circuit_active": (risk_summary.get("last_global_state") or risk_summary or {}).get("soft_circuit_active"),
            },
            "latest_exposure_state": {
                "total_exposure_dollars": (risk_summary.get("last_global_state") or risk_summary or {}).get("total_exposure_dollars"),
                "family_concentration": (risk_summary.get("last_global_state") or risk_summary or {}).get("family_concentration"),
                "correlated_concentration": (risk_summary.get("last_global_state") or risk_summary or {}).get("correlated_concentration"),
            },
            "latest_personality_summary": personality_summary,
            "latest_probability_summary": probability_summary,
            "latest_external_summary": external_summary,
            "latest_evolution_summary": evolution_summary,
            "latest_recommendations": _safe_list((evolution_summary.get("recommendations") if isinstance(evolution_summary, dict) else None) or []),
            "latest_narrative_summary": consciousness_summary,
            "latest_microstructure_summary": _safe_dict(getattr(bot, "last_live_microstructure_summary", {}) or {}),
            "latest_microstructure_family_summary": _safe_dict(getattr(bot, "last_live_microstructure_family_summary", {}) or {}),
            "latest_microstructure_readiness_state": getattr(bot, "last_live_microstructure_readiness_state", None),
            "latest_reactivation_summary": _safe_dict(getattr(bot, "last_reactivation_summary", {}) or {}),
            "latest_reactivation_family_summary": _safe_dict(getattr(bot, "last_reactivation_family_summary", {}) or {}),
            "latest_reactivation_overall_summary": _safe_dict(getattr(bot, "last_reactivation_overall_summary", {}) or {}),
            "latest_adaptive_priority_summary": _safe_dict(getattr(bot, "last_adaptive_priority_summary", {}) or {}),
            "latest_adaptive_priority_family_summary": _safe_dict(getattr(bot, "last_adaptive_priority_family_summary", {}) or {}),
            "latest_adaptive_priority_overall_summary": _safe_dict(getattr(bot, "last_adaptive_priority_overall_summary", {}) or {}),
            "latest_adaptive_confidence_summary": _safe_dict(getattr(bot, "last_adaptive_confidence_summary", {}) or {}),
            "latest_adaptive_confidence_family_summary": _safe_dict(getattr(bot, "last_adaptive_confidence_family_summary", {}) or {}),
            "latest_adaptive_confidence_overall_summary": _safe_dict(getattr(bot, "last_adaptive_confidence_overall_summary", {}) or {}),
            "latest_family_rotation_summary": _safe_dict(getattr(bot, "last_family_rotation_summary", {}) or {}),
            "latest_family_rotation_family_summary": _safe_dict(getattr(bot, "last_family_rotation_family_summary", {}) or {}),
            "latest_family_rotation_overall_summary": _safe_dict(getattr(bot, "last_family_rotation_overall_summary", {}) or {}),
            "latest_candidate_learning_summary": _safe_dict(getattr(bot, "last_candidate_learning_summary", {}) or {}),
            "latest_candidate_learning_family_summary": _safe_dict(getattr(bot, "last_candidate_learning_family_summary", {}) or {}),
            "latest_candidate_learning_overall_summary": _safe_dict(getattr(bot, "last_candidate_learning_overall_summary", {}) or {}),
            "latest_predictive_regime_summary": _safe_dict(getattr(bot, "last_predictive_regime_summary", {}) or {}),
            "latest_predictive_regime_family_summary": _safe_dict(getattr(bot, "last_predictive_regime_family_summary", {}) or {}),
            "latest_predictive_regime_overall_summary": _safe_dict(getattr(bot, "last_predictive_regime_overall_summary", {}) or {}),
            "latest_safe_opportunity_queue_summary": _safe_dict(getattr(bot, "last_safe_opportunity_queue_summary", {}) or {}),
            "latest_safe_opportunity_queue_family_summary": _safe_dict(getattr(bot, "last_safe_opportunity_queue_family_summary", {}) or {}),
            "latest_safe_opportunity_queue_overall_summary": _safe_dict(getattr(bot, "last_safe_opportunity_queue_overall_summary", {}) or {}),
            "latest_entry_timing_fusion_summary": _safe_dict(getattr(bot, "last_entry_timing_fusion_summary", {}) or {}),
            "latest_entry_timing_fusion_family_summary": _safe_dict(getattr(bot, "last_entry_timing_fusion_family_summary", {}) or {}),
            "latest_entry_timing_fusion_overall_summary": _safe_dict(getattr(bot, "last_entry_timing_fusion_overall_summary", {}) or {}),
            "latest_canary_audit_summary": _safe_dict(getattr(bot, "last_canary_audit_summary", {}) or {}),
            "latest_canary_audit_family_summary": _safe_dict(getattr(bot, "last_canary_audit_family_summary", {}) or {}),
            "latest_canary_audit_overall_summary": _safe_dict(getattr(bot, "last_canary_audit_overall_summary", {}) or {}),
            "latest_persistence_summary": _safe_dict(getattr(bot, "last_opportunity_persistence_summary", {}) or {}),
            "latest_persistence_family_summary": _safe_dict(getattr(bot, "last_opportunity_persistence_family_summary", {}) or {}),
            "latest_persistence_overall_summary": _safe_dict(getattr(bot, "last_opportunity_persistence_overall_summary", {}) or {}),
            "latest_sports_harvest_summary": _safe_dict(getattr(bot, "last_sports_harvest_summary", {}) or {}),
            "latest_sports_harvest_family_summary": _safe_dict(getattr(bot, "last_sports_harvest_family_summary", {}) or {}),
            "latest_sports_harvest_overall_summary": _safe_dict(getattr(bot, "last_sports_harvest_overall_summary", {}) or {}),
            "latest_aion_intelligence_summary": _safe_dict(getattr(bot, "last_aion_intelligence_summary", {}) or {}),
            "latest_aion_family_profile_summary": _safe_dict(getattr(bot, "last_aion_family_profile_summary", {}) or {}),
            "latest_aion_ticker_behavior_summary": _safe_dict(getattr(bot, "last_aion_ticker_behavior_summary", {}) or {}),
            "latest_aion_infra_dashboard_summary": _safe_dict(getattr(bot, "last_aion_infra_dashboard_summary", {}) or {}),
            "consciousness": consciousness_summary,
            "operational_summary": consciousness_summary.get("summary") if isinstance(consciousness_summary, dict) else "",
            "active_realms": _safe_list(bot._realm_flags(bot) if bot is not None and hasattr(bot, "_realm_flags") else getattr(bot, "last_seraph_state", {}).get("active_realms") if bot is not None else []),
            "command_center": command_center_summary,
            "health": {},
            "status": "live" if bot is not None else "waiting",
            "last_updated": live_select_summary.get("timestamp") or live_select_summary.get("updated_at") or live_select_summary.get("created_at"),
            "last_source": source,
        },
        source=source,
    )
    print(
        "[EXECUTION-POSTURE] "
        f"posture={execution_posture.get('posture') or execution_posture.get('mode') or 'UNKNOWN'} "
        f"phase={current_phase} selected_ticker={selected_ticker or 'none'}"
    )
    print(
        "[SERAPH-EXECUTION-POSTURE] "
        f"posture={execution_posture.get('posture') or execution_posture.get('mode') or 'UNKNOWN'} "
        f"status={snapshot.get('status')}"
    )
    registry._log(
        "[TELEMETRY-HYDRATED]",
        source=source,
        selected_ticker=selected_ticker,
        candidate_count=len(candidates or []),
        websocket_state=websocket_summary.get("health") or websocket_summary.get("connection_state"),
    )
    return snapshot

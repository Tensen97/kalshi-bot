from __future__ import annotations

from .ecosystem_awareness import EcosystemAwareness
from .operational_summary import OperationalSummaryBuilder
from .state_interpreter import StateInterpreter


class NarrativeEngine:
    def __init__(self, enabled=False):
        self.enabled = bool(enabled)
        self.interpreter = StateInterpreter()
        self.summary_builder = OperationalSummaryBuilder()
        self.awareness = EcosystemAwareness()

    def build(self, state=None, source="live"):
        state = state or {}
        if not self.enabled:
            summary = state.get("operational_summary") or state.get("summary_text") or ""
            return {
                "enabled": False,
                "status": state.get("status", "waiting"),
                "summary": summary,
                "sections": {},
                "awareness": {},
            }
        sections = self.interpreter.interpret(state)
        summary = self.summary_builder.build(state, sections)
        awareness = self.awareness.build(state, sections, summary)
        print(
            "[NARRATIVE-GENERATED] "
            f"source={source} status={awareness.get('status')} readiness={awareness.get('readiness')}"
        )
        print(
            "[STATE-INTERPRETER] "
            f"execution={sections.get('execution', {}).get('posture')} "
            f"websocket={sections.get('websocket', {}).get('status')} "
            f"risk={sections.get('risk', {}).get('capital_state')}"
        )
        print(
            "[ECOSYSTEM-AWARENESS] "
            f"health={awareness.get('health_state')} "
            f"warnings={len(awareness.get('warnings', []) or [])} "
            f"active_blocks={len(awareness.get('active_blocks', []) or [])}"
        )
        return {
            "enabled": True,
            "status": awareness.get("status", state.get("status", "waiting")),
            "summary": summary,
            "sections": sections,
            "awareness": awareness,
            "narrative": summary,
            "operational_summary": summary,
            "readiness": awareness.get("readiness"),
            "health_state": awareness.get("health_state"),
            "health_score": awareness.get("health_score"),
        }


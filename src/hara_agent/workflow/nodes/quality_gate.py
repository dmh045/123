from __future__ import annotations

from hara_agent.models import ReviewStatus
from hara_agent.workflow.state import HARAState, WorkflowStage


def pass_quality_gate(state: HARAState) -> HARAState:
    pending_values = []
    rejected_values = []
    for risk in state.risk_results:
        for field in ("severity", "exposure", "controllability", "asil", "ftti_seconds"):
            status = getattr(risk, field).status
            if status is ReviewStatus.PENDING:
                pending_values.append(f"{risk.assessment_id}.{field}")
            elif status is ReviewStatus.REJECTED:
                rejected_values.append(f"{risk.assessment_id}.{field}")
    pending_goals = [goal.sg_id for goal in state.safety_goals if goal.status is ReviewStatus.PENDING]
    rejected_goals = [goal.sg_id for goal in state.safety_goals if goal.status is ReviewStatus.REJECTED]
    if pending_values or rejected_values or pending_goals or rejected_goals or state.pending_reviews or state.errors:
        state.record(
            "quality_gate_blocked",
            pending_risk_fields=len(pending_values),
            rejected_risk_fields=len(rejected_values),
            pending_safety_goals=len(pending_goals),
            rejected_safety_goals=len(rejected_goals),
            pending_reviews=len(state.pending_reviews),
            errors=len(state.errors),
        )
        return state
    state.record("quality_gate_passed")
    state.stage = WorkflowStage.RENDER
    return state

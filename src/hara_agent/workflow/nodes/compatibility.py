from __future__ import annotations

from typing import Any

from hara_agent.compatibility import V13ScoringAdapter
from hara_agent.models import ReviewStatus
from hara_agent.workflow.state import HARAState, WorkflowStage


def import_v13_scoring(state: HARAState, scoring_data: dict[str, Any]) -> HARAState:
    """Import a legacy phase-3 result into typed workflow state."""
    adapter = V13ScoringAdapter(scoring_data)
    state.scenarios = adapter.scenarios()
    state.risk_results = adapter.risks()
    state.safety_goals = adapter.safety_goals()

    metadata = scoring_data.get("metadata", {})
    profile = metadata.get("domain_profile", {}) or {}
    state.domain = str(profile.get("name") or metadata.get("subsystem", ""))
    state.profile_version = str(profile.get("version", ""))
    state.pending_reviews = []

    for scenario in state.scenarios:
        if scenario.status is ReviewStatus.PENDING:
            state.pending_reviews.append({
                "type": "scenario_evidence",
                "scenario_id": scenario.scenario_id,
                "reason": scenario.review_reason,
            })
    for risk in state.risk_results:
        for dimension in ("severity", "exposure", "controllability", "ftti_seconds"):
            evidence = getattr(risk, dimension)
            if evidence.status is ReviewStatus.PENDING:
                state.pending_reviews.append({
                    "type": "risk_evidence",
                    "assessment_id": risk.assessment_id,
                    "scenario_id": risk.scenario_id,
                    "dimension": dimension,
                    "reason": evidence.review_reason,
                })
    for goal in state.safety_goals:
        if goal.status is ReviewStatus.PENDING:
            state.pending_reviews.append({
                "type": "safety_goal",
                "sg_id": goal.sg_id,
                "reason": "Domain Profile或FTTI尚未批准",
            })

    state.stage = WorkflowStage.QUALITY_GATE
    state.record(
        "v13_scoring_imported",
        scenarios=len(state.scenarios),
        risk_assessments=len(state.risk_results),
        safety_goals=len(state.safety_goals),
        pending_reviews=len(state.pending_reviews),
    )
    return state


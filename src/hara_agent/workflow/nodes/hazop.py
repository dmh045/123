from __future__ import annotations

from dataclasses import asdict

from hara_agent.models import FunctionDefinition
from hara_agent.services.semantic import GuidewordApplicabilityAgent
from hara_agent.workflow.state import HARAState, WorkflowStage

from .parallel import ordered_parallel_map


def assess_guidewords(state: HARAState, agent: GuidewordApplicabilityAgent,
                      functions: list[FunctionDefinition], guidewords: list[str],
                      max_workers: int = 1,
                      progress=None) -> HARAState:
    assessments = []
    batches = ordered_parallel_map(
        functions,
        lambda function: agent.assess(function, guidewords),
        max_workers=max_workers,
        on_progress=(lambda done, total: progress("guideword", done, total)) if progress else None,
    )
    for items, audit in batches:
        assessments.extend(items)
        state.record("guideword_applicability_assessed", **audit)
    state.malfunctions = [{"guideword_assessment": asdict(item)} for item in assessments]
    incomplete_count = sum(not item.is_semantically_complete for item in assessments)
    review_pending_count = sum(
        item.status.value == "PENDING" and item.is_semantically_complete
        for item in assessments
    )
    if incomplete_count:
        state.pending_reviews.append({
            "field": "guideword_assessments",
            "issue_type": "semantic_incomplete",
            "reason": f"{incomplete_count}个Guideword适用性结论语义字段不完整",
        })
    if review_pending_count:
        state.pending_reviews.append({
            "field": "guideword_assessments",
            "issue_type": "pending_review",
            "reason": f"{review_pending_count}个完整Guideword适用性结论尚未完成工程审批",
        })
    blanket_function_ids = [
        str(audit.get("function_id", ""))
        for _, audit in batches
        if audit.get("blanket_applicability") is True
    ]
    if blanket_function_ids:
        state.record(
            "guideword_blanket_applicability_observed",
            function_ids=blanket_function_ids,
            function_count=len(blanket_function_ids),
            action="continue_to_malfunction_and_causal_filters",
        )
    state.stage = WorkflowStage.HAZOP
    return state

from __future__ import annotations

from hara_agent.models import ReviewStatus, SafetyGoal
from hara_agent.services.analysis import SafetyGoalService
from hara_agent.workflow.state import HARAState, WorkflowStage


def aggregate_safety_goals(
    state: HARAState,
    catalog: SafetyGoalService,
) -> HARAState:
    """Map non-QM risks to approved-domain SG definitions and aggregate them."""
    malfunctions = {
        str(item.get("malfunction_id", "")): item for item in state.malfunctions
    }
    functions = {
        str(item.get("function_id", "")): item for item in state.functions
    }
    scenario_ids_by_goal: dict[str, list[str]] = {}
    risk_status_by_goal: dict[str, list[ReviewStatus]] = {}

    for risk in state.risk_results:
        if risk.asil.value in {"QM", "NA", "N/A", ""}:
            continue
        malfunction = malfunctions.get(risk.malfunction_id)
        if malfunction is None:
            raise ValueError(f"Safety Goal聚合缺少Malfunction外键: {risk.malfunction_id!r}")
        function = functions.get(str(malfunction.get("function_id", "")))
        if function is None:
            raise ValueError(f"Safety Goal聚合缺少Function外键: {malfunction.get('function_id')!r}")
        function_name = str(function.get("name", ""))
        sg_id = catalog.classify(function_name)
        if not sg_id:
            raise ValueError(f"Domain Profile缺少安全相关Function的SG映射: {function_name!r}")
        ftti_result = {
            "ftti_value_s": risk.ftti_seconds.value,
            "ftti_status": (
                "FINALIZED" if risk.ftti_seconds.status is ReviewStatus.FINALIZED
                else "NEEDS_REVIEW"
            ),
        }
        catalog.register(
            sg_id=sg_id,
            function_name=function_name,
            malfunction=str(malfunction.get("description", "")),
            guideword=str(malfunction.get("guideword", "")),
            scenario_id=risk.scenario_id,
            hazard_event=risk.hazardous_event,
            asil=str(risk.asil.value),
            ftti_result=ftti_result,
        )
        risk.safety_goal_id = sg_id
        scenario_ids_by_goal.setdefault(sg_id, []).append(risk.scenario_id)
        risk_status_by_goal.setdefault(sg_id, []).extend([
            risk.severity.status,
            risk.exposure.status,
            risk.controllability.status,
            risk.asil.status,
            risk.ftti_seconds.status,
        ])

    goals = []
    for sg_id, value in catalog.to_dict().items():
        statuses = risk_status_by_goal[sg_id]
        finalized = catalog.is_approved and all(
            status in {ReviewStatus.FINALIZED, ReviewStatus.NOT_APPLICABLE}
            for status in statuses
        )
        status = ReviewStatus.FINALIZED if finalized else ReviewStatus.PENDING
        goals.append(SafetyGoal(
            sg_id=sg_id,
            text=str(value["safety_goal"]),
            safe_state=str(value["safety_state"]),
            max_asil=str(value["max_asil"]),
            ftti_seconds=value.get("ftti_value_s"),
            associated_scenario_ids=list(dict.fromkeys(scenario_ids_by_goal[sg_id])),
            status=status,
        ))
        if status is ReviewStatus.PENDING:
            state.pending_reviews.append({
                "safety_goal_id": sg_id,
                "field": "safety_goal",
                "reason": "Domain Profile或关联风险判据尚未全部批准",
            })

    state.safety_goals = goals
    state.stage = WorkflowStage.QUALITY_GATE
    state.record(
        "safety_goals_aggregated",
        non_qm_risk_count=sum(
            risk.asil.value not in {"QM", "NA", "N/A", ""}
            for risk in state.risk_results
        ),
        safety_goal_count=len(goals),
    )
    return state

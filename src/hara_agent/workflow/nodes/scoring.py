from __future__ import annotations

from typing import Any

from hara_agent.models import EvidenceValue, ReviewStatus, RiskAssessment, SourceRef
from hara_agent.services.analysis import ASILLookupService, FTTIService, ScenarioScoringService
from hara_agent.workflow.state import HARAState, WorkflowStage


def _unique_index(values, key, label: str):
    result = {}
    for value in values:
        identity = key(value)
        if not identity:
            raise ValueError(f"{label} ID不能为空")
        if identity in result:
            raise ValueError(f"{label} ID不唯一，禁止静默覆盖: {identity!r}")
        result[identity] = value
    return result


def _review_status(value: str) -> ReviewStatus:
    return ReviewStatus.FINALIZED if str(value).upper() in {"FINALIZED", "APPROVED"} else ReviewStatus.PENDING


def _scoring_evidence(result: dict[str, Any], value_key: str) -> EvidenceValue[str]:
    status = _review_status(result.get("engineering_status", "PENDING"))
    sources = [SourceRef(
        str(result.get("engineering_source_type", "scoring_service")),
        str(result.get("engineering_source", "migration-only-domain-policy")),
        str(result.get("engineering_rule_id", "")),
        str(result.get("engineering_basis", "")),
    )]
    if result.get("template_standard_source"):
        contract_hash = str(result.get("template_standard_contract_hash", ""))
        sources.append(SourceRef(
            "method_contract" if contract_hash else "input_template",
            contract_hash or str(result["template_standard_source"]),
            f"{result.get('template_standard_sheet', '')}!{result.get('template_standard_location', '')}",
            "；".join(value for value in (
                str(result.get("template_standard_description", "")),
                str(result.get("template_standard_criterion", "")),
            ) if value),
        ))
    return EvidenceValue(
        value=str(result[value_key]),
        status=status,
        sources=sources,
        rule_version=str(result.get("engineering_rule_version", "")),
        review_reason="" if status is ReviewStatus.FINALIZED else "Domain评分判据尚未获得项目批准",
    )


def score_structured_scenarios(
    state: HARAState,
    scoring: ScenarioScoringService,
    asil_table: ASILLookupService,
    ftti: FTTIService,
) -> HARAState:
    """Score each retained malfunction-scenario association without prose heuristics."""
    scenario_by_id = _unique_index(state.scenarios, lambda item: item.scenario_id, "Scenario")
    malfunction_by_id = _unique_index(
        state.malfunctions, lambda item: str(item.get("malfunction_id", "")), "Malfunction",
    )
    assessments = state.item_definition.get("scenario_assessments", [])
    retained = [item for item in assessments if all((
        item.get("physically_feasible") is True,
        item.get("functionally_relevant") is True,
        item.get("causally_relevant") is True,
        bool(item.get("risk_dimensions_changed")),
    ))]
    risks: list[RiskAssessment] = []
    pending: list[dict[str, Any]] = []

    for index, assessment in enumerate(retained, start=1):
        scenario_id = str(assessment.get("scenario_id", ""))
        malfunction_id = str(assessment.get("malfunction_id", ""))
        if scenario_id not in scenario_by_id or malfunction_id not in malfunction_by_id:
            raise ValueError(
                f"场景评分外键缺失: scenario_id={scenario_id!r}, malfunction_id={malfunction_id!r}"
            )
        candidate = scenario_by_id[scenario_id]
        scenario = {"scenario_id": scenario_id, **candidate.facts}
        scenario.setdefault("situational_description", candidate.situational_description)
        scenario.setdefault("situational_detailing", candidate.situational_detailing)
        hazard_event = str(assessment.get("hazardous_event", ""))
        potential_harm = str(assessment.get("potential_harm", ""))
        if not hazard_event or not potential_harm:
            raise ValueError(
                f"保留场景缺少Hazardous Event/Potential Harm: {malfunction_id}/{scenario_id}"
            )

        scored = scoring.score(scenario, hazard_event)
        severity = _scoring_evidence(scored["severity"], "severity_score")
        exposure = _scoring_evidence(scored["exposure"], "exposure_score")
        controllability = _scoring_evidence(
            scored["controllability"], "controllability_score"
        )
        exposure_method = str(scored["exposure"].get("exposure_method", "")).upper()
        asil_value = asil_table.determine(severity.value, exposure.value, controllability.value)
        asil_status = (
            ReviewStatus.FINALIZED
            if asil_value not in {"NA", "N/A"}
            and all(
                item.status is ReviewStatus.FINALIZED
                for item in (severity, exposure, controllability)
            )
            else ReviewStatus.PENDING
        )
        asil = EvidenceValue(
            value=asil_value,
            status=asil_status,
            sources=[asil_table.evidence_source(
                severity.value, exposure.value, controllability.value
            )],
            review_reason="" if asil_status is ReviewStatus.FINALIZED else "ASIL查表输入S/E/C尚未全部批准",
        )
        ftti_result = ftti.evaluate(scenario, hazard_event, asil_value)
        ftti_status = (
            ReviewStatus.NOT_APPLICABLE
            if ftti_result["ftti_status"] == "NOT_REQUIRED"
            else _review_status(ftti_result["ftti_status"])
        )
        ftti_value = EvidenceValue(
            value=ftti_result.get("ftti_value_s"),
            status=ftti_status,
            sources=[SourceRef(
                "ftti_basis", str(ftti_result.get("source", "")),
                str(ftti_result.get("formula_id", "")), str(ftti_result.get("basis", "")),
            )],
            review_reason=str(ftti_result.get("review_reason", "")),
        )
        risk = RiskAssessment(
            assessment_id=f"RA-{index:04d}-{malfunction_id}-{scenario_id}",
            scenario_id=scenario_id,
            severity=severity,
            exposure=exposure,
            controllability=controllability,
            asil=asil,
            ftti_seconds=ftti_value,
            malfunction_id=malfunction_id,
            hazardous_event=hazard_event,
            potential_harm=potential_harm,
            exposure_tf=exposure_method,
        )
        risks.append(risk)
        for field_name in ("severity", "exposure", "controllability", "asil", "ftti_seconds"):
            evidence = getattr(risk, field_name)
            if evidence.status is ReviewStatus.PENDING:
                pending.append({
                    "assessment_id": risk.assessment_id,
                    "field": field_name,
                    "reason": evidence.review_reason,
                })

    state.risk_results = risks
    state.pending_reviews.extend(pending)
    state.stage = WorkflowStage.SAFETY_GOALS
    state.record(
        "structured_risk_scoring_completed",
        assessment_count=len(risks),
        pending_field_count=len(pending),
        asil_source=asil_table.source,
    )
    return state

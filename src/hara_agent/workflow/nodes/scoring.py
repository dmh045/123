from __future__ import annotations

from typing import Any

from hara_agent.contracts import ScenarioCausalAssessment
from hara_agent.models import (
    EvidenceValue, ItemDefinitionFacts, ReviewStatus, RiskAssessment, SourceRef,
)
from hara_agent.services.analysis import (
    ASILLookupService, MethodRiskFactBindingService,
    ScenarioScoringService,
)
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
    sources = []
    engineering_source = str(result.get("engineering_source", "")).strip()
    if engineering_source:
        sources.append(SourceRef(
            str(result.get("engineering_source_type", "scoring_service")),
            engineering_source,
            str(result.get("engineering_location", result.get("engineering_rule_id", ""))),
            str(result.get("engineering_excerpt", result.get("engineering_basis", ""))),
        ))
    for source in result.get("fact_sources", []):
        if isinstance(source, dict):
            sources.append(SourceRef(
                str(source.get("source_type", "project_fact")),
                str(source.get("source_id", "")),
                str(source.get("location", "")),
                str(source.get("excerpt", "")),
            ))
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
        review_reason=(
            "" if status is ReviewStatus.FINALIZED
            else str(result.get("engineering_basis", "Scoring evidence is pending."))
        ),
    )


def _validated_causal_assessment(value: dict[str, Any]) -> ScenarioCausalAssessment:
    payload = value.get("causal_assessment")
    if not isinstance(payload, dict):
        raise ValueError(
            "Scenario scoring requires a typed causal_assessment; legacy flags are not authoritative"
        )
    try:
        assessment = ScenarioCausalAssessment.from_dict(payload)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Scenario causal_assessment failed deterministic validation") from error
    dimensions = [item.dimension for item in assessment.risk_dimension_changes]
    consistent = all((
        assessment.scenario_id == str(value.get("scenario_id", "")),
        assessment.is_validated is (value.get("causally_relevant") is True),
        assessment.breakpoint is not None,
        assessment.breakpoint.value == str(value.get("breakpoint", "")),
        dimensions == list(value.get("risk_dimensions_changed", [])),
        assessment.hazardous_event == str(value.get("hazardous_event", "")),
        assessment.potential_harm == str(value.get("potential_harm", "")),
    ))
    if not consistent:
        raise ValueError("Scenario causal_assessment conflicts with compatibility fields")
    return assessment


def score_structured_scenarios(
    state: HARAState,
    scoring: ScenarioScoringService,
    asil_table: ASILLookupService,
    risk_fact_binding: MethodRiskFactBindingService | None = None,
) -> HARAState:
    """Score each retained malfunction-scenario association without prose heuristics."""
    scenario_by_id = _unique_index(state.scenarios, lambda item: item.scenario_id, "Scenario")
    malfunction_by_id = _unique_index(
        state.malfunctions, lambda item: str(item.get("malfunction_id", "")), "Malfunction",
    )
    assessments = state.item_definition.get("scenario_assessments", [])
    typed_assessments = [
        (item, _validated_causal_assessment(item)) for item in assessments
    ]
    retained = [(item, causal) for item, causal in typed_assessments if all((
        item.get("status") == ReviewStatus.FINALIZED.value,
        causal.review_status is ReviewStatus.FINALIZED,
        item.get("physically_feasible") is True,
        item.get("functionally_relevant") is True,
        causal.is_validated,
    ))]
    risks: list[RiskAssessment] = []
    pending: list[dict[str, Any]] = []
    binding_audits: list[dict[str, Any]] = []
    project_facts = (
        ItemDefinitionFacts.from_dict(state.item_definition["typed"])
        if risk_fact_binding is not None else None
    )

    for index, (assessment, causal_assessment) in enumerate(retained, start=1):
        scenario_id = str(assessment.get("scenario_id", ""))
        malfunction_id = str(assessment.get("malfunction_id", ""))
        if scenario_id not in scenario_by_id or malfunction_id not in malfunction_by_id:
            raise ValueError(
                f"场景评分外键缺失: scenario_id={scenario_id!r}, malfunction_id={malfunction_id!r}"
            )
        candidate = scenario_by_id[scenario_id]
        scenario = {"scenario_id": scenario_id, **candidate.facts}
        scenario["_fact_provenance"] = dict(candidate.fact_provenance)
        scenario.setdefault("situational_description", candidate.situational_description)
        scenario.setdefault("situational_detailing", candidate.situational_detailing)
        if risk_fact_binding is not None and project_facts is not None:
            binding = risk_fact_binding.bind(project_facts, {
                **scenario,
                "malfunction_id": malfunction_id,
                "scenario_id": scenario_id,
                "atomic_variant": candidate.atomic_variant,
            })
            scenario.update(binding.values)
            scenario["_fact_provenance"].update(binding.provenance)
            binding_audits.append({
                "malfunction_id": malfunction_id,
                "scenario_id": scenario_id,
                **binding.audit,
            })
        hazard_event = causal_assessment.hazardous_event
        potential_harm = causal_assessment.potential_harm
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
        valid_scores = (
            severity.value in {"S0", "S1", "S2", "S3"}
            and exposure.value in {"E0", "E1", "E2", "E3", "E4"}
            and controllability.value in {"C0", "C1", "C2", "C3"}
        )
        asil_value = (
            asil_table.determine(
                severity.value, exposure.value, controllability.value
            )
            if valid_scores else ""
        )
        asil_status = (
            ReviewStatus.FINALIZED
            if asil_value and asil_value not in {"NA", "N/A"}
            and all(
                item.status is ReviewStatus.FINALIZED
                for item in (severity, exposure, controllability)
            )
            else ReviewStatus.PENDING
        )
        asil = EvidenceValue(
            value=asil_value,
            status=asil_status,
            sources=(
                [asil_table.evidence_source(
                    severity.value, exposure.value, controllability.value
                )]
                if valid_scores else []
            ),
            review_reason=(
                "" if asil_status is ReviewStatus.FINALIZED
                else "ASIL lookup requires complete, approved S/E/C values."
            ),
        )
        risk = RiskAssessment(
            assessment_id=f"RA-{index:04d}-{malfunction_id}-{scenario_id}",
            scenario_id=scenario_id,
            severity=severity,
            exposure=exposure,
            controllability=controllability,
            asil=asil,
            malfunction_id=malfunction_id,
            hazardous_event=hazard_event,
            potential_harm=potential_harm,
            exposure_tf=exposure_method,
        )
        risks.append(risk)
        for field_name in ("severity", "exposure", "controllability", "asil"):
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
        risk_fact_binding_audits=binding_audits,
    )
    return state

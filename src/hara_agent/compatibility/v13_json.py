from __future__ import annotations

from typing import Any

from hara_agent.models import (
    EvidenceValue,
    ReviewStatus,
    RiskAssessment,
    SafetyGoal,
    ScenarioCandidate,
    SourceRef,
)


class V13ScoringAdapter:
    """Convert legacy phase-3 JSON into typed Agent state during migration."""

    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.metadata = data.get("metadata", {})

    @staticmethod
    def _by_scenario(value: Any) -> dict[str, dict[str, Any]]:
        found = {}

        def walk(node: Any):
            if isinstance(node, dict):
                scenario_id = str(node.get("scenario_id", ""))
                if scenario_id:
                    found[scenario_id] = node
                else:
                    for child in node.values():
                        walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return found

    @staticmethod
    def _leaves(value: Any) -> list[dict[str, Any]]:
        found = []

        def walk(node: Any):
            if isinstance(node, dict):
                if "scenario_id" in node:
                    found.append(node)
                else:
                    for child in node.values():
                        walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return found

    @staticmethod
    def _status(value: str) -> ReviewStatus:
        normalized = str(value or "").upper()
        if normalized in {"FINALIZED", "APPROVED"}:
            return ReviewStatus.FINALIZED
        if normalized in {"REJECTED"}:
            return ReviewStatus.REJECTED
        if normalized in {"NOT_APPLICABLE", "NOT_REQUIRED"}:
            return ReviewStatus.NOT_APPLICABLE
        return ReviewStatus.PENDING

    def scenarios(self) -> list[ScenarioCandidate]:
        result = []
        for scenario_id, item in self.data.get("scenario_catalog", {}).items():
            reserved = {
                "scenario_id", "operating_scenario", "situational_description",
                "situational_detailing", "engineering_status", "rule_version", "review_reason",
            }
            sources = [
                SourceRef("project_input", str(source_id), str(field))
                for field, source_id in item.get("parameter_sources", {}).items()
            ]
            result.append(ScenarioCandidate(
                scenario_id=scenario_id,
                operating_scenario=str(item.get("operating_scenario", "")),
                situational_description=str(item.get("situational_description", "")),
                situational_detailing=str(item.get("situational_detailing", "")),
                facts={key: value for key, value in item.items() if key not in reserved},
                status=self._status(item.get("engineering_status", "")),
                sources=sources,
                rule_version=str(item.get("rule_version", "")),
                review_reason=str(item.get("review_reason", "")),
            ))
        return result

    def risks(self) -> list[RiskAssessment]:
        severity = self._leaves(self.data.get("severity_results", {}))
        exposure = self._leaves(self.data.get("exposure_results", {}))
        controllability = self._leaves(self.data.get("controllability_results", {}))
        asil = self._leaves(self.data.get("asil_results", {}))
        ftti = self._leaves(self.data.get("ftti_results", {}))
        goals = self._leaves(self.data.get("safety_goals", {}))
        lengths = {len(items) for items in (severity, exposure, controllability, asil, ftti, goals)}
        if len(lengths) != 1:
            raise ValueError(f"Phase 3评分维度数量不一致: {sorted(lengths)}")
        matrix_source = str(self.metadata.get("asil_matrix_source", ""))
        result = []
        for index, (s, e, c, a, f, sg) in enumerate(zip(
            severity, exposure, controllability, asil, ftti, goals
        ), start=1):
            scenario_ids = {str(item.get("scenario_id", "")) for item in (s, e, c, a, f, sg)}
            if len(scenario_ids) != 1 or not next(iter(scenario_ids), ""):
                raise ValueError(f"Phase 3评分维度场景错位: index={index}, ids={sorted(scenario_ids)}")
            scenario_id = next(iter(scenario_ids))
            result.append(RiskAssessment(
                assessment_id=f"RA-{index:04d}-{scenario_id}",
                scenario_id=scenario_id,
                severity=self._engineering_value(s.get("severity_score"), s),
                exposure=self._engineering_value(e.get("exposure_score"), e),
                controllability=self._engineering_value(c.get("controllability_score"), c),
                asil=EvidenceValue(
                    value=str(a.get("ASIL", "")),
                    status=ReviewStatus.FINALIZED,
                    sources=[SourceRef("excel_template", matrix_source, "ASIL_Table")],
                ),
                ftti_seconds=EvidenceValue(
                    value=f.get("ftti_value_s"),
                    status=self._status(f.get("ftti_status", "")),
                    sources=[SourceRef("ftti", str(f.get("source", "")), str(f.get("formula_id", "")))],
                    review_reason=str(f.get("review_reason", "")),
                ),
                hazardous_event=str(s.get("hazard_event", "")),
                safety_goal_id=str(sg.get("sg_id", "")),
            ))
        return result

    @staticmethod
    def _engineering_value(value: Any, item: dict[str, Any]) -> EvidenceValue[str]:
        return EvidenceValue(
            value=str(value or ""),
            status=V13ScoringAdapter._status(item.get("engineering_status", "")),
            rule_version=str(item.get("engineering_rule_version", "")),
            review_reason=str(item.get("engineering_basis", "")),
        )

    def safety_goals(self) -> list[SafetyGoal]:
        approved = str(self.metadata.get("domain_profile", {}).get("approval_status", "")).lower() == "approved"
        result = []
        for sg_id, item in self.data.get("safety_goal_catalog", {}).items():
            result.append(SafetyGoal(
                sg_id=sg_id,
                text=str(item.get("safety_goal", "")),
                safe_state=str(item.get("safety_state", "")),
                max_asil=str(item.get("max_asil", "")),
                ftti_seconds=item.get("ftti_value_s"),
                associated_scenario_ids=[
                    str(association.get("scenario_id", ""))
                    for association in item.get("associations", [])
                    if association.get("scenario_id")
                ],
                status=ReviewStatus.FINALIZED if approved and item.get("ftti_status") == "FINALIZED" else ReviewStatus.PENDING,
            ))
        return result

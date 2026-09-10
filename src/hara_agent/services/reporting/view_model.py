from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True)
class HARAReportRowView:
    hara_id: str
    malfunction_id: str
    scenario_id: str
    hazardous_event_id: str
    function_id: str
    function_name: str
    function_output: str
    guideword: str
    malfunction: str
    hazard: str
    operational_scenario: str
    scenario_detail: str
    hazardous_event: str
    potential_harm: str
    severity: str
    severity_rationale: str
    exposure: str
    exposure_rationale: str
    controllability: str
    controllability_rationale: str
    asil: str
    asil_rationale: str
    ftti: str
    ftti_rationale: str
    sg_id: str
    safety_goal: str
    safe_state: str
    assessment_status: str
    clarification_ids: str
    remark: str

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True)
class SummaryView:
    run_id: str
    method_source: str
    method_hash: str
    report_schema: str
    report_schema_version: str
    report_schema_hash: str
    style_template_hash: str
    report_status: str
    release_status: str
    function_count: int
    guideword_assessment_count: int
    malfunction_count: int
    scenario_count: int
    eligible_hazardous_event_count: int
    severity_finalized: int
    severity_pending: int
    exposure_finalized: int
    exposure_pending: int
    controllability_finalized: int
    controllability_pending: int
    asil_finalized: int
    asil_pending: int
    clarification_ids: str

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True)
class MethodBasisView:
    method_source: str
    guidewords: str
    severity: str
    exposure: str
    controllability: str
    asil: str
    ftti: str

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True)
class SafetyGoalView:
    sg_id: str
    safety_goal: str
    safe_state: str
    max_asil: str
    hazard: str

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True)
class AuditReferenceView:
    run_id: str
    method_hash: str
    report_schema_hash: str
    style_template_hash: str
    hara_id: str
    hazardous_event_id: str
    scenario_id: str
    risk_trace_reference: str
    clarification_ids: str
    assessment_status: str

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True)
class HARAReportViewModel:
    rows: tuple[HARAReportRowView, ...]
    summary: SummaryView
    method_basis: MethodBasisView
    safety_goals: tuple[SafetyGoalView, ...]
    audit_references: tuple[AuditReferenceView, ...]
    schema_hash: str
    method_contract_hash: str
    style_template_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [item.to_dict() for item in self.rows],
            "summary": self.summary.to_dict(),
            "method_basis": self.method_basis.to_dict(),
            "safety_goals": [item.to_dict() for item in self.safety_goals],
            "audit_references": [item.to_dict() for item in self.audit_references],
            "schema_hash": self.schema_hash,
            "method_contract_hash": self.method_contract_hash,
            "style_template_hash": self.style_template_hash,
        }


__all__ = [
    "AuditReferenceView", "HARAReportRowView", "HARAReportViewModel",
    "MethodBasisView", "SafetyGoalView", "SummaryView",
]

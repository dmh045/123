from __future__ import annotations

from typing import Any, Mapping

from hara_agent.contracts import MethodContract
from hara_agent.workflow.state import HARAState

from .engineering_text_mapper import EngineeringReportTextMapper
from .report_schema import ReportSchema
from .view_model import (
    AuditReferenceView, HARAReportRowView, HARAReportViewModel,
    MethodBasisView, SafetyGoalView, SummaryView,
)


def _value(evidence: Any) -> str:
    value = getattr(evidence, "value", evidence)
    return str(getattr(value, "value", value) or "")


def _status(evidence: Any) -> str:
    value = getattr(evidence, "status", "")
    return str(getattr(value, "value", value))


class HARAReportProjectionService:
    """Project committed runtime facts into reviewer-facing report values."""

    def __init__(self, schema: ReportSchema):
        self.schema = schema
        self.text = EngineeringReportTextMapper()

    @staticmethod
    def _clarifications(risk: Any) -> str:
        ids: list[str] = []
        if _status(risk.severity) != "FINALIZED":
            ids.append("EC-03")
        if _status(risk.exposure) != "FINALIZED":
            ids.append("EC-01")
        if _status(risk.controllability) != "FINALIZED":
            ids.append("EC-02")
        return "; ".join(ids)

    def project(
        self,
        state: HARAState,
        method: MethodContract,
        *,
        risk_trace: Mapping[str, Any] | None = None,
        run_summary: Mapping[str, Any] | None = None,
        style_template_hash: str = "",
        risk_trace_reference: str = "",
    ) -> HARAReportViewModel:
        trace_rows = (risk_trace or {}).get("assessments", [])
        trace_by_pair = {
            (str(item.get("malfunction_id", "")), str(item.get("scenario_id", ""))): item
            for item in trace_rows if isinstance(item, Mapping)
        }
        malfunctions = {str(item.get("malfunction_id", "")): item for item in state.malfunctions}
        scenarios = {str(item.scenario_id): item for item in state.scenarios}
        functions = {str(item.get("function_id", "")): item for item in state.functions}
        rows: list[HARAReportRowView] = []
        audit: list[AuditReferenceView] = []
        for offset, risk in enumerate(state.risk_results, start=1):
            malfunction = malfunctions.get(risk.malfunction_id, {})
            function = functions.get(str(malfunction.get("function_id", "")), {})
            scenario = scenarios.get(risk.scenario_id)
            if scenario is None:
                raise ValueError(f"Report projection scenario foreign key missing: {risk.scenario_id}")
            operational, detail = self.text.scenario(scenario)
            trace = trace_by_pair.get((risk.malfunction_id, risk.scenario_id), {})
            severity_trace = trace.get("severity", {}) if isinstance(trace, Mapping) else {}
            exposure_trace = trace.get("exposure", {}) if isinstance(trace, Mapping) else {}
            control_trace = trace.get("controllability", {}) if isinstance(trace, Mapping) else {}
            values = {
                "severity": _value(risk.severity) or self.text.pending_value(risk.severity),
                "exposure": _value(risk.exposure) or self.text.pending_value(risk.exposure),
                "controllability": _value(risk.controllability) or self.text.pending_value(risk.controllability),
                "asil": _value(risk.asil) or self.text.pending_value(risk.asil),
            }
            row = HARAReportRowView(
                hara_id=f"HARA_{offset:03d}",
                malfunction_id=risk.malfunction_id,
                scenario_id=risk.scenario_id,
                hazardous_event_id=str(trace.get("hazardous_event_id", "") or ""),
                function_id=str(malfunction.get("function_id", "")),
                function_name=str(function.get("name", "")),
                function_output=str(function.get("output", "")),
                guideword=str(malfunction.get("guideword", "")),
                malfunction=str(malfunction.get("description", "")),
                hazard=str(malfunction.get("vehicle_level_hazard", "")),
                operational_scenario=operational,
                scenario_detail=detail,
                hazardous_event=str(risk.hazardous_event or self.text.pending_value(risk.severity)),
                potential_harm=self.text.potential_harm(risk.potential_harm, severity=risk.severity),
                severity=values["severity"],
                severity_rationale=self.text.severity_rationale(risk.severity, severity_trace),
                exposure=values["exposure"],
                exposure_rationale=self.text.exposure_rationale(risk.exposure, exposure_trace),
                controllability=values["controllability"],
                controllability_rationale=self.text.controllability_rationale(risk.controllability, control_trace),
                asil=values["asil"],
                asil_rationale=self.text.asil_rationale(risk.asil, trace.get("asil", {}) if isinstance(trace, Mapping) else {}),
                ftti="Pending",
                ftti_rationale=self.text.ftti_rationale(),
                sg_id="",
                safety_goal="",
                safe_state="",
                assessment_status="ELIGIBLE — RISK SCORING INVOKED",
                clarification_ids=self._clarifications(risk),
                remark="",
            )
            rows.append(row)
            audit.append(AuditReferenceView(
                run_id=state.run_id,
                method_hash=str(method.metadata.get("method_source_hash", method.metadata.get("template_hash", ""))),
                report_schema_hash=self.schema.schema_hash,
                style_template_hash=style_template_hash,
                hara_id=row.hara_id,
                hazardous_event_id=row.hazardous_event_id,
                scenario_id=row.scenario_id,
                risk_trace_reference=risk_trace_reference or "risk_execution_trace.json",
                clarification_ids=row.clarification_ids,
                assessment_status=row.assessment_status,
            ))

        summary = run_summary or {}

        def count(field: str, status: str = "FINALIZED") -> int:
            return sum(_status(getattr(item, field)) == status for item in state.risk_results)

        method_hash = str(method.metadata.get("method_source_hash", method.metadata.get("template_hash", "")))
        clarification_ids = "; ".join(sorted({
            item for row in rows for item in row.clarification_ids.split("; ") if item
        }))
        summary_view = SummaryView(
            run_id=state.run_id,
            method_source=str(state.method_contract.get("source_kind", "YAML_BASELINE")),
            method_hash=method_hash,
            report_schema=self.schema.report_id,
            report_schema_version=self.schema.schema_version,
            report_schema_hash=self.schema.schema_hash,
            style_template_hash=style_template_hash,
            report_status="DRAFT_READY",
            release_status="DRAFT — NOT FOR RELEASE",
            function_count=len(state.functions),
            guideword_assessment_count=len(state.guideword_assessments),
            malfunction_count=len(state.malfunctions),
            scenario_count=int(summary.get("scenario_feasibility_count", len(rows))),
            eligible_hazardous_event_count=int(summary.get("scenario_feasible_count", len(rows))),
            severity_finalized=count("severity"),
            severity_pending=len(state.risk_results) - count("severity"),
            exposure_finalized=count("exposure"),
            exposure_pending=len(state.risk_results) - count("exposure"),
            controllability_finalized=count("controllability"),
            controllability_pending=len(state.risk_results) - count("controllability"),
            asil_finalized=count("asil"),
            asil_pending=len(state.risk_results) - count("asil"),
            clarification_ids=clarification_ids,
        )
        basis = MethodBasisView(
            method_source=summary_view.method_source,
            guidewords=f"{len(method.guidewords.guidewords)} 个启用的引导词",
            severity="基于 RELATIVE_SPEED；依赖交通参与者与碰撞配置",
            exposure="VDA 推导的 atom 方法；场景维度覆盖语义尚未定义",
            controllability="iav_avp_v1；正向覆盖加 TTC；UNKNOWN 转换未定义",
            asil="当前 MethodContract 矩阵",
            ftti="方法源已提供；运行时未启用",
        )
        goals = tuple(
            SafetyGoalView(goal.sg_id, goal.text, goal.safe_state, goal.max_asil, "")
            for goal in state.safety_goals
        )
        return HARAReportViewModel(
            tuple(rows), summary_view, basis, goals, tuple(audit), self.schema.schema_hash,
            method_hash, style_template_hash,
        )


__all__ = ["HARAReportProjectionService"]

from types import SimpleNamespace

from hara_agent.models import EvidenceValue, ReviewStatus, RiskAssessment, ScenarioCandidate
from hara_agent.services.reporting import (
    EngineeringReportTextMapper, HARAReportProjectionService,
    audit_content_presentation, audit_potential_harm_path, load_report_schema,
)
from hara_agent.workflow.state import HARAState


def _pending(reason: str) -> EvidenceValue:
    return EvidenceValue(None, ReviewStatus.PENDING, review_reason=reason)


def _method() -> SimpleNamespace:
    return SimpleNamespace(
        metadata={"method_source_hash": "method-hash"},
        guidewords=SimpleNamespace(guidewords=["No/Loss"]),
    )


def _state(*, potential_harm: str = "") -> HARAState:
    risk = RiskAssessment(
        assessment_id="RA-1", scenario_id="SCN-1",
        severity=_pending("MISSING_RELATIVE_SPEED"),
        exposure=_pending("EXPOSURE_DIMENSION_COVERAGE"),
        controllability=_pending("METHOD_BRANCH_UNRESOLVED"),
        asil=_pending("missing=S,E,C"), malfunction_id="MF-1",
        hazardous_event="车辆出现危险状态", potential_harm=potential_harm,
    )
    return HARAState(
        run_id="content-audit",
        functions=[{"function_id": "F-1", "name": "功能", "output": "输出"}],
        malfunctions=[{
            "malfunction_id": "MF-1", "function_id": "F-1", "guideword": "No/Loss",
            "description": "失效", "vehicle_level_hazard": "危害",
        }],
        scenarios=[ScenarioCandidate(
            "SCN-1", "室外停车场", "", "", operating_mode="active",
            facts={
                "operating_mode": "active",
                "ego_speed_constraint": {"speed_min_kph": 0, "speed_max_kph": 20},
                "method_scenario_dimensions": {
                    "ROAD": {"unresolved_reason": "AMBIGUOUS_BINDING"},
                    "TRAFFIC_PATTERN": {"unresolved_reason": "NO_ITEM_FACT"},
                    "OBJECT": {"unresolved_reason": "NO_ITEM_FACT"},
                    "EGO_X_ROAD": {"unresolved_reason": "NO_ITEM_FACT"},
                },
            },
        )],
        risk_results=[risk],
        audit_trail=[{
            "event": "structured_risk_scoring_completed",
            "risk_calculation_inputs": [{
                "malfunction_id": "MF-1", "scenario_id": "SCN-1",
                "potential_harm": {
                    "potential_harm": "", "status": "PENDING_METHOD_SEMANTICS",
                    "reason": "Severity has not been finalized.",
                },
            }],
        }],
    )


def test_text_mapper_keeps_scenario_facts_and_hides_dimension_reason_codes():
    mapper = EngineeringReportTextMapper()
    scenario = _state().scenarios[0]
    operational, detail = mapper.scenario(scenario)
    assert operational == "室外停车场，AVP 处于 Active 状态，车辆速度为 0–20 km/h。"
    assert detail == "道路条件未明确；交通参与者信息未提供；对象信息未提供；道路交互条件未提供。"
    assert "AMBIGUOUS_BINDING" not in detail
    assert "NO_ITEM_FACT" not in detail


def test_potential_harm_path_is_upstream_pending_not_a_projection_gap():
    state = _state()
    view = HARAReportProjectionService(load_report_schema()).project(state, _method())
    path = audit_potential_harm_path(state, view)
    content = audit_content_presentation(view, path)

    assert path["resolver_invocation_count"] == 1
    assert path["classification"] == "UPSTREAM_RISK_NOT_READY"
    assert path["runtime_to_projection_wiring_gap"] is False
    assert view.rows[0].potential_harm == "Pending（上游风险评定未完成）"
    assert content["quality_gate"] == "PASS"
    assert content["raw_machine_status_leakage_count"] == 0
    assert content["remark_duplicate_information_count"] == 0


def test_projection_preserves_a_resolved_potential_harm_without_recomputing_it():
    state = _state(potential_harm="人员受伤")
    view = HARAReportProjectionService(load_report_schema()).project(state, _method())
    assert view.rows[0].potential_harm == "人员受伤"

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

from hara_agent.models import (
    EvidenceValue, ReviewStatus, RiskAssessment, ScenarioCandidate, SourceRef,
)
from hara_agent.services.reporting import (
    EngineeringReportTextMapper, HARAReportProjectionService,
    audit_content_presentation, load_report_schema,
    load_scenario_projection_contexts,
)
from hara_agent.workflow.state import HARAState


def _pending() -> EvidenceValue:
    return EvidenceValue(None, ReviewStatus.PENDING, review_reason="upstream pending")


def _scenario(index: int, coverage: str = "typical") -> ScenarioCandidate:
    return ScenarioCandidate(
        scenario_id=f"SCN-{index}",
        operating_scenario="室内停车场",
        situational_description="",
        situational_detailing="",
        operating_mode="active",
        facts={"ego_speed_constraint": {"min_kph": 0.0, "max_kph": 5.0}},
        sources=[SourceRef("item_definition", "ItemDef.docx", f"paragraph[{index}]")],
        source_scenario_id="SCN-PARENT",
        atomic_variant=f"scenario_synthesis:{coverage}",
        context_resolution={"scenario_synthesis": {"coverage_label": coverage}},
        analysis_instance={
            "semantic_group_id": "SYNTH-GROUP-1",
            "parent_scenario_id": "SCN-PARENT",
            "hazardous_event_id": "HE-1",
            "selected_atoms": [f"FA00{index}"],
            "structured_semantic_query": {
                "location_categories": ["LOCATION_PARKING"],
                "action_categories": ["ACTION_PARK"],
                "object_categories": ["OBJECT_PEDESTRIAN"],
            },
            "validation_status": "VALIDATED",
        },
    )


def _risk(index: int, hazardous_event: str = "车辆可能与行人碰撞") -> RiskAssessment:
    return RiskAssessment(
        assessment_id=f"RA-{index}", scenario_id=f"SCN-{index}",
        severity=_pending(), exposure=_pending(), controllability=_pending(),
        asil=_pending(), malfunction_id="MF-1",
        hazardous_event=hazardous_event,
    )


def _state(hazardous_events: tuple[str, ...]) -> HARAState:
    coverage = ("typical", "boundary", "extreme")
    return HARAState(
        run_id="reviewer-report",
        functions=[{"function_id": "F-1", "name": "自主泊车", "output": "车辆运动"}],
        malfunctions=[{
            "malfunction_id": "MF-1", "function_id": "F-1", "guideword": "丧失",
            "description": "行人识别丧失", "vehicle_level_hazard": "车辆未及时制动",
        }],
        scenarios=[_scenario(index, coverage[index - 1]) for index in range(1, 4)],
        risk_results=[
            _risk(index, hazardous_events[index - 1]) for index in range(1, 4)
        ],
    )


def _method() -> SimpleNamespace:
    return SimpleNamespace(
        metadata={"method_source_hash": "method-hash"},
        guidewords=SimpleNamespace(guidewords=["丧失"]),
    )


def test_three_siblings_project_to_one_main_row_and_three_detail_rows_without_state_mutation():
    state = _state(("车辆可能与行人碰撞",) * 3)
    before = deepcopy(state)

    view = HARAReportProjectionService(load_report_schema()).project(state, _method())

    assert len(view.rows) == 1
    assert len(view.scenario_details) == 3
    assert {item.hara_id for item in view.scenario_details} == {view.rows[0].hara_id}
    assert {item.semantic_group_id for item in view.scenario_details} == {"SYNTH-GROUP-1"}
    assert all("行人" in item.object_interaction_summary for item in view.scenario_details)
    assert len(view.audit_references) == 3
    assert view.projection_metrics["grouping_reduction"] == 2
    assert state == before


def test_hazardous_event_text_divergence_prevents_false_merge():
    state = _state((
        "车辆可能与行人碰撞",
        "车辆可能与行人碰撞",
        "车辆可能撞击静态障碍物",
    ))

    view = HARAReportProjectionService(load_report_schema()).project(state, _method())

    assert len(view.rows) == 2
    assert len(view.scenario_details) == 3
    assert view.projection_metrics[
        "groups_not_merged_due_hazardous_event_divergence"
    ] == 1


def test_text_mapper_preserves_range_and_uses_only_finalized_point_speed():
    mapper = EngineeringReportTextMapper()
    scenario = _scenario(1)
    scenario.facts["ego_speed_constraint"] = {"min_kph": 0.0, "max_kph": 7.0}
    assert mapper.speed_text(scenario) == "适用车速范围：0–7 km/h"

    scenario.facts["ego_speed_kph"] = 3.5
    scenario.fact_provenance["ego_speed_kph"] = {"approval": "FINALIZED"}
    assert mapper.speed_text(scenario) == "分析车速：3.5 km/h"


def test_hazardous_event_compactor_only_normalizes_source_terms_and_duplicates():
    source = "AVP在Active模式；AVP在Active模式；车辆未拉起EPB"

    rendered = EngineeringReportTextMapper().hazardous_event(source)

    assert rendered == "AVP在激活模式；车辆未拉起电子驻车制动。"
    assert "车辆未拉起" in rendered


def test_content_gate_rejects_raw_dimension_atom_and_unapproved_language():
    state = _state(("车辆可能与行人碰撞",) * 3)
    view = HARAReportProjectionService(load_report_schema()).project(state, _method())
    bad_row = replace(
        view.rows[0],
        operational_scenario="WHERE=Garage；FA001 | Drive low speed。",
    )
    bad_view = replace(view, rows=(bad_row,))

    audit = audit_content_presentation(
        bad_view, {"classification": "UPSTREAM_RISK_NOT_READY"},
    )

    assert audit["quality_gate"] == "FAIL"
    assert audit["raw_dimension_syntax_leakage_count"] == 1
    assert audit["raw_atom_id_leakage_count"] == 1
    assert audit["language_mix_count"] == 1


def test_content_gate_allows_epb_as_an_engineering_acronym():
    state = _state(("车辆未拉起EPB并可能发生溜车",) * 3)
    view = HARAReportProjectionService(load_report_schema()).project(state, _method())
    row = replace(view.rows[0], hazardous_event="车辆未拉起EPB并可能发生溜车。")

    audit = audit_content_presentation(
        replace(view, rows=(row,)),
        {"classification": "UPSTREAM_RISK_NOT_READY"},
    )

    assert audit["language_mix_count"] == 0


def test_fresh_report_context_restores_group_coverage_and_contextual_speed(tmp_path):
    state = _state(("车辆可能与行人碰撞",) * 3)
    state.scenarios = state.scenarios[:1]
    state.risk_results = state.risk_results[:1]
    before = deepcopy(state)
    key = ("MF-1", "SCN-1", "HE-1")
    candidates = tmp_path / "scenario_synthesis_candidates.json"
    candidates.write_text(json.dumps({"groups": [{
        "malfunction_id": key[0],
        "parent_scenario_id": key[1],
        "hazardous_event_id": key[2],
        "semantic_group_id": "SYNTH-FRESH-1",
        "structured_semantic_query": {
            "location_categories": ["LOCATION_PARKING"],
            "action_categories": ["ACTION_PARK"],
            "object_categories": ["OBJECT_PEDESTRIAN"],
            "traffic_relations": ["TRAFFIC_CROSSING"],
            "road_relations": [],
        },
        "coverage_plan": {
            "desired_variant_count": 3,
            "variant_intents": [
                {"coverage_label": "typical"},
                {"coverage_label": "boundary"},
                {"coverage_label": "extreme"},
            ],
        },
    }]}), encoding="utf-8")
    speed = tmp_path / "speed_context.json"
    speed.write_text(json.dumps({"records": [{
        "malfunction_id": key[0],
        "parent_scenario_id": key[1],
        "hazardous_event_id": key[2],
        "classification": "CONTEXTUAL_SPEED_CONSUMED",
        "selected_context": "PARKING",
        "match_basis": "FUNCTION.odd_constraints[0]",
        "report_visible_range": [0.0, 5.0],
        "source_speed_envelopes": [],
    }]}), encoding="utf-8")
    contexts = load_scenario_projection_contexts(
        synthesis_candidates_path=candidates,
        speed_context_audit_path=speed,
    )

    view = HARAReportProjectionService(load_report_schema()).project(
        state,
        _method(),
        risk_trace={"assessments": [{
            "malfunction_id": key[0],
            "scenario_id": key[1],
            "hazardous_event_id": key[2],
        }]},
        scenario_projection_contexts=contexts,
    )

    detail = view.scenario_details[0]
    assert detail.semantic_group_id == "SYNTH-FRESH-1"
    assert detail.variant == "计划覆盖：代表场景 / 边界场景 / 高要求场景"
    assert detail.speed_constraint == "适用车速范围：不高于5 km/h"
    assert "车辆执行泊车" in detail.operational_scenario
    assert "横穿交通参与者" in detail.operational_scenario
    assert "当前为综合前审阅投影" in view.rows[0].scenario_detail
    assert view.projection_metrics["contextual_speed_rows"] == 1
    assert state == before

from __future__ import annotations

from pathlib import Path

from hara_agent.models import ReviewStatus, ScenarioCandidate
from hara_agent.services.analysis import (
    FTTIService,
    MethodContractASILService,
    MethodRuleScoringService,
    MethodSafetyGoalService,
)
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow import HARAState, WorkflowStage
from hara_agent.workflow.nodes import score_structured_scenarios


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "references" / "HARA_Template_AI_20260327.xlsx"


def _method():
    return TemplateRoleCompiler().compile_method(TEMPLATE, use_manifest=False)


def _source(location: str) -> dict:
    return {
        "approval": "FINALIZED",
        "provenance": "PROJECT_INPUT",
        "source_refs": [{
            "source_type": "project_input",
            "source_id": "item.docx",
            "location": location,
            "excerpt": location,
        }],
    }


def _canonical_facts() -> tuple[dict, dict]:
    facts = {
        "collision_type": "VEHICLE_TO_ROAD_USER",
        "road_user_type": "PEDESTRIAN",
        "speed_unspecified_kph": 20.0,
        "exposure_method": "F",
        "occurrence_frequency": "MONTHLY_OR_MORE",
        "avoidability_percent": 99.5,
    }
    provenance = {key: _source(key) for key in facts if key != "exposure_method"}
    provenance["exposure_method"] = _source("exposure_method")
    return facts, provenance


def test_compiler_parses_natural_language_controllability_thresholds():
    method = _method()
    by_result = {item.result: item for item in method.controllability.criteria}

    c1 = by_result["C1"].predicates[0]
    c3 = by_result["C3"].predicates[0]
    assert c1.lower == 99.0 and c1.lower_inclusive is False
    assert c3.upper == 90.0 and c3.upper_inclusive is False


def test_method_rule_service_executes_exact_facts_and_preserves_method_ambiguity():
    method = _method()
    facts, provenance = _canonical_facts()
    facts["_fact_provenance"] = provenance

    scored = MethodRuleScoringService(method).score(facts, "vehicle approaches pedestrian")

    assert scored["severity"]["severity_score"] == "S2"
    assert scored["severity"]["engineering_status"] == "PENDING"
    assert "Template ambiguity" in scored["severity"]["engineering_basis"]
    assert scored["exposure"]["exposure_score"] == "E3"
    assert scored["exposure"]["engineering_status"] == "FINALIZED"
    assert scored["controllability"]["controllability_score"] == "C1"
    assert scored["controllability"]["engineering_status"] == "FINALIZED"
    assert scored["severity"]["engineering_source_type"] == "method_contract"


def test_method_rule_service_fails_closed_on_missing_fact_and_open_boundary():
    method = _method()
    facts, provenance = _canonical_facts()
    facts.pop("speed_unspecified_kph")
    provenance.pop("speed_unspecified_kph")
    facts["avoidability_percent"] = 99.0
    facts["_fact_provenance"] = provenance

    scored = MethodRuleScoringService(method).score(facts, "hazard")

    assert scored["severity"]["severity_score"] == ""
    assert "SPEED_UNSPECIFIED" in scored["severity"]["missing_fact_types"]
    assert scored["controllability"]["controllability_score"] == ""
    assert "boundary" in scored["controllability"]["engineering_basis"]


def test_exposure_method_requires_its_own_grounded_provenance():
    method = _method()
    facts, provenance = _canonical_facts()
    provenance.pop("exposure_method")
    facts["_fact_provenance"] = provenance

    scored = MethodRuleScoringService(method).score(facts, "hazard")

    assert scored["exposure"]["exposure_score"] == "E3"
    assert scored["exposure"]["engineering_status"] == "PENDING"
    assert "exposure_method" in scored["exposure"]["engineering_basis"]


def test_scoring_node_uses_method_rules_and_only_looks_up_complete_sec():
    method = _method()
    facts, provenance = _canonical_facts()
    state = HARAState(run_id="method-scoring", stage=WorkflowStage.SCORING)
    state.scenarios = [ScenarioCandidate(
        scenario_id="SCN-1",
        operating_scenario="Parking",
        situational_description="Parking",
        situational_detailing="vehicle approaches pedestrian",
        facts=facts,
        fact_provenance=provenance,
    )]
    state.malfunctions = [{
        "malfunction_id": "MF-1",
        "function_id": "FUN-1",
        "guideword": "loss",
        "description": "braking request lost",
    }]
    state.item_definition["scenario_assessments"] = [{
        "malfunction_id": "MF-1",
        "scenario_id": "SCN-1",
        "physically_feasible": True,
        "functionally_relevant": True,
        "causally_relevant": True,
        "risk_dimensions_changed": ["severity"],
        "hazardous_event": "vehicle approaches pedestrian",
        "potential_harm": "pedestrian injury",
    }]

    result = score_structured_scenarios(
        state,
        MethodRuleScoringService(method),
        MethodContractASILService(method),
        FTTIService(),
    )

    risk = result.risk_results[0]
    assert risk.severity.value == "S2"
    assert risk.severity.status is ReviewStatus.PENDING
    assert risk.exposure.status is ReviewStatus.FINALIZED
    assert risk.controllability.status is ReviewStatus.FINALIZED
    assert risk.asil.value
    assert risk.asil.status is ReviewStatus.PENDING
    assert risk.asil.sources[0].source_type == "method_contract"


def test_method_safety_goal_service_aggregates_exact_intent_and_stays_pending():
    service = MethodSafetyGoalService(_method())
    first = service.register_intent(
        function_name="制动控制",
        malfunction="制动请求丢失",
        guideword="loss",
        scenario_id="SCN-1",
        hazard_event="车辆接近行人",
        asil="A",
        ftti_result={"ftti_value_s": 1.2},
    )
    second = service.register_intent(
        function_name="制动控制",
        malfunction="制动请求丢失",
        guideword="loss",
        scenario_id="SCN-2",
        hazard_event="车辆接近墙体",
        asil="C",
        ftti_result={"ftti_value_s": 0.8},
    )

    assert first["sg_id"] == second["sg_id"]
    entry = service.to_dict()[first["sg_id"]]
    assert entry["max_asil"] == "C"
    assert entry["ftti_value_s"] == 0.8
    assert entry["derivation_status"] == "NEEDS_REVIEW"
    assert service.is_approved is False

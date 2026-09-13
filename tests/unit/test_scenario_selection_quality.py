from __future__ import annotations

from collections import defaultdict
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.contracts import ScenarioDimensionApplicability
from hara_agent.models import ReviewStatus, ScenarioCandidate
from hara_agent.services.analysis import ConstrainedScenarioSynthesisService
from hara_agent.services.analysis.scenario_selection_quality import ScenarioCoveragePlanner
from hara_agent.services.semantic.scenario_synthesis_agent import (
    BoundedScenarioSynthesisAgent,
)
from hara_agent.services.reporting import ScenarioSelectorQualityAudit
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow.checkpoints import CheckpointRepository
from hara_agent.workflow.scenario_causal_revalidation import (
    ScenarioCausalRevalidationRunner,
)


ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest().upper()


@pytest.fixture(scope="module")
def method():
    report = TemplateRoleCompiler().compile_method(
        ROOT / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    return YamlBaselineCompiler().compile(
        ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )


def _parent(
    *, object_type: str = "pedestrian", collision_type: str = "front",
    operating_scenario: str = "parking garage", description: str = "parking maneuver",
    dynamic_authority: str = "EXACT_PROJECT_FACT", source_template: bool = False,
) -> ScenarioCandidate:
    binding = {
        "resolution_status": "RESOLVED",
        "binding_status": "EXACT",
        "atom_id": "FA001",
        "canonical_atom_id": "FA001",
        "project_value": "0..20 km/h",
        "binding_authority": dynamic_authority,
        "speed_constraint": {
            "min_kph": 0.0, "max_kph": 20.0,
            "resolved_by": dynamic_authority,
        },
    }
    instance = {}
    if source_template:
        instance = {
            "source_template_id": "FM_TEMPLATE_001",
            "source_option_id": "FM_TEMPLATE_001:OPTION:1",
        }
    return ScenarioCandidate(
        scenario_id="SCN-Q", operating_scenario=operating_scenario,
        situational_description=description, situational_detailing=description,
        operating_mode="active",
        facts={
            "ego_speed_constraint": {"min_kph": 0.0, "max_kph": 20.0},
            "object_type": object_type, "collision_type": collision_type,
            "object_position": "rear" if collision_type == "rear" else "front",
            "method_scenario_dimensions": {"EGO_DYNAMICS": binding},
            "scenario_atom_ids": ["FA001"],
        },
        analysis_instance=instance,
        semantic_fingerprint="quality-fixture", status=ReviewStatus.FINALIZED,
    )


def _malfunction(hazard: str, *, description: str = "parking control failure") -> dict:
    return {
        "malfunction_id": "MF-Q", "function_id": "F-Q",
        "guideword": "No/Loss", "description": description,
        "functional_effect": hazard, "vehicle_level_hazard": hazard,
        "component_category": "actuator_longitudinal", "failure_type": "loss",
    }


def _assessment(hazard: str) -> dict:
    return {
        "malfunction_id": "MF-Q", "scenario_id": "SCN-Q",
        "hazardous_event_id": "HE-Q", "hazardous_event": hazard,
        "causal_assessment": {
            "status": "VALIDATED", "hazardous_event": hazard,
            "causal_chain": ["malfunction", "vehicle behavior", hazard],
        },
    }


def _project(location: str = "parking garage", road: str = "normal road surface") -> dict:
    return {
        "odd_locations": [location], "odd_road_types": [location],
        "odd_weather_conditions": ["clear"], "odd_road_surfaces": [road],
        "speed_min_kph": 0.0, "speed_max_kph": 20.0,
    }


def _build(service, hazard: str, *, parent=None, project=None, description=None):
    parent = parent or _parent()
    return service.build_input(
        malfunction=_malfunction(hazard, description=description or "parking control failure"),
        parent=parent, assessment=_assessment(hazard),
        project_context=project or _project(),
    )


def _sets(synthesis_input):
    return {item.dimension: item for item in synthesis_input.dimension_candidate_sets}


@pytest.mark.parametrize("signature_count", [1, 2, 3])
def test_coverage_variant_count_uses_distinct_engineering_support(signature_count):
    candidates = []
    score_names = (
        "template_score", "mechanism_score", "action_score", "object_score",
        "traffic_relation_score", "causal_score",
    )
    for index in range(signature_count):
        scores = {name: 0.0 for name in score_names}
        scores[score_names[index]] = 1.0
        candidates.append(SimpleNamespace(
            ranking_scores=scores, method_semantics={}, speed_range_kph=None,
            template_relationship="NONE",
        ))
    # A duplicate evidence signature must not inflate the requested count.
    candidates.append(SimpleNamespace(
        ranking_scores=dict(candidates[0].ranking_scores), method_semantics={},
        speed_range_kph=None, template_relationship="NONE",
    ))
    candidate_set = SimpleNamespace(
        dimension="EGO_ACTION", candidates=tuple(candidates),
        applicability=SimpleNamespace(status=ScenarioDimensionApplicability.REQUIRED),
        binding_decision=SimpleNamespace(parent_atom_id="", refinable=True),
    )
    plan = ScenarioCoveragePlanner.plan(
        query={"action_categories": ["ACTION_STOP"], "source_refs": ["HE.hazardous_event"]},
        candidate_sets=(candidate_set,),
    )
    assert plan.desired_variant_count == signature_count
    assert plan.variant_intents[0]["supported_primary_signature_counts"] == {
        "EGO_ACTION": signature_count,
    }


def test_rear_end_requires_traffic_and_ranks_following_compounds(method):
    service = ConstrainedScenarioSynthesisService(method)
    hazard = "Unexpected braking causes a rear-end collision with the following rear vehicle."
    synthesis_input = _build(
        service, hazard,
        parent=_parent(object_type="passenger_car", collision_type="rear"),
    )
    sets = _sets(synthesis_input)
    assert sets["TRAFFIC_PATTERN"].applicability.status.value == "REQUIRED"
    assert sets["TRAFFIC_PATTERN"].generation_status == "METHOD_GAP"
    assert sets["EGO_ACTION"].candidates[0].atom_id in {"PU015", "PU016"}
    assert sets["OBJECT"].candidates[0].atom_id in {"PU015", "PU016"}
    selected = {
        dimension: ((item.candidates[0].atom_id,) if item.candidates else ())
        for dimension, item in sets.items()
    }
    assert "REQUIRED_DIMENSION_EMPTY:TRAFFIC_PATTERN" in service._selection_reasons(
        synthesis_input, selected,
    )


def test_oncoming_relation_prioritizes_source_compatible_method_atom(method):
    service = ConstrainedScenarioSynthesisService(method)
    hazard = "Turning across oncoming traffic can cause a head-on collision."
    synthesis_input = _build(
        service, hazard,
        parent=_parent(
            object_type="passenger_car", operating_scenario="motorway",
            description="turning on motorway",
        ),
        project=_project("motorway", "normal road surface"),
    )
    traffic = _sets(synthesis_input)["TRAFFIC_PATTERN"]
    assert traffic.applicability.status.value == "REQUIRED"
    assert traffic.candidates
    assert "oncoming" in traffic.candidates[0].label.casefold()
    assert traffic.candidates[0].ranking_scores["traffic_relation_score"] == 1.0


def test_stationary_obstacle_does_not_force_traffic_relation(method):
    service = ConstrainedScenarioSynthesisService(method)
    synthesis_input = _build(
        service, "Stationary vehicle may contact a static obstacle.",
        parent=_parent(object_type="static_obstacle"),
    )
    sets = _sets(synthesis_input)
    assert sets["OBJECT"].applicability.status.value == "REQUIRED"
    assert sets["OBJECT"].generation_status == "METHOD_GAP"
    assert sets["TRAFFIC_PATTERN"].applicability.status.value == "NOT_APPLICABLE"
    assert sets["TRAFFIC_PATTERN"].candidates == ()


def test_slope_holding_plan_treats_road_variation_as_primary(method):
    service = ConstrainedScenarioSynthesisService(method)
    hazard = "Loss of parking brake holding capability causes rollaway on a slope."
    synthesis_input = _build(
        service, hazard,
        parent=_parent(description="vehicle holding on slope"),
        project=_project(road="parking slope gradient up to 15%"),
    )
    plan = synthesis_input.coverage_plan
    assert plan.primary_variation_dimensions[:2] == ("ROAD", "EGO_X_ROAD")
    assert "ROAD" not in plan.prohibited_trivial_only_dimensions
    selections = (
        {"ROAD": ("FB001",), "EGO_X_ROAD": ("PH012",), "EGO_ACTION": ("PH002",)},
        {"ROAD": ("FB002",), "EGO_X_ROAD": ("PH013",), "EGO_ACTION": ("PH002",)},
    )
    assert service.diversity_validator.reasons(plan, selections) == []


def test_reverse_pedestrian_plan_rejects_environment_only_siblings(method):
    service = ConstrainedScenarioSynthesisService(method)
    hazard = "Vehicle reversing into a pedestrian during parking."
    synthesis_input = _build(
        service, hazard,
        parent=_parent(description="reversing parking maneuver"),
    )
    plan = synthesis_input.coverage_plan
    assert plan.primary_variation_dimensions[:2] == ("EGO_ACTION", "OBJECT")
    selections = (
        {"EGO_ACTION": ("FA033",), "OBJECT": ("PU004",), "WHERE": ("SO010",), "ROAD": ("FB005",)},
        {"EGO_ACTION": ("FA033",), "OBJECT": ("PU004",), "WHERE": ("SO020",), "ROAD": ("FB002",)},
        {"EGO_ACTION": ("FA033",), "OBJECT": ("PU004",), "WHERE": ("VD013",), "ROAD": ("FB003",)},
    )
    assert service.diversity_validator.reasons(plan, selections) == [
        "TRIVIAL_VARIANT_DIVERSITY"
    ]


def test_range_containment_parent_is_refinable(method):
    service = ConstrainedScenarioSynthesisService(method)
    synthesis_input = _build(
        service, "Vehicle is reversing during a parking maneuver.",
        parent=_parent(
            object_type="", description="reversing parking maneuver",
            dynamic_authority="RANGE_CONTAINMENT",
        ),
    )
    dynamics = _sets(synthesis_input)["EGO_DYNAMICS"]
    assert dynamics.binding_decision.authority.value == "RANGE_CONTAINMENT"
    assert dynamics.binding_decision.refinable is True
    assert dynamics.locked_atom_ids == ()
    assert "FA033" in {item.atom_id for item in dynamics.candidates}


def test_exact_authoritative_dynamic_remains_locked(method):
    service = ConstrainedScenarioSynthesisService(method)
    dynamics = _sets(_build(service, "Parking maneuver near a pedestrian."))["EGO_DYNAMICS"]
    assert dynamics.binding_decision.authority.value == "EXACT_PROJECT_FACT"
    assert dynamics.binding_decision.refinable is False
    assert dynamics.locked_atom_ids == ("FA001",)


def test_unrepresentable_crossing_relation_is_method_gap_not_catalog_fallback(method):
    service = ConstrainedScenarioSynthesisService(method)
    traffic = _sets(_build(
        service, "Crossing traffic enters the parking path.",
        parent=_parent(object_type="passenger_car"),
    ))["TRAFFIC_PATTERN"]
    assert traffic.applicability.status.value == "REQUIRED"
    assert traffic.generation_status == "METHOD_GAP"
    assert traffic.candidates == ()
    assert traffic.hard_filtered_pool_size == 0


def test_offline_provider_request_contains_governance_and_bounded_metadata(method):
    service = ConstrainedScenarioSynthesisService(method)
    synthesis_input = _build(
        service, "Parking brake failure may contact a pedestrian.",
        parent=_parent(source_template=True),
    )
    payload = BoundedScenarioSynthesisAgent._user_payload(synthesis_input)
    assert payload["dimension_applicability"]
    assert payload["scenario_coverage_plan"]["desired_variant_count"] in {1, 2, 3}
    assert payload["fm_scenario_template"]["template_id"] == "FM_TEMPLATE_001"
    candidate = next(
        item for value in payload["candidate_sets"].values()
        for item in value["candidates"]
    )
    assert candidate["candidate_origin"]
    assert candidate["selection_reason"]
    assert set(candidate["ranking_scores"]) == {
        "template_score", "mechanism_score", "action_score", "object_score",
        "traffic_relation_score", "odd_score", "causal_score", "lexical_score",
        "semantic_similarity_score", "final_rank_score",
    }
    assert "E_total" not in str(payload)
    all_ids = {
        item["atom_id"] for value in payload["candidate_sets"].values()
        for item in value["candidates"]
    }
    assert len(all_ids) < len(service.by_id)


def test_selector_quality_audit_reports_plan_metrics_without_provider(method):
    service = ConstrainedScenarioSynthesisService(method)
    rear = _build(
        service,
        "Rear-end collision with a following rear vehicle.",
        parent=_parent(object_type="passenger_car", collision_type="rear"),
    )
    static = _build(
        service, "Stationary vehicle may contact a static obstacle.",
        parent=_parent(object_type="static_obstacle"),
    )
    audit = ScenarioSelectorQualityAudit().build((rear, static))
    assert audit["provider_calls"] == 0
    assert audit["parent_groups"] == 2
    assert audit["child_count"] == 0
    assert audit["traffic_pattern"]["required"] == 1
    assert audit["traffic_pattern"]["required_but_missing"] == 1
    assert audit["traffic_pattern"]["not_applicable"] == 1
    assert audit["ranking"]["combination_beam_truncated_groups"] == 0


def test_completed_r3_causal_trace_remains_resumable_without_provider():
    source_run_id = "hara-full-baseline-20260912-r1-synthesis-r3"
    target_run_id = f"{source_run_id}-causal-r2"
    checkpoint_path = ROOT / "runtime/agent" / f"{source_run_id}.checkpoint.json"
    trace_path = ROOT / "runtime/review" / target_run_id / "causal_revalidation_provider_trace.json"
    assert _sha256(checkpoint_path) == (
        "2DBBCBFCF7AECC09332A8BBC2642513D98A2558E1CD33305F432415351E7D357"
    )
    assert _sha256(trace_path) == (
        "8359E7A35EB4DF8E77D9E16D2CAD6151D4327CEC6D7EA57B48603BA159E25BC7"
    )

    state = CheckpointRepository(ROOT / "runtime/agent").load(source_run_id)
    candidates_by_malfunction = defaultdict(list)
    for scenario in state.scenarios:
        candidates_by_malfunction[
            str(scenario.analysis_instance["malfunction_id"])
        ].append(scenario)
    recovered = ScenarioCausalRevalidationRunner._recover_completed(
        trace_path, source_run_id=source_run_id, target_run_id=target_run_id,
        candidates_by_malfunction=dict(candidates_by_malfunction),
    )
    recovered_child_ids = {
        assessment.scenario_id
        for assessments, _audit in recovered.values()
        for assessment in assessments
    }
    assert len(candidates_by_malfunction) == 57
    assert len(recovered) == 57
    assert len(recovered_child_ids) == 1236
    assert sorted(set(candidates_by_malfunction) - set(recovered)) == []

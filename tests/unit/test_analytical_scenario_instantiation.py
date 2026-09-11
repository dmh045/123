from dataclasses import replace
from pathlib import Path

import pytest

from hara_agent.contracts import FailureModeTaxonomyValue, RiskContextFactStatus
from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.models import (
    MalfunctionCandidate, ReviewStatus, ScenarioCandidate,
    ScenarioFeasibilityAssessment,
)
from hara_agent.services.analysis import (
    HazardousEventRiskContextService, ScenarioMethodService,
)
from hara_agent.services.analysis.scenario_physics import derive_scenario_physics
from hara_agent.services.semantic.scenario_evidence import (
    ScenarioEvidenceContractError, ScenarioEvidenceErrorCode,
    build_fact_registry, validate_evidence_contract,
)
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow import HARAState, WorkflowStage
from hara_agent.workflow.nodes.scenarios import assess_scenarios


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def method():
    report = TemplateRoleCompiler().compile_method(
        ROOT / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    base = YamlBaselineCompiler().compile(
        ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )
    taxonomy = base.scenario_model.scenario_method.failure_mode_selector_taxonomy
    assert taxonomy is not None
    expanded = replace(
        taxonomy,
        component_categories=taxonomy.component_categories + (
            FailureModeTaxonomyValue(
                canonical_id="parking_brake", aliases=(),
                source_ref=taxonomy.component_categories[0].source_ref,
            ),
        ),
        failure_types=taxonomy.failure_types + (
            FailureModeTaxonomyValue(
                canonical_id="unintended_deactivation", aliases=(),
                source_ref=taxonomy.failure_types[0].source_ref,
            ),
        ),
    )
    return replace(
        base,
        scenario_model=replace(
            base.scenario_model,
            scenario_method=replace(
                base.scenario_model.scenario_method,
                failure_mode_selector_taxonomy=expanded,
            ),
        ),
    )


def _malfunction() -> MalfunctionCandidate:
    return MalfunctionCandidate(
        malfunction_id="MF-P3-B", function_id="F-P3", guideword="No/Loss",
        description="parking_brake_release",
        functional_effect="confirmed output is unavailable",
        vehicle_level_hazard="vehicle behavior may be unsafe",
        causal_chain=["M", "B"], status=ReviewStatus.FINALIZED,
        confidence=0.9, component_category="parking_brake",
        failure_type="unintended_deactivation",
    )


def _parent() -> ScenarioCandidate:
    return ScenarioCandidate(
        scenario_id="SCN-PARENT", operating_scenario="parking",
        situational_description="项目基础场景", situational_detailing="项目基础场景",
        facts={"ego_speed_constraint": {"min_kph": 0.0, "max_kph": 10.0}},
        semantic_fingerprint="parent-fingerprint", status=ReviewStatus.FINALIZED,
    )


def _materialize(candidate: ScenarioCandidate) -> dict:
    scenario = {**candidate.facts, "_fact_provenance": dict(candidate.fact_provenance)}
    for record in derive_scenario_physics(candidate):
        key = record.evidence_ref.split(".", 1)[1]
        scenario[key] = record.value
        scenario["_fact_provenance"][key] = {
            "provenance": record.provenance.value,
            "approval": record.approval_status.value,
            "source_refs": [source.__dict__ for source in record.source_refs],
            **record.metadata,
        }
    return scenario


def test_strong_template_creates_two_isolated_analysis_instances_with_source_contract(method):
    parent = _parent()
    instances, audit = ScenarioMethodService(method).instantiate_analytical_candidates(
        _malfunction(), [parent],
    )

    assert audit["selection_mode"] == "STRONG_TEMPLATE_ANALYTICAL_INSTANCES"
    assert len(instances) == 2
    assert len({item.scenario_id for item in instances}) == 2
    assert parent.facts == {"ego_speed_constraint": {"min_kph": 0.0, "max_kph": 10.0}}
    assert all(item.source_scenario_id == "SCN-PARENT" for item in instances)
    assert all(item.analysis_instance["malfunction_id"] == "MF-P3-B" for item in instances)
    assert all(item.analysis_instance["validation_status"] == "VALIDATED" for item in instances)
    assert all(item.facts["relative_distance_m"] == 0.3 for item in instances)
    assert all(item.facts["object_speed_kph"] == 0.0 for item in instances)
    assert all("relative_speed_kph" not in item.facts for item in instances)
    assert all("项目速度约束" in item.situational_description for item in instances)
    assert all("本分析场景设定" in item.situational_detailing for item in instances)
    for instance in instances:
        for assumption in instance.analysis_instance["assumptions"]:
            assert assumption["origin"] == "SCENARIO_DEFINED"
            assert assumption["source_template_id"] == "FM_TEMPLATE_006"
            assert assumption["source_option_id"]
            assert assumption["source_hash"]
            assert assumption["method_contract_hash"]
            assert assumption["applicable_scope"]["scenario_id"] == instance.scenario_id
            assert instance.fact_provenance[assumption["field"]]["approval"] == "PENDING"
    assert audit["unmapped_value_count"] == 3  # front twice and passenger_car lack exact compiled mappings


def test_risk_context_accepts_only_same_instance_validated_analysis_assumptions(method):
    instance = ScenarioMethodService(method).instantiate_analytical_candidates(
        _malfunction(), [_parent()],
    )[0][0]
    service = HazardousEventRiskContextService(method)
    context = service.build(
        malfunction_id="MF-P3-B", scenario_id=instance.scenario_id,
        hazard_node_id="H", scenario=_materialize(instance),
    )

    assert context.object_speed_kph.status is RiskContextFactStatus.AVAILABLE
    assert context.relative_distance_m.status is RiskContextFactStatus.AVAILABLE
    assert context.relative_speed_kph.status is RiskContextFactStatus.UNAVAILABLE

    wrong_scope = _materialize(instance)
    wrong_scope["_fact_provenance"]["object_speed_kph"]["applicable_scope"]["scenario_id"] = "SCN-OTHER"
    rejected = service.build(
        malfunction_id="MF-P3-B", scenario_id=instance.scenario_id,
        hazard_node_id="H", scenario=wrong_scope,
    )
    assert rejected.object_speed_kph.status is RiskContextFactStatus.UNAVAILABLE


def test_ttc_retains_analytical_assumption_source_chain_without_deriving_relative_speed(method):
    instance = ScenarioMethodService(method).instantiate_analytical_candidates(
        _malfunction(), [_parent()],
    )[0][0]
    speed_metadata = dict(instance.fact_provenance["object_speed_kph"])
    speed_metadata["field"] = "relative_speed_kph"
    synthetic = replace(
        instance,
        facts={**instance.facts, "relative_speed_kph": 5.0},
        fact_provenance={
            **instance.fact_provenance,
            "relative_speed_kph": speed_metadata,
        },
    )

    ttc = next(
        item for item in derive_scenario_physics(synthetic)
        if item.evidence_ref == "DERIVED.ttc_s"
    )
    assert ttc.value == 0.216
    assert ttc.metadata["analysis_assumption_origin"] == "SCENARIO_DEFINED"
    assert ttc.metadata["analysis_assumption_scope"]["scenario_id"] == instance.scenario_id


def test_analysis_assumption_cannot_prove_a_positive_causal_hop(method):
    malfunction = _malfunction()
    instance = ScenarioMethodService(method).instantiate_analytical_candidates(
        malfunction, [_parent()],
    )[0][0]
    registry = build_fact_registry(malfunction, instance)
    item = {
        "scenario_id": instance.scenario_id,
        "physically_feasible": True, "functionally_relevant": True,
        "causally_relevant": True, "breakpoint": "NONE",
        "causal_chain": {
            "m_to_b": {
                "claim": "output is unavailable", "basis_type": "DIRECT_FACT",
                "evidence_refs": ["MF.functional_effect"],
            },
            "b_to_i": {
                "claim": "interaction occurs in the configured distance", "basis_type": "DIRECT_FACT",
                "evidence_refs": ["SCN.relative_distance_m"],
            },
            "i_to_h": {
                "claim": "hazard", "basis_type": "DIRECT_FACT",
                "evidence_refs": ["MF.description"],
            },
        },
        "risk_dimension_changes": [{
            "dimension": "distance", "evidence_refs": ["SCN.relative_distance_m"],
            "reason": "configured analysis condition",
        }],
        "hazardous_event": "hazard", "potential_harm": "",
        "rationale": "test", "confidence": 0.8,
    }
    with pytest.raises(ScenarioEvidenceContractError) as caught:
        validate_evidence_contract(
            malfunction=malfunction, scenario=instance, item=item, registry=registry,
            prompt_version="p3-b-test", batch="1/1", split_path="root", split_depth=0,
        )
    assert caught.value.code is ScenarioEvidenceErrorCode.ASSUMPTION_IN_POSITIVE_CHAIN


class _Client:
    class Config:
        provider = "test"
        base_url = "local"
        model = "offline"

    config = Config()


class _OfflineAgent:
    prompt_version = "p3-b-test"
    assessment_contract_version = "scenario-causal-assessment-v2"

    def __init__(self):
        self.client = _Client()
        self.candidate_ids: list[str] = []

    def assess(self, malfunction, candidates, project_registry=None):
        self.candidate_ids = [item.scenario_id for item in candidates]
        return [
            ScenarioFeasibilityAssessment(
                malfunction_id=malfunction.malfunction_id,
                scenario_id=candidate.scenario_id,
                physically_feasible=False,
                functionally_relevant=False,
                causally_relevant=False,
                risk_dimensions_changed=[], rationale="offline P3-B wiring test",
                status=ReviewStatus.PENDING,
            )
            for candidate in candidates
        ], {
            "malfunction_id": malfunction.malfunction_id,
            "candidate_count": len(candidates), "retained_count": 0,
            "initial_batch_count": 1, "leaf_batch_count": 1,
            "llm_calls": 0, "leaf_items": [len(candidates)], "leaf_input_chars": [0],
        }


def test_workflow_uses_per_malfunction_instances_without_provider_calls(method):
    state = HARAState(run_id="p3-b-wiring", stage=WorkflowStage.MALFUNCTIONS)
    agent = _OfflineAgent()

    completed = assess_scenarios(
        state, agent, [_malfunction()], [_parent()], max_workers=1, method=method,
    )

    assert completed.stage is WorkflowStage.SCORING
    assert len(agent.candidate_ids) == 2
    assert all(identity.startswith("SCN-ANALYTICAL-") for identity in agent.candidate_ids)
    assert {
        item["scenario_id"] for item in completed.item_definition["scenario_assessments"]
    } == set(agent.candidate_ids)
    event = next(
        item for item in completed.audit_trail
        if item["event"] == "scenario_feasibility_assessed"
    )
    assert event["analytical_scenario_instantiation"]["instance_count"] == 2

import pytest

from hara_agent.contracts import CausalAssessmentStatus
from hara_agent.infrastructure.llm import LLMResponse
from hara_agent.models import (
    MalfunctionCandidate, ReviewStatus, ScenarioCandidate, SourceRef,
)
from hara_agent.services.semantic import ScenarioFeasibilityAgent
from hara_agent.services.semantic.scenario_batching import ScenarioAdaptiveBatchError
from hara_agent.workflow import HARAState, WorkflowStage
from hara_agent.workflow.nodes import score_structured_scenarios


def test_agent_parser_auto_finalizes_only_after_typed_evidence_validation():
    source = SourceRef("item_definition", "item.docx", "p1", "braking evidence")
    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "braking command lost", "no deceleration",
        "vehicle continues moving", ["command lost", "vehicle continues moving"],
        sources=[source], status=ReviewStatus.FINALIZED,
    )
    scenario = ScenarioCandidate(
        "SCN-1", "Parking", "vehicle approaches object", "object ahead",
        {
            "object_position": "ahead",
            "relative_distance": "0.5 m",
            "relative_speed_kph": 5.0,
        },
        fact_provenance={
            key: {
                "provenance": "PROJECT_INPUT",
                "approval": "FINALIZED",
                "source_refs": [{
                    "source_type": source.source_type,
                    "source_id": source.source_id,
                    "location": source.location,
                    "excerpt": source.excerpt,
                }],
            }
            for key in ("object_position", "relative_distance", "relative_speed_kph")
        },
        status=ReviewStatus.FINALIZED, sources=[source], semantic_fingerprint="fp-1",
    )
    payload = {
        "scenario_id": "SCN-1",
        "physically_feasible": True,
        "functionally_relevant": True,
        "causally_relevant": True,
        "breakpoint": "NONE",
        "causal_chain": {
            "m_to_b": {
                "claim": "lost command causes no deceleration",
                "basis_type": "DIRECT_FACT",
                "evidence_refs": ["MF.functional_effect"],
            },
            "b_to_i": {
                "claim": "motion continues toward the object",
                "basis_type": "DIRECT_FACT",
                "evidence_refs": ["SCN.object_position"],
            },
            "i_to_h": {
                "claim": "remaining distance decreases to contact",
                "basis_type": "DERIVED_PHYSICS",
                "evidence_refs": ["DERIVED.ttc_s"],
            },
            "h_to_harm": {
                "claim": "contact can cause harm",
                "basis_type": "DIRECT_FACT",
                "evidence_refs": ["MF.vehicle_level_hazard"],
            },
        },
        "risk_dimension_changes": [{
            "dimension": "distance",
            "evidence_refs": ["SCN.relative_distance"],
            "reason": "explicit separation changes the event timing",
        }],
        "rationale": "complete evidence-linked chain",
        "hazardous_event": "vehicle contacts object",
        "potential_harm": "occupant injury",
        "confidence": 0.8,
        "status": "PENDING",
    }

    result = ScenarioFeasibilityAgent._parse(
        malfunction, payload, scenario=scenario,
    )

    assert result.status is ReviewStatus.FINALIZED
    assert result.causal_assessment is not None
    assert result.causal_assessment.status is CausalAssessmentStatus.VALIDATED
    assert result.retain
    serialized = result.to_dict()
    assert serialized["causal_assessment"]["status"] == "VALIDATED"
    assert serialized["causal_assessment"]["review_status"] == "FINALIZED"


def test_scoring_rejects_legacy_positive_flags_without_typed_contract():
    state = HARAState("legacy-bypass", stage=WorkflowStage.SCORING)
    state.scenarios = [ScenarioCandidate(
        "SCN-1", "Parking", "scenario", "detail",
    )]
    state.malfunctions = [{"malfunction_id": "MF-1"}]
    state.item_definition["scenario_assessments"] = [{
        "malfunction_id": "MF-1",
        "scenario_id": "SCN-1",
        "physically_feasible": True,
        "functionally_relevant": True,
        "causally_relevant": True,
        "risk_dimensions_changed": ["distance"],
        "hazardous_event": "hazard",
        "potential_harm": "harm",
    }]

    with pytest.raises(ValueError, match="typed causal_assessment"):
        score_structured_scenarios(state, None, None)  # type: ignore[arg-type]


def _negative_payload(scenario_id: str) -> dict:
    return {
        "scenario_id": scenario_id,
        "physically_feasible": True,
        "functionally_relevant": True,
        "causally_relevant": False,
        "breakpoint": "I_TO_H",
        "causal_chain": {
            "m_to_b": {
                "claim": "lost command causes no deceleration",
                "basis_type": "DIRECT_FACT",
                "evidence_refs": ["MF.functional_effect"],
            },
            "b_to_i": {
                "claim": "behavior interacts with the explicit object position",
                "basis_type": "DIRECT_FACT",
                "evidence_refs": ["SCN.object_position"],
            },
            "i_to_h": {
                "claim": "contact requires an unsupported closing condition",
                "basis_type": "ASSUMPTION",
                "evidence_refs": [],
            },
        },
        "risk_dimension_changes": [],
        "rationale": "the chain stops at the first unsupported transition",
        "hazardous_event": "",
        "potential_harm": "",
        "confidence": 0.8,
        "status": "PENDING",
    }


def test_scenario_coverage_error_triggers_bounded_adaptive_split():
    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, request):
            self.calls += 1
            ids = [
                scenario_id for scenario_id in ("SCN-1", "SCN-2")
                if f'"scenario_id":"{scenario_id}"' in request.user_prompt
            ]
            if self.calls == 1:
                ids = ids[:1]
            return LLMResponse(
                data={"assessments": [_negative_payload(item) for item in ids]},
                model="fake",
            )

    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "braking command lost", "no deceleration",
        "vehicle continues moving", ["command lost", "vehicle continues moving"],
    )
    scenarios = [
        ScenarioCandidate(
            scenario_id, "Parking", "object ahead", "explicit object",
            {"object_position": "ahead"}, semantic_fingerprint=f"fp-{scenario_id}",
        )
        for scenario_id in ("SCN-1", "SCN-2")
    ]
    client = Client()
    assessments, audit = ScenarioFeasibilityAgent(
        client, batch_max_chars=50000, batch_max_items=12,
    ).assess(malfunction, scenarios)

    assert [item.scenario_id for item in assessments] == ["SCN-1", "SCN-2"]
    assert client.calls == 3
    assert audit["adaptive_split_count"] == 1
    assert audit["coverage_error_count"] == 1


def _empty_hop_claim_payload(scenario_id: str) -> dict:
    payload = _negative_payload(scenario_id)
    payload["breakpoint"] = "H_TO_HARM"
    payload["causal_chain"]["i_to_h"] = {
        "claim": "the event reaches the hazard boundary",
        "basis_type": "DIRECT_FACT",
        "evidence_refs": ["MF.vehicle_level_hazard"],
    }
    payload["causal_chain"]["h_to_harm"] = {
        "claim": "",
        "basis_type": "ASSUMPTION",
        "evidence_refs": [],
    }
    return payload


def test_single_scenario_evidence_error_gets_one_bounded_contract_repair():
    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, request):
            self.calls += 1
            if self.calls == 1:
                payload = _empty_hop_claim_payload("SCN-1")
            else:
                assert request.metadata["item_contract_repair"] is True
                assert "EMPTY_HOP_CLAIM" in request.user_prompt
                assert "h_to_harm" in request.user_prompt
                payload = _negative_payload("SCN-1")
            return LLMResponse(data={"assessments": [payload]}, model="fake")

    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "braking command lost", "no deceleration",
        "vehicle continues moving", ["command lost", "vehicle continues moving"],
    )
    scenario = ScenarioCandidate(
        "SCN-1", "Parking", "object ahead", "explicit object",
        {"object_position": "ahead"}, semantic_fingerprint="fp-SCN-1",
    )
    client = Client()

    assessments, audit = ScenarioFeasibilityAgent(
        client, batch_max_chars=50000, batch_max_items=12,
    ).assess(malfunction, [scenario])

    assert len(assessments) == 1
    assert client.calls == 2
    assert audit["item_contract_repair_count"] == 1
    assert audit["item_contract_repair_failure_count"] == 0
    assert audit["single_item_failures"] == 0


def test_single_scenario_contract_repair_still_fails_closed_after_one_attempt():
    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, _request):
            self.calls += 1
            return LLMResponse(
                data={"assessments": [_empty_hop_claim_payload("SCN-1")]},
                model="fake",
            )

    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "braking command lost", "no deceleration",
        "vehicle continues moving", ["command lost", "vehicle continues moving"],
    )
    scenario = ScenarioCandidate(
        "SCN-1", "Parking", "object ahead", "explicit object",
        {"object_position": "ahead"}, semantic_fingerprint="fp-SCN-1",
    )
    client = Client()

    with pytest.raises(ScenarioAdaptiveBatchError, match="attempt=1/1"):
        ScenarioFeasibilityAgent(
            client, batch_max_chars=50000, batch_max_items=12,
        ).assess(malfunction, [scenario])

    assert client.calls == 2


def test_invalid_causal_chain_container_gets_error_specific_repair_instruction():
    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, request):
            self.calls += 1
            if self.calls == 1:
                payload = _negative_payload("SCN-1")
                payload["causal_chain"] = []
            else:
                assert request.metadata["item_contract_repair"] is True
                assert "INVALID_CAUSAL_CHAIN_SHAPE" in request.user_prompt
                assert "NEVER return causal_chain as an array" in request.user_prompt
                payload = _negative_payload("SCN-1")
            return LLMResponse(data={"assessments": [payload]}, model="fake")

    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "braking command lost", "no deceleration",
        "vehicle continues moving", ["command lost", "vehicle continues moving"],
    )
    scenario = ScenarioCandidate(
        "SCN-1", "Parking", "object ahead", "explicit object",
        {"object_position": "ahead"}, semantic_fingerprint="fp-SCN-1",
    )
    client = Client()

    assessments, audit = ScenarioFeasibilityAgent(
        client, batch_max_chars=50000, batch_max_items=12,
    ).assess(malfunction, [scenario])

    assert len(assessments) == 1
    assert client.calls == 2
    assert audit["item_contract_repair_count"] == 1
    assert audit["item_contract_repair_failure_count"] == 0


def test_derived_physics_kind_mismatch_repair_is_bounded_to_registry_and_breakpoint_enum():
    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, request):
            self.calls += 1
            if self.calls == 1:
                payload = _negative_payload("SCN-1")
                payload["breakpoint"] = "H_TO_HARM"
                payload["causal_chain"]["i_to_h"] = {
                    "claim": "the event reaches the hazard boundary",
                    "basis_type": "DIRECT_FACT",
                    "evidence_refs": ["MF.vehicle_level_hazard"],
                }
                payload["causal_chain"]["h_to_harm"] = {
                    "claim": "the parking label proves a collision and harm",
                    "basis_type": "DERIVED_PHYSICS",
                    "evidence_refs": ["SCN.object_position"],
                }
            else:
                assert request.metadata["item_contract_repair"] is True
                assert "DERIVED_PHYSICS_KIND_MISMATCH" in request.user_prompt
                assert "I_TO_HARM is invalid" not in request.user_prompt
                assert 'AllowedBreakpointValues=["M_TO_B","B_TO_I","I_TO_H","H_TO_HARM","NONE"]' in request.user_prompt
                assert "Do not merely relabel" in request.user_prompt
                assert "PreviousAssessment=" in request.user_prompt
                payload = _negative_payload("SCN-1")
            return LLMResponse(data={"assessments": [payload]}, model="fake")

    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "braking command lost", "no deceleration",
        "vehicle continues moving", ["command lost", "vehicle continues moving"],
    )
    scenario = ScenarioCandidate(
        "SCN-1", "Parking", "object ahead", "explicit object",
        {"object_position": "ahead"}, semantic_fingerprint="fp-SCN-1",
    )
    client = Client()

    assessments, audit = ScenarioFeasibilityAgent(
        client, batch_max_chars=50000, batch_max_items=12,
    ).assess(malfunction, [scenario])

    assert len(assessments) == 1
    assert client.calls == 2
    assert audit["item_contract_repair_count"] == 1


def test_invalid_breakpoint_repair_instruction_lists_only_contract_enum_values():
    from hara_agent.services.semantic.scenario_evidence import (
        ScenarioEvidenceContractError,
        ScenarioEvidenceErrorCode,
    )

    error = ScenarioEvidenceContractError(
        "invalid breakpoint",
        code=ScenarioEvidenceErrorCode.INVALID_BREAKPOINT,
        hop="NONE",
        reason="invalid breakpoint='I_TO_HARM'",
        malfunction_id="MF-1",
        scenario_id="SCN-1",
        semantic_fingerprint="fp",
        invalid_evidence_refs=[],
        basis_type="",
        claim="",
    )

    instruction = ScenarioFeasibilityAgent._single_item_repair_instruction(error)

    assert "I_TO_HARM is invalid" in instruction
    assert "AllowedBreakpointValues" in instruction

from hara_agent.contracts import (
    CausalMechanismDefinition, CausalMechanismPremise,
    InMemoryCausalMechanismCatalog,
)
from hara_agent.evaluation.models import ScenarioEvaluationInput
from hara_agent.evaluation.stages import ScenarioEvaluationHarness
from hara_agent.infrastructure.llm import LLMResponse
from hara_agent.models import (
    EvidenceKind, FactProvenance, MalfunctionCandidate, ReviewStatus,
    ScenarioCandidate, SourceRef,
)
from hara_agent.services.semantic import ScenarioFeasibilityAgent


SOURCE = SourceRef("test_fixture", "P0-2c1", "TEST_ONLY")
STAGES = (
    ("M_TO_B", "M", "B"), ("B_TO_I", "B", "I"),
    ("I_TO_H", "I", "H"), ("H_TO_HARM", "H", "HARM"),
)


def support(ref, kind):
    return {"evidence_ref": ref, "support_type": kind}


def application(mechanism_id, bindings):
    return {
        "mechanism_id": mechanism_id, "mechanism_version": "1",
        "bindings": bindings,
    }


def edge(values, scenario_id, mixed):
    edge_id, from_stage, to_stage = values
    supports = [support("SCN.direct_support", "DIRECT_FACT")]
    mechanism_id = "TEST-MECH-DIRECT-C1"
    bindings = {"fact": "SCN.direct_support"}
    if mixed:
        supports.append(support("DERIVED.ttc_s", "DERIVED_PHYSICS"))
        mechanism_id = "TEST-MECH-MIXED-C1"
        bindings = {
            "fact": "SCN.direct_support", "derived": "DERIVED.ttc_s",
        }
    return {
        "edge_id": edge_id, "from_stage": from_stage, "to_stage": to_stage,
        "claim": f"TEST_ONLY {scenario_id}", "supports": supports,
        "mechanism_application": application(mechanism_id, bindings),
    }


def assessment(scenario_id, positive):
    if positive:
        supports = [
            support("SCN.direct_support", "DIRECT_FACT"),
            support("DERIVED.ttc_s", "DERIVED_PHYSICS"),
        ]
        return {
            "scenario_id": scenario_id, "physically_feasible": True,
            "functionally_relevant": True, "causally_relevant": True,
            "breakpoint": "NONE",
            "edges": [edge(values, scenario_id, True) for values in STAGES],
            "risk_dimension_changes": [{
                "dimension": "distance", "supports": supports,
                "reason": "TEST_ONLY mixed support",
            }],
            "rationale": "TEST_ONLY positive", "hazardous_event": "TEST hazard",
            "potential_harm": "TEST harm", "confidence": 0.8, "status": "PENDING",
        }
    return {
        "scenario_id": scenario_id, "physically_feasible": True,
        "functionally_relevant": True, "causally_relevant": False,
        "breakpoint": "I_TO_H",
        "edges": [edge(values, scenario_id, False) for values in STAGES[:2]],
        "risk_dimension_changes": [], "rationale": "no supported mechanism",
        "hazardous_event": "", "potential_harm": "", "confidence": 0.8,
        "status": "PENDING",
    }


class StubV10Client:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        return LLMResponse(self.payload, "stub-v10", "stub-request", {
            "transport_attempts": 1,
        })


def catalog():
    direct = CausalMechanismDefinition(
        "TEST-MECH-DIRECT-C1", "1", "TEST_ONLY direct", (
            CausalMechanismPremise("fact", EvidenceKind.DIRECT_FACT),
        ), "TEST_RESULT", (SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )
    mixed = CausalMechanismDefinition(
        "TEST-MECH-MIXED-C1", "1", "TEST_ONLY mixed", (
            CausalMechanismPremise("fact", EvidenceKind.DIRECT_FACT),
            CausalMechanismPremise("derived", EvidenceKind.DERIVED_PHYSICS),
        ), "TEST_RESULT", (SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )
    return InMemoryCausalMechanismCatalog((direct, mixed))


def scenarios():
    provenance = {
        "direct_support": {
            "provenance": "PROJECT_INPUT", "approval": "PENDING",
            "source_refs": [SOURCE], "fallback_used": False,
        },
    }
    return [
        ScenarioCandidate(
            "SCN-POSITIVE", "synthetic", "synthetic positive", "synthetic",
            {"direct_support": "explicit", "relative_distance": "10 m",
             "relative_speed_kph": 18},
            fact_provenance=provenance, semantic_fingerprint="positive-fp",
        ),
        ScenarioCandidate(
            "SCN-NEGATIVE", "synthetic", "synthetic negative", "synthetic",
            {"direct_support": "explicit", "relative_distance": "10 m",
             "relative_speed_kph": 18},
            fact_provenance=provenance, semantic_fingerprint="negative-fp",
        ),
    ]


def request(actual_scenarios):
    return ScenarioEvaluationInput(
        malfunction=MalfunctionCandidate(
            "MF-C1", "FUN-1", "loss", "loss", "effect", "hazard",
            ["loss", "effect"],
        ),
        scenarios=actual_scenarios,
        repeat=1,
        metadata={"fixture": "P0-2c1-stub"},
    )


def test_stub_provider_positive_mixed_and_negative_payloads_pass_full_v10_path():
    actual_scenarios = scenarios()
    client = StubV10Client({"assessments": [
        assessment("SCN-POSITIVE", True), assessment("SCN-NEGATIVE", False),
    ]})
    agent = ScenarioFeasibilityAgent(
        client, assessment_contract="v2", mechanism_catalog=catalog(),
        batch_max_chars=30000,
    )
    report = ScenarioEvaluationHarness(agent).evaluate(request(actual_scenarios))

    assert len(client.requests) == 1
    provider_request = client.requests[0]
    assert provider_request.prompt_version == "scenario-feasibility-v10"
    assert provider_request.schema_name == "ScenarioFeasibilityAssessmentV2List"
    assert provider_request.metadata["assessment_contract_version"] == "scenario-evidence-v2"
    assert "AVAILABLE_CAUSAL_MECHANISMS=" in provider_request.user_prompt
    assert report["contract_versions"]["prompt"] == "scenario-feasibility-v10"
    assert report["contract_versions"]["assessment"] == "scenario-evidence-v2"
    assert report["contract_versions"]["mechanism_catalog_fingerprint"]
    assert report["contract_versions"]["contract_cache_fingerprint"]
    assert report["contract_metrics"] == {
        "schema_valid_attempt_count": 1,
        "contract_valid_attempt_count": 1,
        "provider_payload_error_count": 0,
        "mechanism_application_shape_error_count": 0,
        "exact_coverage_error_count": 0,
    }
    assert [item["causally_relevant"] for item in report["results"]] == [True, False]


def test_stub_provider_exact_coverage_error_is_typed_in_harness():
    actual_scenarios = scenarios()
    client = StubV10Client({"assessments": [assessment("SCN-POSITIVE", True)]})
    agent = ScenarioFeasibilityAgent(
        client, assessment_contract="v2", mechanism_catalog=catalog(),
        batch_max_chars=30000,
    )
    report = ScenarioEvaluationHarness(agent).evaluate(request(actual_scenarios))
    assert report["contract_metrics"]["provider_payload_error_count"] == 1
    assert report["contract_metrics"]["exact_coverage_error_count"] == 1
    assert report["errors"][0]["code"] == "MISSING_ASSESSMENT"

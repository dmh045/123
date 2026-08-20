import json

from hara_agent.contracts import (
    CausalMechanismDefinition, CausalMechanismPremise,
    InMemoryCausalMechanismCatalog,
)
from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, ReviewStatus, SourceRef,
)
from hara_agent.services.semantic import ScenarioFeasibilityAgent
from hara_agent.services.semantic.scenario_batching import build_scenario_user_prompt
from hara_agent.services.semantic.scenario_evidence import FactRegistry
from hara_agent.services.semantic.scenario_provider_contract import v2_registry_prompt_snapshot
from hara_agent.infrastructure.llm import OpenAICompatibleClient


class NeverCalledClient:
    def complete_json(self, request):
        raise AssertionError("schema inspection must not call Provider")


def test_v10_is_explicit_and_v9_v1_remains_the_default():
    default = ScenarioFeasibilityAgent(NeverCalledClient())
    v2 = ScenarioFeasibilityAgent(
        NeverCalledClient(), assessment_contract="v2",
        mechanism_catalog=InMemoryCausalMechanismCatalog(),
    )

    assert default.prompt_version == "scenario-feasibility-v9"
    assert default.assessment_contract_version == "scenario-evidence-v1"
    assert default.schema_name == "ScenarioFeasibilityAssessmentList"
    assert v2.prompt_version == "scenario-feasibility-v10"
    assert v2.assessment_contract_version == "scenario-evidence-v2"
    assert v2.schema_name == "ScenarioFeasibilityAssessmentV2List"
    assert "ATOMIC SCENARIO AND FACT PRECEDENCE" in v2.system_prompt
    assert "STRICT FACT BOUNDARY" in v2.system_prompt
    assert "COUNTERFACTUAL TEST" in v2.system_prompt
    assert "per-record supports" in v2.system_prompt
    assert "Never invent a mechanism" in v2.system_prompt


def test_v10_available_mechanisms_are_structured_and_do_not_expose_gold_bindings():
    source = SourceRef("test_fixture", "P0-2c1", "TEST_ONLY")
    definition = CausalMechanismDefinition(
        "TEST-MECH-V10", "1", "TEST_ONLY schema", (
            CausalMechanismPremise("distance", EvidenceKind.DIRECT_FACT),
        ), "TEST_RESULT", (source,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )
    from hara_agent.models import MalfunctionCandidate

    prompt = build_scenario_user_prompt(
        MalfunctionCandidate(
            "MF-1", "FUN-1", "loss", "loss", "effect", "hazard",
            ["loss", "effect"],
        ),
        [],
        assessment_contract_version="scenario-evidence-v2",
        mechanism_definitions=(definition,),
    )
    mechanism_json = prompt.split("AVAILABLE_CAUSAL_MECHANISMS=", 1)[1].split("\n", 1)[0]
    mechanisms = json.loads(mechanism_json)
    assert mechanisms == [{
        "description": "TEST_ONLY schema",
        "mechanism_id": "TEST-MECH-V10",
        "premises": [{
            "description": "", "expected_kind": "DIRECT_FACT",
            "premise_id": "distance", "required": True,
        }],
        "result_state": "TEST_RESULT", "version": "1",
    }]
    assert "bindings" not in mechanism_json


def test_v10_registry_snapshot_exposes_authority_without_full_source_document():
    registry = FactRegistry()
    registry.register(EvidenceRecord(
        "PROJECT.speed.parking.max_kph", 5.0, EvidenceKind.DIRECT_FACT,
        FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, (SOURCE := SourceRef(
            "item_definition", "ItemDef.docx", "table[12].row[3]"
        ),), {"unit": "km/h", "operating_mode": "parking"},
    ))
    snapshot = v2_registry_prompt_snapshot(registry)
    assert snapshot["PROJECT.speed.parking.max_kph"] == {
        "value": 5.0, "kind": "DIRECT_FACT", "provenance": "PROJECT_INPUT",
        "approval_status": "PENDING",
        "metadata": {"unit": "km/h", "operating_mode": "parking"},
    }
    assert "ItemDef.docx" not in json.dumps(snapshot, ensure_ascii=False)
    assert SOURCE.source_id == "ItemDef.docx"


def test_openai_compatible_envelope_contract_recognizes_v10_schema_name():
    OpenAICompatibleClient._validate_schema_envelope(
        {"assessments": []}, "ScenarioFeasibilityAssessmentV2List"
    )

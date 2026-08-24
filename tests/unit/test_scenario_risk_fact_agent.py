from hara_agent.contracts import FactOrigin, FactType, RequiredFactSpec
from hara_agent.config import LLMConfig
from hara_agent.infrastructure.llm import LLMResponse
from hara_agent.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from hara_agent.models import (
    FactProvenance, MalfunctionCandidate, ReviewStatus, ScenarioCandidate, SourceRef,
)
from hara_agent.services.semantic import ScenarioRiskFactAgent


def _spec(fact_type, origin, constraints=(), unit=""):
    return RequiredFactSpec(
        fact_type=fact_type,
        required_for=("severity",),
        unit=unit,
        constraints=constraints,
        condition="when referenced",
        origin=origin,
        source_rule_ids=(),
        source_refs=(),
    )


def test_scenario_risk_facts_are_validated_neutral_and_evidence_grounded():
    class Client:
        def complete_json(self, request):
            assert request.task == "interpret_scenario_risk_facts"
            assert "AVOIDABILITY_PERCENT" not in request.user_prompt
            return LLMResponse(data={"results": [{
                "malfunction_id": "MF-1",
                "scenario_id": "SCN-1",
                "fact_type": "COLLISION_TYPE",
                "status": "FOUND",
                "value": "FRONTAL",
                "unit": "",
                "evidence_ids": ["E0001"],
            }]}, model="fake")

    source = SourceRef("item_definition", "item.docx", "p1", "frontal collision")
    candidate = ScenarioCandidate(
        "SCN-1", "road", "frontal collision", "vehicle approaches target",
        sources=[source],
    )
    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "output lost", "control absent",
        "vehicle continues toward target", ["output lost", "control absent"],
        sources=[source],
    )
    facts, audit = ScenarioRiskFactAgent(Client()).interpret(
        [{
            "malfunction_id": "MF-1", "scenario_id": "SCN-1",
            "physically_feasible": True, "functionally_relevant": True,
            "causally_relevant": True, "risk_dimensions_changed": ["severity"],
            "hazardous_event": "frontal collision", "potential_harm": "injury",
            "status": "FINALIZED",
        }],
        [candidate],
        [malfunction],
        [
            _spec(FactType.COLLISION_TYPE, FactOrigin.SCENARIO_FACT,
                  ("COLLISION_TYPE IN ('FRONTAL',)",)),
            _spec(FactType.AVOIDABILITY_PERCENT, FactOrigin.HUMAN_EVIDENCE, unit="%"),
        ],
    )

    assert len(facts) == 1
    fact = facts[0]
    assert fact.parameter == "COLLISION_TYPE"
    assert fact.context == {"malfunction_id": "MF-1", "scenario_id": "SCN-1"}
    assert fact.provenance is FactProvenance.DERIVED
    assert fact.approval is ReviewStatus.FINALIZED
    assert fact.source_refs == [source]
    assert audit["found_fact_count"] == 1


def test_scenario_risk_fact_agent_fails_closed_on_out_of_ontology_value():
    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"results": [{
                "malfunction_id": "MF-1", "scenario_id": "SCN-1",
                "fact_type": "COLLISION_TYPE", "status": "FOUND",
                "value": "UNKNOWN_KIND", "unit": "", "evidence_ids": ["E0001"],
            }]}, model="fake")

    source = SourceRef("item_definition", "item.docx", "p1", "collision")
    candidate = ScenarioCandidate("SCN-1", "road", "collision", "detail", sources=[source])
    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "lost", "absent", "collision",
        ["lost", "collision"], sources=[source],
    )
    import pytest
    with pytest.raises(ValueError, match="outside the compiled ontology"):
        ScenarioRiskFactAgent(Client()).interpret(
            [{
                "malfunction_id": "MF-1", "scenario_id": "SCN-1",
                "physically_feasible": True, "functionally_relevant": True,
                "causally_relevant": True, "risk_dimensions_changed": ["severity"],
                "status": "FINALIZED",
            }],
            [candidate], [malfunction],
            [_spec(FactType.COLLISION_TYPE, FactOrigin.SCENARIO_FACT,
                   ("COLLISION_TYPE IN ('FRONTAL',)",))],
        )


def test_scenario_risk_fact_agent_repairs_only_missing_contract_results():
    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, request):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(data={"results": []}, model="fake")
            assert request.metadata["coverage_repair"] is True
            return LLMResponse(data={"results": [{
                "malfunction_id": "MF-1",
                "scenario_id": "SCN-1",
                "fact_type": "COLLISION_TYPE",
                "status": "FOUND",
                "value": "FRONTAL",
                "unit": "",
                "evidence_ids": ["E0001"],
            }]}, model="fake")

    source = SourceRef("item_definition", "item.docx", "p1", "frontal collision")
    candidate = ScenarioCandidate("SCN-1", "road", "collision", "detail", sources=[source])
    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "lost", "absent", "collision",
        ["lost", "collision"], sources=[source],
    )
    client = Client()
    facts, audit = ScenarioRiskFactAgent(client).interpret(
        [{
            "malfunction_id": "MF-1", "scenario_id": "SCN-1",
            "physically_feasible": True, "functionally_relevant": True,
            "causally_relevant": True, "risk_dimensions_changed": ["severity"],
            "status": "FINALIZED",
        }],
        [candidate], [malfunction],
        [_spec(FactType.COLLISION_TYPE, FactOrigin.SCENARIO_FACT,
               ("COLLISION_TYPE IN ('FRONTAL',)",))],
    )

    assert len(facts) == 1
    assert audit["coverage_repair_count"] == 1
    assert audit["llm_call_count"] == 2


def test_scenario_risk_fact_agent_batches_by_expected_result_count():
    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, request):
            self.calls += 1
            fact_request = request.user_prompt.split("fact_requests=", 1)[1]
            scenario_id = "SCN-1" if '"scenario_id":"SCN-1"' in fact_request else "SCN-2"
            return LLMResponse(data={"results": [{
                "malfunction_id": "MF-1",
                "scenario_id": scenario_id,
                "fact_type": "COLLISION_TYPE",
                "status": "NOT_FOUND",
            }]}, model="fake")

    source = SourceRef("item_definition", "item.docx", "p1", "collision")
    candidates = [
        ScenarioCandidate(scenario_id, "road", "collision", "detail", sources=[source])
        for scenario_id in ("SCN-1", "SCN-2")
    ]
    malfunction = MalfunctionCandidate(
        "MF-1", "FUN-1", "loss", "lost", "absent", "collision",
        ["lost", "collision"], sources=[source],
    )
    assessments = [{
        "malfunction_id": "MF-1", "scenario_id": scenario_id,
        "physically_feasible": True, "functionally_relevant": True,
        "causally_relevant": True, "risk_dimensions_changed": ["severity"],
        "status": "FINALIZED",
    } for scenario_id in ("SCN-1", "SCN-2")]
    client = Client()
    facts, audit = ScenarioRiskFactAgent(client, batch_max_results=1).interpret(
        assessments, candidates, [malfunction],
        [_spec(FactType.COLLISION_TYPE, FactOrigin.SCENARIO_FACT,
               ("COLLISION_TYPE IN ('FRONTAL',)",))],
    )

    assert facts == []
    assert client.calls == 2
    assert audit["batch_count"] == 2
    assert audit["llm_call_count"] == 2


def test_scenario_risk_fact_task_uses_scenario_thinking_policy():
    client = OpenAICompatibleClient(LLMConfig(
        provider="volcengine-agent-plan",
        base_url="https://example.invalid/v3",
        model="fake",
        api_key="fake",
        scenario_thinking="disabled",
    ))

    assert client._resolve_thinking_mode("interpret_scenario_risk_facts") == "disabled"

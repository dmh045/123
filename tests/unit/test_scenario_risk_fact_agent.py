from hara_agent.contracts import FactOrigin, FactType, RequiredFactSpec
from hara_agent.infrastructure.llm import LLMResponse
from hara_agent.models import MalfunctionCandidate, ReviewStatus, ScenarioCandidate, SourceRef
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


def test_scenario_risk_facts_are_neutral_pending_and_evidence_grounded():
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

    source = SourceRef("project_input", "item.docx", "p1", "frontal collision")
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
    assert fact.approval is ReviewStatus.PENDING
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

    source = SourceRef("project_input", "item.docx", "p1", "collision")
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
            }],
            [candidate], [malfunction],
            [_spec(FactType.COLLISION_TYPE, FactOrigin.SCENARIO_FACT,
                   ("COLLISION_TYPE IN ('FRONTAL',)",))],
        )

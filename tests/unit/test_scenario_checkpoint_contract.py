from hara_agent.services.semantic.scenario_evidence import (
    SCENARIO_ASSESSMENT_CONTRACT_VERSION,
)
from hara_agent.services.semantic.scenario_contract import SCENARIO_CONTRACT_VERSION
from hara_agent.workflow import HARAState, WorkflowStage


def test_old_causal_assessment_checkpoint_is_invalidated_without_losing_malfunctions():
    value = {
        "run_id": "contract-upgrade",
        "stage": "quality_gate",
        "item_definition": {
            "scenario_assessments": [{"legacy": True}],
            "typed": {
                "risk_facts": [
                    {"fact_id": "RF-SCENARIO", "produced_by": "scenario-risk-facts-v2"},
                    {"fact_id": "RF-PROJECT", "produced_by": "source_extraction"},
                ],
                "method_risk_fact_bindings": [
                    {"source_fact_id": "RF-SCENARIO"},
                    {"source_fact_id": "RF-PROJECT"},
                ],
            },
        },
        "functions": [{"function_id": "F01"}],
        "malfunctions": [{"malfunction_id": "MF-F01-001"}],
        "pending_reviews": [
            {"field": "item_definition", "reason": "preserve"},
            {"field": "scenarios", "reason": "stale"},
            {"field": "scenario_risk_facts", "reason": "stale"},
            {"assessment_id": "RA-1", "field": "severity", "reason": "stale"},
            {"safety_goal_id": "SG-1", "field": "safety_goal", "reason": "stale"},
        ],
        "scenario_contract_version": SCENARIO_CONTRACT_VERSION,
        "scenario_assessment_contract_version": "scenario-causal-assessment-v1",
    }

    state = HARAState.from_dict(value)

    assert SCENARIO_ASSESSMENT_CONTRACT_VERSION == "scenario-causal-assessment-v2"
    assert state.stage is WorkflowStage.MALFUNCTIONS
    assert state.functions == [{"function_id": "F01"}]
    assert state.malfunctions == [{"malfunction_id": "MF-F01-001"}]
    assert "scenario_assessments" not in state.item_definition
    assert state.item_definition["typed"]["risk_facts"] == [
        {"fact_id": "RF-PROJECT", "produced_by": "source_extraction"},
    ]
    assert state.item_definition["typed"]["method_risk_fact_bindings"] == [
        {"source_fact_id": "RF-PROJECT"},
    ]
    assert state.pending_reviews == [
        {"field": "item_definition", "reason": "preserve"},
    ]
    assert any(
        item.get("event") == "scenario_checkpoint_invalidated"
        for item in state.audit_trail
    )

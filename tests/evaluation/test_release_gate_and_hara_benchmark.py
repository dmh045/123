import json
from pathlib import Path

from hara_agent.contracts import (
    CausalBreakpoint, CausalEdge, CausalGraph, CausalNode, CausalNodeType,
    CausalRelation, EvidenceBinding, RiskDimensionChange,
    ScenarioCausalAssessment,
)
from hara_agent.evaluation.stages import HARAValidationHarness
from hara_agent.models import (
    EvidenceKind, EvidenceValue, ReviewStatus, RiskAssessment, SafetyGoal,
    ScenarioCandidate, SourceRef,
)
from hara_agent.services.validation import ReleaseGateValidator
from hara_agent.workflow import HARAState, WorkflowGraph, WorkflowStage
from hara_agent.workflow.nodes import pass_quality_gate


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = (
    ROOT / "tests" / "e2e" / "synthetic_hara"
    / "synthetic_hara_benchmark.json"
)


def causal_assessment(review_status=ReviewStatus.PENDING):
    node_ids = ("M", "B", "I", "H", "HARM")
    node_types = (
        CausalNodeType.MALFUNCTION,
        CausalNodeType.SYSTEM_BEHAVIOR_CHANGE,
        CausalNodeType.OPERATIONAL_CONSEQUENCE,
        CausalNodeType.HAZARD,
        CausalNodeType.HARM,
    )
    edge_ids = ("M_TO_B", "B_TO_I", "I_TO_H", "H_TO_HARM")
    edges = tuple(CausalEdge(
        edge_id, source, target, CausalRelation.CAUSES,
        f"{source} causes {target}", (f"TEST.{edge_id}",),
    ) for edge_id, source, target in zip(edge_ids, node_ids, node_ids[1:]))
    return ScenarioCausalAssessment(
        "SCN-1",
        CausalGraph(
            tuple(CausalNode(node_id, node_type, node_type.value)
                  for node_id, node_type in zip(node_ids, node_types)),
            edges,
        ),
        node_ids,
        CausalBreakpoint.NONE,
        tuple(EvidenceBinding(
            edge.edge_id, edge.evidence_refs, EvidenceKind.DIRECT_FACT,
            status=review_status,
        ) for edge in edges),
        (),
        (RiskDimensionChange("distance", ("TEST.I_TO_H",), "distance evidence"),),
        "hazardous event", "potential harm", review_status=review_status,
    )


def state(review_status=ReviewStatus.PENDING):
    source = SourceRef("test_fixture", "synthetic-hara", "approved-input")
    causal = causal_assessment(review_status)
    evidence = lambda value: EvidenceValue(
        value, review_status, [source], rule_version="TEST-RULE-1",
    )
    risk = RiskAssessment(
        "RA-1", "SCN-1", evidence("S2"), evidence("E3"), evidence("C3"),
        evidence("B"), "MF-1", "hazardous event", "potential harm",
        safety_goal_id="SG-1",
    )
    result = HARAState(
        "synthetic-validation", stage=WorkflowStage.QUALITY_GATE,
        method_contract={
            "template_hash": "test-template-hash",
            "contract_version": "method-contract-test",
            "compile_status": "READY",
            "engineering_rules_compiled": True,
        },
        item_definition={
            "typed": {
                "system_description": "synthetic item",
                "item_boundary": "synthetic boundary",
                "sources": [{
                    "source_type": source.source_type,
                    "source_id": source.source_id,
                    "location": source.location,
                    "excerpt": source.excerpt,
                }],
                "status": review_status.value,
                "confidence": 1.0,
            },
            "scenario_assessments": [{
                "malfunction_id": "MF-1",
                "scenario_id": "SCN-1",
                "causally_relevant": True,
                "breakpoint": "NONE",
                "risk_dimensions_changed": ["distance"],
                "hazardous_event": "hazardous event",
                "potential_harm": "potential harm",
                "causal_assessment": causal.to_dict(),
            }],
        },
        scenarios=[ScenarioCandidate("SCN-1", "mode", "situation", "detail")],
        risk_results=[risk],
        safety_goals=[SafetyGoal(
            "SG-1", "prevent hazardous event", "safe state", "B",
            ["SCN-1"], review_status,
        )],
    )
    if review_status is ReviewStatus.PENDING:
        result.pending_reviews.append({
            "field": "synthetic", "reason": "engineering approval required",
        })
    return result


def test_release_gate_requires_approved_facts_causal_risk_and_safety_goal():
    approved = state(ReviewStatus.FINALIZED)

    validation = ReleaseGateValidator().evaluate(approved)

    assert validation.ready_for_release
    assert validation.blockers == ()
    assert approved.can_publish
    pass_quality_gate(approved)
    assert approved.stage is WorkflowStage.RENDER


def test_release_gate_blocks_pending_without_modifying_results():
    pending = state()
    before = pending.risk_results[0].asil.value

    pass_quality_gate(pending)

    assert pending.stage is WorkflowStage.QUALITY_GATE
    assert pending.risk_results[0].asil.value == before
    audit = pending.audit_trail[-1]
    assert audit["event"] == "quality_gate_blocked"
    assert "RISK_ASSESSMENT_PENDING_APPROVAL" in audit["blockers"]


def test_workflow_records_release_blockers_before_review_interruption():
    pending = state()

    result = WorkflowGraph().run(pending)

    assert result.interrupted
    assert result.reason == "pending_engineering_review"
    assert pending.audit_trail[-1]["event"] == "quality_gate_blocked"


def test_missing_project_fact_structure_fails_closed_without_defaults():
    invalid = state(ReviewStatus.FINALIZED)
    invalid.item_definition["typed"].pop("item_boundary")

    validation = ReleaseGateValidator().evaluate(invalid)

    assert not validation.ready_for_release
    assert "PROJECT_FACTS_INVALID" in validation.blockers


def test_synthetic_hara_benchmark_matches_checked_in_report():
    report = HARAValidationHarness().evaluate(state())
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert report == expected

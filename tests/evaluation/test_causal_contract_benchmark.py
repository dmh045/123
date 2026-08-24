import json
from pathlib import Path

from hara_agent.contracts import (
    CausalBreakpoint, CausalEdge, CausalGraph, CausalNode, CausalNodeType,
    CausalRelation, EvidenceBinding, RiskDimensionChange,
    ScenarioCausalAssessment,
)
from hara_agent.evaluation.stages import CausalContractHarness
from hara_agent.models import EvidenceKind, ReviewStatus


ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests" / "regression" / "hara_cases" / "scenario_benchmark.json"


def assessment(case):
    node_types = tuple(CausalNodeType(value) for value in case["node_types"])
    node_ids = tuple(f"N{index}" for index in range(len(node_types)))
    nodes = tuple(
        CausalNode(node_id, node_type, f"{case['name']} {node_type.value}")
        for node_id, node_type in zip(node_ids, node_types)
    )
    edges = []
    bindings = []
    for index, (source, target) in enumerate(zip(node_ids, node_ids[1:])):
        edge_id = f"E{index}"
        refs = () if case.get("missing_evidence_edge") == index else (f"TEST.{edge_id}",)
        edges.append(CausalEdge(
            edge_id, source, target, CausalRelation.CAUSES,
            f"{source} causes {target}", refs,
        ))
        bindings.append(EvidenceBinding(
            edge_id,
            refs,
            (
                EvidenceKind.ASSUMPTION
                if case.get("assumption_edge") == index
                else EvidenceKind.DIRECT_FACT
            ),
            status=(
                ReviewStatus.REJECTED
                if case.get("rejected_binding_edge") == index
                else ReviewStatus.PENDING
            ),
        ))
    breakpoint = case.get("breakpoint")
    return ScenarioCausalAssessment(
        case["name"],
        CausalGraph(nodes, tuple(edges)),
        node_ids,
        CausalBreakpoint(breakpoint) if breakpoint is not None else None,
        tuple(bindings),
        (),
        (RiskDimensionChange("distance", ("TEST.RISK",), "fixture variation"),),
        "hazard", "harm",
    )


def test_ten_case_scenario_benchmark_has_expected_fail_closed_states():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assessments = [assessment(case) for case in cases]

    report = CausalContractHarness().evaluate(assessments)

    assert len(cases) == 10
    assert [item.status.value for item in assessments] == [
        case["expected_status"] for case in cases
    ]
    assert report["metrics"] == {
        "assessment_count": 10,
        "invalid_contract_count": 0,
        "causal_validation_rate": 0.2,
        "causal_gap_rate": 0.4,
        "unsupported_chain_rate": 0.1,
        "edge_evidence_coverage": 0.885714,
        "missing_evidence_rate": 0.114286,
    }


def test_causal_harness_rejects_untyped_free_text_as_authoritative_input():
    report = CausalContractHarness().evaluate([{
        "scenario_id": "SCN-FREE-TEXT",
        "causal_chain": "failure therefore injury",
    }])

    assert report["metrics"]["invalid_contract_count"] == 1
    assert report["results"] == []

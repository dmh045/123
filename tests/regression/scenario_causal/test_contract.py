from dataclasses import replace

from hara_agent.contracts import (
    CausalAssessmentStatus, CausalBreakpoint, CausalEdge, CausalGraph,
    CausalNode, CausalNodeType, CausalRelation, EvidenceBinding,
    RiskDimensionChange, ScenarioCausalAssessment,
)
from hara_agent.models import EvidenceKind, ReviewStatus, SourceRef


NODE_TYPES = (
    CausalNodeType.MALFUNCTION,
    CausalNodeType.SYSTEM_BEHAVIOR_CHANGE,
    CausalNodeType.OPERATIONAL_CONSEQUENCE,
    CausalNodeType.HAZARD,
    CausalNodeType.HARM,
)


def complete_assessment() -> ScenarioCausalAssessment:
    node_ids = ("M", "B", "I", "H", "HARM")
    nodes = tuple(
        CausalNode(node_id, node_type, f"{node_type.value} description", (f"TEST.{node_id}",))
        for node_id, node_type in zip(node_ids, NODE_TYPES)
    )
    edge_ids = ("M_TO_B", "B_TO_I", "I_TO_H", "H_TO_HARM")
    edges = tuple(
        CausalEdge(
            edge_id,
            source,
            target,
            CausalRelation.CAUSES,
            f"{source} causes {target}",
            (f"TEST.{edge_id}",),
        )
        for edge_id, source, target in zip(edge_ids, node_ids, node_ids[1:])
    )
    source = SourceRef("test_fixture", "causal-contract", "edge")
    bindings = tuple(
        EvidenceBinding(
            edge.edge_id,
            edge.evidence_refs,
            EvidenceKind.DIRECT_FACT,
            (source,),
            ReviewStatus.PENDING,
        )
        for edge in edges
    )
    return ScenarioCausalAssessment(
        scenario_id="SCN-1",
        causal_graph=CausalGraph(nodes, edges),
        causal_chain=node_ids,
        breakpoint=CausalBreakpoint.NONE,
        evidence_bindings=bindings,
        unsupported_links=(),
        risk_dimension_changes=(RiskDimensionChange(
            "distance", ("TEST.I_TO_H",), "distance changes the interaction"
        ),),
        hazardous_event="hazardous event",
        potential_harm="harm",
        provenance=tuple(f"TEST.{item}" for item in edge_ids),
    )


def test_causal_graph_and_assessment_roundtrip_is_deterministic():
    assessment = complete_assessment()
    serialized = assessment.to_dict()

    restored = ScenarioCausalAssessment.from_dict(serialized)

    assert restored == assessment
    assert restored.to_dict() == serialized
    assert restored.status is CausalAssessmentStatus.VALIDATED


def test_complete_domain_neutral_causal_path_passes():
    assessment = complete_assessment()

    assert assessment.causal_graph.has_path(assessment.causal_chain)
    assert assessment.is_validated


def test_edge_without_evidence_is_pending_causal_evidence():
    assessment = complete_assessment()
    first_edge = replace(assessment.causal_graph.edges[0], evidence_refs=())
    first_binding = replace(assessment.evidence_bindings[0], evidence_refs=())
    pending = replace(
        assessment,
        causal_graph=replace(
            assessment.causal_graph,
            edges=(first_edge, *assessment.causal_graph.edges[1:]),
        ),
        evidence_bindings=(first_binding, *assessment.evidence_bindings[1:]),
    )

    assert pending.status is CausalAssessmentStatus.PENDING_CAUSAL_EVIDENCE
    assert not pending.is_validated


def test_direct_malfunction_to_harm_is_a_causal_gap():
    graph = CausalGraph(
        nodes=(
            CausalNode("M", CausalNodeType.MALFUNCTION, "sensor noise"),
            CausalNode("HARM", CausalNodeType.HARM, "human injury"),
        ),
        edges=(CausalEdge(
            "M_TO_HARM", "M", "HARM", CausalRelation.CAUSES,
            "sensor noise causes injury", ("TEST.jump",),
        ),),
    )
    assessment = ScenarioCausalAssessment(
        "SCN-GAP", graph, ("M", "HARM"), CausalBreakpoint.NONE,
        (EvidenceBinding(
            "M_TO_HARM", ("TEST.jump",), EvidenceKind.DIRECT_FACT,
        ),),
        (), (), "hazard", "harm",
    )

    assert assessment.status is CausalAssessmentStatus.CAUSAL_GAP


def test_missing_breakpoint_is_pending_breakpoint():
    assessment = replace(complete_assessment(), breakpoint=None)

    assert assessment.status is CausalAssessmentStatus.PENDING_BREAKPOINT


def test_assumption_cannot_validate_a_positive_causal_edge():
    assessment = complete_assessment()
    assumption = replace(
        assessment.evidence_bindings[0], basis_type=EvidenceKind.ASSUMPTION,
    )
    invalid = replace(
        assessment,
        evidence_bindings=(assumption, *assessment.evidence_bindings[1:]),
    )

    assert invalid.status is CausalAssessmentStatus.CAUSAL_GAP

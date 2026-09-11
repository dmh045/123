from __future__ import annotations

from hara_agent.contracts import (
    CausalBreakpoint, CausalEdge, CausalGraph, CausalNode, CausalNodeType,
    CausalRelation, EvidenceBinding, ScenarioCausalAssessment,
)
from hara_agent.models import EvidenceKind, ReviewStatus
from hara_agent.services.analysis import RiskScoreabilityService


def _causal() -> dict:
    nodes = tuple(CausalNode(identity, kind, identity) for identity, kind in (
        ("M", CausalNodeType.MALFUNCTION), ("B", CausalNodeType.SYSTEM_BEHAVIOR_CHANGE),
        ("I", CausalNodeType.OPERATIONAL_CONSEQUENCE), ("H", CausalNodeType.HAZARD),
    ))
    edges = tuple(CausalEdge(edge_id, source, target, CausalRelation.CAUSES, edge_id, (edge_id,))
                  for edge_id, source, target in (("M_B", "M", "B"), ("B_I", "B", "I"), ("I_H", "I", "H")))
    return ScenarioCausalAssessment(
        "SC-1", CausalGraph(nodes, edges), ("M", "B", "I", "H"), CausalBreakpoint.NONE,
        tuple(EvidenceBinding(edge.edge_id, edge.evidence_refs, EvidenceKind.DIRECT_FACT,
                              status=ReviewStatus.FINALIZED) for edge in edges),
        (), (), "collision", review_status=ReviewStatus.FINALIZED,
    ).to_dict()


def _checkpoint() -> dict:
    return {
        "scenarios": [{
            "scenario_id": "SC-1",
            "facts": {
                "ego_speed_constraint": {"speed_max_kph": 20},
                "_fact_provenance": {},
            },
        }],
        "malfunctions": [{
            "malfunction_id": "MF-1", "functional_effect": "lost braking",
        }],
        "item_definition": {"scenario_assessments": [{
            "malfunction_id": "MF-1", "scenario_id": "SC-1",
            "status": "FINALIZED", "physically_feasible": True,
            "functionally_relevant": True, "causally_relevant": True,
            "final_retain": True, "hazardous_event": "collision", "causal_assessment": _causal(),
        }]},
    }


def _supplement() -> dict:
    return {"analytical_options_pending_validation": [{
        "malfunction_id": "MF-1", "parent_scenario_id": "SC-1",
        "causal_status": "PENDING_DIFFERENTIAL_VALIDATION",
        "analysis_instance": {
            "source_template_id": "TEMPLATE-1", "source_option_id": "TEMPLATE-1:OPTION:1",
            "source_option_values": {
                "obj_type": "pedestrian", "obj_distance_m": 2.0,
                "obj_v_kph": 0.0, "collision_type": "side",
            },
            "assumptions": [
                {"field": "road_user_type", "value": "PEDESTRIAN"},
                {"field": "collision_type", "value": "SIDE"},
            ],
        },
    }]}


def test_scoreability_never_promotes_odd_range_to_an_ego_point_speed():
    payload, queue = RiskScoreabilityService().generate(
        checkpoint=_checkpoint(), supplement=_supplement(),
    )

    row = payload["records"][0]
    assert row["S"]["ego_speed_ready"] is False
    assert row["S"]["relative_speed_derivable"] is False
    assert "EGO_POINT_SPEED_ENGINEERING_ASSUMPTION_REQUIRED" in row["S"]["blocker"]
    assert row["C"]["override_state"] == "UNKNOWN"
    assert row["C"]["ttc_ready"] is False
    assert payload["provider_calls"] == queue["provider_calls"] == 0
    assert queue["total_options"] == queue["deduplicated_semantic_groups"] == 1

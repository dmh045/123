from copy import deepcopy

from hara_agent.evaluation.metrics.scenario_real_provider import compare_order_attempts


def record(scenario_id, fingerprint, causal, mechanism="TEST-MECH", binding="SCN.fact"):
    return {
        "scenario_id": scenario_id,
        "semantic_fingerprint": fingerprint,
        "contract_valid": True,
        "provider_causal_verdict": causal,
        "provider_assessment": {
            "edges": [] if not causal else [{
                "edge_id": "M_TO_B",
                "supports": [{
                    "evidence_ref": "SCN.fact", "support_type": "DIRECT_FACT",
                }],
                "mechanism_application": {
                    "mechanism_id": mechanism, "mechanism_version": "1",
                    "bindings": {"fact": binding},
                },
            }],
        },
    }


def test_order_comparison_aligns_by_semantic_identity_not_array_position():
    left = [record("NEG", "fp-neg", False), record("POS", "fp-pos", True)] * 3
    right = list(reversed(deepcopy(left)))
    compared = compare_order_attempts(left, right)
    assert compared["shared_identity_count"] == 2
    assert compared["order_causal_agreement"] == 1.0
    assert compared["order_mechanism_agreement"] == 1.0
    assert compared["order_binding_agreement"] == 1.0
    assert compared["order_support_jaccard_min"] == 1.0


def test_order_binding_instability_is_visible_without_majority_vote():
    left = [record("POS", "fp-pos", True)]
    right = [record("POS", "fp-pos", True, binding="SCN.other")]
    compared = compare_order_attempts(left, right)
    assert compared["order_causal_agreement"] == 1.0
    assert compared["order_binding_agreement"] == 0.0

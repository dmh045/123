from hara_agent.evaluation.metrics.scenario_real_provider import (
    build_v1_v2_differential, summarize_provider_attempts,
)


def attempt(index, *, valid=True, causal=True, support_ref="DERIVED.ttc_s"):
    return {
        "attempt_id": f"a{index}",
        "scenario_id": "SCN-POS",
        "semantic_fingerprint": "fp-pos",
        "schema_valid": True,
        "contract_valid": valid,
        "provider_causal_verdict": causal,
        "provider_assessment": {
            "breakpoint": "NONE" if causal else "I_TO_H",
            "risk_dimensions": ["distance"] if causal else [],
            "edges": [{
                "edge_id": edge_id,
                "supports": [
                    {"evidence_ref": "SCN.contract", "support_type": "DIRECT_FACT"},
                    {"evidence_ref": support_ref, "support_type": "DERIVED_PHYSICS"},
                ],
                "mechanism_application": {
                    "mechanism_id": "TEST-MECH", "mechanism_version": "1",
                    "bindings": {
                        "transition_contract": "SCN.contract", "ttc": "DERIVED.ttc_s",
                    },
                },
            } for edge_id in ("M_TO_B", "B_TO_I", "I_TO_H", "H_TO_HARM")],
        },
        "error": None if valid else {
            "code": "INVALID_MECHANISM_APPLICATION",
            "mechanism_code": "UNKNOWN_MECHANISM",
        },
        "no_applicable_approved_mechanism": False,
    }


def test_positive_gold_support_and_binding_metrics_are_separate_and_exact():
    attempts = [attempt(index) for index in range(5)]
    metrics = summarize_provider_attempts(attempts, gold={
        "causally_relevant": True,
        "breakpoint": "NONE",
        "required_edges": ["M_TO_B", "B_TO_I", "I_TO_H", "H_TO_HARM"],
        "mechanism_id": "TEST-MECH",
        "mechanism_version": "1",
        "bindings": {
            "transition_contract": "SCN.contract", "ttc": "DERIVED.ttc_s",
        },
    })
    assert metrics["schema_valid_rate"] == 1.0
    assert metrics["contract_valid_rate"] == 1.0
    assert metrics["gold"]["causal_gold_agreement"] == 1.0
    assert metrics["gold"]["mechanism_selection_agreement"] == 1.0
    assert metrics["gold"]["required_binding_accuracy"] == 1.0
    assert metrics["gold"]["mixed_direct_derived_attempt_rate"] == 1.0
    assert metrics["support_jaccard_min"] == 1.0
    assert metrics["binding_exact_match_rate"] == 1.0


def test_invalid_positive_keeps_raw_provider_true_separate_from_contract_failure():
    invalid = attempt(1, valid=False)
    metrics = summarize_provider_attempts([invalid])
    assert metrics["provider_causal_distribution"] == {"true": 1}
    assert metrics["provider_causal_true_count"] == 1
    assert metrics["provider_causal_false_count"] == 0
    assert metrics["valid_causal_distribution"] == {}
    assert metrics["valid_causal_true_count"] == 0
    assert metrics["valid_causal_false_count"] == 0
    assert metrics["contract_valid_rate"] == 0.0
    assert metrics["unknown_mechanism_invention_count"] == 1
    assert metrics["causal_agreement"] is None
    assert metrics["support_jaccard_min"] is None


def test_differential_is_observational_and_never_claims_semantic_correctness():
    v1 = summarize_provider_attempts([attempt(1)])
    v2 = summarize_provider_attempts([attempt(1), attempt(2)])
    report = build_v1_v2_differential(v1, v2)
    assert report["classification"] == "OBSERVATIONAL_ONLY"
    assert report["semantic_correctness_conclusion"] is None
    assert report["v2_support_jaccard"]["min"] == 1.0


def test_support_reference_jaccard_is_independent_of_support_type_agreement():
    left = attempt(1)
    right = attempt(2)
    right["provider_assessment"]["edges"][0]["supports"][0]["support_type"] = (
        "DERIVED_PHYSICS"
    )

    metrics = summarize_provider_attempts([left, right])

    assert metrics["support_jaccard_min"] == 1.0
    assert metrics["support_type_agreement"] < 1.0

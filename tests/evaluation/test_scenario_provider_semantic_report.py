import eval_scenario_real_provider as runner


def metrics(*, schema=1.0, contract=1.0, gold=True):
    return {
        "attempt_count": 5,
        "schema_valid_rate": schema,
        "contract_valid_rate": contract,
        "provider_causal_distribution": {"false": 5},
        "unknown_mechanism_invention_count": 0,
        "error_code_distribution": {},
        "gold": {
            "causal_gold_agreement": 1.0 if gold else 0.8,
            "breakpoint_agreement": 1.0,
            "mechanism_selection_agreement": 1.0,
            "mechanism_version_agreement": 1.0,
            "required_binding_accuracy": 1.0,
        },
    }


def summary(positive=None, real=None):
    return {
        "synthetic_negative": {"metrics": metrics()},
        "synthetic_positive": {"metrics": positive or metrics()},
        "batch_order": {"comparison": {
            "order_causal_agreement": 1.0,
            "order_mechanism_agreement": 1.0,
            "order_binding_agreement": 1.0,
        }},
        "real_avp_v2": {
            "available_production_mechanism_count": 0,
            "metrics": real or metrics(),
        },
    }


def test_acceptance_keeps_three_status_axes_and_detects_epistemic_gap():
    result = runner._acceptance(summary())
    assert result["provider_schema_stability"] == "READY"
    assert result["controlled_semantic_stability"] == "READY"
    assert result["real_avp_scenario_evidence"] == "OBSERVATIONAL_ONLY"
    assert result["contract_expressiveness_gap"] is True
    assert result["knowledge_substrate_blocked"] is True
    assert result["primary_blocker"] == "CONTRACT_EXPRESSIVENESS_GAP"


def test_schema_instability_has_priority_and_is_not_reclassified_as_negative():
    result = runner._acceptance(summary(positive=metrics(schema=0.8)))
    assert result["provider_schema_stability"] == "NOT_READY"
    assert result["controlled_semantic_stability"] == "NOT_READY"
    assert result["primary_blocker"] == "PROVIDER_SCHEMA_INSTABILITY"

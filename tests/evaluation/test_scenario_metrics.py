from hara_agent.models import ScenarioFeasibilityAssessment
from hara_agent.services.semantic.scenario_evidence import (
    ScenarioEvidenceContractError, ScenarioEvidenceErrorCode,
)

from hara_agent.evaluation.metrics.scenario import compare_scenario_reports, scenario_repeat_metrics
from hara_agent.evaluation.models import EvaluationAttempt


def assessment(scenario_id="SCN-1", dimensions=None):
    return ScenarioFeasibilityAssessment(
        "MF-1", scenario_id, True, True, True, dimensions or ["distance"],
        "rationale", "hazard", "harm", confidence=0.8,
    )


def error(code):
    return ScenarioEvidenceContractError(
        "fixture", code=code, hop="i_to_h", reason="fixture",
        malfunction_id="MF-1", scenario_id="SCN-1", semantic_fingerprint="fp-1",
        invalid_evidence_refs=[], basis_type="", claim="",
    )


def test_zero_or_one_valid_sample_has_null_stability_not_perfect_scores():
    metrics = scenario_repeat_metrics([
        EvaluationAttempt(False, "SCN-1", "fp-1", error=error(
            ScenarioEvidenceErrorCode.CAUSAL_FALSE_WITH_DIMENSIONS
        )) for _ in range(5)
    ])
    assert metrics["valid_attempt_count"] == 0
    assert not metrics["stability_sample_sufficient"]
    assert metrics["causal_agreement_ratio"] is None
    assert metrics["risk_dimension_jaccard_min"] is None
    assert metrics["risk_dimension_jaccard_avg"] is None


def test_error_distribution_uses_codes_and_categories():
    attempts = [
        EvaluationAttempt(False, "SCN-1", "fp-1", error=error(code))
        for code in (
            ScenarioEvidenceErrorCode.CAUSAL_FALSE_WITH_DIMENSIONS,
            ScenarioEvidenceErrorCode.UNRESOLVED_EVIDENCE_REF,
            ScenarioEvidenceErrorCode.DERIVED_PHYSICS_KIND_MISMATCH,
            ScenarioEvidenceErrorCode.ASSUMPTION_IN_POSITIVE_CHAIN,
        )
    ]
    metrics = scenario_repeat_metrics(attempts)
    assert metrics["cross_field_invariant_error_count"] == 1
    assert metrics["unresolved_evidence_ref_count"] == 1
    assert metrics["evidence_kind_mismatch_count"] == 1
    assert metrics["assumption_violation_count"] == 1


def test_metamorphic_comparison_aligns_by_id_and_fingerprint_not_order():
    left = {"results": [
        {"scenario_id": "A", "semantic_fingerprint": "fa", "causally_relevant": True, "risk_dimensions_changed": ["distance"]},
        {"scenario_id": "B", "semantic_fingerprint": "fb", "causally_relevant": False, "risk_dimensions_changed": []},
    ]}
    right = {"results": list(reversed(left["results"]))}
    compared = compare_scenario_reports(left, right)
    assert compared["shared_identity_count"] == 2
    assert all(item["causal_agreement"] for item in compared["comparisons"])

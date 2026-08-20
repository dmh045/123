from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any

from hara_agent.services.semantic.scenario_evidence import ScenarioEvidenceErrorCode

from hara_agent.evaluation.models import EvaluationAttempt


CROSS_FIELD_CODES = {
    ScenarioEvidenceErrorCode.CAUSAL_BREAKPOINT_MISMATCH,
    ScenarioEvidenceErrorCode.CAUSAL_FALSE_WITH_DIMENSIONS,
    ScenarioEvidenceErrorCode.CAUSAL_TRUE_WITHOUT_DIMENSIONS,
}
KIND_MISMATCH_CODES = {
    ScenarioEvidenceErrorCode.DIRECT_FACT_KIND_MISMATCH,
    ScenarioEvidenceErrorCode.DERIVED_PHYSICS_KIND_MISMATCH,
    ScenarioEvidenceErrorCode.APPROVED_RULE_KIND_MISMATCH,
}


def scenario_repeat_metrics(attempts: list[EvaluationAttempt]) -> dict[str, Any]:
    valid = [item.result for item in attempts if item.valid]
    errors = [item.error for item in attempts if not item.valid]
    codes = [error.code for error in errors if hasattr(error, "code")]
    counts = Counter(code.value for code in codes)
    sufficient = len(valid) >= 2
    dimension_sets = [set(item.risk_dimensions_changed) for item in valid]
    jaccards = [
        len(left & right) / len(left | right) if left | right else 1.0
        for left, right in combinations(dimension_sets, 2)
    ]

    def agreement(values):
        return max(Counter(values).values()) / len(values) if sufficient else None

    return {
        "repeat_count": len(attempts),
        "valid_attempt_count": len(valid),
        "valid_attempt_ratio": len(valid) / len(attempts) if attempts else 0.0,
        "stability_sample_sufficient": sufficient,
        "evidence_contract_error_count": len(errors),
        "error_counts_by_code": dict(sorted(counts.items())),
        "cross_field_invariant_error_count": sum(code in CROSS_FIELD_CODES for code in codes),
        "unresolved_evidence_ref_count": counts.get(
            ScenarioEvidenceErrorCode.UNRESOLVED_EVIDENCE_REF.value, 0
        ),
        "evidence_kind_mismatch_count": sum(code in KIND_MISMATCH_CODES for code in codes),
        "assumption_violation_count": counts.get(
            ScenarioEvidenceErrorCode.ASSUMPTION_IN_POSITIVE_CHAIN.value, 0
        ),
        "causal_agreement_ratio": agreement([item.causally_relevant for item in valid]),
        "physical_agreement_ratio": agreement([item.physically_feasible for item in valid]),
        "functional_agreement_ratio": agreement([item.functionally_relevant for item in valid]),
        "risk_dimension_jaccard_min": min(jaccards) if sufficient else None,
        "risk_dimension_jaccard_avg": (
            sum(jaccards) / len(jaccards) if sufficient else None
        ),
    }


def compare_scenario_reports(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Align metamorphic results by semantic identity, never by array position."""
    def keyed(report):
        return {
            (item["scenario_id"], item["semantic_fingerprint"]): item
            for item in report.get("results", [])
        }
    left_items, right_items = keyed(left), keyed(right)
    shared = sorted(set(left_items) & set(right_items))
    return {
        "shared_identity_count": len(shared),
        "missing_from_left": sorted(set(right_items) - set(left_items)),
        "missing_from_right": sorted(set(left_items) - set(right_items)),
        "comparisons": [{
            "scenario_id": key[0], "semantic_fingerprint": key[1],
            "causal_agreement": left_items[key].get("causally_relevant") == right_items[key].get("causally_relevant"),
            "risk_dimension_jaccard": _jaccard(
                left_items[key].get("risk_dimensions_changed", []),
                right_items[key].get("risk_dimensions_changed", []),
            ),
        } for key in shared],
    }


def _jaccard(left, right):
    left_set, right_set = set(left), set(right)
    return len(left_set & right_set) / len(left_set | right_set) if left_set | right_set else 1.0

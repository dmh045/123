from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from hara_agent.evaluation.models import FactEvaluation, GapClassification
from hara_agent.models import FactProvenance


PRESENT_CLASSIFICATIONS = {
    GapClassification.PRESENT_EXPLICIT,
    GapClassification.PRESENT_DERIVABLE,
}
def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def extraction_metrics(evaluations: list[FactEvaluation]) -> dict[str, Any]:
    project_items = [
        item for item in evaluations
        if item.expected_provenance is not FactProvenance.METHOD_CONTRACT
    ]
    present = [item for item in project_items if item.classification in PRESENT_CLASSIFICATIONS]
    grounded = [item for item in project_items if item.grounded]
    grounded_present = [
        item for item in grounded if item.classification in PRESENT_CLASSIFICATIONS
    ]
    by_field: dict[str, dict[str, int | float | None]] = {}
    grouped: dict[str, list[FactEvaluation]] = defaultdict(list)
    for item in project_items:
        grouped[item.field].append(item)
    for field_name, items in sorted(grouped.items()):
        found = sum(item.classification in PRESENT_CLASSIFICATIONS for item in items)
        by_field[field_name] = {
            "expected": len(items),
            "present": found,
            "recall": _ratio(found, len(items)),
        }
    return {
        "expected_fact_count": len(project_items),
        "present_fact_count": len(present),
        "field_recall": _ratio(len(present), len(project_items)),
        "grounded_field_recall": _ratio(len(grounded_present), len(grounded)),
        "source_ref_accuracy": _ratio(
            sum(item.source_ref_match for item in present), len(present)
        ),
        "context_preservation_rate": _ratio(
            sum(item.context_match for item in present), len(present)
        ),
        "normalization_accuracy": _ratio(
            sum(item.normalization_match for item in project_items), len(project_items)
        ),
        "by_field": by_field,
    }


def gap_counts(evaluations: list[FactEvaluation]) -> dict[str, int]:
    counts = Counter(item.classification.value for item in evaluations)
    return {
        classification.value: counts.get(classification.value, 0)
        for classification in GapClassification
    }

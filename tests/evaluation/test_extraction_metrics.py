from __future__ import annotations

from hara_agent.evaluation.metrics import extraction_metrics, gap_counts
from hara_agent.evaluation.models import FactEvaluation, GapClassification


def result(fact_id, classification, *, grounded=True, source=True, context=True, normalized=True):
    return FactEvaluation(
        fact_id=fact_id,
        field="speed_envelopes",
        classification=classification,
        value_match=normalized,
        grounded=grounded,
        source_ref_match=source,
        context_match=context,
        normalization_match=normalized,
    )


def test_extraction_metrics_use_explicit_denominators():
    evaluations = [
        result("search", GapClassification.PRESENT_EXPLICIT),
        result(
            "parking", GapClassification.NORMALIZATION_LOSS,
            source=False, context=False, normalized=False,
        ),
        result(
            "missing", GapClassification.SOURCE_NOT_PROVIDED,
            grounded=False, source=False, context=False, normalized=False,
        ),
    ]

    metrics = extraction_metrics(evaluations)

    assert metrics["field_recall"] == 1 / 3
    assert metrics["grounded_field_recall"] == 1 / 2
    assert metrics["source_ref_accuracy"] == 1.0
    assert metrics["context_preservation_rate"] == 1.0
    assert metrics["normalization_accuracy"] == 1 / 3
    assert gap_counts(evaluations)["NORMALIZATION_LOSS"] == 1

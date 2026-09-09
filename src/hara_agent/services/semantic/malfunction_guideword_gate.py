"""Authoritative Guideword-to-Malfunction boundary validation."""

from __future__ import annotations

from collections.abc import Collection

from hara_agent.models import (
    GuidewordAssessment,
    GuidewordDisposition,
    ReviewStatus,
)


class MalfunctionGuidewordGateViolation(ValueError):
    """A candidate attempted to bypass the Guideword disposition gate."""


def validate_malfunction_guideword_gate(
    *,
    function_id: str,
    guideword: str,
    guideword_id: str,
    assessment: GuidewordAssessment | None,
    scheduled_guideword_ids: Collection[str],
) -> GuidewordAssessment:
    """Return the exact eligible assessment or fail closed.

    This is intentionally an exact-identity check.  It performs no disposition,
    status, or guideword-ID coercion; caller-side provider normalization remains
    separate from the governed stage boundary.
    """
    if assessment is None:
        raise MalfunctionGuidewordGateViolation(
            "MALFUNCTION_GUIDEWORD_GATE_VIOLATION "
            f"function_id={function_id} guideword={guideword!r} "
            f"guideword_id={guideword_id!r} reason=unknown_guideword"
        )

    scheduled_ids = set(scheduled_guideword_ids)
    eligible = (
        assessment.function_id == function_id
        and assessment.guideword == guideword
        and assessment.guideword_id == guideword_id
        and assessment.applicable is True
        and assessment.disposition is GuidewordDisposition.DOWNSTREAM_CANDIDATE
        and assessment.is_semantically_complete
        and assessment.status is ReviewStatus.FINALIZED
        and assessment.guideword_id in scheduled_ids
    )
    if not eligible:
        raise MalfunctionGuidewordGateViolation(
            "MALFUNCTION_GUIDEWORD_GATE_VIOLATION "
            f"function_id={function_id} guideword={guideword!r} "
            f"guideword_id={guideword_id!r} "
            f"assessment_function_id={assessment.function_id!r} "
            f"assessment_guideword={assessment.guideword!r} "
            f"assessment_guideword_id={assessment.guideword_id!r} "
            f"applicable={assessment.applicable} "
            f"disposition={assessment.disposition.value} "
            f"status={assessment.status.value} "
            "required=exact_identity+applicable+DOWNSTREAM_CANDIDATE+"
            "FINALIZED+scheduled"
        )
    return assessment

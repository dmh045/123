"""Reusable offline metrics for Scenario selector recall and coverage."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


_EXACT_AUTHORITY_CLASSES = frozenset({
    "EXACT_BINDING", "EXACT_METHOD_BINDING", "APPROVED_ALIAS",
    "EXACT_SOURCE_TEMPLATE_CONSTRAINT",
})
_STRUCTURED_SOURCE_CLASSES = frozenset({
    "EXACT_STRUCTURED_SOURCE_MATCH", "METHOD_PHYSICAL_SEMANTICS_MATCH",
    "FM_TEMPLATE_SOURCE_MATCH",
})
_EXPLICIT_FIELD_CLASSES = frozenset({
    "EXPLICIT_HAZARD_OR_CAUSAL_MATCH", "FIELD_CORRECT_CATEGORY_MATCH",
})


def independent_evidence_tier(classes: Iterable[str]) -> int:
    values = set(map(str, classes))
    if values & _EXACT_AUTHORITY_CLASSES:
        return 4
    if values & _STRUCTURED_SOURCE_CLASSES:
        return 3
    if values & _EXPLICIT_FIELD_CLASSES:
        return 2
    if "ODD_CONTEXT_COMPATIBILITY" in values:
        return 1
    return 0


def top_k_recall_audit(
    ranked: Iterable[Mapping[str, Any]], *, cap: int,
) -> dict[str, Any]:
    rows = list(ranked)
    retained = rows[:cap]
    below = rows[cap:]
    cutoff_tier = min(
        (int(item.get("independent_evidence_tier", 0)) for item in retained),
        default=0,
    )
    source_strong = [
        item for item in below
        if int(item.get("independent_evidence_tier", 0)) >= 2
    ]
    at_risk = [
        item for item in source_strong
        if int(item.get("independent_evidence_tier", 0)) >= cutoff_tier
    ]
    kth = retained[-1] if retained else {}
    next_item = below[0] if below else {}
    return {
        "truncated": bool(below),
        "rank_k_score": float(kth.get("final_rank_score", 0.0) or 0.0),
        "rank_k_plus_1_score": float(next_item.get("final_rank_score", 0.0) or 0.0),
        "score_margin": round(
            float(kth.get("final_rank_score", 0.0) or 0.0)
            - float(next_item.get("final_rank_score", 0.0) or 0.0),
            6,
        ) if below else None,
        "source_strong_candidates_below_cutoff": len(source_strong),
        "exact_structured_matches_below_cutoff": sum(
            "EXACT_STRUCTURED_SOURCE_MATCH" in item.get("independent_evidence", [])
            for item in below
        ),
        "fm_template_matches_below_cutoff": sum(
            "FM_TEMPLATE_SOURCE_MATCH" in item.get("independent_evidence", [])
            for item in below
        ),
        "top_k_recall_risk": bool(at_risk),
        "at_risk_atom_ids": [str(item.get("atom_id", "")) for item in at_risk],
    }


__all__ = ["independent_evidence_tier", "top_k_recall_audit"]

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, ReviewStatus,
    ScenarioCandidate, SourceRef,
)


class DerivedPhysicsType(str, Enum):
    TTC = "TTC"
    RELATIVE_MOTION = "RELATIVE_MOTION"
    CANONICAL_INPUT_NORMALIZATION = "CANONICAL_INPUT_NORMALIZATION"


def _distance_m(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*m\s*", str(value or ""), re.IGNORECASE,
    )
    return float(match.group(1)) if match else None


def _input_metadata(
    scenario: ScenarioCandidate, key: str,
) -> tuple[ReviewStatus, tuple[SourceRef, ...]]:
    metadata = scenario.fact_provenance.get(key, {})
    if not isinstance(metadata, dict):
        return ReviewStatus.PENDING, ()
    try:
        approval = ReviewStatus(metadata.get("approval", scenario.status.value))
    except ValueError:
        approval = ReviewStatus.PENDING
    sources = tuple(
        item if isinstance(item, SourceRef) else SourceRef(**item)
        for item in metadata.get("source_refs", [])
        if isinstance(item, (SourceRef, dict))
    )
    return approval, sources


def derive_scenario_physics(
    scenario: ScenarioCandidate,
) -> tuple[EvidenceRecord, ...]:
    """Derive neutral physics from canonical scenario facts only.

    This module supplies quantities such as TTC.  It never maps a quantity to
    S/E/C or selects a project profile; those decisions remain MethodContract
    authority.
    """

    records: list[EvidenceRecord] = []
    normalized: dict[str, tuple[Any, str]] = {}
    for key in (
        "ego_speed_kph", "relative_speed_kph", "impact_speed_kph", "delta_v_kph",
    ):
        value = scenario.facts.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            continue
        normalized[key] = (float(value), key)
    distance_m = _distance_m(
        scenario.facts.get("relative_distance_m", scenario.facts.get("relative_distance"))
    )
    if distance_m is not None:
        normalized["relative_distance_m"] = (distance_m, "relative_distance")

    # Preserve canonical numeric inputs as deterministic, source-linked
    # physics facts.  This is normalization only; it does not derive a new
    # collision or select an S/E/C value.
    for output_key, (value, input_key) in normalized.items():
        status, sources = _input_metadata(scenario, input_key)
        metadata = {
            "derivation_type": DerivedPhysicsType.CANONICAL_INPUT_NORMALIZATION.value,
            "inputs": [f"SCN.{input_key}"],
        }
        if scenario.semantic_fingerprint:
            metadata["semantic_fingerprint"] = scenario.semantic_fingerprint
        records.append(EvidenceRecord(
            f"DERIVED.{output_key}", value,
            EvidenceKind.DERIVED_PHYSICS,
            FactProvenance.DERIVED,
            status,
            sources,
            metadata,
        ))
    relative_speed = scenario.facts.get("relative_speed_kph")
    if (
        distance_m is not None
        and not isinstance(relative_speed, bool)
        and isinstance(relative_speed, (int, float))
        and relative_speed > 0
    ):
        input_keys = ("relative_distance", "relative_speed_kph")
        input_metadata = [_input_metadata(scenario, key) for key in input_keys]
        derived_sources = tuple(dict.fromkeys(
            source for _, sources in input_metadata for source in sources
        ))
        derived_status = (
            ReviewStatus.FINALIZED
            if all(status is ReviewStatus.FINALIZED for status, _ in input_metadata)
            else ReviewStatus.PENDING
        )
        metadata = {
            "derivation_type": DerivedPhysicsType.TTC.value,
            "inputs": ["SCN.relative_distance", "SCN.relative_speed_kph"],
        }
        if scenario.semantic_fingerprint:
            metadata["semantic_fingerprint"] = scenario.semantic_fingerprint
        records.append(EvidenceRecord(
            "DERIVED.ttc_s",
            round(distance_m / (float(relative_speed) / 3.6), 6),
            EvidenceKind.DERIVED_PHYSICS,
            FactProvenance.DERIVED,
            derived_status,
            derived_sources,
            metadata,
        ))
    return tuple(records)

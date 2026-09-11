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
) -> tuple[ReviewStatus, tuple[SourceRef, ...], dict[str, Any]]:
    metadata = scenario.fact_provenance.get(key, {})
    if not isinstance(metadata, dict):
        return ReviewStatus.PENDING, (), {}
    try:
        approval = ReviewStatus(metadata.get("approval", scenario.status.value))
    except ValueError:
        approval = ReviewStatus.PENDING
    sources = tuple(
        item if isinstance(item, SourceRef) else SourceRef(**item)
        for item in metadata.get("source_refs", [])
        if isinstance(item, (SourceRef, dict))
    )
    return approval, sources, metadata


def _analysis_lineage(metadata: dict[str, Any]) -> dict[str, Any]:
    if str(metadata.get("provenance", "")).upper() != FactProvenance.SCENARIO_DEFINED.value:
        return {}
    scope = metadata.get("applicable_scope", {})
    if not isinstance(scope, dict):
        return {}
    return {
        "analysis_assumption_origin": FactProvenance.SCENARIO_DEFINED.value,
        "analysis_assumption_scope": dict(scope),
        "validation_status": metadata.get("validation_status", ""),
        "source_template_id": metadata.get("source_template_id", ""),
        "source_option_id": metadata.get("source_option_id", ""),
        "method_contract_hash": metadata.get("method_contract_hash", ""),
    }


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
    distance_input_key = (
        "relative_distance_m"
        if "relative_distance_m" in scenario.facts
        else "relative_distance"
    )
    distance_m = _distance_m(scenario.facts.get(distance_input_key))
    if distance_m is not None:
        normalized["relative_distance_m"] = (distance_m, distance_input_key)

    # Preserve canonical numeric inputs as deterministic, source-linked
    # physics facts.  This is normalization only; it does not derive a new
    # collision or select an S/E/C value.
    for output_key, (value, input_key) in normalized.items():
        status, sources, input_metadata = _input_metadata(scenario, input_key)
        metadata = {
            "derivation_type": DerivedPhysicsType.CANONICAL_INPUT_NORMALIZATION.value,
            "inputs": [f"SCN.{input_key}"],
            **_analysis_lineage(input_metadata),
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
        input_keys = (distance_input_key, "relative_speed_kph")
        input_metadata = [_input_metadata(scenario, key) for key in input_keys]
        derived_sources = tuple(dict.fromkeys(
            source for _, sources, _ in input_metadata for source in sources
        ))
        derived_status = (
            ReviewStatus.FINALIZED
            if all(status is ReviewStatus.FINALIZED for status, _, _ in input_metadata)
            else ReviewStatus.PENDING
        )
        metadata = {
            "derivation_type": DerivedPhysicsType.TTC.value,
            "inputs": [f"SCN.{distance_input_key}", "SCN.relative_speed_kph"],
        }
        analytical = [
            _analysis_lineage(item)
            for _, _, item in input_metadata
            if _analysis_lineage(item)
        ]
        if analytical:
            scopes = {str(item["analysis_assumption_scope"]) for item in analytical}
            if len(scopes) == 1:
                metadata.update(analytical[0])
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

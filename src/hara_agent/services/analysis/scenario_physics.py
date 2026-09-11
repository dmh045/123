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


_ANALYSIS_ORIGIN = FactProvenance.SCENARIO_DEFINED.value
_FINAL_APPROVALS = {ReviewStatus.FINALIZED.value, "APPROVED"}


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
    origin = str(metadata.get(
        "analysis_assumption_origin",
        metadata.get("origin", metadata.get("provenance", "")),
    )).upper()
    if origin != _ANALYSIS_ORIGIN:
        return {}
    scope = metadata.get(
        "analysis_assumption_scope", metadata.get("applicable_scope", {}),
    )
    return {
        "analysis_assumption_origin": _ANALYSIS_ORIGIN,
        "analysis_assumption_scope": dict(scope) if isinstance(scope, dict) else scope,
        "validation_status": metadata.get("validation_status", ""),
        "source_template_id": metadata.get("source_template_id", ""),
        "source_option_id": metadata.get("source_option_id", ""),
        "method_contract_hash": metadata.get("method_contract_hash", ""),
    }


def analysis_assumption_lineages(metadata: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return every analytical assumption retained by a fact or derivation."""
    inputs = metadata.get("analysis_assumption_inputs", ())
    if isinstance(inputs, list):
        lineages = [
            _analysis_lineage(item)
            for item in inputs
            if isinstance(item, dict) and _analysis_lineage(item)
        ]
        if lineages:
            return tuple(lineages)
    lineage = _analysis_lineage(metadata)
    return (lineage,) if lineage else ()


def has_analysis_assumption_lineage(metadata: dict[str, Any]) -> bool:
    return bool(analysis_assumption_lineages(metadata))


def analysis_assumption_is_valid_for(
    metadata: dict[str, Any], *, malfunction_id: str, scenario_id: str,
) -> bool:
    """Validate every retained analytical source against one M x Scenario."""
    lineages = analysis_assumption_lineages(metadata)
    return bool(lineages) and all(
        item["analysis_assumption_origin"] == _ANALYSIS_ORIGIN
        and str(item.get("validation_status", "")).upper() == "VALIDATED"
        and isinstance(item.get("analysis_assumption_scope"), dict)
        and str(item["analysis_assumption_scope"].get("malfunction_id", ""))
        == malfunction_id
        and str(item["analysis_assumption_scope"].get("scenario_id", ""))
        == scenario_id
        for item in lineages
    )


def source_is_accepted_for(
    metadata: dict[str, Any], *, malfunction_id: str, scenario_id: str,
) -> bool:
    """Apply the one source contract used by derived physics and RiskContext."""
    inputs = metadata.get("input_fact_metadata", ())
    if isinstance(inputs, list):
        return bool(inputs) and all(
            source_is_accepted_for(
                item, malfunction_id=malfunction_id, scenario_id=scenario_id,
            )
            for item in inputs if isinstance(item, dict)
        ) and all(isinstance(item, dict) for item in inputs)
    if (
        str(metadata.get("provenance", "")).upper() == FactProvenance.DERIVED.value
        and metadata.get("inputs")
    ):
        return False
    if has_analysis_assumption_lineage(metadata):
        return analysis_assumption_is_valid_for(
            metadata, malfunction_id=malfunction_id, scenario_id=scenario_id,
        )
    return str(metadata.get("approval", "")).upper() in _FINAL_APPROVALS


def source_origin(metadata: dict[str, Any]) -> str:
    """Expose fact origin independently from the derived/direct authority axis."""
    if has_analysis_assumption_lineage(metadata):
        return _ANALYSIS_ORIGIN
    if metadata.get("source_binding_kind") == "METHOD_RISK_FACT_BINDING":
        return "METHOD_RISK_FACT_BINDING"
    return str(metadata.get("origin", metadata.get("provenance", ""))).upper()


def _source_dict(source: SourceRef) -> dict[str, str]:
    return {
        "source_type": source.source_type,
        "source_id": source.source_id,
        "location": source.location,
        "excerpt": source.excerpt,
    }


def _input_snapshot(
    key: str, status: ReviewStatus, sources: tuple[SourceRef, ...],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    snapshot = {
        "field": key,
        "provenance": metadata.get("provenance", ""),
        "origin": metadata.get("origin", ""),
        "approval": status.value,
        "source_refs": [_source_dict(item) for item in sources],
    }
    for field in (
        "analysis_assumption_origin", "analysis_assumption_scope",
        "validation_status", "source_template_id", "source_option_id",
        "method_contract_hash", "source_binding_kind", "applicable_scope",
        "input_fact_metadata", "analysis_assumption_inputs",
    ):
        if field in metadata:
            snapshot[field] = metadata[field]
    return snapshot


def _combined_analysis_lineage(
    lineages: list[dict[str, Any]],
) -> dict[str, Any]:
    if not lineages:
        return {}
    result = {
        "analysis_assumption_origin": _ANALYSIS_ORIGIN,
        "analysis_assumption_inputs": lineages,
    }
    scopes = {str(item.get("analysis_assumption_scope")) for item in lineages}
    statuses = {str(item.get("validation_status", "")) for item in lineages}
    if len(scopes) == 1:
        result["analysis_assumption_scope"] = lineages[0]["analysis_assumption_scope"]
    if len(statuses) == 1:
        result["validation_status"] = lineages[0]["validation_status"]
    return result


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
            **_combined_analysis_lineage([
                _analysis_lineage(input_metadata),
            ] if _analysis_lineage(input_metadata) else []),
        }
        if input_metadata:
            metadata["input_fact_metadata"] = [
                _input_snapshot(input_key, status, sources, input_metadata),
            ]
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
        if all(item for _, _, item in input_metadata):
            metadata["input_fact_metadata"] = [
                _input_snapshot(key, status, sources, item)
                for key, (status, sources, item) in zip(input_keys, input_metadata)
            ]
        analytical = [
            {**_analysis_lineage(item), "input_field": key}
            for key, (_, _, item) in zip(input_keys, input_metadata)
            if _analysis_lineage(item)
        ]
        metadata.update(_combined_analysis_lineage(analytical))
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

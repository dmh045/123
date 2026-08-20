from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, MalfunctionCandidate,
    ReviewStatus, ScenarioCandidate, SourceRef,
)

from .scenario_contract import RISK_DIMENSION_VALUES, SCENARIO_CONTRACT_VERSION


SCENARIO_ASSESSMENT_CONTRACT_VERSION = "scenario-evidence-v1"


# v1 hop basis values and Registry evidence kinds intentionally share values,
# while kind and provenance remain separate axes.
EvidenceBasisType = EvidenceKind


class CausalBreakpoint(str, Enum):
    M_TO_B = "M_TO_B"
    B_TO_I = "B_TO_I"
    I_TO_H = "I_TO_H"
    H_TO_HARM = "H_TO_HARM"
    NONE = "NONE"


class DerivedPhysicsType(str, Enum):
    TTC = "TTC"
    RELATIVE_MOTION = "RELATIVE_MOTION"


class ScenarioEvidenceErrorCode(str, Enum):
    INVALID_BREAKPOINT = "INVALID_BREAKPOINT"
    CAUSAL_BREAKPOINT_MISMATCH = "CAUSAL_BREAKPOINT_MISMATCH"
    INVALID_CAUSAL_CHAIN_SHAPE = "INVALID_CAUSAL_CHAIN_SHAPE"
    REQUIRED_HOP_MISSING = "REQUIRED_HOP_MISSING"
    EMPTY_HOP_CLAIM = "EMPTY_HOP_CLAIM"
    INVALID_EVIDENCE_REF_SHAPE = "INVALID_EVIDENCE_REF_SHAPE"
    INVALID_BASIS_TYPE = "INVALID_BASIS_TYPE"
    UNRESOLVED_EVIDENCE_REF = "UNRESOLVED_EVIDENCE_REF"
    MISSING_EVIDENCE_REF = "MISSING_EVIDENCE_REF"
    DIRECT_FACT_KIND_MISMATCH = "DIRECT_FACT_KIND_MISMATCH"
    DERIVED_PHYSICS_KIND_MISMATCH = "DERIVED_PHYSICS_KIND_MISMATCH"
    APPROVED_RULE_KIND_MISMATCH = "APPROVED_RULE_KIND_MISMATCH"
    ASSUMPTION_IN_POSITIVE_CHAIN = "ASSUMPTION_IN_POSITIVE_CHAIN"
    INVALID_RISK_DIMENSION_SHAPE = "INVALID_RISK_DIMENSION_SHAPE"
    UNKNOWN_RISK_DIMENSION = "UNKNOWN_RISK_DIMENSION"
    DUPLICATE_RISK_DIMENSION = "DUPLICATE_RISK_DIMENSION"
    INVALID_RISK_DIMENSION_EVIDENCE = "INVALID_RISK_DIMENSION_EVIDENCE"
    MISSING_RISK_DIMENSION_REASON = "MISSING_RISK_DIMENSION_REASON"
    CAUSAL_FALSE_WITH_DIMENSIONS = "CAUSAL_FALSE_WITH_DIMENSIONS"
    CAUSAL_TRUE_WITHOUT_DIMENSIONS = "CAUSAL_TRUE_WITHOUT_DIMENSIONS"


class ScenarioEvidenceContractError(ValueError):
    """A Scenario result cites missing or impermissible causal evidence."""

    def __init__(self, message: str, *, code: ScenarioEvidenceErrorCode, hop: str,
                 reason: str, malfunction_id: str, scenario_id: str,
                 semantic_fingerprint: str, invalid_evidence_refs: list[str],
                 basis_type: str, claim: str):
        super().__init__(message)
        self.code = code
        self.hop = hop
        self.reason = reason
        self.malfunction_id = malfunction_id
        self.scenario_id = scenario_id
        self.semantic_fingerprint = semantic_fingerprint
        self.invalid_evidence_refs = list(invalid_evidence_refs)
        self.basis_type = basis_type
        self.claim = claim


class FactRegistry:
    """Typed evidence store with fail-closed namespaces and collisions."""

    def __init__(self, facts: dict[str, Any] | None = None):
        self._records: dict[str, EvidenceRecord] = {}
        self._duplicate_ref_count = 0
        for evidence_ref, value in (facts or {}).items():
            self.register(
                value if isinstance(value, EvidenceRecord)
                else self._legacy_record(evidence_ref, value)
            )

    @property
    def facts(self) -> dict[str, dict[str, Any]]:
        """v1 compatibility snapshot; the authoritative store is typed."""
        return {
            ref: self._compatibility_dict(record)
            for ref, record in self._records.items()
        }

    @property
    def records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records.values())

    def register(self, record: EvidenceRecord) -> None:
        if record.evidence_ref in self._records:
            self._duplicate_ref_count += 1
            raise DuplicateEvidenceRefError(record.evidence_ref)
        self._records[record.evidence_ref] = record

    def extend(self, records) -> None:
        for record in records:
            self.register(record)

    def to_prompt_dict(self) -> dict[str, dict[str, Any]]:
        # scenario-evidence-v1 intentionally remains provenance-unaware.
        return {
            ref: {
                "value": record.value,
                "kind": record.kind.value,
                **(
                    {
                        "derivation_type": record.metadata.get("derivation_type"),
                        "inputs": record.metadata.get("inputs", []),
                    }
                    if record.kind is EvidenceKind.DERIVED_PHYSICS else {}
                ),
            }
            for ref, record in self._records.items()
        }

    def resolve(self, evidence_ref: str) -> dict[str, Any] | None:
        record = self._records.get(evidence_ref)
        return self._compatibility_dict(record) if record else None

    def resolve_record(self, evidence_ref: str) -> EvidenceRecord | None:
        return self._records.get(evidence_ref)

    def snapshot(self, *, include_values: bool = False) -> dict[str, Any]:
        return {
            "records": [
                record.to_dict(include_value=include_values)
                for record in self._records.values()
            ],
            "diagnostics": {
                "record_count": len(self._records),
                "duplicate_ref_count": self._duplicate_ref_count,
            },
        }

    @staticmethod
    def _legacy_record(evidence_ref: str, value: Any) -> EvidenceRecord:
        payload = dict(value) if isinstance(value, dict) else {"value": value}
        kind = EvidenceKind(payload.pop("kind", EvidenceKind.DIRECT_FACT.value))
        provenance_default = (
            FactProvenance.DERIVED
            if evidence_ref.startswith("DERIVED.")
            else FactProvenance.LLM_INFERENCE
        )
        provenance = FactProvenance(payload.pop("provenance", provenance_default.value))
        approval = ReviewStatus(payload.pop(
            "approval_status", payload.pop("approval", ReviewStatus.PENDING.value)
        ))
        sources = tuple(
            item if isinstance(item, SourceRef) else SourceRef(**item)
            for item in payload.pop("source_refs", payload.pop("sources", []))
        )
        record_value = payload.pop("value", None)
        metadata = payload.pop("metadata", {})
        metadata = {**payload, **metadata}
        return EvidenceRecord(
            evidence_ref, record_value, kind, provenance, approval, sources, metadata
        )

    @staticmethod
    def _compatibility_dict(record: EvidenceRecord) -> dict[str, Any]:
        return {
            "value": record.value,
            "kind": record.kind.value,
            "provenance": record.provenance.value,
            "approval_status": record.approval_status.value,
            "source_refs": [
                {
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "location": item.location,
                    "excerpt": item.excerpt,
                }
                for item in record.source_refs
            ],
            **record.metadata,
        }


class DuplicateEvidenceRefError(ValueError):
    def __init__(self, evidence_ref: str):
        self.evidence_ref = evidence_ref
        super().__init__(f"duplicate evidence_ref rejected: {evidence_ref}")


def build_fact_registry(
    malfunction: MalfunctionCandidate, scenario: ScenarioCandidate,
    project_registry: FactRegistry | None = None,
) -> FactRegistry:
    registry = FactRegistry()
    if project_registry is not None:
        registry.extend(project_registry.records)
    malfunction_provenance = (
        FactProvenance.PROJECT_INPUT
        if malfunction.sources else FactProvenance.LLM_INFERENCE
    )
    for name, value in (
        ("description", malfunction.description),
        ("functional_effect", malfunction.functional_effect),
        ("vehicle_level_hazard", malfunction.vehicle_level_hazard),
    ):
        registry.register(EvidenceRecord(
            f"MF.{name}", value, EvidenceKind.DIRECT_FACT,
            malfunction_provenance, malfunction.status, tuple(malfunction.sources),
            {"extracted_by": "LLM"},
        ))
    for key, value in sorted(scenario.facts.items()):
        if value is None or key.endswith("_source_status") or key in {
            "engineering_status", "review_reason",
        }:
            continue
        metadata = scenario.fact_provenance.get(key, {})
        if not isinstance(metadata, dict):
            metadata = {}
        provenance = FactProvenance(
            metadata.get("provenance", FactProvenance.LLM_INFERENCE.value)
        )
        approval = ReviewStatus(metadata.get("approval", scenario.status.value))
        sources = tuple(
            item if isinstance(item, SourceRef) else SourceRef(**item)
            for item in metadata.get("source_refs", [])
        )
        registry.register(EvidenceRecord(
            f"SCN.{key}", value, EvidenceKind.DIRECT_FACT,
            provenance, approval, sources,
            {name: item for name, item in metadata.items() if name not in {
                "provenance", "approval", "source_refs",
            }},
        ))
    distance_m = _distance_m(scenario.facts.get("relative_distance"))
    relative_speed = scenario.facts.get("relative_speed_kph")
    if distance_m is not None and isinstance(relative_speed, (int, float)) and relative_speed > 0:
        registry.register(EvidenceRecord(
            "DERIVED.ttc_s",
            round(distance_m / (float(relative_speed) / 3.6), 6),
            EvidenceKind.DERIVED_PHYSICS,
            FactProvenance.DERIVED,
            ReviewStatus.PENDING,
            (),
            {
                "derivation_type": DerivedPhysicsType.TTC.value,
                "inputs": ["SCN.relative_distance", "SCN.relative_speed_kph"],
            },
        ))
    return registry


def _distance_m(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*m\s*", str(value or ""), re.IGNORECASE)
    return float(match.group(1)) if match else None


def validate_evidence_contract(
    *,
    malfunction: MalfunctionCandidate,
    scenario: ScenarioCandidate,
    item: dict[str, Any],
    registry: FactRegistry,
    prompt_version: str,
    batch: str,
    split_path: str,
    split_depth: int,
) -> tuple[list[str], dict[str, Any]]:
    causal = item["causally_relevant"]
    breakpoint_raw = item.get("breakpoint")
    try:
        breakpoint = CausalBreakpoint(breakpoint_raw)
    except (ValueError, TypeError) as exc:
        raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                     code=ScenarioEvidenceErrorCode.INVALID_BREAKPOINT,
                     hop="NONE", claim="", basis_type="", invalid_refs=[],
                     reason=f"invalid breakpoint={breakpoint_raw!r}") from exc
    if causal != (breakpoint is CausalBreakpoint.NONE):
        raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                     code=ScenarioEvidenceErrorCode.CAUSAL_BREAKPOINT_MISMATCH,
                     hop="NONE", claim="", basis_type="", invalid_refs=[],
                     reason=f"causal/breakpoint mismatch causal={causal} breakpoint={breakpoint.value}")

    chain = item.get("causal_chain")
    if not isinstance(chain, dict):
        raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                     code=ScenarioEvidenceErrorCode.INVALID_CAUSAL_CHAIN_SHAPE,
                     hop="causal_chain", claim="", basis_type="", invalid_refs=[],
                     reason="causal_chain must be object")
    hop_names = ("m_to_b", "b_to_i", "i_to_h", "h_to_harm")
    breakpoint_index = {
        CausalBreakpoint.M_TO_B: 0, CausalBreakpoint.B_TO_I: 1,
        CausalBreakpoint.I_TO_H: 2, CausalBreakpoint.H_TO_HARM: 3,
        CausalBreakpoint.NONE: 4,
    }[breakpoint]
    assumption_hops = 0
    for index, hop_name in enumerate(hop_names):
        hop = chain.get(hop_name)
        required = causal or index <= breakpoint_index
        if not isinstance(hop, dict):
            if required:
                raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                             code=ScenarioEvidenceErrorCode.REQUIRED_HOP_MISSING,
                             hop=hop_name, claim="", basis_type="", invalid_refs=[],
                             reason="required hop missing")
            continue
        claim = str(hop.get("claim", "")).strip()
        refs = hop.get("evidence_refs")
        basis_raw = hop.get("basis_type")
        try:
            basis = EvidenceBasisType(basis_raw)
        except (ValueError, TypeError) as exc:
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.INVALID_BASIS_TYPE,
                         hop=hop_name, claim=claim, basis_type=str(basis_raw), invalid_refs=[],
                         reason="invalid basis_type") from exc
        if not claim:
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.EMPTY_HOP_CLAIM,
                         hop=hop_name, claim=claim, basis_type=basis.value, invalid_refs=[],
                         reason="hop claim must be non-empty")
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.INVALID_EVIDENCE_REF_SHAPE,
                         hop=hop_name, claim=claim, basis_type=basis.value, invalid_refs=[],
                         reason="evidence_refs must be an array of strings")
        invalid_refs = [ref for ref in refs if registry.resolve(ref) is None]
        if invalid_refs:
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.UNRESOLVED_EVIDENCE_REF,
                         hop=hop_name, claim=claim, basis_type=basis.value,
                         invalid_refs=invalid_refs, reason="unresolved evidence refs")
        if basis is not EvidenceBasisType.ASSUMPTION and not refs:
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.MISSING_EVIDENCE_REF,
                         hop=hop_name, claim=claim, basis_type=basis.value, invalid_refs=[],
                         reason="supported hop requires evidence refs")
        resolved = [registry.resolve(ref) for ref in refs]
        if basis is EvidenceBasisType.DIRECT_FACT and any(
            fact["kind"] != "DIRECT_FACT" for fact in resolved
        ):
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.DIRECT_FACT_KIND_MISMATCH,
                         hop=hop_name, claim=claim, basis_type=basis.value, invalid_refs=refs,
                         reason="DIRECT_FACT refs must resolve to direct facts")
        if basis is EvidenceBasisType.DERIVED_PHYSICS and (
            not refs or any(fact["kind"] != "DERIVED_PHYSICS" for fact in resolved)
        ):
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.DERIVED_PHYSICS_KIND_MISMATCH,
                         hop=hop_name, claim=claim, basis_type=basis.value, invalid_refs=refs,
                         reason="DERIVED_PHYSICS must cite registered bounded derivation")
        if basis is EvidenceBasisType.APPROVED_RULE and (
            not refs or any(fact["kind"] != "APPROVED_RULE" for fact in resolved)
        ):
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.APPROVED_RULE_KIND_MISMATCH,
                         hop=hop_name, claim=claim, basis_type=basis.value, invalid_refs=refs,
                         reason="APPROVED_RULE must cite approved rule ref")
        if basis is EvidenceBasisType.ASSUMPTION:
            assumption_hops += 1
            if causal and index < 4:
                raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                             code=ScenarioEvidenceErrorCode.ASSUMPTION_IN_POSITIVE_CHAIN,
                             hop=hop_name, claim=claim, basis_type=basis.value, invalid_refs=[],
                             reason="causal=true cannot depend on assumption")

    changes = item.get("risk_dimension_changes")
    if not isinstance(changes, list):
        raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                     code=ScenarioEvidenceErrorCode.INVALID_RISK_DIMENSION_SHAPE,
                     hop="risk_dimension_changes", claim="", basis_type="", invalid_refs=[],
                     reason="risk_dimension_changes must be array")
    dimensions: list[str] = []
    for change in changes:
        if not isinstance(change, dict):
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.INVALID_RISK_DIMENSION_SHAPE,
                         hop="risk_dimension_changes", claim="", basis_type="", invalid_refs=[],
                         reason="dimension change must be object")
        dimension = change.get("dimension")
        refs = change.get("evidence_refs")
        reason = str(change.get("reason", "")).strip()
        if dimension not in RISK_DIMENSION_VALUES:
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.UNKNOWN_RISK_DIMENSION,
                         hop="risk_dimension_changes", claim=reason, basis_type="",
                         invalid_refs=[], reason=f"unknown dimension={dimension!r}")
        if dimension in dimensions:
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.DUPLICATE_RISK_DIMENSION,
                         hop="risk_dimension_changes", claim=reason, basis_type="",
                         invalid_refs=[], reason=f"duplicate dimension={dimension!r}")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.INVALID_RISK_DIMENSION_EVIDENCE,
                         hop="risk_dimension_changes", claim=reason, basis_type="",
                         invalid_refs=[], reason="dimension evidence refs required")
        invalid_refs = [ref for ref in refs if registry.resolve(ref) is None]
        if invalid_refs:
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.UNRESOLVED_EVIDENCE_REF,
                         hop="risk_dimension_changes", claim=reason, basis_type="",
                         invalid_refs=invalid_refs, reason="unresolved evidence refs")
        if not reason:
            raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                         code=ScenarioEvidenceErrorCode.MISSING_RISK_DIMENSION_REASON,
                         hop="risk_dimension_changes", claim=reason, basis_type="",
                         invalid_refs=[], reason="dimension reason must be non-empty")
        dimensions.append(dimension)
    if not causal and dimensions:
        raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                     code=ScenarioEvidenceErrorCode.CAUSAL_FALSE_WITH_DIMENSIONS,
                     hop="risk_dimension_changes", claim="", basis_type="", invalid_refs=[],
                     reason="causal=false requires no dimension changes")
    if causal and not dimensions:
        raise _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
                     code=ScenarioEvidenceErrorCode.CAUSAL_TRUE_WITHOUT_DIMENSIONS,
                     hop="risk_dimension_changes", claim="", basis_type="", invalid_refs=[],
                     reason="causal=true requires dimension evidence")
    return dimensions, {"assumption_hop_count": assumption_hops}


def _error(malfunction, scenario, prompt_version, batch, split_path, split_depth,
           *, code, hop, claim, basis_type, invalid_refs, reason):
    message = (
        "Scenario evidence contract violation: "
        f"code={code.value} "
        f"malfunction_id={malfunction.malfunction_id} scenario_id={scenario.scenario_id} "
        f"semantic_fingerprint={scenario.semantic_fingerprint} hop={hop} claim={claim!r} "
        f"basis_type={basis_type!r} invalid_evidence_refs={invalid_refs!r} "
        f"scenario_contract_version={SCENARIO_CONTRACT_VERSION} "
        f"assessment_contract_version={SCENARIO_ASSESSMENT_CONTRACT_VERSION} "
        f"prompt_version={prompt_version} batch={batch} split_path={split_path} "
        f"split_depth={split_depth} reason={reason}"
    )
    return ScenarioEvidenceContractError(
        message, code=code, hop=hop, reason=reason,
        malfunction_id=malfunction.malfunction_id, scenario_id=scenario.scenario_id,
        semantic_fingerprint=scenario.semantic_fingerprint,
        invalid_evidence_refs=invalid_refs, basis_type=basis_type, claim=claim,
    )

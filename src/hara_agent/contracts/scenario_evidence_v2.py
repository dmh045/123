from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hara_agent.models import EvidenceKind

from .causal_mechanism import (
    CausalMechanismApplication, CausalMechanismCatalog,
    CausalMechanismContractError, EvidenceAuthorityError, EvidenceRecordResolver,
    ValidationPolicy, validate_evidence_authority,
)


SCENARIO_EVIDENCE_V2_CONTRACT_VERSION = "scenario-evidence-v2"


class CausalEdgeId(str, Enum):
    M_TO_B = "M_TO_B"
    B_TO_I = "B_TO_I"
    I_TO_H = "I_TO_H"
    H_TO_HARM = "H_TO_HARM"


class CausalBreakpointV2(str, Enum):
    M_TO_B = "M_TO_B"
    B_TO_I = "B_TO_I"
    I_TO_H = "I_TO_H"
    H_TO_HARM = "H_TO_HARM"
    NONE = "NONE"


class ScenarioEvidenceV2ErrorCode(str, Enum):
    BREAKPOINT_MISMATCH = "BREAKPOINT_MISMATCH"
    INVALID_EDGE_SEQUENCE = "INVALID_EDGE_SEQUENCE"
    EMPTY_EDGE_CLAIM = "EMPTY_EDGE_CLAIM"
    MISSING_EDGE_SUPPORT = "MISSING_EDGE_SUPPORT"
    DUPLICATE_SUPPORT_REF = "DUPLICATE_SUPPORT_REF"
    UNRESOLVED_SUPPORT_REF = "UNRESOLVED_SUPPORT_REF"
    SUPPORT_KIND_MISMATCH = "SUPPORT_KIND_MISMATCH"
    UNAPPROVED_EVIDENCE_AUTHORITY = "UNAPPROVED_EVIDENCE_AUTHORITY"
    INVALID_DERIVATION_METADATA = "INVALID_DERIVATION_METADATA"
    MISSING_MECHANISM_APPLICATION = "MISSING_MECHANISM_APPLICATION"
    INVALID_MECHANISM_APPLICATION = "INVALID_MECHANISM_APPLICATION"
    INVALID_RISK_DIMENSION = "INVALID_RISK_DIMENSION"
    DUPLICATE_RISK_DIMENSION = "DUPLICATE_RISK_DIMENSION"
    CAUSAL_FALSE_WITH_DIMENSIONS = "CAUSAL_FALSE_WITH_DIMENSIONS"
    CAUSAL_FALSE_WITH_HAZARD_OUTPUT = "CAUSAL_FALSE_WITH_HAZARD_OUTPUT"
    CAUSAL_TRUE_WITHOUT_DIMENSIONS = "CAUSAL_TRUE_WITHOUT_DIMENSIONS"
    CAUSAL_TRUE_WITHOUT_HAZARD_OUTPUT = "CAUSAL_TRUE_WITHOUT_HAZARD_OUTPUT"


class ScenarioEvidenceV2ContractError(ValueError):
    def __init__(
        self,
        code: ScenarioEvidenceV2ErrorCode,
        message: str,
        *,
        edge_id: str = "",
        evidence_ref: str = "",
        mechanism_code: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.edge_id = edge_id
        self.evidence_ref = evidence_ref
        self.mechanism_code = mechanism_code


@dataclass(frozen=True)
class CausalSupport:
    evidence_ref: str
    support_type: EvidenceKind

    def __post_init__(self) -> None:
        if not self.evidence_ref.strip():
            raise ValueError("CausalSupport.evidence_ref must not be empty")


@dataclass(frozen=True)
class CausalEdgeV2:
    edge_id: CausalEdgeId
    from_stage: str
    to_stage: str
    claim: str
    supports: tuple[CausalSupport, ...]
    mechanism_application: CausalMechanismApplication | None


@dataclass(frozen=True)
class RiskDimensionChangeV2:
    dimension: str
    supports: tuple[CausalSupport, ...]
    reason: str


@dataclass(frozen=True)
class ScenarioEvidenceV2:
    causally_relevant: bool
    breakpoint: CausalBreakpointV2
    edges: tuple[CausalEdgeV2, ...]
    risk_dimension_changes: tuple[RiskDimensionChangeV2, ...]
    hazardous_event: str
    potential_harm: str
    contract_version: str = SCENARIO_EVIDENCE_V2_CONTRACT_VERSION


_EDGE_SEQUENCE = (
    CausalEdgeId.M_TO_B, CausalEdgeId.B_TO_I,
    CausalEdgeId.I_TO_H, CausalEdgeId.H_TO_HARM,
)
_EDGE_STAGES = {
    CausalEdgeId.M_TO_B: ("M", "B"),
    CausalEdgeId.B_TO_I: ("B", "I"),
    CausalEdgeId.I_TO_H: ("I", "H"),
    CausalEdgeId.H_TO_HARM: ("H", "HARM"),
}
# Mirrors the stable atomic-scenario-v1 machine values without importing semantic code.
RISK_DIMENSION_VALUES_V2 = (
    "collision_object", "relative_speed", "distance", "collision_geometry",
    "operating_mode", "driver_intervention", "severity", "exposure",
    "controllability", "ftti", "safe_state",
)
_RISK_DIMENSIONS = frozenset(RISK_DIMENSION_VALUES_V2)


def validate_causal_supports(
    supports: tuple[CausalSupport, ...],
    resolver: EvidenceRecordResolver,
    *,
    policy: ValidationPolicy = ValidationPolicy.STRICT_RELEASE,
    edge_id: str = "",
) -> frozenset[str]:
    refs = [item.evidence_ref for item in supports]
    if len(refs) != len(set(refs)):
        raise ScenarioEvidenceV2ContractError(
            ScenarioEvidenceV2ErrorCode.DUPLICATE_SUPPORT_REF,
            "duplicate evidence_ref in supports", edge_id=edge_id,
        )
    for support in supports:
        record = resolver.resolve_record(support.evidence_ref)
        if record is None:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.UNRESOLVED_SUPPORT_REF,
                f"unresolved support: {support.evidence_ref}",
                edge_id=edge_id, evidence_ref=support.evidence_ref,
            )
        if record.kind is not support.support_type:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.SUPPORT_KIND_MISMATCH,
                f"support declares {support.support_type.value}, record is {record.kind.value}",
                edge_id=edge_id, evidence_ref=support.evidence_ref,
            )
        try:
            validate_evidence_authority(record, policy=policy)
        except EvidenceAuthorityError as error:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.UNAPPROVED_EVIDENCE_AUTHORITY,
                str(error), edge_id=edge_id, evidence_ref=support.evidence_ref,
            ) from error
        if record.kind is EvidenceKind.DERIVED_PHYSICS:
            unresolved_inputs = [
                item for item in record.metadata["inputs"]
                if resolver.resolve_record(item) is None
            ]
            if unresolved_inputs:
                raise ScenarioEvidenceV2ContractError(
                    ScenarioEvidenceV2ErrorCode.INVALID_DERIVATION_METADATA,
                    f"unresolved derivation input: {unresolved_inputs[0]}",
                    edge_id=edge_id, evidence_ref=support.evidence_ref,
                )
    return frozenset(refs)


def validate_scenario_evidence_v2(
    contract: ScenarioEvidenceV2,
    resolver: EvidenceRecordResolver,
    catalog: CausalMechanismCatalog,
    *,
    policy: ValidationPolicy = ValidationPolicy.STRICT_RELEASE,
) -> None:
    if contract.contract_version != SCENARIO_EVIDENCE_V2_CONTRACT_VERSION:
        raise ValueError(f"unsupported v2 contract version: {contract.contract_version}")
    if contract.causally_relevant != (contract.breakpoint is CausalBreakpointV2.NONE):
        raise ScenarioEvidenceV2ContractError(
            ScenarioEvidenceV2ErrorCode.BREAKPOINT_MISMATCH,
            "causally_relevant and breakpoint are inconsistent",
        )
    edges = {edge.edge_id: edge for edge in contract.edges}
    if len(edges) != len(contract.edges):
        raise ScenarioEvidenceV2ContractError(
            ScenarioEvidenceV2ErrorCode.INVALID_EDGE_SEQUENCE, "duplicate causal edge id"
        )
    if contract.causally_relevant:
        required_edges = _EDGE_SEQUENCE
    else:
        breakpoint_index = _EDGE_SEQUENCE.index(CausalEdgeId(contract.breakpoint.value))
        required_edges = _EDGE_SEQUENCE[:breakpoint_index]
    if any(edge_id not in edges for edge_id in required_edges):
        raise ScenarioEvidenceV2ContractError(
            ScenarioEvidenceV2ErrorCode.INVALID_EDGE_SEQUENCE,
            "required causal edge prefix is incomplete",
        )
    for edge_id in required_edges:
        edge = edges[edge_id]
        if (edge.from_stage, edge.to_stage) != _EDGE_STAGES[edge_id]:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.INVALID_EDGE_SEQUENCE,
                f"invalid stage transition for {edge_id.value}", edge_id=edge_id.value,
            )
        if not edge.claim.strip():
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.EMPTY_EDGE_CLAIM,
                "required causal edge claim is empty", edge_id=edge_id.value,
            )
        if not edge.supports:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.MISSING_EDGE_SUPPORT,
                "required causal edge has no supports", edge_id=edge_id.value,
            )
        support_refs = validate_causal_supports(
            edge.supports, resolver, policy=policy, edge_id=edge_id.value
        )
        if edge.mechanism_application is None:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.MISSING_MECHANISM_APPLICATION,
                "required causal edge has no mechanism application", edge_id=edge_id.value,
            )
        try:
            catalog.validate_application(
                edge.mechanism_application, resolver,
                support_refs=support_refs, policy=policy,
            )
        except CausalMechanismContractError as error:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.INVALID_MECHANISM_APPLICATION,
                str(error), edge_id=edge_id.value,
                evidence_ref=error.evidence_ref, mechanism_code=error.code.value,
            ) from error
    _validate_risk_dimensions(contract, resolver, policy)


def _validate_risk_dimensions(
    contract: ScenarioEvidenceV2,
    resolver: EvidenceRecordResolver,
    policy: ValidationPolicy,
) -> None:
    dimensions = [item.dimension for item in contract.risk_dimension_changes]
    if len(dimensions) != len(set(dimensions)):
        raise ScenarioEvidenceV2ContractError(
            ScenarioEvidenceV2ErrorCode.DUPLICATE_RISK_DIMENSION,
            "risk dimensions must be unique",
        )
    for change in contract.risk_dimension_changes:
        if change.dimension not in _RISK_DIMENSIONS or not change.reason.strip():
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.INVALID_RISK_DIMENSION,
                f"invalid risk dimension: {change.dimension}",
            )
        if not change.supports:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.INVALID_RISK_DIMENSION,
                f"risk dimension {change.dimension} requires supports",
            )
        validate_causal_supports(
            change.supports, resolver, policy=policy,
            edge_id=f"RISK.{change.dimension}",
        )
    if not contract.causally_relevant:
        if contract.risk_dimension_changes:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.CAUSAL_FALSE_WITH_DIMENSIONS,
                "causal=false requires no risk dimension changes",
            )
        if contract.hazardous_event or contract.potential_harm:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.CAUSAL_FALSE_WITH_HAZARD_OUTPUT,
                "causal=false requires empty hazard outputs",
            )
    else:
        if not contract.risk_dimension_changes:
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.CAUSAL_TRUE_WITHOUT_DIMENSIONS,
                "causal=true requires risk dimension changes",
            )
        if not contract.hazardous_event.strip() or not contract.potential_harm.strip():
            raise ScenarioEvidenceV2ContractError(
                ScenarioEvidenceV2ErrorCode.CAUSAL_TRUE_WITHOUT_HAZARD_OUTPUT,
                "causal=true requires hazardous_event and potential_harm",
            )

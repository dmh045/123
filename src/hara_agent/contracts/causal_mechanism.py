from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, ReviewStatus, SourceRef,
)


class EvidenceRecordResolver(Protocol):
    def resolve_record(self, evidence_ref: str) -> EvidenceRecord | None: ...


class ValidationPolicy(str, Enum):
    STRICT_RELEASE = "STRICT_RELEASE"
    MIGRATION_EVALUATION = "MIGRATION_EVALUATION"


class CausalMechanismErrorCode(str, Enum):
    UNKNOWN_MECHANISM = "UNKNOWN_MECHANISM"
    MECHANISM_VERSION_MISMATCH = "MECHANISM_VERSION_MISMATCH"
    UNAPPROVED_MECHANISM = "UNAPPROVED_MECHANISM"
    MISSING_REQUIRED_PREMISE = "MISSING_REQUIRED_PREMISE"
    UNKNOWN_PREMISE_BINDING = "UNKNOWN_PREMISE_BINDING"
    UNRESOLVED_PREMISE_REF = "UNRESOLVED_PREMISE_REF"
    PREMISE_KIND_MISMATCH = "PREMISE_KIND_MISMATCH"
    INVALID_PREMISE_AUTHORITY = "INVALID_PREMISE_AUTHORITY"
    BINDING_NOT_SUPPORTED = "BINDING_NOT_SUPPORTED"


class CausalMechanismContractError(ValueError):
    def __init__(
        self,
        code: CausalMechanismErrorCode,
        message: str,
        *,
        mechanism_id: str = "",
        premise_id: str = "",
        evidence_ref: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.mechanism_id = mechanism_id
        self.premise_id = premise_id
        self.evidence_ref = evidence_ref


@dataclass(frozen=True)
class CausalMechanismPremise:
    premise_id: str
    expected_kind: EvidenceKind
    required: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not self.premise_id.strip():
            raise ValueError("mechanism premise_id must not be empty")


@dataclass(frozen=True)
class CausalMechanismDefinition:
    mechanism_id: str
    version: str
    description: str
    premises: tuple[CausalMechanismPremise, ...]
    result_state: str
    source_refs: tuple[SourceRef, ...]
    approval_status: ReviewStatus
    provenance: FactProvenance
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.mechanism_id.strip() or not self.version.strip():
            raise ValueError("mechanism_id and version must not be empty")
        if not self.description.strip() or not self.result_state.strip():
            raise ValueError("mechanism description and result_state must not be empty")
        premise_ids = [item.premise_id for item in self.premises]
        if len(premise_ids) != len(set(premise_ids)):
            raise ValueError("mechanism premise_id values must be unique")
        if not self.source_refs:
            raise ValueError("mechanism definition requires SourceRef")


@dataclass(frozen=True)
class CausalMechanismApplication:
    mechanism_id: str
    mechanism_version: str
    bindings: dict[str, str]

    def __post_init__(self) -> None:
        if not self.mechanism_id.strip() or not self.mechanism_version.strip():
            raise ValueError("mechanism application requires id and version")
        if any(not key.strip() or not value.strip() for key, value in self.bindings.items()):
            raise ValueError("mechanism bindings require non-empty premise and evidence refs")


class CausalMechanismCatalog(Protocol):
    def list_definitions(self) -> tuple[CausalMechanismDefinition, ...]: ...

    def get(self, mechanism_id: str, version: str) -> CausalMechanismDefinition | None: ...

    def validate_application(
        self,
        application: CausalMechanismApplication,
        resolver: EvidenceRecordResolver,
        *,
        support_refs: frozenset[str],
        policy: ValidationPolicy = ValidationPolicy.STRICT_RELEASE,
    ) -> CausalMechanismDefinition: ...


class InMemoryCausalMechanismCatalog:
    """Deterministic catalog for contract fixtures; no production rules are bundled."""

    def __init__(self, definitions: tuple[CausalMechanismDefinition, ...] = ()):
        self._definitions: dict[tuple[str, str], CausalMechanismDefinition] = {}
        for definition in definitions:
            key = (definition.mechanism_id, definition.version)
            if key in self._definitions:
                raise ValueError(f"duplicate mechanism definition rejected: {key}")
            self._definitions[key] = definition

    def get(self, mechanism_id: str, version: str) -> CausalMechanismDefinition | None:
        return self._definitions.get((mechanism_id, version))

    def list_definitions(self) -> tuple[CausalMechanismDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def validate_application(
        self,
        application: CausalMechanismApplication,
        resolver: EvidenceRecordResolver,
        *,
        support_refs: frozenset[str],
        policy: ValidationPolicy = ValidationPolicy.STRICT_RELEASE,
    ) -> CausalMechanismDefinition:
        versions = {
            version for mechanism_id, version in self._definitions
            if mechanism_id == application.mechanism_id
        }
        definition = self.get(application.mechanism_id, application.mechanism_version)
        if definition is None:
            code = (
                CausalMechanismErrorCode.MECHANISM_VERSION_MISMATCH
                if versions else CausalMechanismErrorCode.UNKNOWN_MECHANISM
            )
            raise CausalMechanismContractError(
                code, f"mechanism unavailable: {application.mechanism_id}@{application.mechanism_version}",
                mechanism_id=application.mechanism_id,
            )
        if (
            definition.approval_status is not ReviewStatus.FINALIZED
            or definition.provenance is not FactProvenance.DOMAIN_POLICY
        ):
            raise CausalMechanismContractError(
                CausalMechanismErrorCode.UNAPPROVED_MECHANISM,
                "positive causal edges require a FINALIZED DOMAIN_POLICY mechanism",
                mechanism_id=definition.mechanism_id,
            )
        premises = {item.premise_id: item for item in definition.premises}
        unknown = sorted(set(application.bindings) - set(premises))
        if unknown:
            raise CausalMechanismContractError(
                CausalMechanismErrorCode.UNKNOWN_PREMISE_BINDING,
                f"unknown premise binding: {unknown[0]}",
                mechanism_id=definition.mechanism_id, premise_id=unknown[0],
            )
        missing = sorted(
            item.premise_id for item in definition.premises
            if item.required and item.premise_id not in application.bindings
        )
        if missing:
            raise CausalMechanismContractError(
                CausalMechanismErrorCode.MISSING_REQUIRED_PREMISE,
                f"missing required premise: {missing[0]}",
                mechanism_id=definition.mechanism_id, premise_id=missing[0],
            )
        for premise_id, evidence_ref in application.bindings.items():
            if evidence_ref not in support_refs:
                raise CausalMechanismContractError(
                    CausalMechanismErrorCode.BINDING_NOT_SUPPORTED,
                    f"binding evidence is not declared in edge supports: {evidence_ref}",
                    mechanism_id=definition.mechanism_id,
                    premise_id=premise_id, evidence_ref=evidence_ref,
                )
            record = resolver.resolve_record(evidence_ref)
            if record is None:
                raise CausalMechanismContractError(
                    CausalMechanismErrorCode.UNRESOLVED_PREMISE_REF,
                    f"unresolved premise evidence: {evidence_ref}",
                    mechanism_id=definition.mechanism_id,
                    premise_id=premise_id, evidence_ref=evidence_ref,
                )
            premise = premises[premise_id]
            if record.kind is not premise.expected_kind:
                raise CausalMechanismContractError(
                    CausalMechanismErrorCode.PREMISE_KIND_MISMATCH,
                    f"premise {premise_id} requires {premise.expected_kind.value}",
                    mechanism_id=definition.mechanism_id,
                    premise_id=premise_id, evidence_ref=evidence_ref,
                )
            try:
                validate_evidence_authority(record, policy=policy)
            except EvidenceAuthorityError as error:
                raise CausalMechanismContractError(
                    CausalMechanismErrorCode.INVALID_PREMISE_AUTHORITY,
                    str(error), mechanism_id=definition.mechanism_id,
                    premise_id=premise_id, evidence_ref=evidence_ref,
                ) from error
        return definition


class EvidenceAuthorityError(ValueError):
    pass


def validate_evidence_authority(
    record: EvidenceRecord,
    *,
    policy: ValidationPolicy = ValidationPolicy.STRICT_RELEASE,
) -> None:
    if record.kind is EvidenceKind.ASSUMPTION:
        if policy is ValidationPolicy.MIGRATION_EVALUATION:
            return
        raise EvidenceAuthorityError("ASSUMPTION is forbidden in STRICT_RELEASE")
    if (
        policy is ValidationPolicy.STRICT_RELEASE
        and record.provenance in {
        FactProvenance.LEGACY_MIGRATION, FactProvenance.LLM_INFERENCE,
        }
    ):
        raise EvidenceAuthorityError(
            f"{record.provenance.value} is not authoritative release evidence"
        )
    if record.kind is EvidenceKind.DIRECT_FACT:
        if (
            policy is ValidationPolicy.STRICT_RELEASE
            and record.provenance not in {
                FactProvenance.PROJECT_INPUT, FactProvenance.METHOD_CONTRACT,
            }
        ):
            raise EvidenceAuthorityError("DIRECT_FACT requires project or method authority")
        if not record.source_refs:
            raise EvidenceAuthorityError("authoritative DIRECT_FACT requires SourceRef")
        return
    if record.kind is EvidenceKind.DERIVED_PHYSICS:
        inputs = record.metadata.get("inputs")
        if (
            record.provenance is not FactProvenance.DERIVED
            or not str(record.metadata.get("derivation_type", "")).strip()
            or not isinstance(inputs, list)
            or not inputs
            or any(not isinstance(item, str) or not item for item in inputs)
        ):
            raise EvidenceAuthorityError("DERIVED_PHYSICS requires canonical derivation metadata")
        return
    if record.kind is EvidenceKind.APPROVED_RULE:
        if (
            record.provenance is not FactProvenance.DOMAIN_POLICY
            or record.approval_status is not ReviewStatus.FINALIZED
        ):
            raise EvidenceAuthorityError("APPROVED_RULE requires FINALIZED DOMAIN_POLICY")
        return
    raise EvidenceAuthorityError(f"unsupported evidence authority for {record.kind.value}")

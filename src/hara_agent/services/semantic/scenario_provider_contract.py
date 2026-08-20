from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hara_agent.contracts import (
    SCENARIO_EVIDENCE_V2_CONTRACT_VERSION,
    CausalBreakpointV2,
    CausalEdgeId,
    CausalEdgeV2,
    CausalMechanismApplication,
    CausalMechanismCatalog,
    CausalMechanismDefinition,
    CausalSupport,
    RiskDimensionChangeV2,
    ScenarioEvidenceV2,
    ValidationPolicy,
    validate_scenario_evidence_v2,
)
from hara_agent.models import (
    EvidenceKind, MalfunctionCandidate, ReviewStatus, ScenarioCandidate,
    ScenarioFeasibilityAssessment,
)

from .parsing import parse_confidence
from .scenario_evidence import FactRegistry
from .scenario_provider_schema import (
    ScenarioFeasibilityAssessmentV2ListProvider,
    ScenarioFeasibilityAssessmentV2Provider,
    provider_field_names,
    scenario_v2_provider_schema_fingerprint,
)


SCENARIO_V1_PROMPT_VERSION = "scenario-feasibility-v9"
SCENARIO_V1_ASSESSMENT_CONTRACT = "scenario-evidence-v1"
SCENARIO_V1_SCHEMA_NAME = "ScenarioFeasibilityAssessmentList"
SCENARIO_V2_PROMPT_VERSION = "scenario-feasibility-v10"
SCENARIO_V2_SCHEMA_NAME = "ScenarioFeasibilityAssessmentV2List"


class ScenarioContractMode(str, Enum):
    V1 = "v1"
    V2 = "v2"


class ScenarioProviderErrorCode(str, Enum):
    INVALID_V2_ENVELOPE = "INVALID_V2_ENVELOPE"
    INVALID_V2_ASSESSMENT_SHAPE = "INVALID_V2_ASSESSMENT_SHAPE"
    INVALID_V2_EDGE_SHAPE = "INVALID_V2_EDGE_SHAPE"
    INVALID_SUPPORT_SHAPE = "INVALID_SUPPORT_SHAPE"
    INVALID_MECHANISM_APPLICATION_SHAPE = "INVALID_MECHANISM_APPLICATION_SHAPE"
    INVALID_RISK_DIMENSION_SHAPE = "INVALID_RISK_DIMENSION_SHAPE"
    V1_FIELD_IN_V2_PAYLOAD = "V1_FIELD_IN_V2_PAYLOAD"
    V2_FIELD_IN_V1_PAYLOAD = "V2_FIELD_IN_V1_PAYLOAD"
    MISSING_EDGE = "MISSING_EDGE"
    DUPLICATE_EDGE = "DUPLICATE_EDGE"
    MISSING_ASSESSMENT = "MISSING_ASSESSMENT"
    DUPLICATE_ASSESSMENT = "DUPLICATE_ASSESSMENT"
    UNKNOWN_SCENARIO_ID = "UNKNOWN_SCENARIO_ID"


class ScenarioProviderContractError(ValueError):
    def __init__(
        self,
        code: ScenarioProviderErrorCode,
        message: str,
        *,
        scenario_id: str = "",
        field: str = "",
    ):
        super().__init__(message)
        self.code = code
        self.scenario_id = scenario_id
        self.field = field


def mechanism_catalog_payload(
    definitions: tuple[CausalMechanismDefinition, ...],
) -> list[dict[str, Any]]:
    return [
        {
            "mechanism_id": definition.mechanism_id,
            "version": definition.version,
            "description": definition.description,
            "premises": [
                {
                    "premise_id": premise.premise_id,
                    "expected_kind": premise.expected_kind.value,
                    "required": premise.required,
                    "description": premise.description,
                }
                for premise in definition.premises
            ],
            "result_state": definition.result_state,
        }
        for definition in sorted(
            definitions, key=lambda item: (item.mechanism_id, item.version)
        )
    ]


def mechanism_catalog_fingerprint(
    definitions: tuple[CausalMechanismDefinition, ...],
) -> str:
    material = [
        {
            "mechanism_id": definition.mechanism_id,
            "version": definition.version,
            "premises": [
                {
                    "premise_id": premise.premise_id,
                    "expected_kind": premise.expected_kind.value,
                    "required": premise.required,
                }
                for premise in definition.premises
            ],
            "approval_status": definition.approval_status.value,
            "provenance": definition.provenance.value,
        }
        for definition in sorted(
            definitions, key=lambda item: (item.mechanism_id, item.version)
        )
    ]
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ScenarioContractRuntimeConfig:
    mode: ScenarioContractMode
    prompt_version: str
    assessment_contract_version: str
    schema_name: str
    mechanism_catalog_fingerprint: str
    provider_schema_fingerprint: str

    @classmethod
    def create(
        cls,
        mode: ScenarioContractMode | str = ScenarioContractMode.V1,
        *,
        mechanism_definitions: tuple[CausalMechanismDefinition, ...] = (),
    ) -> "ScenarioContractRuntimeConfig":
        actual = ScenarioContractMode(mode)
        catalog_fingerprint = mechanism_catalog_fingerprint(
            mechanism_definitions if actual is ScenarioContractMode.V2 else ()
        )
        if actual is ScenarioContractMode.V1:
            return cls(
                actual, SCENARIO_V1_PROMPT_VERSION,
                SCENARIO_V1_ASSESSMENT_CONTRACT, SCENARIO_V1_SCHEMA_NAME,
                catalog_fingerprint, "",
            )
        return cls(
            actual, SCENARIO_V2_PROMPT_VERSION,
            SCENARIO_EVIDENCE_V2_CONTRACT_VERSION, SCENARIO_V2_SCHEMA_NAME,
            catalog_fingerprint, scenario_v2_provider_schema_fingerprint(),
        )

    @property
    def cache_fingerprint(self) -> str:
        encoded = json.dumps(
            {
                "prompt_version": self.prompt_version,
                "assessment_contract_version": self.assessment_contract_version,
                "mechanism_catalog_fingerprint": self.mechanism_catalog_fingerprint,
                "provider_schema_fingerprint": self.provider_schema_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def v2_registry_prompt_snapshot(registry: FactRegistry) -> dict[str, dict[str, Any]]:
    return {
        record.evidence_ref: {
            "value": record.value,
            "kind": record.kind.value,
            "provenance": record.provenance.value,
            "approval_status": record.approval_status.value,
            "metadata": dict(record.metadata),
        }
        for record in registry.records
    }


def validate_v2_envelope(
    payload: Any,
    expected_scenario_ids: list[str],
) -> list[dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or set(payload) != provider_field_names(ScenarioFeasibilityAssessmentV2ListProvider)
        or not isinstance(payload.get("assessments"), list)
    ):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_ENVELOPE,
            "v2 provider output must be {'assessments': [...]}",
        )
    assessments = payload["assessments"]
    if any(not isinstance(item, dict) for item in assessments):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE,
            "every v2 assessment must be a JSON object",
        )
    actual = [str(item.get("scenario_id", "")).strip() for item in assessments]
    duplicates = sorted({item for item in actual if actual.count(item) > 1})
    if duplicates:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.DUPLICATE_ASSESSMENT,
            f"duplicate scenario assessments: {duplicates}", scenario_id=duplicates[0],
        )
    unknown = sorted(set(actual) - set(expected_scenario_ids))
    if unknown:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.UNKNOWN_SCENARIO_ID,
            f"unknown scenario assessments: {unknown}", scenario_id=unknown[0],
        )
    missing = sorted(set(expected_scenario_ids) - set(actual))
    if missing:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.MISSING_ASSESSMENT,
            f"missing scenario assessments: {missing}", scenario_id=missing[0],
        )
    return assessments


def reject_v2_fields_in_v1_payload(item: dict[str, Any]) -> None:
    if _contains_v2_fields(item):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.V2_FIELD_IN_V1_PAYLOAD,
            "v1 payload must not contain v2 edges/supports",
            scenario_id=str(item.get("scenario_id", "")),
        )


def _contains_v2_fields(value: Any) -> bool:
    if isinstance(value, dict):
        if any(
            key in value
            for key in ("edges", "supports", "support_type", "mechanism_application")
        ):
            return True
        return any(_contains_v2_fields(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_v2_fields(item) for item in value)
    return False


def parse_v2_assessment(
    item: Any,
    *,
    malfunction: MalfunctionCandidate,
    scenario: ScenarioCandidate | None,
    registry: FactRegistry,
    catalog: CausalMechanismCatalog,
) -> ScenarioFeasibilityAssessment:
    if not isinstance(item, dict):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE,
            "v2 assessment must be an object",
        )
    if _contains_v1_fields(item):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.V1_FIELD_IN_V2_PAYLOAD,
            "v2 payload contains basis_type/evidence_refs/causal_chain",
            scenario_id=str(item.get("scenario_id", "")),
        )
    required_fields = provider_field_names(ScenarioFeasibilityAssessmentV2Provider)
    if set(item) != required_fields:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE,
            "v2 assessment fields do not match the contract",
            scenario_id=str(item.get("scenario_id", "")),
        )
    scenario_id = str(item.get("scenario_id", "")).strip()
    if not isinstance(item["scenario_id"], str) or not scenario_id:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE,
            "v2 scenario_id must be a non-empty string", field="scenario_id",
        )
    if scenario is None:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.UNKNOWN_SCENARIO_ID,
            f"unknown scenario_id={scenario_id!r}", scenario_id=scenario_id,
        )
    boolean_fields = ("physically_feasible", "functionally_relevant", "causally_relevant")
    if any(not isinstance(item.get(field), bool) for field in boolean_fields):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE,
            "v2 feasibility fields must be JSON boolean", scenario_id=scenario_id,
        )
    string_fields = ("rationale", "hazardous_event", "potential_harm", "status")
    if any(not isinstance(item[field], str) for field in string_fields):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE,
            "v2 rationale, hazard outputs and status must be strings",
            scenario_id=scenario_id,
        )
    if item["status"].upper() not in {"PENDING", "FINALIZED"}:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE,
            "v2 status must be PENDING or FINALIZED",
            scenario_id=scenario_id, field="status",
        )
    try:
        breakpoint = CausalBreakpointV2(item.get("breakpoint"))
    except (TypeError, ValueError) as error:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE,
            "invalid v2 breakpoint", scenario_id=scenario_id, field="breakpoint",
        ) from error
    raw_edges = item.get("edges")
    if not isinstance(raw_edges, list):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_EDGE_SHAPE,
            "v2 edges must be an array", scenario_id=scenario_id, field="edges",
        )
    edges = tuple(_parse_edge(value, scenario_id) for value in raw_edges)
    edge_ids = [edge.edge_id for edge in edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.DUPLICATE_EDGE,
            "duplicate v2 causal edge", scenario_id=scenario_id, field="edges",
        )
    required = _required_edges(item["causally_relevant"], breakpoint)
    missing_edges = [edge.value for edge in required if edge not in edge_ids]
    if missing_edges:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.MISSING_EDGE,
            f"missing required v2 edges: {missing_edges}",
            scenario_id=scenario_id, field="edges",
        )
    if edge_ids != list(required):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_EDGE_SHAPE,
            "v2 edges must be exactly the ordered required prefix",
            scenario_id=scenario_id, field="edges",
        )
    raw_changes = item.get("risk_dimension_changes")
    if not isinstance(raw_changes, list):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_RISK_DIMENSION_SHAPE,
            "v2 risk_dimension_changes must be an array",
            scenario_id=scenario_id, field="risk_dimension_changes",
        )
    changes = tuple(_parse_risk_change(value, scenario_id) for value in raw_changes)
    contract = ScenarioEvidenceV2(
        causally_relevant=item["causally_relevant"],
        breakpoint=breakpoint,
        edges=edges,
        risk_dimension_changes=changes,
        hazardous_event=str(item.get("hazardous_event", "")).strip(),
        potential_harm=str(item.get("potential_harm", "")).strip(),
    )
    validate_scenario_evidence_v2(
        contract, registry, catalog, policy=ValidationPolicy.STRICT_RELEASE
    )
    status = (
        ReviewStatus.FINALIZED
        if str(item.get("status", "")).upper() == "FINALIZED"
        else ReviewStatus.PENDING
    )
    return ScenarioFeasibilityAssessment(
        malfunction_id=malfunction.malfunction_id,
        scenario_id=scenario_id,
        physically_feasible=item["physically_feasible"],
        functionally_relevant=item["functionally_relevant"],
        causally_relevant=item["causally_relevant"],
        risk_dimensions_changed=[change.dimension for change in changes],
        rationale=str(item.get("rationale", "")).strip(),
        hazardous_event=contract.hazardous_event,
        potential_harm=contract.potential_harm,
        status=status,
        confidence=parse_confidence(
            item.get("confidence"),
            field_name=f"Scenario v10 confidence validation failed: scenario={scenario_id}",
        ),
        breakpoint=breakpoint.value,
        causal_chain={"edges": [_edge_to_dict(edge) for edge in edges]},
        risk_dimension_changes=[_risk_change_to_dict(change) for change in changes],
        evidence_contract_version=SCENARIO_EVIDENCE_V2_CONTRACT_VERSION,
    )


def _contains_v1_fields(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in value for key in ("causal_chain", "basis_type", "evidence_refs")):
            return True
        return any(_contains_v1_fields(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_v1_fields(item) for item in value)
    return False


def _parse_support(value: Any, scenario_id: str) -> CausalSupport:
    if (
        not isinstance(value, dict)
        or set(value) != provider_field_names(CausalSupport)
        or not isinstance(value.get("evidence_ref"), str)
        or not isinstance(value.get("support_type"), str)
    ):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_SUPPORT_SHAPE,
            "support requires exactly evidence_ref and support_type",
            scenario_id=scenario_id, field="supports",
        )
    try:
        return CausalSupport(
            str(value["evidence_ref"]).strip(), EvidenceKind(value["support_type"])
        )
    except (TypeError, ValueError) as error:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_SUPPORT_SHAPE,
            "invalid support evidence_ref or support_type",
            scenario_id=scenario_id, field="supports",
        ) from error


def _parse_edge(value: Any, scenario_id: str) -> CausalEdgeV2:
    required = provider_field_names(CausalEdgeV2)
    if (
        not isinstance(value, dict)
        or set(value) != required
        or any(not isinstance(value.get(field), str)
               for field in ("edge_id", "from_stage", "to_stage", "claim"))
    ):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_EDGE_SHAPE,
            "v2 edge fields do not match the contract",
            scenario_id=scenario_id, field="edges",
        )
    if not isinstance(value["supports"], list):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_SUPPORT_SHAPE,
            "edge supports must be an array", scenario_id=scenario_id, field="supports",
        )
    supports = tuple(_parse_support(item, scenario_id) for item in value["supports"])
    application = _parse_application(value["mechanism_application"], scenario_id)
    try:
        edge_id = CausalEdgeId(value["edge_id"])
    except (TypeError, ValueError) as error:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_V2_EDGE_SHAPE,
            "invalid v2 edge_id", scenario_id=scenario_id, field="edge_id",
        ) from error
    return CausalEdgeV2(
        edge_id, str(value["from_stage"]), str(value["to_stage"]),
        str(value["claim"]), supports, application,
    )


def _parse_application(
    value: Any, scenario_id: str,
) -> CausalMechanismApplication | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != provider_field_names(CausalMechanismApplication)
        or not isinstance(value.get("mechanism_id"), str)
        or not isinstance(value.get("mechanism_version"), str)
        or not isinstance(value.get("bindings"), dict)
        or any(not isinstance(key, str) or not key.strip()
               or not isinstance(ref, str) or not ref.strip()
               for key, ref in value.get("bindings", {}).items())
    ):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_MECHANISM_APPLICATION_SHAPE,
            "invalid v2 mechanism_application shape",
            scenario_id=scenario_id, field="mechanism_application",
        )
    try:
        return CausalMechanismApplication(
            str(value["mechanism_id"]), str(value["mechanism_version"]),
            dict(value["bindings"]),
        )
    except ValueError as error:
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_MECHANISM_APPLICATION_SHAPE,
            str(error), scenario_id=scenario_id, field="mechanism_application",
        ) from error


def _parse_risk_change(value: Any, scenario_id: str) -> RiskDimensionChangeV2:
    if (
        not isinstance(value, dict)
        or set(value) != provider_field_names(RiskDimensionChangeV2)
        or not isinstance(value.get("supports"), list)
        or not isinstance(value.get("dimension"), str)
        or not isinstance(value.get("reason"), str)
    ):
        raise ScenarioProviderContractError(
            ScenarioProviderErrorCode.INVALID_RISK_DIMENSION_SHAPE,
            "risk dimension change requires dimension, supports and reason",
            scenario_id=scenario_id, field="risk_dimension_changes",
        )
    return RiskDimensionChangeV2(
        str(value["dimension"]),
        tuple(_parse_support(item, scenario_id) for item in value["supports"]),
        str(value["reason"]),
    )


def _required_edges(
    causal: bool, breakpoint: CausalBreakpointV2,
) -> tuple[CausalEdgeId, ...]:
    sequence = tuple(CausalEdgeId)
    if causal:
        return sequence
    if breakpoint is CausalBreakpointV2.NONE:
        return ()
    return sequence[:sequence.index(CausalEdgeId(breakpoint.value))]


def _edge_to_dict(edge: CausalEdgeV2) -> dict[str, Any]:
    application = edge.mechanism_application
    return {
        "edge_id": edge.edge_id.value,
        "from_stage": edge.from_stage,
        "to_stage": edge.to_stage,
        "claim": edge.claim,
        "supports": [
            {"evidence_ref": item.evidence_ref, "support_type": item.support_type.value}
            for item in edge.supports
        ],
        "mechanism_application": (
            {
                "mechanism_id": application.mechanism_id,
                "mechanism_version": application.mechanism_version,
                "bindings": dict(application.bindings),
            }
            if application else None
        ),
    }


def _risk_change_to_dict(change: RiskDimensionChangeV2) -> dict[str, Any]:
    return {
        "dimension": change.dimension,
        "supports": [
            {"evidence_ref": item.evidence_ref, "support_type": item.support_type.value}
            for item in change.supports
        ],
        "reason": change.reason,
    }

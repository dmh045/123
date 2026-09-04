from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from hara_agent.models.common import ReviewStatus

from .method_contract import SourceRef


RISK_CALCULATION_CONTRACT_VERSION = "risk-calculation-contract-v1"


def _to_dict(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: _to_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_dict(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_dict(item) for item in value]
    return value


class CalculationStatus(str, Enum):
    FINALIZED = "FINALIZED"
    PENDING_INPUT = "PENDING_INPUT"
    PENDING_METHOD_SEMANTICS = "PENDING_METHOD_SEMANTICS"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RiskFactResolutionStatus(str, Enum):
    FOUND = "FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ABSENT_IN_EVIDENCE = "ABSENT_IN_EVIDENCE"
    UNRESOLVED_DEPENDENCY = "UNRESOLVED_DEPENDENCY"
    UNRESOLVED_METHOD_SEMANTICS = "UNRESOLVED_METHOD_SEMANTICS"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


@dataclass(frozen=True)
class RiskFactResolution:
    malfunction_id: str
    scenario_id: str
    fact_type: str
    status: RiskFactResolutionStatus
    reason: str
    fact_id: str = ""

    def __post_init__(self) -> None:
        if not self.malfunction_id or not self.scenario_id or not self.fact_type:
            raise ValueError("RiskFactResolution requires pair and fact identities")
        if not self.reason.strip():
            raise ValueError("RiskFactResolution requires a reason")
        if self.status is RiskFactResolutionStatus.FOUND and not self.fact_id:
            raise ValueError("FOUND RiskFactResolution requires fact_id")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


class ExposureCombinationRelation(str, Enum):
    INDEPENDENT = "INDEPENDENT"
    DEPENDENT = "DEPENDENT"
    CONDITIONAL = "CONDITIONAL"
    NOT_COMBINABLE = "NOT_COMBINABLE"


class ExposureMethodDomain(str, Enum):
    TIME = "Z"
    FREQUENCY = "F"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class SeverityMethodBand:
    rule_id: str
    collision_group: str
    collision_type: str
    result: str
    source_ref: SourceRef
    lower_kph: float | None = None
    upper_kph: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = False

    def __post_init__(self) -> None:
        if not self.rule_id or self.result not in {"S0", "S1", "S2", "S3"}:
            raise ValueError("SeverityMethodBand requires identity and S0-S3 result")
        if self.lower_kph is None and self.upper_kph is None:
            raise ValueError("SeverityMethodBand requires at least one speed boundary")


@dataclass(frozen=True)
class SeverityMethod:
    method_id: str
    speed_semantic: SpeedSemantic
    bands: tuple[SeverityMethodBand, ...]
    source_ref: SourceRef
    road_user_groups: tuple[tuple[str, str], ...] = ()
    collision_types: tuple[tuple[str, str], ...] = ()
    fallback_policy: str = "PENDING_INPUT"

    def __post_init__(self) -> None:
        if not self.method_id or not self.bands:
            raise ValueError("SeverityMethod requires identity and bands")
        if self.speed_semantic is SpeedSemantic.UNRESOLVED:
            raise ValueError("SeverityMethod speed semantic must be explicit")


@dataclass(frozen=True)
class ExposureAtom:
    atom_id: str
    dimensions: tuple[str, ...]
    label: str
    duration_level: str
    frequency_level: str
    source_ref: SourceRef

    def __post_init__(self) -> None:
        if not self.atom_id or not self.dimensions or not self.label:
            raise ValueError("ExposureAtom requires identity, dimension and label")


@dataclass(frozen=True)
class ExposureDomainRule:
    rule_id: str
    component_categories: tuple[str, ...]
    domain: ExposureMethodDomain
    source_ref: SourceRef

    def __post_init__(self) -> None:
        if not self.rule_id or not self.component_categories:
            raise ValueError("ExposureDomainRule requires identity and categories")
        if self.domain is ExposureMethodDomain.UNRESOLVED:
            raise ValueError("ExposureDomainRule requires Z or F")


@dataclass(frozen=True)
class ExposureAggregationPolicy:
    policy_id: str
    minimum_level: int
    all_highest_operand: str
    all_highest_result: str
    mixed_high_operands: tuple[str, ...]
    mixed_high_result: str
    mixed_strategy: str
    independent_decrement: int
    dependent_decrement: int
    source_ref: SourceRef

    def __post_init__(self) -> None:
        if not self.policy_id or self.mixed_strategy not in {"MINIMUM"}:
            raise ValueError("Unsupported Exposure aggregation policy")
        if self.minimum_level < 0:
            raise ValueError("Exposure minimum level cannot be negative")


@dataclass(frozen=True)
class ExposureMethod:
    method_id: str
    atoms: tuple[ExposureAtom, ...]
    domain_rules: tuple[ExposureDomainRule, ...]
    strong_couplings: tuple[tuple[str, str], ...]
    aggregation_policy: ExposureAggregationPolicy
    dimension_fallback_policy: str
    source_refs: tuple[SourceRef, ...]

    def __post_init__(self) -> None:
        if not self.method_id or not self.atoms or not self.domain_rules:
            raise ValueError("ExposureMethod requires identity, atoms and domain rules")


@dataclass(frozen=True)
class ControllabilityCondition:
    field: str
    expected: bool

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError("ControllabilityCondition requires a field")


@dataclass(frozen=True)
class ControllabilityOverride:
    rule_id: str
    any_of: tuple[ControllabilityCondition, ...]
    all_of: tuple[ControllabilityCondition, ...]
    result: str
    priority: int
    source_ref: SourceRef

    def __post_init__(self) -> None:
        if not self.rule_id or self.result not in {"C0", "C1", "C2", "C3"}:
            raise ValueError("ControllabilityOverride requires identity and C0-C3 result")
        if not self.any_of and not self.all_of:
            raise ValueError("ControllabilityOverride requires conditions")


@dataclass(frozen=True)
class StructuredRiskMethod:
    severity: SeverityMethod
    exposure: ExposureMethod
    controllability_profile: ControllabilityProfile
    controllability_overrides: tuple[ControllabilityOverride, ...]
    asil_zero_short_circuit: str
    method_source_hash: str

    def __post_init__(self) -> None:
        if self.asil_zero_short_circuit not in {"QM", "NA", "N/A"}:
            raise ValueError("StructuredRiskMethod has invalid zero short-circuit")
        if not self.method_source_hash:
            raise ValueError("StructuredRiskMethod requires a method source hash")


@dataclass(frozen=True)
class ExposureCombinationRule:
    rule_id: str
    relation: ExposureCombinationRelation
    left_level: str
    right_level: str
    result_level: str
    source_ref: SourceRef
    left_dimension: str = ""
    right_dimension: str = ""
    review_status: ReviewStatus = ReviewStatus.PENDING

    def __post_init__(self) -> None:
        if not self.rule_id or not self.left_level or not self.right_level:
            raise ValueError("ExposureCombinationRule requires identity and operands")
        if self.review_status is ReviewStatus.FINALIZED and not self.result_level:
            raise ValueError("Finalized ExposureCombinationRule requires result_level")


@dataclass(frozen=True)
class ExposureCombinationStep:
    left_dimension: str
    right_dimension: str
    output_dimension: str
    relation: ExposureCombinationRelation

    def __post_init__(self) -> None:
        if not self.left_dimension or not self.right_dimension or not self.output_dimension:
            raise ValueError("ExposureCombinationStep requires input and output identities")


@dataclass(frozen=True)
class ExposureDimensionAssessment:
    dimension: str
    scenario_value: str
    matched_entry_ids: tuple[str, ...]
    duration_level: str = ""
    frequency_level: str = ""
    selected_domain: ExposureMethodDomain = ExposureMethodDomain.UNRESOLVED
    status: CalculationStatus = CalculationStatus.PENDING_METHOD_SEMANTICS
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.dimension or not self.scenario_value or not self.reason.strip():
            raise ValueError("ExposureDimensionAssessment requires dimension, value and reason")


@dataclass(frozen=True)
class ExposureAssessment:
    scenario_id: str
    dimensions: tuple[ExposureDimensionAssessment, ...]
    combination_rule_ids: tuple[str, ...] = ()
    value: str = ""
    status: CalculationStatus = CalculationStatus.PENDING_METHOD_SEMANTICS
    reason: str = ""
    contract_version: str = RISK_CALCULATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.reason.strip():
            raise ValueError("ExposureAssessment requires scenario_id and reason")
        if self.status is CalculationStatus.FINALIZED and not self.value:
            raise ValueError("Finalized ExposureAssessment requires a value")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


class SpeedSemantic(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    EGO_SPEED = "EGO_SPEED"
    RELATIVE_SPEED = "RELATIVE_SPEED"
    IMPACT_SPEED = "IMPACT_SPEED"
    DELTA_V = "DELTA_V"


@dataclass(frozen=True)
class PhysicalConsequence:
    collision_object: str = ""
    collision_configuration: str = ""
    ego_speed_kph: float | None = None
    collision_type: str = ""
    road_user_type: str = ""
    relative_speed_kph: float | None = None
    impact_speed_kph: float | None = None
    delta_v_kph: float | None = None
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeverityAssessmentInput:
    malfunction_id: str
    scenario_id: str
    consequence: PhysicalConsequence
    speed_semantic: SpeedSemantic = SpeedSemantic.UNRESOLVED
    method_rule_ids: tuple[str, ...] = ()
    status: CalculationStatus = CalculationStatus.PENDING_METHOD_SEMANTICS
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.malfunction_id or not self.scenario_id or not self.reason.strip():
            raise ValueError("SeverityAssessmentInput requires identity and reason")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class ControllabilityAssessmentInput:
    malfunction_id: str
    scenario_id: str
    ttc_s: float | None = None
    reaction_time_reference_s: float | None = None
    driver_position: str = ""
    driver_in_vehicle: bool | None = None
    has_remote_app: bool | None = None
    function_type: str = ""
    direct_control_available: bool | None = None
    remote_intervention_available: bool | None = None
    selected_profile_id: str = ""
    matched_rule_id: str = ""
    inputs_used: tuple[str, ...] = ()
    other_road_user_avoidance_possible: bool | None = None
    vehicle_stability: str = ""
    evidence_refs: tuple[str, ...] = ()
    status: CalculationStatus = CalculationStatus.PENDING_METHOD_SEMANTICS
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.malfunction_id or not self.scenario_id or not self.reason.strip():
            raise ValueError("ControllabilityAssessmentInput requires identity and reason")
        if self.ttc_s is not None and self.ttc_s < 0:
            raise ValueError("ControllabilityAssessmentInput.ttc_s cannot be negative")
        for name in (
            "driver_in_vehicle", "has_remote_app", "direct_control_available",
            "remote_intervention_available", "other_road_user_avoidance_possible",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(
                    f"ControllabilityAssessmentInput.{name} must be boolean or null"
                )

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class ControllabilityBand:
    rule_id: str
    result: str
    source_ref: SourceRef
    lower_ttc_s: float | None = None
    upper_ttc_s: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = False
    review_status: ReviewStatus = ReviewStatus.PENDING

    def __post_init__(self) -> None:
        if not self.rule_id or self.result not in {"C0", "C1", "C2", "C3"}:
            raise ValueError("ControllabilityBand requires a rule ID and C0-C3 result")
        if self.lower_ttc_s is None and self.upper_ttc_s is None:
            raise ValueError("ControllabilityBand requires at least one TTC boundary")
        if (
            self.lower_ttc_s is not None and self.upper_ttc_s is not None
            and self.upper_ttc_s < self.lower_ttc_s
        ):
            raise ValueError("ControllabilityBand TTC range is inverted")


@dataclass(frozen=True)
class ControllabilityProfile:
    profile_id: str
    bands: tuple[ControllabilityBand, ...]
    source_ref: SourceRef
    review_status: ReviewStatus = ReviewStatus.PENDING

    def __post_init__(self) -> None:
        if not self.profile_id or not self.bands:
            raise ValueError("ControllabilityProfile requires identity and bands")


@dataclass(frozen=True)
class ControllabilityJudgement:
    value: str
    profile_id: str
    rule_id: str
    inputs_used: tuple[str, ...]
    status: CalculationStatus
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("ControllabilityJudgement requires a reason")
        if self.status is CalculationStatus.FINALIZED and (
            self.value not in {"C0", "C1", "C2", "C3"}
            or not self.profile_id or not self.rule_id or not self.inputs_used
        ):
            raise ValueError(
                "Finalized ControllabilityJudgement requires value, profile, rule and inputs"
            )

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


@dataclass(frozen=True)
class FTTIAssessmentInput:
    malfunction_id: str
    scenario_id: str
    asil: str = ""
    hazard_time_limit_s: float | None = None
    detection_time_s: float | None = None
    reaction_time_s: float | None = None
    safe_state_transition_time_s: float | None = None
    method_rule_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    status: CalculationStatus = CalculationStatus.PENDING_METHOD_SEMANTICS
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.malfunction_id or not self.scenario_id or not self.reason.strip():
            raise ValueError("FTTIAssessmentInput requires identity and reason")
        for value in (
            self.hazard_time_limit_s, self.detection_time_s,
            self.reaction_time_s, self.safe_state_transition_time_s,
        ):
            if value is not None and value < 0:
                raise ValueError("FTTIAssessmentInput times cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return _to_dict(self)


class ScenarioApplicabilityStatus(str, Enum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNRESOLVED_PHASE_BINDING = "UNRESOLVED_PHASE_BINDING"
    UNRESOLVED_DIMENSION_COMPATIBILITY = "UNRESOLVED_DIMENSION_COMPATIBILITY"


@dataclass(frozen=True)
class ScenarioApplicabilityAssessment:
    function_id: str
    malfunction_id: str
    scenario_id: str
    operating_phase: str
    status: ScenarioApplicabilityStatus
    evidence_refs: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if not self.function_id or not self.malfunction_id or not self.scenario_id:
            raise ValueError("ScenarioApplicabilityAssessment requires identities")
        if not self.reason.strip():
            raise ValueError("ScenarioApplicabilityAssessment requires a reason")

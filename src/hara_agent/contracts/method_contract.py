from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .method import RoleBinding, TemplateRole


METHOD_CONTRACT_VERSION = "method-contract-v2"
TEMPLATE_COMPILER_VERSION = "full-template-compiler-v1"


class CompileStatus(str, Enum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    NOT_READY = "NOT_READY"


class CompilerDiagnosticSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class CompilerDiagnosticCode(str, Enum):
    ROLE_MISSING = "ROLE_MISSING"
    ROLE_AMBIGUOUS = "ROLE_AMBIGUOUS"
    RULE_PARSE_FAILED = "RULE_PARSE_FAILED"
    RULE_CONFLICT = "RULE_CONFLICT"
    RULE_RANGE_GAP = "RULE_RANGE_GAP"
    RULE_RANGE_OVERLAP = "RULE_RANGE_OVERLAP"
    UNRESOLVED_RULE_VARIABLE = "UNRESOLVED_RULE_VARIABLE"
    SEMANTIC_VARIABLE_UNRESOLVED = "SEMANTIC_VARIABLE_UNRESOLVED"
    AMBIGUOUS_RESULT = "AMBIGUOUS_RESULT"
    EXAMPLE_NOT_EXECUTABLE = "EXAMPLE_NOT_EXECUTABLE"
    ASSUMPTION_UNRESOLVED = "ASSUMPTION_UNRESOLVED"
    EXPOSURE_METHOD_SELECTION_UNRESOLVED = "EXPOSURE_METHOD_SELECTION_UNRESOLVED"
    ASIL_MATRIX_INCOMPLETE = "ASIL_MATRIX_INCOMPLETE"
    ASIL_NA_SEMANTICS_UNRESOLVED = "ASIL_NA_SEMANTICS_UNRESOLVED"
    REPORT_FIELD_MISMATCH = "REPORT_FIELD_MISMATCH"
    WORKFLOW_COORDINATE_MISMATCH = "WORKFLOW_COORDINATE_MISMATCH"
    SOURCE_REF_MISSING = "SOURCE_REF_MISSING"
    MALFORMED_TEMPLATE_TEXT = "MALFORMED_TEMPLATE_TEXT"


class RuleType(str, Enum):
    NORMATIVE_RULE = "NORMATIVE_RULE"
    CRITERION = "CRITERION"
    DEFINITION = "DEFINITION"
    EXAMPLE = "EXAMPLE"
    ASSUMPTION = "ASSUMPTION"
    WORKFLOW_INSTRUCTION = "WORKFLOW_INSTRUCTION"
    REFERENCE = "REFERENCE"
    OUTPUT_MAPPING = "OUTPUT_MAPPING"


class NormativeStrength(str, Enum):
    NORMATIVE = "NORMATIVE"
    CRITERION = "CRITERION"
    EXAMPLE = "EXAMPLE"
    ASSUMPTION = "ASSUMPTION"
    INSTRUCTION = "INSTRUCTION"
    REFERENCE = "REFERENCE"


class ParseStatus(str, Enum):
    COMPILED = "COMPILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NON_EXECUTABLE = "NON_EXECUTABLE"
    FAILED = "FAILED"


class PredicateOperator(str, Enum):
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LE = "LE"
    GT = "GT"
    GE = "GE"
    BETWEEN = "BETWEEN"
    IN = "IN"
    NOT_IN = "NOT_IN"
    EXISTS = "EXISTS"


class FactType(str, Enum):
    VEHICLE_SPEED = "VEHICLE_SPEED"
    RELATIVE_SPEED = "RELATIVE_SPEED"
    IMPACT_SPEED = "IMPACT_SPEED"
    SPEED_UNSPECIFIED = "SPEED_UNSPECIFIED"
    COLLISION_TYPE = "COLLISION_TYPE"
    ROAD_USER_TYPE = "ROAD_USER_TYPE"
    DURATION_PERCENT = "DURATION_PERCENT"
    OCCURRENCE_FREQUENCY = "OCCURRENCE_FREQUENCY"
    SITUATION_CLASSIFICATION = "SITUATION_CLASSIFICATION"
    DRIVER_STATE = "DRIVER_STATE"
    INTERVENTION_AVAILABLE = "INTERVENTION_AVAILABLE"
    AVOIDABILITY_PERCENT = "AVOIDABILITY_PERCENT"
    FUNCTION = "FUNCTION"
    OUTPUT = "OUTPUT"
    GUIDEWORD = "GUIDEWORD"
    MALFUNCTION = "MALFUNCTION"
    HAZARD = "HAZARD"
    SCENARIO = "SCENARIO"
    SCENARIO_DETAIL = "SCENARIO_DETAIL"
    HAZARDOUS_EVENT = "HAZARDOUS_EVENT"
    SEVERITY = "SEVERITY"
    EXPOSURE = "EXPOSURE"
    CONTROLLABILITY = "CONTROLLABILITY"
    ASIL = "ASIL"
    SAFETY_GOAL = "SAFETY_GOAL"
    SAFE_STATE = "SAFE_STATE"


class FactOrigin(str, Enum):
    PROJECT_FACT = "PROJECT_FACT"
    SCENARIO_FACT = "SCENARIO_FACT"
    DERIVED_FACT = "DERIVED_FACT"
    HUMAN_EVIDENCE = "HUMAN_EVIDENCE"


@dataclass(frozen=True)
class SourceRef:
    workbook: str
    template_hash: str
    sheet: str
    range: str
    raw_text: str
    source_hash: str

    @classmethod
    def create(
        cls, *, workbook: str, template_hash: str, sheet: str, range: str, raw_text: str
    ) -> "SourceRef":
        payload = f"{template_hash}\n{sheet}\n{range}\n{raw_text}"
        return cls(
            workbook=workbook,
            template_hash=template_hash,
            sheet=sheet,
            range=range,
            raw_text=raw_text,
            source_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRef":
        return cls(**data)


@dataclass(frozen=True)
class Predicate:
    field: FactType
    operator: PredicateOperator
    value: Any = None
    unit: str = ""
    source: SourceRef | None = None


@dataclass(frozen=True)
class RangePredicate:
    field: FactType
    lower: float | None
    upper: float | None
    lower_inclusive: bool | None
    upper_inclusive: bool | None
    unit: str = ""
    source: SourceRef | None = None


@dataclass(frozen=True)
class CategoricalPredicate:
    field: FactType
    values: tuple[str, ...]
    matching_semantics: str = "EXACT_CANONICAL"
    source: SourceRef | None = None


RulePredicate = Predicate | RangePredicate | CategoricalPredicate


@dataclass(frozen=True)
class CompilerDiagnostic:
    severity: CompilerDiagnosticSeverity
    code: CompilerDiagnosticCode
    message: str
    role: TemplateRole | None = None
    source_refs: tuple[SourceRef, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)
    blocking: bool = False


@dataclass(frozen=True)
class CompiledRule:
    rule_id: str
    rule_type: RuleType
    role: TemplateRole
    predicates: tuple[RulePredicate, ...]
    result: str | None
    alternatives: tuple[str, ...]
    priority: int
    normative_strength: NormativeStrength
    source_refs: tuple[SourceRef, ...]
    raw_text: str
    parser: str
    parse_status: ParseStatus
    diagnostics: tuple[str, ...] = ()
    resolution_requirement: str = ""
    assessment_method: str = ""
    executable: bool = True


@dataclass(frozen=True)
class WorkflowStep:
    activity: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    description: str
    source_ref: SourceRef


@dataclass(frozen=True)
class WorkflowContract:
    steps: tuple[WorkflowStep, ...]
    source_binding: RoleBinding


@dataclass(frozen=True)
class Guideword:
    guideword_id: str
    name: str
    description: str
    order: int
    source_ref: SourceRef


@dataclass(frozen=True)
class GuidewordContract:
    guidewords: tuple[Guideword, ...]
    source_binding: RoleBinding


@dataclass(frozen=True)
class ScenarioDimension:
    dimension_id: str
    canonical_name: str
    display_name: str
    values: tuple[str, ...]
    unit: str
    source_ref: SourceRef
    semantics: str = "METHOD_SCENARIO_ONTOLOGY"


@dataclass(frozen=True)
class ScenarioModel:
    dimensions: tuple[ScenarioDimension, ...]
    structural_constraints: tuple[str, ...]
    source_binding: RoleBinding
    source_type: str = "METHOD_SCENARIO_ONTOLOGY"


@dataclass(frozen=True)
class ScaleLevel:
    level: str
    description: str
    criterion: str
    evidence: str
    source_ref: SourceRef


@dataclass(frozen=True)
class SeverityScale:
    levels: tuple[ScaleLevel, ...]
    source_binding: RoleBinding


@dataclass(frozen=True)
class SeverityContract:
    scale: SeverityScale
    rules: tuple[CompiledRule, ...]
    diagnostics: tuple[CompilerDiagnostic, ...]


@dataclass(frozen=True)
class ExposureEntry:
    entry_id: str
    source_role: TemplateRole
    description: str
    duration_rating: str
    frequency_rating: str
    normative_strength: NormativeStrength
    source_ref: SourceRef
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExposureContract:
    scale: tuple[ScaleLevel, ...]
    duration_rules: tuple[CompiledRule, ...]
    frequency_rules: tuple[CompiledRule, ...]
    situation_mappings: tuple[ExposureEntry, ...]
    examples: tuple[CompiledRule, ...]
    diagnostics: tuple[CompilerDiagnostic, ...]


@dataclass(frozen=True)
class ControllabilityContract:
    scale: tuple[ScaleLevel, ...]
    criteria: tuple[CompiledRule, ...]
    examples: tuple[CompiledRule, ...]
    references: tuple[CompiledRule, ...]
    assumptions: tuple[MethodAssumption, ...]
    diagnostics: tuple[CompilerDiagnostic, ...]


@dataclass(frozen=True)
class ASILMapping:
    severity: str
    exposure: str
    controllability: str
    result: str
    source_ref: SourceRef


@dataclass(frozen=True)
class ASILMatrix:
    severity_levels: tuple[str, ...]
    exposure_levels: tuple[str, ...]
    controllability_levels: tuple[str, ...]
    mappings: tuple[ASILMapping, ...]
    na_semantics: str
    source_binding: RoleBinding
    diagnostics: tuple[CompilerDiagnostic, ...]


@dataclass(frozen=True)
class MethodAssumption:
    parameter: str
    value: str
    unit: str
    scope: str
    normative_strength: NormativeStrength
    source_ref: SourceRef


@dataclass(frozen=True)
class DerivationMethod:
    inputs_required: tuple[str, ...]
    derivation_pattern: str
    qm_handling: str
    aggregation_instructions: str
    semantic_derivation_required: bool
    assumptions: tuple[MethodAssumption, ...]
    instructions: tuple[CompiledRule, ...]
    source_binding: RoleBinding


@dataclass(frozen=True)
class ReportFieldMapping:
    canonical_field: str
    sheet: str
    column: str
    column_index: int
    header_rows: tuple[int, ...]
    source_ref: SourceRef


@dataclass(frozen=True)
class ReportContract:
    hara_fields: tuple[ReportFieldMapping, ...]
    safety_goal_fields: tuple[ReportFieldMapping, ...]
    source_bindings: tuple[RoleBinding, ...]
    diagnostics: tuple[CompilerDiagnostic, ...]


@dataclass(frozen=True)
class RequiredFactSpec:
    fact_type: FactType
    required_for: tuple[str, ...]
    unit: str
    constraints: tuple[str, ...]
    condition: str
    origin: FactOrigin
    source_rule_ids: tuple[str, ...]
    source_refs: tuple[SourceRef, ...]


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serialize(item) for item in value]
    return value


@dataclass(frozen=True)
class MethodContract:
    metadata: dict[str, Any]
    workflow: WorkflowContract
    guidewords: GuidewordContract
    scenario_model: ScenarioModel
    severity: SeverityContract
    exposure: ExposureContract
    controllability: ControllabilityContract
    asil: ASILMatrix
    safety_goal_method: DerivationMethod
    safe_state_method: DerivationMethod
    report_contract: ReportContract
    required_fact_specs: tuple[RequiredFactSpec, ...]
    diagnostics: tuple[CompilerDiagnostic, ...]
    sources: tuple[SourceRef, ...]
    compile_status: CompileStatus
    engineering_rules_compiled: bool
    contract_version: str = METHOD_CONTRACT_VERSION
    compiler_version: str = TEMPLATE_COMPILER_VERSION

    @property
    def blocking_diagnostics(self) -> tuple[CompilerDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.blocking)

    @property
    def warnings(self) -> tuple[CompilerDiagnostic, ...]:
        return tuple(
            item for item in self.diagnostics
            if item.severity is CompilerDiagnosticSeverity.WARNING
        )

    def all_rules(self) -> tuple[CompiledRule, ...]:
        return (
            self.severity.rules
            + self.exposure.duration_rules
            + self.exposure.frequency_rules
            + self.exposure.examples
            + self.controllability.criteria
            + self.controllability.examples
            + self.controllability.references
            + self.safety_goal_method.instructions
            + self.safe_state_method.instructions
        )

    def to_dict(self) -> dict[str, Any]:
        result = _serialize(self)
        result["blocking_diagnostics"] = _serialize(self.blocking_diagnostics)
        result["warnings"] = _serialize(self.warnings)
        return result

    def audit_snapshot(self) -> dict[str, Any]:
        """Return a compact, mutation-sensitive regression representation.

        The full contract remains available through :meth:`to_dict` for audit
        export.  Regression fixtures do not need to duplicate every cell's raw
        text and workbook metadata at each provenance edge; the source hash,
        sheet and range are sufficient to detect a source or parser change.
        """

        def compact(value: Any) -> Any:
            if isinstance(value, dict):
                if {"source_hash", "sheet", "range"} <= value.keys():
                    return {
                        "sheet": value["sheet"],
                        "range": value["range"],
                        "source_hash": value["source_hash"],
                    }
                return {
                    key: compact(item)
                    for key, item in value.items()
                    if key not in {"blocking_diagnostics", "warnings", "raw_text"}
                }
            if isinstance(value, list):
                return [compact(item) for item in value]
            return value

        snapshot = compact(self.to_dict())
        sources = snapshot.pop("sources", [])
        snapshot["source_hashes"] = sorted(
            {item["source_hash"] for item in sources}
        )
        snapshot["snapshot_schema"] = "method-contract-audit-v1"
        return snapshot

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)
        return target

def unique_sources(items: list[SourceRef]) -> tuple[SourceRef, ...]:
    unique: dict[tuple[str, str, str], SourceRef] = {}
    for item in items:
        unique[(item.sheet, item.range, item.source_hash)] = item
    return tuple(unique.values())

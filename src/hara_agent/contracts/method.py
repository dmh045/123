from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


TEMPLATE_ROLE_CONTRACT_VERSION = "template-role-v1"
METHOD_CONTRACT_VERSION = "method-contract-foundation-v1"


class TemplateRole(str, Enum):
    WORKFLOW = "WORKFLOW"
    GUIDEWORD_TABLE = "GUIDEWORD_TABLE"
    SCENARIO_MODEL = "SCENARIO_MODEL"
    SEVERITY_LEVELS = "SEVERITY_LEVELS"
    SEVERITY_RULES = "SEVERITY_RULES"
    EXPOSURE_DURATION_RULES = "EXPOSURE_DURATION_RULES"
    EXPOSURE_FREQUENCY_RULES = "EXPOSURE_FREQUENCY_RULES"
    EXPOSURE_EXAMPLES = "EXPOSURE_EXAMPLES"
    VDA702_SUMMARY = "VDA702_SUMMARY"
    VDA702_FULL = "VDA702_FULL"
    SITUATION_CATALOG = "SITUATION_CATALOG"
    CONTROLLABILITY_LEVELS = "CONTROLLABILITY_LEVELS"
    CONTROLLABILITY_RULES = "CONTROLLABILITY_RULES"
    ASIL_MATRIX = "ASIL_MATRIX"
    HARA_OUTPUT_TABLE = "HARA_OUTPUT_TABLE"
    SAFETY_GOAL_OUTPUT_TABLE = "SAFETY_GOAL_OUTPUT_TABLE"
    SAFETY_GOAL_METHOD = "SAFETY_GOAL_METHOD"
    SAFE_STATE_METHOD = "SAFE_STATE_METHOD"


REQUIRED_TEMPLATE_ROLES = tuple(TemplateRole)


class TemplateDiagnosticSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class TemplateDiagnosticCode(str, Enum):
    ROLE_AMBIGUOUS = "ROLE_AMBIGUOUS"
    ROLE_MISSING = "ROLE_MISSING"
    WORKFLOW_COORDINATE_MISMATCH = "WORKFLOW_COORDINATE_MISMATCH"
    RULE_REGION_CONFLICT = "RULE_REGION_CONFLICT"
    STRUCTURE_INCOMPLETE = "STRUCTURE_INCOMPLETE"
    STRUCTURE_METADATA_MISSING = "STRUCTURE_METADATA_MISSING"
    CONFIRMATION_HASH_MISMATCH = "CONFIRMATION_HASH_MISMATCH"


@dataclass(frozen=True)
class TemplateDiagnostic:
    severity: TemplateDiagnosticSeverity
    code: TemplateDiagnosticCode
    message: str
    role: TemplateRole | None = None
    sheet: str = ""
    region: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["severity"] = self.severity.value
        result["code"] = self.code.value
        result["role"] = self.role.value if self.role is not None else None
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TemplateDiagnostic":
        role = data.get("role")
        return cls(
            severity=TemplateDiagnosticSeverity(data["severity"]),
            code=TemplateDiagnosticCode(data["code"]),
            message=str(data["message"]),
            role=TemplateRole(role) if role else None,
            sheet=str(data.get("sheet", "")),
            region=str(data.get("region", "")),
            details=dict(data.get("details", {})),
        )


@dataclass(frozen=True)
class RoleBinding:
    role: TemplateRole
    sheet: str
    region: str
    detection_method: str
    structural_signature: tuple[str, ...]
    semantic_signature: tuple[str, ...]
    confidence: float
    source_hash: str
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("RoleBinding confidence must be between 0 and 1")
        if not self.sheet or not self.region:
            raise ValueError("RoleBinding requires a sheet and region")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["role"] = self.role.value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoleBinding":
        return cls(
            role=TemplateRole(data["role"]),
            sheet=str(data["sheet"]),
            region=str(data["region"]),
            detection_method=str(data["detection_method"]),
            structural_signature=tuple(data.get("structural_signature", ())),
            semantic_signature=tuple(data.get("semantic_signature", ())),
            confidence=float(data["confidence"]),
            source_hash=str(data["source_hash"]),
            diagnostics=tuple(data.get("diagnostics", ())),
        )


@dataclass(frozen=True)
class TemplateRoleConfirmation:
    """One-time human resolution for an ambiguous template hash.

    The confirmation contains only role locations. It is invalid for any other
    workbook hash and never carries copied engineering rules.
    """

    template_hash: str
    selected_regions: dict[str, dict[str, str]]
    confirmed_by: str
    confirmed_at: str
    rationale: str = ""

    def selection_for(self, role: TemplateRole) -> tuple[str, str] | None:
        item = self.selected_regions.get(role.value)
        if not item:
            return None
        return str(item["sheet"]), str(item["region"])


@dataclass(frozen=True)
class TemplateRoleContract:
    template_id: str
    template_hash: str
    role_bindings: tuple[RoleBinding, ...]
    discovery_evidence: tuple[str, ...]
    diagnostics: tuple[TemplateDiagnostic, ...]
    contract_version: str = TEMPLATE_ROLE_CONTRACT_VERSION
    confirmation: TemplateRoleConfirmation | None = None

    def __post_init__(self) -> None:
        if self.confirmation and self.confirmation.template_hash != self.template_hash:
            raise ValueError(
                "TemplateRoleConfirmation is not valid for this TemplateRoleContract hash"
            )

    def bindings_for(self, role: TemplateRole) -> tuple[RoleBinding, ...]:
        return tuple(item for item in self.role_bindings if item.role is role)

    def binding(self, role: TemplateRole) -> RoleBinding:
        matches = self.bindings_for(role)
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one binding for {role.value}, found {len(matches)}")
        return matches[0]

    @property
    def missing_roles(self) -> tuple[TemplateRole, ...]:
        present = {item.role for item in self.role_bindings}
        return tuple(role for role in REQUIRED_TEMPLATE_ROLES if role not in present)

    @property
    def ready(self) -> bool:
        return not self.missing_roles and not any(
            item.severity is TemplateDiagnosticSeverity.ERROR for item in self.diagnostics
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "template_hash": self.template_hash,
            "role_bindings": [item.to_dict() for item in self.role_bindings],
            "discovery_evidence": list(self.discovery_evidence),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "contract_version": self.contract_version,
            "confirmation": asdict(self.confirmation) if self.confirmation else None,
            "ready": self.ready,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TemplateRoleContract":
        raw_confirmation = data.get("confirmation")
        confirmation = (
            TemplateRoleConfirmation(**raw_confirmation) if raw_confirmation else None
        )
        return cls(
            template_id=str(data["template_id"]),
            template_hash=str(data["template_hash"]),
            role_bindings=tuple(RoleBinding.from_dict(item) for item in data["role_bindings"]),
            discovery_evidence=tuple(data.get("discovery_evidence", ())),
            diagnostics=tuple(
                TemplateDiagnostic.from_dict(item) for item in data.get("diagnostics", ())
            ),
            contract_version=str(data.get("contract_version", TEMPLATE_ROLE_CONTRACT_VERSION)),
            confirmation=confirmation,
        )

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)
        return target

    @classmethod
    def read_json(
        cls, path: str | Path, *, expected_template_hash: str | None = None
    ) -> "TemplateRoleContract":
        contract = cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        if expected_template_hash and contract.template_hash != expected_template_hash:
            raise ValueError(
                "Template role manifest hash mismatch: "
                f"expected={expected_template_hash}, actual={contract.template_hash}"
            )
        return contract


@dataclass(frozen=True)
class MethodSection:
    name: str
    roles: tuple[TemplateRole, ...]
    source_bindings: tuple[RoleBinding, ...]
    compile_status: str = "ROLE_BOUND_RULES_NOT_COMPILED"


@dataclass(frozen=True)
class MethodContract:
    """P0 boundary for the future executable method contract.

    P0 binds method sections to workbook roles only. It deliberately does not
    copy Domain Profile rules or claim that engineering rules are compiled.
    """

    metadata: dict[str, Any]
    workflow: MethodSection
    guidewords: MethodSection
    scenario_model: MethodSection
    severity: MethodSection
    exposure: MethodSection
    controllability: MethodSection
    asil: MethodSection
    safety_goal_method: MethodSection
    safe_state_method: MethodSection
    report_contract: MethodSection
    required_facts: tuple[str, ...] = ()
    diagnostics: tuple[TemplateDiagnostic, ...] = ()
    contract_version: str = METHOD_CONTRACT_VERSION

    @classmethod
    def from_role_contract(cls, contract: TemplateRoleContract) -> "MethodContract":
        if not contract.ready:
            raise ValueError("TemplateRoleContract is not ready for MethodContract foundation")

        def section(name: str, roles: Iterable[TemplateRole]) -> MethodSection:
            role_tuple = tuple(roles)
            bindings = tuple(
                binding
                for role in role_tuple
                for binding in contract.bindings_for(role)
            )
            return MethodSection(name=name, roles=role_tuple, source_bindings=bindings)

        return cls(
            metadata={
                "template_id": contract.template_id,
                "template_hash": contract.template_hash,
                "role_contract_version": contract.contract_version,
                "engineering_rules_compiled": False,
            },
            workflow=section("workflow", (TemplateRole.WORKFLOW,)),
            guidewords=section("guidewords", (TemplateRole.GUIDEWORD_TABLE,)),
            scenario_model=section("scenario_model", (TemplateRole.SCENARIO_MODEL,)),
            severity=section(
                "severity", (TemplateRole.SEVERITY_LEVELS, TemplateRole.SEVERITY_RULES)
            ),
            exposure=section(
                "exposure",
                (
                    TemplateRole.EXPOSURE_DURATION_RULES,
                    TemplateRole.EXPOSURE_FREQUENCY_RULES,
                    TemplateRole.EXPOSURE_EXAMPLES,
                    TemplateRole.VDA702_SUMMARY,
                    TemplateRole.VDA702_FULL,
                    TemplateRole.SITUATION_CATALOG,
                ),
            ),
            controllability=section(
                "controllability",
                (TemplateRole.CONTROLLABILITY_LEVELS, TemplateRole.CONTROLLABILITY_RULES),
            ),
            asil=section("asil", (TemplateRole.ASIL_MATRIX,)),
            safety_goal_method=section(
                "safety_goal_method", (TemplateRole.SAFETY_GOAL_METHOD,)
            ),
            safe_state_method=section("safe_state_method", (TemplateRole.SAFE_STATE_METHOD,)),
            report_contract=section(
                "report_contract",
                (TemplateRole.HARA_OUTPUT_TABLE, TemplateRole.SAFETY_GOAL_OUTPUT_TABLE),
            ),
            diagnostics=contract.diagnostics,
        )

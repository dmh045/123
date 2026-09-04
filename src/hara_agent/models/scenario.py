from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from .common import ReviewStatus, SourceRef

if TYPE_CHECKING:
    from hara_agent.contracts.scenario_causal_assessment import ScenarioCausalAssessment


@dataclass
class ScenarioCandidate:
    scenario_id: str
    operating_scenario: str
    situational_description: str
    situational_detailing: str
    facts: dict[str, Any] = field(default_factory=dict)
    operating_mode: str = ""
    context_resolution: dict[str, Any] = field(default_factory=dict)
    fact_provenance: dict[str, Any] = field(default_factory=dict)
    status: ReviewStatus = ReviewStatus.PENDING
    sources: list[SourceRef] = field(default_factory=list)
    rule_version: str = ""
    review_reason: str = ""
    source_scenario_id: str = ""
    atomic_variant: str = ""
    semantic_fingerprint: str = ""
    scenario_contract_version: str = ""
    exposure_context: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.scenario_id:
            raise ValueError("ScenarioCandidate必须具有scenario_id")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


@dataclass
class ScenarioFeasibilityAssessment:
    malfunction_id: str
    scenario_id: str
    physically_feasible: bool
    functionally_relevant: bool
    causally_relevant: bool
    risk_dimensions_changed: list[str]
    rationale: str
    hazardous_event: str = ""
    potential_harm: str = ""
    status: ReviewStatus = ReviewStatus.PENDING
    confidence: float = 0.0
    breakpoint: str = ""
    causal_chain: dict[str, Any] = field(default_factory=dict)
    risk_dimension_changes: list[dict[str, Any]] = field(default_factory=list)
    evidence_contract_version: str = ""
    causal_assessment: "ScenarioCausalAssessment | None" = None

    @property
    def retain(self) -> bool:
        legacy_result = (
            self.physically_feasible
            and self.functionally_relevant
            and self.causally_relevant
        )
        return legacy_result and (
            self.causal_assessment is None or self.causal_assessment.is_validated
        )

    def __post_init__(self):
        if not self.malfunction_id or not self.scenario_id or not self.rationale:
            raise ValueError("ScenarioFeasibilityAssessment缺少ID或理由")
        if self.retain and not self.hazardous_event:
            raise ValueError("保留场景必须给出Hazardous Event")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Scenario confidence必须在0到1之间")
        if self.causal_assessment is not None:
            if self.causal_assessment.scenario_id != self.scenario_id:
                raise ValueError("Scenario causal assessment ID不一致")
            if self.causally_relevant and not self.causal_assessment.is_validated:
                raise ValueError("正向因果结论必须具有VALIDATED causal assessment")
            if self.breakpoint != self.causal_assessment.breakpoint.value:
                raise ValueError("Scenario breakpoint与typed causal assessment不一致")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        if self.causal_assessment is not None:
            result["causal_assessment"] = self.causal_assessment.to_dict()
        return self._serialize(result)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScenarioFeasibilityAssessment":
        from hara_agent.contracts.scenario_causal_assessment import (
            ScenarioCausalAssessment,
        )

        boolean_fields = (
            "physically_feasible", "functionally_relevant", "causally_relevant",
        )
        if any(not isinstance(value.get(field), bool) for field in boolean_fields):
            raise ValueError(
                "ScenarioFeasibilityAssessment checkpoint booleans are invalid"
            )
        causal_value = value.get("causal_assessment")
        causal_assessment = (
            ScenarioCausalAssessment.from_dict(causal_value)
            if isinstance(causal_value, dict) else None
        )
        return cls(
            malfunction_id=str(value["malfunction_id"]),
            scenario_id=str(value["scenario_id"]),
            physically_feasible=value["physically_feasible"],
            functionally_relevant=value["functionally_relevant"],
            causally_relevant=value["causally_relevant"],
            risk_dimensions_changed=[
                str(item) for item in value.get("risk_dimensions_changed", [])
            ],
            rationale=str(value["rationale"]),
            hazardous_event=str(value.get("hazardous_event", "")),
            potential_harm=str(value.get("potential_harm", "")),
            status=ReviewStatus(value.get("status", ReviewStatus.PENDING.value)),
            confidence=float(value.get("confidence", 0.0)),
            breakpoint=str(value.get("breakpoint", "")),
            causal_chain=dict(value.get("causal_chain", {})),
            risk_dimension_changes=list(value.get("risk_dimension_changes", [])),
            evidence_contract_version=str(
                value.get("evidence_contract_version", "")
            ),
            causal_assessment=causal_assessment,
        )

    @classmethod
    def _serialize(cls, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {key: cls._serialize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._serialize(item) for item in value]
        return value

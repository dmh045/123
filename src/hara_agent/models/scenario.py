from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .common import ReviewStatus, SourceRef


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

    @property
    def retain(self) -> bool:
        return (
            self.physically_feasible
            and self.functionally_relevant
            and self.causally_relevant
            and bool(self.risk_dimensions_changed)
        )

    def __post_init__(self):
        if not self.malfunction_id or not self.scenario_id or not self.rationale:
            raise ValueError("ScenarioFeasibilityAssessment缺少ID或理由")
        if self.retain and (not self.hazardous_event or not self.potential_harm):
            raise ValueError("保留场景必须给出Hazardous Event和Potential Harm")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Scenario confidence必须在0到1之间")

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from hara_agent.models import FactProvenance, SourceRef


@dataclass
class EvaluationAttempt:
    valid: bool
    scenario_id: str
    semantic_fingerprint: str
    result: Any = None
    error: Exception | None = None


@dataclass
class ScenarioEvaluationInput:
    malfunction: Any
    scenarios: list[Any]
    repeat: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


class GapClassification(str, Enum):
    PRESENT_EXPLICIT = "PRESENT_EXPLICIT"
    PRESENT_DERIVABLE = "PRESENT_DERIVABLE"
    EXTRACTOR_MISSED = "EXTRACTOR_MISSED"
    ATOMICIZATION_FAILED = "ATOMICIZATION_FAILED"
    ROUTING_MISSED = "ROUTING_MISSED"
    NORMALIZATION_LOSS = "NORMALIZATION_LOSS"
    METHOD_DEFINED = "METHOD_DEFINED"
    SOURCE_NOT_PROVIDED = "SOURCE_NOT_PROVIDED"


@dataclass(frozen=True)
class ExpectedProjectFact:
    fact_id: str
    field: str
    value: Any
    unit: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    source: SourceRef | None = None
    source_block_ids: tuple[str, ...] = ()
    match_terms: tuple[str, ...] = ()
    derivable: bool = False
    route_task: str = ""
    provenance: FactProvenance = FactProvenance.PROJECT_INPUT

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExpectedProjectFact":
        source_value = value.get("source")
        source = SourceRef(**source_value) if isinstance(source_value, dict) else None
        return cls(
            fact_id=str(value["fact_id"]),
            field=str(value["field"]),
            value=value.get("value"),
            unit=str(value.get("unit", "")),
            context=dict(value.get("context", {})),
            source=source,
            source_block_ids=tuple(str(item) for item in value.get("source_block_ids", [])),
            match_terms=tuple(str(item) for item in value.get("match_terms", [])),
            derivable=bool(value.get("derivable", False)),
            route_task=str(value.get("route_task", "")),
            provenance=FactProvenance(
                value.get("provenance", FactProvenance.PROJECT_INPUT.value)
            ),
        )


@dataclass
class ExtractionEvaluationInput:
    source_id: str
    source_blocks: list[Any]
    expected_facts: list[ExpectedProjectFact]
    project_facts: Any
    routed_block_ids: set[str] = field(default_factory=set)
    routed_block_ids_by_task: dict[str, set[str]] = field(default_factory=dict)
    routing_diagnostics_by_task: dict[str, dict[str, Any]] = field(default_factory=dict)
    production_speed_resolutions: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FactEvaluation:
    fact_id: str
    field: str
    classification: GapClassification
    value_match: bool
    grounded: bool
    source_ref_match: bool
    context_match: bool
    normalization_match: bool
    expected_provenance: FactProvenance = FactProvenance.PROJECT_INPUT
    expected_source_locator: str = ""
    selected_block_ids: tuple[str, ...] = ()
    retrieval_diagnostics: dict[str, Any] = field(default_factory=dict)
    observed: Any = None
    reason: str = ""

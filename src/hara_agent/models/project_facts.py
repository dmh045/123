from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .common import FactProvenance, ReviewStatus, SourceRef


class ConstraintOperator(str, Enum):
    LT = "LT"
    LE = "LE"
    EQ = "EQ"
    GE = "GE"
    GT = "GT"
    RANGE = "RANGE"


class ProjectFactOutputType(str, Enum):
    SPEED_ENVELOPE = "SPEED_ENVELOPE"
    NUMERIC_CONSTRAINT = "NUMERIC_CONSTRAINT"
    DRIVER_CONTEXT = "DRIVER_CONTEXT"


class DriverLocation(str, Enum):
    INSIDE = "INSIDE"
    OUTSIDE = "OUTSIDE"


def _validate_context(context: dict[str, str]) -> None:
    if not isinstance(context, dict):
        raise ValueError("Project Fact context must be an object")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in context.items()):
        raise ValueError("Project Fact context must contain only string keys and values")


@dataclass(frozen=True)
class NumericConstraintFact:
    fact_type: str
    parameter: str
    operator: ConstraintOperator
    value: float
    unit: str
    context: dict[str, str] = field(default_factory=dict)
    value_max: float | None = None
    source_refs: list[SourceRef] = field(default_factory=list)
    provenance: FactProvenance = FactProvenance.PROJECT_INPUT
    approval: ReviewStatus = ReviewStatus.PENDING
    extracted_by: str = "LLM"

    def __post_init__(self) -> None:
        if not self.fact_type.strip() or not self.parameter.strip():
            raise ValueError("NumericConstraintFact requires fact_type and parameter")
        if isinstance(self.value, bool) or not math.isfinite(float(self.value)):
            raise ValueError("NumericConstraintFact.value must be a finite number")
        if self.operator is ConstraintOperator.RANGE:
            if self.value_max is None or not math.isfinite(float(self.value_max)):
                raise ValueError("RANGE requires a finite value_max")
            if float(self.value_max) < float(self.value):
                raise ValueError("RANGE value_max must be >= value")
        elif self.value_max is not None:
            raise ValueError("value_max is only valid for RANGE")
        if not self.unit.strip():
            raise ValueError("NumericConstraintFact.unit must not be empty")
        if not self.source_refs:
            raise ValueError("NumericConstraintFact requires an exact SourceRef")
        if self.provenance is not FactProvenance.PROJECT_INPUT:
            raise ValueError("source-grounded numeric facts must use PROJECT_INPUT provenance")
        _validate_context(self.context)


@dataclass(frozen=True)
class DriverContextFact:
    fact_type: str
    driver_location: DriverLocation
    control_mode: str
    condition: str
    sources: list[SourceRef] = field(default_factory=list)
    provenance: FactProvenance = FactProvenance.PROJECT_INPUT
    approval: ReviewStatus = ReviewStatus.PENDING
    extracted_by: str = "LLM"

    def __post_init__(self) -> None:
        if not self.fact_type.strip():
            raise ValueError("DriverContextFact.fact_type must not be empty")
        if not self.sources:
            raise ValueError("DriverContextFact requires an exact SourceRef")
        if self.provenance is not FactProvenance.PROJECT_INPUT:
            raise ValueError("source-grounded driver facts must use PROJECT_INPUT provenance")
        if not isinstance(self.control_mode, str) or not isinstance(self.condition, str):
            raise ValueError("DriverContextFact control_mode and condition must be strings")


def numeric_constraint_from_dict(value: dict[str, Any]) -> NumericConstraintFact:
    payload = dict(value)
    payload["operator"] = ConstraintOperator(payload["operator"])
    payload["source_refs"] = [
        item if isinstance(item, SourceRef) else SourceRef(**item)
        for item in payload.get("source_refs", [])
    ]
    payload["provenance"] = FactProvenance(
        payload.get("provenance", FactProvenance.PROJECT_INPUT.value)
    )
    payload["approval"] = ReviewStatus(
        payload.get("approval", ReviewStatus.PENDING.value)
    )
    return NumericConstraintFact(**payload)


def driver_context_from_dict(value: dict[str, Any]) -> DriverContextFact:
    payload = dict(value)
    payload["driver_location"] = DriverLocation(payload["driver_location"])
    payload["sources"] = [
        item if isinstance(item, SourceRef) else SourceRef(**item)
        for item in payload.get("sources", [])
    ]
    payload["provenance"] = FactProvenance(
        payload.get("provenance", FactProvenance.PROJECT_INPUT.value)
    )
    payload["approval"] = ReviewStatus(
        payload.get("approval", ReviewStatus.PENDING.value)
    )
    return DriverContextFact(**payload)

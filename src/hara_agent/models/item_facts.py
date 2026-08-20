from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .common import FactProvenance, ReviewStatus, SourceRef
from .project_facts import (
    DriverContextFact,
    NumericConstraintFact,
    driver_context_from_dict,
    numeric_constraint_from_dict,
)


@dataclass(frozen=True)
class SpeedEnvelope:
    """A speed constraint scoped to one operating mode and condition."""

    operating_mode: str
    speed_min_kph: Optional[float]
    speed_max_kph: Optional[float]
    condition: str = ""
    unit: str = "km/h"
    sources: list[SourceRef] = field(default_factory=list)
    provenance: FactProvenance = FactProvenance.PROJECT_INPUT
    status: ReviewStatus = ReviewStatus.PENDING

    def __post_init__(self) -> None:
        if not self.operating_mode.strip():
            raise ValueError("SpeedEnvelope.operating_mode must not be empty")
        if self.unit not in {"km/h", "kph"}:
            raise ValueError("SpeedEnvelope.unit must be km/h or kph")
        if self.speed_min_kph is None and self.speed_max_kph is None:
            raise ValueError("SpeedEnvelope must contain at least one speed bound")
        if self.speed_min_kph is not None and self.speed_min_kph < 0:
            raise ValueError("SpeedEnvelope.speed_min_kph must not be negative")
        if self.speed_max_kph is not None and self.speed_max_kph < 0:
            raise ValueError("SpeedEnvelope.speed_max_kph must not be negative")
        if (
            self.speed_min_kph is not None
            and self.speed_max_kph is not None
            and self.speed_max_kph < self.speed_min_kph
        ):
            raise ValueError("SpeedEnvelope maximum must be >= minimum")
        if self.status is ReviewStatus.FINALIZED and not self.sources:
            raise ValueError("FINALIZED SpeedEnvelope must contain a SourceRef")

    @property
    def mode(self) -> str:
        return self.operating_mode

    @property
    def min_kph(self) -> Optional[float]:
        return self.speed_min_kph

    @property
    def max_kph(self) -> Optional[float]:
        return self.speed_max_kph


@dataclass
class ItemDefinitionFacts:
    system_description: str
    item_boundary: str
    operating_modes: list[str] = field(default_factory=list)
    odd_locations: list[str] = field(default_factory=list)
    odd_road_types: list[str] = field(default_factory=list)
    odd_weather_conditions: list[str] = field(default_factory=list)
    odd_road_surfaces: list[str] = field(default_factory=list)
    speed_min_kph: Optional[float] = None
    speed_max_kph: Optional[float] = None
    speed_envelopes: list[SpeedEnvelope] = field(default_factory=list)
    numeric_constraints: list[NumericConstraintFact] = field(default_factory=list)
    driver_context_facts: list[DriverContextFact] = field(default_factory=list)
    # Compatibility views. New production consumers must use the typed fields above.
    performance_parameters: list[dict[str, Any]] = field(default_factory=list)
    driver_contexts: list[dict[str, Any]] = field(default_factory=list)
    exposure_inputs: list[dict[str, Any]] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)
    status: ReviewStatus = ReviewStatus.PENDING
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ItemDefinitionFacts":
        payload = dict(value)
        payload["sources"] = [
            source if isinstance(source, SourceRef) else SourceRef(**source)
            for source in payload.get("sources", [])
        ]
        envelopes = []
        for item in payload.get("speed_envelopes", []):
            if isinstance(item, SpeedEnvelope):
                envelopes.append(item)
                continue
            envelope = dict(item)
            envelope["sources"] = [
                source if isinstance(source, SourceRef) else SourceRef(**source)
                for source in envelope.get("sources", [])
            ]
            envelope["provenance"] = FactProvenance(
                envelope.get("provenance", FactProvenance.PROJECT_INPUT.value)
            )
            envelope["status"] = ReviewStatus(
                envelope.get("status", ReviewStatus.PENDING.value)
            )
            envelopes.append(SpeedEnvelope(**envelope))
        payload["speed_envelopes"] = envelopes
        payload["numeric_constraints"] = [
            item if isinstance(item, NumericConstraintFact) else numeric_constraint_from_dict(item)
            for item in payload.get("numeric_constraints", [])
        ]
        payload["driver_context_facts"] = [
            item if isinstance(item, DriverContextFact) else driver_context_from_dict(item)
            for item in payload.get("driver_context_facts", [])
        ]
        payload["status"] = ReviewStatus(
            payload.get("status", ReviewStatus.PENDING.value)
        )
        return cls(**payload)

    def __post_init__(self):
        if not self.system_description or not self.item_boundary:
            raise ValueError("ItemDefinitionFacts必须包含系统描述和Item边界")
        if (self.speed_min_kph is None) != (self.speed_max_kph is None):
            raise ValueError("ODD速度范围必须同时提供min和max")
        if self.speed_min_kph is not None:
            if self.speed_min_kph < 0 or self.speed_max_kph < self.speed_min_kph:
                raise ValueError("ODD速度范围无效")
        modes = [envelope.operating_mode.strip().casefold() for envelope in self.speed_envelopes]
        if len(modes) != len(set(modes)):
            raise ValueError("speed_envelopes must contain at most one envelope per operating mode")
        numeric_types = [item.fact_type for item in self.numeric_constraints]
        if len(numeric_types) != len(set(numeric_types)):
            raise ValueError("numeric_constraints must contain at most one fact per fact_type")
        driver_types = [item.fact_type for item in self.driver_context_facts]
        if len(driver_types) != len(set(driver_types)):
            raise ValueError("driver_context_facts must contain at most one fact per fact_type")
        if (
            self.speed_min_kph is None
            and self.speed_max_kph is None
            and self.speed_envelopes
            and all(item.speed_min_kph is not None for item in self.speed_envelopes)
            and all(item.speed_max_kph is not None for item in self.speed_envelopes)
        ):
            self.speed_min_kph = min(
                float(item.speed_min_kph) for item in self.speed_envelopes
                if item.speed_min_kph is not None
            )
            self.speed_max_kph = max(
                float(item.speed_max_kph) for item in self.speed_envelopes
                if item.speed_max_kph is not None
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Item Definition confidence必须在0到1之间")
        if not self.sources:
            raise ValueError("ItemDefinitionFacts必须包含来源定位")
        for context in self.driver_contexts:
            if not str(context.get("context_id", "")).strip():
                raise ValueError("driver_contexts每项必须包含context_id")
            if not str(context.get("driver_position", "")).strip():
                raise ValueError("driver_contexts每项必须包含driver_position")
            direct_control = context.get("direct_vehicle_control")
            if direct_control is not None and not isinstance(direct_control, bool):
                raise ValueError("direct_vehicle_control必须为boolean或null")
        for evidence in self.exposure_inputs:
            method = str(evidence.get("method", "")).upper()
            if method and method not in {"T", "F"}:
                raise ValueError("exposure_inputs.method必须为T、F或空")

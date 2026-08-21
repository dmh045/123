from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Sequence

from hara_agent.contracts import FactOrigin, FactType, RequiredFactSpec
from hara_agent.models import ProjectFactOutputType

from .evidence_retrieval import FactRetrievalSpec


@dataclass(frozen=True)
class RequiredProjectFactSpec:
    """Extraction schema compiled from Item facts and the Method Contract."""

    fact_type: str
    output_type: ProjectFactOutputType
    aliases: tuple[str, ...]
    unit_hints: tuple[str, ...]
    context_hints: tuple[str, ...]
    required_fields: tuple[str, ...]
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.fact_type or not self.aliases or not self.required_fields:
            raise ValueError("RequiredProjectFactSpec requires identity, aliases and fields")
        forbidden = {"expected_value", "gold", "source_block_ids", "source_location"}
        if forbidden & set(self.required_fields):
            raise ValueError("RequiredProjectFactSpec cannot encode answers or locators")

    def retrieval_spec(self) -> FactRetrievalSpec:
        return FactRetrievalSpec(
            fact_type=self.fact_type,
            aliases=self.aliases,
            unit_hints=self.unit_hints,
            context_hints=self.context_hints,
        )


def build_project_fact_spec_batches(
    operating_modes: Sequence[str],
    method_specs: Sequence[RequiredFactSpec],
) -> dict[str, tuple[RequiredProjectFactSpec, ...]]:
    """Build bounded extraction work without product/domain constants in Python."""

    speed_specs = tuple(_speed_spec(mode) for mode in _unique_modes(operating_modes))
    risk_specs = tuple(
        _method_risk_spec(spec)
        for spec in _unique_method_specs(method_specs)
        if spec.origin in {FactOrigin.PROJECT_FACT, FactOrigin.HUMAN_EVIDENCE}
        and spec.fact_type not in {FactType.FUNCTION, FactType.OUTPUT}
    )
    batches: dict[str, tuple[RequiredProjectFactSpec, ...]] = {}
    if speed_specs:
        batches["mode_speed_envelopes"] = speed_specs
    if risk_specs:
        batches["method_risk_facts"] = risk_specs
    return batches


def _speed_spec(mode: str) -> RequiredProjectFactSpec:
    normalized = _mode_identity(mode)
    slug = re.sub(r"[^a-z0-9]+", ".", normalized.casefold()).strip(".") or "mode"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return RequiredProjectFactSpec(
        fact_type=f"speed.{slug[:40]}.{digest}",
        output_type=ProjectFactOutputType.SPEED_ENVELOPE,
        aliases=(normalized,),
        unit_hints=("km/h", "kph", "kmh"),
        context_hints=(normalized,),
        required_fields=(
            "status", "operator", "value", "unit",
            "operating_mode", "source_block_id",
        ),
    )


def _method_risk_spec(spec: RequiredFactSpec) -> RequiredProjectFactSpec:
    canonical = spec.fact_type.value
    spaced = canonical.replace("_", " ").casefold()
    return RequiredProjectFactSpec(
        fact_type=canonical,
        output_type=ProjectFactOutputType.RISK_FACT,
        aliases=tuple(dict.fromkeys((canonical, spaced))),
        unit_hints=(spec.unit,) if spec.unit.strip() else (),
        context_hints=(),
        required_fields=("status", "value", "unit", "context", "source_block_id"),
        constraints=spec.constraints,
    )


def _unique_nonblank(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(str(value).split())
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)


def _mode_identity(value: str) -> str:
    normalized = " ".join(str(value).split())
    head = re.split(r"[:：|]", normalized, maxsplit=1)[0].strip()
    return head or normalized


def _unique_modes(values: Sequence[str]) -> tuple[str, ...]:
    return _unique_nonblank(tuple(_mode_identity(value) for value in values))


def _unique_method_specs(
    specs: Sequence[RequiredFactSpec],
) -> tuple[RequiredFactSpec, ...]:
    result: list[RequiredFactSpec] = []
    seen: set[FactType] = set()
    for spec in specs:
        if spec.fact_type not in seen:
            seen.add(spec.fact_type)
            result.append(spec)
    return tuple(result)

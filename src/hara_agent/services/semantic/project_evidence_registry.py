from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, Protocol

from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, ItemDefinitionFacts, ReviewStatus,
)
from .scenario_evidence import FactRegistry


def _token(value: str) -> str:
    token = re.sub(r"[^a-z0-9_.-]+", "_", value.strip().casefold()).strip("_.-")
    if not token:
        raise ValueError("evidence identity component must not be empty")
    return token


def _context_identity(context: dict[str, str]) -> str:
    if not context:
        return "context.default"
    material = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    readable = ".".join(
        f"{_token(key)}.{_token(value)}" for key, value in sorted(context.items()) if value
    )
    if readable and len(readable) <= 80:
        return "context." + readable
    return "context.sha256-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def project_evidence_records(facts: ItemDefinitionFacts) -> tuple[EvidenceRecord, ...]:
    records: list[EvidenceRecord] = []
    for envelope in sorted(
        facts.speed_envelopes,
        key=lambda item: _token(item.operating_mode),
    ):
        mode = _token(envelope.operating_mode)
        for bound, value in (
            ("min_kph", envelope.speed_min_kph),
            ("max_kph", envelope.speed_max_kph),
        ):
            if value is None:
                continue
            records.append(EvidenceRecord(
                f"PROJECT.speed.{mode}.{bound}",
                float(value), EvidenceKind.DIRECT_FACT, envelope.provenance,
                envelope.status, tuple(envelope.sources),
                {
                    "fact_type": "speed_envelope",
                    "operating_mode": mode,
                    "bound": bound,
                    "unit": "km/h",
                    "condition": envelope.condition,
                },
            ))
    for fact in sorted(facts.risk_facts, key=lambda item: (
        item.parameter, json.dumps(item.context, ensure_ascii=False, sort_keys=True)
    )):
        identity = f"{_token(fact.parameter)}.{_context_identity(fact.context)}"
        records.append(EvidenceRecord(
            f"PROJECT.risk.{identity}", fact.value,
            EvidenceKind.DIRECT_FACT, fact.provenance, fact.approval,
            tuple(fact.source_refs),
            {
                "fact_id": fact.fact_id,
                "parameter": fact.parameter,
                "unit": fact.unit,
                "context": dict(fact.context),
                "produced_by": fact.produced_by,
            },
        ))
    # Construction is the collision check; no last-writer-wins path exists.
    registry = FactRegistry()
    registry.extend(records)
    return registry.records


def build_project_evidence_registry(facts: ItemDefinitionFacts) -> FactRegistry:
    registry = FactRegistry()
    registry.extend(project_evidence_records(facts))
    return registry


class EvidenceProvider(Protocol):
    def evidence_records(self) -> Iterable[EvidenceRecord]: ...


class ApprovedRuleEvidenceProvider(EvidenceProvider, Protocol):
    """Interface for versioned, independently approved engineering rules."""


@dataclass(frozen=True)
class StaticApprovedRuleEvidenceProvider:
    """Deterministic fixture/provider adapter; every record must truly be approved."""

    records: tuple[EvidenceRecord, ...]

    def evidence_records(self) -> Iterable[EvidenceRecord]:
        for record in self.records:
            if not record.evidence_ref.startswith("APPROVED_RULE."):
                raise ValueError("approved rules require APPROVED_RULE.* refs")
            if record.kind is not EvidenceKind.APPROVED_RULE:
                raise ValueError("approved rules require APPROVED_RULE kind")
            if record.provenance is not FactProvenance.APPROVED_RULE:
                raise ValueError("approved rules require APPROVED_RULE provenance")
            if record.approval_status is not ReviewStatus.FINALIZED:
                raise ValueError("approved rules require FINALIZED approval")
            yield record


def register_evidence_provider(registry: FactRegistry, provider: EvidenceProvider) -> None:
    registry.extend(provider.evidence_records())

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, is_dataclass
from typing import TYPE_CHECKING, Any, Iterable, Protocol

from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, ItemDefinitionFacts, ReviewStatus,
    SourceRef,
)
from .scenario_evidence import FactRegistry

if TYPE_CHECKING:
    from hara_agent.contracts import MethodContract


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
    speed_mode_counts: dict[str, int] = {}
    for envelope in facts.speed_envelopes:
        mode = _token(envelope.operating_mode)
        speed_mode_counts[mode] = speed_mode_counts.get(mode, 0) + 1
    for envelope in sorted(
        facts.speed_envelopes,
        key=lambda item: (
            _token(item.operating_mode), item.condition,
            tuple((source.location, source.excerpt) for source in item.sources),
        ),
    ):
        mode = _token(envelope.operating_mode)
        scope_material = json.dumps({
            "condition": envelope.condition,
            "sources": [asdict(source) for source in envelope.sources],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        scope = hashlib.sha256(scope_material.encode("utf-8")).hexdigest()[:12]
        identity = mode if speed_mode_counts[mode] == 1 else f"{mode}.scope-{scope}"
        for bound, value in (
            ("min_kph", envelope.speed_min_kph),
            ("max_kph", envelope.speed_max_kph),
        ):
            if value is None:
                continue
            records.append(EvidenceRecord(
                f"PROJECT.speed.{identity}.{bound}",
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


def _method_source_hash(method: "MethodContract") -> str:
    structured = method.structured_risk_method
    return str(
        structured.method_source_hash if structured is not None
        else method.metadata.get("template_hash", "")
    )


def _method_record(
    namespace: str, rule_id: str, value: Any, source, method: "MethodContract",
    *, metadata: dict[str, Any] | None = None,
) -> EvidenceRecord:
    source_hash = _method_source_hash(method)
    evidence_source = source
    if not hasattr(source, "source_type"):
        evidence_source = SourceRef(
            "method_contract",
            str(getattr(source, "template_hash", "") or source_hash),
            f"{getattr(source, 'sheet', '')}!{getattr(source, 'range', '')}",
            str(getattr(source, "raw_text", "")),
        )
    if is_dataclass(value):
        value = asdict(value)
    return EvidenceRecord(
        f"METHOD.{namespace}.{_token(rule_id)}", value,
        EvidenceKind.APPROVED_RULE, FactProvenance.APPROVED_RULE,
        ReviewStatus.FINALIZED, (evidence_source,), {
            "rule_id": rule_id,
            "method_source_hash": source_hash,
            "source_location": f"{getattr(source, 'sheet', '')}!{getattr(source, 'range', '')}",
            **(metadata or {}),
        },
    )


class MethodEvidenceProvider:
    """Expose only rules already compiled into the active MethodContract."""

    def __init__(self, method: "MethodContract"):
        self.method = method

    def evidence_records(self) -> Iterable[EvidenceRecord]:
        method = self.method
        structured = method.structured_risk_method
        if structured is not None:
            for rule in structured.severity.bands:
                yield _method_record(
                    "SEVERITY", rule.rule_id, rule.result, rule.source_ref,
                    method, metadata={
                        "speed_semantic": structured.severity.speed_semantic.value,
                        "collision_group": rule.collision_group,
                        "collision_type": rule.collision_type,
                    },
                )
            for atom in structured.exposure.atoms:
                yield _method_record(
                    "EXPOSURE", atom.atom_id,
                    {
                        "atom_id": atom.atom_id, "dimension": list(atom.dimensions),
                        "label": atom.label, "e_z": atom.duration_level,
                        "e_f": atom.frequency_level,
                    }, atom.source_ref, method,
                    metadata={"kind": "EXPOSURE_ATOM"},
                )
            for rule in structured.exposure.domain_rules:
                yield _method_record(
                    "EXPOSURE", rule.rule_id, rule.domain.value, rule.source_ref,
                    method, metadata={
                        "component_categories": list(rule.component_categories),
                        "kind": "EXPOSURE_DOMAIN_SELECTION",
                    },
                )
            yield _method_record(
                "EXPOSURE", structured.exposure.aggregation_policy.policy_id,
                structured.exposure.aggregation_policy,
                structured.exposure.aggregation_policy.source_ref, method,
                metadata={"kind": "EXPOSURE_COMBINATION_POLICY"},
            )
            for rule in structured.controllability_profile.bands:
                yield _method_record(
                    "CONTROLLABILITY", rule.rule_id, rule.result, rule.source_ref,
                    method, metadata={"profile_id": structured.controllability_profile.profile_id},
                )
            for rule in structured.controllability_overrides:
                yield _method_record(
                    "CONTROLLABILITY", rule.rule_id, rule.result, rule.source_ref,
                    method, metadata={"priority": rule.priority, "kind": "OVERRIDE"},
                )
        else:
            for rule in method.severity.rules:
                yield _method_record("SEVERITY", rule.rule_id, rule.result or rule.alternatives, rule.source_refs[0], method)
            for rule in (*method.exposure.duration_rules, *method.exposure.frequency_rules):
                yield _method_record("EXPOSURE", rule.rule_id, rule.result or rule.alternatives, rule.source_refs[0], method)
            for rule in (*method.controllability.criteria, *method.controllability.examples, *method.controllability.references):
                if rule.source_refs:
                    yield _method_record("CONTROLLABILITY", rule.rule_id, rule.result or rule.alternatives, rule.source_refs[0], method)
        for mapping in method.asil.mappings:
            yield _method_record(
                "ASIL", f"{mapping.severity}_{mapping.exposure}_{mapping.controllability}",
                mapping.result, mapping.source_ref, method,
            )


def build_project_evidence_registry(
    facts: ItemDefinitionFacts, method: "MethodContract" | None = None,
) -> FactRegistry:
    registry = FactRegistry()
    registry.extend(project_evidence_records(facts))
    if method is not None:
        register_evidence_provider(registry, MethodEvidenceProvider(method))
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

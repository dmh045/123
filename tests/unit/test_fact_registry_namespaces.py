import pytest

from hara_agent.models import (
    EVIDENCE_NAMESPACES, EvidenceKind, EvidenceRecord, FactProvenance, ReviewStatus,
)
from hara_agent.services.semantic.project_evidence_registry import (
    StaticApprovedRuleEvidenceProvider, register_evidence_provider,
)
from hara_agent.services.semantic.scenario_evidence import (
    DuplicateEvidenceRefError, FactRegistry,
)


def _record(ref: str) -> EvidenceRecord:
    return EvidenceRecord(
        ref, "fixture", EvidenceKind.DIRECT_FACT,
        FactProvenance.METHOD_CONTRACT, ReviewStatus.PENDING,
    )


def test_all_canonical_namespaces_are_accepted_and_unknown_namespace_is_rejected():
    for namespace in EVIDENCE_NAMESPACES:
        assert _record(f"{namespace}.fixture").namespace == namespace
    with pytest.raises(ValueError, match="namespace or shape"):
        _record("DOMAIN.fixture")


def test_duplicate_evidence_ref_fails_closed_without_overwrite():
    registry = FactRegistry()
    original = _record("METHOD.speed_selection")
    registry.register(original)
    with pytest.raises(DuplicateEvidenceRefError, match="duplicate evidence_ref"):
        registry.register(_record("METHOD.speed_selection"))
    assert registry.resolve_record("METHOD.speed_selection") is original
    assert registry.snapshot()["diagnostics"]["duplicate_ref_count"] == 1


def test_approved_rule_provider_requires_approved_domain_policy_record():
    approved = EvidenceRecord(
        "APPROVED_RULE.fixture", "fixture rule", EvidenceKind.APPROVED_RULE,
        FactProvenance.APPROVED_RULE, ReviewStatus.FINALIZED,
    )
    registry = FactRegistry()
    register_evidence_provider(
        registry, StaticApprovedRuleEvidenceProvider((approved,))
    )
    assert registry.resolve_record(approved.evidence_ref) is approved

    invalid = EvidenceRecord(
        "APPROVED_RULE.pending", "unapproved", EvidenceKind.APPROVED_RULE,
        FactProvenance.LLM_INFERENCE, ReviewStatus.PENDING,
    )
    with pytest.raises(ValueError, match="APPROVED_RULE provenance"):
        register_evidence_provider(
            FactRegistry(), StaticApprovedRuleEvidenceProvider((invalid,))
        )

import pytest

from hara_agent.contracts import (
    CausalSupport, ScenarioEvidenceV2ContractError, ScenarioEvidenceV2ErrorCode,
    ValidationPolicy, validate_causal_supports,
)
from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, ReviewStatus, SourceRef,
)
from hara_agent.services.semantic.scenario_evidence import FactRegistry


SOURCE = SourceRef("test_fixture", "P0-2b", "support")


def registry() -> FactRegistry:
    facts = FactRegistry()
    facts.extend((
        EvidenceRecord(
            "PROJECT.distance", 5.0, EvidenceKind.DIRECT_FACT,
            FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, (SOURCE,),
        ),
        EvidenceRecord(
            "DERIVED.ttc_s", 2.0, EvidenceKind.DERIVED_PHYSICS,
            FactProvenance.DERIVED, ReviewStatus.PENDING, (),
            {"derivation_type": "TTC", "inputs": ["PROJECT.distance"]},
        ),
        EvidenceRecord(
            "SCN.legacy_distance", 0.5, EvidenceKind.DIRECT_FACT,
            FactProvenance.LEGACY_MIGRATION, ReviewStatus.PENDING, (SOURCE,),
        ),
        EvidenceRecord(
            "DOMAIN_RULE.TEST-APPROVED", "fixture", EvidenceKind.APPROVED_RULE,
            FactProvenance.DOMAIN_POLICY, ReviewStatus.FINALIZED, (SOURCE,),
        ),
        EvidenceRecord(
            "DOMAIN_RULE.TEST-PENDING", "fixture", EvidenceKind.APPROVED_RULE,
            FactProvenance.DOMAIN_POLICY, ReviewStatus.PENDING, (SOURCE,),
        ),
        EvidenceRecord(
            "SCN.assumption", "fixture", EvidenceKind.ASSUMPTION,
            FactProvenance.LLM_INFERENCE, ReviewStatus.PENDING,
        ),
        EvidenceRecord(
            "SCN.inference", "fixture", EvidenceKind.DIRECT_FACT,
            FactProvenance.LLM_INFERENCE, ReviewStatus.PENDING, (SOURCE,),
        ),
    ))
    return facts


@pytest.mark.parametrize("support", [
    CausalSupport("PROJECT.distance", EvidenceKind.DIRECT_FACT),
    CausalSupport("DERIVED.ttc_s", EvidenceKind.DERIVED_PHYSICS),
    CausalSupport("DOMAIN_RULE.TEST-APPROVED", EvidenceKind.APPROVED_RULE),
])
def test_direct_derived_project_and_approved_rule_supports_pass_strict(support):
    assert validate_causal_supports((support,), registry()) == {support.evidence_ref}


def test_unknown_support_and_kind_mismatch_fail_with_typed_codes():
    with pytest.raises(ScenarioEvidenceV2ContractError) as unknown:
        validate_causal_supports(
            (CausalSupport("PROJECT.unknown", EvidenceKind.DIRECT_FACT),), registry()
        )
    assert unknown.value.code is ScenarioEvidenceV2ErrorCode.UNRESOLVED_SUPPORT_REF

    with pytest.raises(ScenarioEvidenceV2ContractError) as mismatch:
        validate_causal_supports(
            (CausalSupport("DERIVED.ttc_s", EvidenceKind.DIRECT_FACT),), registry()
        )
    assert mismatch.value.code is ScenarioEvidenceV2ErrorCode.SUPPORT_KIND_MISMATCH


def test_legacy_is_rejected_by_default_but_explicit_migration_policy_can_inspect_it():
    support = CausalSupport("SCN.legacy_distance", EvidenceKind.DIRECT_FACT)
    with pytest.raises(ScenarioEvidenceV2ContractError) as strict:
        validate_causal_supports((support,), registry())
    assert strict.value.code is ScenarioEvidenceV2ErrorCode.UNAPPROVED_EVIDENCE_AUTHORITY
    assert validate_causal_supports(
        (support,), registry(), policy=ValidationPolicy.MIGRATION_EVALUATION
    ) == {"SCN.legacy_distance"}


def test_pending_domain_rule_fails_closed():
    support = CausalSupport("DOMAIN_RULE.TEST-PENDING", EvidenceKind.APPROVED_RULE)
    for policy in (ValidationPolicy.STRICT_RELEASE, ValidationPolicy.MIGRATION_EVALUATION):
        with pytest.raises(ScenarioEvidenceV2ContractError) as pending:
            validate_causal_supports((support,), registry(), policy=policy)
        assert pending.value.code is ScenarioEvidenceV2ErrorCode.UNAPPROVED_EVIDENCE_AUTHORITY


@pytest.mark.parametrize("support", [
    CausalSupport("SCN.assumption", EvidenceKind.ASSUMPTION),
    CausalSupport("SCN.inference", EvidenceKind.DIRECT_FACT),
])
def test_assumption_and_llm_inference_fail_strict_positive_support(support):
    with pytest.raises(ScenarioEvidenceV2ContractError) as caught:
        validate_causal_supports((support,), registry())
    assert caught.value.code is ScenarioEvidenceV2ErrorCode.UNAPPROVED_EVIDENCE_AUTHORITY


def test_derived_support_requires_resolvable_canonical_inputs():
    facts = registry()
    facts.register(EvidenceRecord(
        "DERIVED.invalid", 1.0, EvidenceKind.DERIVED_PHYSICS,
        FactProvenance.DERIVED, ReviewStatus.PENDING, (),
        {"derivation_type": "TEST_ONLY", "inputs": ["PROJECT.missing"]},
    ))
    with pytest.raises(ScenarioEvidenceV2ContractError) as caught:
        validate_causal_supports((
            CausalSupport("DERIVED.invalid", EvidenceKind.DERIVED_PHYSICS),
        ), facts)
    assert caught.value.code is ScenarioEvidenceV2ErrorCode.INVALID_DERIVATION_METADATA

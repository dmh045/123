import pytest

from hara_agent.contracts import (
    CausalMechanismApplication, CausalMechanismContractError,
    CausalMechanismDefinition, CausalMechanismErrorCode, CausalMechanismPremise,
    InMemoryCausalMechanismCatalog,
)
from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, ReviewStatus, SourceRef,
)
from hara_agent.services.semantic.scenario_evidence import FactRegistry


SOURCE = SourceRef("test_fixture", "P0-2b", "TEST_ONLY")


def definition(status=ReviewStatus.FINALIZED):
    return CausalMechanismDefinition(
        "TEST-MECH-DISTANCE-DERIVATION", "1", "TEST_ONLY contract wiring",
        (
            CausalMechanismPremise("distance", EvidenceKind.DIRECT_FACT),
            CausalMechanismPremise("ttc", EvidenceKind.DERIVED_PHYSICS),
        ),
        "TEST_ONLY_RESULT", (SOURCE,), status, FactProvenance.DOMAIN_POLICY,
        {"classification": "TEST_ONLY"},
    )


def registry() -> FactRegistry:
    result = FactRegistry()
    result.extend((
        EvidenceRecord(
            "PROJECT.distance", 5.0, EvidenceKind.DIRECT_FACT,
            FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, (SOURCE,),
        ),
        EvidenceRecord(
            "DERIVED.ttc_s", 2.0, EvidenceKind.DERIVED_PHYSICS,
            FactProvenance.DERIVED, ReviewStatus.PENDING, (),
            {"derivation_type": "TTC", "inputs": ["PROJECT.distance"]},
        ),
    ))
    return result


def application(**bindings):
    return CausalMechanismApplication(
        "TEST-MECH-DISTANCE-DERIVATION", "1", bindings
    )


def test_approved_mechanism_with_complete_bindings_passes():
    catalog = InMemoryCausalMechanismCatalog((definition(),))
    actual = catalog.validate_application(
        application(distance="PROJECT.distance", ttc="DERIVED.ttc_s"), registry(),
        support_refs=frozenset({"PROJECT.distance", "DERIVED.ttc_s"}),
    )
    assert actual.mechanism_id == "TEST-MECH-DISTANCE-DERIVATION"


@pytest.mark.parametrize(("catalog", "app", "code"), [
    (
        InMemoryCausalMechanismCatalog(),
        application(distance="PROJECT.distance", ttc="DERIVED.ttc_s"),
        CausalMechanismErrorCode.UNKNOWN_MECHANISM,
    ),
    (
        InMemoryCausalMechanismCatalog((definition(ReviewStatus.PENDING),)),
        application(distance="PROJECT.distance", ttc="DERIVED.ttc_s"),
        CausalMechanismErrorCode.UNAPPROVED_MECHANISM,
    ),
    (
        InMemoryCausalMechanismCatalog((definition(),)),
        application(distance="PROJECT.distance"),
        CausalMechanismErrorCode.MISSING_REQUIRED_PREMISE,
    ),
    (
        InMemoryCausalMechanismCatalog((definition(),)),
        application(distance="PROJECT.distance", ttc="PROJECT.distance"),
        CausalMechanismErrorCode.PREMISE_KIND_MISMATCH,
    ),
])
def test_unknown_pending_missing_and_wrong_kind_fail_closed(catalog, app, code):
    with pytest.raises(CausalMechanismContractError) as caught:
        catalog.validate_application(
            app, registry(),
            support_refs=frozenset({"PROJECT.distance", "DERIVED.ttc_s"}),
        )
    assert caught.value.code is code


def test_version_mismatch_and_unknown_binding_have_distinct_codes():
    catalog = InMemoryCausalMechanismCatalog((definition(),))
    with pytest.raises(CausalMechanismContractError) as version:
        catalog.validate_application(
            CausalMechanismApplication(
                "TEST-MECH-DISTANCE-DERIVATION", "2", {}
            ), registry(), support_refs=frozenset(),
        )
    assert version.value.code is CausalMechanismErrorCode.MECHANISM_VERSION_MISMATCH

    with pytest.raises(CausalMechanismContractError) as unknown:
        catalog.validate_application(
            application(distance="PROJECT.distance", ttc="DERIVED.ttc_s", extra="PROJECT.distance"),
            registry(), support_refs=frozenset({"PROJECT.distance", "DERIVED.ttc_s"}),
        )
    assert unknown.value.code is CausalMechanismErrorCode.UNKNOWN_PREMISE_BINDING


def test_duplicate_premise_schema_and_unresolved_binding_fail_closed():
    with pytest.raises(ValueError, match="premise_id values must be unique"):
        CausalMechanismDefinition(
            "TEST-DUPLICATE", "1", "TEST_ONLY", (
                CausalMechanismPremise("same", EvidenceKind.DIRECT_FACT),
                CausalMechanismPremise("same", EvidenceKind.DIRECT_FACT),
            ), "result", (SOURCE,), ReviewStatus.FINALIZED,
            FactProvenance.DOMAIN_POLICY,
        )
    catalog = InMemoryCausalMechanismCatalog((definition(),))
    with pytest.raises(CausalMechanismContractError) as unresolved:
        catalog.validate_application(
            application(distance="PROJECT.missing", ttc="DERIVED.ttc_s"), registry(),
            support_refs=frozenset({"PROJECT.missing", "DERIVED.ttc_s"}),
        )
    assert unresolved.value.code is CausalMechanismErrorCode.UNRESOLVED_PREMISE_REF

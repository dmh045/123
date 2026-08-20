from dataclasses import replace

from hara_agent.contracts import (
    CausalMechanismDefinition, CausalMechanismPremise,
)
from hara_agent.models import EvidenceKind, FactProvenance, ReviewStatus, SourceRef
from hara_agent.services.semantic import mechanism_catalog_fingerprint
from hara_agent.services.semantic.scenario_provider_contract import mechanism_catalog_payload


SOURCE = SourceRef("test_fixture", "P0-2c1", "TEST_ONLY")


def definition(mechanism_id, kind=EvidenceKind.DIRECT_FACT):
    return CausalMechanismDefinition(
        mechanism_id, "1", "TEST_ONLY", (
            CausalMechanismPremise("fact", kind),
        ), "TEST_RESULT", (SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )


def test_catalog_fingerprint_is_order_independent_and_schema_sensitive():
    first, second = definition("TEST-A"), definition("TEST-B")
    assert mechanism_catalog_fingerprint((first, second)) == mechanism_catalog_fingerprint(
        (second, first)
    )
    assert mechanism_catalog_fingerprint((first,)) != mechanism_catalog_fingerprint((
        replace(first, version="2"),
    ))
    assert mechanism_catalog_fingerprint((first,)) != mechanism_catalog_fingerprint((
        definition("TEST-A", EvidenceKind.DERIVED_PHYSICS),
    ))
    assert mechanism_catalog_fingerprint((first,)) != mechanism_catalog_fingerprint((
        replace(first, approval_status=ReviewStatus.PENDING),
    ))


def test_catalog_prompt_payload_is_deterministic_and_contains_no_bindings():
    payload = mechanism_catalog_payload((definition("TEST-B"), definition("TEST-A")))
    assert [item["mechanism_id"] for item in payload] == ["TEST-A", "TEST-B"]
    assert all("bindings" not in item for item in payload)


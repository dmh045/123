import pytest

from hara_agent.contracts import (
    CausalMechanismDefinition, CausalMechanismPremise,
    InMemoryCausalMechanismCatalog, ScenarioEvidenceV2ContractError,
    ScenarioEvidenceV2ErrorCode,
)
from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, MalfunctionCandidate,
    ReviewStatus, ScenarioCandidate, SourceRef,
)
from hara_agent.services.semantic import (
    ScenarioProviderContractError, ScenarioProviderErrorCode,
)
from hara_agent.services.semantic.scenario_evidence import FactRegistry
from hara_agent.services.semantic.scenario_provider_contract import parse_v2_assessment


SOURCE = SourceRef("test_fixture", "P0-2c1", "TEST_ONLY")
EDGE_STAGES = (
    ("M_TO_B", "M", "B"), ("B_TO_I", "B", "I"),
    ("I_TO_H", "I", "H"), ("H_TO_HARM", "H", "HARM"),
)


def context():
    malfunction = MalfunctionCandidate(
        "MF-V10", "FUN-1", "loss", "loss", "effect", "hazard",
        ["loss", "effect"],
    )
    scenario = ScenarioCandidate(
        "SCN-V10", "synthetic", "synthetic", "synthetic",
        {"relative_distance": "10 m", "relative_speed_kph": 18},
        semantic_fingerprint="v10-fixture",
    )
    registry = FactRegistry()
    registry.extend((
        EvidenceRecord(
            "PROJECT.distance", 10.0, EvidenceKind.DIRECT_FACT,
            FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, (SOURCE,),
        ),
        EvidenceRecord(
            "DERIVED.ttc_s", 2.0, EvidenceKind.DERIVED_PHYSICS,
            FactProvenance.DERIVED, ReviewStatus.PENDING, (),
            {"derivation_type": "TTC", "inputs": ["PROJECT.distance"]},
        ),
        EvidenceRecord(
            "SCN.legacy", 0.5, EvidenceKind.DIRECT_FACT,
            FactProvenance.LEGACY_MIGRATION, ReviewStatus.PENDING, (SOURCE,),
        ),
    ))
    direct = CausalMechanismDefinition(
        "TEST-MECH-DIRECT-V10", "1", "TEST_ONLY direct", (
            CausalMechanismPremise("fact", EvidenceKind.DIRECT_FACT),
        ), "TEST_RESULT", (SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )
    mixed = CausalMechanismDefinition(
        "TEST-MECH-MIXED-V10", "1", "TEST_ONLY mixed", (
            CausalMechanismPremise("distance", EvidenceKind.DIRECT_FACT),
            CausalMechanismPremise("ttc", EvidenceKind.DERIVED_PHYSICS),
        ), "TEST_RESULT", (SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )
    return malfunction, scenario, registry, InMemoryCausalMechanismCatalog((direct, mixed))


def support(ref, kind):
    return {"evidence_ref": ref, "support_type": kind}


def edge(edge_id, from_stage, to_stage, *, mixed=True):
    supports = (
        [support("PROJECT.distance", "DIRECT_FACT"), support("DERIVED.ttc_s", "DERIVED_PHYSICS")]
        if mixed else [support("PROJECT.distance", "DIRECT_FACT")]
    )
    return {
        "edge_id": edge_id, "from_stage": from_stage, "to_stage": to_stage,
        "claim": "TEST_ONLY claim", "supports": supports,
        "mechanism_application": {
            "mechanism_id": "TEST-MECH-MIXED-V10" if mixed else "TEST-MECH-DIRECT-V10",
            "mechanism_version": "1",
            "bindings": (
                {"distance": "PROJECT.distance", "ttc": "DERIVED.ttc_s"}
                if mixed else {"fact": "PROJECT.distance"}
            ),
        },
    }


def positive_payload():
    supports = [
        support("PROJECT.distance", "DIRECT_FACT"),
        support("DERIVED.ttc_s", "DERIVED_PHYSICS"),
    ]
    return {
        "scenario_id": "SCN-V10", "physically_feasible": True,
        "functionally_relevant": True, "causally_relevant": True,
        "breakpoint": "NONE",
        "edges": [edge(*values) for values in EDGE_STAGES],
        "risk_dimension_changes": [{
            "dimension": "distance", "supports": supports,
            "reason": "TEST_ONLY explicit change",
        }],
        "rationale": "TEST_ONLY complete chain", "hazardous_event": "TEST hazard",
        "potential_harm": "TEST harm", "confidence": 0.8, "status": "PENDING",
    }


def negative_payload():
    return {
        "scenario_id": "SCN-V10", "physically_feasible": True,
        "functionally_relevant": True, "causally_relevant": False,
        "breakpoint": "I_TO_H",
        "edges": [edge(*values, mixed=False) for values in EDGE_STAGES[:2]],
        "risk_dimension_changes": [], "rationale": "no supported I_TO_H mechanism",
        "hazardous_event": "", "potential_harm": "", "confidence": 0.8,
        "status": "PENDING",
    }


def parse(payload):
    malfunction, scenario, registry, catalog = context()
    return parse_v2_assessment(
        payload, malfunction=malfunction, scenario=scenario,
        registry=registry, catalog=catalog,
    )


def test_synthetic_positive_mixed_and_negative_breakpoint_payloads_pass():
    positive = parse(positive_payload())
    negative = parse(negative_payload())
    assert positive.causally_relevant is True
    assert positive.evidence_contract_version == "scenario-evidence-v2"
    assert positive.causal_chain["edges"][0]["supports"] == [
        support("PROJECT.distance", "DIRECT_FACT"),
        support("DERIVED.ttc_s", "DERIVED_PHYSICS"),
    ]
    assert negative.causally_relevant is False
    assert negative.breakpoint == "I_TO_H"
    assert len(negative.causal_chain["edges"]) == 2
    assert negative.risk_dimensions_changed == []


def test_v1_field_and_missing_support_ref_fail_as_provider_schema_errors():
    hybrid = positive_payload()
    hybrid["basis_type"] = "DIRECT_FACT"
    with pytest.raises(ScenarioProviderContractError) as v1_field:
        parse(hybrid)
    assert v1_field.value.code is ScenarioProviderErrorCode.V1_FIELD_IN_V2_PAYLOAD

    missing_ref = positive_payload()
    del missing_ref["edges"][0]["supports"][0]["evidence_ref"]
    with pytest.raises(ScenarioProviderContractError) as support_shape:
        parse(missing_ref)
    assert support_shape.value.code is ScenarioProviderErrorCode.INVALID_SUPPORT_SHAPE


@pytest.mark.parametrize(("mutate", "mechanism_code"), [
    (
        lambda item: item["edges"][0]["mechanism_application"].update(
            {"mechanism_id": "TEST-INVENTED-MECHANISM"}
        ),
        "UNKNOWN_MECHANISM",
    ),
    (
        lambda item: item["edges"][0]["mechanism_application"]["bindings"].update(
            {"distance": "SCN.legacy"}
        ),
        "BINDING_NOT_SUPPORTED",
    ),
    (
        lambda item: item["edges"][0]["mechanism_application"].update(
            {"mechanism_version": "999"}
        ),
        "MECHANISM_VERSION_MISMATCH",
    ),
])
def test_unknown_mechanism_binding_outside_supports_and_version_fail(mutate, mechanism_code):
    payload = positive_payload()
    mutate(payload)
    with pytest.raises(ScenarioEvidenceV2ContractError) as caught:
        parse(payload)
    assert caught.value.code is ScenarioEvidenceV2ErrorCode.INVALID_MECHANISM_APPLICATION
    assert caught.value.mechanism_code == mechanism_code


def test_legacy_positive_dimensions_on_negative_and_missing_positive_edge_fail():
    legacy = positive_payload()
    for edge_item in legacy["edges"]:
        edge_item["supports"][0] = support("SCN.legacy", "DIRECT_FACT")
        edge_item["mechanism_application"]["bindings"]["distance"] = "SCN.legacy"
    legacy["risk_dimension_changes"][0]["supports"][0] = support(
        "SCN.legacy", "DIRECT_FACT"
    )
    with pytest.raises(ScenarioEvidenceV2ContractError) as authority:
        parse(legacy)
    assert authority.value.code is ScenarioEvidenceV2ErrorCode.UNAPPROVED_EVIDENCE_AUTHORITY

    negative_dimensions = negative_payload()
    negative_dimensions["risk_dimension_changes"] = [{
        "dimension": "distance",
        "supports": [support("PROJECT.distance", "DIRECT_FACT")],
        "reason": "invalid negative dimension",
    }]
    with pytest.raises(ScenarioEvidenceV2ContractError) as dimensions:
        parse(negative_dimensions)
    assert dimensions.value.code is ScenarioEvidenceV2ErrorCode.CAUSAL_FALSE_WITH_DIMENSIONS

    missing_edge = positive_payload()
    missing_edge["edges"].pop()
    with pytest.raises(ScenarioProviderContractError) as missing:
        parse(missing_edge)
    assert missing.value.code is ScenarioProviderErrorCode.MISSING_EDGE


def test_mechanism_application_shape_is_not_repaired():
    payload = positive_payload()
    payload["edges"][0]["mechanism_application"].pop("bindings")
    with pytest.raises(ScenarioProviderContractError) as caught:
        parse(payload)
    assert caught.value.code is ScenarioProviderErrorCode.INVALID_MECHANISM_APPLICATION_SHAPE


def test_v2_assessment_shape_is_exact_and_not_semantically_coerced():
    payload = positive_payload()
    payload["unexpected"] = "not allowed"
    with pytest.raises(ScenarioProviderContractError) as extra:
        parse(payload)
    assert extra.value.code is ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE

    payload = positive_payload()
    payload["status"] = 1
    with pytest.raises(ScenarioProviderContractError) as non_string:
        parse(payload)
    assert non_string.value.code is ScenarioProviderErrorCode.INVALID_V2_ASSESSMENT_SHAPE


def test_negative_payload_cannot_fabricate_edges_after_breakpoint():
    payload = negative_payload()
    payload["edges"].append(edge(*EDGE_STAGES[2], mixed=False))
    with pytest.raises(ScenarioProviderContractError) as caught:
        parse(payload)
    assert caught.value.code is ScenarioProviderErrorCode.INVALID_V2_EDGE_SHAPE

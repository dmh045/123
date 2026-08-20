from dataclasses import replace

import pytest

from hara_agent.contracts import (
    CausalBreakpointV2, CausalEdgeId, CausalEdgeV2,
    CausalMechanismApplication, CausalMechanismDefinition,
    CausalMechanismPremise, CausalSupport, InMemoryCausalMechanismCatalog,
    RiskDimensionChangeV2, ScenarioEvidenceV2, ScenarioEvidenceV2ContractError,
    ScenarioEvidenceV2ErrorCode, validate_scenario_evidence_v2,
)
from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, ReviewStatus, SourceRef,
)
from hara_agent.services.semantic.scenario_evidence import FactRegistry


SOURCE = SourceRef("test_fixture", "P0-2b", "TEST_ONLY")
STAGES = {
    CausalEdgeId.M_TO_B: ("M", "B"), CausalEdgeId.B_TO_I: ("B", "I"),
    CausalEdgeId.I_TO_H: ("I", "H"), CausalEdgeId.H_TO_HARM: ("H", "HARM"),
}


def registry():
    result = FactRegistry()
    result.extend((
        EvidenceRecord(
            "PROJECT.distance", 10.0, EvidenceKind.DIRECT_FACT,
            FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, (SOURCE,),
        ),
        EvidenceRecord(
            "DERIVED.ttc_s", 2.0, EvidenceKind.DERIVED_PHYSICS,
            FactProvenance.DERIVED, ReviewStatus.PENDING, (),
            {"derivation_type": "TTC", "inputs": ["PROJECT.distance"]},
        ),
    ))
    return result


def mechanism(kinds):
    premises = tuple(
        CausalMechanismPremise(f"p{index}", kind)
        for index, kind in enumerate(kinds, start=1)
    )
    definition = CausalMechanismDefinition(
        "TEST-MECH-SUPPORTS", "1", "TEST_ONLY state transition", premises,
        "TEST_ONLY_RESULT", (SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )
    return definition, InMemoryCausalMechanismCatalog((definition,))


def positive_contract(supports, definition):
    bindings = {
        premise.premise_id: support.evidence_ref
        for premise, support in zip(definition.premises, supports)
    }
    application = CausalMechanismApplication(definition.mechanism_id, "1", bindings)
    edges = tuple(
        CausalEdgeV2(edge_id, *STAGES[edge_id], "TEST_ONLY claim", supports, application)
        for edge_id in CausalEdgeId
    )
    return ScenarioEvidenceV2(
        True, CausalBreakpointV2.NONE, edges,
        (RiskDimensionChangeV2("distance", supports, "explicit fixture change"),),
        "TEST_ONLY hazard", "TEST_ONLY harm",
    )


@pytest.mark.parametrize("supports", [
    (CausalSupport("PROJECT.distance", EvidenceKind.DIRECT_FACT),),
    (CausalSupport("DERIVED.ttc_s", EvidenceKind.DERIVED_PHYSICS),),
    (
        CausalSupport("PROJECT.distance", EvidenceKind.DIRECT_FACT),
        CausalSupport("DERIVED.ttc_s", EvidenceKind.DERIVED_PHYSICS),
    ),
])
def test_direct_derived_and_mixed_support_edges_pass_v2(supports):
    definition, catalog = mechanism(tuple(item.support_type for item in supports))
    validate_scenario_evidence_v2(
        positive_contract(supports, definition), registry(), catalog
    )


def test_causal_false_with_dimensions_preserves_cross_field_failure():
    contract = ScenarioEvidenceV2(
        False, CausalBreakpointV2.M_TO_B, (),
        (RiskDimensionChangeV2(
            "distance", (CausalSupport("PROJECT.distance", EvidenceKind.DIRECT_FACT),),
            "not allowed for negative",
        ),),
        "", "",
    )
    with pytest.raises(ScenarioEvidenceV2ContractError) as caught:
        validate_scenario_evidence_v2(
            contract, registry(), InMemoryCausalMechanismCatalog()
        )
    assert caught.value.code is ScenarioEvidenceV2ErrorCode.CAUSAL_FALSE_WITH_DIMENSIONS


def test_breakpoint_mismatch_fails_and_negative_prefix_needs_no_later_mechanism():
    mismatch = ScenarioEvidenceV2(
        False, CausalBreakpointV2.NONE, (), (), "", ""
    )
    with pytest.raises(ScenarioEvidenceV2ContractError) as caught:
        validate_scenario_evidence_v2(
            mismatch, registry(), InMemoryCausalMechanismCatalog()
        )
    assert caught.value.code is ScenarioEvidenceV2ErrorCode.BREAKPOINT_MISMATCH

    # First edge is unsupported: no completed/later mechanism may be fabricated.
    valid_negative = ScenarioEvidenceV2(
        False, CausalBreakpointV2.M_TO_B, (), (), "", ""
    )
    validate_scenario_evidence_v2(
        valid_negative, registry(), InMemoryCausalMechanismCatalog()
    )


@pytest.mark.parametrize(("contract", "code"), [
    (
        ScenarioEvidenceV2(
            False, CausalBreakpointV2.M_TO_B, (), (), "unexpected", "",
        ),
        ScenarioEvidenceV2ErrorCode.CAUSAL_FALSE_WITH_HAZARD_OUTPUT,
    ),
    (
        ScenarioEvidenceV2(
            True, CausalBreakpointV2.NONE, (), (), "hazard", "harm",
        ),
        ScenarioEvidenceV2ErrorCode.INVALID_EDGE_SEQUENCE,
    ),
])
def test_positive_negative_cross_field_and_edge_invariants_remain_fail_closed(contract, code):
    with pytest.raises(ScenarioEvidenceV2ContractError) as caught:
        validate_scenario_evidence_v2(
            contract, registry(), InMemoryCausalMechanismCatalog()
        )
    assert caught.value.code is code


def test_positive_contract_still_requires_dimensions_and_hazard_outputs():
    supports = (CausalSupport("PROJECT.distance", EvidenceKind.DIRECT_FACT),)
    definition, catalog = mechanism((EvidenceKind.DIRECT_FACT,))
    valid = positive_contract(supports, definition)

    with pytest.raises(ScenarioEvidenceV2ContractError) as dimensions:
        validate_scenario_evidence_v2(
            replace(valid, risk_dimension_changes=()), registry(), catalog
        )
    assert dimensions.value.code is ScenarioEvidenceV2ErrorCode.CAUSAL_TRUE_WITHOUT_DIMENSIONS

    with pytest.raises(ScenarioEvidenceV2ContractError) as hazard:
        validate_scenario_evidence_v2(
            replace(valid, hazardous_event=""), registry(), catalog
        )
    assert hazard.value.code is ScenarioEvidenceV2ErrorCode.CAUSAL_TRUE_WITHOUT_HAZARD_OUTPUT

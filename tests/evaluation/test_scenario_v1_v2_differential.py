from hara_agent.contracts import (
    CausalBreakpointV2, CausalEdgeId, CausalEdgeV2,
    CausalMechanismApplication, CausalMechanismDefinition,
    CausalMechanismPremise, CausalSupport, InMemoryCausalMechanismCatalog,
    ScenarioEvidenceV2, validate_scenario_evidence_v2,
)
from hara_agent.evaluation.stages import ScenarioContractDifferentialHarness
from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, MalfunctionCandidate,
    ReviewStatus, ScenarioCandidate, SourceRef,
)
from hara_agent.services.semantic.scenario_evidence import (
    FactRegistry, validate_evidence_contract,
)


SOURCE = SourceRef("test_fixture", "P0-2b", "TEST_ONLY")


def test_mixed_direct_and_derived_fixture_fails_v1_and_passes_v2():
    registry = FactRegistry()
    registry.extend((
        EvidenceRecord(
            "MF.functional_effect", "effect", EvidenceKind.DIRECT_FACT,
            FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, (SOURCE,),
        ),
        EvidenceRecord(
            "SCN.relative_distance", "10 m", EvidenceKind.DIRECT_FACT,
            FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, (SOURCE,),
        ),
        EvidenceRecord(
            "DERIVED.ttc_s", 2.0, EvidenceKind.DERIVED_PHYSICS,
            FactProvenance.DERIVED, ReviewStatus.PENDING, (),
            {"derivation_type": "TTC", "inputs": ["SCN.relative_distance"]},
        ),
    ))
    malfunction = MalfunctionCandidate(
        "MF-DIFF", "FUN-1", "loss", "lost", "effect", "hazard",
        ["lost", "effect"],
    )
    scenario = ScenarioCandidate(
        "SCN-DIFF", "fixture", "fixture", "fixture",
        {"relative_distance": "10 m"}, semantic_fingerprint="diff",
    )
    v1_item = {
        "causally_relevant": False, "breakpoint": "I_TO_H",
        "causal_chain": {
            "m_to_b": {
                "claim": "direct", "basis_type": "DIRECT_FACT",
                "evidence_refs": ["MF.functional_effect"],
            },
            "b_to_i": {
                "claim": "mixed", "basis_type": "DERIVED_PHYSICS",
                "evidence_refs": ["SCN.relative_distance", "DERIVED.ttc_s"],
            },
            "i_to_h": {"claim": "unsupported", "basis_type": "ASSUMPTION", "evidence_refs": []},
        },
        "risk_dimension_changes": [], "hazardous_event": "", "potential_harm": "",
    }

    direct_definition = CausalMechanismDefinition(
        "TEST-MECH-DIRECT", "1", "TEST_ONLY direct", (
            CausalMechanismPremise("fact", EvidenceKind.DIRECT_FACT),
        ), "TEST_ONLY_RESULT", (SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )
    mixed_definition = CausalMechanismDefinition(
        "TEST-MECH-MIXED", "1", "TEST_ONLY mixed", (
            CausalMechanismPremise("distance", EvidenceKind.DIRECT_FACT),
            CausalMechanismPremise("ttc", EvidenceKind.DERIVED_PHYSICS),
        ), "TEST_ONLY_RESULT", (SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )
    catalog = InMemoryCausalMechanismCatalog((direct_definition, mixed_definition))
    v2_contract = ScenarioEvidenceV2(
        False, CausalBreakpointV2.I_TO_H,
        (
            CausalEdgeV2(
                CausalEdgeId.M_TO_B, "M", "B", "direct",
                (CausalSupport("MF.functional_effect", EvidenceKind.DIRECT_FACT),),
                CausalMechanismApplication("TEST-MECH-DIRECT", "1", {"fact": "MF.functional_effect"}),
            ),
            CausalEdgeV2(
                CausalEdgeId.B_TO_I, "B", "I", "mixed",
                (
                    CausalSupport("SCN.relative_distance", EvidenceKind.DIRECT_FACT),
                    CausalSupport("DERIVED.ttc_s", EvidenceKind.DERIVED_PHYSICS),
                ),
                CausalMechanismApplication(
                    "TEST-MECH-MIXED", "1",
                    {"distance": "SCN.relative_distance", "ttc": "DERIVED.ttc_s"},
                ),
            ),
        ), (), "", "",
    )

    report = ScenarioContractDifferentialHarness().evaluate(
        lambda: validate_evidence_contract(
            malfunction=malfunction, scenario=scenario, item=v1_item, registry=registry,
            prompt_version="scenario-feasibility-v9", batch="1/1",
            split_path="root", split_depth=0,
        ),
        lambda: validate_scenario_evidence_v2(v2_contract, registry, catalog),
        fixture_id="mixed-direct-derived",
    )

    assert report["v1_valid"] is False
    assert report["v1_error_codes"] == ["DERIVED_PHYSICS_KIND_MISMATCH"]
    assert report["v2_valid"] is True
    assert report["v2_error_codes"] == []

import pytest

from hara_agent.contracts import (
    CausalMechanismDefinition, CausalMechanismPremise,
)
from hara_agent.models import EvidenceKind, FactProvenance, ReviewStatus, SourceRef
from hara_agent.services.semantic import (
    ScenarioProviderContractError, ScenarioProviderErrorCode,
)
from hara_agent.services.semantic.scenario_provider_contract import (
    ScenarioContractRuntimeConfig, reject_v2_fields_in_v1_payload,
    validate_v2_envelope,
)
from hara_agent.evaluation.stages import ScenarioContractDifferentialHarness
import eval_hara
import scenario_smoke


SOURCE = SourceRef("test_fixture", "P0-2c1", "TEST_ONLY")


def mechanism(version="1"):
    return CausalMechanismDefinition(
        "TEST-VERSION", version, "TEST_ONLY", (
            CausalMechanismPremise("fact", EvidenceKind.DIRECT_FACT),
        ), "TEST_RESULT", (SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY,
    )


def test_v1_v2_and_catalog_changes_have_distinct_cache_fingerprints():
    v1 = ScenarioContractRuntimeConfig.create("v1", mechanism_definitions=(mechanism(),))
    v2 = ScenarioContractRuntimeConfig.create("v2", mechanism_definitions=(mechanism(),))
    changed = ScenarioContractRuntimeConfig.create(
        "v2", mechanism_definitions=(mechanism("2"),)
    )
    assert v1.cache_fingerprint != v2.cache_fingerprint
    assert v2.cache_fingerprint != changed.cache_fingerprint
    assert ScenarioContractRuntimeConfig.create("v1").cache_fingerprint == v1.cache_fingerprint


def test_v1_parser_rejects_v2_fields_with_typed_error():
    with pytest.raises(ScenarioProviderContractError) as caught:
        reject_v2_fields_in_v1_payload({"scenario_id": "SCN-1", "edges": []})
    assert caught.value.code is ScenarioProviderErrorCode.V2_FIELD_IN_V1_PAYLOAD

    with pytest.raises(ScenarioProviderContractError) as nested:
        reject_v2_fields_in_v1_payload({
            "scenario_id": "SCN-1",
            "causal_chain": {
                "m_to_b": {
                    "claim": "hybrid", "supports": [],
                    "mechanism_application": None,
                },
            },
        })
    assert nested.value.code is ScenarioProviderErrorCode.V2_FIELD_IN_V1_PAYLOAD


@pytest.mark.parametrize(("assessments", "code"), [
    ([], ScenarioProviderErrorCode.MISSING_ASSESSMENT),
    ([{"scenario_id": "SCN-1"}, {"scenario_id": "SCN-1"}],
     ScenarioProviderErrorCode.DUPLICATE_ASSESSMENT),
    ([{"scenario_id": "SCN-UNKNOWN"}], ScenarioProviderErrorCode.UNKNOWN_SCENARIO_ID),
])
def test_v2_envelope_exact_coverage_fails_closed(assessments, code):
    with pytest.raises(ScenarioProviderContractError) as caught:
        validate_v2_envelope({"assessments": assessments}, ["SCN-1"])
    assert caught.value.code is code


def test_v2_envelope_rejects_extra_top_level_fields():
    with pytest.raises(ScenarioProviderContractError) as caught:
        validate_v2_envelope(
            {"assessments": [{"scenario_id": "SCN-1"}], "extra": True}, ["SCN-1"]
        )
    assert caught.value.code is ScenarioProviderErrorCode.INVALID_V2_ENVELOPE


def test_differential_report_exposes_both_provider_and_assessment_versions():
    report = ScenarioContractDifferentialHarness().evaluate(
        lambda: None, lambda: None, fixture_id="version-visibility",
    )
    assert report["contracts"] == {
        "v1": {
            "provider_schema_version": "ScenarioFeasibilityAssessmentList",
            "assessment_contract_version": "scenario-evidence-v1",
        },
        "v2": {
            "provider_schema_version": "ScenarioFeasibilityAssessmentV2List",
            "assessment_contract_version": "scenario-evidence-v2",
        },
    }


def test_cli_contract_switches_default_to_v1_and_allow_explicit_v2():
    smoke_required = [
        "--domain", "avp", "--template", "template.xlsx", "--run-id", "run-1",
    ]
    assert scenario_smoke._parser().parse_args(smoke_required).scenario_contract == "v1"
    assert scenario_smoke._parser().parse_args(
        [*smoke_required, "--scenario-contract", "v2"]
    ).scenario_contract == "v2"

    assert eval_hara._parser().parse_args(["--stage", "scenario"]).scenario_contract == "v1"
    assert eval_hara._parser().parse_args(
        ["--stage", "scenario", "--scenario-contract", "v2"]
    ).scenario_contract == "v2"

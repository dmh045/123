from hara_agent.services.semantic.scenario_provider_schema import (
    ScenarioFeasibilityAssessmentV2Provider,
    provider_field_names,
    scenario_v2_provider_schema,
    scenario_v2_provider_schema_fingerprint,
)


EXPECTED_SCHEMA_SHA256 = "4a28a8159c751033112ab400dc8d20223ff2c059424e047b64620458f643ddb3"


def test_provider_schema_is_strict_and_derived_from_canonical_models():
    schema = scenario_v2_provider_schema()
    definitions = schema["$defs"]
    assessment = definitions["ScenarioFeasibilityAssessmentV2Provider"]

    assert set(assessment["required"]) == provider_field_names(
        ScenarioFeasibilityAssessmentV2Provider
    )
    assert all(
        definition.get("additionalProperties") is False
        for definition in definitions.values()
    )
    assert assessment["properties"]["breakpoint"]["enum"][-1] == "NONE"
    assert definitions["CausalEdgeV2"]["properties"]["edge_id"]["enum"] == [
        "M_TO_B", "B_TO_I", "I_TO_H", "H_TO_HARM",
    ]


def test_schema_contains_canonical_enums_and_nullability():
    definitions = scenario_v2_provider_schema()["$defs"]
    assert definitions["ScenarioFeasibilityAssessmentV2Provider"]["properties"]["breakpoint"]["enum"] == [
        "M_TO_B", "B_TO_I", "I_TO_H", "H_TO_HARM", "NONE",
    ]
    assert definitions["CausalEdgeV2"]["properties"]["from_stage"]["enum"] == [
        "M", "B", "I", "H", "HARM",
    ]
    application = definitions["CausalEdgeV2"]["properties"]["mechanism_application"]
    assert {item.get("type") for item in application["anyOf"]} >= {"null"}


def test_provider_schema_snapshot_hash_changes_on_contract_drift():
    assert scenario_v2_provider_schema_fingerprint() == EXPECTED_SCHEMA_SHA256

from copy import deepcopy

from hara_agent.services.semantic.provider_contract_conformance import (
    ProviderConformanceCode,
    evaluate_provider_contract_conformance,
)


def _support(ref="SCN.transition"):
    return {"evidence_ref": ref, "support_type": "DIRECT_FACT"}


def _application(ref="SCN.transition"):
    return {
        "mechanism_id": "TEST-MECH",
        "mechanism_version": "1",
        "bindings": {"transition_contract": ref},
    }


def _edge(edge_id, start, end):
    return {
        "edge_id": edge_id,
        "from_stage": start,
        "to_stage": end,
        "claim": f"{start} causes {end}",
        "supports": [_support()],
        "mechanism_application": _application(),
    }


def canonical_negative():
    return {"assessments": [{
        "scenario_id": "SCN-NEG",
        "physically_feasible": True,
        "functionally_relevant": True,
        "causally_relevant": False,
        "breakpoint": "I_TO_H",
        "edges": [_edge("M_TO_B", "M", "B"), _edge("B_TO_I", "B", "I")],
        "risk_dimension_changes": [],
        "rationale": "I_TO_H is unsupported.",
        "hazardous_event": "",
        "potential_harm": "",
        "confidence": 0.9,
        "status": "PENDING",
    }]}


def canonical_positive():
    payload = canonical_negative()
    item = payload["assessments"][0]
    item.update({
        "scenario_id": "SCN-POS",
        "causally_relevant": True,
        "breakpoint": "NONE",
        "edges": [
            _edge("M_TO_B", "M", "B"), _edge("B_TO_I", "B", "I"),
            _edge("I_TO_H", "I", "H"), _edge("H_TO_HARM", "H", "HARM"),
        ],
        "risk_dimension_changes": [{
            "dimension": "distance", "supports": [_support()],
            "reason": "Distance changes the interaction.",
        }],
        "hazardous_event": "Collision",
        "potential_harm": "Injury",
    })
    return payload


def test_canonical_negative_and_positive_are_structurally_conformant():
    assert evaluate_provider_contract_conformance(canonical_negative()).valid
    assert evaluate_provider_contract_conformance(canonical_positive()).valid


def test_null_wrong_enum_v1_leak_and_wrong_mechanism_field_are_separate():
    payload = deepcopy(canonical_negative())
    item = payload["assessments"][0]
    item["hazardous_event"] = None
    item["breakpoint"] = "I->H"
    item["causal_chain"] = {}
    application = item["edges"][0]["mechanism_application"]
    application["version"] = application.pop("mechanism_version")

    report = evaluate_provider_contract_conformance(payload)

    assert not report.valid
    assert ProviderConformanceCode.WRONG_NULLABILITY.value in report.error_codes
    assert ProviderConformanceCode.WRONG_BREAKPOINT_ENCODING.value in report.error_codes
    assert ProviderConformanceCode.V1_FIELD_LEAKAGE.value in report.error_codes
    assert ProviderConformanceCode.WRONG_FIELD_NAME.value in report.error_codes


def test_negative_requires_empty_array_and_empty_strings_not_null_or_na():
    payload = deepcopy(canonical_negative())
    item = payload["assessments"][0]
    item["risk_dimension_changes"] = None
    item["hazardous_event"] = "N/A"

    report = evaluate_provider_contract_conformance(payload)

    assert ProviderConformanceCode.WRONG_NULLABILITY.value in report.error_codes
    assert ProviderConformanceCode.CROSS_FIELD_INVARIANT.value in report.error_codes

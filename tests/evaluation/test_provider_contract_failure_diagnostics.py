import json
from pathlib import Path

from hara_agent.services.semantic.provider_contract_conformance import (
    ProviderConformanceCode,
    diagnose_captured_attempt_record,
    summarize_provider_failure_layers,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "provider_contract"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_captured_negative_exposes_status_stage_and_mechanism_subreasons():
    captured = _fixture("p0_2c2_synthetic_negative_real_failure.json")

    report = diagnose_captured_attempt_record(captured["attempt"])

    assert not report.valid
    assert report.json_valid is None
    assert ProviderConformanceCode.RAW_PAYLOAD_NOT_CAPTURED.value in report.error_codes
    assert ProviderConformanceCode.WRONG_ENUM_VALUE.value in report.error_codes
    assert ProviderConformanceCode.WRONG_FIELD_NAME.value in report.error_codes
    assert ProviderConformanceCode.WRONG_MECHANISM_SHAPE.value in report.error_codes
    assert captured["raw_provider_body_persisted"] is False


def test_captured_positive_is_transport_timeout_not_schema_failure():
    captured = _fixture("p0_2c2_synthetic_positive_real_failure.json")

    diagnostics = diagnose_captured_attempt_record(captured["attempt"])
    layers = summarize_provider_failure_layers({
        "provider_calls": [captured["provider_call"]],
        "attempts": [captured["attempt"]],
    })

    assert ProviderConformanceCode.TRANSPORT_TIMEOUT.value in diagnostics.error_codes
    assert layers == {
        "transport_error_count": 1,
        "provider_finish_error_count": 1,
        "schema_error_count": 0,
        "contract_error_count": 0,
    }

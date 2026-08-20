from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from hara_agent.contracts import CausalEdgeId

from .scenario_provider_schema import scenario_v2_provider_schema


class ProviderConformanceCode(str, Enum):
    INVALID_JSON = "INVALID_JSON"
    RAW_PAYLOAD_NOT_CAPTURED = "RAW_PAYLOAD_NOT_CAPTURED"
    MISSING_FIELD = "MISSING_FIELD"
    EXTRA_FIELD = "EXTRA_FIELD"
    WRONG_FIELD_NAME = "WRONG_FIELD_NAME"
    WRONG_NULLABILITY = "WRONG_NULLABILITY"
    WRONG_COLLECTION_SHAPE = "WRONG_COLLECTION_SHAPE"
    WRONG_EDGE_SHAPE = "WRONG_EDGE_SHAPE"
    WRONG_BREAKPOINT_ENCODING = "WRONG_BREAKPOINT_ENCODING"
    WRONG_EMPTY_VALUE_ENCODING = "WRONG_EMPTY_VALUE_ENCODING"
    WRONG_SUPPORT_SHAPE = "WRONG_SUPPORT_SHAPE"
    WRONG_MECHANISM_SHAPE = "WRONG_MECHANISM_SHAPE"
    WRONG_ENUM_VALUE = "WRONG_ENUM_VALUE"
    WRONG_BOOLEAN_TYPE = "WRONG_BOOLEAN_TYPE"
    V1_FIELD_LEAKAGE = "V1_FIELD_LEAKAGE"
    CROSS_FIELD_INVARIANT = "CROSS_FIELD_INVARIANT"
    TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"


@dataclass(frozen=True)
class ProviderConformanceDiagnostic:
    code: ProviderConformanceCode
    path: str
    reason: str


@dataclass(frozen=True)
class ProviderContractConformanceReport:
    json_valid: bool | None
    envelope_valid: bool
    assessment_shape_valid: bool
    edge_shape_valid: bool
    support_shape_valid: bool
    mechanism_shape_valid: bool
    cross_field_valid: bool
    first_failure_stage: str | None
    diagnostics: tuple[ProviderConformanceDiagnostic, ...]

    @property
    def error_codes(self) -> list[str]:
        return list(dict.fromkeys(item.code.value for item in self.diagnostics))

    @property
    def valid(self) -> bool:
        return all((
            self.json_valid is True,
            self.envelope_valid,
            self.assessment_shape_valid,
            self.edge_shape_valid,
            self.support_shape_valid,
            self.mechanism_shape_valid,
            self.cross_field_valid,
        ))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["error_codes"] = self.error_codes
        result["valid"] = self.valid
        return result


def evaluate_provider_contract_conformance(
    payload: str | dict[str, Any] | list[Any],
) -> ProviderContractConformanceReport:
    diagnostics: list[ProviderConformanceDiagnostic] = []
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            diagnostics.append(ProviderConformanceDiagnostic(
                ProviderConformanceCode.INVALID_JSON, "$",
                f"invalid JSON at line {error.lineno} column {error.colno}",
            ))
            return _report(False, diagnostics)
    else:
        value = payload
    _validate_schema(
        scenario_v2_provider_schema(), value, "$", diagnostics,
        scenario_v2_provider_schema().get("$defs", {}),
    )
    if isinstance(value, dict) and isinstance(value.get("assessments"), list):
        for index, assessment in enumerate(value["assessments"]):
            if isinstance(assessment, dict):
                _validate_cross_fields(assessment, f"$.assessments[{index}]", diagnostics)
    return _report(True, diagnostics)


def diagnose_captured_attempt_record(
    attempt: dict[str, Any],
) -> ProviderContractConformanceReport:
    """Diagnose a P0-2c2 audit record when the full raw body was not retained."""
    diagnostics = [ProviderConformanceDiagnostic(
        ProviderConformanceCode.RAW_PAYLOAD_NOT_CAPTURED,
        "$", "P0-2c2 persisted a parsed summary, not the complete Provider body",
    )]
    error = attempt.get("error") or {}
    error_code = str(error.get("code", ""))
    reason = str(error.get("reason", ""))
    if "timeout" in reason.lower() or "超时" in reason:
        diagnostics.append(ProviderConformanceDiagnostic(
            ProviderConformanceCode.TRANSPORT_TIMEOUT,
            "$", "Provider read timeout occurred before a payload was available",
        ))
        return _report(None, diagnostics)
    if error_code == "MISSING_ASSESSMENT":
        diagnostics.append(ProviderConformanceDiagnostic(
            ProviderConformanceCode.MISSING_FIELD,
            "$.assessments[expected_scenario_id]",
            "adaptive child results did not provide exact scenario coverage",
        ))
    if "status must be PENDING or FINALIZED" in reason:
        diagnostics.append(ProviderConformanceDiagnostic(
            ProviderConformanceCode.WRONG_ENUM_VALUE,
            "$.assessments[*].status",
            "captured parser error proves status was outside PENDING/FINALIZED; actual value was not retained",
        ))
    assessment = attempt.get("provider_assessment") or {}
    for index, edge in enumerate(assessment.get("edges", [])):
        if not isinstance(edge, dict):
            continue
        edge_id = str(edge.get("edge_id", ""))
        expected = edge_id.split("_TO_") if edge_id in {item.value for item in CausalEdgeId} else []
        for offset, field_name in enumerate(("from_stage", "to_stage")):
            actual = edge.get(field_name)
            if len(expected) == 2 and actual != expected[offset]:
                diagnostics.append(ProviderConformanceDiagnostic(
                    ProviderConformanceCode.WRONG_ENUM_VALUE,
                    f"$.assessments[*].edges[{index}].{field_name}",
                    f"expected {expected[offset]!r}, captured {actual!r}",
                ))
        application = edge.get("mechanism_application")
        if isinstance(application, dict) and "version" in application:
            diagnostics.append(ProviderConformanceDiagnostic(
                ProviderConformanceCode.WRONG_FIELD_NAME,
                f"$.assessments[*].edges[{index}].mechanism_application.version",
                "field must be mechanism_version, not version",
            ))
            diagnostics.append(ProviderConformanceDiagnostic(
                ProviderConformanceCode.WRONG_MECHANISM_SHAPE,
                f"$.assessments[*].edges[{index}].mechanism_application",
                "mechanism application does not match the canonical field set",
            ))
    return _report(None, diagnostics)


def summarize_provider_failure_layers(report: dict[str, Any]) -> dict[str, int]:
    calls = list(report.get("provider_calls", []))
    attempts = list(report.get("attempts", []))
    transport_types = {"LLMTimeoutError", "LLMTransportError", "TransientLLMError"}
    schema_prefixes = (
        "INVALID_V2_", "V1_FIELD_", "V2_FIELD_", "MISSING_ASSESSMENT",
        "DUPLICATE_ASSESSMENT", "UNKNOWN_SCENARIO_ID",
    )
    return {
        "transport_error_count": sum(
            str((item.get("error") or {}).get("type", "")) in transport_types
            for item in calls
        ),
        "provider_finish_error_count": sum(
            str(item.get("finish_reason", "")) == "error" for item in calls
        ),
        "schema_error_count": sum(
            str((item.get("error") or {}).get("code", "")).startswith(schema_prefixes)
            for item in attempts
        ),
        "contract_error_count": sum(
            bool(item.get("schema_valid")) and not bool(item.get("contract_valid"))
            for item in attempts
        ),
    }


def _validate_schema(
    schema: dict[str, Any], value: Any, path: str,
    diagnostics: list[ProviderConformanceDiagnostic], definitions: dict[str, Any],
) -> None:
    if "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        _validate_schema(definitions[name], value, path, diagnostics, definitions)
        return
    if "anyOf" in schema:
        if any(_matches_type(item, value, definitions) for item in schema["anyOf"]):
            selected = next(item for item in schema["anyOf"] if _matches_type(item, value, definitions))
            _validate_schema(selected, value, path, diagnostics, definitions)
        else:
            _add_type_error(schema, value, path, diagnostics)
        return
    expected_type = schema.get("type")
    if expected_type and not _is_type(expected_type, value):
        _add_type_error(schema, value, path, diagnostics)
        return
    if "enum" in schema and value not in schema["enum"]:
        code = (
            ProviderConformanceCode.WRONG_BREAKPOINT_ENCODING
            if path.endswith(".breakpoint") else ProviderConformanceCode.WRONG_ENUM_VALUE
        )
        diagnostics.append(ProviderConformanceDiagnostic(
            code, path, f"expected one of {schema['enum']}, got {value!r}",
        ))
    if isinstance(value, dict) and expected_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                code = ProviderConformanceCode.MISSING_FIELD
                diagnostics.append(ProviderConformanceDiagnostic(
                    code, f"{path}.{name}", "required field is missing",
                ))
        extra = set(value) - set(properties)
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for name in sorted(extra):
                if name in {"causal_chain", "basis_type", "evidence_refs"}:
                    code = ProviderConformanceCode.V1_FIELD_LEAKAGE
                elif name == "version" and "mechanism_version" in properties:
                    code = ProviderConformanceCode.WRONG_FIELD_NAME
                else:
                    code = ProviderConformanceCode.EXTRA_FIELD
                diagnostics.append(ProviderConformanceDiagnostic(
                    code, f"{path}.{name}", "field is not allowed by the canonical schema",
                ))
        elif isinstance(additional, dict):
            for name in sorted(extra):
                _validate_schema(
                    additional, value[name], f"{path}.{name}", diagnostics, definitions,
                )
        for name, child in properties.items():
            if name in value:
                _validate_schema(child, value[name], f"{path}.{name}", diagnostics, definitions)
    elif isinstance(value, list) and expected_type == "array":
        for index, item in enumerate(value):
            _validate_schema(
                schema.get("items", {}), item, f"{path}[{index}]", diagnostics, definitions,
            )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            diagnostics.append(ProviderConformanceDiagnostic(
                ProviderConformanceCode.WRONG_ENUM_VALUE, path, "number is below minimum",
            ))
        if "maximum" in schema and value > schema["maximum"]:
            diagnostics.append(ProviderConformanceDiagnostic(
                ProviderConformanceCode.WRONG_ENUM_VALUE, path, "number is above maximum",
            ))


def _validate_cross_fields(
    item: dict[str, Any], path: str,
    diagnostics: list[ProviderConformanceDiagnostic],
) -> None:
    causal = item.get("causally_relevant")
    breakpoint = item.get("breakpoint")
    edges = item.get("edges")
    changes = item.get("risk_dimension_changes")
    hazard = item.get("hazardous_event")
    harm = item.get("potential_harm")
    edge_ids = [edge.get("edge_id") for edge in edges if isinstance(edge, dict)] if isinstance(edges, list) else []
    sequence = [edge.value for edge in CausalEdgeId]
    if causal is True:
        invalid = (
            breakpoint != "NONE" or edge_ids != sequence
            or not isinstance(changes, list) or not changes
            or not isinstance(hazard, str) or not hazard
            or not isinstance(harm, str) or not harm
        )
    elif causal is False:
        prefix = sequence[:sequence.index(breakpoint)] if breakpoint in sequence else []
        invalid = (
            breakpoint == "NONE" or edge_ids != prefix or changes != []
            or hazard != "" or harm != ""
        )
        if hazard is None or harm is None or changes is None:
            diagnostics.append(ProviderConformanceDiagnostic(
                ProviderConformanceCode.WRONG_EMPTY_VALUE_ENCODING, path,
                "negative uses null/omission instead of [], and empty strings",
            ))
    else:
        invalid = True
    if invalid:
        diagnostics.append(ProviderConformanceDiagnostic(
            ProviderConformanceCode.CROSS_FIELD_INVARIANT, path,
            "causal verdict, breakpoint, edge prefix, dimensions and hazard outputs disagree",
        ))


def _add_type_error(
    schema: dict[str, Any], value: Any, path: str,
    diagnostics: list[ProviderConformanceDiagnostic],
) -> None:
    expected = schema.get("type")
    if value is None:
        code = ProviderConformanceCode.WRONG_NULLABILITY
    elif expected == "array":
        code = ProviderConformanceCode.WRONG_COLLECTION_SHAPE
    elif expected == "boolean":
        code = ProviderConformanceCode.WRONG_BOOLEAN_TYPE
    elif ".supports" in path:
        code = ProviderConformanceCode.WRONG_SUPPORT_SHAPE
    elif ".mechanism_application" in path:
        code = ProviderConformanceCode.WRONG_MECHANISM_SHAPE
    elif ".edges" in path:
        code = ProviderConformanceCode.WRONG_EDGE_SHAPE
    else:
        code = ProviderConformanceCode.WRONG_ENUM_VALUE
    diagnostics.append(ProviderConformanceDiagnostic(
        code, path, f"expected JSON {expected}, got {type(value).__name__}",
    ))


def _matches_type(schema: dict[str, Any], value: Any, definitions: dict[str, Any]) -> bool:
    if "$ref" in schema:
        schema = definitions[str(schema["$ref"]).rsplit("/", 1)[-1]]
    return not schema.get("type") or _is_type(schema["type"], value)


def _is_type(expected: str, value: Any) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def _report(
    json_valid: bool | None,
    diagnostics: list[ProviderConformanceDiagnostic],
) -> ProviderContractConformanceReport:
    paths = [(item.code, item.path) for item in diagnostics]
    envelope_codes = {
        ProviderConformanceCode.INVALID_JSON,
        ProviderConformanceCode.RAW_PAYLOAD_NOT_CAPTURED,
    }
    envelope_valid = json_valid is True and not any(
        code in envelope_codes or path in {"$", "$.assessments"}
        for code, path in paths
    )
    assessment_valid = envelope_valid and not any(
        ".assessments[" in path and ".edges" not in path
        and ".risk_dimension_changes" not in path for _, path in paths
    )
    edge_valid = not any(".edges" in path for _, path in paths)
    support_valid = not any(".supports" in path for _, path in paths)
    mechanism_valid = not any(".mechanism_application" in path for _, path in paths)
    cross_valid = not any(
        code in {ProviderConformanceCode.CROSS_FIELD_INVARIANT,
                 ProviderConformanceCode.WRONG_EMPTY_VALUE_ENCODING}
        for code, _ in paths
    )
    first_stage = None
    for stage, valid in (
        ("JSON", json_valid is True), ("ENVELOPE", envelope_valid),
        ("ASSESSMENT", assessment_valid), ("EDGE", edge_valid),
        ("SUPPORT", support_valid), ("MECHANISM", mechanism_valid),
        ("CROSS_FIELD", cross_valid),
    ):
        if not valid:
            first_stage = stage
            break
    return ProviderContractConformanceReport(
        json_valid, envelope_valid, assessment_valid, edge_valid,
        support_valid, mechanism_valid, cross_valid, first_stage,
        tuple(diagnostics),
    )

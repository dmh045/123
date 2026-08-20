from __future__ import annotations

import hashlib
import json
import types
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Literal, get_args, get_origin, get_type_hints

from hara_agent.contracts import (
    CausalBreakpointV2,
    CausalEdgeId,
    CausalEdgeV2,
    RISK_DIMENSION_VALUES_V2,
    RiskDimensionChangeV2,
)


SCENARIO_V2_SERIALIZATION_PROFILE = "scenario-v2-provider-serialization-v1"
_STAGES = tuple(dict.fromkeys(
    stage
    for edge_id in CausalEdgeId
    for stage in edge_id.value.split("_TO_")
))


@dataclass(frozen=True)
class ScenarioFeasibilityAssessmentV2Provider:
    """Provider envelope item; causal evidence fields reuse canonical v2 types."""

    scenario_id: str
    physically_feasible: bool
    functionally_relevant: bool
    causally_relevant: bool
    breakpoint: CausalBreakpointV2
    edges: tuple[CausalEdgeV2, ...]
    risk_dimension_changes: tuple[RiskDimensionChangeV2, ...]
    rationale: str
    hazardous_event: str
    potential_harm: str
    confidence: float = field(metadata={"minimum": 0.0, "maximum": 1.0})
    status: Literal["PENDING", "FINALIZED"] = "PENDING"


@dataclass(frozen=True)
class ScenarioFeasibilityAssessmentV2ListProvider:
    assessments: tuple[ScenarioFeasibilityAssessmentV2Provider, ...]


def scenario_v2_provider_schema() -> dict[str, Any]:
    """Derive the Provider JSON Schema from canonical Python dataclasses/enums."""
    definitions: dict[str, Any] = {}
    schema = _schema_for_type(
        ScenarioFeasibilityAssessmentV2ListProvider, definitions
    )
    result = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCENARIO_V2_SERIALIZATION_PROFILE,
        **schema,
        "$defs": definitions,
    }
    edge = definitions["CausalEdgeV2"]
    edge["properties"]["from_stage"] = {"type": "string", "enum": list(_STAGES)}
    edge["properties"]["to_stage"] = {"type": "string", "enum": list(_STAGES)}
    risk = definitions["RiskDimensionChangeV2"]
    risk["properties"]["dimension"] = {
        "type": "string", "enum": list(RISK_DIMENSION_VALUES_V2),
    }
    return result


def provider_field_names(model: type) -> frozenset[str]:
    if not is_dataclass(model):
        raise TypeError(f"provider model must be a dataclass: {model!r}")
    return frozenset(item.name for item in fields(model))


def scenario_v2_provider_schema_fingerprint() -> str:
    encoded = json.dumps(
        scenario_v2_provider_schema(), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def scenario_v2_serialization_instructions() -> str:
    """Compact, mechanical v10 output block; contains no semantic Gold."""
    schema = json.dumps(
        scenario_v2_provider_schema(), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    )
    negative = {
        "causally_relevant": False,
        "breakpoint": "I_TO_H",
        "edges": ["exact ordered prefix before breakpoint"],
        "risk_dimension_changes": [],
        "hazardous_event": "",
        "potential_harm": "",
    }
    positive = {
        "causally_relevant": True,
        "breakpoint": "NONE",
        "edges": ["M_TO_B", "B_TO_I", "I_TO_H", "H_TO_HARM"],
        "risk_dimension_changes": ["one or more canonical objects"],
        "hazardous_event": "non-empty string",
        "potential_harm": "non-empty string",
    }
    return (
        "\nV10_PROVIDER_SERIALIZATION_CONSTRAINT\n"
        f"profile={SCENARIO_V2_SERIALIZATION_PROFILE}\n"
        "Return exactly one JSON object. Every listed field is required; null, N/A, "
        "omitted fields, aliases and additional fields are invalid. status is exactly "
        "PENDING or FINALIZED. from_stage/to_stage use only M,B,I,H,HARM. "
        "mechanism_application is either null or exactly "
        "{mechanism_id,mechanism_version,bindings}; never use a field named version. "
        "Enums are exact and must not be rewritten with arrows or prose. "
        "Use each scenario_id exactly once. Evidence refs and mechanism ids must be selected "
        "only from the supplied registry/catalog; do not output expected selections or Gold.\n"
        f"CANONICAL_JSON_SCHEMA={schema}\n"
        "NEGATIVE_SHAPE="
        + json.dumps(negative, ensure_ascii=False, separators=(",", ":"))
        + "\nPOSITIVE_SHAPE="
        + json.dumps(positive, ensure_ascii=False, separators=(",", ":"))
    )


def _schema_for_type(annotation: Any, definitions: dict[str, Any]) -> dict[str, Any]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        values = list(args)
        value_type = _json_type(type(values[0])) if values else "string"
        return {"type": value_type, "enum": values}
    if origin in {tuple, list}:
        item_type = args[0] if args else Any
        return {"type": "array", "items": _schema_for_type(item_type, definitions)}
    if origin is dict:
        value_type = args[1] if len(args) == 2 else Any
        return {
            "type": "object",
            "additionalProperties": _schema_for_type(value_type, definitions),
        }
    if origin in {types.UnionType} or str(origin) == "typing.Union":
        return {"anyOf": [_schema_for_type(item, definitions) for item in args]}
    if annotation is Any:
        return {}
    if annotation is type(None):
        return {"type": "null"}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        values = [item.value for item in annotation]
        return {"type": _json_type(type(values[0])), "enum": values}
    if is_dataclass(annotation):
        name = annotation.__name__
        if name not in definitions:
            definitions[name] = {}
            hints = get_type_hints(annotation)
            properties: dict[str, Any] = {}
            required = []
            for item in fields(annotation):
                item_schema = _schema_for_type(hints[item.name], definitions)
                item_schema.update(item.metadata)
                properties[item.name] = item_schema
                required.append(item.name)
            definitions[name] = {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
        return {"$ref": f"#/$defs/{name}"}
    return {"type": _json_type(annotation)}


def _json_type(annotation: Any) -> str:
    return {
        str: "string", bool: "boolean", int: "integer", float: "number",
    }.get(annotation, "string")

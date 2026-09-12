from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hara_agent.infrastructure.llm import LLMResponse
from hara_agent.models import ReviewStatus
from hara_agent.services.extraction import ValidatedArtifactCache
from hara_agent.services.semantic.core_item_artifact_contract import (
    CORE_ITEM_ARTIFACTS_SCHEMA,
    CoreItemArtifactsContractError,
    normalize_core_item_artifacts,
)
from hara_agent.services.semantic.item_artifact_agent import (
    ItemArtifactExtractionAgent,
)


def _payload() -> dict:
    return {
        "item_definition": {
            "system_description": "AVP system",
            "item_boundary": "ADS ECU and vehicle interfaces",
            "operating_modes": ["Active"],
            "odd": {
                "locations": ["parking area"],
                "road_types": ["parking road"],
                "weather_conditions": ["clear"],
                "road_surfaces": ["dry"],
                "speed_range_kph": [0, 20],
            },
            "source_location": "paragraph[1]",
            "source_excerpt": "AVP system definition.",
            "confidence": 0.9,
            "status": "PENDING",
        },
        "functions": [{
            "function_id": "F01",
            "name": "Parking control",
            "output": "Vehicle motion request",
            "description": "Control vehicle motion in the parking area.",
            "preconditions": ["AVP active"],
            "triggers": ["Parking requested"],
            "odd_constraints": ["Parking area"],
            "fallback_behavior": "Stop vehicle",
            "consequences": ["Vehicle follows the planned path"],
            "source_location": "paragraph[2]",
            "source_excerpt": "Control vehicle motion in the parking area.",
            "confidence": 0.9,
            "status": "PENDING",
        }],
    }


def test_contract_mechanically_normalizes_single_string_list_fields() -> None:
    payload = _payload()
    payload["item_definition"]["operating_modes"] = "Active"
    payload["functions"][0]["consequences"] = "Vehicle follows the planned path"
    payload["functions"][0]["fallback_behavior"] = ["Stop", "Hold"]

    normalized, diagnostics = normalize_core_item_artifacts(payload)

    assert normalized["item_definition"]["operating_modes"] == ["Active"]
    assert normalized["functions"][0]["consequences"] == [
        "Vehicle follows the planned path"
    ]
    assert normalized["functions"][0]["fallback_behavior"] == "Stop; Hold"
    assert {item["path"] for item in diagnostics} >= {
        "item_definition.operating_modes",
        "functions[0].consequences",
        "functions[0].fallback_behavior",
    }


def test_contract_aggregates_unsafe_nested_types() -> None:
    payload = _payload()
    payload["functions"][0]["consequences"] = {"result": "stopped"}
    payload["functions"][0]["triggers"] = ["request", {"bad": True}]

    with pytest.raises(CoreItemArtifactsContractError) as raised:
        normalize_core_item_artifacts(payload)

    assert any("functions[0].consequences" in value for value in raised.value.errors)
    assert any("functions[0].triggers[1]" in value for value in raised.value.errors)


class _Client:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.requests = []
        self.config = SimpleNamespace(max_tokens=32768)

    def complete_json(self, request):
        self.requests.append(request)
        return LLMResponse(
            data=self.responses.pop(0),
            model="fake",
            request_id=f"request-{len(self.requests)}",
            usage={},
        )


def test_item_agent_accepts_string_consequences_without_second_llm_call() -> None:
    payload = _payload()
    payload["functions"][0]["consequences"] = "Vehicle follows the planned path"
    document = (
        "AVP system definition.\n"
        "Control vehicle motion in the parking area."
    )
    client = _Client([payload])

    facts, functions, audit = ItemArtifactExtractionAgent(client).extract(
        document,
        "ItemDef.docx",
        [
            {"kind": "paragraph", "location": "paragraph[1]", "text": "AVP system definition."},
            {"kind": "paragraph", "location": "paragraph[2]", "text": "Control vehicle motion in the parking area."},
        ],
    )

    assert facts.status is ReviewStatus.FINALIZED
    assert functions[0].status is ReviewStatus.FINALIZED
    assert functions[0].consequences == ["Vehicle follows the planned path"]
    assert audit["schema_repair_count"] == 0
    assert audit["llm_call_count"] == 1
    assert audit["contract_normalizations"] == [{
        "path": "functions[0].consequences",
        "from_type": "string",
        "to_type": "array",
    }]
    assert client.requests[0].response_schema == CORE_ITEM_ARTIFACTS_SCHEMA


def test_item_agent_repairs_unsafe_schema_once() -> None:
    invalid = _payload()
    invalid["functions"][0]["consequences"] = {"result": "stopped"}
    repaired = _payload()
    client = _Client([invalid, repaired])
    document = (
        "AVP system definition.\n"
        "Control vehicle motion in the parking area."
    )

    _, functions, audit = ItemArtifactExtractionAgent(client).extract(
        document, "ItemDef.docx",
    )

    assert functions[0].consequences == ["Vehicle follows the planned path"]
    assert [request.task for request in client.requests] == [
        "extract_core_item_artifacts",
        "repair_core_item_artifact_schema",
    ]
    assert audit["schema_repair_count"] == 1
    assert audit["llm_call_count"] == 2


def test_item_agent_repairs_ungrounded_sources_without_changing_content() -> None:
    invalid = _payload()
    invalid["functions"][0]["source_excerpt"] = "Control parking motion."
    repaired = _payload()
    client = _Client([invalid, repaired])
    document = (
        "AVP system definition.\n"
        "Control vehicle motion in the parking area."
    )

    _, functions, audit = ItemArtifactExtractionAgent(client).extract(
        document, "ItemDef.docx",
    )

    assert functions[0].description == "Control vehicle motion in the parking area."
    assert [request.task for request in client.requests] == [
        "extract_core_item_artifacts",
        "repair_core_item_artifact_sources",
    ]
    assert audit["source_reference_repair_count"] == 1
    assert audit["llm_call_count"] == 2


def test_item_agent_rejects_source_repair_that_changes_content() -> None:
    invalid = _payload()
    invalid["functions"][0]["source_excerpt"] = "Control parking motion."
    repaired = _payload()
    repaired["functions"][0]["description"] = "Changed engineering content."
    client = _Client([invalid, repaired])

    with pytest.raises(
        ValueError,
        match="source reference repair altered non-source Item Definition content",
    ):
        ItemArtifactExtractionAgent(client).extract(
            "AVP system definition.\nControl vehicle motion in the parking area.",
            "ItemDef.docx",
        )


def test_item_agent_revalidates_quarantined_candidate_without_llm_call() -> None:
    payload = _payload()
    payload["functions"][0]["consequences"] = "Vehicle follows the planned path"
    document = (
        "AVP system definition.\n"
        "Control vehicle motion in the parking area."
    )
    first_client = _Client([payload])
    recorded: list[LLMResponse] = []
    ItemArtifactExtractionAgent(first_client).extract(
        document, "ItemDef.docx", candidate_recorder=recorded.append,
    )
    assert len(recorded) == 1

    second_client = _Client([])
    _, functions, audit = ItemArtifactExtractionAgent(second_client).extract(
        document,
        "ItemDef.docx",
        candidate_response=recorded[0],
    )

    assert second_client.requests == []
    assert functions[0].consequences == ["Vehicle follows the planned path"]
    assert audit["candidate_cache_hit"] is True
    assert audit["llm_call_count"] == 0


def test_candidate_cache_is_isolated_from_validated_cache() -> None:
    directory = Path("runtime/agent/cache-contract-tests")
    key = f"candidate-{uuid4().hex}"
    candidate_path = directory / "candidates" / f"{key}.json"
    cache = ValidatedArtifactCache(directory, "readwrite")
    payload = {"schema_version": "raw", "data": _payload()}
    try:
        cache.save_candidate(key, payload)

        assert cache.load(key) is None
        assert cache.load_candidate_with_status(key) == (payload, "hit")
    finally:
        candidate_path.unlink(missing_ok=True)

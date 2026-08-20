from __future__ import annotations

from dataclasses import fields

import pytest

from hara_agent.services.extraction import (
    DEFAULT_CONTEXT_CHARACTER_BUDGET,
    DeterministicEvidenceRetriever,
    FactRetrievalSpec,
)
from hara_agent.services.semantic import ItemEvidenceRouter, ItemSupplementAgent
from hara_agent.services.semantic.item_supplement_agent import RoutedDocumentBlocks


def block(block_id: str, text: str, *, location: str = "paragraph[1]", kind: str = "paragraph"):
    return {"block_id": block_id, "kind": kind, "location": location, "text": text}


def test_single_fact_keyword_and_unit_are_ranked():
    spec = FactRetrievalSpec(
        "performance.brake_response",
        ("brake response", "制动响应"),
        ("ms",),
        ("latency", "time", "时间"),
    )
    rankings = DeterministicEvidenceRetriever().rank([
        block("B-1", "general introduction"),
        block("B-2", "brake response time shall be bounded in ms"),
    ], (spec,))

    assert [item.block_id for item in rankings[spec.fact_type]] == ["B-2"]
    assert "unit:ms" in rankings[spec.fact_type][0].reasons


@pytest.mark.parametrize(
    ("alias", "text"),
    [
        ("steering response", "steering response time is specified in ms"),
        ("转向响应", "转向响应时间以ms为单位"),
    ],
)
def test_chinese_and_english_aliases_are_equivalent_retrieval_inputs(alias, text):
    spec = FactRetrievalSpec("steering.response", (alias,), ("ms",))
    routed = ItemEvidenceRouter().route(
        [block("B-1", text)], "project_evidence", required_specs=(spec,),
    )

    assert routed is not None
    assert routed.block_ids == ["B-1"]
    assert routed.source_blocks[0]["location"] == "paragraph[1]"


@pytest.mark.parametrize("unit", ["ms", "km/h", "m/s²"])
def test_engineering_unit_hints_contribute_to_score(unit):
    spec = FactRetrievalSpec("fact", ("parameter",), (unit,))
    ranking = DeterministicEvidenceRetriever().rank(
        [block("B-1", f"parameter limit uses {unit}")], (spec,),
    )["fact"]

    assert ranking[0].score > 12
    assert any(reason.startswith("unit:") for reason in ranking[0].reasons)


def test_unknown_fact_does_not_fabricate_a_source():
    spec = FactRetrievalSpec("unknown", ("nonexistent-unicorn-concept",))
    result = ItemEvidenceRouter().retrieve(
        [block("B-1", "brake response 50 ms")],
        "project_evidence",
        required_specs=(spec,),
    )

    assert result.routed is None
    assert result.diagnostics.facts[0].coverage_status == "NO_CANDIDATE"
    assert result.diagnostics.facts[0].selected_block_ids == ()


def test_retrieval_schema_cannot_contain_gold_value_or_locator():
    schema_fields = {item.name for item in fields(FactRetrievalSpec)}
    assert "expected_value" not in schema_fields
    assert "source_block_id" not in schema_fields
    with pytest.raises(TypeError):
        FactRetrievalSpec("brake", ("brake",), expected_value="50ms")


def test_project_evidence_budget_constant_remains_5000():
    assert DEFAULT_CONTEXT_CHARACTER_BUDGET == 5000


def test_supplement_locator_resolves_to_structured_source_ref():
    routed = RoutedDocumentBlocks(
        task="project_evidence",
        block_ids=["T-1-R-2"],
        text="[T-1-R-2] table[1].row[2]: brake response time",
        source_blocks=[{
            "block_id": "T-1-R-2",
            "kind": "table_row",
            "location": "table[1].row[2]",
            "text": "brake response time",
        }],
    )
    data = {
        "performance_parameters": [{
            "parameter": "brake response",
            "source_location": "T-1-R-2",
            "source_excerpt": "brake response time",
        }],
        "driver_contexts": [],
        "exposure_inputs": [],
    }

    unresolved = ItemSupplementAgent._attach_source_refs(data, routed, "ItemDef.docx")

    assert unresolved == 0
    assert data["performance_parameters"][0]["sources"] == [{
        "source_type": "item_definition",
        "source_id": "ItemDef.docx",
        "location": "table[1].row[2]",
        "excerpt": "brake response time",
    }]

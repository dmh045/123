from __future__ import annotations

import pytest
from dataclasses import asdict

from hara_agent.models import (
    FactProvenance,
    ItemDefinitionFacts,
    ReviewStatus,
    SourceRef,
    SpeedEnvelope,
)
from hara_agent.workflow.nodes.item_artifacts import _facts_from_dict


def source(location: str) -> SourceRef:
    return SourceRef("item_definition", "ItemDef.docx", location, "grounded")


def test_speed_envelopes_preserve_modes_and_build_aggregate_summary():
    facts = ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle motion control",
        speed_envelopes=[
            SpeedEnvelope("search", 0, 30, sources=[source("search")]),
            SpeedEnvelope("parking", 0, 5, sources=[source("parking")]),
            SpeedEnvelope("control", 0, 7, sources=[source("control")]),
        ],
        sources=[source("item")],
    )

    assert [item.speed_max_kph for item in facts.speed_envelopes] == [30, 5, 7]
    assert (facts.speed_min_kph, facts.speed_max_kph) == (0, 30)
    assert facts.speed_envelopes[1].provenance is FactProvenance.PROJECT_INPUT


def test_finalized_speed_envelope_requires_source_and_valid_bounds():
    with pytest.raises(ValueError, match="SourceRef"):
        SpeedEnvelope("parking", 0, 5, status=ReviewStatus.FINALIZED)
    with pytest.raises(ValueError, match="maximum"):
        SpeedEnvelope("parking", 7, 5)


def test_conflicting_same_scope_operating_modes_fail_closed():
    with pytest.raises(ValueError, match="conflicting scoped"):
        ItemDefinitionFacts(
            system_description="AVP",
            item_boundary="boundary",
            speed_envelopes=[
                SpeedEnvelope("Parking", 0, 5),
                SpeedEnvelope("parking", 0, 7),
            ],
            sources=[source("item")],
        )


def test_same_mode_can_preserve_distinct_contextual_envelopes():
    facts = ItemDefinitionFacts(
        system_description="AVP", item_boundary="boundary",
        speed_envelopes=[
            SpeedEnvelope(
                "Active", 0, 24, condition="parking-space search",
                sources=[source("table[12].row[2]")],
            ),
            SpeedEnvelope(
                "active", 0, 7, condition="vehicle control",
                sources=[source("table[14].row[15]")],
            ),
        ],
        sources=[source("item")],
    )

    assert len(facts.speed_envelopes) == 2
    assert [item.condition for item in facts.speed_envelopes] == [
        "parking-space search", "vehicle control",
    ]


def test_speed_envelope_survives_checkpoint_style_round_trip():
    original = ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="boundary",
        speed_envelopes=[SpeedEnvelope("parking", 0, 5, sources=[source("parking")])],
        sources=[source("item")],
    )

    restored = _facts_from_dict(asdict(original))

    assert restored.speed_envelopes[0].operating_mode == "parking"
    assert restored.speed_envelopes[0].sources[0].location == "parking"
    assert restored.speed_envelopes[0].provenance is FactProvenance.PROJECT_INPUT

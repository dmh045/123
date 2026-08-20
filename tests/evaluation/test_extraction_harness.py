from __future__ import annotations

import json
from pathlib import Path

from hara_agent.evaluation.models import ExpectedProjectFact, ExtractionEvaluationInput
from hara_agent.evaluation.stages import ExtractionEvaluationHarness
from hara_agent.models import ItemDefinitionFacts, SourceRef, SpeedEnvelope
from hara_agent.services.extraction import DocumentBlock, DocumentReader


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT / "src/hara_agent/evaluation/fixtures/extraction/legacy_avp/itemdef_grounded.json"
)


def test_real_itemdef_fixture_locators_are_grounded():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    artifact = DocumentReader().read(ROOT / fixture["source_path"])
    expected = [ExpectedProjectFact.from_dict(item) for item in fixture["facts"][:3]]
    source = SourceRef(
        "item_definition", "ItemDef.docx", "table[14].row[15]",
        "车速 | 搜索车位0-30kph，控车范围0-7kph",
    )
    facts = ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle motion control",
        speed_envelopes=[
            SpeedEnvelope("search", 0, 30, sources=[source]),
            SpeedEnvelope("control", 0, 7, sources=[source]),
            SpeedEnvelope(
                "parking", 0, 5,
                sources=[SourceRef(
                    "item_definition", "ItemDef.docx", "table[12].row[3]",
                    "2 | 泊车时的最大车速 | ≤5km/h | 标定值",
                )],
            ),
        ],
        sources=[source],
    )

    report = ExtractionEvaluationHarness().evaluate(ExtractionEvaluationInput(
        source_id=artifact.source_id,
        source_blocks=artifact.blocks,
        expected_facts=expected,
        project_facts=facts,
        routed_block_ids={"T-014-R-0015", "T-012-R-0003"},
    ))

    assert report["field_metrics"]["field_recall"] == 1.0
    assert report["field_metrics"]["grounded_field_recall"] == 1.0
    assert report["gap_counts"]["PRESENT_EXPLICIT"] == 3


def test_global_speed_collapse_is_reported_as_normalization_loss():
    blocks = [
        DocumentBlock("S-1", "row", "row[1]", "search 0-30 km/h"),
        DocumentBlock("S-2", "row", "row[2]", "parking 0-5 km/h"),
        DocumentBlock("S-3", "row", "row[3]", "control 0-7 km/h"),
    ]
    fixture = json.loads((
        ROOT / "src/hara_agent/evaluation/fixtures/extraction/synthetic/mode_specific_speed.json"
    ).read_text(encoding="utf-8"))
    expected = [ExpectedProjectFact.from_dict(item) for item in fixture["facts"]]
    facts = ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="boundary",
        speed_min_kph=0,
        speed_max_kph=30,
        sources=[SourceRef("synthetic", "synthetic", "row[1]", "search 0-30 km/h")],
    )

    report = ExtractionEvaluationHarness().evaluate(ExtractionEvaluationInput(
        source_id="synthetic",
        source_blocks=blocks,
        expected_facts=expected,
        project_facts=facts,
        routed_block_ids={"S-1", "S-2", "S-3"},
    ))

    assert report["gap_counts"]["NORMALIZATION_LOSS"] == 3


def test_router_and_extractor_gaps_are_distinguished():
    block = DocumentBlock("B-1", "row", "row[1]", "brake response 50 ms")
    base = {
        "fact_id": "brake.response",
        "field": "performance_parameters",
        "value": {"parameter": "brake_response", "value": 50},
        "unit": "ms",
        "route_task": "project_evidence",
        "source_block_ids": ["B-1"],
        "source": {
            "source_type": "synthetic", "source_id": "synthetic",
            "location": "row[1]", "excerpt": "brake response 50 ms",
        },
    }
    expected = ExpectedProjectFact.from_dict(base)
    facts = {
        "system_description": "AVP", "item_boundary": "boundary",
        "performance_parameters": [],
    }
    harness = ExtractionEvaluationHarness()

    routing = harness.evaluate(ExtractionEvaluationInput(
        "synthetic", [block], [expected], facts, routed_block_ids=set(),
    ))
    extractor = harness.evaluate(ExtractionEvaluationInput(
        "synthetic", [block], [expected], facts, routed_block_ids={"B-1"},
    ))

    assert routing["gap_counts"]["ROUTING_MISSED"] == 1
    assert extractor["gap_counts"]["EXTRACTOR_MISSED"] == 1

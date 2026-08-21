from __future__ import annotations

import json
from pathlib import Path

from hara_agent.evaluation.models import ExpectedProjectFact, ExtractionEvaluationInput
from hara_agent.evaluation.stages import ExtractionEvaluationHarness
from hara_agent.models import ItemDefinitionFacts, SourceRef
from hara_agent.services.extraction import DocumentBlock


ROOT = Path(__file__).resolve().parents[2]
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
        "field": "risk_facts",
        "value": {"fact_type": "brake.response", "parameter": "brake_response", "value": 50},
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
        "risk_facts": [],
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

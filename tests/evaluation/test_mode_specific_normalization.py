from __future__ import annotations

import json
from pathlib import Path

from hara_agent.evaluation.models import ExpectedProjectFact, ExtractionEvaluationInput
from hara_agent.evaluation.stages import ExtractionEvaluationHarness
from hara_agent.models import ItemDefinitionFacts, SourceRef, SpeedEnvelope
from hara_agent.services.analysis import ProjectFactResolver
from hara_agent.services.extraction import DocumentBlock


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT / "src/hara_agent/evaluation/fixtures/extraction/synthetic/mode_specific_speed.json"
)


def test_fresh_typed_fixture_has_zero_production_normalization_loss():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected = [ExpectedProjectFact.from_dict(item) for item in fixture["facts"]]
    blocks = [
        DocumentBlock("S-1", "row", "row[1]", "search 0-30 km/h"),
        DocumentBlock("S-2", "row", "row[2]", "parking 0-5 km/h"),
        DocumentBlock("S-3", "row", "row[3]", "control 0-7 km/h"),
    ]
    sources = {
        "search": SourceRef("synthetic", "synthetic", "row[1]", "search 0-30 km/h"),
        "parking": SourceRef("synthetic", "synthetic", "row[2]", "parking 0-5 km/h"),
        "control": SourceRef("synthetic", "synthetic", "row[3]", "control 0-7 km/h"),
    }
    facts = ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle motion control",
        speed_envelopes=[
            SpeedEnvelope("search", 0, 30, sources=[sources["search"]]),
            SpeedEnvelope("parking", 0, 5, sources=[sources["parking"]]),
            SpeedEnvelope("control", 0, 7, sources=[sources["control"]]),
        ],
        sources=list(sources.values()),
    )
    resolver = ProjectFactResolver()
    production_resolutions = [
        resolver.resolve_speed_context(facts, mode).to_dict()
        for mode in ("search", "parking", "control")
    ]

    report = ExtractionEvaluationHarness().evaluate(ExtractionEvaluationInput(
        source_id="synthetic",
        source_blocks=blocks,
        expected_facts=expected,
        project_facts=facts,
        routed_block_ids={"S-1", "S-2", "S-3"},
        production_speed_resolutions=production_resolutions,
        metadata={"fixture_age": "fresh", "checkpoint_kind": "typed_current"},
    ))

    assert report["gap_counts"].get("NORMALIZATION_LOSS", 0) == 0
    assert report["field_metrics"]["field_recall"] == 1.0
    assert report["field_metrics"]["grounded_field_recall"] == 1.0
    assert report["production_context"]["evaluated"] is True
    assert report["production_context"]["resolved_mode_count"] == 3
    assert report["production_context"]["normalization_loss_count"] == 0
    assert {
        item["operating_mode"]: item["observed_speed_kph"]
        for item in report["production_context"]["mode_results"]
    } == {"search": 30.0, "parking": 5.0, "control": 7.0}

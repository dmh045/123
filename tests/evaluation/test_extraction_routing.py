from __future__ import annotations

import json
from pathlib import Path

from hara_agent.evaluation.models import ExpectedProjectFact, ExtractionEvaluationInput
from hara_agent.evaluation.stages import ExtractionEvaluationHarness
from hara_agent.models import ItemDefinitionFacts, SourceRef
from hara_agent.services.extraction import DocumentReader
from hara_agent.services.semantic import ItemEvidenceRouter


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "src/hara_agent/evaluation/fixtures/extraction/legacy_avp/itemdef_grounded.json"


def test_schema_guided_router_eliminates_grounded_legacy_avp_routing_misses():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    artifact = DocumentReader().read(ROOT / fixture["source_path"])
    blocks = [block.__dict__ for block in artifact.blocks]
    router = ItemEvidenceRouter()
    routing = {
        task: router.retrieve(blocks, task)
        for task in ("odd_repair", "project_evidence")
    }
    source = SourceRef("item_definition", artifact.source_id, "document", "AVP")
    collapsed_project_facts = ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle motion control",
        speed_min_kph=0,
        speed_max_kph=30,
        performance_parameters=[],
        driver_contexts=[],
        sources=[source],
    )
    report = ExtractionEvaluationHarness().evaluate(ExtractionEvaluationInput(
        source_id=artifact.source_id,
        source_blocks=artifact.blocks,
        expected_facts=[ExpectedProjectFact.from_dict(item) for item in fixture["facts"]],
        project_facts=collapsed_project_facts,
        routed_block_ids={
            block_id
            for result in routing.values() if result.routed
            for block_id in result.routed.block_ids
        },
        routed_block_ids_by_task={
            task: set(result.routed.block_ids if result.routed else [])
            for task, result in routing.items()
        },
        routing_diagnostics_by_task={
            task: result.diagnostics.to_dict() for task, result in routing.items()
        },
    ))

    assert report["gap_counts"]["ROUTING_MISSED"] == 0
    assert report["gap_counts"]["NORMALIZATION_LOSS"] == 3
    assert report["gap_counts"]["EXTRACTOR_MISSED"] == 7
    brake = next(item for item in report["facts"] if item["fact_id"] == "performance.brake_response")
    assert brake["expected_source_locator"] == "table[13].row[7]"
    assert "T-013-R-0007" in brake["selected_block_ids"]
    assert brake["retrieval_diagnostics"]["coverage_status"] == "COVERED"
    assert set(report["field_metrics"]) >= {
        "field_recall", "grounded_field_recall", "source_ref_accuracy",
        "context_preservation_rate", "normalization_accuracy",
    }

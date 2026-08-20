from hara_agent.evaluation.models import (
    ExpectedProjectFact, ExtractionEvaluationInput, GapClassification,
)
from hara_agent.evaluation.stages import ExtractionEvaluationHarness
from hara_agent.models import SourceRef
from hara_agent.services.extraction import PERFORMANCE_PROJECT_FACT_SPECS, ProjectFactNormalizer


def test_compound_raw_semantic_is_atomicization_failed_not_extractor_missed():
    source = SourceRef("item_definition", "ItemDef.docx", "table[1].row[1]", "响应时间≤50ms")
    expected = ExpectedProjectFact(
        "performance.brake_response", "performance_parameters",
        {"parameter": "brake_response_time", "operator": "LE", "value": 50},
        "ms", source=source, source_block_ids=("B1",), route_task="project_evidence",
    )
    report = ExtractionEvaluationHarness().evaluate(ExtractionEvaluationInput(
        "ItemDef.docx",
        [{"block_id": "B1", "location": source.location, "text": source.excerpt}],
        [expected],
        {"performance_parameters": [{
            "parameter": "制动参数", "value": "正常≤3m/s2；紧急＞5m/s2；响应≤50ms",
            "sources": [source.__dict__],
        }]},
        routed_block_ids_by_task={"project_evidence": {"B1"}},
    ))
    assert report["facts"][0]["classification"] == GapClassification.ATOMICIZATION_FAILED.value


def test_atomicity_validator_rejects_compound_numeric_string_without_splitting():
    result = ProjectFactNormalizer().normalize(
        (PERFORMANCE_PROJECT_FACT_SPECS[2],),
        [{"fact_type": "performance.brake_response", "status": "FOUND",
          "parameter": "brake_response_time", "operator": "LE", "value": "≤50ms；≤150ms",
          "unit": "ms", "condition": "parking_brake_control", "source_block_id": "B1"}],
        [{"block_id": "B1", "location": "table[1].row[1]", "text": "泊车制动 响应≤50ms；转向≤150ms"}],
        "ItemDef.docx",
    )
    assert not result.numeric_constraints
    assert result.failures[0].code == "NON_ATOMIC_CANDIDATE"


def test_harness_distinguishes_typed_driver_facts_sharing_one_source_ref():
    source = SourceRef(
        "item_definition", "ItemDef.docx", "table[14].row[13]",
        "位姿状态 | 在驾驶位/不在驾驶位",
    )
    expected = ExpectedProjectFact(
        "driver.outside", "driver_context_facts",
        {"fact_type": "driver.outside", "driver_location": "OUTSIDE"},
        source=source, source_block_ids=("B1",), match_terms=("不在驾驶位",),
    )
    shared = [source.__dict__]
    report = ExtractionEvaluationHarness().evaluate(ExtractionEvaluationInput(
        "ItemDef.docx",
        [{"block_id": "B1", "location": source.location, "text": source.excerpt}],
        [expected],
        {"driver_context_facts": [
            {"fact_type": "driver.inside", "driver_location": "INSIDE", "sources": shared},
            {"fact_type": "driver.outside", "driver_location": "OUTSIDE", "sources": shared},
        ]},
    ))
    assert report["facts"][0]["classification"] == "PRESENT_EXPLICIT"

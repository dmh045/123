from hara_agent.infrastructure.llm import LLMResponse
from hara_agent.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from hara_agent.models import ProjectFactOutputType
from hara_agent.services.extraction import RequiredProjectFactSpec
from hara_agent.services.semantic import TargetedProjectFactExtractionAgent
from hara_agent.services.semantic.item_supplement_agent import RoutedDocumentBlocks


SPEED_PROJECT_FACT_SPECS = tuple(
    RequiredProjectFactSpec(
        f"speed.{name}", ProjectFactOutputType.SPEED_ENVELOPE,
        (alias,), ("km/h", "kph"), (name,),
        ("status", "operator", "value", "unit", "operating_mode", "source_block_id"),
    )
    for name, alias in (
        ("search", "搜索车位"),
        ("control", "控车范围"),
        ("parking", "泊车时的最大车速"),
    )
)


class TargetedClient:
    class Config:
        max_tokens = 4096

    config = Config()

    def __init__(self):
        self.request = None

    def complete_json(self, request):
        self.request = request
        return LLMResponse({"results": [
            {"fact_type": "speed.search", "status": "FOUND", "operator": "RANGE",
             "value": 0, "value_max": 30, "unit": "km/h", "operating_mode": "search",
             "source_block_id": "B1", "source_excerpt": "搜索车位0-30kph"},
            {"fact_type": "speed.control", "status": "FOUND", "operator": "RANGE",
             "value": 0, "value_max": 7, "unit": "km/h", "operating_mode": "control",
             "source_block_id": "B1", "source_excerpt": "控车范围0-7kph"},
            {"fact_type": "speed.parking", "status": "NOT_FOUND"},
        ]}, "test-model", "req-1")


def test_targeted_batch_has_explicit_per_spec_coverage_and_normalizes_found_only():
    client = TargetedClient()
    routed = RoutedDocumentBlocks(
        "project_evidence", ["B1"], "[B1] table: 搜索车位0-30kph，控车范围0-7kph",
        [{"block_id": "B1", "location": "table[1].row[1]", "kind": "table_row",
          "text": "搜索车位0-30kph，控车范围0-7kph"}],
    )
    result, audit = TargetedProjectFactExtractionAgent(client).extract(
        "speed", SPEED_PROJECT_FACT_SPECS, routed, "ItemDef.docx"
    )
    assert client.request.task == "extract_targeted_project_facts:speed"
    assert len(result.speed_envelopes) == 2
    assert [item["status"] for item in result.coverage] == [
        "FOUND", "FOUND", "NOT_FOUND_IN_EVIDENCE"
    ]
    assert result.coverage[-1]["source_truth_verified"] is False
    assert audit["llm_call_count"] == 1
    assert "0-30" not in client.request.user_prompt.split("evidence_blocks=")[0]


def test_targeted_project_fact_calls_use_extraction_thinking_policy():
    assert OpenAICompatibleClient._is_extraction_task(
        "extract_targeted_project_facts:speed"
    )


def test_speed_prompt_accepts_mode_transition_as_direct_evidence():
    client = TargetedClient()
    routed = RoutedDocumentBlocks(
        "project_evidence", ["B1"],
        "[B1] table[9].row[4]: Standby to Active | vehicle speed <=20km/h",
        [{
            "block_id": "B1",
            "location": "table[9].row[4]",
            "kind": "table_row",
            "text": "Standby to Active | vehicle speed <=20km/h",
        }],
    )

    TargetedProjectFactExtractionAgent(client).extract(
        "speed", (SPEED_PROJECT_FACT_SPECS[0],), routed, "ItemDef.docx"
    )

    assert "activation, entry, transition" in client.request.system_prompt
    assert TargetedProjectFactExtractionAgent.PROMPT_VERSION.endswith(
        "atomic-speed-shape"
    )
    assert "Never return a range string/list" in client.request.system_prompt
    assert "stationary branch and a moving upper-bound branch" in (
        client.request.system_prompt
    )

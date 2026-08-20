from __future__ import annotations

from dataclasses import asdict

from hara_agent.services.semantic import ItemDefinitionExtractionAgent
from hara_agent.workflow.state import HARAState, WorkflowStage


def extract_typed_item_definition(
    state: HARAState,
    agent: ItemDefinitionExtractionAgent,
) -> HARAState:
    facts, audit = agent.extract(
        str(state.item_definition.get("text", "")),
        str(state.item_definition.get("source_id", "")),
    )
    state.item_definition["typed"] = asdict(facts)
    state.stage = WorkflowStage.ITEM_DEFINITION
    state.record("typed_item_definition_extracted", **audit)
    if facts.status.value == "PENDING":
        state.pending_reviews.append({
            "field": "item_definition",
            "reason": "Item Definition/ODD语义抽取尚未完成工程确认",
        })
    return state

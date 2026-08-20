from __future__ import annotations

from dataclasses import asdict

from hara_agent.services.semantic import FunctionExtractionAgent
from hara_agent.workflow.state import HARAState, WorkflowStage


def extract_functions(state: HARAState, agent: FunctionExtractionAgent,
                      document_text: str, source_id: str) -> HARAState:
    functions, audit = agent.extract(document_text, source_id)
    state.functions = [asdict(item) for item in functions]
    pending_count = sum(item.status.value == "PENDING" for item in functions)
    if pending_count:
        state.pending_reviews.append({
            "field": "functions",
            "reason": f"{pending_count}个Function候选尚未完成工程确认",
        })
    state.stage = WorkflowStage.FUNCTIONS
    state.record("function_candidates_extracted", **audit)
    return state

import pytest

from hara_agent.services.analysis import UnresolvedProjectContextError
from hara_agent.workflow import HARAState, WorkflowStage
from hara_agent.workflow.semantic_graph import (
    SemanticWorkflowAgents,
    SemanticWorkflowInputs,
    build_semantic_frontend_graph,
)


class _GuidewordAgent:
    def __init__(self):
        self.calls = 0

    def assess(self, _function, _guidewords):
        self.calls += 1
        return [], {}


def test_project_context_preflight_blocks_before_guideword_llm_calls():
    guideword_agent = _GuidewordAgent()

    def reject(_state):
        raise UnresolvedProjectContextError(
            "parking", "available_speed_envelope_modes=['Active']",
        )

    graph = build_semantic_frontend_graph(
        SemanticWorkflowInputs(
            item_path="unused.docx",
            guidewords=["loss"],
            project_context_preflight=reject,
        ),
        SemanticWorkflowAgents(
            client=object(),
            guidewords=guideword_agent,
            malfunctions=object(),
            scenarios=object(),
        ),
    )
    state = HARAState(
        run_id="preflight-order",
        stage=WorkflowStage.FUNCTIONS,
        functions=[{
            "function_id": "F001",
            "name": "Test function",
            "description": "Test description",
            "output": "Test output",
            "sources": [],
            "status": "PENDING",
            "confidence": 1.0,
        }],
    )

    with pytest.raises(UnresolvedProjectContextError, match="Active"):
        graph.run(state)

    assert guideword_agent.calls == 0

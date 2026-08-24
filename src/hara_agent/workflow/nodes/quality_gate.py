from __future__ import annotations

from hara_agent.services.validation import ReleaseGateValidator
from hara_agent.workflow.state import HARAState, WorkflowStage


def pass_quality_gate(state: HARAState) -> HARAState:
    validation = ReleaseGateValidator().evaluate(state)
    if not validation.ready_for_release:
        state.record(
            "quality_gate_blocked",
            blockers=list(validation.blockers),
            checks=validation.checks,
        )
        return state
    state.record("quality_gate_passed")
    state.stage = WorkflowStage.RENDER
    return state

from __future__ import annotations

from pathlib import Path

from hara_agent.services.reporting import HARAExcelRenderer
from hara_agent.workflow.state import HARAState, WorkflowStage


def render_excel_report(
    state: HARAState,
    renderer: HARAExcelRenderer,
    template_path: str | Path,
    output_path: str | Path,
) -> HARAState:
    if state.stage is not WorkflowStage.RENDER:
        raise ValueError(f"报告渲染阶段错误: {state.stage.value}")
    output = renderer.render(state, template_path, output_path)
    state.record("excel_report_rendered", output_path=str(output))
    state.stage = WorkflowStage.COMPLETE
    return state

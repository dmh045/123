from dataclasses import replace
from pathlib import Path

from openpyxl import load_workbook

from hara_agent.services.reporting import HARAExcelRenderer
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow import HARAState, WorkflowStage


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "references" / "HARA_Template_AI_20260327.xlsx"


def test_renderer_uses_compiled_report_sheet_names():
    test_dir = ROOT / "output" / ".test-report"
    test_dir.mkdir(parents=True, exist_ok=True)
    template = test_dir / "renamed-template.xlsx"
    workbook = load_workbook(TEMPLATE)
    workbook["05_HARA"].title = "Risk Register"
    workbook["06_Safety Goal"].title = "Safety Objectives"
    workbook.save(template)
    workbook.close()

    method = TemplateRoleCompiler().compile_method(TEMPLATE)
    contract = replace(
        method.report_contract,
        hara_fields=tuple(
            replace(item, sheet="Risk Register")
            for item in method.report_contract.hara_fields
        ),
        safety_goal_fields=tuple(
            replace(item, sheet="Safety Objectives")
            for item in method.report_contract.safety_goal_fields
        ),
    )
    output = test_dir / "renamed-rendered.xlsx"
    state = HARAState(run_id="dynamic-report-contract", stage=WorkflowStage.RENDER)
    HARAExcelRenderer(contract).render(state, template, output, draft=True)

    rendered = load_workbook(output, read_only=True)
    try:
        assert rendered["Risk Register"]["A3"].value.startswith("DRAFT")
        assert rendered["Safety Objectives"]["A2"].value.startswith("DRAFT")
    finally:
        rendered.close()


def test_renderer_rejects_report_contract_with_duplicate_output_column():
    method = TemplateRoleCompiler().compile_method(TEMPLATE)
    mappings = list(method.report_contract.hara_fields)
    mappings[1] = replace(mappings[1], column_index=mappings[0].column_index)
    contract = replace(method.report_contract, hara_fields=tuple(mappings))

    import pytest
    with pytest.raises(ValueError, match="Duplicate HARA output column"):
        HARAExcelRenderer(contract).render(
            HARAState(run_id="invalid-report-contract"),
            TEMPLATE,
            ROOT / "output" / ".test-report" / "invalid.xlsx",
            draft=True,
        )

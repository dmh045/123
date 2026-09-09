from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from hara_agent.cli import main
from hara_agent.method_sources import YamlBaselineCompileError, YamlBaselineCompiler
from hara_agent.models import FunctionDefinition, ReviewStatus, SourceRef
from hara_agent.services.analysis import (
    ScenarioCoverageProposalService,
    ScenarioCoverageRuleService,
)
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow import ReviewArtifactWriter


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "method_assets/fusa_baseline_v1/manifest.yaml"
REPORT_TEMPLATE = ROOT / "references/HARA_Template_AI_20260327.xlsx"


def _report_contract():
    return TemplateRoleCompiler().compile_method(REPORT_TEMPLATE).report_contract


def _rule(*, rule_id="SCN_COV_001", status="APPROVED") -> dict:
    return {
        "coverage_rule_id": rule_id,
        "scope": {
            "function_selector": {"name": ["parking"]},
            "operating_modes": ["Active"],
        },
        "required_dimensions": ["WHERE", "EGO_DYNAMICS"],
        "optional_dimensions": ["ROAD"],
        "not_applicable_dimensions": ["OBJECT"],
        "provenance": {
            "classification": "NORMATIVE_METHOD_RULE",
            "source_asset": "approved_method_rule.yaml",
            "source_rule": "coverage.parking",
        },
        "rationale": "approved test coverage rule",
        "status": status,
        "approval": {
            "reviewer": "engineer",
            "reviewed_at": "2026-09-07T00:00:00Z",
        },
    }


def _baseline_with_coverage_rules(rules: list[dict]):
    root = ROOT / "runtime/test-scenario-coverage-baseline"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(ROOT / "method_assets/fusa_baseline_v1", root)
    path = root / "normalized/scenario_coverage_rules.yaml"
    base = yaml.safe_load(path.read_text(encoding="utf-8"))
    base["rules"] = rules
    path.write_text(yaml.safe_dump(base, allow_unicode=True, sort_keys=False), encoding="utf-8")
    manifest_path = root / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["asset_hashes"]["normalized/scenario_coverage_rules.yaml"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )
    return root, manifest_path


def _function(function_id="F05", *, name="parking completion") -> FunctionDefinition:
    return FunctionDefinition(
        function_id=function_id,
        name=name,
        output="engage P and apply EPB",
        preconditions=["AVP Active"],
        triggers=["parking completed"],
        odd_constraints=["parking area"],
        sources=[SourceRef("item", "ItemDef.docx", "p1", "function")],
        status=ReviewStatus.FINALIZED,
    )


def _bindings() -> dict[str, dict]:
    return {
        "WHERE": {"resolution_status": "RESOLVED"},
        "ROAD": {"resolution_status": "PENDING"},
        "EGO_DYNAMICS": {"resolution_status": "PENDING"},
        "OBJECT": {"resolution_status": "PENDING"},
    }


def test_compiler_exposes_only_approved_coverage_rules_and_runtime_uses_contract_only():
    root, manifest = _baseline_with_coverage_rules([
        _rule(), _rule(rule_id="SCN_COV_002", status="PROPOSED"),
    ])
    try:
        method = YamlBaselineCompiler().compile(manifest, report_contract=_report_contract())
        assert [item["coverage_rule_id"] for item in method.metadata["scenario_coverage_rules"]] == ["SCN_COV_001"]
        assert method.metadata["scenario_coverage_governance"]["proposed_count"] == 1

        runtime = ScenarioCoverageRuleService(method)
        (root / "normalized/scenario_coverage_rules.yaml").unlink()
        result = runtime.evaluate(_function(), _bindings(), "Active")
        assert result["status"] == "APPROVED_COVERAGE_RULE_APPLIED"
        assert result["dimensions"]["EGO_DYNAMICS"]["status"] == "PENDING"
        assert result["dimensions"]["OBJECT"]["status"] == "NOT_APPLICABLE"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_proposed_rule_does_not_change_runtime_and_non_executable_sources_stay_non_executable():
    root, manifest = _baseline_with_coverage_rules([_rule(status="PROPOSED")])
    try:
        method = YamlBaselineCompiler().compile(manifest, report_contract=_report_contract())
        runtime = ScenarioCoverageRuleService(method)
        assert runtime.has_approved_rules is False
        assert runtime.evaluate(_function(), _bindings(), "Active")["status"] == (
            "PENDING_NO_MATCHING_APPROVED_COVERAGE_RULE"
        )
        classes = {
            item["source_asset"]: item["classification"]
            for item in method.metadata["scenario_coverage_knowledge_sources"]
        }
        assert classes["raw/fm_scenario_templates.yaml"] == "SCENARIO_TEMPLATE_CONSTRAINT"
        assert classes["raw/domain_rules/avp_low_speed.yaml"] == "DOMAIN_RULE"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_compiler_rejects_approved_rule_with_non_executable_provenance():
    invalid = _rule()
    invalid["provenance"]["classification"] = "EXAMPLE_TEMPLATE"
    root, manifest = _baseline_with_coverage_rules([invalid])
    try:
        with pytest.raises(YamlBaselineCompileError, match="non-executable provenance"):
            YamlBaselineCompiler().compile(manifest, report_contract=_report_contract())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_coverage_proposals_are_deduplicated_and_keep_function_context_out_of_method_dimensions():
    method = YamlBaselineCompiler().compile(MANIFEST, report_contract=_report_contract())
    service = ScenarioCoverageProposalService(method)
    function = {
        "function_id": "F05", "name": "parking completion", "output": "engage P",
        "description": "", "preconditions": ["AVP Active"],
        "triggers": ["parking completed"], "odd_constraints": ["parking area"],
    }
    duplicate = {**function, "function_id": "F10"}
    payload = service.generate({"scenario_binding_coverage": {"dimensions": {
        "WHERE": {"context_values": [{"source_value": "parking area", "reason": "NO_EXPLICIT_METHOD_ALIAS"}], "reasons": {"NO_EXPLICIT_METHOD_ALIAS": 1}},
    }}}, {"function": [function, duplicate]})

    assert payload["proposal_count"] == 1
    assert payload["deduplicated_function_ids"] == ["F10"]
    proposal = payload["proposals"][0]
    assert proposal["coverage_status"] == "PENDING_NO_APPROVED_COVERAGE_RULE"
    assert "preconditions" in proposal["function_context"]
    assert "FUNCTION_PHASE" not in {item["dimension"] for item in proposal["candidate_dimensions"]}


def test_scenario_coverage_cli_writes_review_only_proposals(monkeypatch):
    root = ROOT / "runtime/test-scenario-coverage-review"
    shutil.rmtree(root, ignore_errors=True)
    try:
        writer = ReviewArtifactWriter("coverage-run", root)
        writer.record_function({
            "function_id": "F05", "name": "parking completion", "output": "engage P",
            "description": "", "preconditions": ["AVP Active"],
            "triggers": ["parking completed"], "odd_constraints": ["parking area"],
        })
        writer.write_scenario_binding_gaps({"dimensions": {
            "WHERE": {"context_values": [{"source_value": "parking area", "reason": "NO_EXPLICIT_METHOD_ALIAS"}], "reasons": {"NO_EXPLICIT_METHOD_ALIAS": 1}},
        }}, [])
        monkeypatch.setenv("HARA_REVIEW_ARTIFACT_DIR", str(root))

        assert main([
            "scenario-coverage", "--baseline", str(MANIFEST),
            "--review-run-id", "coverage-run", "--report-template", str(REPORT_TEMPLATE),
        ]) == 0
        payload = json.loads(
            (root / "coverage-run" / "scenario_coverage_proposals.json").read_text(
                encoding="utf-8"
            )
        )
        assert payload["automatic_approval_performed"] is False
        assert payload["proposal_count"] == 1
        assert payload["proposals"][0]["approval_status"] == "PROPOSED"
    finally:
        shutil.rmtree(root, ignore_errors=True)

from pathlib import Path
import shutil

from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.services.analysis import MethodContractParityAuditService
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow import ReviewArtifactWriter


ROOT = Path(__file__).resolve().parents[2]


def _payload() -> dict:
    template = TemplateRoleCompiler().compile_method(
        ROOT / "references/HARA_Template_AI_20260327.xlsx"
    )
    yaml = YamlBaselineCompiler().compile(
        ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=template.report_contract,
    )
    return MethodContractParityAuditService(template, yaml).generate()


def test_parity_audit_keeps_method_and_report_hashes_independent():
    payload = _payload()
    sources = payload["method_sources"]
    assert sources["template"]["method_hash"] != sources["yaml"]["method_hash"]
    assert sources["yaml"]["report_template_hash"] == sources["template"]["method_hash"]
    assert payload["contract_surface_comparison"]["report_template_is_yaml_method_source"] is False
    assert payload["provider_calls"] == 0
    assert payload["runtime_yaml_read"] == 0


def test_parity_audit_classifies_source_rules_without_claiming_executor_drift():
    payload = _payload()
    assert payload["contract_surface_comparison"]["shared_scoring_facade"] is True
    assert payload["contract_surface_comparison"]["dimension_specific_shared_api"] is False
    assert payload["severity_parity"]["classification"] == "METHOD_RULE_DIFFERENCE"
    assert payload["exposure_parity"]["classification"] == "SOURCE_SPECIFIC_DIFFERENCE"
    assert payload["controllability_parity"]["classification"] == "SOURCE_SPECIFIC_DIFFERENCE"
    assert payload["executor_drift_findings"] == []


def test_asil_common_nonzero_cells_match_and_zero_shortcut_is_source_difference():
    asil = _payload()["asil_parity"]
    assert asil["nonzero_common_cells"] == 36
    assert asil["nonzero_exact_cells"] == 36
    assert asil["synthetic_fixture"]["template"] == asil["synthetic_fixture"]["yaml"]
    assert asil["zero_shortcut"]["template_values"] == ["NA"]
    assert asil["zero_shortcut"]["yaml_values"] == ["QM"]
    assert asil["zero_shortcut"]["classification"] == "METHOD_RULE_DIFFERENCE"


def test_parity_inventory_marks_ftti_and_risk_input_model_without_rule_mutation():
    payload = _payload()
    assert payload["template_contract_inventory"]["surface"]["ftti"]["status"] == "ABSENT"
    assert payload["yaml_contract_inventory"]["surface"]["ftti"]["status"] == "PARTIAL"
    assert payload["ftti_parity"]["yaml"] == "SOURCE_PRESENT_RUNTIME_UNUSED"
    assert payload["risk_input_model_parity"]["finding_code"] == "LEGACY_INPUT_MODEL_DIVERGENCE"


def test_parity_writer_persists_matrix_and_source_inventories():
    review_root = ROOT / "runtime" / "review"
    run_id = "method-contract-parity-unit"
    artifact_dir = review_root / run_id
    shutil.rmtree(artifact_dir, ignore_errors=True)
    try:
        writer = ReviewArtifactWriter(run_id, review_root)
        writer.write_method_contract_parity_audit(_payload())

        assert (artifact_dir / "method_contract_parity_audit.json").is_file()
        assert (artifact_dir / "template_method_contract_audit.json").is_file()
        assert (artifact_dir / "yaml_method_contract_audit.json").is_file()
    finally:
        shutil.rmtree(artifact_dir, ignore_errors=True)

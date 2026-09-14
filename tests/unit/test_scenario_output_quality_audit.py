from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.models import (
    EvidenceValue, ReviewStatus, RiskAssessment, ScenarioCandidate,
    ScenarioFeasibilityAssessment,
)
from hara_agent.services.reporting import (
    EngineeringReportTextMapper, OfflineReportRebuilder,
    ScenarioOutputQualityAuditService, load_report_schema,
)
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow.scenario_causal_revalidation import (
    ScenarioCausalRevalidationRunner,
)
from hara_agent.workflow.state import HARAState


ROOT = Path(__file__).resolve().parents[2]


def _method():
    report = TemplateRoleCompiler().compile_method(
        ROOT / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    return YamlBaselineCompiler().compile(
        ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )


def _pending(reason: str) -> EvidenceValue:
    return EvidenceValue("", ReviewStatus.PENDING, review_reason=reason)


def _child(label: str, scenario_id: str, road_id: str) -> ScenarioCandidate:
    bindings = {
        "WHERE": {
            "resolution_status": "RESOLVED", "atom_id": "SO010",
            "canonical_atom_id": "SO010", "method_value": "SO010 | Garage",
            "atom_provenance": {"source_asset": "raw/vda702_atoms.yaml", "source_rule": "atoms[33]"},
            "selection_origin": "ODD_CONSTRAINED_CATALOG", "selection_reason": "source bounded",
        },
        "ROAD": {
            "resolution_status": "RESOLVED", "atom_id": road_id,
            "canonical_atom_id": road_id, "method_value": f"{road_id} | Road",
            "atom_provenance": {"source_asset": "raw/vda702_atoms.yaml", "source_rule": "test"},
            "selection_origin": "ODD_CONSTRAINED_CATALOG", "selection_reason": "source bounded",
        },
        "EGO_ACTION": {
            "resolution_status": "RESOLVED", "atom_id": "FV010",
            "canonical_atom_id": "PH005", "method_value": "FV010 | Parking in/out",
            "atom_provenance": {"source_asset": "raw/vda702_atoms.yaml", "source_rule": "atoms[90]"},
            "selection_origin": "HAZARD_CAUSAL_SEMANTIC_CANDIDATE", "selection_reason": "hazard bounded",
        },
        "EGO_X_ROAD": {"resolution_status": "PENDING", "unresolved_reason": "METHOD_IRRELEVANCE_NOT_PROVEN"},
        "TRAFFIC_PATTERN": {"resolution_status": "PENDING", "unresolved_reason": "METHOD_IRRELEVANCE_NOT_PROVEN"},
        "EGO_DYNAMICS": {
            "resolution_status": "RESOLVED", "atom_id": "FA001",
            "canonical_atom_id": "FA001", "method_value": "FA001 | Drive v <= 130 kph",
            "atom_provenance": {"source_asset": "raw/vda702_atoms.yaml", "source_rule": "atoms[121]"},
            "selection_origin": "DIRECT_PROJECT_BINDING", "selection_reason": "speed envelope",
        },
        "OBJECT": {
            "resolution_status": "RESOLVED", "atom_id": "CN_other_road_users",
            "canonical_atom_id": "CN_other_road_users",
            "method_value": "CN_other_road_users | Other road users (cyclists, pedestrians)",
            "atom_provenance": {"source_asset": "raw/vda702_atoms.yaml", "source_rule": "atoms[184]"},
            "selection_origin": "HAZARD_CAUSAL_SEMANTIC_CANDIDATE", "selection_reason": "explicit pedestrian",
        },
    }
    return ScenarioCandidate(
        scenario_id=scenario_id, operating_scenario="garage",
        situational_description="fixture", situational_detailing="fixture",
        operating_mode="active", atomic_variant=f"scenario_synthesis:{label}",
        source_scenario_id="SCN-PARENT", status=ReviewStatus.FINALIZED,
        facts={
            "method_scenario_dimensions": bindings,
            "scenario_atom_ids": ["SO010", road_id, "PH005", "FA001", "CN_other_road_users"],
        },
        analysis_instance={
            "malfunction_id": "MF-1", "hazardous_event_id": "HE-1",
            "semantic_group_id": "SYNTH-1", "validation_status": "VALIDATED",
        },
    )


def test_quality_audit_measures_diversity_provenance_and_partial_causal_status():
    children = [
        _child("typical", "SCN-1", "FB005"),
        _child("boundary", "SCN-2", "FB002"),
        _child("extreme", "SCN-3", "FB003"),
    ]
    risks = [
        RiskAssessment(
            assessment_id=f"RA-{index}", scenario_id=child.scenario_id,
            severity=_pending("causal"), exposure=_pending("causal"),
            controllability=_pending("causal"), asil=_pending("causal"),
            malfunction_id="MF-1", hazardous_event="泊入时与行人碰撞",
        )
        for index, child in enumerate(children, start=1)
    ]
    state = HARAState(
        run_id="synthesis", scenarios=children, risk_results=risks,
        malfunctions=[{
            "malfunction_id": "MF-1", "function_id": "F-1",
            "description": "泊入时行人识别丢失", "functional_effect": "未制动",
            "vehicle_level_hazard": "车辆与行人碰撞", "component_category": "perception",
        }],
    )
    causal_trace = {"audits": [{"item_salvage_audit": [{
        "parsed_assessment": {
            "malfunction_id": "MF-1", "scenario_id": "SCN-1",
            "final_retain": True, "hazardous_event": "泊入时与行人碰撞",
        },
    }]}]}
    audit = ScenarioOutputQualityAuditService(_method()).audit(
        state, causal_trace=causal_trace,
    )

    assert audit["summary"]["children_audited"] == 3
    assert audit["summary"]["causal_delta_status"] == {
        "CAUSAL_REVALIDATED": 1, "CAUSAL_REVALIDATION_REQUIRED": 2,
    }
    diversity = audit["diversity_metrics"]
    assert diversity["unique_complete_canonical_atom_sets"] == 3
    assert diversity["unique_atom_sets_excluding_where_road"] == 1
    assert diversity["parent_groups_where_all_three_differ_only_by_where_road"] == 1
    assert diversity["scenario_variant_low_diversity_groups"] == 1
    assert audit["hazard_consistency"]["hazard_object_mismatches"] == 0
    assert audit["hazard_consistency"]["hazard_action_mismatches"] == 0
    assert audit["hazard_consistency"]["PH014_selected"] == 0
    assert audit["hazard_consistency"]["FB007_selected"] == 0
    assert audit["records"][0]["selected_dimension_bindings"]["OBJECT"][
        "source_provenance"
    ]["source_rule"] == "atoms[184]"


def test_report_projection_does_not_change_partial_causal_resume_recovery(tmp_path):
    completed = _child("typical", "SCN-COMPLETE", "FB005")
    deferred = _child("boundary", "SCN-DEFERRED", "FB002")
    deferred.analysis_instance["malfunction_id"] = "MF-2"
    assessment = ScenarioFeasibilityAssessment(
        malfunction_id="MF-1", scenario_id="SCN-COMPLETE",
        physically_feasible=True, functionally_relevant=True,
        causally_relevant=True, risk_dimensions_changed=[],
        rationale="source-linked", hazardous_event="bounded event",
        status=ReviewStatus.FINALIZED, confidence=0.9,
    )
    trace = tmp_path / "causal_revalidation_provider_trace.json"
    trace.write_text(json.dumps({
        "artifact_version": "scenario-causal-revalidation-provider-trace-v1",
        "source_run_id": "source-run", "run_id": "target-run",
        "completed_malfunctions": 1, "target_malfunctions": 2,
        "audits": [{
            "malfunction_id": "MF-1",
            "item_salvage_audit": [{
                "scenario_id": "SCN-COMPLETE",
                "parsed_assessment": assessment.to_dict(),
            }],
        }],
        "deferred_malfunctions": {"MF-2": {"error_type": "quota_exceeded"}},
    }), encoding="utf-8")
    groups = {"MF-1": [completed], "MF-2": [deferred]}
    digest_before = hashlib.sha256(trace.read_bytes()).hexdigest()
    recovered_before = ScenarioCausalRevalidationRunner._recover_completed(
        trace, source_run_id="source-run", target_run_id="target-run",
        candidates_by_malfunction=groups,
    )

    EngineeringReportTextMapper().scenario(completed)

    recovered_after = ScenarioCausalRevalidationRunner._recover_completed(
        trace, source_run_id="source-run", target_run_id="target-run",
        candidates_by_malfunction=groups,
    )
    assert hashlib.sha256(trace.read_bytes()).hexdigest() == digest_before
    assert list(recovered_before) == list(recovered_after) == ["MF-1"]
    assert recovered_after["MF-1"][0][0].scenario_id == "SCN-COMPLETE"
    assert set(groups) - set(recovered_after) == {"MF-2"}


def test_offline_rebuild_preserves_checkpoint_and_causal_trace(tmp_path):
    child = _child("typical", "SCN-1", "FB005")
    risk = RiskAssessment(
        assessment_id="RA-1", scenario_id="SCN-1",
        severity=_pending("causal"), exposure=_pending("causal"),
        controllability=_pending("causal"), asil=_pending("causal"),
        malfunction_id="MF-1", hazardous_event="泊入时与行人碰撞",
    )
    state = HARAState(
        run_id="offline-report", method_contract={"source_kind": "YAML_BASELINE"},
        functions=[{"function_id": "F-1", "name": "AVP", "output": "parking"}],
        malfunctions=[{
            "malfunction_id": "MF-1", "function_id": "F-1", "guideword": "Loss",
            "description": "泊入时行人识别丢失", "functional_effect": "未制动",
            "vehicle_level_hazard": "车辆与行人碰撞", "component_category": "perception",
        }],
        scenarios=[child], risk_results=[risk],
    )
    checkpoint = tmp_path / "offline-report.checkpoint.json"
    checkpoint.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    causal_trace = tmp_path / "causal_revalidation_provider_trace.json"
    causal_trace.write_text(json.dumps({
        "audits": [{"item_salvage_audit": [{"parsed_assessment": {
            "malfunction_id": "MF-1", "scenario_id": "SCN-1",
            "final_retain": True,
        }}]}],
    }), encoding="utf-8")
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (checkpoint, causal_trace)
    }
    output = tmp_path / "report.xlsx"
    rebuilt = OfflineReportRebuilder(load_report_schema()).rebuild(
        checkpoint_path=checkpoint,
        method_baseline_path=ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_style_template_path=ROOT / "references/HARA_Template_AI_20260327.xlsx",
        output_path=output, review_root=tmp_path / "review",
        causal_trace_path=causal_trace, audit_output_dir=tmp_path / "report-audit",
    )
    assert rebuilt == output.resolve()
    assert output.is_file()
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (checkpoint, causal_trace)
    } == before

    from openpyxl import load_workbook

    workbook = load_workbook(output, read_only=False, data_only=False)
    try:
        assert "停车场" in workbook["04_HARA"]["G6"].value
        assert "Other road users" not in workbook["04_HARA"]["G6"].value
        assert "EGO_ACTION=" not in workbook["04_HARA"]["H6"].value
        assert workbook["04A_Scenario Detail"].column_dimensions["K"].width > 30
        assert workbook["99_Audit"].column_dimensions["K"].width > 20
        assert workbook["99_Audit"]["G5"].value == "METHOD_VALID — CAUSAL_REVALIDATED"
    finally:
        workbook.close()

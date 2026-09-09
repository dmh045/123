from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from hara_agent.cli import main
from hara_agent.method_sources import YamlBaselineCompileError, YamlBaselineCompiler
from hara_agent.services.analysis import (
    MethodAtomResolver,
    ScenarioAliasProposalService,
)
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow import ReviewArtifactWriter


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "method_assets/fusa_baseline_v1/manifest.yaml"
REPORT_TEMPLATE = ROOT / "references/HARA_Template_AI_20260327.xlsx"


def _report_contract():
    return TemplateRoleCompiler().compile_method(REPORT_TEMPLATE).report_contract


def _baseline_with_aliases(aliases: list[dict]):
    root = ROOT / "runtime/test-scenario-alias-baseline"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(ROOT / "method_assets/fusa_baseline_v1", root)
    aliases_path = root / "normalized/scenario_aliases.yaml"
    aliases_path.write_text(yaml.safe_dump({
        "schema_version": "1.0", "source": "test", "aliases": aliases,
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    manifest_path = root / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["asset_hashes"]["normalized/scenario_aliases.yaml"] = hashlib.sha256(
        aliases_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8",
    )
    return root, manifest_path


def _approved_alias(*, alias_id="SCN_ALIAS_001", dimension="EGO_ACTION", atom_id="FA005"):
    return {
        "alias_id": alias_id,
        "dimension": dimension,
        "source_term": "项目慢速",
        "canonical_target": {"atom_id": atom_id},
        "mapping_type": "EXACT_TERMINOLOGY",
        "status": "APPROVED",
        "provenance": {"source_asset": "ItemDef.docx", "source_location": "p1"},
        "rationale": "reviewed test mapping",
        "approval": {"reviewer": "engineer", "reviewed_at": "2026-09-07T00:00:00Z"},
    }


def test_compiler_only_exposes_approved_aliases_and_resolver_uses_contract_only():
    aliases = [
        _approved_alias(),
        {**_approved_alias(alias_id="SCN_ALIAS_002"), "source_term": "proposed", "status": "PROPOSED"},
        {**_approved_alias(alias_id="SCN_ALIAS_003"), "source_term": "rejected", "status": "REJECTED"},
    ]
    root, manifest = _baseline_with_aliases(aliases)
    try:
        method = YamlBaselineCompiler().compile(manifest, report_contract=_report_contract())
        assert [item["alias_id"] for item in method.metadata["scenario_aliases"]] == ["SCN_ALIAS_001"]
        resolver = MethodAtomResolver(method)
        assert resolver.resolve("项目慢速", "EGO_ACTION").reason == "APPROVED_GOVERNED_ALIAS"
        assert resolver.resolve("proposed", "EGO_ACTION").status == "PENDING"
        assert resolver.resolve("rejected", "EGO_ACTION").status == "PENDING"

        # The resolver has already received its MethodContract.  Changing the
        # temporary source asset cannot change that runtime object.
        (root / "normalized/scenario_aliases.yaml").unlink()
        assert resolver.resolve("项目慢速", "EGO_ACTION").atom_id == "FA005"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_compiler_rejects_approved_alias_target_dimension_mismatch():
    root, manifest = _baseline_with_aliases([
        _approved_alias(dimension="WHERE", atom_id="FA005"),
    ])
    try:
        with pytest.raises(YamlBaselineCompileError, match="DIMENSION_MISMATCH"):
            YamlBaselineCompiler().compile(manifest, report_contract=_report_contract())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _proposal_service() -> ScenarioAliasProposalService:
    method = SimpleNamespace(metadata={
        "template_hash": "test-method",
        "scenario_atom_catalog": [
            {"atom_id": "A1", "label": "one", "aliases": ["same"], "filled_dimensions": ["OBJECT"]},
            {"atom_id": "A2", "label": "two", "aliases": ["same"], "filled_dimensions": ["OBJECT"]},
            {"atom_id": "P1", "label": "parking", "aliases": [], "filled_dimensions": ["WHERE"]},
        ],
        "scenario_aliases": [],
    })
    return ScenarioAliasProposalService(method)


def test_alias_proposals_deduplicate_and_never_choose_ambiguous_target():
    service = _proposal_service()
    payload = service.generate({"gaps": [
        {"scenario_id": "SCN-1", "dimension": "OBJECT", "source_value": "same", "reason": "NO_EXPLICIT_METHOD_ALIAS"},
        {"scenario_id": "SCN-2", "dimension": "OBJECT", "source_value": "same", "reason": "NO_EXPLICIT_METHOD_ALIAS"},
    ]}, {"scenario_candidate": [], "malfunction": []})

    assert payload["unique_unresolved_terms"] == 1
    assert payload["proposals"] == []
    assert payload["findings"][0]["resolution"] == "AMBIGUOUS"
    assert payload["findings"][0]["candidate_targets"] == ["A1", "A2"]


def test_active_and_parking_slot_are_not_forced_into_method_aliases():
    service = _proposal_service()
    payload = service.generate({"gaps": [
        {"scenario_id": "SCN-1", "dimension": "EGO_ACTION", "source_value": "Active", "reason": "NO_EXPLICIT_METHOD_ALIAS"},
        {"scenario_id": "SCN-1", "dimension": "WHERE", "source_value": "垂直车位", "reason": "NO_EXPLICIT_METHOD_ALIAS"},
    ]}, {"scenario_candidate": [{"scenario_id": "SCN-1", "operating_mode": "Active"}], "malfunction": []})

    by_term = {item["source_term"]: item for item in payload["findings"]}
    assert by_term["Active"]["resolution"] == "SEMANTIC_LEVEL_MISMATCH"
    assert by_term["垂直车位"]["resolution"] == "NO_GOVERNED_ALIAS_CANDIDATE"
    assert payload["proposals"] == []


def test_scenario_aliases_cli_writes_review_only_proposal_artifact(monkeypatch):
    root = ROOT / "runtime/test-scenario-alias-review"
    shutil.rmtree(root, ignore_errors=True)
    try:
        writer = ReviewArtifactWriter("alias-run", root)
        writer.write_scenario_binding_gaps({}, [{
            "scenario_id": "SCN-1", "dimension": "WHERE",
            "source_value": "unmapped", "reason": "NO_EXPLICIT_METHOD_ALIAS",
        }])
        monkeypatch.setenv("HARA_REVIEW_ARTIFACT_DIR", str(root))

        assert main([
            "scenario-aliases", "--baseline", str(MANIFEST),
            "--review-run-id", "alias-run", "--report-template", str(REPORT_TEMPLATE),
        ]) == 0
        payload = json.loads(
            (root / "alias-run" / "scenario_alias_proposals.json").read_text(
                encoding="utf-8"
            )
        )
        assert payload["automatic_approval_performed"] is False
        assert payload["findings"][0]["resolution"] == "NO_GOVERNED_ALIAS_CANDIDATE"
    finally:
        shutil.rmtree(root, ignore_errors=True)

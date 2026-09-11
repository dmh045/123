from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.models import FunctionDefinition
from hara_agent.services.analysis import (
    ExposureDimensionCoverageAuditService, ExposureDimensionCoverageService,
    ExposureMethodExecutor,
)
from hara_agent.template import TemplateRoleCompiler


ROOT = Path(__file__).resolve().parents[2]


def _method():
    report = TemplateRoleCompiler().compile_method(
        ROOT / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    return YamlBaselineCompiler().compile(
        ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )


def _function() -> FunctionDefinition:
    return FunctionDefinition(
        function_id="F01", name="parking entry", output="show AVP entry",
        description="parking function", preconditions=["parking"],
    )


def _approved_rule() -> dict:
    return {
        "coverage_rule_id": "EXPOSURE_COVERAGE_001",
        "scope": {"function_selector": {"name": ["parking"]}, "operating_modes": ["Active"]},
        "required_dimensions": ["WHERE", "EGO_DYNAMICS"],
        "optional_dimensions": ["ROAD", "EGO_X_ROAD", "TRAFFIC_PATTERN"],
        "not_applicable_dimensions": ["EGO_ACTION", "OBJECT"],
        "provenance": {
            "classification": "NORMATIVE_METHOD_RULE",
            "source_asset": "approved_method_rule.yaml",
            "source_rule": "coverage.parking",
        },
        "approval": {"reviewer": "engineer", "reviewed_at": "2026-09-08T00:00:00Z"},
    }


def _with_rule():
    method = _method()
    return replace(method, metadata={
        **method.metadata, "scenario_coverage_rules": [_approved_rule()],
    })


def test_explicit_method_rule_is_the_only_source_of_required_and_not_applicable():
    decision = ExposureDimensionCoverageService(_with_rule()).decide(
        assessment_key="MF-1::SC-1", function=_function(), operating_mode="Active",
    )
    statuses = {item.dimension: item.status.value for item in decision.dimensions}

    assert decision.coverage_status.value == "RESOLVED"
    assert statuses["WHERE"] == "REQUIRED"
    assert statuses["OBJECT"] == "NOT_APPLICABLE"
    assert statuses["ROAD"] == "OPTIONAL"


def test_absent_method_coverage_never_promotes_missing_or_existing_facts():
    service = ExposureDimensionCoverageService(_method())
    decision = service.decide(
        assessment_key="MF-1::SC-1", function=_function(), operating_mode="Active",
    )

    assert decision.coverage_status.value == "PENDING_METHOD_SEMANTICS"
    assert {item.status.value for item in decision.dimensions} == {"PENDING_METHOD_SEMANTICS"}
    assert "平面车位" not in {item.dimension for item in decision.dimensions}
    assert "Active" not in {item.dimension for item in decision.dimensions}


def test_readiness_is_evaluated_only_after_resolved_coverage():
    pending = ExposureDimensionCoverageService(_method()).decide(
        assessment_key="MF-1::SC-1", function=_function(), operating_mode="Active",
    )
    assert ExposureDimensionCoverageService.readiness(
        pending, bindings={}, scenario_atom_ids={"FA001"}, component_domain_resolved=True,
    )["exposure_input_status"] == "NOT_EVALUABLE"

    resolved = ExposureDimensionCoverageService(_with_rule()).decide(
        assessment_key="MF-1::SC-1", function=_function(), operating_mode="Active",
    )
    incomplete = ExposureDimensionCoverageService.readiness(
        resolved,
        bindings={"WHERE": {"resolution_status": "RESOLVED", "atom_id": "VD001"}},
        scenario_atom_ids={"VD001"}, component_domain_resolved=True,
    )
    assert incomplete["exposure_input_status"] == "BLOCKED"
    ready = ExposureDimensionCoverageService.readiness(
        resolved,
        bindings={
            "WHERE": {"resolution_status": "RESOLVED", "atom_id": "VD001"},
            "EGO_DYNAMICS": {"resolution_status": "RESOLVED", "atom_id": "FA001"},
        },
        scenario_atom_ids={"VD001", "FA001"}, component_domain_resolved=True,
    )
    assert ready["exposure_input_status"] == "READY"


def test_shared_scenario_is_recorded_per_malfunction_without_a_scenario_cache():
    payload = ExposureDimensionCoverageAuditService(_method()).generate({
        "function": [{"function_id": "F01", "name": "parking entry", "output": "show AVP entry"}],
        "malfunction": [
            {"malfunction_id": "MF-HMI", "function_id": "F01", "component_category": "hmi"},
            {"malfunction_id": "MF-COMP", "function_id": "F01", "component_category": "computing"},
        ],
        "scenario_feasibility": [
            {"malfunction_id": "MF-HMI", "scenario_id": "SC-1", "function_id": "F01", "status": "FINALIZED", "physically_feasible": True, "functionally_relevant": True, "causally_relevant": True, "hazardous_event": "event one"},
            {"malfunction_id": "MF-COMP", "scenario_id": "SC-1", "function_id": "F01", "status": "FINALIZED", "physically_feasible": True, "functionally_relevant": True, "causally_relevant": True, "hazardous_event": "event two"},
        ],
    })

    records = payload["assessment_coverage_records"]
    assert [item["assessment_key"] for item in records] == ["MF-HMI::SC-1", "MF-COMP::SC-1"]
    assert payload["coverage_granularity"]["status"] == "UNRESOLVED"
    assert payload["summary"] == {"RESOLVED": 0, "PENDING_METHOD_SEMANTICS": 2}
    assert payload["runtime_yaml_read"] == 0


def test_historical_partial_atom_scoring_is_not_a_coverage_rule():
    method = _method()
    executor = ExposureMethodExecutor()
    results = {
        "missing_where": executor.lookup(
                {"component_category": "computing", "scenario_atom_ids": ["PU002"], "atoms_coupling": "independent"},
            method.structured_risk_method.exposure,
        ),
        "missing_object": executor.lookup(
            {"component_category": "computing", "scenario_atom_ids": ["VD001"], "atoms_coupling": "independent"},
            method.structured_risk_method.exposure,
        ),
        "ego_dynamics_only": executor.lookup(
            {"component_category": "computing", "scenario_atom_ids": ["FA001"], "atoms_coupling": "independent"},
            method.structured_risk_method.exposure,
        ),
        "multiple_atoms": executor.lookup(
            {"component_category": "computing", "scenario_atom_ids": ["VD001", "FA001", "PU002"], "atoms_coupling": "independent"},
            method.structured_risk_method.exposure,
        ),
    }
    payload = ExposureDimensionCoverageAuditService(method).generate({})

    assert {item["status"].value for item in results.values()} == {"FINALIZED"}
    assert payload["historical_consumer_behavior"]["classification"] == "HISTORICAL_PARTIAL_INPUT_SCORING"
    assert payload["historical_consumer_behavior"]["normative_authority"] is False

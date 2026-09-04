from __future__ import annotations

from pathlib import Path

from hara_agent.contracts import CalculationStatus
from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.services.analysis import StructuredRiskScoringService
from hara_agent.template import TemplateRoleCompiler


ROOT = Path(__file__).resolve().parents[2]


def _service() -> StructuredRiskScoringService:
    report = TemplateRoleCompiler().compile_method(
        ROOT / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    method = YamlBaselineCompiler().compile(
        ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )
    return StructuredRiskScoringService(method)


def _scenario(delta_v: float) -> dict:
    return {
        "scenario_id": "SC-1", "malfunction_id": "MF-1",
        "delta_v_kph": delta_v, "collision_type": "FRONTAL",
        "road_user_type": "VEHICLE", "component_category": "sensor_camera",
        "scenario_atom_ids": ["SO010", "PH005"],
        "ttc_s": 4.1, "driver_in_vehicle": True,
        "remote_intervention_available": False,
        "other_road_user_avoidance_possible": False,
    }


def test_structured_severity_boundaries_and_c_profile():
    service = _service()
    assert service.score(_scenario(3.9), "hazard")["severity"]["severity_score"] == "S0"
    scored = service.score(_scenario(4.0), "hazard")
    assert scored["severity"]["severity_score"] == "S1"
    assert scored["exposure"]["exposure_score"] in {"E1", "E2", "E3", "E4"}
    assert scored["controllability"]["controllability_score"] == "C1"


def test_structured_c_override_precedes_ttc():
    service = _service()
    scenario = _scenario(20.0)
    scenario.update({
        "ttc_s": 10.0, "driver_in_vehicle": False,
        "remote_intervention_available": False,
        "other_road_user_avoidance_possible": False,
    })
    scored = service.score(scenario, "hazard")
    assert scored["controllability"]["controllability_score"] == "C3"
    assert scored["controllability"]["engineering_rule_id"] == "driver_outside_no_intervention"


def test_structured_exposure_missing_atom_is_pending_input():
    service = _service()
    scenario = _scenario(20.0)
    scenario["scenario_atom_ids"] = []
    scored = service.score(scenario, "hazard")
    assert scored["exposure"]["calculation_status"] == CalculationStatus.PENDING_INPUT.value

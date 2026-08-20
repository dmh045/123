import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from logic_checkers.scenario_checker import test_scenario_checker


class P006ScenarioTruthSourceTests(unittest.TestCase):
    phase2 = ROOT / "output" / "p006_scen_hazevent_output.json"
    phase3 = ROOT / "output" / "p009_scoring_sg_output.json"
    template = ROOT / "references" / "HARA_Template_AI_20260327.xlsx"
    temp_root = ROOT / "tmp"

    @classmethod
    def setUpClass(cls):
        missing = [path for path in (cls.phase2, cls.phase3) if not path.is_file()]
        if missing:
            raise unittest.SkipTest(
                "legacy generated fixtures are absent: " + ", ".join(str(path) for path in missing)
            )

    @staticmethod
    def _leaf_items(value):
        if isinstance(value, list):
            for item in value:
                yield from P006ScenarioTruthSourceTests._leaf_items(item)
        elif isinstance(value, dict):
            if "scenario_id" in value:
                yield value
            else:
                for item in value.values():
                    yield from P006ScenarioTruthSourceTests._leaf_items(item)

    def test_phase2_uses_catalog_ids_for_all_scenarios_and_hazard_events(self):
        data = json.loads(self.phase2.read_text(encoding="utf-8"))
        catalog = data["scenario_catalog"]
        self.assertTrue(catalog)

        for section in ("function_scenarios", "refined_function_scenarios", "hazard_events"):
            items = list(self._leaf_items(data[section]))
            self.assertEqual(70, len(items))
            self.assertTrue(all(item["scenario_id"] in catalog for item in items))

        result = test_scenario_checker(
            str(self.phase2), template_path=str(self.template)
        )
        self.assertTrue(result["passed"], result["errors"])

    def test_catalog_contains_quantitative_avp_parameters(self):
        data = json.loads(self.phase2.read_text(encoding="utf-8"))
        facts = list(data["scenario_catalog"].values())
        required = {
            "ego_speed_kph", "relative_speed_kph", "longitudinal_acceleration_mps2",
            "steering_angle_deg", "steering_angle_status", "object_type",
            "relative_distance", "parameter_sources",
        }
        self.assertTrue(all(required.issubset(item) for item in facts))
        self.assertTrue(all(item["ego_speed_kph"] <= 5 for item in facts))
        self.assertTrue(any("儿童" in item["object_type"] for item in facts))
        self.assertTrue(any("柱体" in item["object_type"] or "墙体" in item["object_type"] for item in facts))
        self.assertTrue(any(item["steering_control_error_deg"] == 0.1 for item in facts))

    def test_phase3_results_reference_same_catalog(self):
        data = json.loads(self.phase3.read_text(encoding="utf-8"))
        catalog = data["scenario_catalog"]
        for section in (
            "severity_results", "exposure_results", "controllability_results",
            "asil_results", "safety_goals", "safe_states",
        ):
            items = list(self._leaf_items(data[section]))
            self.assertEqual(70, len(items))
            self.assertTrue(all(item["scenario_id"] in catalog for item in items))

    def test_checker_blocks_denormalized_scenario_drift(self):
        self.temp_root.mkdir(exist_ok=True)
        invalid_path = self.temp_root / "p006_invalid_scenario.json"
        try:
            data = json.loads(self.phase2.read_text(encoding="utf-8"))
            function = next(iter(data["function_scenarios"].values()))
            scenarios = next(iter(function.values()))
            scenarios[0]["scenario_facts"]["vehicle_speed"] = "99 km/h"
            invalid_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            result = test_scenario_checker(
                str(invalid_path), template_path=str(self.template)
            )

            self.assertFalse(result["passed"])
            self.assertTrue(any(
                error.get("type") == "scenario_fact_drift"
                for error in result["errors"]
            ))
        finally:
            invalid_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

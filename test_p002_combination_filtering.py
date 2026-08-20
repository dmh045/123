import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "references" / "HARA_Template_AI_20260327.xlsx"
sys.path.insert(0, str(ROOT / "scripts"))

from malfunction_engine import MalfunctionEngine
from scen_hazevent_engine import LocalScenarioChecker, ScenHazEventEngine
from logic_checkers.scenario_checker import ScenarioChecker
from scoring_sg_engine import (
    ASILDeterminer,
    ControllabilityReasoningEngine,
    ExposureReasoningEngine,
    ScoringSGEngine,
    SafetyStateGenerator,
    SeverityReasoningEngine,
)


AVP_FUNCTIONS = [
    "输出制动扭矩",
    "输出驱动扭矩",
    "输出转向扭矩",
    "开启功能",
    "激活功能",
    "退出功能",
    "关闭功能",
    "输出驻车制动",
    "报警提示",
]


class P002CombinationFilteringTests(unittest.TestCase):
    def _phase1(self):
        engine = MalfunctionEngine()
        engine.context = {
            "functions": AVP_FUNCTIONS,
            "function_outputs": {function: function for function in AVP_FUNCTIONS},
            "function_guidewords": {},
            "function_malfunctions": {},
            "function_hazards": {},
        }
        engine._step3_guidewords()
        engine._step4_malfunction()
        return engine

    def test_all_guidewords_are_assessed_but_only_applicable_items_continue(self):
        engine = self._phase1()
        assessments = engine.context["function_guideword_assessments"]
        malfunctions = engine.context["function_malfunctions"]

        self.assertEqual(9, len(assessments))
        self.assertTrue(all(len(items) == 14 for items in assessments.values()))
        self.assertEqual(70, sum(len(items) for items in malfunctions.values()))
        self.assertTrue(all(
            item["analysis_status"] == "applicable"
            for items in malfunctions.values() for item in items
        ))
        self.assertTrue(any(
            item["status"] == "not_applicable"
            for items in assessments.values() for item in items
        ))

    def test_avp_phase2_uses_risk_relevant_candidates_without_hard_limit_three(self):
        phase1 = self._phase1()
        engine = ScenHazEventEngine()
        engine.input_data = {
            "functions": AVP_FUNCTIONS,
            "function_malfunctions": phase1.context["function_malfunctions"],
        }
        engine.subsystem = "avp"
        engine.scenarios_library = [{
            "scenario": "Highway / Motorway (高速公路)",
            "operating_scenario": "Highway / Motorway (高速公路)",
            "vehicle_speed": "80 < v ≤ 130 km/h",
        }]

        result = engine._step6_combine_scenarios(
            phase1.context["function_malfunctions"]
        )

        self.assertTrue(result["success"])
        self.assertGreater(result["total_combinations"], 70)
        self.assertLessEqual(result["total_combinations"], 70 * 6)
        found_above_soft_target = False
        for malfunctions in result["function_scenarios"].values():
            for scenarios in malfunctions.values():
                self.assertGreaterEqual(len(scenarios), 1)
                self.assertLessEqual(len(scenarios), 6)
                found_above_soft_target |= len(scenarios) > 3
                self.assertIn("AVP Parking Lot", scenarios[0]["_main_scenario"])
                self.assertNotIn("Highway", scenarios[0]["scenario_summary"])
                self.assertIn("driver_state", scenarios[0])
                risk_keys = {
                    (
                        item.get("scenario_variant"), item.get("object_type"),
                        item.get("collision_geometry"), item.get("target_speed_kph"),
                    )
                    for item in scenarios
                }
                self.assertEqual(len(scenarios), len(risk_keys))
        self.assertTrue(found_above_soft_target)

    def test_motion_control_failure_keeps_fixed_object_as_fourth_risk_class(self):
        engine = ScenHazEventEngine()
        engine.subsystem = "avp"
        scenarios = engine._build_avp_candidate_scenarios(
            "输出转向扭矩", "输出转向扭矩丢失"
        )

        self.assertGreater(len(scenarios), 3)
        fixed = [item for item in scenarios if "fixed-object" in item["collision_geometry"]]
        self.assertEqual(1, len(fixed))
        self.assertEqual("柱体或墙体", fixed[0]["object_type"])

    def test_low_speed_parking_risk_is_scored_instead_of_rejected(self):
        scenario = {
            "operating_scenario": "AVP Parking Lot / Garage (代客泊车停车场)",
            "vehicle_state": "AVP低速泊车运动",
            "vehicle_speed": "5 km/h",
            "weather_conditions": "正常天气",
            "road_surface_conditions": "干燥铺装路面",
        }

        for checker in (ScenarioChecker(), LocalScenarioChecker()):
            result = checker.check_malfunction_scenario_combo("刹车系统失效", scenario)
            self.assertTrue(result["is_valid"], result.get("reasons"))

    def test_drive_torque_degradation_is_not_collapsed_to_availability_only(self):
        engine = ScenHazEventEngine()
        engine.subsystem = "avp"

        drive_scenarios = engine._build_avp_candidate_scenarios(
            "输出驱动扭矩", "输出驱动扭矩过小"
        )
        activation_scenarios = engine._build_avp_candidate_scenarios(
            "开启功能", "开启功能未输出"
        )

        self.assertGreater(len(drive_scenarios), 1)
        self.assertTrue(any(
            item.get("scenario_variant") == "controlled_standstill"
            for item in drive_scenarios
        ))
        self.assertTrue(any(
            item.get("scenario_variant") != "controlled_standstill"
            for item in drive_scenarios
        ))
        self.assertEqual(1, len(activation_scenarios))
        self.assertEqual("controlled_standstill", activation_scenarios[0]["scenario_variant"])

    def test_rejected_combination_keeps_auditable_reason(self):
        engine = ScenHazEventEngine()
        engine.subsystem = "avp"
        engine._select_relevant_scenarios = lambda *_args: [{
            "scenario": "Highway",
            "operating_scenario": "Highway",
            "odd_valid": False,
            "scenario_variant": "out_of_odd",
        }]

        result = engine._step6_combine_scenarios({
            "输出制动扭矩": [{
                "guideword": "loss",
                "malfunction": "输出制动扭矩丢失",
                "analysis_status": "applicable",
            }]
        })

        self.assertEqual(0, result["total_combinations"])
        self.assertEqual(1, result["rejected_combinations"])
        self.assertEqual("out_of_odd", result["rejected_combination_audit"][0]["scenario_variant"])
        self.assertIn("ODD", result["rejected_combination_audit"][0]["reasons"][0])

    def test_avp_hazard_event_keeps_guideword_and_does_not_reuse_epb_text(self):
        engine = ScenHazEventEngine()
        engine.subsystem = "avp"

        scenario = engine._build_avp_candidate_scenarios(
            "输出制动扭矩", "制动扭矩未输出"
        )[0]
        event = engine._infer_hazard_event(
            "制动扭矩未输出", "", "", scenario,
            subsystem="avp", function="输出制动扭矩", guideword="loss",
        )

        self.assertIn("功能完全丧失", event)
        self.assertIn("行人", event)
        self.assertNotIn("Rolling when continuously holding", event)
        self.assertNotIn("Wiper", event)

    def test_avp_risk_and_availability_cases_do_not_collapse_to_same_asil(self):
        phase2 = ScenHazEventEngine()
        phase2.subsystem = "avp"
        risk_scenario = phase2._build_avp_candidate_scenarios(
            "输出制动扭矩", "制动扭矩未输出"
        )[0]
        risk_scenario["refined_scenario"] = risk_scenario["scenario"]
        risk_event = phase2._infer_hazard_event(
            "制动扭矩未输出", "", "", risk_scenario,
            subsystem="avp", function="输出制动扭矩", guideword="loss",
        )
        drive_scenarios = phase2._build_avp_candidate_scenarios(
            "输出驱动扭矩", "驱动扭矩未输出"
        )
        drive_safe = next(
            item for item in drive_scenarios
            if item["scenario_variant"] == "controlled_standstill"
        )
        drive_risk = next(
            item for item in drive_scenarios
            if item["scenario_variant"] == "near_pedestrian"
        )
        availability_event = phase2._infer_hazard_event(
            "驱动扭矩未输出", "", "", drive_safe,
            subsystem="avp", function="输出驱动扭矩", guideword="loss",
        )
        drive_risk_event = phase2._infer_hazard_event(
            "驱动扭矩未输出", "", "", drive_risk,
            subsystem="avp", function="输出驱动扭矩", guideword="loss",
        )

        severity = SeverityReasoningEngine()
        exposure = ExposureReasoningEngine()
        control = ControllabilityReasoningEngine()
        asil = ASILDeterminer(str(TEMPLATE))

        risk_s = severity.analyze(risk_event, risk_scenario)["severity_score"]
        risk_e = exposure.analyze(risk_scenario)["exposure_score"]
        risk_c = control.analyze(risk_event, "avp", "loss")["controllability_score"]
        availability_s = severity.analyze(availability_event, drive_safe)["severity_score"]
        drive_risk_s = severity.analyze(drive_risk_event, drive_risk)["severity_score"]

        self.assertEqual("S1", risk_s)
        self.assertEqual("E4", risk_e)
        self.assertEqual("C3", risk_c)
        self.assertNotEqual("QM", asil.determine(risk_s, risk_e, risk_c))
        self.assertEqual("S0", availability_s)
        self.assertNotEqual("S0", drive_risk_s)

    def test_post_scoring_grouping_keeps_different_object_or_sec_results(self):
        phase2 = ScenHazEventEngine()
        phase2.subsystem = "avp"
        scenarios = phase2._build_avp_candidate_scenarios(
            "输出制动扭矩", "制动扭矩未输出"
        )[:3]
        severity = [{"severity_score": "S1"}] * 3
        exposure = [
            {"exposure_score": "E4"},
            {"exposure_score": "E2"},
            {"exposure_score": "E1"},
        ]
        control = [{"controllability_score": "C3"}] * 3
        asil = [
            {"ASIL": "B"}, {"ASIL": "QM"}, {"ASIL": "QM"},
        ]
        goals = [
            {"sg_id": "SG_AVP_02"},
            {"safety_goal": "NA"},
            {"safety_goal": "NA"},
        ]

        selected, groups = ScoringSGEngine._select_risk_representatives(
            scenarios, severity, exposure, control, asil, goals
        )

        self.assertEqual([0, 1, 2], selected)
        self.assertEqual(3, len(groups))

    def test_post_scoring_grouping_keeps_distinct_motion_or_driver_control(self):
        base = {
            "object_type": "接近车辆",
            "collision_geometry": "frontal/rear vehicle",
            "maneuver": "低速泊车运动",
            "parking_direction": "车尾泊入",
            "ego_speed_kph": 5.0,
            "target_speed_kph": 10.0,
            "driver_state": "驾驶员位于车外，无法直接接管",
        }
        scenarios = [
            dict(base, scenario_id="SCN_A"),
            dict(base, scenario_id="SCN_B", target_speed_kph=30.0),
            dict(base, scenario_id="SCN_C", driver_state="驾驶员位于车内，可直接接管"),
        ]
        severity = [{"severity_score": "S2"}] * 3
        exposure = [{"exposure_score": "E3"}] * 3
        control = [{"controllability_score": "C2"}] * 3
        asil = [{"ASIL": "A"}] * 3
        goals = [{"sg_id": "SG_AVP_01"}] * 3

        selected, groups = ScoringSGEngine._select_risk_representatives(
            scenarios, severity, exposure, control, asil, goals
        )

        self.assertEqual([0, 1, 2], selected)
        self.assertEqual(3, len(groups))

    def test_avp_safe_state_is_item_specific(self):
        state = SafetyStateGenerator().infer(
            "避免AVP车辆非预期运动", "输出驻车制动",
            subsystem="avp", guideword="loss",
        )
        self.assertIn("驻车", state)
        self.assertNotIn("Wiper", state)
        self.assertNotIn("雨刮", state)


if __name__ == "__main__":
    unittest.main()

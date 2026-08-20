import argparse
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from hara_controller import HARAController
from report_generator import HARAReportGenerator


class P013QualityGateTests(unittest.TestCase):
    malfunction_json = ROOT / "output" / "p006_malfunction_output.json"
    scenario_json = ROOT / "output" / "p006_scen_hazevent_output.json"
    scoring_json = ROOT / "output" / "p009_scoring_sg_output.json"
    grouped_scenario_json = ROOT / "output" / "p014_scen_hazevent_output.json"
    grouped_scoring_json = ROOT / "output" / "p014_scoring_sg_output.json"
    risk_aware_scenario_json = ROOT / "output" / "p015_scen_hazevent_output.json"
    risk_aware_scoring_json = ROOT / "output" / "p015_scoring_sg_output.json"
    template = ROOT / "references" / "HARA_Template_AI_20260327.xlsx"
    temp_root = ROOT / "tmp"

    @classmethod
    def setUpClass(cls):
        required = (
            cls.malfunction_json, cls.scenario_json, cls.scoring_json,
            cls.grouped_scenario_json, cls.grouped_scoring_json,
            cls.risk_aware_scenario_json, cls.risk_aware_scoring_json,
        )
        missing = [path for path in required if not path.is_file()]
        if missing:
            raise unittest.SkipTest(
                "legacy generated fixtures are absent: " + ", ".join(str(path) for path in missing)
            )
        cls.temp_root.mkdir(exist_ok=True)

    def _generator(self, scoring_path=None):
        return HARAReportGenerator(
            template_path=str(self.template),
            malfunction_json=str(self.malfunction_json),
            scen_hazevent_json=str(self.scenario_json),
            scoring_json=str(scoring_path or self.scoring_json),
        )

    def test_valid_three_phase_outputs_pass_quality_gate(self):
        result = self._generator().validate_three_phase_quality_gate()
        self.assertTrue(result["analysis_valid"])
        self.assertEqual("pass", result["status"])
        self.assertEqual([], result["errors"])

    def test_bounded_multi_scenario_avp_output_passes_quality_gate(self):
        generator = HARAReportGenerator(
            template_path=str(self.template),
            malfunction_json=str(self.malfunction_json),
            scen_hazevent_json=str(self.grouped_scenario_json),
            scoring_json=str(self.grouped_scoring_json),
        )
        result = generator.validate_three_phase_quality_gate()
        self.assertTrue(result["analysis_valid"])
        self.assertEqual("pass", result["status"])

    def test_more_than_three_distinct_risk_scenarios_pass_quality_gate(self):
        generator = HARAReportGenerator(
            template_path=str(self.template),
            malfunction_json=str(self.malfunction_json),
            scen_hazevent_json=str(self.risk_aware_scenario_json),
            scoring_json=str(self.risk_aware_scoring_json),
        )
        result = generator.validate_three_phase_quality_gate()

        self.assertTrue(result["analysis_valid"])
        self.assertEqual("pass", result["status"])

    def test_invalid_scoring_blocks_report_and_creates_no_workbook(self):
        invalid_scoring = self.temp_root / "p013_invalid_scoring.json"
        output = self.temp_root / "p013_must_not_exist.xlsx"
        try:
            data = json.loads(self.scoring_json.read_text(encoding="utf-8"))
            data.pop("safety_goals", None)
            invalid_scoring.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            result = self._generator(invalid_scoring).generate_hara_report(
                template_excel_path=str(self.template),
                output_excel_path=str(output),
            )

            self.assertFalse(result["success"])
            self.assertFalse(result["analysis_valid"])
            self.assertEqual("blocked_by_quality_gate", result["status"])
            self.assertFalse(output.exists())
        finally:
            invalid_scoring.unlink(missing_ok=True)
            output.unlink(missing_ok=True)

    def test_controller_does_not_auto_skip_failed_phase_checker(self):
        args = argparse.Namespace(
            item=None,
            template=str(self.template),
            output=str(ROOT / "output" / "unused.xlsx"),
            malfunction_json=None,
            resume=False,
        )
        controller = HARAController(args)
        controller.PROGRESS_FILE = ROOT / "output" / "unused_progress.json"
        controller.progress.malfunction_completed = True
        controller.progress.malfunction_json = str(self.malfunction_json)
        controller.progress.scen_hazevent_json = str(self.scenario_json)
        controller._run_engine = Mock(return_value=(True, ""))
        controller._run_checker = Mock(return_value=(False, {
            "status": "fail",
            "errors": [{"type": "forced_failure"}],
        }))
        controller._save_progress = Mock()

        result = controller.run_scen_hazevent_phase()

        self.assertFalse(result)
        self.assertFalse(controller.progress.scen_hazevent_completed)
        self.assertTrue(any(
            item.get("reason") == "checker_failed_quality_gate_blocked"
            for item in controller.progress.warnings
        ))

    def test_controller_does_not_auto_skip_failed_engine(self):
        args = argparse.Namespace(
            item=None,
            template=str(self.template),
            output=str(ROOT / "output" / "unused.xlsx"),
            malfunction_json=None,
            resume=False,
        )
        controller = HARAController(args)
        controller.PROGRESS_FILE = ROOT / "output" / "unused_progress.json"
        controller._run_engine = Mock(return_value=(False, ""))
        controller._ask_retry = Mock(return_value=False)
        controller._save_progress = Mock()

        result = controller.run_malfunction_phase()

        self.assertFalse(result)
        self.assertFalse(controller.progress.malfunction_completed)
        self.assertTrue(any(
            item.get("reason") == "engine_failed_quality_gate_blocked"
            for item in controller.progress.warnings
        ))


if __name__ == "__main__":
    unittest.main()

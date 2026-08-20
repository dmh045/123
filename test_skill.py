#!/usr/bin/env python3
"""Focused regression tests for the HARA v13 safety delivery gates."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from ftti import (  # noqa: E402
    FTTIEstimator,
    FTTI_FINALIZED,
    FTTI_NEEDS_REVIEW,
    aggregate_sg_ftti,
)
from logic_checkers.scoring_checker import ScoringChecker  # noqa: E402
from report_generator import HARAReportGenerator  # noqa: E402
from openpyxl import Workbook  # noqa: E402


class FTTITest(unittest.TestCase):
    def test_longitudinal_candidate_is_not_ttc(self):
        result = FTTIEstimator().evaluate({
            "scenario_id": "SCN_1",
            "relative_distance": "0.5 m",
            "relative_speed_kph": 5.0,
            "collision_geometry": "longitudinal fixed-object",
        }, "碰撞", "B")
        self.assertEqual(result["ftti_status"], FTTI_NEEDS_REVIEW)
        self.assertAlmostEqual(result["ftti_value_s"], 0.289, places=3)
        self.assertAlmostEqual(result["ttc_screening_s"], 0.36, places=2)
        self.assertNotEqual(result["ftti_value_s"], result["ttc_screening_s"])

    def test_non_collision_requires_timing_review(self):
        result = FTTIEstimator().evaluate({
            "scenario_id": "SCN_2", "collision_geometry": "none",
            "object_type": "无近距离冲突目标",
        }, "", "A")
        self.assertIsNone(result["ftti_value_s"])
        self.assertEqual(result["ftti_requirement"], "NEEDS_REVIEW")

    def test_approved_explicit_value_is_finalized(self):
        result = FTTIEstimator().evaluate({
            "scenario_id": "SCN_3", "ftti_seconds": 0.42,
            "ftti_status": "APPROVED", "ftti_source": "SYS-SAF-001 v2",
        }, "碰撞", "C")
        self.assertEqual(result["ftti_status"], FTTI_FINALIZED)
        self.assertEqual(result["ftti_requirement"], "≤ 0.42 s")

    def test_safety_goal_uses_most_stringent_candidate(self):
        result = aggregate_sg_ftti([
            {"ftti_value_s": 0.60, "ftti_status": FTTI_NEEDS_REVIEW},
            {"ftti_value_s": 0.42, "ftti_status": FTTI_NEEDS_REVIEW},
        ])
        self.assertEqual(result["ftti_value_s"], 0.42)
        self.assertEqual(result["ftti_status"], FTTI_NEEDS_REVIEW)


class QualityGateTest(unittest.TestCase):
    def setUp(self):
        self.experience = {
            "subsystem": "AVP",
            "05_HARA": [{
                "ASIL": "B", "SG-ID": "SG_1", "Safety Goal": "避免碰撞",
                "Safe state": "制动至静止",
            }],
            "06_Safety Goal": [{"SG-ID": "SG_1"}],
        }

    def test_experience_formal_blocks_pending_ftti(self):
        result = HARAReportGenerator().validate_experience_quality_gate(self.experience)
        self.assertFalse(result["can_generate"])
        self.assertEqual(result["status"], "blocked")

    def test_experience_draft_is_watermark_eligible(self):
        result = HARAReportGenerator(allow_draft=True).validate_experience_quality_gate(self.experience)
        self.assertTrue(result["can_generate"])
        self.assertFalse(result["analysis_valid"])
        self.assertEqual(result["status"], "draft")
        self.assertTrue(all(item["severity"] == "WARNING" for item in result["warnings"]))

    def test_cross_subsystem_pollution_is_always_error(self):
        polluted = dict(self.experience)
        polluted["05_HARA"] = [dict(self.experience["05_HARA"][0], **{"Safe state": "Wiper automatic mode"})]
        result = HARAReportGenerator(allow_draft=True).validate_experience_quality_gate(polluted)
        self.assertFalse(result["can_generate"])
        self.assertTrue(any(item["type"] == "cross_subsystem_contamination" for item in result["errors"]))

    def test_scoring_ftti_review_demotes_only_in_draft(self):
        data = {
            "asil_results": {"F": {"M": {"loss": [{"scenario_id": "S1", "ASIL": "B"}]}}},
            "ftti_results": {"F": {"M": {"loss": [{
                "scenario_id": "S1", "ASIL": "B", "ftti_status": "NEEDS_REVIEW",
                "ftti_value_s": 0.42, "ftti_requirement": "≤ 0.42 s",
                "formula_id": "longitudinal_t1_t2_draft",
            }]}}},
            "safety_goal_catalog": {},
        }
        formal = ScoringChecker("unused")
        formal.data = data
        formal._check_ftti_results()
        self.assertTrue(formal.errors)
        draft = ScoringChecker("unused", allow_draft=True)
        draft.data = data
        draft._check_ftti_results()
        self.assertFalse(draft.errors)
        self.assertTrue(draft.warnings)

    def test_draft_watermark_preserves_template_page_header(self):
        workbook = Workbook()
        hara = workbook.active
        hara.title = "05_HARA"
        safety_goal = workbook.create_sheet("06_Safety Goal")
        for sheet in (hara, safety_goal):
            sheet.oddHeader.left.text = 'Template &"Arial" header'

        generator = HARAReportGenerator(allow_draft=True)
        generator._apply_draft_watermark(workbook, {"warnings": [{}]})

        self.assertEqual(hara.oddHeader.left.text, 'Template &"Arial" header')
        self.assertIsNone(hara.oddHeader.center.text)
        self.assertIsNone(safety_goal.oddHeader.center.text)
        self.assertIsNone(hara.sheet_properties.pageSetUpPr)
        self.assertIsNone(safety_goal.sheet_properties.pageSetUpPr)
        self.assertIn("DRAFT / DIAGNOSTIC", hara["A1"].value)
        self.assertIn("DRAFT / DIAGNOSTIC", safety_goal["A2"].value)


if __name__ == "__main__":
    unittest.main(verbosity=2)

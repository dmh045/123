import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from document_parsers.word_processor import WordProcessor
from logic_checkers.function_checker import FunctionChecker
from malfunction_engine import MalfunctionEngine


class P001FunctionSemanticsTests(unittest.TestCase):
    item = ROOT / "input" / "ItemDef.docx"
    template = ROOT / "references" / "HARA_Template_AI_20260327.xlsx"

    def test_avp_function_table_is_split_into_semantic_fields(self):
        result = WordProcessor().extract_item_definition(str(self.item))
        functions = result["functions"]

        self.assertEqual(9, len(functions))
        self.assertEqual(11, len(result["item_semantics"]["odd_constraints"]))
        self.assertGreaterEqual(len(result["item_semantics"]["state_transitions"]), 10)
        self.assertGreaterEqual(
            len(result["item_semantics"]["fallback_behaviors"]), 1
        )
        for function in functions:
            self.assertLessEqual(len(function["name"]), 40)
            self.assertTrue(function["output"])
            self.assertNotEqual(function["description"], function["output"])
            self.assertIsInstance(function["preconditions"], list)
            self.assertIsInstance(function["triggers"], list)
            self.assertTrue(function["preconditions"])
            self.assertTrue(function["triggers"])
            self.assertIsInstance(function["expected_effects"], list)
            self.assertIsInstance(function["consequences"], list)
            self.assertIsInstance(function["odd_constraints"], list)
            self.assertIsInstance(function["fallback_behaviors"], list)
            self.assertIsInstance(function["source_table"], int)
            self.assertIsInstance(function["source_row"], int)

    def test_function_checker_rejects_copied_description_as_output(self):
        invalid = [{
            "name": "输出制动扭矩",
            "description": "MDC发送制动请求并控制车辆减速",
            "output": "MDC发送制动请求并控制车辆减速",
            "preconditions": [],
            "triggers": [],
            "expected_effects": [],
            "consequences": [],
            "odd_constraints": [],
            "fallback_behaviors": [],
            "source": "table",
            "source_table": 5,
            "source_row": 2,
        }]

        result = FunctionChecker().check(invalid)

        self.assertFalse(result["is_valid"])
        self.assertIn(
            "Output与完整功能描述高度重复，未完成语义拆分",
            result["invalid_functions"][0]["validation_reasons"],
        )

    def test_phase1_keeps_function_semantics_without_reintroducing_explosion(self):
        result = MalfunctionEngine(
            item_definition_path=str(self.item),
            excel_template_path=str(self.template),
        ).run()

        self.assertEqual(9, result["metadata"]["total_functions"])
        self.assertEqual(126, result["metadata"]["total_guideword_candidates"])
        self.assertEqual(70, result["metadata"]["total_malfunctions"])
        self.assertEqual(9, len(result["functions_raw"]))
        self.assertEqual(11, len(result["item_semantics"]["odd_constraints"]))
        for function in result["functions_raw"]:
            self.assertNotEqual(function["description"], function["output"])


if __name__ == "__main__":
    unittest.main()

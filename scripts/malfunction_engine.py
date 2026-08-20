#!/usr/bin/env python3
"""
Malfunction Analysis Engine
HARA V12 - Phase1: Generate malfunction and hazard data
"""

import os
import re
import json
import logging
import argparse
import sys
from typing import Dict, List, Any, Optional
from pathlib import Path

script_dir = Path(__file__).parent
logger = logging.getLogger(__name__)


class HazardRef:
    """Hazard Reference Database"""

    def __init__(self):
        self.hazard_ref = {}
        self.new_hazards = []
        self.entries = []
        try:
            ref_path = script_dir / "HazardRef.json"
            if ref_path.exists():
                with open(ref_path, "r", encoding="utf-8") as f:
                    self.hazard_ref = json.load(f)
                self.entries = self.hazard_ref.get("entries", [])
                logger.info(f"HazardRef加载完成: {len(self.entries)} 条危害记录")
            else:
                logger.warning(f"HazardRef.json not found at {ref_path}, using fallback")
                self.hazard_ref = self._get_fallback_ref()
        except Exception as e:
            logger.warning(f"HazardRef加载失败: {e}")
            self.hazard_ref = self._get_fallback_ref()

    def _get_fallback_ref(self) -> Dict:
        return {
            "entries": [
                {"id": "HZ_generic", "subsystem_keywords": [], "function_keywords": [], "malfunction_keywords": [], "context_keywords": [], "hazards": []}
            ]
        }

    def _find_hazards_by_keywords(self, subsystem: str = "", function: str = "",
                                  malfunction: str = "", context: str = "") -> List[Dict[str, Any]]:
        """通过关键词在HazardRef中查找对应危害

        优先级：
        1. subsystem_hazards.{subsystem} - 子系统专属危害（最优先）
        2. entries - 通用危害库
        """
        if not self.hazard_ref:
            return []

        all_keywords = f"{subsystem} {function} {malfunction} {context}".lower()
        all_set = set(all_keywords.split())

        matched = []

        if subsystem and subsystem in self.hazard_ref.get("subsystem_hazards", {}):
            for entry in self.hazard_ref["subsystem_hazards"][subsystem]:
                subsystem_kws = set(" ".join(entry.get("subsystem_keywords", [])).lower().split())
                func_kws = set(" ".join(entry.get("function_keywords", [])).lower().split())
                malf_kws = set(" ".join(entry.get("malfunction_keywords", [])).lower().split())
                context_kws = set(" ".join(entry.get("context_keywords", [])).lower().split())

                subsystem_overlap = subsystem_kws & all_set
                if subsystem_overlap:
                    func_overlap = func_kws & all_set
                    if func_overlap:
                        matched.append(entry)
                    elif len(subsystem_overlap) >= 2:
                        matched.append(entry)

        if not matched:
            for entry in self.hazard_ref.get("entries", []):
                subsystem_kws = set(" ".join(entry.get("subsystem_keywords", [])).lower().split())
                func_kws = set(" ".join(entry.get("function_keywords", [])).lower().split())
                malf_kws = set(" ".join(entry.get("malfunction_keywords", [])).lower().split())
                context_kws = set(" ".join(entry.get("context_keywords", [])).lower().split())
                entry_set = subsystem_kws | func_kws | malf_kws | context_kws

                if subsystem:
                    subsystem_overlap = subsystem_kws & all_set
                    if subsystem_overlap:
                        func_overlap = func_kws & all_set
                        if func_overlap:
                            matched.append(entry)
                        elif len(subsystem_overlap) >= 2:
                            matched.append(entry)
                    continue

                overlap = entry_set & all_set
                if len(overlap) >= 3:
                    matched.append(entry)
                elif subsystem_kws & all_set and len(func_kws & all_set) >= 1:
                    matched.append(entry)

        return matched

    def _generate_hazard_from_ref(self, malfunction: str, function: str = "",
                                  subsystem: str = "") -> Optional[Dict[str, Any]]:
        """优先从HazardRef查找匹配的危害"""
        matched = self._find_hazards_by_keywords(
            subsystem=subsystem,
            function=function,
            malfunction=malfunction
        )

        if matched:
            entry = matched[0]
            hazards = entry.get("hazards", [])
            if hazards:
                primary = hazards[0]
                return {
                    "hazard": primary.get("description", ""),
                    "severity": primary.get("severity", "S1"),
                    "source": "HazardRef",
                    "ref_id": entry.get("id", ""),
                    "all_hazards": hazards
                }
        return None

    def record_new_hazard(self, subsystem: str, function: str, malfunction: str,
                          hazard_desc: str, severity: str = "S1") -> None:
        """记录新发现的危害"""
        self.new_hazards.append({
            "subsystem": subsystem,
            "function": function,
            "malfunction": malfunction,
            "hazard": hazard_desc,
            "severity": severity,
        })

    def get_new_hazards(self) -> List[Dict]:
        return self.new_hazards


class MalfunctionEngine:
    """Malfunction Analysis Engine (Steps 1-5)"""

    HAZOP_GUIDEWORDS = [
        {"guideword": "unintended", "description": "unrelated to the trigger."},
        {"guideword": "always active", "description": "Action is carried out continuously despite no trigger condition being present."},
        {"guideword": "loss", "description": "Action is not executed despite trigger condition being present."},
        {"guideword": "too large", "description": "Value above a specified limit value."},
        {"guideword": "too small", "description": "Value below a limit value."},
        {"guideword": "too early", "description": "Before the time event."},
        {"guideword": "too late", "description": "After the time event."},
        {"guideword": "too fast", "description": "Action takes place faster than defined."},
        {"guideword": "too slow", "description": "Action takes longer than defined."},
        {"guideword": "too long", "description": "Action is executed as planned, but takes longer than required."},
        {"guideword": "too short", "description": "Action is executed as planned, but too short."},
        {"guideword": "incomplete", "description": "Action is not completed but too short."},
        {"guideword": "different to", "description": "A different action than the expected one is executed."},
        {"guideword": "as well as", "description": "In addition to the action, a further, unintended action is also executed."},
    ]

    def __init__(self, item_definition_path: str = None, excel_template_path: str = None, input_json: str = None):
        self.item_definition_path = item_definition_path
        self.excel_template_path = excel_template_path
        self.input_json = input_json
        self.context = {}
        self.hazard_ref = HazardRef()

    def run(self) -> Dict[str, Any]:
        if self.input_json and os.path.exists(self.input_json):
            return self._load_from_json(self.input_json)

        if "functions" not in self.context:
            self.context = {
                "item_definition_path": self.item_definition_path,
                "functions": [],
                "functions_raw": [],
                "item_semantics": {},
                "function_outputs": {},
                "function_guidewords": {},
                "function_malfunctions": {},
                "function_hazards": {}
            }

        functions = self.context.get("functions", [])
        if not functions and self.item_definition_path:
            self._extract_functions_from_item_def()

        functions = self.context.get("functions", [])
        if not functions:
            logger.warning("未提取到任何功能")
            return self._build_output()

        logger.info(f"使用上游传入的 {len(functions)} 个功能")

        self._step2_output_description()
        self._step3_guidewords()
        self._step4_malfunction()
        self._step5_hazard_analysis()

        return self._build_output()

    def _load_from_json(self, json_path: str) -> Dict[str, Any]:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception as e:
            logger.error(f"加载JSON失败: {e}")
            return {"error": str(e)}

    def _extract_functions_from_item_def(self):
        if not self.item_definition_path or not os.path.exists(self.item_definition_path):
            logger.warning(f"Item定义文件不存在: {self.item_definition_path}")
            return

        try:
            from document_parsers.word_processor import WordProcessor
            wp = WordProcessor()
            result = wp.extract_item_definition(self.item_definition_path)
            funcs_data = result.get("functions", [])
            self.context["item_semantics"] = result.get("item_semantics", {})
            outputs_data = result.get("outputs", [])
            hazard_events = result.get("hazard_events", [])

            if funcs_data:
                from logic_checkers.function_checker import FunctionChecker
                validation = FunctionChecker().check(funcs_data)
                self.context["function_validation"] = validation
                funcs_data = validation.get("valid_functions", [])
                if not funcs_data:
                    rejected = validation.get("invalid_functions", [])
                    raise ValueError(
                        "功能抽取结果未通过质量门: "
                        + "; ".join(
                            f"{item.get('name', '')}: {', '.join(item.get('validation_reasons', []))}"
                            for item in rejected
                        )
                    )

                func_names = []
                for f in funcs_data:
                    name = f.get("name", "")
                    if name:
                        func_names.append(name)

                self.context["functions"] = func_names
                self.context["functions_raw"] = funcs_data

                func_outputs = {}
                for f in funcs_data:
                    name = f.get("name", "")
                    desc = f.get("output") or f.get("description", "")
                    if name:
                        func_outputs[name] = desc
                self.context["function_outputs"] = func_outputs

                logger.info(f"从Item定义提取了 {len(func_names)} 个功能")
                for fn in func_names:
                    logger.info(f"  - {fn}: {func_outputs.get(fn, '')}")
            else:
                logger.warning("从Item定义未提取到功能（functions列表为空）")
        except Exception as e:
            logger.error(f"提取功能失败: {e}")
            import traceback
            traceback.print_exc()

    def _step2_output_description(self):
        functions = self.context.get("functions", [])
        function_outputs = self.context.get("function_outputs", {})
        for func in functions:
            # Preserve the Item Definition output/description. Fabricated
            # placeholder text destroys traceability and hides missing input.
            function_outputs.setdefault(func, None)
        self.context["function_outputs"] = function_outputs
        logger.info(f"为 {len(functions)} 个功能提取了输出描述")

    def _step3_guidewords(self):
        functions = self.context.get("functions", [])
        function_guidewords = self.context.get("function_guidewords", {})
        for func in functions:
            function_guidewords[func] = self.HAZOP_GUIDEWORDS
        self.context["function_guidewords"] = function_guidewords
        logger.info(f"提取到 {len(self.HAZOP_GUIDEWORDS)} 个引导词")

    def _step4_malfunction(self):
        functions = self.context.get("functions", [])
        function_guidewords = self.context.get("function_guidewords", {})
        function_malfunctions = self.context.get("function_malfunctions", {})
        function_outputs = self.context.get("function_outputs", {})
        from logic_checkers.malfunction_checker import MalfunctionChecker
        checker = MalfunctionChecker()

        total_combinations = 0
        total_not_applicable = 0
        total_duplicates = 0
        total_applicable = 0
        assessments = {}
        for func in functions:
            guidewords = function_guidewords.get(func, [])
            output_desc = function_outputs.get(func, "")
            malf_list = []
            func_assessments = []
            generated_by_description = {}

            for gw_info in guidewords:
                gw = gw_info["guideword"]
                check_result = checker.check_function_guideword_combo(func, gw)
                assessment = {
                    "guideword": gw,
                    "description": gw_info.get("description", ""),
                    "status": "applicable",
                    "reason": "",
                    "covered_by": None,
                }
                if not check_result.get("is_valid", True):
                    reasons = check_result.get("reasons", [])
                    reason = reasons[0] if reasons else f"功能与引导词 {gw} 组合不合理"
                    assessment["status"] = "not_applicable"
                    assessment["reason"] = reason
                    total_not_applicable += 1
                    func_assessments.append(assessment)
                    continue

                malf = self._generate_malfunction(func, gw, output_desc)
                normalized = self._normalize_malfunction_for_dedup(malf)
                if normalized in generated_by_description:
                    assessment["status"] = "duplicate"
                    assessment["reason"] = "与已生成的失效模式语义相同"
                    assessment["covered_by"] = generated_by_description[normalized]
                    total_duplicates += 1
                    func_assessments.append(assessment)
                    continue

                generated_by_description[normalized] = gw
                assessment["reason"] = check_result.get("reasons", ["适用"])[0]
                func_assessments.append(assessment)
                malf_list.append({
                    "guideword": gw,
                    "malfunction": malf,
                    "analysis_status": "applicable",
                    "applicability_reason": assessment["reason"],
                })
                total_applicable += 1

            function_malfunctions[func] = malf_list
            assessments[func] = func_assessments
            total_combinations += len(guidewords)

        self.context["function_malfunctions"] = function_malfunctions
        self.context["function_guideword_assessments"] = assessments
        logger.info(
            f"检查摘要: {total_applicable} 个适用组合, "
            f"{total_not_applicable} 个不适用组合, {total_duplicates} 个重复组合, "
            f"共评估 {total_combinations} 个"
        )
        logger.info(f"仅向下游传递 {total_applicable} 个Malfunction记录")

    @staticmethod
    def _normalize_malfunction_for_dedup(malfunction: str) -> str:
        """Normalize generated text for exact semantic-duplicate suppression."""
        return re.sub(r"[\s，。,:：;；()（）_-]+", "", str(malfunction or "")).lower()

    def _generate_malfunction(self, function: str, guideword: str, output_desc: str) -> str:
        gw_chinese = {
            "unintended": "非预期触发",
            "always active": "持续激活",
            "loss": "丢失",
            "too large": "过大",
            "too small": "过小",
            "too early": "过早",
            "too late": "过晚",
            "too fast": "过快",
            "too slow": "过慢",
            "too long": "过长",
            "too short": "过短",
            "incomplete": "不完整",
            "different to": "不同",
            "as well as": "同时发生其他动作",
        }
        prefix = gw_chinese.get(guideword, guideword)
        if guideword == "loss":
            return f"{function}丢失"
        elif guideword == "too large":
            return f"{function}过大"
        elif guideword == "too small":
            return f"{function}过小"
        elif guideword == "always active":
            return f"{function}持续激活"
        elif guideword in ["too early", "too late", "too fast", "too slow", "too long", "too short"]:
            return f"{function}过" + {"too early": "早", "too late": "晚", "too fast": "快", "too slow": "慢", "too long": "长", "too short": "短"}.get(guideword, guideword)
        elif guideword == "unintended":
            return f"非预期触发{function}"
        elif guideword == "as well as":
            return f"{function}同时发生其他动作"
        else:
            return f"{prefix}{function}"

    def _step5_hazard_analysis(self):
        functions = self.context.get("functions", [])
        function_malfunctions = self.context.get("function_malfunctions", {})
        function_hazards = self.context.get("function_hazards", {})

        total_hazards = 0
        for func in functions:
            malf_list = function_malfunctions.get(func, [])
            haz_list = []

            for malf_item in malf_list:
                malf_desc = malf_item.get("malfunction", "")
                malf_gw = malf_item.get("guideword", "")
                if not malf_desc:
                    continue

                if "不适用" in malf_desc:
                    haz_list.append({
                        "malfunction": malf_desc,
                        "description": "不适用",
                        "category": "不适用",
                        "severity": "S0",
                        "source": "logical_validity_check",
                        "guideword": malf_gw,
                    })
                    total_hazards += 1
                    continue

                hazard_result = self._analyze_hazard_from_malfunction(malf_desc, func, malf_gw)
                hazard_result["malfunction"] = malf_desc
                hazard_result["guideword"] = malf_gw
                haz_list.append(hazard_result)
                total_hazards += 1

            function_hazards[func] = haz_list

        self.context["function_hazards"] = function_hazards
        logger.info(f"分析出 {total_hazards} 个危害")

    def _analyze_hazard_from_malfunction(self, malfunction: str, function: str, guideword: str = "") -> Dict[str, str]:
        """根据Malfunction分析可能的危害"""
        combined = f"{function} {malfunction}".lower()
        subsystem = ""
        for kw in ["wiper", "雨刷", "雨刮", "wash", "喷洗", "front wiper", "rear wiper"]:
            if kw in combined:
                subsystem = "wiper"
                break

        ref_hazard = self._generate_hazard_from_ref(malfunction, function, subsystem=subsystem)
        if ref_hazard:
            self.hazard_ref.record_new_hazard(subsystem, function, malfunction, ref_hazard["hazard"], ref_hazard["severity"])
            return {
                "description": ref_hazard["hazard"],
                "category": "HazardRef",
                "severity": ref_hazard.get("severity", "S1"),
                "ref_id": ref_hazard.get("ref_id", ""),
                "source": "HazardRef"
            }

        malf_lower = malfunction.lower()
        if "非预期" in malf_lower or "unintended" in malf_lower:
            return {
                "description": "功能在不应激活时激活，导致意外行为",
                "category": "控制类",
                "severity": "S1",
                "source": "fallback"
            }
        elif "持续激活" in malf_lower or "always active" in malf_lower:
            return {
                "description": "功能持续工作导致过热、耗电或其他问题",
                "category": "功能类",
                "severity": "S1",
                "source": "fallback"
            }
        elif "丢失" in malf_lower or "loss" in malf_lower:
            return {
                "description": "功能缺失导致系统无法响应预期操作",
                "category": "缺失类",
                "severity": "S1",
                "source": "fallback"
            }
        elif any(kw in malf_lower for kw in ["过", "too"]):
            return {
                "description": "功能参数超出正常范围导致异常行为",
                "category": "参数类",
                "severity": "S1",
                "source": "fallback"
            }
        else:
            return {
                "description": "功能异常导致潜在危害",
                "category": "通用类",
                "severity": "S1",
                "source": "fallback"
            }

    def _generate_hazard_from_ref(self, malfunction: str, function: str = "",
                                  subsystem: str = "") -> Optional[Dict[str, Any]]:
        """优先从HazardRef查找匹配的危害"""
        matched = self.hazard_ref._find_hazards_by_keywords(
            subsystem=subsystem,
            function=function,
            malfunction=malfunction
        )

        if matched:
            entry = matched[0]
            hazards = entry.get("hazards", [])
            if hazards:
                primary = hazards[0]
                return {
                    "hazard": primary.get("description", ""),
                    "severity": primary.get("severity", "S1"),
                    "source": "HazardRef",
                    "ref_id": entry.get("id", ""),
                    "all_hazards": hazards
                }
        return None

    def _build_output(self) -> Dict[str, Any]:
        return {
            "functions": self.context.get("functions", []),
            "functions_raw": self.context.get("functions_raw", []),
            "item_semantics": self.context.get("item_semantics", {}),
            "function_validation": self.context.get("function_validation", {}),
            "function_outputs": self.context.get("function_outputs", {}),
            "function_guidewords": self.context.get("function_guidewords", {}),
            "function_guideword_assessments": self.context.get("function_guideword_assessments", {}),
            "function_malfunctions": self.context.get("function_malfunctions", {}),
            "function_hazards": self.context.get("function_hazards", {}),
            "steps_completed": [1, 2, 3, 4, 5],
            "metadata": {
                "engine": "malfunction_engine",
                "total_functions": len(self.context.get("functions", [])),
                "total_malfunctions": sum(len(v) for v in self.context.get("function_malfunctions", {}).values()),
                "total_guideword_candidates": sum(len(v) for v in self.context.get("function_guidewords", {}).values()),
                "item_definition_path": self.item_definition_path,
            }
        }

    def export_json(self, output_path: str):
        output = self._build_output()
        output_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        logger.info(f"结果已导出至: {output_path}")


def main() -> int:
    """命令行入口：执行 HARA 第 1-5 步并生成阶段 JSON。"""
    parser = argparse.ArgumentParser(description="Malfunction Analysis Engine (Steps 1-5)")
    parser.add_argument("--item", "-i", required=True, help="Item Definition 文档路径")
    parser.add_argument("--template", "-t", required=True, help="HARA 模板 Excel 路径")
    parser.add_argument(
        "--output", "-o",
        default=str(script_dir.parent / "runtime" / "current" / "malfunction_output.json"),
        help="malfunction_output.json 输出路径",
    )
    parser.add_argument("--input-json", help="可选的已有阶段 JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="输出调试日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    item_path = os.path.abspath(args.item)
    template_path = os.path.abspath(args.template)
    output_path = os.path.abspath(args.output)

    if not os.path.isfile(item_path):
        logger.error(f"Item Definition 文档不存在: {item_path}")
        return 2
    if not os.path.isfile(template_path):
        logger.error(f"HARA 模板不存在: {template_path}")
        return 2

    try:
        engine = MalfunctionEngine(
            item_definition_path=item_path,
            excel_template_path=template_path,
            input_json=args.input_json,
        )
        result = engine.run()
        if result.get("error"):
            logger.error(f"引擎执行失败: {result['error']}")
            return 1
        if not result.get("functions"):
            logger.error("Item Definition 中未提取到任何功能，未生成无效阶段输出")
            return 1

        engine.export_json(output_path)
        print(f"成功! 输出文件: {output_path}")
        return 0
    except Exception as exc:
        logger.exception(f"malfunction 阶段执行异常: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

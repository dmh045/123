#!/usr/bin/env python3
"""
HARA引擎
实现17步HARA分析流程的核心引擎
"""

import os
import hashlib
import json
import re
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import logging

# 导入自定义模块
from document_parsers.word_processor import WordProcessor
from document_parsers.pdf_processor import PDFProcessor
from document_parsers.excel_processor import ExcelProcessor
from logic_checkers.malfunction_checker import MalfunctionChecker
from logic_checkers.scenario_checker import ScenarioChecker
from experience_library import HARAExperienceLibrary
from asil_matrix import TemplateASILMatrix
from ftti import FTTIEstimator

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class HARAStep:
    """HARA步骤基类"""
    
    def __init__(self, step_number: int, step_info: Dict[str, Any]):
        """
        初始化HARA步骤
        
        Args:
            step_number: 步骤编号
            step_info: 步骤信息
        """
        self.step_number = step_number
        self.step_info = step_info
        self.input = step_info.get("input", "")
        self.activity = step_info.get("activity", "")
        self.output = step_info.get("output", "")
        self.process_description = step_info.get("process_description", "")
        
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行步骤
        
        Args:
            context: 上下文信息
            
        Returns:
            执行结果
        """
        raise NotImplementedError("子类必须实现execute方法")
    
    def get_description(self) -> str:
        """获取步骤描述"""
        return f"步骤{self.step_number}: {self.activity}"
    
    def validate_input(self, context: Dict[str, Any]) -> bool:
        """
        验证输入
        
        Args:
            context: 上下文信息
            
        Returns:
            输入是否有效
        """
        # 解析输入要求
        inputs_needed = self._parse_input_requirements()
        
        for input_req in inputs_needed:
            if input_req not in context:
                logger.warning(f"步骤{self.step_number}缺少输入: {input_req}")
                return False
        
        return True
    
    def _parse_input_requirements(self) -> List[str]:
        """
        解析输入要求
        
        Returns:
            需要的输入列表
        """
        inputs_needed = []
        
        # 从input字段解析
        if self.input:
            # 示例: "Sheet '05_HARA'中的B列单元格"
            pattern = r"['\"]([^'\"]+)['\"]"
            matches = re.findall(pattern, self.input)
            inputs_needed.extend(matches)
        
        return inputs_needed


class Step1_ListFunctions(HARAStep):
    """步骤1: 罗列功能"""
    
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤1"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        # 获取Item Definition文档路径
        item_def_path = context.get("item_definition_path")
        if not item_def_path or not os.path.exists(item_def_path):
            return {"success": False, "error": f"Item Definition文档不存在: {item_def_path}"}
        
        # 使用WordProcessor提取功能
        word_processor = WordProcessor(item_def_path)
        functions = word_processor.extract_functions()
        
        if not functions:
            return {"success": False, "error": "未提取到功能信息"}
        
        # 更新上下文
        context["functions"] = functions
        
        logger.info(f"提取到 {len(functions)} 个功能")
        for i, func in enumerate(functions, 1):
            logger.info(f"  功能{i}: {func}")
        
        return {
            "success": True,
            "functions": functions,
            "message": f"成功提取{len(functions)}个功能"
        }


class Step2_ListOutputs(HARAStep):
    """步骤2: 罗列输出描述"""
    
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤2"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        # 获取Item Definition文档路径
        item_def_path = context.get("item_definition_path")
        if not item_def_path or not os.path.exists(item_def_path):
            return {"success": False, "error": f"Item Definition文档不存在: {item_def_path}"}
        
        # 获取功能列表
        functions = context.get("functions", [])
        if not functions:
            return {"success": False, "error": "未找到功能列表，请先执行步骤1"}
        
        # 使用WordProcessor提取输出
        word_processor = WordProcessor(item_def_path)
        outputs = word_processor.extract_outputs()
        
        if not outputs:
            return {"success": False, "error": "未提取到输出信息"}
        
        # 将功能与输出关联（简单实现：按顺序匹配）
        function_outputs = {}
        for i, func in enumerate(functions):
            if i < len(outputs):
                function_outputs[func] = outputs[i]
            else:
                function_outputs[func] = f"{func}的默认输出描述"
        
        # 更新上下文
        context["function_outputs"] = function_outputs
        
        logger.info(f"为 {len(functions)} 个功能提取了输出描述")
        for func, output in list(function_outputs.items())[:3]:  # 显示前3个
            logger.info(f"  {func} -> {output}")
        
        return {
            "success": True,
            "function_outputs": function_outputs,
            "message": f"成功为{len(functions)}个功能提取输出描述"
        }


class Step3_ListGuidewords(HARAStep):
    """步骤3: 罗列对应功能的引导词"""
    
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤3"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        # 获取Excel路径
        excel_path = context.get("excel_template_path")
        if not excel_path or not os.path.exists(excel_path):
            return {"success": False, "error": f"Excel模板不存在: {excel_path}"}
        
        # 获取功能列表
        functions = context.get("functions", [])
        if not functions:
            return {"success": False, "error": "未找到功能列表，请先执行步骤1"}
        
        # 使用ExcelProcessor提取引导词
        excel_processor = ExcelProcessor(excel_path)
        if not excel_processor.load_excel():
            return {"success": False, "error": "加载Excel失败"}
        
        guidewords = excel_processor.get_hazop_guidewords()
        
        if not guidewords:
            return {"success": False, "error": "未提取到引导词"}
        
        # 为每个功能创建引导词行
        function_guidewords = {}
        for func in functions:
            function_guidewords[func] = guidewords
        
        # 更新上下文
        context["guidewords"] = guidewords
        context["function_guidewords"] = function_guidewords
        
        logger.info(f"提取到 {len(guidewords)} 个引导词")
        logger.info(f"为 {len(functions)} 个功能分配了引导词")
        
        return {
            "success": True,
            "guidewords": guidewords,
            "function_guidewords": function_guidewords,
            "message": f"成功提取{len(guidewords)}个引导词，并为{len(functions)}个功能分配"
        }


class Step4_GenerateMalfunctions(HARAStep):
    """步骤4: 罗列对应功能的Malfunction"""
    
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤4"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        # 获取功能与引导词
        function_guidewords = context.get("function_guidewords", {})
        if not function_guidewords:
            return {"success": False, "error": "未找到功能与引导词映射，请先执行步骤3"}
        
        # 使用MalfunctionChecker检查组合合理性
        malfunction_checker = MalfunctionChecker()
        all_malfunctions = {}
        
        for func, guidewords in function_guidewords.items():
            # 检查组合
            results = malfunction_checker.check_multiple_combinations([func], guidewords)
            
            # 过滤有效组合
            valid_results = malfunction_checker.filter_valid_combinations(results)
            
            # 提取Malfunction描述
            malfunctions = []
            for result in valid_results:
                malfunctions.append({
                    "guideword": result["guideword"],
                    "guideword_description": result["guideword_description"],
                    "malfunction_description": result["malfunction_description"],
                    "check_result": result["check_result"]
                })
            
            all_malfunctions[func] = malfunctions
        
        # 更新上下文
        context["function_malfunctions"] = all_malfunctions
        
        # 统计
        total_malfunctions = sum(len(malfs) for malfs in all_malfunctions.values())
        logger.info(f"生成 {total_malfunctions} 个有效的Malfunction")
        
        # 显示示例
        for func, malfs in list(all_malfunctions.items())[:2]:  # 显示前2个功能
            logger.info(f"  {func}: {len(malfs)}个Malfunction")
            for malf in malfs[:2]:  # 显示前2个Malfunction
                logger.info(f"    - {malf['malfunction_description']}")
        
        return {
            "success": True,
            "function_malfunctions": all_malfunctions,
            "total_malfunctions": total_malfunctions,
            "message": f"成功生成{total_malfunctions}个有效的Malfunction"
        }


class Step5_AnalyzeHazards(HARAStep):
    """步骤5: 罗列对应Malfunction的Hazard"""
    
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤5"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        # 获取Malfunction
        function_malfunctions = context.get("function_malfunctions", {})
        if not function_malfunctions:
            return {"success": False, "error": "未找到Malfunction，请先执行步骤4"}
        
        # 分析每个Malfunction可能导致的危害
        function_hazards = {}
        
        for func, malfunctions in function_malfunctions.items():
            hazards = []
            
            for malf in malfunctions:
                # 基于Malfunction描述分析危害
                hazard = self._analyze_hazard(malf["malfunction_description"])
                
                hazards.append({
                    "malfunction": malf["malfunction_description"],
                    "guideword": malf["guideword"],
                    "hazard_description": hazard,
                    "hazard_category": self._categorize_hazard(hazard)
                })
            
            function_hazards[func] = hazards
        
        # 更新上下文
        context["function_hazards"] = function_hazards
        
        # 统计
        total_hazards = sum(len(hazards) for hazards in function_hazards.values())
        logger.info(f"分析出 {total_hazards} 个危害")
        
        return {
            "success": True,
            "function_hazards": function_hazards,
            "total_hazards": total_hazards,
            "message": f"成功分析出{total_hazards}个危害"
        }
    
    def _analyze_hazard(self, malfunction: str) -> str:
        """
        分析Malfunction可能导致的危害
        
        Args:
            malfunction: Malfunction描述
            
        Returns:
            危害描述
        """
        malfunction_lower = malfunction.lower()
        
        # 根据Malfunction关键词分析危害
        hazard_patterns = [
            (r"刹车.*失效", "车辆制动能力下降或丧失，导致碰撞风险"),
            (r"转向.*失效", "车辆转向控制能力下降或丧失，导致偏离车道"),
            (r"加速.*失控", "车辆速度不受控制，导致超速或碰撞"),
            (r"灯光.*失效", "车辆照明不足，导致能见度降低和碰撞风险"),
            (r"电池.*故障", "车辆电源系统异常，可能导致车辆失能"),
            (r"安全.*气囊", "安全系统误触发或未触发，导致乘员受伤"),
            (r"车门.*异常", "车门意外开启或无法开启，导致乘员跌落或被困"),
            (r"车窗.*异常", "车窗意外开启或无法关闭，导致安全隐患"),
            (r"非预期.*触发", "功能在不应激活时激活，导致意外行为"),
            (r"持续.*激活", "功能持续工作导致过热、耗电或其他问题"),
            (r"丢失", "功能无法正常工作，导致系统性能下降"),
            (r"过大|过小", "参数超出正常范围，导致系统性能异常"),
            (r"过早|过晚", "时机不正确，导致系统协调问题"),
            (r"过快|过慢", "速度异常，导致系统响应问题"),
        ]
        
        for pattern, hazard in hazard_patterns:
            if re.search(pattern, malfunction_lower):
                return hazard
        
        # 默认危害描述
        return f"{malfunction}可能导致的安全隐患"
    
    def _categorize_hazard(self, hazard: str) -> str:
        """
        对危害进行分类
        
        Args:
            hazard: 危害描述
            
        Returns:
            危害类别
        """
        hazard_lower = hazard.lower()
        
        if any(keyword in hazard_lower for keyword in ["碰撞", "撞击", "追尾"]):
            return "碰撞类"
        elif any(keyword in hazard_lower for keyword in ["偏离", "失控", "失稳"]):
            return "控制类"
        elif any(keyword in hazard_lower for keyword in ["失能", "断电", "停机"]):
            return "功能类"
        elif any(keyword in hazard_lower for keyword in ["受伤", "伤害", "安全"]):
            return "人身安全类"
        elif any(keyword in hazard_lower for keyword in ["被困", "跌落", "夹伤"]):
            return "乘员保护类"
        else:
            return "其他类"


class Step6_CombineScenarios(HARAStep):
    """步骤6: 组合场景"""
    
    # 定义功能与场景类型的强关联关系
    FUNCTION_SCENARIO_CORRELATION = {
        "刹车": ["Highway", "Urban", "Rural", "Intersection", "Roundabout", "Parking"],
        "制动": ["Highway", "Urban", "Rural", "Intersection", "Roundabout", "Parking"],
        "转向": ["Urban", "Intersection", "Roundabout", "Parking", "Residential"],
        "加速": ["Highway", "Urban", "Rural", "Parking"],
        "加速控制": ["Highway", "Urban", "Rural", "Parking"],
        "动力": ["Highway", "Urban", "Rural"],
        "灯光": ["Night", "Tunnel", "Dawn", "Dusk", "Parking", "Heavy Rain", "Fog"],
        "车灯": ["Night", "Tunnel", "Dawn", "Dusk", "Parking", "Heavy Rain", "Fog"],
        "近光灯": ["Night", "Tunnel", "Dawn", "Dusk", "Parking", "Heavy Rain", "Fog"],
        "远光灯": ["Night", "Highway", "Rural"],
        "车门": ["Parking", "Residential", "Urban"],
        "车窗": ["Parking", "Urban", "Rain"],
        "气囊": ["Crash", "Collision", "Highway"],
        "安全气囊": ["Crash", "Collision", "Highway"],
    }
    
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤6"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        # 获取Excel路径和场景库
        excel_path = context.get("excel_template_path")
        if not excel_path or not os.path.exists(excel_path):
            return {"success": False, "error": f"Excel模板不存在: {excel_path}"}
        
        # 获取功能危害
        function_hazards = context.get("function_hazards", {})
        if not function_hazards:
            return {"success": False, "error": "未找到功能危害，请先执行步骤5"}
        
        # 使用ExcelProcessor提取场景库
        excel_processor = ExcelProcessor(excel_path)
        if not excel_processor.load_excel():
            return {"success": False, "error": "加载Excel失败"}
        
        scenarios = excel_processor.get_scenarios_library()
        
        if not scenarios:
            return {"success": False, "error": "未提取到场景库"}
        
        # 使用ScenarioChecker检查Malfunction与场景组合
        scenario_checker = ScenarioChecker()
        function_scenarios = {}
        
        # Bug3修复：获取场景库中的"All"类型场景（用于弱关联时减少组合）
        all_scenarios = [s for s in scenarios if "All" in str(s.get("scenario", "")).lower() 
                        or "all" in str(s.get("operating_scenario", "")).lower()]
        
        for func, hazards in function_hazards.items():
            # 提取该功能的所有Malfunction
            malfunctions = []
            for hazard in hazards:
                malfunctions.append(hazard["malfunction"])
            
            # Bug3修复：判断功能与场景类型的关联强度
            related_scenarios = self._get_related_scenarios(func, scenarios, all_scenarios)
            
            logger.info(f"功能'{func}'关联到{len(related_scenarios)}个场景")
            
            # 只对关联的场景进行检查
            results = scenario_checker.check_multiple_scenario_combinations(malfunctions, related_scenarios)
            
            # 过滤有效组合
            valid_results = scenario_checker.filter_valid_combinations(results)
            
            # 按Malfunction分组
            malfunction_scenarios = {}
            for result in valid_results:
                malf = result["malfunction"]
                if malf not in malfunction_scenarios:
                    malfunction_scenarios[malf] = []
                
                malfunction_scenarios[malf].append({
                    "scenario": result["scenario"],
                    "scenario_summary": result["scenario_summary"],
                    "hazard_event": result["hazard_event"],
                    "risk_level": result["check_result"]["risk_level"]
                })
            
            function_scenarios[func] = malfunction_scenarios
        
        # 更新上下文
        context["scenarios_library"] = scenarios
        context["function_scenarios"] = function_scenarios
        
        # 统计
        total_combinations = sum(
            sum(len(scenarios) for scenarios in malf_scenarios.values())
            for malf_scenarios in function_scenarios.values()
        )
        logger.info(f"生成了 {total_combinations} 个有效的Malfunction-场景组合")
        
        return {
            "success": True,
            "scenarios_library": scenarios,
            "function_scenarios": function_scenarios,
            "total_combinations": total_combinations,
            "message": f"成功生成{total_combinations}个有效的Malfunction-场景组合"
        }
    
    def _get_related_scenarios(self, func: str, all_scenarios: List, all_type_scenarios: List) -> List:
        """
        Bug3修复：根据功能与场景类型的关联强度，返回相关场景列表
        - 强关联：使用特定场景类型
        - 弱关联：使用"All"类型场景以减少组合数量
        """
        func_lower = func.lower()
        
        # 检查功能是否与特定场景类型有强关联
        has_strong_correlation = False
        related_keywords = []
        
        for keyword, scenario_types in self.FUNCTION_SCENARIO_CORRELATION.items():
            if keyword.lower() in func_lower:
                has_strong_correlation = True
                related_keywords.extend(scenario_types)
        
        if has_strong_correlation and related_keywords:
            # 强关联：筛选包含相关关键词的场景
            related = []
            for sc in all_scenarios:
                scenario_text = str(sc.get("scenario", "")).lower()
                operating_text = str(sc.get("operating_scenario", "")).lower()
                
                for kw in related_keywords:
                    if kw.lower() in scenario_text or kw.lower() in operating_text:
                        related.append(sc)
                        break
            
            # 如果找到关联场景，返回它们；否则返回all类型场景
            if related:
                return related
            else:
                return all_type_scenarios if all_type_scenarios else all_scenarios[:5]
        else:
            # 弱关联：只使用"All"类型场景，减少组合数量
            logger.info(f"功能'{func}'与场景类型弱关联，使用All类型场景")
            if all_type_scenarios:
                return all_type_scenarios
            else:
                # 如果没有All类型场景，只返回前5个场景
                return all_scenarios[:5]


class Step7_RefineScenarios(HARAStep):
    """步骤7: 细化组合场景描述"""
    
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤7"""
        logger.info(f"执行{self.get_description()}")

        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        excel_path = context.get("excel_template_path")
        function_scenarios = context.get("function_scenarios", {})
        
        if not function_scenarios:
            return {"success": False, "error": "未找到步骤6的场景组合结果"}
        
        excel_processor = ExcelProcessor(excel_path)
        if not excel_processor.load_excel():
            return {"success": False, "error": "加载Excel失败"}
        
        scenarios_library = excel_processor.get_scenarios_library()
        
        if not scenarios_library:
            return {"success": False, "error": "未从Scenarios_Library sheet提取到场景库"}
        
        supplementary_dims = {
            "vehicle_state": ("C", "车辆状态"),
            "vehicle_speed": ("D", "车辆速度"),
            "weather_conditions": ("E", "天气情况"),
            "road_surface_conditions": ("F", "路面情况")
        }
        
        supplementary_values = {dim: set() for dim in supplementary_dims.keys()}
        
        for item in scenarios_library:
            for dim in supplementary_dims.keys():
                if dim in item and item[dim]:
                    supplementary_values[dim].add(str(item[dim]))
        
        for dim in supplementary_values:
            if not supplementary_values[dim]:
                supplementary_values[dim].add("N/A")
        
        logger.info(f"补充场景维度: {[(k, len(v)) for k, v in supplementary_values.items()]}")
        
        refined_function_scenarios = {}
        total_refined = 0
        
        import itertools
        
        # Bug3修复：限制每个malfunction的最大场景数量，防止组合爆炸
        MAX_REFINED_PER_MALF = 10
        
        for func, malf_scenarios in function_scenarios.items():
            refined_malf_scenarios = {}
            
            for malf, scenarios in malf_scenarios.items():
                refined_scenarios = []
                
                for s in scenarios:
                    main_scenario = s.get('scenario', s.get('scenario_summary', ''))
                    
                    library_item = next(
                        (item for item in scenarios_library 
                         if item.get("scenario") == main_scenario or item.get("operating_scenario") == main_scenario),
                        {}
                    )
                    
                    current_supp_values = {}
                    for dim_key, (col_letter, dim_name) in supplementary_dims.items():
                        if library_item.get(dim_key):
                            current_supp_values[dim_name] = [str(library_item[dim_key])]
                        else:
                            all_vals = list(supplementary_values[dim_key])
                            # Bug3修复：限制补充值数量，只取前3个
                            current_supp_values[dim_name] = all_vals[:3] if all_vals else ["N/A"]
                    
                    keys = current_supp_values.keys()
                    values = current_supp_values.values()
                    
                    for combination in itertools.product(*values):
                        # Bug3修复：限制总数量
                        if len(refined_scenarios) >= MAX_REFINED_PER_MALF:
                            break
                            
                        comb_dict = dict(zip(keys, combination))
                        supp_parts = [f"{dim}={val}" for dim, val in comb_dict.items()]
                        refined_scenario_text = f"{main_scenario} [{', '.join(supp_parts)}]"
                        
                        refined_scenarios.append({
                            "original_scenario": main_scenario,
                            "refined_scenario": refined_scenario_text,
                            "vehicle_state": comb_dict.get("车辆状态", ""),
                            "vehicle_speed": comb_dict.get("车辆速度", ""),
                            "weather_conditions": comb_dict.get("天气情况", ""),
                            "road_surface_conditions": comb_dict.get("路面情况", ""),
                            "hazard_event": s.get('hazard_event', '')
                        })
                        total_refined += 1
                    
                    # Bug3修复：达到上限后跳过剩余场景
                    if len(refined_scenarios) >= MAX_REFINED_PER_MALF:
                        break
                
                refined_malf_scenarios[malf] = refined_scenarios
            refined_function_scenarios[func] = refined_malf_scenarios
        
        context["refined_function_scenarios"] = refined_function_scenarios
        context["supplementary_dims"] = list(supplementary_dims.values())
        
        logger.info(f"为 {len(function_scenarios)} 个功能生成了 {total_refined} 个细化场景")
        
        return {
            "success": True,
            "refined_function_scenarios": refined_function_scenarios,
            "total_refined": total_refined,
            "message": f"成功细化{total_refined}个场景描述"
        }


class Step8_AnalyzeHazardEvents(HARAStep):
    """步骤8: 分析危害事件

    优先级获取逻辑：
    1. 如果用户提供了Item Definition文档，从该文档中提取危害事件
    2. 如果用户未提供Item Definition文档，从HARA模板的Severity sheet的L列获取危害事件
    3. 如果以上两个来源都没有找到关联的hazard event，采用推理形式：
       "<某道路使用者>受到<某种>伤害由于整车的<某种>行为"
    """

    ROAD_USERS = ["驾驶员", "乘员", "行人", "骑行者", "摩托车驾驶员", "其他道路使用者"]
    INJURY_TYPES = ["致命伤害", "严重伤害", "轻中度伤害", "轻微伤害"]
    BEHAVIOR_KEYWORDS = {
        "刹车失效": "制动功能失效",
        "转向失效": "转向控制失效",
        "加速失控": "非预期加速",
        "灯光失效": "照明功能失效",
        "车门异常": "车门控制失效",
        "车窗异常": "车窗控制失效",
        "电池故障": "电源系统故障",
        "气囊异常": "安全气囊功能异常",
        "动力丧失": "动力输出异常",
        "制动失效": "制动功能失效",
        "转向失灵": "转向控制失效"
    }

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤8"""
        logger.info(f"执行{self.get_description()}")

        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}

        function_hazards = context.get("function_hazards", {})
        if not function_hazards:
            return {"success": False, "error": "未找到危害分析结果，请先执行步骤5"}

        function_scenarios = context.get("function_scenarios", {})
        if not function_scenarios:
            return {"success": False, "error": "未找到场景组合结果，请先执行步骤6"}

        hazard_events_results = {}
        total_events = 0

        # 获取危害事件的来源
        item_def_path = context.get("item_definition_path")
        excel_path = context.get("excel_template_path")
        
        # 优先级1: 从Item Definition文档获取危害事件
        id_hazard_events = {}
        if item_def_path and os.path.exists(item_def_path):
            id_hazard_events = self._extract_hazard_events_from_item_def(item_def_path)
            logger.info(f"从Item Definition提取到 {len(id_hazard_events)} 个危害事件")

        # 优先级2: 从HARA模板Severity sheet的L列获取危害事件
        template_hazard_events = {}
        if excel_path and os.path.exists(excel_path):
            template_hazard_events = self._extract_hazard_events_from_template(excel_path)
            logger.info(f"从HARA模板提取到 {len(template_hazard_events)} 个危害事件")

        # 为每个功能-Malf组合分析危害事件
        for func, malfunctions in function_hazards.items():
            malf_hazard_events = {}
            func_scenarios = function_scenarios.get(func, {})

            for malf_data in malfunctions:
                malf = malf_data.get("malfunction", "")
                hazard_desc = malf_data.get("hazard_description", "")
                hazard_category = malf_data.get("hazard_category", "")
                malf_scenarios = func_scenarios.get(malf, [])

                # 尝试从Item Definition或模板获取关联的危害事件
                linked_hazard_event = self._find_linked_hazard_event(
                    func, malf, hazard_desc, hazard_category,
                    id_hazard_events, template_hazard_events
                )

                if linked_hazard_event:
                    # 找到关联的危害事件，直接使用
                    if malf_scenarios:
                        scenario_hazards = []
                        for sc in malf_scenarios:
                            scenario = sc.get("scenario_summary", sc.get("scenario", ""))
                            scenario_hazards.append({
                                "refined_scenario": scenario,
                                "hazard_event": linked_hazard_event
                            })
                        malf_hazard_events[malf] = scenario_hazards
                        total_events += len(scenario_hazards)
                    else:
                        malf_hazard_events[malf] = [{
                            "refined_scenario": "",
                            "hazard_event": linked_hazard_event
                        }]
                        total_events += 1
                else:
                    # 优先级3: 推理生成危害事件
                    if malf_scenarios:
                        scenario_hazards = []
                        for sc in malf_scenarios:
                            scenario = sc.get("scenario_summary", sc.get("scenario", ""))
                            hazard_event = self._infer_hazard_event(malf, hazard_desc, hazard_category, scenario)
                            scenario_hazards.append({
                                "refined_scenario": scenario,
                                "hazard_event": hazard_event
                            })
                        malf_hazard_events[malf] = scenario_hazards
                        total_events += len(scenario_hazards)
                    else:
                        hazard_event = self._infer_hazard_event(malf, hazard_desc, hazard_category, "")
                        malf_hazard_events[malf] = [{
                            "refined_scenario": "",
                            "hazard_event": hazard_event
                        }]
                        total_events += 1

            hazard_events_results[func] = malf_hazard_events

        context["function_hazard_events"] = hazard_events_results
        context["hazard_event_sources"] = {
            "item_definition_count": len(id_hazard_events),
            "template_count": len(template_hazard_events),
            "inferred_count": total_events - min(len(id_hazard_events), len(template_hazard_events))
        }

        logger.info(f"分析出 {total_events} 个具体的危害事件")

        return {
            "success": True,
            "function_hazard_events": hazard_events_results,
            "total_events": total_events,
            "message": f"成功分析出{total_events}个危害事件"
        }

    def _extract_hazard_events_from_item_def(self, item_def_path: str) -> Dict[str, List[str]]:
        """从Item Definition文档提取危害事件"""
        hazard_events = {}
        try:
            word_processor = WordProcessor(item_def_path)
            result = word_processor.extract_sections()
            if result and "hazard_events" in result:
                for he in result["hazard_events"]:
                    desc = he.get("description", "")
                    if desc:
                        # 按功能关键词分类
                        for kw in self.ROAD_USERS + ["道路使用者", "vehicle", "车辆"]:
                            if kw.lower() in desc.lower():
                                if kw not in hazard_events:
                                    hazard_events[kw] = []
                                hazard_events[kw].append(desc)
                                break
                        else:
                            if "general" not in hazard_events:
                                hazard_events["general"] = []
                            hazard_events["general"].append(desc)
        except Exception as e:
            logger.warning(f"从Item Definition提取危害事件失败: {e}")
        return hazard_events

    def _extract_hazard_events_from_template(self, excel_path: str) -> Dict[int, str]:
        """从HARA模板Severity sheet的L列获取危害事件"""
        hazard_events = {}
        try:
            excel_processor = ExcelProcessor(excel_path)
            if excel_processor.load_excel():
                severity_data = excel_processor.get_sheet_data("Severity")
                if severity_data:
                    # L列是第12列（索引11）
                    header_row_idx = -1
                    for i in range(min(5, len(severity_data))):
                        if len(severity_data[i]) > 11:
                            header = str(severity_data[i][11]).lower() if severity_data[i][11] else ""
                            if "hazard" in header and "event" in header:
                                header_row_idx = i
                                break
                    
                    start_row = header_row_idx + 1 if header_row_idx >= 0 else 2
                    for i in range(start_row, min(start_row + 50, len(severity_data))):
                        if len(severity_data[i]) > 11:
                            hazard_event = self._clean_text(severity_data[i][11])
                            if hazard_event and hazard_event.strip():
                                hazard_events[i] = hazard_event
        except Exception as e:
            logger.warning(f"从HARA模板提取危害事件失败: {e}")
        return hazard_events

    def _find_linked_hazard_event(self, func: str, malf: str, hazard_desc: str, 
                                   hazard_category: str,
                                   id_hazard_events: Dict, 
                                   template_hazard_events: Dict) -> Optional[str]:
        """查找与当前功能-Malf组合关联的危害事件"""
        # 构建搜索关键词
        search_keywords = []
        if func:
            search_keywords.append(func.lower())
        if malf:
            malf_keywords = [kw.lower() for kw in self.BEHAVIOR_KEYWORDS.keys()]
            for kw in malf_keywords:
                if kw in malf.lower():
                    search_keywords.append(kw)
        
        # 在Item Definition危害事件中搜索
        for keyword in search_keywords:
            for category, events in id_hazard_events.items():
                for event in events:
                    if keyword in event.lower():
                        return event

        # 在模板危害事件中搜索
        for keyword in search_keywords:
            for row_idx, event in template_hazard_events.items():
                if keyword in event.lower():
                    return event

        return None

    # 引导词到车辆故障状态的映射（v9.0新增）
    FAULT_STATE_MAPPING = {
        "unintended": "非预期触发导致系统执行非计划操作",
        "always active": "持续激活导致系统无法进入休眠",
        "loss": "功能完全丧失导致关键系统失效",
        "too large": "参数过大导致系统过载或数据溢出",
        "too small": "参数过小导致系统响应不足",
        "too early": "触发时机过早导致前置条件未满足",
        "too late": "触发时机过晚导致错过最佳响应窗口",
        "too fast": "执行速度过快导致验证不充分",
        "too slow": "执行速度过慢导致超时或资源占用",
        "too long": "持续时间过长导致系统资源耗尽",
        "too short": "持续时间过短导致操作不完整",
        "incomplete": "操作不完整导致系统处于中间态",
        "different to": "行为异常导致预期外系统响应",
        "as well as": "附加动作触发导致多系统冲突"
    }
    
    # 引导词到车辆行为的映射（v9.0新增）
    BEHAVIOR_MAPPING = {
        "unintended": "车辆出现非预期的功能激活或参数变化",
        "always active": "车辆持续执行非预期的重复操作",
        "loss": "车辆关键功能突然失效，无法正常操作",
        "too large": "车辆参数异常增大导致操控困难",
        "too small": "车辆参数异常减小导致功能受限",
        "too early": "车辆在危险条件下执行操作引发事故",
        "too late": "车辆错过关键操作时机导致事故",
        "too fast": "车辆出现快速且不可控的行为变化",
        "too slow": "车辆响应迟滞导致驾驶员无法及时控制",
        "too long": "车辆长时间处于异常状态引发危险",
        "too short": "车辆操作中断导致系统不一致",
        "incomplete": "车辆功能部分实现导致不可预测行为",
        "different to": "车辆表现与驾驶员预期不符",
        "as well as": "车辆同时执行多个冲突指令"
    }
    
    # 引导词到人员伤害的映射（v9.0新增）
    INJURY_MAPPING = {
        "unintended": "驾驶员惊慌失措导致操作失误，造成碰撞伤害",
        "always active": "驾驶员被迫反复应对异常，造成疲劳和操作失误",
        "loss": "驾驶员失去对车辆的控制，导致严重碰撞伤害",
        "too large": "驾驶员难以精确控制车辆，造成碰撞或侧翻伤害",
        "too small": "驾驶员无法获得足够的功能支持，造成被困或延误伤害",
        "too early": "驾驶员未做好应对准备，造成应急响应失败伤害",
        "too late": "驾驶员错过最佳应对时机，造成不可避免的碰撞伤害",
        "too fast": "驾驶员来不及反应，造成高速碰撞伤害",
        "too slow": "驾驶员判断延迟，造成追尾或延误救援伤害",
        "too long": "驾驶员长时间处于紧张状态，造成疲劳伤害",
        "too short": "驾驶员无法确认操作完成，造成二次事故伤害",
        "incomplete": "驾驶员面对不一致的车辆状态，造成判断失误伤害",
        "different to": "驾驶员误判车辆状态，造成操作失误伤害",
        "as well as": "驾驶员同时面对多重异常，造成应对失败伤害"
    }
    
    def _infer_hazard_event(self, malfunction: str, hazard_desc: str, 
                           hazard_category: str, scenario: str) -> str:
        """推理生成危害事件（v9.0更新语义结构）
        
        语义模板（新）: <具体车辆故障状态>导致<具体车辆行为><具体人员伤害>
        当无直接获取内容时使用推理结果。
        """
        # 提取引导词
        guideword = ""
        malf_lower = malfunction.lower() if malfunction else ""
        
        for gw in self.FAULT_STATE_MAPPING.keys():
            if gw in malf_lower:
                guideword = gw
                break
        
        # 获取故障状态
        fault_state = self.FAULT_STATE_MAPPING.get(guideword, 
            f"{malfunction}导致系统异常" if malfunction else "系统异常")
        
        # 获取车辆行为
        vehicle_behavior = self.BEHAVIOR_MAPPING.get(guideword, 
            "车辆出现不可预测的行为")
        
        # 获取人员伤害
        injury = self.INJURY_MAPPING.get(guideword, 
            "造成人员伤害")
        
        # 构建危害事件描述（使用新语义结构）
        hazard_event = f"<{fault_state}>导致<{vehicle_behavior}>，<{injury}>"
        
        return hazard_event

    def _clean_text(self, text) -> str:
        """清理文本"""
        if text is None:
            return ""
        text_str = str(text)
        text_str = re.sub(r'\s+', ' ', text_str)
        return text_str.strip()


class Step9_SeverityReasoning(HARAStep):
    """步骤9: 严重度合理性分析

    评估逻辑（基于模板Severity sheet）：
    1. 在L列Hazard events中模糊匹配当前危害事件描述
       1-1. 有匹配 → 取N列S值；碰撞类需按I列速度指引进一步判定
       1-2. 无匹配 → 分析伤害的AIS等级，参考B23:B41的AIS定义
             再参考B7:F9的S值定义确定最终等级
    """

    DEFAULT_ROAD_USERS = ["驾驶员", "乘员", "行人", "自行车驾驶员", "摩托车驾驶员", "维修人员", "应急响应人员"]

    # Severity sheet L:M:N 危害事件映射表
    HAZARD_EVENT_MAP = {
        "collision": {"keywords": ["collision", "collisions", "碰撞", "撞击", "追尾"],
                      "s_normal": None, "note": "需按I列速度映射表判定"},
        "personal or things falling out of vehicle": {"keywords": ["falling out", "掉落", "跌落", "甩出"],
                      "s_speed_high": "S3", "s_speed_low": "S2",
                      "speed_threshold": 15, "note": "中高速>15km/h=S3, 低速<15km/h=S2"},
        "squeeze or pinch": {"keywords": ["squeeze", "pinch", "夹伤", "挤压", "夹持"],
                      "s_adult": "S1", "s_child": "S2", "s_driver": "S2",
                      "note": "成人S1, 儿童/驾驶员S2"},
        "possible rib fracture and asphyxia": {"keywords": ["rib fracture", "asphyxia", "肋骨骨折", "窒息", "缺氧"],
                      "s": "S3", "note": "S3"},
        "skin-deep wounds": {"keywords": ["skin-deep wound", "皮肤浅表伤", "表皮伤", "轻微伤"],
                      "s": "S1", "note": "S1"},
        "possible fracture of the passenger's finger": {"keywords": ["finger fracture", "手指骨折"],
                      "s": "S1", "note": "S1"},
        "fail to escape the vechie after crash": {"keywords": ["fail to escape", "逃生失败", "无法逃离"],
                      "s": "S3", "note": "S3"},
        "asphyxia": {"keywords": ["asphyxia", "窒息", "缺氧", "窒息风险"],
                      "s": "S3", "note": "S3"},
        "electric shock": {"keywords": ["electric shock", "电击", "触电", "电伤害"],
                      "s": "S3", "note": "S3"},
        "destabilization or lane departure": {"keywords": ["destabilization", "lane departure", "偏离车道", "失稳", "车道偏离"],
                      "s": "S3", "note": "S3"},
    }

    # Severity sheet I列 碰撞速度映射表
    I_COLUMN_COLLISION_MAP = [
        # (碰撞类型关键词, 速度条件, S值, 判定条件)
        # ── Rear-end collision (追尾) ──
        # Severity sheet I列定义: v<15=S0, v:15-64=S1, v:64-80=S1, v:80-100=S2, v>100=S3
        ("rear-end collision", "<15", "S0", lambda v: v < 15),
        ("rear-end collision", "15-64", "S1", lambda v: 15 <= v < 64),
        ("rear-end collision", "64-80", "S1", lambda v: 64 <= v < 80),
        ("rear-end collision", "80-100", "S2", lambda v: 80 <= v < 100),
        ("rear-end collision", ">100", "S3", lambda v: v >= 100),
        ("frontal collision", "<15", "S0", lambda v: v < 15),
        ("frontal collision", "15-30", "S0", lambda v: 15 <= v < 30),
        ("frontal collision", "30-50", "S1", lambda v: 30 <= v < 50),
        ("frontal collision", "50-64", "S2", lambda v: 50 <= v < 64),
        ("frontal collision", "64-80", "S2", lambda v: 64 <= v < 80),
        ("frontal collision", "64-80", "S3", lambda v: 64 <= v < 80 and "S3" in "S2 (S3)"),
        ("frontal collision", ">80", "S3", lambda v: v >= 80),
        ("side collision", "<15", "S0", lambda v: v < 15),
        ("side collision", "15-30", "S1", lambda v: 15 <= v < 30),
        ("side collision", "30-50", "S2", lambda v: 30 <= v < 50),
        ("side collision", "50-64", "S3", lambda v: 50 <= v < 64),
        ("side collision", ">80", "S3", lambda v: v >= 80),
        ("car-pedestrian", "<15", "S1", lambda v: v < 15),
        ("car-pedestrian", "15-30", "S2", lambda v: 15 <= v < 30),
        ("car pedestrians", "30-50", "S2", lambda v: 30 <= v < 50),
        ("car pedestrians", "50-64", "S3", lambda v: 50 <= v < 64),
        ("car cyclists", "<15", "S1", lambda v: v < 15),
        ("car cyclists", "15-30", "S2", lambda v: 15 <= v < 30),
        ("car cyclists", "30-50", "S2", lambda v: 30 <= v < 50),
        ("car cyclists", "50-64", "S3", lambda v: 50 <= v < 64),
        ("destabilization/lane departure", "all", "S3", lambda v: True),
    ]

    # Severity sheet B7:F9 S值定义
    S_LEVEL_DEFINITIONS = {
        "S0": {
            "description": "No injuries (无伤害)",
            "ais": "AIS 0 and less than 10% probability of AIS 1-6; or damage that cannot be classified safety-related",
            "prob_threshold": "AIS 1-6 概率 < 10%",
            "examples": ["Bumps with roadside infrastructure", "Light grazing damage", "Damage entering/exiting parking space", "Leaving the road without collision or rollover"]
        },
        "S1": {
            "description": "Light and moderate injuries (轻中度伤害)",
            "ais": "More than 10% probability of AIS 1-6 (and not S2 or S3)",
            "prob_threshold": "AIS 1-6 概率 > 10%",
            "examples": ["Side impact with a narrow stationary object", "Rear/front collision with very low speed", "skin-deep wounds, muscle pains, whiplash", "Possible fracture of finger"]
        },
        "S2": {
            "description": "Severe and life-threatening injuries, survival probable (严重伤害，可能存活)",
            "ais": "More than 10% probability of AIS 3-6 (and not S3)",
            "prob_threshold": "AIS 3-6 概率 > 10%",
            "examples": ["Side impact with a tree (low speed)", "Pedestrian/bicycle accident with low speed", "Frontal collision 50-80km/h"]
        },
        "S3": {
            "description": "Life-threatening injuries, survival uncertain, fatal (危及生命/致命)",
            "ais": "More than 10% probability of AIS 5-6",
            "prob_threshold": "AIS 5-6 概率 > 10%",
            "examples": ["Front/side collision with medium speed", "Frontal collision >80km/h", "Destabilization/lane departure", "Asphyxia", "Electric shock"]
        }
    }

    # AIS等级描述 (from B23:B41)
    AIS_DESCRIPTIONS = {
        0: "no injuries (无伤害)",
        1: "light injuries such as skin-deep wounds, muscle pains, whiplash (表皮轻伤)",
        2: "moderate injuries such as deep flesh wounds, concussion up to 15min unconsciousness, uncomplicated long bone/rib fractures (中度伤)",
        3: "severe but not life-threatening injuries such as skull fractures without brain injury, spinal dislocations below C4 without cord damage (严重但非致命)",
        4: "severe injuries (life-threatening, survival probable) such as concussion up to 12h unconsciousness (危及生命，可能存活)",
        5: "critical injuries (life-threatening, survival uncertain) such as spinal fractures with cord damage, intestinal/cardiac tears (严重危及生命，存活不确定)",
        6: "extremely critical or fatal injuries such as cervical vertebra fractures above C3 with cord damage (极危/致命)"
    }

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤9"""
        logger.info(f"执行{self.get_description()}")

        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}

        excel_path = context.get("excel_template_path")
        excel_processor = ExcelProcessor(excel_path)
        if not excel_processor.load_excel():
            return {"success": False, "error": "加载Excel失败"}
        
        severity_criteria = excel_processor.get_severity_criteria()
        
        function_hazard_events = context.get("function_hazard_events", {})
        if not function_hazard_events:
            return {"success": False, "error": "未找到危害事件，请先执行步骤8"}
        
        refined_scenarios = context.get("refined_function_scenarios", {})
        
        road_users_from_template = severity_criteria.get("road_users", [])
        if not road_users_from_template:
            road_users_from_template = self.DEFAULT_ROAD_USERS
        
        severity_reasoning = {}
        total_reasonings = 0

        for func, malf_events in function_hazard_events.items():
            malf_reasoning = {}
            for malf, scenarios in malf_events.items():
                scenario_reasoning = []
                
                scenario_key = f"{func}_{malf}"
                related_refined = refined_scenarios.get(func, {}).get(malf, [])
                
                affected_road_users = self._identify_affected_road_users(
                    func, malf, related_refined, road_users_from_template
                )
                
                for se in scenarios:
                    reason = self._reason_severity(
                        se["hazard_event"], 
                        severity_criteria,
                        affected_road_users,
                        related_refined
                    )
                    scenario_reasoning.append({
                        "hazard_event": se["hazard_event"],
                        "severity_reason": reason,
                        "affected_road_users": affected_road_users
                    })
                malf_reasoning[malf] = scenario_reasoning
                total_reasonings += len(scenario_reasoning)
            severity_reasoning[func] = malf_reasoning

        context["severity_reasoning"] = severity_reasoning
        context["severity_criteria"] = severity_criteria
        logger.info(f"完成了 {total_reasonings} 项严重度合理性分析")

        return {
            "success": True,
            "severity_reasoning": severity_reasoning,
            "total_reasonings": total_reasonings,
            "message": f"成功分析{total_reasonings}项严重度合理性"
        }

    def _identify_affected_road_users(self, func: str, malf: str, 
                                       refined_scenarios: List, 
                                       available_users: List[str]) -> List[str]:
        """根据功能、失效模式和场景识别受影响的道路使用者"""
        func_lower = func.lower()
        malf_lower = malf.lower()
        
        affected = []
        
        scenario_text = " ".join([
            s.get("refined_scenario", "") for s in refined_scenarios[:3]
        ]).lower()
        
        if any(kw in scenario_text for kw in ["行人", "横穿", "walking", "pedestrian"]):
            affected.append("行人")
        if any(kw in scenario_text for kw in ["车内", "乘员", "passenger", "occupant"]):
            affected.extend(["驾驶员", "乘员"])
        if any(kw in scenario_text for kw in ["自行车", "cyclist", "bike"]):
            affected.append("自行车驾驶员")
        if any(kw in scenario_text for kw in ["摩托", "motorcycle", "motorcyclist"]):
            affected.append("摩托车驾驶员")
        if any(kw in scenario_text for kw in ["维修", "service", "maintenance"]):
            affected.append("维修人员")
        if any(kw in scenario_text for kw in ["救援", "emergency", "应急"]):
            affected.append("应急响应人员")
        
        if not affected:
            if any(kw in func_lower for kw in ["刹车", "制动", "brake", "转向", "steering"]):
                affected = ["驾驶员", "乘员", "行人"]
            elif any(kw in malf_lower for kw in ["碰撞", "crash", "impact"]):
                affected = available_users[:3]
            else:
                affected = available_users[:2] if available_users else ["驾驶员", "乘员"]
        
        return list(set(affected))[:4]

    def _reason_severity(self, hazard_event: str, criteria: Dict,
                         affected_users: List[str],
                         refined_scenarios: List) -> str:
        """
        严重度合理性评估（v9.0更新语义结构）
        
        语义模板（新4步结构）:
        步骤1: 危害事件描述
        步骤2: 在{场景}场景下({速度}, {天气})，{潜在伤害描述}
        步骤3: 依据{严重度级别定义}
        步骤4: 参考{GB/T 34590 Table B.1参考}
        评定Severity={最终S值}
        """
        hazard_lower = hazard_event.lower()
        severity_levels = criteria.get("severity_levels", {})
        users_str = "、".join(affected_users) if affected_users else "道路使用者"

        # 提取场景信息
        scenario_info = ""
        vehicle_speed = 0
        weather_condition = "any weather condition"
        if refined_scenarios:
            sample = refined_scenarios[0].get("refined_scenario", "")
            if sample:
                scenario_info = sample[:60]
            vs = refined_scenarios[0].get("vehicle_speed", "")
            if vs:
                speed_match = re.search(r'(\d+)', str(vs))
                if speed_match:
                    vehicle_speed = int(speed_match.group(1))
            wc = refined_scenarios[0].get("weather", "")
            if wc:
                weather_condition = wc
        
        # 在L列危害事件库中模糊匹配
        matched_level = self._match_hazard_event_in_template(hazard_lower, vehicle_speed)
        
        # 获取严重度级别定义
        level_def = self.S_LEVEL_DEFINITIONS.get(matched_level, {})
        level_desc = level_def.get("description", "")
        ais_ref = level_def.get("ais", "")
        
        # 推断潜在伤害描述
        potential_harm = "驾驶员可能因功能异常而遭受伤害"
        if matched_level == "S3":
            potential_harm = "驾驶员面临危及生命的严重伤害风险"
        elif matched_level == "S2":
            potential_harm = f"对{users_str}造成严重的和危及生命的伤害"
        elif matched_level == "S1":
            potential_harm = f"对{users_str}造成轻中度伤害"
        elif matched_level == "S0":
            potential_harm = "对安全影响轻微或无伤害"

        # 构建新的4步语义结构
        step1 = f"危害事件: {hazard_event[:80]}..." if len(hazard_event) > 80 else f"危害事件: {hazard_event}"
        step2 = f"在{scenario_info}场景下({vehicle_speed} km/h, {weather_condition})，{potential_harm}"
        step3 = f"依据{matched_level} - {level_desc}"
        step4 = f"参考GB/T 34590 Table B.1: {ais_ref}"
        final = f"评定Severity={matched_level}"

        return f"{step1}。\n{step2}。\n{step3}，\n{step4}，\n{final}。"

    def _match_hazard_event_in_template(self, hazard_lower: str, vehicle_speed: int) -> Optional[str]:
        """
        在L列危害事件库中模糊匹配，返回S等级

        1-1. 有匹配 → 取N列S值
             碰撞相关(hazard_event含collision/crash/碰撞) → 按I列速度映射进一步判定
        """
        # ── 判断是否为碰撞类（需按I列速度映射判定） ──
        is_collision = any(kw in hazard_lower for kw in
                          ["collision", "crash", "rear-end", "frontal", "side impact",
                           "car-pedestrian", "car cyclists", "追尾", "正面碰撞", "侧面碰撞",
                           "行人碰撞", "自行车碰撞", "lane departure", "偏离车道"])

        if is_collision:
            return self._match_collision_speed(hazard_lower, vehicle_speed)

        # ── L:M:N 映射匹配 ──
        for event_key, event_info in self.HAZARD_EVENT_MAP.items():
            if any(kw in hazard_lower for kw in event_info["keywords"]):
                s = event_info.get("s")
                if s:
                    return s
                note = event_info.get("note", "")
                if "speed" in note or "速度" in note:
                    speed_th = event_info.get("speed_threshold", 15)
                    if vehicle_speed > speed_th:
                        return event_info.get("s_speed_high", "S3")
                    else:
                        return event_info.get("s_speed_low", "S2")
                if "adult" in hazard_lower or "adult" in hazard_lower:
                    if "child" in hazard_lower or "child" in hazard_lower:
                        return "S2"
                    return event_info.get("s_adult", "S1")
                if "child" in hazard_lower or "儿童" in hazard_lower:
                    return event_info.get("s_child", "S2")
                if "driver" in hazard_lower or "驾驶员" in hazard_lower:
                    return event_info.get("s_driver", "S2")

        return None

    def _match_collision_speed(self, hazard_lower: str, vehicle_speed: int) -> str:
        """
        碰撞类型 → 按I列速度映射表判定S值
        根据碰撞关键词匹配I列条目，再利用速度条件确定S
        """
        # 识别碰撞类型
        # 语义规则：
        #   "与前车碰撞" = 追尾(rear-end)  → 自己的车撞前车
        #   "与后车碰撞" = 正面(frontal)   → 后车撞自己
        collision_type = None
        if any(kw in hazard_lower for kw in ["rear-end", "追尾", "rear", "前车", "撞上前车", "与前车发生碰撞"]):
            collision_type = "rear-end collision"
        elif any(kw in hazard_lower for kw in ["frontal", "正面", "front", "后车", "被后车撞"]):
            collision_type = "frontal collision"
        elif any(kw in hazard_lower for kw in ["side", "侧面", "side impact", "侧面碰撞"]):
            collision_type = "side collision"
        elif any(kw in hazard_lower for kw in ["pedestrian", "行人", "行人与", "碰撞行人"]):
            collision_type = "car-pedestrian"
        elif any(kw in hazard_lower for kw in ["cyclist", "bike", "bicycle", "自行车", "骑行者", "骑车"]):
            collision_type = "car cyclists"
        elif any(kw in hazard_lower for kw in ["destabilization", "lane departure", "偏离", "失稳", "车道偏离"]):
            return "S3"

        # 兜底：如果包含"碰撞"关键词但无明确类型，按一般正面碰撞处理
        if not collision_type:
            if any(kw in hazard_lower for kw in ["碰撞", "crash", "impact", "撞击"]):
                collision_type = "rear-end collision"  # 默认追尾（最常见场景）
            else:
                return "S1"  # 无法判断，默认为S1

        # 在I列映射表中找到匹配的S值
        best_match = ("S2", False)
        for entry in self.I_COLUMN_COLLISION_MAP:
            if collision_type.lower() in entry[0].lower():
                try:
                    if entry[3](vehicle_speed):
                        # 检查该条目在I列中的S值
                        s_raw = entry[2]
                        if vehicle_speed == 0:
                            return "S1"
                        return s_raw
                except:
                    continue

        return best_match[0]

    def _evaluate_ais_and_severity(self, hazard_lower: str, affected_users: List[str]) -> Optional[str]:
        """
        步骤1-2: 未匹配到L列 → 分析AIS等级 + 概率 + 参考B7:F9 S值定义

        参考 Severity sheet B9:F9 的概率要求（More than 10% probability）：
        S0 (AIS 0): less than 10% probability of AIS 1-6; or damage not safety-related
        S1 (AIS 1-2): More than 10% probability of AIS 1-6 (and not S2 or S3)
        S2 (AIS 3-4): More than 10% probability of AIS 3-6 (and not S3)
        S3 (AIS 5-6): More than 10% probability of AIS 5-6

        评估步骤：
        1. 识别AIS最高等级 (ais_max)
        2. 判断概率是否 > 10%（默认>10%为true，除非描述含low probability/rare）
        3. 基于 ais_max × probability ≥ 10% 判定S等级
        """
        # ── 评估概率：默认>10%，除非描述明确为低概率 ──
        probability_over_10pct = not any(kw in hazard_lower for kw in
                                          ["low probability", "rare", "unlikely", "improbable",
                                           "less than 10%", "<10%", "极低概率", "罕见"])

        # ── 步骤1: 识别AIS最高等级 ──

        # AIS 6: 极危/致命
        ais_6 = any(kw in hazard_lower for kw in
                    ["extremely critical", "fatal injury", "fatalities",
                     "multiple fatalities", "mass casualty",
                     "spinal fractures.*above.*cervical",
                     "exsanguination", "decapitation",
                     "极危", "惨死", "多器官衰竭"])

        # AIS 5: 临界/危及生命
        ais_5 = any(kw in hazard_lower for kw in
                    ["critical injury", "life-threatening.*uncertain", "survival uncertain",
                     "cardiac tear", "intestinal tear",
                     "spinal fracture.*cord damage", "intracranial bleeding.*12h",
                     "严重危及生命", "存活不确定"]) or \
                any(kw in hazard_lower for kw in
                    ["fatal", "death", "kill", "mortality",
                     "致命", "死亡", "asphyxia", "electric shock"])

        # AIS 4: 严重(可能存活)
        ais_4 = any(kw in hazard_lower for kw in
                    ["severe.*life-threatening", "survival probable",
                     "concussion.*12h", "paradoxical breathing",
                     "intracranial bleeding", "severe organ",
                     "严重受伤.*危及", "开颅"])

        # AIS 3: 严重(非致命)
        ais_3 = any(kw in hazard_lower for kw in
                    ["severe but not life-threatening", "skull fracture",
                     "spinal dislocation", "uncomplicated rib fracture",
                     "multiple rib fracture", "rib fracture",
                     "肋骨骨折", "严重受伤", "多处骨折", "颅骨骨折"])

        # AIS 2: 中度
        ais_2 = any(kw in hazard_lower for kw in
                    ["moderate injury", "deep flesh wound",
                     "concussion.*unconsciousness", "uncomplicated long bone fracture",
                     "uncomplicated rib fracture", "rib fracture",
                     "骨折", "中度伤", "深层"])

        # AIS 1: 轻度
        ais_1 = any(kw in hazard_lower for kw in
                    ["light injury", "minor injury", "whiplash",
                     "skin-deep wound", "muscle pain", "abrasion",
                     "superficial", "light fracture", "finger fracture",
                     "squeeze", "pinch", "bruise", "cut", "laceration",
                     "轻伤", "擦伤", "挫伤", "夹伤", "挤压", "擦碰",
                     "injured", "injury", "wound", "hurt", "受伤", "碰撞", "撞击"])

        # AIS 0: 无伤
        ais_0 = any(kw in hazard_lower for kw in
                    ["no injury", "trivial", "inconvenience",
                     "无伤害", "轻微", "不便"]) if not (ais_1 or ais_2 or ais_3 or ais_4 or ais_5 or ais_6) else False

        # ── 确定最大AIS等级 ──
        if ais_6:
            ais_max = 6
        elif ais_5:
            ais_max = 5
        elif ais_4:
            ais_max = 4
        elif ais_3:
            ais_max = 3
        elif ais_2:
            ais_max = 2
        elif ais_1:
            ais_max = 1
        elif ais_0:
            ais_max = 0
        else:
            return None

        # ── 步骤2: 参考B7:F9 S值定义 + B9:F9概率要求 ──
        # B9: S = "AIS 0 and less than 10% probability of AIS 1-6"
        # D9: S1 = "More than 10% probability of AIS 1-6 (and not S2 or S3)"
        # E9: S2 = "More than 10% probability of AIS 3-6 (and not S3)"
        # F9: S3 = "More than 10% probability of AIS 5-6"

        if probability_over_10pct:
            if ais_max >= 5:
                return "S3"  # F9: >10% prob AIS 5-6
            elif ais_max >= 3:
                return "S2"  # E9: >10% prob AIS 3-6 (>5 handled by S3)
            elif ais_max >= 1:
                return "S1"  # D9: >10% prob AIS 1-6 (>3 handled by S2)
            else:
                return "S0"
        else:
            # 概率<10%
            if ais_max >= 5:
                # AIS 5-6 但概率<10%: 降级到S2 (参照B9:F9)
                return "S2"
            elif ais_max >= 3:
                # AIS 3-4 但概率<10%: 降级到S1
                return "S1"
            else:
                return "S0"


class Step10_SeverityScoring(HARAStep):
    """步骤10: 严重度打分"""

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤10"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}

        severity_reasoning = context.get("severity_reasoning", {})
        if not severity_reasoning:
            return {"success": False, "error": "未找到严重度合理性分析，请先执行步骤9"}

        severity_scores = {}
        total_scores = 0

        for func, malf_reasoning in severity_reasoning.items():
            malf_scores = {}
            for malf, scenarios in malf_reasoning.items():
                scenario_scores = []
                for sr in scenarios:
                    score = self._score_severity(sr["severity_reason"])
                    scenario_scores.append({
                        "hazard_event": sr["hazard_event"],
                        "severity_score": score
                    })
                malf_scores[malf] = scenario_scores
                total_scores += len(scenario_scores)
            severity_scores[func] = malf_scores

        context["severity_scores"] = severity_scores
        logger.info(f"完成了 {total_scores} 项严重度打分")

        return {
            "success": True,
            "severity_scores": severity_scores,
            "total_scores": total_scores,
            "message": f"成功完成{total_scores}项严重度打分"
        }

    def _score_severity(self, reason: str) -> str:
        """从合理性描述中提取分值"""
        for score in ["S3", "S2", "S1", "S0"]:
            if score in reason:
                return score
        return "S0"


class Step11_ExposureReasoning(HARAStep):
    """步骤11: 暴露度合理性分析"""

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤11"""
        logger.info(f"执行{self.get_description()}")

        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}

        excel_path = context.get("excel_template_path")
        excel_processor = ExcelProcessor(excel_path)
        if not excel_processor.load_excel():
            return {"success": False, "error": "加载Excel失败"}

        exposure_criteria = excel_processor.get_exposure_criteria()

        refined_function_scenarios = context.get("refined_function_scenarios", {})
        if not refined_function_scenarios:
            return {"success": False, "error": "未找到细化场景，请先执行步骤7"}

        exposure_reasoning = {}
        total_reasonings = 0

        for func, malf_scenarios in refined_function_scenarios.items():
            malf_reasoning = {}
            for malf, scenarios in malf_scenarios.items():
                scenario_reasoning = []
                for rs in scenarios:
                    scenario_info = {
                        "main_scenario": rs.get("original_scenario", ""),
                        "refined_scenario": rs.get("refined_scenario", ""),
                        "vehicle_state": rs.get("vehicle_state", ""),
                        "vehicle_speed": rs.get("vehicle_speed", ""),
                        "weather_conditions": rs.get("weather_conditions", ""),
                        "road_surface_conditions": rs.get("road_surface_conditions", "")
                    }
                    reason = self._reason_exposure(scenario_info, exposure_criteria)
                    scenario_reasoning.append({
                        "scenario": rs.get("refined_scenario", ""),
                        "exposure_reason": reason,
                        "exposure_type": "duration" if "高速" in rs.get("refined_scenario", "") or "日常" in rs.get("refined_scenario", "") else "frequency"
                    })
                malf_reasoning[malf] = scenario_reasoning
                total_reasonings += len(scenario_reasoning)
            exposure_reasoning[func] = malf_reasoning

        context["exposure_reasoning"] = exposure_reasoning
        context["exposure_criteria"] = exposure_criteria
        logger.info(f"完成了 {total_reasonings} 项暴露度合理性分析")

        return {
            "success": True,
            "exposure_reasoning": exposure_reasoning,
            "total_reasonings": total_reasonings,
            "message": f"成功分析{total_reasonings}项暴露度合理性"
        }

    def _reason_exposure(self, scenario_info: Dict, criteria: Dict) -> str:
        """
        根据场景描述和标准推理暴露度合理性
        GB/T 34590.3-2022: E4-频繁, E3-经常, E2-偶尔, E1-极少, E0-不可能
        """
        refined_scenario = scenario_info.get("refined_scenario", "")
        vehicle_speed = scenario_info.get("vehicle_speed", "")
        weather = scenario_info.get("weather_conditions", "")
        road_surface = scenario_info.get("road_surface_conditions", "")
        
        scenario_lower = refined_scenario.lower()
        
        duration_based = criteria.get("duration_based", {})
        frequency_based = criteria.get("frequency_based", {})
        exposure_levels = criteria.get("exposure_levels", {})
        
        duration_c = duration_based.get("C", "基于时长")
        duration_d = duration_based.get("D", "基于时长")
        freq_c = frequency_based.get("C", "基于频次")
        freq_d = frequency_based.get("D", "基于频次")
        
        scenario_parts = []
        if vehicle_speed:
            scenario_parts.append(f"车速:{vehicle_speed}")
        if weather:
            scenario_parts.append(f"天气:{weather}")
        if road_surface:
            scenario_parts.append(f"路面:{road_surface}")
        scenario_context = "，".join(scenario_parts) if scenario_parts else ""
        
        exposure_rules = {
            "E4": {
                "keywords": ["高速", "高速公路", "常用", "日常", "通勤", "normal driving",
                           "highway", "frequent", "urban road", "日常驾驶", ">10%"],
                "use_duration": True,
                "reason_template": "该场景在日常驾驶中频繁出现({duration})，{context}符合E4定义: {desc}"
            },
            "E3": {
                "keywords": ["市区", "城市道路", "低速", "普通道路", "suburban",
                           "city driving", "moderate frequency", "weekly", "1-10%"],
                "use_duration": False,
                "reason_template": "该场景在常见驾驶环境下经常出现({freq})，{context}符合E3定义: {desc}"
            },
            "E2": {
                "keywords": ["雨天", "雪天", "夜间", "恶劣天气", "山路", "unusual weather",
                           "night", "adverse conditions", "winter", "specific road", "0.1-1%"],
                "use_duration": False,
                "reason_template": "该场景仅在特定条件下偶尔出现({freq})，{context}符合E2定义: {desc}"
            },
            "E1": {
                "keywords": ["极罕见", "特殊", "极端", "edge case", "rare", "exceptional", "<0.1%"],
                "use_duration": False,
                "reason_template": "该场景极少出现({freq})，{context}符合E1定义: {desc}"
            },
            "E0": {
                "keywords": ["impossible", "improbable", "theoretical", "理论上"],
                "use_duration": False,
                "reason_template": "该场景几乎不可能出现，符合E0定义: {desc}"
            }
        }
        
        speed_value = re.search(r'(\d+)', vehicle_speed) if vehicle_speed else None
        if speed_value:
            speed = int(speed_value.group(1))
            if speed >= 120:
                e4_info = exposure_levels.get("E4", {})
                level_desc = e4_info.get("duration", "运行时间>10%")
                return f"该场景为高速行驶场景(120km/h以上)，暴露时长占比高({duration_c}:{level_desc})，{scenario_context}，符合E4定义"
            elif speed >= 80:
                e3_info = exposure_levels.get("E3", {})
                level_desc = e3_info.get("duration", "运行时间1-10%")
                return f"该场景为中高速行驶场景(80-120km/h)，{duration_c}:{level_desc}，{scenario_context}，符合E3定义"
        
        for level, rule in exposure_rules.items():
            if any(kw in scenario_lower for kw in rule["keywords"]):
                level_info = exposure_levels.get(level, {})
                level_desc = level_info.get("duration", level_info.get("frequency", "无明确定义"))
                
                if rule["use_duration"]:
                    ref_text = f"{duration_c}:{level_info.get('duration', '')}"
                else:
                    ref_text = f"{freq_c}:{level_info.get('frequency', '')}"
                
                context_str = scenario_context if scenario_context else "在当前驾驶场景下"
                reason = rule["reason_template"].format(
                    duration=ref_text,
                    freq=ref_text,
                    context=context_str,
                    desc=level_desc
                )
                return reason
        
        default_info = exposure_levels.get("E2", {})
        level_desc = default_info.get("frequency", "运行时间0.1-1%")
        return f"该场景具有一般发生概率({freq_c}:{level_desc})，{scenario_context}，默认为E2"


class Step12_ExposureScoring(HARAStep):
    """步骤12: 暴露度打分"""
    
    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤12"""
        logger.info(f"执行{self.get_description()}")
        
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        excel_path = context.get("excel_template_path")
        excel_processor = ExcelProcessor(excel_path)
        if not excel_processor.load_excel():
            return {"success": False, "error": "加载Excel失败"}
        
        vda702_criteria = excel_processor.get_vda702_exposure_criteria()
        exposure_criteria = getattr(excel_processor, 'get_exposure_criteria', lambda: {})()
        
        exposure_reasoning = context.get("exposure_reasoning", {})
        if not exposure_reasoning:
            return {"success": False, "error": "未找到暴露度合理性分析，请先执行步骤11"}
        
        refined_scenarios = context.get("refined_function_scenarios", {})
        
        exposure_scores = {}
        total_scores = 0
        
        for func, malf_reasoning in exposure_reasoning.items():
            malf_scores = {}
            for malf, scenarios in malf_reasoning.items():
                scenario_scores = []
                
                related_refined = refined_scenarios.get(func, {}).get(malf, [])
                
                for i, sr in enumerate(scenarios):
                    scenario_text = sr.get("scenario", "")
                    related_scenario = related_refined[i] if i < len(related_refined) else {}
                    
                    score = self._score_exposure(
                        scenario_text,
                        vda702_criteria,
                        exposure_criteria,
                        related_scenario
                    )
                    scenario_scores.append({
                        "scenario": scenario_text,
                        "exposure_score": score
                    })
                malf_scores[malf] = scenario_scores
                total_scores += len(scenario_scores)
            exposure_scores[func] = malf_scores
            
        context["exposure_scores"] = exposure_scores
        context["vda702_criteria"] = vda702_criteria
        logger.info(f"完成了 {total_scores} 项暴露度打分")
        
        return {
            "success": True,
            "exposure_scores": exposure_scores,
            "total_scores": total_scores,
            "message": f"成功完成{total_scores}项暴露度打分"
        }
    
    def _score_exposure(self, scenario: str, vda702_criteria: Dict, 
                        exposure_criteria: Dict, related_scenario: Dict) -> str:
        """
        基于场景信息和标准进行暴露度打分
        通过模糊匹配查找对应的E值
        """
        scenario_lower = scenario.lower()
        
        vehicle_speed = related_scenario.get("vehicle_speed", "")
        weather = related_scenario.get("weather_conditions", "")
        road_surface = related_scenario.get("road_surface_conditions", "")
        
        keywords_ebMapping = {
            "E4": ["高速", "高速公路", "常用", "日常", "通勤", "highway", "frequent", ">10%"],
            "E3": ["市区", "城市道路", "低速", "普通道路", "urban", "city", "1-10%"],
            "E2": ["雨天", "雪天", "夜间", "恶劣天气", "山路", "night", "adverse", "0.1-1%"],
            "E1": ["极罕见", "特殊", "极端", "rare", "exceptional", "<0.1%"],
            "E0": ["impossible", "improbable", "theoretical"]
        }
        
        if vehicle_speed:
            speed_match = re.search(r'(\d+)', vehicle_speed)
            if speed_match:
                speed = int(speed_match.group(1))
                if speed >= 120:
                    return "E4"
                elif speed >= 80:
                    return "E3"
                elif speed >= 40:
                    return "E2"
        
        if weather:
            weather_lower = weather.lower()
            if any(kw in weather_lower for kw in ["雨", "雪", "冰"]):
                return "E2"
            elif any(kw in weather_lower for kw in ["雾", "大风"]):
                return "E2"
        
        if road_surface:
            road_lower = road_surface.lower()
            if any(kw in road_lower for kw in ["冰", "雪", "湿滑"]):
                return "E2"
        
        keywords_mapping = vda702_criteria.get("keywords_mapping", {})
        for level in ["E4", "E3", "E2", "E1", "E0"]:
            if level in keywords_mapping:
                keywords_text = keywords_mapping[level].lower()
                if any(kw in scenario_lower for kw in keywords_ebMapping.get(level, [])):
                    return level
        
        for level, keywords in keywords_ebMapping.items():
            if any(kw in scenario_lower for kw in keywords):
                return level
        
        exposure_levels = exposure_criteria.get("exposure_levels", {})
        if exposure_levels:
            for level in ["E4", "E3", "E2", "E1", "E0"]:
                if level in scenario:
                    return level
        
        return "E2"


class Step13_ControllabilityReasoning(HARAStep):
    """步骤13: 可控度合理性分析

    可控度论证逻辑类型（基于HARA经验库分析总结）：
    1. 感知能力：行为人能否识别危险
       - 关键词：recognize, realize, detect, notice, observe
    2. 反应时间：是否有足够时间应对
       - 关键词：enough time, reaction time, sufficient time, timely
    3. 物理干预：能否通过操作避免伤害
       - 关键词：brake, steering, change lane, stop, avoid, apply
    4. 系统保障：功能安全措施是否生效
       - 关键词：anti-pinch, switch to, function, can be controlled
    5. 防护装备：安全设备是否提供保护
       - 关键词：seat belt, child seat, protected by
    6. 外部协助：第三方能否施救
       - 关键词：rescuer, external, looked after, assistance
    7. 工况依赖：可控性随速度/天气/距离变化
       - 关键词：low speed, high speed, normal distance, weather

    典型论证模板：
    [可控性级别] - [主语] + [感知/干预能力] + [时间条件/系统保障]
    例如：Simply controllable - Driver could realize the moving of the window,
          and the window will stop inside the anti-pinch area.
    """

    CONTROLLABILITY_LEVELS = {
        "C3": {"label": "难以控制", "keywords": ["difficult to control", "uncontrollable", "difficult to control or uncontrollable"]},
        "C2": {"label": "一般可控", "keywords": ["normally controllable", "normal to control", "controllable in general", "normally controllable"]},
        "C1": {"label": "简单可控", "keywords": ["simply controllable", "easy to control", "easy to control"]},
        "C0": {"label": "可控", "keywords": ["well controllable", "controllable", "easy to control", "simply controllable"]}
    }

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤13"""
        logger.info(f"执行{self.get_description()}")

        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}

        excel_path = context.get("excel_template_path")
        excel_processor = ExcelProcessor(excel_path)
        if not excel_processor.load_excel():
            return {"success": False, "error": "加载Excel失败"}

        controllability_criteria = excel_processor.get_sheet_data("Controllability")

        function_hazard_events = context.get("function_hazard_events", {})
        if not function_hazard_events:
            return {"success": False, "error": "未找到危害事件，请先执行步骤8"}

        controllability_reasoning = {}
        total_reasonings = 0

        for func, malf_events in function_hazard_events.items():
            malf_reasoning = {}
            for malf, scenarios in malf_events.items():
                scenario_reasoning = []
                for se in scenarios:
                    logic_types, reason = self._reason_controllability(se["hazard_event"], controllability_criteria)
                    scenario_reasoning.append({
                        "hazard_event": se["hazard_event"],
                        "controllability_reason": reason,
                        "logic_types_used": logic_types
                    })
                malf_reasoning[malf] = scenario_reasoning
                total_reasonings += len(scenario_reasoning)
            controllability_reasoning[func] = malf_reasoning

        context["controllability_reasoning"] = controllability_reasoning
        logger.info(f"完成了 {total_reasonings} 项可控度合理性分析")

        return {
            "success": True,
            "controllability_reasoning": controllability_reasoning,
            "total_reasonings": total_reasonings,
            "message": f"成功分析{total_reasonings}项可控度合理性"
        }

    def _reason_controllability(self, hazard_event: str, criteria: Any) -> Tuple[List[str], str]:
        """
        根据危害事件和标准推理可控度合理性
        GB/T 34590.3-2022: C3-难以控制, C2-一般可控, C1-简单可控, C0-可控

        返回: (使用的逻辑类型列表, 可控度合理性描述)
        """
        hazard_lower = hazard_event.lower()

        logic_analysis = self._analyze_controllability_logic_types(hazard_event)

        controllability_level = self._determine_controllability_level(hazard_lower, logic_analysis)

        reason = self._generate_reason_template(controllability_level, logic_analysis, hazard_event)

        used_logic_types = [lt for lt, info in logic_analysis.items() if info["matched"]]

        return used_logic_types, reason

    def _analyze_controllability_logic_types(self, hazard_event: str) -> Dict[str, Dict]:
        """分析危害事件中涉及的7种可控度论证逻辑类型"""
        hazard_lower = hazard_event.lower()

        return {
            "感知能力": {
                "keywords": ["recognize", "realize", "detect", "notice", "observe", "see", "identify",
                            "识别", "发现", "注意到", "看到", "观察"],
                "matched": any(kw in hazard_lower for kw in ["recognize", "realize", "detect", "notice",
                              "observe", "see", "identify", "识别", "发现", "注意"])
            },
            "反应时间": {
                "keywords": ["enough time", "sufficient time", "reaction time", "timely", "enough reaction",
                            "timely react", "has enough time", "sufficient reaction", "反应时间", "足够时间", "及时"],
                "matched": any(kw in hazard_lower for kw in ["enough time", "sufficient time", "reaction time",
                              "timely", "及时", "足够"])
            },
            "物理干预": {
                "keywords": ["brake", "steering", "steer", "change lane", "stop", "avoid", "apply brakes",
                            "apply steering", "slow down", "slowing", "wiper", "switch", "break the window",
                            "打开车门", "刹车", "转向", "变道", "停车", "躲避", "制动"],
                "matched": any(kw in hazard_lower for kw in ["brake", "steering", "steer", "change lane",
                              "stop", "avoid", "slow down", "wiper", "switch", "刹车", "转向", "变道", "停"])
            },
            "系统保障": {
                "keywords": ["anti-pinch", "anti-pitch", "function", "can be switched", "can be controlled",
                            "goes back", "will stop", "safety function", "系统功能", "防夹", "功能"],
                "matched": any(kw in hazard_lower for kw in ["anti-pinch", "anti-pitch", "can be switched",
                              "can be controlled", "goes back", "will stop", "防夹", "功能"])
            },
            "防护装备": {
                "keywords": ["seat belt", "child seat", "protected by", "fasten", "belt",
                            "安全带", "儿童座椅", "防护", "系安全带"],
                "matched": any(kw in hazard_lower for kw in ["seat belt", "child seat", "protected by",
                              "fasten", "belt", "安全带", "儿童座椅", "防护"])
            },
            "外部协助": {
                "keywords": ["rescuer", "external", "looked after", "assistance", "another passenger",
                            "救援人员", "外部", "协助", "他人帮助", "看护"],
                "matched": any(kw in hazard_lower for kw in ["rescuer", "external", "looked after",
                              "assistance", "another passenger", "救援", "外部", "协助"])
            },
            "工况依赖": {
                "keywords": ["low speed", "high speed", "normal distance", "normal following", "weather",
                            "heavy rain", "lower speed", "at low speeds", "at high speeds",
                            "低速", "高速", "正常距离", "天气", "大雨"],
                "matched": any(kw in hazard_lower for kw in ["low speed", "high speed", "normal distance",
                              "normal following", "weather", "heavy rain", "lower speed", "low speeds",
                              "高速", "低速", "距离", "天气"])
            }
        }

    def _determine_controllability_level(self, hazard_lower: str, logic_analysis: Dict) -> str:
        """根据逻辑分析结果确定可控度等级"""
        if any(kw in hazard_lower for kw in ["normal to control", "normally controllable", "controllable in general",
                                               "may not have enough time"]):
            return "C2"

        if any(kw in hazard_lower for kw in ["difficult to control", "difficult to control or uncontrollable",
                                               "unable to react in time", "cannot open the car door in time"]):
            return "C3"

        if any(kw in hazard_lower for kw in ["sudden", "unexpected", "uncontrollable", "no warning",
                                               "immediate", "loss of control", "突然", "毫无征兆", "无法预判",
                                               "cannot open", "cannot slow down", "unable to"]):
            return "C3"

        if any(kw in hazard_lower for kw in ["easy to control", "well controllable", "simply controllable",
                                               "escape", "can escape", "can easily", "easily avoid",
                                               "easily recognize", "enough time to avoid", "can detect"]):
            return "C0"

        perception = logic_analysis.get("感知能力", {}).get("matched", False)
        reaction_time = logic_analysis.get("反应时间", {}).get("matched", False)
        physical = logic_analysis.get("物理干预", {}).get("matched", False)
        system = logic_analysis.get("系统保障", {}).get("matched", False)
        equipment = logic_analysis.get("防护装备", {}).get("matched", False)
        external = logic_analysis.get("外部协助", {}).get("matched", False)
        condition = logic_analysis.get("工况依赖", {}).get("matched", False)

        matched_count = sum([perception, reaction_time, physical, system, equipment, external, condition])

        if matched_count >= 3 and (system or (perception and reaction_time) or (perception and physical)):
            return "C0"
        elif matched_count >= 2 and (perception or physical):
            return "C1"
        elif matched_count >= 1:
            return "C2"
        else:
            return "C2"

    def _generate_reason_template(self, controllability_level: str, logic_analysis: Dict, hazard_event: str) -> str:
        """根据可控度等级和逻辑类型生成典型论证模板格式的合理性描述"""
        level_info = self.CONTROLLABILITY_LEVELS.get(controllability_level, {"label": "一般可控"})
        level_label = level_info["label"]

        logic_parts = []

        if logic_analysis.get("感知能力", {}).get("matched"):
            logic_parts.append("行为人能够感知/识别危险")

        if logic_analysis.get("反应时间", {}).get("matched"):
            logic_parts.append("有足够的反应时间")

        if logic_analysis.get("物理干预", {}).get("matched"):
            logic_parts.append("可通过制动/转向/变道等操作避免")

        if logic_analysis.get("系统保障", {}).get("matched"):
            logic_parts.append("系统安全功能提供保障")

        if logic_analysis.get("防护装备", {}).get("matched"):
            logic_parts.append("安全防护装备提供保护")

        if logic_analysis.get("外部协助", {}).get("matched"):
            logic_parts.append("外部人员可提供协助")

        if logic_analysis.get("工况依赖", {}).get("matched"):
            logic_parts.append("特定工况下可控")

        logic_description = "，".join(logic_parts) if logic_parts else "通过常规驾驶操作可控制"

        reason_template = f"[{controllability_level} {level_label}] - {logic_description}。{hazard_event[:60]}..."

        return reason_template


class Step14_ControllabilityScoring(HARAStep):
    """步骤14: 可控度打分"""

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤14"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        controllability_reasoning = context.get("controllability_reasoning", {})
        if not controllability_reasoning:
            return {"success": False, "error": "未找到可控度合理性分析，请先执行步骤13"}
        
        controllability_scores = {}
        total_scores = 0
        
        for func, malf_reasoning in controllability_reasoning.items():
            malf_scores = {}
            for malf, scenarios in malf_reasoning.items():
                scenario_scores = []
                for sr in scenarios:
                    score = self._score_controllability(sr["controllability_reason"])
                    scenario_scores.append({
                        "hazard_event": sr["hazard_event"],
                        "controllability_score": score
                    })
                malf_scores[malf] = scenario_scores
                total_scores += len(scenario_scores)
            controllability_scores[func] = malf_scores
            
        context["controllability_scores"] = controllability_scores
        logger.info(f"完成了 {total_scores} 项可控度打分")
        
        return {
            "success": True,
            "controllability_scores": controllability_scores,
            "total_scores": total_scores,
            "message": f"成功完成{total_scores}项可控度打分"
        }
    
    def _score_controllability(self, reason: str) -> str:
        """从合理性描述中提取分值"""
        for score in ["C3", "C2", "C1", "C0"]:
            if score in reason:
                return score
        return "C0"


class Step15_DetermineASIL(HARAStep):
    """步骤15: 判定ASIL等级"""

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤15"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        # 获取Excel模板路径读取ASIL_Table
        excel_path = context.get("excel_template_path")
        excel_processor = ExcelProcessor(excel_path)
        if not excel_processor.load_excel():
            return {"success": False, "error": "加载Excel失败"}
        
        try:
            asil_matrix = TemplateASILMatrix(excel_path)
        except (FileNotFoundError, ValueError) as exc:
            return {"success": False, "error": f"ASIL_Table加载失败: {exc}"}
        
        # 获取严重度、暴露度、可控度打分
        severity_scores = context.get("severity_scores", {})
        exposure_scores = context.get("exposure_scores", {})
        controllability_scores = context.get("controllability_scores", {})
        
        if not severity_scores or not exposure_scores or not controllability_scores:
            return {"success": False, "error": "缺少S/E/C打分，请先执行步骤10、12、14"}
        
        # 判定ASIL等级（以输入模板中的 ASIL_Table 为唯一判定源）
        asil_results = {}
        ftti_results = {}
        ftti_estimator = FTTIEstimator()
        function_hazard_events = context.get("function_hazard_events", {})
        total_items = 0
        
        for func in severity_scores.keys():
            malf_asil = {}
            malf_ftti = {}
            if func in exposure_scores and func in controllability_scores:
                for malf in severity_scores[func].keys():
                    severity_list = severity_scores[func].get(malf, [])
                    exposure_list = exposure_scores[func].get(malf, [])
                    controllability_list = controllability_scores[func].get(malf, [])
                    
                    item_asils = []
                    item_ftti = []
                    hazard_events = function_hazard_events.get(func, {}).get(malf, [])
                    for i in range(len(severity_list)):
                        s = severity_list[i].get("severity_score", "S0") if i < len(severity_list) else "S0"
                        e = exposure_list[i].get("exposure_score", "E0") if i < len(exposure_list) else "E0"
                        c = controllability_list[i].get("controllability_score", "C0") if i < len(controllability_list) else "C0"
                        
                        asil = asil_matrix.determine(s, e, c)
                        item_asils.append({
                            "severity": s,
                            "exposure": e,
                            "controllability": c,
                            "ASIL": asil
                        })
                        event = (
                            hazard_events[i]
                            if i < len(hazard_events) and isinstance(hazard_events[i], dict)
                            else {}
                        )
                        scenario = dict(event.get("scenario_facts", {}))
                        scenario.update({key: value for key, value in event.items() if key not in scenario})
                        item_ftti.append(ftti_estimator.evaluate(
                            scenario, str(event.get("hazard_event", "")), asil
                        ))
                        total_items += 1
                    malf_asil[malf] = item_asils
                    malf_ftti[malf] = item_ftti
            asil_results[func] = malf_asil
            ftti_results[func] = malf_ftti
        
        context["asil_results"] = asil_results
        context["ftti_results"] = ftti_results
        context["asil_determination_method"] = "template_matrix_lookup"
        context["asil_matrix_source"] = asil_matrix.source
        logger.info(f"判定了 {total_items} 项ASIL等级")
        
        return {
            "success": True,
            "asil_results": asil_results,
            "ftti_results": ftti_results,
            "total_items": total_items,
            "message": f"成功判定{total_items}项ASIL等级"
        }
    
class Step16_SafetyGoal(HARAStep):
    """步骤16: 填写安全目标"""

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤16"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        asil_results = context.get("asil_results", {})
        function_hazard_events = context.get("function_hazard_events", {})
        function_malfunctions = context.get("function_malfunctions", {})
        ftti_results = context.get("ftti_results", {})
        
        if not asil_results:
            return {"success": False, "error": "未找到ASIL结果，请先执行步骤15"}
        
        # 获取功能列表
        functions = context.get("functions", [])
        
        # 生成安全目标
        safety_goals = {}
        total_goals = 0
        
        for func in asil_results.keys():
            malf_goals = {}
            if func in function_hazard_events and func in function_malfunctions:
                for malf in asil_results[func].keys():
                    asil_list = asil_results[func][malf]
                    hazard_events = function_hazard_events[func].get(malf, [])
                    ftti_list = ftti_results.get(func, {}).get(malf, [])
                    malfunctions = function_malfunctions[func]
                    
                    goal_items = []
                    for i, asil_item in enumerate(asil_list):
                        asil = asil_item.get("ASIL", "QM")
                        hazard_event = hazard_events[i].get("hazard_event", "") if i < len(hazard_events) else ""
                        scenario = hazard_events[i].get("refined_scenario", "") if i < len(hazard_events) else ""
                        
                        # 获取引导词描述
                        guideword_desc = ""
                        for mf in malfunctions:
                            if mf.get("malfunction_description") == malf:
                                guideword_desc = mf.get("guideword_description", "")
                                break
                        
                        if asil in ["A", "B", "C", "D"]:
                            safety_goal = self._generate_safety_goal(guideword_desc, malf, scenario, hazard_event)
                            sg_id = "SG_FALLBACK_" + hashlib.sha1(
                                safety_goal.encode("utf-8")
                            ).hexdigest()[:8].upper()
                            ftti_item = ftti_list[i] if i < len(ftti_list) else {}
                            goal_items.append({
                                "ASIL": asil,
                                "sg_id": sg_id,
                                "safety_goal": safety_goal,
                                "ftti_requirement": ftti_item.get("ftti_requirement", "NEEDS_REVIEW"),
                                "ftti_status": ftti_item.get("ftti_status", "NEEDS_REVIEW"),
                            })
                        else:
                            goal_items.append({
                                "ASIL": asil,
                                "safety_goal": "NA"
                            })
                        total_goals += 1
                    malf_goals[malf] = goal_items
            safety_goals[func] = malf_goals
        
        context["safety_goals"] = safety_goals
        logger.info(f"生成了 {total_goals} 个安全目标")
        
        return {
            "success": True,
            "safety_goals": safety_goals,
            "total_goals": total_goals,
            "message": f"成功生成{total_goals}个安全目标"
        }
    
    def _generate_safety_goal(self, guideword_desc: str, malfunction: str, scenario: str, hazard: str) -> str:
        """生成安全目标描述"""
        guideword_text = guideword_desc if guideword_desc else "功能异常"
        malfunction_text = malfunction if malfunction else "未知失效"
        scenario_text = scenario.split(" (补充条件:")[0] if scenario else "特定场景"
        hazard_text = hazard if hazard else "安全隐患"
        
        return f"避免因{guideword_text}{malfunction_text}导致在{scenario_text}发生{hazard_text}"


class Step17_SafetyState(HARAStep):
    """步骤17: 安全状态"""

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行步骤17"""
        logger.info(f"执行{self.get_description()}")
        
        # 验证输入
        if not self.validate_input(context):
            return {"success": False, "error": "输入验证失败"}
        
        safety_goals = context.get("safety_goals", {})
        function_outputs = context.get("function_outputs", {})
        
        if not safety_goals:
            return {"success": False, "error": "未找到安全目标，请先执行步骤16"}
        
        # 生成安全状态
        safety_states = {}
        total_states = 0
        
        for func, malf_goals in safety_goals.items():
            malf_states = {}
            for malf, goal_items in malf_goals.items():
                state_items = []
                for goal_item in goal_items:
                    safety_goal = goal_item.get("safety_goal", "")
                    sg_id = goal_item.get("sg_id", "")
                    
                    if safety_goal == "NA" or not safety_goal:
                        state_items.append({
                            "sg_id": sg_id,
                            "safety_goal": safety_goal,
                            "safety_state": "NA"
                        })
                    else:
                        # 基于功能描述推理安全状态
                        func_output = function_outputs.get(func, "") if func in function_outputs else ""
                        safety_state = self._infer_safety_state(safety_goal, func_output)
                        state_items.append({
                            "sg_id": sg_id,
                            "safety_goal": safety_goal,
                            "safety_state": safety_state
                        })
                    total_states += 1
                malf_states[malf] = state_items
            safety_states[func] = malf_states
        
        context["safety_states"] = safety_states
        context["safe_states"] = safety_states
        logger.info(f"推理了 {total_states} 个安全状态")
        
        return {
            "success": True,
            "safety_states": safety_states,
            "total_states": total_states,
            "message": f"成功推理{total_states}个安全状态"
        }
    
    def _infer_safety_state(self, safety_goal: str, func_output: str) -> str:
        """推理安全状态"""
        goal_lower = safety_goal.lower()
        
        if "制动" in goal_lower or "刹车" in goal_lower:
            return "启用制动失效警告，限制车速并提示驾驶员检查"
        elif "转向" in goal_lower:
            return "禁用转向助力，保持机械转向能力并提示驾驶员"
        elif "加速" in goal_lower or "动力" in goal_lower:
            return "切断动力输出，启用跛行模式"
        elif "灯光" in goal_lower:
            return "启用故障灯光提示，保障基本照明"
        elif "气囊" in goal_lower:
            return "禁用气囊点爆，保持气囊保护状态"
        elif "车门" in goal_lower:
            return "锁定车门，防止意外开启"
        else:
            return f"关闭{func_output}输出并提醒驾驶员"


class HARAEngine:
    """HARA引擎主类"""
    
    def __init__(self):
        """初始化HARA引擎"""
        self.steps = {}
        self.context = {}
        self.results = {}
        self.experience_library = None
        
    def initialize(self, item_definition_path: str, excel_template_path: str, 
                   standard_pdf_path: Optional[str] = None,
                   user_input: Optional[str] = None):
        """
        初始化引擎
        
        Args:
            item_definition_path: Item Definition文档路径
            excel_template_path: Excel模板路径
            standard_pdf_path: 标准PDF文档路径（可选）
            user_input: 用户输入信息（用于匹配经验库）
        """
        self.context = {
            "item_definition_path": item_definition_path,
            "excel_template_path": excel_template_path,
            "standard_pdf_path": standard_pdf_path
        }
        
        self._initialize_experience_library()
        
        if user_input and self.experience_library:
            match_result = self.experience_library.match_input(user_input)
            if match_result.get("success"):
                self.context["experience_library_match"] = match_result
                logger.info(f"用户输入匹配到经验库: {match_result.get('matched_subsystems', [])}")
        
        # 加载Excel模板获取步骤信息
        excel_processor = ExcelProcessor(excel_template_path)
        if excel_processor.load_excel():
            steps_info = excel_processor.get_ai_process_steps()
            
            # 创建步骤对象
            for step_info in steps_info:
                step_num = step_info["step_number"]
                
                # 根据步骤号创建对应的步骤对象
                if step_num == 1:
                    self.steps[step_num] = Step1_ListFunctions(step_num, step_info)
                elif step_num == 2:
                    self.steps[step_num] = Step2_ListOutputs(step_num, step_info)
                elif step_num == 3:
                    self.steps[step_num] = Step3_ListGuidewords(step_num, step_info)
                elif step_num == 4:
                    self.steps[step_num] = Step4_GenerateMalfunctions(step_num, step_info)
                elif step_num == 5:
                    self.steps[step_num] = Step5_AnalyzeHazards(step_num, step_info)
                elif step_num == 6:
                    self.steps[step_num] = Step6_CombineScenarios(step_num, step_info)
                elif step_num == 7:
                    self.steps[step_num] = Step7_RefineScenarios(step_num, step_info)
                elif step_num == 8:
                    self.steps[step_num] = Step8_AnalyzeHazardEvents(step_num, step_info)
                elif step_num == 9:
                    self.steps[step_num] = Step9_SeverityReasoning(step_num, step_info)
                elif step_num == 10:
                    self.steps[step_num] = Step10_SeverityScoring(step_num, step_info)
                elif step_num == 11:
                    self.steps[step_num] = Step11_ExposureReasoning(step_num, step_info)
                elif step_num == 12:
                    self.steps[step_num] = Step12_ExposureScoring(step_num, step_info)
                elif step_num == 13:
                    self.steps[step_num] = Step13_ControllabilityReasoning(step_num, step_info)
                elif step_num == 14:
                    self.steps[step_num] = Step14_ControllabilityScoring(step_num, step_info)
                elif step_num == 15:
                    self.steps[step_num] = Step15_DetermineASIL(step_num, step_info)
                elif step_num == 16:
                    self.steps[step_num] = Step16_SafetyGoal(step_num, step_info)
                elif step_num == 17:
                    self.steps[step_num] = Step17_SafetyState(step_num, step_info)
                else:
                    # 默认步骤对象
                    self.steps[step_num] = HARAStep(step_num, step_info)


        
        logger.info(f"HARA引擎初始化完成，共{len(self.steps)}个步骤")
    
    def _initialize_experience_library(self):
        """初始化HARA经验库"""
        try:
            self.experience_library = HARAExperienceLibrary()
            if self.experience_library.initialize():
                logger.info("HARA经验库初始化成功")
            else:
                logger.warning("HARA经验库初始化失败")
                self.experience_library = None
        except Exception as e:
            logger.warning(f"HARA经验库初始化异常: {e}")
            self.experience_library = None
    
    def check_experience_library(self, subsystem: str = None, function: str = None) -> Dict[str, Any]:
        """
        检查HARA经验库并返回匹配的HARA分析结论
        
        Args:
            subsystem: 子系统名称
            function: 功能名称
            
        Returns:
            经验库匹配结果
        """
        if not self.experience_library:
            self._initialize_experience_library()
            
        if not self.experience_library:
            return {"success": False, "error": "HARA经验库不可用"}
        
        if subsystem:
            return self.experience_library.search_by_subsystem(subsystem)
        elif function:
            return self.experience_library.search_by_function(function)
        else:
            return self.experience_library.get_index_summary()
    
    def use_experience_data(self, subsystem: str = None, function: str = None) -> Dict[str, Any]:
        """
        使用经验库数据填充上下文
        
        Args:
            subsystem: 子系统名称
            function: 功能名称
            
        Returns:
            是否成功使用经验库数据
        """
        if not self.experience_library:
            self._initialize_experience_library()
            
        if not self.experience_library:
            return {"success": False, "error": "HARA经验库不可用"}
        
        result = self.check_experience_library(subsystem, function)
        if not result.get("success"):
            return result
        
        hara_data = []
        functions = set()
        
        if subsystem:
            for matched_subsystem, info in result.get("results", {}).items():
                for record in info.get("hara_data", []):
                    hara_data.append(record)
                    if record.get("Function"):
                        functions.add(record.get("Function"))
        
        if function:
            for matched_func, info in result.get("results", {}).items():
                for record in info.get("full_hara_data", []):
                    hara_data.append(record)
                    if record.get("Function"):
                        functions.add(record.get("Function"))
        
        self.context["experience_hara_data"] = hara_data
        self.context["experience_functions"] = list(functions)
        self.context["functions"] = list(functions)
        
        logger.info(f"从经验库加载了 {len(hara_data)} 条HARA记录")
        
        return {
            "success": True,
            "record_count": len(hara_data),
            "function_count": len(functions),
            "functions": list(functions)
        }
    
    def execute_step(self, step_number: int) -> Dict[str, Any]:
        """
        执行单个步骤
        
        Args:
            step_number: 步骤编号
            
        Returns:
            执行结果
        """
        if step_number not in self.steps:
            return {"success": False, "error": f"步骤{step_number}不存在"}
        
        step = self.steps[step_number]
        
        try:
            result = step.execute(self.context)
            
            # 保存结果
            self.results[step_number] = result
            
            # 如果执行成功，更新上下文
            if result.get("success", False):
                # 将结果中的关键数据合并到上下文
                for key, value in result.items():
                    if key not in ["success", "message", "error"]:
                        self.context[key] = value
            
            return result
            
        except Exception as e:
            error_msg = f"执行步骤{step_number}时发生错误: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
    
    def execute_all_steps(self, start_step: int = 1, end_step: int = 17) -> Dict[str, Any]:
        """
        执行多个步骤
        
        Args:
            start_step: 起始步骤
            end_step: 结束步骤
            
        Returns:
            总体执行结果
        """
        overall_result = {
            "success": True,
            "executed_steps": [],
            "failed_steps": [],
            "total_steps": 0,
            "successful_steps": 0
        }
        
        for step_num in range(start_step, end_step + 1):
            if step_num not in self.steps:
                logger.warning(f"步骤{step_num}不存在，跳过")
                continue
            
            logger.info(f"开始执行步骤{step_num}/{end_step}")
            
            result = self.execute_step(step_num)
            
            if result.get("success", False):
                overall_result["successful_steps"] += 1
                overall_result["executed_steps"].append({
                    "step": step_num,
                    "success": True,
                    "message": result.get("message", "")
                })
                logger.info(f"步骤{step_num}执行成功: {result.get('message', '')}")
            else:
                overall_result["success"] = False
                overall_result["failed_steps"].append({
                    "step": step_num,
                    "success": False,
                    "error": result.get("error", "未知错误")
                })
                logger.error(f"步骤{step_num}执行失败: {result.get('error', '')}")
                
                # 可以根据需要决定是否继续执行
                # break  # 如果某个步骤失败就停止
            
            overall_result["total_steps"] += 1
        
        # 计算成功率
        if overall_result["total_steps"] > 0:
            success_rate = overall_result["successful_steps"] / overall_result["total_steps"] * 100
            overall_result["success_rate"] = round(success_rate, 2)
        else:
            overall_result["success_rate"] = 0
        
        logger.info(f"所有步骤执行完成。成功率: {overall_result['success_rate']}%")
        
        return overall_result
    
    def get_context(self) -> Dict[str, Any]:
        """获取当前上下文"""
        return self.context
    
    def get_results(self) -> Dict[str, Any]:
        """获取执行结果"""
        return self.results
    
    def export_results(self, output_dir: str) -> bool:
        """
        导出结果
        
        Args:
            output_dir: 输出目录
            
        Returns:
            是否导出成功
        """
        try:
            os.makedirs(output_dir, exist_ok=True)
            
            # 导出上下文
            context_path = os.path.join(output_dir, "hara_context.json")
            with open(context_path, 'w', encoding='utf-8') as f:
                json.dump(self.context, f, ensure_ascii=False, indent=2)
            
            # 导出结果
            results_path = os.path.join(output_dir, "hara_results.json")
            with open(results_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            
            # 导出摘要报告
            summary_path = os.path.join(output_dir, "hara_summary.md")
            self._export_summary_report(summary_path)
            
            logger.info(f"结果已导出到: {output_dir}")
            return True
            
        except Exception as e:
            logger.error(f"导出结果失败: {e}")
            return False
    
    def _export_summary_report(self, output_path: str):
        """
        导出摘要报告
        
        Args:
            output_path: 输出文件路径
        """
        summary = [
            "# HARA分析摘要报告",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 1. 执行概览",
            f"- 总步骤数: {len(self.results)}",
            f"- 成功步骤数: {sum(1 for r in self.results.values() if r.get('success', False))}",
            f"- 失败步骤数: {sum(1 for r in self.results.values() if not r.get('success', True))}",
            "",
            "## 2. 关键成果"
        ]
        
        # 添加关键数据
        if "functions" in self.context:
            functions = self.context["functions"]
            summary.append(f"- 分析的功能数量: {len(functions)}")
            summary.append("  主要功能:")
            for func in functions[:5]:  # 显示前5个
                summary.append(f"    - {func}")
            if len(functions) > 5:
                summary.append(f"    - ... 等{len(functions)}个功能")
        
        if "function_malfunctions" in self.context:
            total_malfunctions = self.context.get("total_malfunctions", 0)
            summary.append(f"- 生成的Malfunction数量: {total_malfunctions}")
        
        if "function_hazards" in self.context:
            total_hazards = self.context.get("total_hazards", 0)
            summary.append(f"- 分析的危害数量: {total_hazards}")
        
        if "function_scenarios" in self.context:
            total_combinations = self.context.get("total_combinations", 0)
            summary.append(f"- Malfunction-场景组合数量: {total_combinations}")
        
        summary.append("")
        summary.append("## 3. 步骤执行详情")
        
        for step_num, result in sorted(self.results.items()):
            status = "✅ 成功" if result.get("success", False) else "❌ 失败"
            message = result.get("message", "") or result.get("error", "")
            summary.append(f"### 步骤{step_num}: {status}")
            summary.append(f"- 状态: {status}")
            if message:
                summary.append(f"- 结果: {message}")
            summary.append("")
        
        # 写入文件
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(summary))

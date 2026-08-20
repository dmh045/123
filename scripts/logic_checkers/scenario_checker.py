#!/usr/bin/env python3
"""
场景组合逻辑检查器
用于检查Malfunction与场景组合的合理性
支持场景库导入和场景偏差检测
"""

import re
import json
import sys
import os
from typing import Dict, List, Any, Tuple, Set, Optional
import logging
import argparse
from pathlib import Path

try:
    import openpyxl
except ImportError:
    openpyxl = None

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ScenarioChecker:
    """场景组合逻辑检查器"""
    
    DEFAULT_LIBRARY_PATH = r"C:\Users\F21N3EE\AIHARA\AI_HARA_Skill\99_HARA-CEA\Combined_Scenarios_E_Value_Final_20260421 2.xlsx"
    DEFAULT_TEMPLATE_PATH = str(Path(__file__).resolve().parents[2] / "references" / "HARA_Template_AI_20260327.xlsx")
    
    def __init__(self, scenario_library_path: str = None, 
                 template_path: str = None):
        """
        初始化检查器
        
        Args:
            scenario_library_path: 场景库Excel文件路径 (可选)
            template_path: HARA模板Excel文件路径 (可选，用于加载主场景列表)
        """
        # 场景类型定义
        self.scenario_types = {
            "operating_scenario": "主场景",
            "vehicle_state": "车辆状态",
            "vehicle_speed": "车速",
            "weather_conditions": "天气条件",
            "road_surface_conditions": "路面条件"
        }
        
        # 场景库数据
        self.scenario_library = []
        self.scenario_library_loaded = False
        
        # 模板中的主场景列表（14个标准主场景）
        self.standard_main_scenarios = []
        self.standard_main_scenarios_loaded = False
        
        # 加载场景库
        if scenario_library_path:
            self.load_scenario_library(scenario_library_path)
        elif os.path.exists(self.DEFAULT_LIBRARY_PATH):
            self.load_scenario_library(self.DEFAULT_LIBRARY_PATH)
        
        # 加载模板主场景
        if template_path:
            self.load_template_main_scenarios(template_path)
        elif os.path.exists(self.DEFAULT_TEMPLATE_PATH):
            self.load_template_main_scenarios(self.DEFAULT_TEMPLATE_PATH)

        self.invalid_scenario_combinations = [
            (r"停车.*高速", "停车状态不应与高速组合"),
            (r"熄火.*行驶", "熄火状态不应与行驶组合"),
            (r"维修.*高速", "维修状态不应与高速组合"),
            (r"静止.*高速", "静止状态不应与高速组合"),
            (r"晴天.*湿滑", "晴天通常不与湿滑路面组合"),
            (r"干燥.*冰雪", "干燥天气通常不与冰雪路面组合"),
            (r"暴雨.*干燥", "暴雨通常不与干燥路面组合"),
            (r"大雪.*干燥", "大雪通常不与干燥路面组合"),
            (r"高速.*停车场", "高速不应在停车场场景"),
            (r"低速.*高速公路", "低速不应在高速公路场景"),
            (r"静止.*国道", "静止状态不应在国道场景"),
        ]
        self.scenario_compatibility = {
            "Driving": ["Highway", "City road", "Country road", "Parking", "Garage"],
            "Parking": ["Parking lot", "Garage", "Roadside"],
            "Maintenance": ["Garage", "Service center"],
            "Charging": ["Charging station", "Garage"],
            "0-10 km/h": ["Parking", "Traffic jam", "City center"],
            "10-30 km/h": ["Residential area", "School zone", "City road"],
            "30-50 km/h": ["City road", "Suburban road"],
            "50-70 km/h": ["Country road", "Highway entrance"],
            "70-100 km/h": ["Highway", "Expressway"],
            "100+ km/h": ["Highway", "Autobahn"],
            "Sunny": ["All road types"],
            "Rainy": ["All road types"],
            "Snowy": ["Main roads"],
            "Foggy": ["Low speed roads"],
            "Stormy": ["Avoid driving"],
            "Dry": ["All conditions"],
            "Wet": ["Reduce speed"],
            "Icy": ["Extreme caution"],
            "Snow covered": ["Reduce speed"],
            "Muddy": ["Off-road only"],
        }
        self.malfunction_severity_scenarios = {
            "high_severity": [
                "刹车系统失效", "转向系统失效",
                "加速系统失控", "安全气囊误触发", "电池热失控"
            ],
            "medium_severity": [
                "灯光系统失效", "雨刷器失效",
                "空调系统失效", "娱乐系统故障", "车窗控制失效"
            ],
            "low_severity": [
                "座椅调节失效", "后视镜调节失效",
                "氛围灯故障", "音响系统故障", "充电口故障"
            ]
        }

        self.SCENARIO_REF_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ScenarioRef.json")
        self.scenario_ref = self._load_scenario_ref()

    def _load_scenario_ref(self) -> Dict[str, Any]:
        """加载ScenarioRef.json参考库"""
        if not os.path.exists(self.SCENARIO_REF_PATH):
            return {}
        try:
            with open(self.SCENARIO_REF_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f"ScenarioRef加载完成: {len(data.get('entries', []))} 条场景记录")
            return data
        except Exception as e:
            logger.warning(f"ScenarioRef加载失败: {e}")
            return {}

    def _find_scenarios_by_keywords(self, subsystem: str = "", function: str = "",
                                     hazard: str = "", malfunction: str = "") -> List[Dict[str, Any]]:
        """通过关键词在ScenarioRef中查找对应场景组合

        Args:
            subsystem: 子系统名称
            function: 功能名称
            hazard: 危害描述
            malfunction: 故障描述

        Returns:
            匹配的场景组合列表
        """
        if not self.scenario_ref:
            return []

        all_keywords = f"{subsystem} {function} {hazard} {malfunction}".lower()
        matched = []

        for entry in self.scenario_ref.get("entries", []):
            entry_keywords = (
                " ".join(entry.get("subsystem_keywords", [])) + " " +
                " ".join(entry.get("function_keywords", [])) + " " +
                " ".join(entry.get("hazard_keywords", [])) + " " +
                " ".join(entry.get("malfunction_keywords", []))
            ).lower()

            entry_set = set(entry_keywords.split())
            all_set = set(all_keywords.split())
            overlap = entry_set & all_set

            if len(overlap) >= 2:
                matched.append(entry)
            elif any(kw in entry_keywords for kw in all_set):
                matched.append(entry)

        return matched

    def check_scenario_ref_completeness(self, malfunction: str, hazard: str,
                                         subsystem: str = "", function: str = "",
                                         existing_scenarios: List[str] = None) -> Dict[str, Any]:
        """检查现有场景组合是否覆盖ScenarioRef中的推荐场景

        Args:
            malfunction: 故障描述
            hazard: 危害描述
            subsystem: 子系统名称
            function: 功能名称
            existing_scenarios: 现有场景列表

        Returns:
            {'complete': bool, 'missing': List[Dict], 'found': List[Dict], 'recommendation': str}
        """
        existing_scenarios = existing_scenarios or []
        matched_entries = self._find_scenarios_by_keywords(subsystem, function, hazard, malfunction)

        if not matched_entries:
            return {
                'complete': False,
                'missing': [],
                'found': [],
                'recommendation': 'ScenarioRef中无匹配条目，建议按标准主场景补充'
            }

        all_required = []
        for entry in matched_entries:
            for sc in entry.get("required_scenarios", []):
                all_required.append({
                    'scenario': sc.get('scenario', ''),
                    'e_value': sc.get('e_value', ''),
                    'entry_id': entry.get('id', ''),
                    'notes': entry.get('notes', '')
                })

        found = []
        missing = []
        existing_lower = [s.lower() for s in existing_scenarios]

        for req in all_required:
            sc_lower = req['scenario'].lower()
            if any(req_sc in sc_lower or sc_lower in req_sc for req_sc in existing_lower):
                found.append(req)
            else:
                missing.append(req)

        return {
            'complete': len(missing) == 0,
            'missing': missing,
            'found': found,
            'matched_entries': [e.get('id') for e in matched_entries],
            'recommendation': f"建议补充 {len(missing)} 个缺失场景" if missing else "场景覆盖完整"
        }

    def check_function_scenario_ref(self, func_scenarios: Dict[str, Any],
                                     subsystem: str = "", function: str = "") -> Dict[str, Any]:
        """对整个功能进行ScenarioRef完整性检查

        Returns:
            {missing_scenarios: [...], issues: [...]}
        """
        results = {
            'checked_functions': [],
            'missing_scenarios': [],
            'issues': []
        }

        for func, scenarios_data in func_scenarios.items():
            func_scenario_texts = []
            hazards = []

            if isinstance(scenarios_data, dict):
                for malf, malf_scenarios in scenarios_data.items():
                    for sc in malf_scenarios:
                        sc_text = sc.get('scenario_summary', '')
                        if sc_text:
                            func_scenario_texts.append(sc_text)
                        hazard_ev = sc.get('hazard_event', '')
                        if hazard_ev:
                            hazards.append(hazard_ev)

            for hazard in hazards:
                check_result = self.check_scenario_ref_completeness(
                    malfunction=func,
                    hazard=hazard,
                    subsystem=subsystem,
                    function=function,
                    existing_scenarios=func_scenario_texts
                )

                if not check_result['complete']:
                    results['missing_scenarios'].extend(check_result['missing'])
                    results['issues'].append({
                        'function': func,
                        'hazard': hazard[:50],
                        'recommendation': check_result['recommendation']
                    })

            results['checked_functions'].append(func)

        return results
    
    def load_scenario_library(self, library_path: str) -> bool:
        """
        加载场景库
        
        Args:
            library_path: 场景库Excel文件路径
            
        Returns:
            是否加载成功
        """
        if not openpyxl:
            logger.warning("openpyxl未安装，无法加载场景库")
            return False
        
        if not os.path.exists(library_path):
            logger.warning(f"场景库文件不存在: {library_path}")
            return False
        
        try:
            wb = openpyxl.load_workbook(library_path, data_only=True)
            
            # 查找Combined Scenarios sheet
            sheet_name = None
            for name in wb.sheetnames:
                if 'Combined' in name or 'Scenario' in name:
                    sheet_name = name
                    break
            
            if not sheet_name:
                logger.warning("场景库中未找到场景sheet")
                return False
            
            ws = wb[sheet_name]
            
            # 提取场景数据 (跳过表头)
            self.scenario_library = []
            for row in range(3, ws.max_row + 1):
                scene_id = ws.cell(row, 1).value
                desc = ws.cell(row, 2).value
                e_value = ws.cell(row, 3).value
                scene_type = ws.cell(row, 10).value if ws.max_column >= 10 else None
                
                if scene_id and desc:
                    self.scenario_library.append({
                        'id': scene_id,
                        'description': desc,
                        'e_value': e_value,
                        'type': scene_type
                    })
            
            self.scenario_library_loaded = True
            logger.info(f"场景库加载完成: {len(self.scenario_library)} 条记录")
            wb.close()
            return True
            
        except Exception as e:
            logger.error(f"加载场景库失败: {e}")
            return False
    
    def load_template_main_scenarios(self, template_path: str) -> bool:
        """
        从HARA模板加载主场景列表（14个标准Operating scenarios）
        
        Args:
            template_path: HARA模板Excel文件路径
            
        Returns:
            是否加载成功
        """
        if not openpyxl:
            logger.warning("openpyxl未安装，无法加载模板主场景")
            return False
        
        if not os.path.exists(template_path):
            logger.warning(f"HARA模板文件不存在: {template_path}")
            return False
        
        try:
            wb = openpyxl.load_workbook(template_path, data_only=True)
            
            # 查找Scenarios_Library sheet
            sheet_name = None
            for name in wb.sheetnames:
                if 'Scenarios' in name and 'Library' in name:
                    sheet_name = name
                    break
            
            if not sheet_name:
                logger.warning("模板中未找到Scenarios_Library sheet")
                wb.close()
                return False
            
            ws = wb[sheet_name]
            
            # 提取主场景（Operating scenarios列，B列=第2列）
            # 从第3行开始（跳过表头第2行），到第16行（14个主场景），跳过第17行后的汇总行
            self.standard_main_scenarios = []
            for row in range(3, 17):  # 第3-16行，共14个主场景
                scenario = ws.cell(row, 2).value  # B列：Operating scenarios
                if scenario:
                    # 清理场景名称（去除多余空格和换行）
                    scenario_clean = str(scenario).replace('\n', ' ').strip()
                    # 排除汇总行
                    if 'all' not in scenario_clean.lower():
                        self.standard_main_scenarios.append(scenario_clean)
            
            self.standard_main_scenarios_loaded = True
            logger.info(f"模板主场景加载完成: {len(self.standard_main_scenarios)} 个主场景")
            wb.close()
            return True
            
        except Exception as e:
            logger.error(f"加载模板主场景失败: {e}")
            return False
    
    def get_standard_main_scenarios(self) -> List[str]:
        """
        获取14个标准主场景列表

        Returns:
            14个标准主场景列表
        """
        return self.standard_main_scenarios.copy()

    def get_related_scenarios(self, func: str, scenarios_library: List[Dict],
                               all_type_scenarios: List[Dict]) -> List[Dict]:
        """
        获取与给定功能相关的场景列表

        Args:
            func: 功能名称
            scenarios_library: 完整场景库
            all_type_scenarios: 通用场景（适用所有功能）

        Returns:
            相关场景列表
        """
        if all_type_scenarios:
            return all_type_scenarios
        return scenarios_library

    def check_scenario_completeness(self, used_scenarios: List[str]) -> Dict[str, Any]:
        """
        检查HARA报告中是否使用了所有主场景
        
        Args:
            used_scenarios: 实际使用的主场景描述列表
            
        Returns:
            完整性检查结果，包含缺失的场景和建议
        """
        if not self.standard_main_scenarios_loaded:
            return {
                "complete": False,
                "error": "标准主场景列表未加载",
                "message": "请先调用load_template_main_scenarios或设置template_path"
            }
        
        # 标准化已使用的主场景
        used_normalized = set()
        for sc in used_scenarios:
            if sc:
                # 提取关键标识（英文名称或中文名称）
                sc_clean = str(sc).replace('\n', ' ').lower().strip()
                # 提取第一个关键词（通常是英文名称）
                keywords = re.findall(r'[a-z]+(?:\s+[a-z]+)*', sc_clean)
                if keywords:
                    # 使用第一个完整词作为标识
                    used_normalized.add(keywords[0] if len(keywords[0]) > 3 else ' '.join(keywords[:2]))
        
        # 检查每个标准场景是否被使用
        used_list = []
        missing_list = []
        
        for std_scenario in self.standard_main_scenarios:
            std_clean = std_scenario.replace('\n', ' ').lower().strip()
            # 提取英文关键词（用于匹配）
            keywords = re.findall(r'[a-z]+(?:\s+[a-z]+)*', std_clean)
            std_keyword = keywords[0] if keywords else std_clean.split('/')[0].strip()
            
            # 检查是否在已使用场景中
            is_used = False
            for used_sc in used_scenarios:
                if used_sc:
                    used_clean = str(used_sc).replace('\n', ' ').lower()
                    # 检查关键词匹配
                    for kw in keywords:
                        if kw in used_clean:
                            is_used = True
                            break
                    if is_used:
                        break
            
            if is_used:
                used_list.append(std_scenario)
            else:
                missing_list.append(std_scenario)
        
        result = {
            "complete": len(missing_list) == 0,
            "total_standard": len(self.standard_main_scenarios),
            "used_count": len(used_list),
            "missing_count": len(missing_list),
            "used_scenarios": used_list,
            "missing_scenarios": missing_list,
            "message": "",
            "suggestion": ""
        }
        
        if result["complete"]:
            result["message"] = f"✓ 所有{len(self.standard_main_scenarios)}个标准主场景均已使用 ({len(used_list)}/{len(self.standard_main_scenarios)})"
        else:
            result["message"] = f"✗ 缺少 {len(missing_list)} 个标准主场景 ({len(used_list)}/{len(self.standard_main_scenarios)})"
            result["suggestion"] = f"建议添加以下主场景的HARA分析:\n" + "\n".join([f"  - {sc}" for sc in missing_list])
        
        return result
    
    def check_hara_report_scenario_completeness(self, hara_records: List[Dict[str, Any]], 
                                                scenario_column: str = "operating_scenario") -> Dict[str, Any]:
        """
        检查HARA报告的场景完整性
        
        Args:
            hara_records: HARA记录列表（每个记录是一个字典）
            scenario_column: 场景列名（默认为"operating_scenario"或"scenario"）
            
        Returns:
            完整性检查结果
        """
        # 从HARA记录中提取使用的场景
        used_scenarios = []
        for record in hara_records:
            if scenario_column in record:
                sc = record[scenario_column]
                if isinstance(sc, list):
                    used_scenarios.extend(sc)
                else:
                    used_scenarios.append(sc)
            # 也检查其他可能的列名
            elif "scenario" in record:
                sc = record["scenario"]
                if isinstance(sc, list):
                    used_scenarios.extend(sc)
                else:
                    used_scenarios.append(sc)
        
        return self.check_scenario_completeness(used_scenarios)
    
    def check_excel_hara_scenario_completeness(self, excel_path: str, 
                                               sheet_name: str = "05_HARA",
                                               scenario_column: int = 7) -> Dict[str, Any]:
        """
        检查Excel中HARA报告的场景完整性
        
        Args:
            excel_path: Excel文件路径
            sheet_name: HARA sheet名称
            scenario_column: 场景描述列号（G列=7, Situational description)
            
        Returns:
            完整性检查结果
        """
        try:
            import openpyxl
            
            if not os.path.exists(excel_path):
                return {
                    "complete": False,
                    "error": f"文件不存在: {excel_path}",
                    "message": f"文件不存在: {excel_path}"
                }
            
            # 确保标准主场景已加载
            if not self.standard_main_scenarios_loaded:
                if os.path.exists(self.DEFAULT_TEMPLATE_PATH):
                    self.load_template_main_scenarios(self.DEFAULT_TEMPLATE_PATH)
                else:
                    return {
                        "complete": False,
                        "error": "标准主场景列表未加载且无法找到默认模板",
                        "message": "标准主场景列表未加载"
                    }
            
            wb = openpyxl.load_workbook(excel_path, data_only=True)
            
            # 检查是否有所需的sheet
            if sheet_name not in wb.sheetnames:
                # 尝试其他可能的sheet名称
                possible_names = ['05_HARA', 'HARA', 'Hazard Analysis']
                for name in possible_names:
                    if name in wb.sheetnames:
                        sheet_name = name
                        break
            
            if sheet_name not in wb.sheetnames:
                wb.close()
                return {
                    "complete": False,
                    "error": f"未找到HARA sheet: {sheet_name}",
                    "message": f"未找到HARA sheet: {sheet_name}"
                }
            
            ws = wb[sheet_name]
            
            used_scenarios = []
            
            # 从第6行开始扫描（第5行是表头）
            for row in range(6, ws.max_row + 1):
                # G列：Situational description (主场景描述)
                sc = ws.cell(row, scenario_column).value
                if sc:
                    used_scenarios.append(str(sc).strip())
            
            wb.close()
            
            return self.check_scenario_completeness(used_scenarios)
            
        except Exception as e:
            return {
                "complete": False,
                "error": str(e),
                "message": f"检查失败: {e}"
            }
    
    def check_malfunction_scenario_combo(self, malfunction: str, scenario: Dict[str, str]) -> Dict[str, Any]:
        """
        检查Malfunction与场景组合的合理性
        
        Args:
            malfunction: Malfunction描述
            scenario: 场景字典，包含各个维度的场景信息
            
        Returns:
            检查结果，包含是否合理、原因和建议
        """
        result = {
            "is_valid": True,
            "malfunction": malfunction,
            "scenario": scenario,
            "reasons": [],
            "suggestions": [],
            "risk_level": "低",
            "scenario_summary": self._summarize_scenario(scenario)
        }
        
        # 检查1：基本合理性检查
        if not malfunction or not scenario:
            result["is_valid"] = False
            result["reasons"].append("Malfunction或场景为空")
            return result
        
        # 检查2：检查不合理的组合模式
        scenario_text = self._create_scenario_text(scenario)
        combo_text = f"{malfunction} {scenario_text}"
        
        for pattern, reason in self.invalid_scenario_combinations:
            if re.search(pattern, combo_text, re.IGNORECASE):
                result["is_valid"] = False
                result["reasons"].append(f"匹配到不合理模式: {reason}")
        
        # 检查3：检查Malfunction严重度与场景匹配性
        severity_match = self._check_severity_scenario_match(malfunction, scenario)
        if not severity_match["compatible"]:
            result["is_valid"] = False
            result["reasons"].append(severity_match["reason"])
        else:
            result["risk_level"] = severity_match["risk_level"]
        
        # 检查4：检查场景内部一致性
        scenario_consistency = self._check_scenario_consistency(scenario)
        if not scenario_consistency["consistent"]:
            result["is_valid"] = False
            result["reasons"].append(scenario_consistency["reason"])
        
        # 检查5：检查场景与Malfunction的因果关系
        causality_check = self._check_causality(malfunction, scenario)
        if not causality_check["plausible"]:
            result["is_valid"] = False
            result["reasons"].append(causality_check["reason"])
        
        # 如果检查通过，添加积极反馈
        if result["is_valid"]:
            result["reasons"].append(f"Malfunction'{malfunction}'与场景组合合理")
            result["reasons"].append(f"风险等级: {result['risk_level']}")
        
        return result
    
    # 引导词到故障状态、行为、伤害的映射（v9.0新增）
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
        "as well as": "附加动作触发导致多系统冲突",
        "非预期": "非预期触发导致系统执行非计划操作",
        "非预期触发": "非预期触发导致系统执行非计划操作",
        "持续激活": "持续激活导致系统无法进入休眠",
        "持续工作": "持续激活导致系统无法进入休眠",
        "持续": "持续激活导致系统无法进入休眠",
        "丢失": "功能完全丧失导致关键系统失效",
        "功能丧失": "功能完全丧失导致关键系统失效",
        "失效": "功能完全丧失导致关键系统失效",
        "过大": "参数过大导致系统过载或数据溢出",
        "过小": "参数过小导致系统响应不足",
        "过早": "触发时机过早导致前置条件未满足",
        "过晚": "触发时机过晚导致错过最佳响应窗口",
        "过快": "执行速度过快导致验证不充分",
        "过慢": "执行速度过慢导致超时或资源占用",
        "过长": "持续时间过长导致系统资源耗尽",
        "过短": "持续时间过短导致操作不完整",
        "不完整": "操作不完整导致系统处于中间态",
        "不同": "行为异常导致预期外系统响应",
        "差异": "行为异常导致预期外系统响应",
        "同时发生": "附加动作触发导致多系统冲突",
        "附加": "附加动作触发导致多系统冲突"
    }

    GW_CHINESE_TO_ENGLISH = {
        "非预期": "unintended", "非预期触发": "unintended",
        "持续激活": "always active", "持续工作": "always active", "持续": "always active",
        "丢失": "loss", "功能丧失": "loss", "失效": "loss",
        "过大": "too large", "过小": "too small",
        "过早": "too early", "过晚": "too late",
        "过快": "too fast", "过慢": "too slow",
        "过长": "too long", "过短": "too short",
        "不完整": "incomplete", "不同": "different to", "差异": "different to",
        "同时发生": "as well as", "附加": "as well as"
    }

    def _detect_guideword(self, text: str) -> str:
        """检测引导词，支持中英文。

        优先检测英文（更精确），若英文未匹配则检测中文别名。
        检测到中文后映射为英文 guideword。
        """
        text_lower = text.lower()
        for gw in ["unintended", "always active", "loss", "too large", "too small",
                   "too early", "too late", "too fast", "too slow",
                   "too long", "too short", "incomplete", "different to", "as well as"]:
            if gw in text_lower:
                return gw
        for cn, en in self.GW_CHINESE_TO_ENGLISH.items():
            if cn in text:
                return en
        return ""
    
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

    SPECIFIC_BEHAVIOR_RESULTS = {
        "loss": {
            "刹车": "车辆发生追尾碰撞",
            "制动": "车辆发生追尾碰撞",
            "转向": "车辆发生侧向碰撞",
            "方向盘": "车辆发生侧向碰撞",
            "加速": "车辆发生前方碰撞",
            "油门": "车辆发生追尾碰撞或被后车追尾",
            "灯光": "车辆与行人发生碰撞",
            "wiper": "雨刷功能丧失导致驾驶员视野被雨水遮挡，在雨天低速行驶或路口时与行人或障碍物发生正面碰撞",
            "雨刷": "雨刷功能丧失导致驾驶员视野被雨水遮挡，在雨天低速行驶或路口时与行人或障碍物发生正面碰撞",
            "rear wiper": "后雨刷功能丧失导致驾驶员失去后方视野，在倒车或变道时与后车或障碍物发生碰撞",
            "后雨刷": "后雨刷功能丧失导致驾驶员失去后方视野，在倒车或变道时与后车或障碍物发生碰撞",
            "安全气囊": "车辆发生碰撞后乘员遭受额外伤害",
            "default": "车辆发生碰撞事故"
        },
        "too fast": {
            "刹车": "车辆发生前方碰撞或侧翻",
            "加速": "车辆失控发生前方或侧向碰撞",
            "转向": "车辆高速过弯发生侧翻或侧向碰撞",
            "wiper": "雨刷速度过快干扰驾驶员视线，在弯道或复杂路况时分心导致车辆与行人或障碍物发生碰撞",
            "雨刷": "雨刷速度过快干扰驾驶员视线，在弯道或复杂路况时分心导致车辆与行人或障碍物发生碰撞",
            "rear wiper": "后雨刷速度过快干扰驾驶员注意力，在关键时刻操作失误导致车辆偏离车道",
            "后雨刷": "后雨刷速度过快干扰驾驶员注意力，在关键时刻操作失误导致车辆偏离车道",
            "default": "车辆发生高速碰撞"
        },
        "too slow": {
            "刹车": "车辆发生追尾碰撞",
            "加速": "车辆被后车追尾",
            "wiper": "雨刷速度过慢无法及时清除雨水，驾驶员前方视野持续受阻，在雨天低速行驶时与行人发生碰撞",
            "雨刷": "雨刷速度过慢无法及时清除雨水，驾驶员前方视野持续受阻，在雨天低速行驶时与行人发生碰撞",
            "rear wiper": "后雨刷速度过慢无法清除后窗雨水，驾驶员后方视野受阻，在变道时与后车发生碰撞",
            "后雨刷": "后雨刷速度过慢无法清除后窗雨水，驾驶员后方视野受阻，在变道时与后车发生碰撞",
            "default": "车辆发生追尾碰撞"
        },
        "too large": {
            "刹车": "车辆紧急制动导致车轮抱死，发生侧翻或侧向碰撞",
            "加速": "车辆突然加速失控，发生前方碰撞",
            "wiper": "雨刷工作参数超出正常范围，导致刮片异常跳动干扰驾驶员视野，在雨天与行人或障碍物发生碰撞",
            "雨刷": "雨刷工作参数超出正常范围，导致刮片异常跳动干扰驾驶员视野，在雨天与行人或障碍物发生碰撞",
            "rear wiper": "后雨刷工作参数异常，导致后窗视野持续模糊，在变道或倒车时与后车发生碰撞",
            "后雨刷": "后雨刷工作参数异常，导致后窗视野持续模糊，在变道或倒车时与后车发生碰撞",
            "default": "车辆发生碰撞事故"
        },
        "too small": {
            "刹车": "车辆制动不足，发生前方碰撞",
            "加速": "车辆加速过慢，被后车追尾",
            "wiper": "雨刷工作范围过小无法覆盖全部挡风玻璃，驾驶员视野部分受阻，在雨天与行人发生碰撞",
            "雨刷": "雨刷工作范围过小无法覆盖全部挡风玻璃，驾驶员视野部分受阻，在雨天与行人发生碰撞",
            "rear wiper": "后雨刷工作范围不足，后方视野部分受阻，在变道时与后车发生碰撞",
            "后雨刷": "后雨刷工作范围不足，后方视野部分受阻，在变道时与后车发生碰撞",
            "default": "车辆发生碰撞事故"
        },
        "too early": {
            "刹车": "车辆过早制动导致后车追尾",
            "加速": "车辆在危险条件下过早加速，导致前方碰撞",
            "转向": "车辆在盲区过早转向，与侧方来车发生碰撞",
            "wiper": "雨刷在不需要时过早激活，干扰驾驶员注意力，在关键时刻操作失误导致碰撞",
            "雨刷": "雨刷在不需要时过早激活，干扰驾驶员注意力，在关键时刻操作失误导致碰撞",
            "rear wiper": "后雨刷过早激活干扰驾驶员，在关键时刻分心导致车辆偏离车道或与侧方来车碰撞",
            "后雨刷": "后雨刷过早激活干扰驾驶员，在关键时刻分心导致车辆偏离车道或与侧方来车碰撞",
            "default": "车辆与来车发生碰撞"
        },
        "too late": {
            "刹车": "车辆错过最佳制动时机，发生前方或追尾碰撞",
            "加速": "车辆错过安全间隙，与行人或障碍物发生碰撞",
            "转向": "车辆错过路口，与侧向来车发生碰撞",
            "wiper": "雨刷激活过晚，在需要时未能及时清除雨水，驾驶员在雨天低能见度条件下与行人发生碰撞",
            "雨刷": "雨刷激活过晚，在需要时未能及时清除雨水，驾驶员在雨天低能见度条件下与行人发生碰撞",
            "rear wiper": "后雨刷激活过晚，后窗雨水持续遮挡，在变道时与后车发生碰撞",
            "后雨刷": "后雨刷激活过晚，后窗雨水持续遮挡，在变道时与后车发生碰撞",
            "default": "车辆发生碰撞事故"
        },
        "unintended": {
            "刹车": "车辆非预期制动，导致后车追尾",
            "加速": "车辆非预期加速，发生前方碰撞",
            "转向": "车辆非预期转向，发生侧向碰撞",
            "wiper": "雨刷意外激活分散驾驶员注意力，在关键时刻操作失误导致车辆与行人或侧向来车发生碰撞",
            "雨刷": "雨刷意外激活分散驾驶员注意力，在关键时刻操作失误导致车辆与行人或侧向来车发生碰撞",
            "rear wiper": "后雨刷意外激活分散驾驶员注意力，在关键时刻操作失误导致车辆偏离车道或与侧方来车发生碰撞",
            "后雨刷": "后雨刷意外激活分散驾驶员注意力，在关键时刻操作失误导致车辆偏离车道或与侧方来车发生碰撞",
            "安全气囊": "车辆非预期弹开，导致乘员受伤",
            "default": "车辆发生非预期碰撞事故"
        },
        "always active": {
            "刹车": "车辆持续制动导致过热，引发后方碰撞",
            "加速": "车辆持续加速失控，发生前方碰撞",
            "转向": "车辆持续转向，导致侧翻或侧向碰撞",
            "wiper": "雨刷持续工作分散驾驶员注意力，在关键时刻操作失误导致车辆与行人或侧向来车发生碰撞",
            "雨刷": "雨刷持续工作分散驾驶员注意力，在关键时刻操作失误导致车辆与行人或侧向来车发生碰撞",
            "rear wiper": "后雨刷持续工作分散驾驶员注意力，在关键时刻操作失误导致车辆偏离车道或与侧方来车发生碰撞",
            "后雨刷": "后雨刷持续工作分散驾驶员注意力，在关键时刻操作失误导致车辆偏离车道或与侧方来车发生碰撞",
            "default": "车辆持续异常运动，发生碰撞事故"
        },
        "too long": {
            "刹车": "车辆制动过久导致过热，引发后方碰撞",
            "加速": "车辆加速过程过长，超出安全范围导致碰撞",
            "wiper": "雨刷持续工作时间过长，干扰驾驶员在长途行驶中的注意力，在高速路上与前车发生追尾碰撞",
            "雨刷": "雨刷持续工作时间过长，干扰驾驶员在长途行驶中的注意力，在高速路上与前车发生追尾碰撞",
            "rear wiper": "后雨刷持续工作干扰驾驶员注意力，在长途行驶中分心导致车辆偏离车道",
            "后雨刷": "后雨刷持续工作干扰驾驶员注意力，在长途行驶中分心导致车辆偏离车道",
            "default": "车辆异常状态持续，导致碰撞事故"
        },
        "too short": {
            "刹车": "车辆制动过短导致停车距离不足，发生前方碰撞",
            "加速": "车辆加速过短导致汇入失败，发生侧向碰撞",
            "wiper": "雨刷工作周期过短，在雨天刚激活就停止，驾驶员视野在关键时期被雨水遮挡导致碰撞",
            "雨刷": "雨刷工作周期过短，在雨天刚激活就停止，驾驶员视野在关键时期被雨水遮挡导致碰撞",
            "rear wiper": "后雨刮工作周期过短，后窗雨水未清除，在变道时与后车发生碰撞",
            "后雨刷": "后雨刮工作周期过短，后窗雨水未清除，在变道时与后车发生碰撞",
            "default": "车辆操作中断导致碰撞事故"
        },
        "incomplete": {
            "刹车": "车辆制动不完整，发生前方碰撞",
            "转向": "车辆转向不完整，发生侧向碰撞",
            "wiper": "雨刷功能不完整导致驾驶员视野部分受阻，在雨天或低能见度条件下无法有效识别障碍物导致碰撞",
            "雨刷": "雨刷功能不完整导致驾驶员视野部分受阻，在雨天或低能见度条件下无法有效识别障碍物导致碰撞",
            "rear wiper": "后雨刷功能不完整导致驾驶员后方视野部分受阻，在变道或倒车时与后车发生碰撞",
            "后雨刷": "后雨刷功能不完整导致驾驶员后方视野部分受阻，在变道或倒车时与后车发生碰撞",
            "default": "车辆功能不完整导致碰撞事故"
        },
        "different to": {
            "刹车": "车辆制动效果与预期不符，导致碰撞",
            "转向": "车辆转向方向与预期不符，导致侧向碰撞",
            "wiper": "雨刷行为与驾驶员预期不符，驾驶员在预期清晰视野时发现雨水未清除，分心操作导致碰撞",
            "雨刷": "雨刷行为与驾驶员预期不符，驾驶员在预期清晰视野时发现雨水未清除，分心操作导致碰撞",
            "rear wiper": "后雨刷行为与预期不符，驾驶员在变道时误判后方距离，与后车发生碰撞",
            "后雨刷": "后雨刷行为与预期不符，驾驶员在变道时误判后方距离，与后车发生碰撞",
            "default": "车辆行为偏离驾驶员预期，导致碰撞"
        },
        "as well as": {
            "刹车": "车辆同时执行多重指令导致失控，发生碰撞",
            "wiper": "雨刷与其他功能同时异常，驾驶员需要分心处理多重异常，在雨天与行人或障碍物发生碰撞",
            "雨刷": "雨刷与其他功能同时异常，驾驶员需要分心处理多重异常，在雨天与行人或障碍物发生碰撞",
            "rear wiper": "后雨刷与其他功能同时异常，驾驶员分心处理多重异常，在变道时与后车发生碰撞",
            "后雨刷": "后雨刷与其他功能同时异常，驾驶员分心处理多重异常，在变道时与后车发生碰撞",
            "default": "车辆多重功能冲突导致碰撞事故"
        }
    }

    def generate_hazard_event(self, malfunction: str, scenario: Dict[str, str]) -> str:
        """
        生成危害事件描述（v9.0更新语义结构）

        新语义模板: <具体车辆故障状态>导致<具体车辆行为><具体人员伤害>
        优先级: SPECIFIC_BEHAVIOR_RESULTS（关键词匹配） -> BEHAVIOR_MAPPING -> fallback

        Args:
            malfunction: Malfunction描述
            scenario: 场景字典

        Returns:
            危害事件描述
        """
        guideword = self._detect_guideword(malfunction)

        fault_state = self.FAULT_STATE_MAPPING.get(guideword,
            f"{malfunction}导致系统异常")

        vehicle_behavior = None
        specific_behaviors = self.SPECIFIC_BEHAVIOR_RESULTS.get(guideword, {})
        if specific_behaviors:
            for kw, behavior in specific_behaviors.items():
                if kw != "default" and kw in malfunction:
                    vehicle_behavior = behavior
                    break
        if not vehicle_behavior:
            vehicle_behavior = self.BEHAVIOR_MAPPING.get(guideword,
                "车辆出现不可预测的行为")

        injury = self.INJURY_MAPPING.get(guideword,
            "造成人员伤害")

        hazard_event = f"<{fault_state}>导致<{vehicle_behavior}>，<{injury}>"

        return hazard_event
    
    def check_multiple_scenario_combinations(self, malfunctions: List[str], 
                                           scenarios: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        检查多个Malfunction与场景的组合
        
        Args:
            malfunctions: Malfunction列表
            scenarios: 场景列表
            
        Returns:
            检查结果列表
        """
        results = []
        
        for malfunction in malfunctions:
            for scenario in scenarios:
                # 检查组合合理性
                check_result = self.check_malfunction_scenario_combo(malfunction, scenario)
                
                # 生成危害事件
                if check_result["is_valid"]:
                    hazard_event = self.generate_hazard_event(malfunction, scenario)
                else:
                    hazard_event = f"不合理组合: {malfunction} + {self._summarize_scenario(scenario)}"
                
                result = {
                    "malfunction": malfunction,
                    "scenario": scenario,
                    "scenario_summary": self._summarize_scenario(scenario),
                    "check_result": check_result,
                    "hazard_event": hazard_event,
                    "should_include": check_result["is_valid"]
                }
                
                results.append(result)
        
        return results
    
    def _summarize_scenario(self, scenario: Dict[str, str]) -> str:
        """
        汇总场景描述
        
        Args:
            scenario: 场景字典
            
        Returns:
            场景摘要
        """
        parts = []
        
        if scenario.get("operating_scenario"):
            parts.append(scenario["operating_scenario"])
        if scenario.get("vehicle_state"):
            parts.append(scenario["vehicle_state"])
        if scenario.get("vehicle_speed"):
            parts.append(f"车速{scenario['vehicle_speed']}")
        
        # 天气和路面条件作为补充
        conditions = []
        if scenario.get("weather_conditions"):
            conditions.append(scenario["weather_conditions"])
        if scenario.get("road_surface_conditions"):
            conditions.append(scenario["road_surface_conditions"])
        
        if conditions:
            parts.append(f"({'、'.join(conditions)})")
        
        return "，".join(parts)
    
    def _create_scenario_text(self, scenario: Dict[str, str]) -> str:
        """
        创建场景文本用于模式匹配
        
        Args:
            scenario: 场景字典
            
        Returns:
            场景文本
        """
        return " ".join([str(v) for v in scenario.values() if v])
    
    def _check_severity_scenario_match(self, malfunction: str, scenario: Dict[str, str]) -> Dict[str, Any]:
        """
        检查Malfunction严重度与场景匹配性
        
        Args:
            malfunction: Malfunction描述
            scenario: 场景字典
            
        Returns:
            匹配性检查结果
        """
        result = {
            "compatible": True,
            "risk_level": "低",
            "reason": ""
        }
        
        # 判断Malfunction严重度
        malfunction_severity = "low"
        for severity_level, malfunctions in self.malfunction_severity_scenarios.items():
            for m in malfunctions:
                if m in malfunction:
                    malfunction_severity = severity_level.split('_')[0]  # high, medium, low
                    break
        
        # 分析场景风险
        scenario_risk = self._analyze_scenario_risk(scenario)
        
        # 评估组合风险
        if malfunction_severity == "high" and scenario_risk == "high":
            result["risk_level"] = "极高"
        elif malfunction_severity == "high" or scenario_risk == "high":
            result["risk_level"] = "高"
        elif malfunction_severity == "medium" or scenario_risk == "medium":
            result["risk_level"] = "中"
        else:
            result["risk_level"] = "低"
        
        # 风险较低只影响后续S/E/C结果，不能用于否决一个物理可行的HARA组合。
        # 例如AVP制动失效发生在停车场仍可能伤及近距离行人；即使最终为QM，
        # 也应保留到评分阶段，而不是在场景筛选阶段删除。
        if malfunction_severity == "high" and scenario_risk == "low":
            scenario_text = self._summarize_scenario(scenario)
            result["reason"] = (
                f"高后果潜力Malfunction位于低速/低暴露场景'{scenario_text}'；"
                "组合保持有效，由后续S/E/C判定实际风险"
            )
        
        return result
    
    def _analyze_scenario_risk(self, scenario: Dict[str, str]) -> str:
        """
        分析场景风险等级
        
        Args:
            scenario: 场景字典
            
        Returns:
            风险等级: high, medium, low
        """
        risk_score = 0
        
        # 车速风险
        vehicle_speed = scenario.get("vehicle_speed", "")
        if "高速" in vehicle_speed or "100+" in vehicle_speed:
            risk_score += 3
        elif "70-100" in vehicle_speed or "50-70" in vehicle_speed:
            risk_score += 2
        elif "30-50" in vehicle_speed:
            risk_score += 1
        
        # 道路类型风险
        operating_scenario = scenario.get("operating_scenario", "")
        if any(road in operating_scenario for road in ["高速公路", "高速", "国道", "主干道"]):
            risk_score += 2
        elif any(road in operating_scenario for road in ["城市道路", "市区", "居民区"]):
            risk_score += 1
        
        # 天气条件风险
        weather = scenario.get("weather_conditions", "")
        if any(cond in weather for cond in ["暴雨", "大雪", "大雾", "冰雹"]):
            risk_score += 2
        elif any(cond in weather for cond in ["雨天", "雪天", "雾天"]):
            risk_score += 1
        
        # 路面条件风险
        road_surface = scenario.get("road_surface_conditions", "")
        if any(cond in road_surface for cond in ["结冰", "积雪", "湿滑", "泥泞"]):
            risk_score += 2
        
        # 判断风险等级
        if risk_score >= 4:
            return "high"
        elif risk_score >= 2:
            return "medium"
        else:
            return "low"
    
    def _check_scenario_consistency(self, scenario: Dict[str, str]) -> Dict[str, Any]:
        """
        检查场景内部一致性
        
        Args:
            scenario: 场景字典
            
        Returns:
            一致性检查结果
        """
        result = {
            "consistent": True,
            "reason": ""
        }
        
        # 检查车辆状态与车速一致性
        vehicle_state = scenario.get("vehicle_state", "")
        vehicle_speed = scenario.get("vehicle_speed", "")
        
        if "停车" in vehicle_state or "静止" in vehicle_state or "熄火" in vehicle_state:
            if "高速" in vehicle_speed or "行驶" in vehicle_speed:
                result["consistent"] = False
                result["reason"] = "车辆状态为停车/静止/熄火时，不应有高速或行驶的车速"
        
        # 检查天气与路面条件一致性
        weather = scenario.get("weather_conditions", "")
        road_surface = scenario.get("road_surface_conditions", "")
        
        if "晴天" in weather and "干燥" not in road_surface and road_surface:
            # 晴天通常对应干燥路面，但可能有例外
            pass  # 不设为不一致，只是记录
        
        if "暴雨" in weather and "干燥" in road_surface:
            result["consistent"] = False
            result["reason"] = "暴雨天气通常不与干燥路面组合"
        
        if "大雪" in weather and "干燥" in road_surface:
            result["consistent"] = False
            result["reason"] = "大雪天气通常不与干燥路面组合"
        
        return result
    
    def _check_causality(self, malfunction: str, scenario: Dict[str, str]) -> Dict[str, Any]:
        """
        检查Malfunction与场景的因果关系
        
        Args:
            malfunction: Malfunction描述
            scenario: 场景字典
            
        Returns:
            因果关系检查结果
        """
        result = {
            "plausible": True,
            "reason": ""
        }
        
        scenario_summary = self._summarize_scenario(scenario)
        malfunction_lower = malfunction.lower()
        
        # 检查因果关系合理性
        causality_rules = [
            # Malfunction需要特定场景才可能发生
            ("涉水", "雨天|积水", "涉水故障通常需要雨天或积水场景"),
            ("高温", "高温天气|暴晒", "高温故障通常需要高温天气或暴晒场景"),
            ("低温", "寒冷|冰雪", "低温故障通常需要寒冷或冰雪场景"),
            ("腐蚀", "潮湿|盐雾", "腐蚀故障通常需要潮湿或盐雾场景"),
            
            # Malfunction在某些场景中不可能发生
            ("行驶中", "停车|静止", "行驶中故障在停车/静止场景中不可能发生"),
            ("充电中", "行驶", "充电中故障在行驶场景中不可能发生"),
            ("维修中", "行驶", "维修中故障在行驶场景中不可能发生"),
        ]
        
        for malfunction_keyword, scenario_pattern, reason in causality_rules:
            if malfunction_keyword in malfunction_lower:
                if re.search(scenario_pattern, scenario_summary, re.IGNORECASE):
                    # 找到了匹配的场景
                    pass
                else:
                    # 没有找到匹配的场景，可能不合理
                    result["plausible"] = False
                    result["reason"] = f"Malfunction包含'{malfunction_keyword}'，但场景'{scenario_summary}'中缺少必要条件"
        
        return result
    
    def filter_valid_combinations(self, combinations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        过滤出有效的组合
        
        Args:
            combinations: 组合列表
            
        Returns:
            有效组合列表
        """
        return [combo for combo in combinations if combo["check_result"]["is_valid"]]
    
    def check(self, malfunction: str, scenario: Dict[str, str]) -> Dict[str, Any]:
        """
        综合场景检查（返回pass/fail格式）

        检查项目：
        1. 不合理场景组合模式（15种）
        2. 场景兼容性映射
        3. 故障严重度与场景匹配
        4. 场景风险评分
        5. 场景完整性检查（14个标准主场景）

        Returns:
            检查结果: {"status": "pass/fail", "errors": [...]}
        """
        errors = []

        if not malfunction or not scenario:
            errors.append("Malfunction或场景为空")
            return {"status": "fail", "errors": errors}

        scenario_text = self._create_scenario_text(scenario)
        combo_text = f"{malfunction} {scenario_text}"

        for pattern, reason in self.invalid_scenario_combinations:
            if re.search(pattern, combo_text, re.IGNORECASE):
                errors.append(f"不合理组合: {reason}")

        severity_match = self._check_severity_scenario_match(malfunction, scenario)
        if not severity_match["compatible"]:
            errors.append(severity_match["reason"])

        scenario_consistency = self._check_scenario_consistency(scenario)
        if not scenario_consistency["consistent"]:
            errors.append(scenario_consistency["reason"])

        causality_check = self._check_causality(malfunction, scenario)
        if not causality_check["plausible"]:
            errors.append(causality_check["reason"])

        scenario_risk = self._analyze_scenario_risk(scenario)
        risk_score = self._calculate_risk_score(scenario)
        errors.append(f"风险等级: {scenario_risk}, 风险评分: {risk_score}")

        if errors:
            return {"status": "fail", "errors": errors}
        return {"status": "pass", "errors": []}

    def _calculate_risk_score(self, scenario: Dict[str, str]) -> int:
        """
        计算场景风险评分

        评分规则：
        - 车速风险: 高速(100+/100+km/h)+3, 70-100/50-70+2, 30-50+1
        - 道路类型: 高速+2, 城市+1
        - 天气风险: 暴雨/大雪/大雾+2, 雨天/雪天/雾天+1
        - 路面风险: 结冰/积雪/湿滑+2
        """
        score = 0

        vehicle_speed = scenario.get("vehicle_speed", "")
        if "高速" in vehicle_speed or "100+" in vehicle_speed:
            score += 3
        elif "70-100" in vehicle_speed or "50-70" in vehicle_speed:
            score += 2
        elif "30-50" in vehicle_speed:
            score += 1

        operating_scenario = scenario.get("operating_scenario", "")
        if any(road in operating_scenario for road in ["高速公路", "高速", "国道", "主干道"]):
            score += 2
        elif any(road in operating_scenario for road in ["城市道路", "市区", "居民区"]):
            score += 1

        weather = scenario.get("weather_conditions", "")
        if any(cond in weather for cond in ["暴雨", "大雪", "大雾", "冰雹"]):
            score += 2
        elif any(cond in weather for cond in ["雨天", "雪天", "雾天"]):
            score += 1

        road_surface = scenario.get("road_surface_conditions", "")
        if any(cond in road_surface for cond in ["结冰", "积雪", "湿滑", "泥泞"]):
            score += 2

        return score

    def check_completeness(self, used_scenarios: List[str]) -> Dict[str, Any]:
        """
        检查场景完整性（14个标准主场景）

        Returns:
            完整性检查结果: {"complete": bool, "missing": [...]}
        """
        if not self.standard_main_scenarios_loaded:
            if os.path.exists(self.DEFAULT_TEMPLATE_PATH):
                self.load_template_main_scenarios(self.DEFAULT_TEMPLATE_PATH)
            else:
                return {"complete": False, "missing": [], "error": "标准主场景未加载"}

        used_lower = [s.lower() for s in used_scenarios if s]
        missing = []

        for std in self.standard_main_scenarios:
            std_lower = std.lower()
            found = False
            for used in used_lower:
                keywords = re.findall(r'[a-z]+(?:\s+[a-z]+)*', std_lower)
                for kw in keywords:
                    if kw in used or any(kw2 in std_lower for kw2 in used.split() if len(kw2) > 3):
                        found = True
                        break
                if found:
                    break
            if not found:
                missing.append(std)

        return {"complete": len(missing) == 0, "missing": missing}

    def export_check_results(self, results: List[Dict[str, Any]], output_path: str) -> bool:
        """
        导出检查结果

        Args:
            results: 检查结果列表
            output_path: 输出文件路径

        Returns:
            是否导出成功
        """
        try:
            total_combinations = len(results)
            valid_combinations = len([r for r in results if r["check_result"]["is_valid"]])
            invalid_combinations = total_combinations - valid_combinations

            risk_levels = {}
            for result in results:
                if result["check_result"]["is_valid"]:
                    risk_level = result["check_result"]["risk_level"]
                    risk_levels[risk_level] = risk_levels.get(risk_level, 0) + 1

            summary = {
                "total_combinations": total_combinations,
                "valid_combinations": valid_combinations,
                "invalid_combinations": invalid_combinations,
                "valid_percentage": round(valid_combinations / total_combinations * 100, 2) if total_combinations > 0 else 0,
                "risk_level_distribution": risk_levels,
                "results": results
            }

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)

            logger.info(f"场景检查结果已导出到: {output_path}")
            logger.info(f"总组合数: {total_combinations}, 有效组合: {valid_combinations}, 无效组合: {invalid_combinations}")
            logger.info(f"风险等级分布: {risk_levels}")

            return True

        except Exception as e:
            logger.error(f"导出检查结果失败: {e}")
            return False


def test_scenario_checker(input_path: str, verbose: bool = False,
                           template_path: str = None,
                           allow_draft: bool = False) -> Dict[str, Any]:
    """
    测试场景检查器 - 从 JSON 文件加载数据并检查

    Args:
        input_path: scen_hazevent_output.json 文件路径
        verbose: 是否显示详细输出
        template_path: HARA模板Excel路径

    Returns:
        检查结果字典 {"passed": bool, "errors": [...], "summary": {...}}
    """
    checker = ScenarioChecker(template_path=template_path)
    errors = []
    warnings = []
    
    if not os.path.exists(input_path):
        return {
            "passed": False,
            "errors": [{"type": "file_not_found", "message": f"文件不存在：{input_path}"}]
        }
    
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {
            "passed": False,
            "errors": [{"type": "json_parse_error", "message": f"JSON 解析失败：{e}"}]
        }
    
    function_hazards = data.get("function_hazards", {})
    function_scenarios = data.get("function_scenarios", {})
    scenario_catalog = data.get("scenario_catalog", {})
    metadata = data.get("metadata", {})
    
    if not function_hazards and not function_scenarios:
        return {
            "passed": False,
            "errors": [{"type": "no_data", "message": "未找到 function_hazards 或 function_scenarios 数据"}]
        }
    
    all_scenarios = []
    scenarios_per_malfunction = {}
    downstream_not_applicable = []
    scenario_refs = []
    for func, scenarios in function_scenarios.items():
        if isinstance(scenarios, dict):
            for malf, scenario_list in scenarios.items():
                if isinstance(scenario_list, list):
                    scenarios_per_malfunction[f"{func}::{malf}"] = len(scenario_list)
                    if "不适用" in str(malf) and scenario_list:
                        downstream_not_applicable.append(f"{func}::{malf}")
                    for item in scenario_list:
                        if isinstance(item, dict):
                            scenario_refs.append(item)
                            scenario = {
                                "operating_scenario": (
                                    item.get("operating_scenario")
                                    or item.get("_main_scenario")
                                    or item.get("scenario")
                                    or item.get("original_scenario", "")
                                ),
                                "vehicle_state": item.get("vehicle_state", ""),
                                "vehicle_speed": item.get("vehicle_speed", ""),
                                "weather_conditions": item.get("weather_conditions", ""),
                                "road_surface_conditions": item.get("road_surface_conditions", ""),
                                "_malfunction": str(malf).split("||", 1)[0],
                            }
                            all_scenarios.append(scenario)

    profile = metadata.get("domain_profile")
    if isinstance(profile, dict) and str(profile.get("approval_status", "")).lower() != "approved":
        issue = {
            "type": "domain_profile_not_approved",
            "profile": profile.get("name", ""),
            "profile_version": profile.get("version", ""),
            "approval_status": profile.get("approval_status", ""),
            "message": "场景Domain Profile尚未获工程批准，只允许生成Draft报告。",
        }
        (warnings if allow_draft else errors).append(issue)

    pending_scenario_ids = sorted({
        str(item.get("scenario_id", ""))
        for item in scenario_refs
        if str(item.get("scenario_facts", {}).get("engineering_status", "")).upper() == "PENDING"
    })
    if pending_scenario_ids:
        issue = {
            "type": "scenario_engineering_evidence_pending",
            "count": len(pending_scenario_ids),
            "scenario_ids": pending_scenario_ids,
            "message": "场景候选包含待确认的速度、距离、对象或Exposure工程参数。",
        }
        (warnings if allow_draft else errors).append(issue)

    over_soft_target = {
        key: count for key, count in scenarios_per_malfunction.items() if count > 3
    }
    if over_soft_target:
        warnings.append({
            "type": "scenario_review_target_exceeded",
            "soft_target": 3,
            "message": "场景数超过1～3条默认审阅目标；只要风险类别不同即可保留，不作为失败条件",
            "items": over_soft_target,
        })
    if downstream_not_applicable:
        errors.append({
            "type": "not_applicable_reached_phase2",
            "items": downstream_not_applicable,
        })

    metadata = data.get("metadata", {})
    subsystem = str(metadata.get("subsystem", "")).lower()
    if subsystem == "avp":
        if not scenario_catalog:
            errors.append({
                "type": "scenario_catalog_missing",
                "message": "AVP三阶段输出必须提供scenario_catalog作为唯一场景事实源",
            })
        missing_ids = []
        unknown_ids = []
        drifted = []
        for item in scenario_refs:
            scenario_id = item.get("scenario_id", "")
            if not scenario_id:
                missing_ids.append(item.get("_malf_desc", ""))
                continue
            canonical = scenario_catalog.get(scenario_id)
            if not canonical:
                unknown_ids.append(scenario_id)
                continue
            facts = item.get("scenario_facts", {})
            for field, value in facts.items():
                if canonical.get(field) != value:
                    drifted.append({"scenario_id": scenario_id, "field": field})
                    break
        if missing_ids:
            errors.append({"type": "scenario_id_missing", "count": len(missing_ids)})
        if unknown_ids:
            errors.append({"type": "scenario_id_not_in_catalog", "ids": sorted(set(unknown_ids))})
        if drifted:
            errors.append({"type": "scenario_fact_drift", "items": drifted[:10]})

        required_quantitative = (
            "ego_speed_kph", "relative_speed_kph", "longitudinal_acceleration_mps2",
            "steering_angle_status", "object_type", "relative_distance",
        )
        incomplete_facts = []
        for scenario_id, facts in scenario_catalog.items():
            missing = [field for field in required_quantitative if field not in facts or facts[field] == ""]
            if missing:
                incomplete_facts.append({"scenario_id": scenario_id, "missing": missing})
        if incomplete_facts:
            errors.append({"type": "scenario_quantitative_fields_missing", "items": incomplete_facts})

        hazard_refs = []
        for malfunctions in data.get("hazard_events", {}).values():
            for events in malfunctions.values():
                for event in events:
                    if isinstance(event, dict):
                        hazard_refs.append(event.get("scenario_id", ""))
        bad_hazard_refs = [sid for sid in hazard_refs if not sid or sid not in scenario_catalog]
        if bad_hazard_refs:
            errors.append({
                "type": "hazard_event_scenario_reference_invalid",
                "count": len(bad_hazard_refs),
            })
    if subsystem == "avp":
        forbidden_odd = re.compile(
            r"highway|motorway|高速公路|railway|铁路|rural road|乡村道路|"
            r"service area|服务区|off-road|越野|60\s*<\s*v|80\s*<\s*v|130\s*km/h",
            re.IGNORECASE,
        )
        odd_violations = []
        for scenario in all_scenarios:
            text = " ".join(str(value) for value in scenario.values())
            if forbidden_odd.search(text):
                odd_violations.append(text)
        if odd_violations:
            errors.append({
                "type": "avp_odd_violation",
                "count": len(odd_violations),
                "examples": odd_violations[:5],
            })
        scenario_sets = {
            f"{func}::{malf}": scenarios
            for func, malfunctions in data.get("function_scenarios", {}).items()
            for malf, scenarios in malfunctions.items()
            if isinstance(scenarios, list)
        }
        manual_review = {
            key: len(scenarios)
            for key, scenarios in scenario_sets.items() if len(scenarios) > 6
        }
        if manual_review:
            warnings.append({
                "type": "avp_candidate_manual_review_recommended",
                "review_threshold": 6,
                "message": "单个失效超过6个候选场景，请确认每条都会改变危害形式、S/E/C或Safety Goal",
                "items": manual_review,
            })

        # Prevent Cartesian-style repetition semantically instead of enforcing a
        # fixed row count.  A scenario variant is the declared risk class; two
        # candidates with the same risk-driving facts are an invalid duplicate.
        duplicate_risk_candidates = []
        for key, scenarios in scenario_sets.items():
            seen_risk_keys = set()
            for scenario in scenarios:
                risk_key = (
                    str(scenario.get("scenario_variant", "")).strip().lower(),
                    str(scenario.get("object_type", "")).strip().lower(),
                    str(scenario.get("collision_geometry", "")).strip().lower(),
                    str(scenario.get("maneuver", "")).strip().lower(),
                    str(scenario.get("parking_direction", "")).strip().lower(),
                    str(scenario.get("relative_distance", "")).strip().lower(),
                    str(scenario.get("ego_speed_kph", "")).strip().lower(),
                    str(scenario.get("target_speed_kph", "")).strip().lower(),
                    str(scenario.get("driver_state", "")).strip().lower(),
                )
                if risk_key in seen_risk_keys:
                    duplicate_risk_candidates.append(key)
                    break
                seen_risk_keys.add(risk_key)
        if duplicate_risk_candidates:
            errors.append({
                "type": "avp_risk_equivalent_candidate_duplication",
                "message": "存在风险驱动维度相同的重复候选场景；不得仅靠天气/文字排列扩充数量",
                "items": duplicate_risk_candidates,
            })
    
    used_scenarios = []
    for scenario in all_scenarios:
        if scenario.get("operating_scenario"):
            used_scenarios.append(scenario["operating_scenario"])
        elif scenario.get("original_scenario"):
            used_scenarios.append(scenario["original_scenario"])
    
    if subsystem == "avp":
        # Completeness is Item-ODD based.  Requiring highway, railway and other
        # global template scenarios for AVP was the direct source of expansion.
        parking_count = sum(
            1 for value in used_scenarios
            if "parking" in str(value).lower() or "停车场" in str(value)
        )
        scenario_completeness = {
            "complete": bool(used_scenarios) and parking_count == len(used_scenarios),
            "total_standard": 1,
            "used_count": 1 if parking_count else 0,
            "missing_count": 0 if parking_count else 1,
            "used_scenarios": ["AVP Parking Lot / Garage"] if parking_count else [],
            "missing_scenarios": [] if parking_count else ["AVP Parking Lot / Garage"],
            "message": "✓ AVP场景均位于Item ODD" if parking_count == len(used_scenarios) and used_scenarios else "✗ AVP场景超出Item ODD",
        }
    else:
        scenario_completeness = checker.check_scenario_completeness(used_scenarios)
    if not scenario_completeness.get("complete", False):
        if used_scenarios:
            warnings.append({
                "type": "scenario_incomplete",
                "missing_count": scenario_completeness.get("missing_count", 0),
                "missing_scenarios": scenario_completeness.get("missing_scenarios", []),
                "message": scenario_completeness.get("message", "")
            })
        else:
            errors.append({
                "type": "scenario_incomplete",
                "missing_count": scenario_completeness.get("missing_count", 0),
                "missing_scenarios": scenario_completeness.get("missing_scenarios", []),
                "message": scenario_completeness.get("message", "")
            })

    invalid_combos = []
    for scenario in all_scenarios:
        if scenario.get("operating_scenario") == "不适用":
            continue
        check_result = checker.check_malfunction_scenario_combo(
            scenario.get("_malfunction", ""), scenario
        )
        if not check_result.get("is_valid", False):
            invalid_combos.append({
                "scenario": scenario,
                "errors": check_result.get("reasons", [])
            })
    
    passed = len(errors) == 0 and len(all_scenarios) > 0

    summary = {
        "total_scenarios": len(all_scenarios),
        "scenario_completeness": scenario_completeness,
        "invalid_combinations": len(invalid_combos),
        "max_scenarios_per_malfunction": max(scenarios_per_malfunction.values(), default=0),
        "scenario_count_by_malfunction": scenarios_per_malfunction,
        "warnings": warnings
    }

    if verbose:
        print(f"\n场景检查结果:")
        print(f"  场景数量：{len(all_scenarios)}")
        print(f"  主场景完整性：{'通过' if scenario_completeness.get('complete') else '部分缺失'}")
        if scenario_completeness.get("missing_scenarios"):
            print(f"  缺失的主场景：{scenario_completeness['missing_scenarios']}")
        if invalid_combos:
            print(f"\n  无效组合详情:")
            for combo in invalid_combos[:10]:
                print(f"    - {combo['scenario'].get('operating_scenario', '')}: {', '.join(combo['errors'][:2])}")

    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "summary": summary
    }


# This is a production quality-gate helper retained for backward compatibility,
# not a pytest test function.  Prevent pytest from collecting it when imported
# into a regression module.
test_scenario_checker.__test__ = False


def main():
    parser = argparse.ArgumentParser(description="Scenario Checker - 场景合理性检查")
    parser.add_argument("--input", "-i", type=str, required=True, help="scen_hazevent_output.json 路径")
    parser.add_argument("--template", "-t", type=str, help="HARA模板Excel路径 (可选)")
    parser.add_argument("--output", "-o", type=str, help="输出报告路径 (可选)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--allow-draft", action="store_true", help="允许FTTI/非QM不完整等草稿通过")
    args = parser.parse_args()

    result = test_scenario_checker(
        args.input,
        verbose=args.verbose,
        template_path=args.template,
        allow_draft=args.allow_draft,
    )
    
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"检查结果已保存到：{args.output}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    sys.exit(0 if result.get("passed") else 1)


if __name__ == "__main__":
    main()

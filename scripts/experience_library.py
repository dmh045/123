#!/usr/bin/env python3
"""
HARA经验库索引与调取模块
功能：
1. 扫描HARA经验库目录，建立子系统/功能/引导词索引
2. 根据用户输入信息匹配经验库中的HARA分析结论
3. 从05_HARA sheet调取完整的HARA分析数据
4. 支持导出到不同模板格式的Excel
"""

import os
import json
import re
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict
import logging

try:
    import openpyxl
except ImportError:
    openpyxl = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_default_library_dir() -> str:
    """获取默认经验库目录路径，优先使用实际存在的路径"""
    configured_path = os.environ.get("HARA_EXPERIENCE_LIBRARY")
    possible_paths = [
        configured_path,
        r"C:\Users\TVUEDEQ\AI_task\01_HARA\99_HARA-CEA",
        r"C:\Users\F21N3EE\AIHARA\AI_HARA_Skill\99_HARA-CEA",
    ]
    for path in possible_paths:
        if path and os.path.exists(path):
            return path
    return configured_path or possible_paths[1]


def _extract_subsystem_name_from_filename(filename: str) -> str:
    """从文件名提取子系统名称，统一处理&和空格等特殊字符"""
    name = os.path.basename(filename)
    name = name.replace('.xlsx', '')
    patterns = [
        r'VCTC_(.+?)_HARA',
        r'(.+?)_HARA',
    ]
    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            extracted = match.group(1)
            extracted = extracted.replace('&', '和')
            extracted = re.sub(r'\s+', ' ', extracted).strip()
            return extracted
    return name


class HaraColumnMapper:
    """HARA列映射器 - 处理不同模板的列差异"""

    LIBRARY_COLUMNS = [
        "HARA-ID", "Function", "Output", "Guide-Word", "Malfunction",
        "Hazard", "Potential damage", "Situational description", "Situational detailing",
        "Severity", "Severity_Rationale", "Exposure", "Exposure_TF", "Exposure_Rationale",
        "Controllability", "Controllability_Rationale", "ASIL",
        "HZ-ID", "Hazard_Full", "SG-ID", "Safety Goal", "Remark", "Subsystem"
    ]

    USER_TEMPLATE_COLUMNS = {
        1: "HARA-ID",
        2: "Function",
        3: "Output",
        4: "Guide-Word",
        5: "Malfunction",
        6: "Hazard",
        7: "Situational description",
        8: "Situational detailing",
        9: "Potential damage",
        10: "Severity",
        11: "Severity_Rationale",
        12: "Exposure",
        13: "Exposure_TF",
        14: "Exposure_Rationale",
        15: "Controllability",
        16: "Controllability_Rationale",
        17: "ASIL",
        18: "SG-ID",
        19: "Safety Goal",
        20: "Safe state",
        21: "Remark"
    }

    SAFETY_GOAL_MAPPING: Dict[str, str] = {}
    SAFETY_GOAL_MAPPING_BY_SUBSYSTEM: Dict[str, Dict] = {}
    SAFETY_GOAL_MAPPING_BY_FILE: Dict[str, Dict] = {}

    @classmethod
    def load_safety_goal_mapping(cls, library_dir: str):
        """从经验库加载SG-ID/Safety Goal到Safety State的映射（按文件/子系统分组）"""
        cls.SAFETY_GOAL_MAPPING = {}
        cls.SAFETY_GOAL_MAPPING_BY_SUBSYSTEM = {}
        cls.SUBSYSTEM_SG_MAPPING = {}
        if not openpyxl:
            return

        def extract_subsystem_name(filename: str) -> str:
            return _extract_subsystem_name_from_filename(filename)

        try:
            excel_files = [f for f in os.listdir(library_dir) if f.endswith('.xlsx') and not f.startswith('~')]
            for f in excel_files:
                file_path = os.path.join(library_dir, f)
                wb = openpyxl.load_workbook(file_path, data_only=True)

                file_mapping = {}
                subsystem_name = extract_subsystem_name(f)

                if '06_Safety Goal' in wb.sheetnames:
                    ws = wb['06_Safety Goal']
                    for row in range(4, ws.max_row + 1):
                        sg_id = ws.cell(row, 3).value
                        safety_goal = ws.cell(row, 4).value
                        safety_state = ws.cell(row, 5).value

                        if not safety_state:
                            continue

                        if sg_id:
                            sg_id_str = str(sg_id).strip()
                            file_mapping[sg_id_str] = safety_state

                        if safety_goal:
                            sg_str = str(safety_goal).strip()
                            file_mapping[sg_str] = safety_state

                cls.SAFETY_GOAL_MAPPING_BY_SUBSYSTEM[subsystem_name] = file_mapping
                for sg_id_or_goal, state in file_mapping.items():
                    cls.SAFETY_GOAL_MAPPING[f"{subsystem_name}|{sg_id_or_goal}"] = state

                wb.close()
        except Exception as e:
            logger.warning(f"加载Safety Goal映射失败: {e}")
    
    @classmethod
    def map_record_to_user_template(cls, record: Dict, library_dir: str = None) -> Dict:
        """将经验库记录映射到用户模板格式

        Args:
            record: 经验库原始记录
            library_dir: 经验库目录（用于加载Safety Goal映射）

        Returns:
            映射后的记录 (按用户模板列顺序)
        """
        if library_dir and not cls.SAFETY_GOAL_MAPPING:
            cls.load_safety_goal_mapping(library_dir)

        mapped = {}
        for target_col, field_name in cls.USER_TEMPLATE_COLUMNS.items():
            if target_col == 20 and field_name == "Safe state":
                mapped[target_col] = cls._get_safety_state(record, library_dir)
            else:
                mapped[target_col] = record.get(field_name, "")
        return mapped

    @classmethod
    def _get_safety_state(cls, record: Dict, library_dir: str = None) -> str:
        """根据SG-ID或Safety Goal从06_Safety Goal sheet获取Safety State

        Args:
            record: HARA记录（包含Subsystem字段表示子系统名）
            library_dir: 经验库目录

        Returns:
            Safety State值
        """
        if library_dir and not cls.SAFETY_GOAL_MAPPING_BY_SUBSYSTEM:
            cls.load_safety_goal_mapping(library_dir)

        subsystem = record.get("Subsystem", "")
        sg_id = record.get("SG-ID", "")
        safety_goal = record.get("Safety Goal", "")

        if subsystem and subsystem in cls.SAFETY_GOAL_MAPPING_BY_SUBSYSTEM:
            subsystem_mapping = cls.SAFETY_GOAL_MAPPING_BY_SUBSYSTEM[subsystem]

            if sg_id and sg_id in subsystem_mapping:
                return subsystem_mapping[sg_id]

            if safety_goal and safety_goal in subsystem_mapping:
                return subsystem_mapping[safety_goal]

            for key, value in subsystem_mapping.items():
                if sg_id and key == str(sg_id):
                    return value
                if safety_goal and key == str(safety_goal):
                    return value

        return ""

    @classmethod
    def export_to_excel(cls, hara_data: List[Dict], template_path: str, output_path: str, start_row: int = 7, library_dir: str = None) -> bool:
        """导出HARA数据到Excel模板

        Args:
            hara_data: HARA记录列表
            template_path: 模板文件路径
            output_path: 输出文件路径
            start_row: 数据起始行 (默认7)
            library_dir: 经验库目录（用于获取Safety State）

        Returns:
            是否导出成功
        """
        if not openpyxl:
            logger.error("openpyxl未安装")
            return False

        if library_dir and not cls.SAFETY_GOAL_MAPPING:
            cls.load_safety_goal_mapping(library_dir)

        try:
            wb = openpyxl.load_workbook(template_path)

            if "05_HARA" not in wb.sheetnames:
                logger.error("模板中不存在05_HARA sheet")
                return False

            ws = wb["05_HARA"]

            for i, record in enumerate(hara_data):
                row = start_row + i
                if row > ws.max_row:
                    ws.append([None] * ws.max_column)

                mapped = cls.map_record_to_user_template(record, library_dir)
                for col, value in mapped.items():
                    ws.cell(row, col, value)

            wb.save(output_path)
            logger.info(f"导出完成: {output_path}, 记录数: {len(hara_data)}")
            return True

        except Exception as e:
            logger.error(f"导出失败: {e}")
            return False


class HARAExperienceLibrary:
    """HARA经验库索引与调取类"""

    def __init__(self, library_dir: str = None):
        """
        初始化HARA经验库

        Args:
            library_dir: HARA经验库目录路径
        """
        if library_dir is None:
            library_dir = _get_default_library_dir()

        self.library_dir = library_dir
        self.index = {
            "subsystems": {},        # 子系统索引: {子系统名: {file_path, functions: []}}
            "functions": {},         # 功能索引: {功能名: {file_path, guidewords: []}}
            "malfunctions": {},      # Malfunction索引
            "hazard_events": {},     # 危害事件索引
            "files": {}              # 文件索引: {file_path: subsystem_name}
        }
        self.hara_data = {}          # HARA数据缓存: {file_path: [rows]}
        self.initialized = False

    def initialize(self) -> bool:
        """
        初始化经验库索引

        Returns:
            初始化是否成功
        """
        if not os.path.exists(self.library_dir):
            logger.error(f"HARA经验库目录不存在: {self.library_dir}")
            return False

        if openpyxl is None:
            logger.error("openpyxl库未安装")
            return False

        try:
            excel_files = self._scan_library_files()
            logger.info(f"发现 {len(excel_files)} 个HARA经验库文件")

            for file_path in excel_files:
                self._index_file(file_path)

            self.initialized = True
            logger.info(f"HARA经验库索引完成: {len(self.index['subsystems'])} 个子系统")
            return True

        except Exception as e:
            logger.error(f"初始化HARA经验库失败: {e}")
            return False

    def _scan_library_files(self) -> List[str]:
        """扫描经验库目录下的Excel文件"""
        excel_files = []
        for f in os.listdir(self.library_dir):
            if f.endswith('.xlsx') and not f.startswith('~'):
                file_path = os.path.join(self.library_dir, f)
                excel_files.append(file_path)
        return excel_files

    def _extract_subsystem_name(self, filename: str) -> str:
        """从文件名提取子系统名称，统一处理&和空格"""
        return _extract_subsystem_name_from_filename(filename)

    def _index_file(self, file_path: str):
        """
        索引单个HARA Excel文件

        Args:
            file_path: Excel文件路径
        """
        try:
            wb = openpyxl.load_workbook(file_path, data_only=True)
            subsystem_name = self._extract_subsystem_name(file_path)

            if '05_HARA' not in wb.sheetnames:
                logger.warning(f"文件 {file_path} 不包含05_HARA sheet，跳过")
                return

            ws = wb['05_HARA']
            hara_rows = self._extract_hara_data(ws, subsystem_name)

            if not hara_rows:
                logger.warning(f"文件 {file_path} 未提取到HARA数据")
                return

            self.hara_data[file_path] = hara_rows
            self.index["files"][file_path] = subsystem_name
            self.index["subsystems"][subsystem_name] = {
                "file_path": file_path,
                "functions": [],
                "record_count": len(hara_rows)
            }

            functions_set = set()
            for row in hara_rows:
                func = row.get("Function")
                if func:
                    functions_set.add(func)

                    if func not in self.index["functions"]:
                        self.index["functions"][func] = {
                            "file_path": file_path,
                            "subsystem": subsystem_name,
                            "guidewords": set(),
                            "malfunctions": []
                        }

                    guideword = row.get("Guide-Word")
                    if guideword:
                        self.index["functions"][func]["guidewords"].add(guideword)

                    malfunction = row.get("Malfunction")
                    if malfunction:
                        self.index["functions"][func]["malfunctions"].append({
                            "malfunction": malfunction,
                            "guideword": guideword,
                            "severity": row.get("Severity"),
                            "exposure": row.get("Exposure"),
                            "controllability": row.get("Controllability"),
                            "ASIL": row.get("ASIL"),
                            "hazard": row.get("Hazard"),
                            "safety_goal": row.get("Safety Goal")
                        })

            self.index["subsystems"][subsystem_name]["functions"] = list(functions_set)
            wb.close()
            logger.info(f"索引文件 {subsystem_name}: {len(hara_rows)} 条记录, {len(functions_set)} 个功能")

        except Exception as e:
            logger.error(f"索引文件失败 {file_path}: {e}")

    def _extract_hara_data(self, ws, subsystem_name: str) -> List[Dict]:
        """
        从05_HARA sheet提取HARA数据

        默认使用标准 HARA_Template 列位置（A=1..U=21），通过读取 header 行检测实际列偏移。

        标准模板列布局：
          1=HARA-ID, 2=Function, 3=Output, 4=Guide-Word, 5=Malfunction,
          6=Hazard, 7=Situational description, 8=Situational detailing,
          9=Hazardous event, 10=Severity, 11=Rationale Severity,
          12=Exposure, 13=T/F, 14=Rationale Exposure,
          15=Controllability, 16=Rationale Controllability, 17=ASIL,
          18=SG-ID, 19=Safety Goal, 20=Safe state, 21=Remark

        通过检测 header 中的 "HARA-ID" (col 1) 和 "ASIL" (col 17) 确定是否有列偏移。
        """
        data = []

        def get_row_headers(ws):
            r4 = {}
            r5 = {}
            for ci in range(1, ws.max_column + 1):
                v4 = ws.cell(4, ci).value
                v5 = ws.cell(5, ci).value
                r4[ci] = str(v4 or '').replace('\n', ' ').strip() if v4 else ''
                r5[ci] = str(v5 or '').replace('\n', ' ').strip() if v5 else ''
            return r4, r5

        r4, r5 = get_row_headers(ws)

        def has_header(ci, *texts):
            h = r5[ci] or r4[ci]
            if not h:
                return False
            h = h.lower()
            for t in texts:
                if t.lower() in h:
                    return True
            return False

        detected_offset = 0
        for ci in range(1, ws.max_column + 1):
            h = r5[ci] or r4[ci]
            if h and 'HARA-ID' in h and 'Function' not in h:
                detected_offset = ci - 1
                break

        def col(n):
            return n + detected_offset

        STANDARD = {
            'HARA-ID': col(1), 'Function': col(2), 'Output': col(3), 'Guide-Word': col(4),
            'Malfunction': col(5), 'Hazard': col(6), 'Situational description': col(7),
            'Situational detailing': col(8), 'Potential damage': col(9),
            'Severity': col(10), 'Severity_Rationale': col(11),
            'Exposure': col(12), 'Exposure_TF': col(13), 'Exposure_Rationale': col(14),
            'Controllability': col(15), 'Controllability_Rationale': col(16), 'ASIL': col(17),
            'HZ-ID': col(18), 'Hazard_Full': col(19), 'SG-ID': col(20),
            'Safety Goal': col(21), 'Safe state': col(22), 'Remark': col(23),
        }

        c_override = {}
        for ci in range(1, ws.max_column + 1):
            h = r5[ci] or r4[ci]
            if not h:
                continue
            h_lower = h.lower()
            if 'rationale severity evaluation' in h_lower or 'rationale severity' == h_lower.split('(')[0].strip().lower():
                c_override['Severity_Rationale'] = ci
            elif 'rationale exposure evaluation' in h_lower or 'rationale exposure' == h_lower.split('(')[0].strip().lower():
                c_override['Exposure_Rationale'] = ci
            elif 'rationale controllability evaluation' in h_lower or 'rationale controllability' == h_lower.split('(')[0].strip().lower():
                c_override['Controllability_Rationale'] = ci
            elif has_header(ci, 'HZ-ID') and not has_header(ci, 'SZ-ID', 'SG-ID'):
                c_override['HZ-ID'] = ci
            elif has_header(ci, 'Hazard') and not has_header(ci, 'HARA-ID', 'Function'):
                c_override['Hazard_Full'] = ci
            elif has_header(ci, 'SZ-ID', 'SG-ID'):
                c_override['SG-ID'] = ci
            elif 'safety goal' in h_lower:
                c_override['Safety Goal'] = ci
            elif 'safe state' in h_lower:
                c_override['Safe state'] = ci

        c = {k: c_override.get(k, v) for k, v in STANDARD.items()}

        for row_idx in range(1, ws.max_row + 1):
            v = ws.cell(row_idx, 1).value
            if v and str(v).strip():
                vs = str(v).strip()
                if vs == 'HARA-ID':
                    continue
                if vs.startswith('Function <') or vs.startswith('HARA_'):
                    break

        for row_idx in range(row_idx, ws.max_row + 1):
            v = ws.cell(row_idx, 1).value
            if not (v and str(v).strip()):
                continue
            vs = str(v).strip()
            if vs.startswith('Function <'):
                continue
            if not vs.startswith('HARA_'):
                continue

            def g(k):
                ci = c.get(k)
                return ws.cell(row_idx, ci).value if ci else None

            record = {
                "HARA-ID": g('HARA-ID'),
                "Function": g('Function'),
                "Output": g('Output'),
                "Guide-Word": g('Guide-Word'),
                "Malfunction": g('Malfunction'),
                "Hazard": g('Hazard'),
                "Potential damage": g('Potential damage'),
                "Situational description": g('Situational description'),
                "Situational detailing": g('Situational detailing'),
                "Severity": g('Severity'),
                "Severity_Rationale": g('Severity_Rationale'),
                "Exposure": g('Exposure'),
                "Exposure_TF": g('Exposure_TF'),
                "Exposure_Rationale": g('Exposure_Rationale'),
                "Controllability": g('Controllability'),
                "Controllability_Rationale": g('Controllability_Rationale'),
                "ASIL": g('ASIL'),
                "HZ-ID": g('HZ-ID'),
                "Hazard_Full": g('Hazard_Full'),
                "SG-ID": g('SG-ID'),
                "Safety Goal": g('Safety Goal'),
                "Safe state": g('Safe state'),
                "Remark": g('Remark'),
                "Subsystem": subsystem_name
            }
            data.append(record)

        return data

    def search_by_subsystem(self, subsystem_name: str) -> Dict[str, Any]:
        """
        根据子系统名称搜索HARA分析结论

        Args:
            subsystem_name: 子系统名称（如"Light"、"Door"、"Seat"等）

        Returns:
            搜索结果
        """
        if not self.initialized:
            return {"success": False, "error": "经验库未初始化"}

        subsystem_name_lower = subsystem_name.lower()
        matched_subsystems = []

        for subsystem, info in self.index["subsystems"].items():
            if subsystem_name_lower in subsystem.lower():
                matched_subsystems.append(subsystem)

        if not matched_subsystems:
            return {
                "success": False,
                "error": f"未找到子系统: {subsystem_name}",
                "available_subsystems": list(self.index["subsystems"].keys())
            }

        results = {}
        for subsystem in matched_subsystems:
            file_path = self.index["subsystems"][subsystem]["file_path"]
            if file_path in self.hara_data:
                results[subsystem] = {
                    "file_path": file_path,
                    "record_count": len(self.hara_data[file_path]),
                    "functions": self.index["subsystems"][subsystem]["functions"],
                    "hara_data": self.hara_data[file_path]
                }

        return {
            "success": True,
            "subsystem_name": subsystem_name,
            "matched_subsystems": matched_subsystems,
            "results": results
        }

    def search_by_function(self, function_name: str) -> Dict[str, Any]:
        """
        根据功能名称搜索HARA分析结论

        Args:
            function_name: 功能名称

        Returns:
            搜索结果
        """
        if not self.initialized:
            return {"success": False, "error": "经验库未初始化"}

        function_name_lower = function_name.lower()
        matched_functions = []

        for func in self.index["functions"].keys():
            if function_name_lower in func.lower():
                matched_functions.append(func)

        if not matched_functions:
            return {
                "success": False,
                "error": f"未找到功能: {function_name}",
                "available_functions": list(self.index["functions"].keys())[:20]
            }

        results = {}
        for func in matched_functions:
            func_info = self.index["functions"][func]
            file_path = func_info["file_path"]
            if file_path in self.hara_data:
                results[func] = {
                    "file_path": file_path,
                    "subsystem": func_info["subsystem"],
                    "guidewords": list(func_info["guidewords"]),
                    "malfunctions": func_info["malfunctions"],
                    "full_hara_data": [r for r in self.hara_data[file_path] if r.get("Function") == func]
                }

        return {
            "success": True,
            "function_name": function_name,
            "matched_functions": matched_functions,
            "results": results
        }

    def search_by_malfunction(self, malfunction: str) -> Dict[str, Any]:
        """
        根据Malfunction搜索HARA分析结论

        Args:
            malfunction: Malfunction描述

        Returns:
            搜索结果
        """
        if not self.initialized:
            return {"success": False, "error": "经验库未初始化"}

        malfunction_lower = malfunction.lower()
        matched_records = []

        for file_path, hara_data in self.hara_data.items():
            for record in hara_data:
                malf = record.get("Malfunction", "")
                if malf and malfunction_lower in malf.lower():
                    matched_records.append({
                        "file_path": file_path,
                        "subsystem": record.get("Subsystem"),
                        "record": record
                    })

        if not matched_records:
            return {
                "success": False,
                "error": f"未找到Malfunction: {malfunction}"
            }

        return {
            "success": True,
            "malfunction": malfunction,
            "matched_count": len(matched_records),
            "results": matched_records
        }

    def get_function_hara(self, subsystem: str = None, function: str = None) -> Dict[str, Any]:
        """
        获取指定子系统或功能的完整HARA分析结论

        Args:
            subsystem: 子系统名称
            function: 功能名称（可选）

        Returns:
            HARA分析结论
        """
        if not self.initialized:
            return {"success": False, "error": "经验库未初始化"}

        if subsystem:
            return self.search_by_subsystem(subsystem)
        elif function:
            return self.search_by_function(function)
        else:
            return {"success": False, "error": "请提供subsystem或function参数"}

    def match_input(self, user_input: str) -> Dict[str, Any]:
        """
        根据用户输入智能匹配HARA经验库

        Args:
            user_input: 用户输入的信息

        Returns:
            匹配结果
        """
        if not self.initialized:
            self.initialize()

        user_input_lower = user_input.lower()

        subsystem_keywords = {
            "light": ["灯光", "照明", "车灯", "light", "lamp", "headlamp", "尾灯"],
            "door": ["车门", "门", "door", "liftgate"],
            "window": ["车窗", "窗", "window", "玻璃"],
            "seat": ["座椅", "座位", "seat"],
            "wiper": ["雨刷", "刮水器", "wiper", "雨刮"],
            "hud": ["HUD", "抬头显示", "head-up display"]
        }

        for subsystem_key, keywords in subsystem_keywords.items():
            for kw in keywords:
                if kw in user_input_lower:
                    return self.search_by_subsystem(subsystem_key)

        for func in self.index["functions"].keys():
            if func.lower() in user_input_lower:
                return self.search_by_function(func)

        return {
            "success": False,
            "error": "无法从输入中识别HARA经验库内容",
            "hint": "请输入子系统名称（如Light、Door、Seat）或功能名称"
        }

    def match_and_lock_excel(self, user_input: str) -> Dict[str, Any]:
        """优化后的匹配方法：锁定Excel文件并返回可直接提取数据的结果

        Args:
            user_input: 用户输入的信息

        Returns:
            包含匹配结果和锁定Excel信息的字典
        """
        if not self.initialized:
            self.initialize()

        match_result = self.match_input(user_input)
        if not match_result.get("success"):
            return match_result

        results = match_result.get("results", {})
        if not results:
            return {"success": False, "error": "匹配结果为空"}

        first_key = list(results.keys())[0]
        first_result = results[first_key]
        file_path = first_result.get("file_path")

        if not file_path or not os.path.exists(file_path):
            return {"success": False, "error": f"Excel文件不存在: {file_path}"}

        wb = None
        try:
            wb = openpyxl.load_workbook(file_path, data_only=True)

            if "05_HARA" not in wb.sheetnames:
                return {"success": False, "error": "Excel中不存在05_HARA sheet"}

            ws_hara = wb["05_HARA"]

            header_map = self._find_column_headers(ws_hara)

            hara_data = first_result.get("hara_data", [])
            if first_result.get("full_hara_data"):
                hara_data = first_result.get("full_hara_data")

            return {
                "success": True,
                "subsystem": first_key,
                "file_path": file_path,
                "workbook": wb,
                "worksheet": ws_hara,
                "header_map": header_map,
                "hara_data": hara_data,
                "record_count": len(hara_data),
                "functions": first_result.get("functions", [])
            }

        except Exception as e:
            if wb:
                wb.close()
            return {"success": False, "error": f"打开Excel失败: {e}"}

    def _find_column_headers(self, ws) -> Dict[str, int]:
        """在worksheet中查找列标题并返回列索引映射

        合并多行标题信息。主标题行通常是包含 "HARA-ID" 和 "Function" 的行，
        子标题行通常包含 "S"、"E"、"C"、"HZ-ID"、"SG-ID" 等单字母或短标题。

        Args:
            ws: openpyxl worksheet

        Returns:
            标题到列索引的映射字典
        """
        header_map = {}

        main_header_row = None
        sub_header_row = None

        for row_idx in range(1, min(10, ws.max_row + 1)):
            col1_val = ws.cell(row_idx, 1).value
            if col1_val and "HARA-ID" in str(col1_val):
                main_header_row = row_idx
                break
            if col1_val and "HARA_" in str(col1_val):
                break

        if main_header_row:
            for row_idx in range(main_header_row + 1, min(main_header_row + 5, ws.max_row + 1)):
                col10_val = ws.cell(row_idx, 10).value
                if col10_val and str(col10_val).strip() == "S":
                    sub_header_row = row_idx
                    break

        if main_header_row:
            for col_idx in range(1, ws.max_column + 1):
                val = ws.cell(main_header_row, col_idx).value
                if val:
                    header_map[str(val).strip()] = col_idx

        if sub_header_row:
            for col_idx in range(1, ws.max_column + 1):
                val = ws.cell(sub_header_row, col_idx).value
                if val:
                    header_map[str(val).strip()] = col_idx

        return header_map

    def extract_column_data(self, locked_result: Dict[str, Any], column_name: str) -> List[str]:
        """从锁定的Excel中提取指定列的数据

        Args:
            locked_result: match_and_lock_excel返回的结果
            column_name: 列名称（如"SG-ID"、"Safety Goal"等）

        Returns:
            列数据列表
        """
        if not locked_result.get("success"):
            return []

        ws = locked_result.get("worksheet")
        header_map = locked_result.get("header_map", {})

        col_idx = None
        for header, idx in header_map.items():
            if column_name.lower() in header.lower():
                col_idx = idx
                break

        if not col_idx:
            return []

        data = []
        header_row = None
        for row_idx in range(1, min(10, ws.max_row + 1)):
            if ws.cell(row_idx, 1).value and "Function" in str(ws.cell(row_idx, 1).value):
                header_row = row_idx
                break

        if not header_row:
            return []

        for row_idx in range(header_row + 1, ws.max_row + 1):
            cell_value = ws.cell(row_idx, col_idx).value
            if cell_value:
                data.append(str(cell_value).strip())

        return data

    def close_locked_excel(self, locked_result: Dict[str, Any]):
        """关闭锁定的Excel文件

        Args:
            locked_result: match_and_lock_excel返回的结果
        """
        wb = locked_result.get("workbook")
        if wb:
            wb.close()

    def get_index_summary(self) -> Dict[str, Any]:
        """
        获取索引摘要

        Returns:
            索引摘要信息
        """
        if not self.initialized:
            return {"success": False, "error": "经验库未初始化"}

        return {
            "success": True,
            "library_dir": self.library_dir,
            "total_files": len(self.index["files"]),
            "total_subsystems": len(self.index["subsystems"]),
            "total_functions": len(self.index["functions"]),
            "subsystems": list(self.index["subsystems"].keys()),
            "function_count_by_subsystem": {
                k: len(v["functions"]) for k, v in self.index["subsystems"].items()
            }
        }

    def auto_generate_output_path(self, subsystem: str) -> str:
        """
        自动生成输出路径

        Args:
            subsystem: 子系统名称

        Returns:
            自动生成的JSON文件路径
        """
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', subsystem)
        filename = f"experience_library_{safe_name}_{timestamp}.json"
        return os.path.join(self.library_dir, filename)

    def get_template_headers(self, template_path: str) -> Dict[str, Any]:
        """
        获取模板文件的05_HARA和06_Safety Goal页的列标题映射

        Args:
            template_path: 模板文件路径

        Returns:
            包含各sheet列标题的字典
        """
        if not openpyxl:
            return {"success": False, "error": "openpyxl未安装"}

        if not os.path.exists(template_path):
            return {"success": False, "error": f"模板文件不存在: {template_path}"}

        result = {"success": True, "template_path": template_path, "sheets": {}}

        try:
            wb = openpyxl.load_workbook(template_path, data_only=True)
            target_sheets = ['05_HARA', '06_Safety Goal']

            for sheet_name in target_sheets:
                if sheet_name not in wb.sheetnames:
                    continue

                ws = wb[sheet_name]
                headers = {}

                for row_idx in range(1, min(10, ws.max_row + 1)):
                    for col_idx in range(1, ws.max_column + 1):
                        val = ws.cell(row_idx, col_idx).value
                        if val and str(val).strip():
                            key = str(val).strip()
                            if key not in headers:
                                headers[key] = {
                                    "col": col_idx,
                                    "row": row_idx
                                }

                result["sheets"][sheet_name] = headers

            wb.close()
            return result

        except Exception as e:
            logger.error(f"读取模板标题失败: {e}")
            return {"success": False, "error": str(e)}

    def _extract_safety_goals(self, file_path: str) -> List[Dict]:
        """
        从06_Safety Goal sheet提取安全目标数据

        Args:
            file_path: Excel文件路径

        Returns:
            安全目标数据列表
        """
        if not openpyxl:
            return []

        try:
            wb = openpyxl.load_workbook(file_path, data_only=True)

            if '06_Safety Goal' not in wb.sheetnames:
                wb.close()
                return []

            ws = wb['06_Safety Goal']
            sg_data = []

            for row_idx in range(1, ws.max_row + 1):
                row_values = [ws.cell(row_idx, col).value for col in range(1, 10)]

                if row_idx <= 3:
                    continue

                if row_values[0] is None and row_values[1] is None and row_values[2] is None:
                    continue

                record = {
                    "Hazard_ID": row_values[0],
                    "Hazard_Name": row_values[1],
                    "SG-ID": row_values[2],
                    "Safety Goal": row_values[3],
                    "Safe state": row_values[4],
                    "Max.ASIL": row_values[5],
                    "Verification": row_values[6],
                    "Remark": row_values[7],
                    "subsystem": self._extract_subsystem_name(file_path)
                }

                if any(v for v in [record["SG-ID"], record["Safety Goal"], record["Safe state"]]):
                    sg_data.append(record)

            wb.close()
            return sg_data

        except Exception as e:
            logger.warning(f"提取Safety Goal失败 {file_path}: {e}")
            return []

    def _generate_safety_goals_from_hara(self, hara_records: List[Dict], file_path: str):
        """
        从 HARA 记录中动态生成 06_Safety Goal 数据

        遍历所有 HARA 记录，对每个非 QM 且有 HZ-ID 的记录生成 SG 条目。
        同一 HZ-ID 保留 ASIL 等级最高的记录。

        Args:
            hara_records: HARA 记录列表
            file_path: 源 Excel 文件路径（用于读取 SG sheet 作为补充）
        """
        self._safety_goal_records = []
        sg_map = {}

        asil_rank = {"QM": 0, "ASIL A": 1, "ASIL B": 2, "ASIL C": 3, "ASIL D": 4}

        for r in hara_records:
            hz_id = str(r.get("HZ-ID", "")).strip()
            sg_id = str(r.get("SG-ID", "")).strip()
            asil_str = str(r.get("ASIL", "")).strip().upper()
            safety_goal = r.get("Safety Goal", "")
            hazard = r.get("Hazard", "") or r.get("Hazard_Full", "")

            if not hz_id or asil_str == "QM" or asil_str == "":
                continue

            record = {
                "HZ-ID": hz_id,
                "Hazard": hazard,
                "SG-ID": sg_id,
                "Safety Goal": safety_goal,
                "Safety State": r.get("Safe state", ""),
                "Max.ASIL": r.get("ASIL", "")
            }

            if hz_id not in sg_map:
                sg_map[hz_id] = {"ASIL": asil_str, "record": record}
            else:
                current_rank = asil_rank.get(asil_str, 0)
                existing_rank = asil_rank.get(sg_map[hz_id]["ASIL"], 0)
                if current_rank > existing_rank:
                    sg_map[hz_id] = {"ASIL": asil_str, "record": record}

        for sg_record in sg_map.values():
            self._safety_goal_records.append(sg_record["record"])

        existing_sg = self._extract_safety_goals(file_path)
        for sg in existing_sg:
            hz_id = str(sg.get("SG-ID", sg.get("HZ-ID", ""))).strip()
            if hz_id and not any(s.get("SG-ID", "") == hz_id for s in self._safety_goal_records):
                self._safety_goal_records.append(sg)

    def extract_to_json(self, subsystem: str, output_path: str = None) -> str:
        """
        从经验库提取指定子系统的完整HARA数据到JSON文件

        Args:
            subsystem: 子系统名称（如"Light", "Door", "Seat"等）
            output_path: 输出JSON路径（可选，默认自动生成）

        Returns:
            生成的JSON文件路径
        """
        if not self.initialized:
            self.initialize()

        search_result = self.search_by_subsystem(subsystem)
        if not search_result.get("success"):
            raise ValueError(f"未找到子系统: {subsystem} - {search_result.get('error')}")

        results = search_result.get("results", {})
        if not results:
            raise ValueError(f"子系统 {subsystem} 无匹配结果")

        subsystem_name = list(results.keys())[0]
        result_data = results[subsystem_name]
        file_path = result_data["file_path"]
        hara_records = result_data["hara_data"]

        if output_path is None:
            output_path = self.auto_generate_output_path(subsystem_name)

        hara_records = self.hara_data.get(file_path, hara_records)

        safety_goals = self._extract_safety_goals(file_path)

        functions = result_data.get("functions", [])
        if not functions:
            functions = list(set(r.get("Function", "") for r in hara_records if r.get("Function")))

        qm_count = sum(1 for r in hara_records if r.get("ASIL", "").upper() == "QM")
        non_qm_count = len(hara_records) - qm_count

        asil_distribution = {"QM": qm_count}
        for r in hara_records:
            asil = str(r.get("ASIL", "")).strip().upper()
            if asil not in asil_distribution:
                asil_distribution[asil] = 0
            asil_distribution[asil] += 1

        template_path = file_path
        hara_headers = {}
        sg_headers = {}
        try:
            if openpyxl:
                wb = openpyxl.load_workbook(template_path, data_only=True)
                for sheet_name in ['05_HARA', '06_Safety Goal']:
                    if sheet_name in wb.sheetnames:
                        ws = wb[sheet_name]
                        for row_idx in range(1, min(10, ws.max_row + 1)):
                            for col_idx in range(1, ws.max_column + 1):
                                val = ws.cell(row_idx, col_idx).value
                                if val and str(val).strip():
                                    key = str(val).strip()
                                    if sheet_name == '05_HARA':
                                        if key not in hara_headers:
                                            hara_headers[key] = {"col": col_idx, "row": row_idx}
                                    else:
                                        if key not in sg_headers:
                                            sg_headers[key] = {"col": col_idx, "row": row_idx}
                wb.close()
        except Exception as e:
            logger.warning(f"读取模板标题时出错: {e}")

        hara_data_raw = []
        for r in hara_records:
            hara_data_raw.append({k: v for k, v in r.items() if v is not None and v != ""})

        self._generate_safety_goals_from_hara(hara_data_raw, file_path)

        json_data = {
            "05_HARA": hara_data_raw,
            "06_Safety Goal": self._safety_goal_records,
            "subsystem": subsystem_name,
            "timestamp": self._get_timestamp(),
            "record_count": len(hara_records),
            "source": file_path,
            "exported_by": "experience_library.extract_to_json()",
            "hara_data": hara_data_raw,
            "safety_goals": self._safety_goal_records,
            "summary": {
                "total_records": len(hara_records),
                "qm_count": qm_count,
                "non_qm_count": non_qm_count,
                "asil_distribution": asil_distribution
            }
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON导出完成: {output_path}, 记录数: {len(hara_records)}")
        return output_path

    def extract_with_template_mapping(self, subsystem: str, template_path: str) -> Dict[str, Any]:
        """
        提取数据并按照指定模板的列标题进行映射

        Args:
            subsystem: 子系统名称
            template_path: 模板文件路径

        Returns:
            映射后的完整数据字典
        """
        if not self.initialized:
            self.initialize()

        search_result = self.search_by_subsystem(subsystem)
        if not search_result.get("success"):
            return {"success": False, "error": search_result.get("error")}

        results = search_result.get("results", {})
        subsystem_name = list(results.keys())[0]
        result_data = results[subsystem_name]
        file_path = result_data["file_path"]
        hara_records = self.hara_data.get(file_path, result_data["hara_data"])

        template_headers = self.get_template_headers(template_path)
        if not template_headers.get("success"):
            return {"success": False, "error": template_headers.get("error")}

        hara_template_map = template_headers["sheets"].get("05_HARA", {})
        sg_template_map = template_headers["sheets"].get("06_Safety Goal", {})

        asil_rank = {"QM": 0, "ASIL A": 1, "ASIL B": 2, "ASIL C": 3, "ASIL D": 4}
        sg_records = {}
        for r in hara_records:
            sg_id = r.get("SG-ID", "")
            asil = str(r.get("ASIL", "")).strip().upper()
            if sg_id and asil not in ("QM", ""):
                if sg_id not in sg_records:
                    sg_records[sg_id] = {"ASIL": asil, "record": r}
                else:
                    current_rank = asil_rank.get(asil, 0)
                    existing_rank = asil_rank.get(sg_records[sg_id]["ASIL"], 0)
                    if current_rank > existing_rank:
                        sg_records[sg_id] = {"ASIL": asil, "record": r}

        safety_goals = self._extract_safety_goals(file_path)

        functions = list(set(r.get("Function", "") for r in hara_records if r.get("Function")))
        qm_count = sum(1 for r in hara_records if r.get("ASIL", "").upper() == "QM")

        asil_distribution = {"QM": qm_count}
        for r in hara_records:
            asil = str(r.get("ASIL", "")).strip()
            if asil not in asil_distribution:
                asil_distribution[asil] = 0
            asil_distribution[asil] += 1

        return {
            "success": True,
            "subsystem": subsystem_name,
            "file_path": file_path,
            "version": "V12",
            "source": "experience_library",
            "timestamp": self._get_timestamp(),
            "record_count": len(hara_records),
            "functions": functions,
            "template_mapped": {
                "05_HARA": {
                    "headers": hara_template_map,
                    "data": hara_records
                },
                "06_Safety Goal": {
                    "headers": sg_template_map,
                    "data": safety_goals
                }
            },
            "hara_data": hara_records,
            "safety_goals": safety_goals,
            "summary": {
                "total_records": len(hara_records),
                "qm_count": qm_count,
                "non_qm_count": len(hara_records) - qm_count,
                "asil_distribution": asil_distribution
            }
        }

    def validate_experience_json(self, json_path: str) -> bool:
        """
        验证生成的JSON文件是否完整

        Args:
            json_path: JSON文件路径

        Returns:
            是否验证通过
        """
        if not os.path.exists(json_path):
            logger.error(f"JSON文件不存在: {json_path}")
            return False

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            required_top_fields = ["version", "source", "subsystem", "timestamp", "record_count", "functions", "hara_data", "summary"]
            for field in required_top_fields:
                if field not in data:
                    logger.error(f"缺少必填字段: {field}")
                    return False

            if not isinstance(data.get("functions"), list):
                logger.error("functions字段应为列表")
                return False

            if not isinstance(data.get("hara_data"), list):
                logger.error("hara_data字段应为列表")
                return False

            expected_record_fields = [
                "HARA-ID", "Function", "Guide-Word", "Malfunction",
                "Severity", "Exposure", "Controllability", "ASIL", "Subsystem"
            ]
            for i, record in enumerate(data.get("hara_data", [])):
                if not isinstance(record, dict):
                    logger.error(f"记录 {i} 格式错误，应为字典")
                    return False
                for field in expected_record_fields:
                    if field not in record:
                        logger.warning(f"记录 {i} 缺少字段: {field}")

            if "summary" in data:
                summary_fields = ["total_records", "qm_count", "non_qm_count", "asil_distribution"]
                for field in summary_fields:
                    if field not in data["summary"]:
                        logger.error(f"summary缺少字段: {field}")
                        return False

            if "template_matched" in data:
                for sheet in ["05_HARA", "06_Safety Goal"]:
                    if sheet in data["template_matched"]:
                        if "headers" not in data["template_matched"][sheet]:
                            logger.warning(f"template_matched.{sheet}缺少headers字段")

            record_count = data.get("record_count", 0)
            actual_hara_count = len(data.get("hara_data", []))
            if record_count != actual_hara_count:
                logger.warning(f"record_count不匹配: 声明{record_count} vs 实际{actual_hara_count}")

            logger.info(f"JSON验证通过: {json_path}")
            return True

        except json.JSONDecodeError as e:
            logger.error(f"JSON格式错误: {e}")
            return False
        except Exception as e:
            logger.error(f"验证JSON时出错: {e}")
            return False

    def _get_timestamp(self) -> str:
        """获取当前时间戳字符串"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def create_library(library_dir: str = None) -> HARAExperienceLibrary:
    """
    创建HARA经验库实例

    Args:
        library_dir: 经验库目录路径

    Returns:
        HARAExperienceLibrary实例
    """
    library = HARAExperienceLibrary(library_dir)
    library.initialize()
    return library


if __name__ == "__main__":
    lib = HARAExperienceLibrary()
    if lib.initialize():
        summary = lib.get_index_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))

#!/usr/bin/env python3
"""
Excel文档处理器
用于读取HARA模板Excel中的关键sheet内容
包括：AI-process步骤、HAZOP引导词、HARA模板、场景库、ASIL表等
"""

import os
import re
import json
import openpyxl
from openpyxl import load_workbook
from typing import Dict, List, Any, Optional, Tuple
import logging

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ExcelProcessor:
    """Excel文档处理器"""
    
    def __init__(self, excel_path: str):
        """
        初始化Excel处理器
        
        Args:
            excel_path: Excel文件路径
        """
        self.excel_path = excel_path
        self.excel_data = None
        self.workbook = None
        
    def load_excel(self) -> bool:
        """
        加载Excel文件
        
        Returns:
            是否加载成功
        """
        try:
            if not os.path.exists(self.excel_path):
                logger.error(f"Excel文件不存在: {self.excel_path}")
                return False
                
            # 使用openpyxl读取Excel
            self.workbook = load_workbook(self.excel_path, data_only=True)
            self.excel_data = {}
            
            for sheet_name in self.workbook.sheetnames:
                ws = self.workbook[sheet_name]
                rows = []
                for row in ws.iter_rows(values_only=True):
                    rows.append(list(row))
                self.excel_data[sheet_name] = rows
            
            logger.info(f"成功加载Excel文件: {self.excel_path}")
            logger.info(f"Sheet数量: {len(self.excel_data)}")
            logger.info(f"Sheet名称: {list(self.excel_data.keys())}")
            
            return True
            
        except Exception as e:
            logger.error(f"加载Excel文件失败: {e}")
            return False
            return False
    
    def get_sheet_names(self) -> List[str]:
        """
        获取所有sheet名称
        
        Returns:
            sheet名称列表
        """
        if self.excel_data is None:
            if not self.load_excel():
                return []
        return list(self.excel_data.keys())
    
    def get_ai_process_steps(self) -> List[Dict[str, Any]]:
        """
        获取AI-process sheet中的17个步骤
        
        Returns:
            步骤列表，每个步骤为字典格式
        """
        if self.excel_data is None:
            if not self.load_excel():
                return []
        
        steps = []
        sheet_name = "AI-process"
        
        if sheet_name not in self.excel_data:
            logger.error(f"Sheet '{sheet_name}' 不存在")
            return steps
        
        df = self.excel_data[sheet_name]
        
        # 查找步骤行，从第3行开始（0-indexed）
        for i in range(len(df)):
            row = df[i]  # Fixed: df is a list, not DataFrame
            if not self._is_na(row[0]) and isinstance(row[0], (int, float)):
                try:
                    step_num = int(row[0])
                    if 1 <= step_num <= 17:
                        step = {
                            "step_number": step_num,
                            "deliverable": self._clean_text(row[1]) if len(row) > 1 else "",
                            "input": self._clean_text(row[2]) if len(row) > 2 else "",
                            "activity": self._clean_text(row[3]) if len(row) > 3 else "",
                            "process_description": self._clean_text(row[4]) if len(row) > 4 else "",
                            "skill": self._clean_text(row[5]) if len(row) > 5 else "",
                            "output": self._clean_text(row[6]) if len(row) > 6 else ""
                        }
                        steps.append(step)
                except (ValueError, TypeError):
                    continue
        
        logger.info(f"从AI-process sheet中提取了 {len(steps)} 个步骤")
        return steps
    
    def get_hazop_guidewords(self) -> List[Dict[str, str]]:
        """
        获取HAZOP引导词
        
        Returns:
            引导词列表，每个引导词包含解释
        """
        if self.excel_data is None:
            if not self.load_excel():
                return []
        
        guidewords = []
        sheet_name = "04_HAZOP"
        
        if sheet_name not in self.excel_data:
            logger.error(f"Sheet '{sheet_name}' 不存在")
            return guidewords
        
        df = self.excel_data[sheet_name]
        
        # 从第2行开始读取（0-indexed）
        for i in range(2, min(20, len(df))):  # 最多读取20行
            row = df[i]  # Fixed: df is a list, not DataFrame
            if len(row) >= 2:
                guideword = self._clean_text(row[0])
                description = self._clean_text(row[1])
                
                if guideword and guideword.strip():
                    guidewords.append({
                        "guideword": guideword,
                        "description": description
                    })
        
        logger.info(f"从HAZOP sheet中提取了 {len(guidewords)} 个引导词")
        return guidewords
    
    def get_hara_template_structure(self) -> Dict[str, Any]:
        """
        获取HARA模板结构
        
        Returns:
            HARA模板结构信息
        """
        if self.excel_data is None:
            if not self.load_excel():
                return {}
        
        structure = {
            "columns": [],
            "header_rows": [],
            "example_rows": [],
            "function_sections": []
        }
        
        sheet_name = "05_HARA"
        
        if sheet_name not in self.excel_data:
            logger.error(f"Sheet '{sheet_name}' 不存在")
            return structure
        
        df = self.excel_data[sheet_name]
        
        # 获取表头行（第3-4行，0-indexed）
        if len(df) >= 4:
            # 第3行（索引2）
            header_row1 = [self._clean_text(cell) for cell in df[2].tolist()]
            # 第4行（索引3）
            header_row2 = [self._clean_text(cell) for cell in df[3].tolist()]
            
            structure["header_rows"] = [header_row1, header_row2]
            
            # 合并两行表头创建列名
            columns = []
            for col_idx in range(len(header_row1)):
                col_name1 = header_row1[col_idx] if col_idx < len(header_row1) else ""
                col_name2 = header_row2[col_idx] if col_idx < len(header_row2) else ""
                
                # 合并两行表头
                if col_name1 and col_name2:
                    column_name = f"{col_name1} - {col_name2}"
                elif col_name1:
                    column_name = col_name1
                elif col_name2:
                    column_name = col_name2
                else:
                    column_name = f"Column_{col_idx+1}"
                
                columns.append({
                    "index": col_idx,
                    "letter": self._col_index_to_letter(col_idx),
                    "name": column_name,
                    "header_row1": col_name1,
                    "header_row2": col_name2
                })
            
            structure["columns"] = columns
        
        # 获取示例行（第5行开始，索引4）
        example_rows = []
        for i in range(4, min(20, len(df))):  # 最多读取前20行作为示例
            row = df[i]
            if len(row) > 0 and not self._is_na(row[0]) and str(row[0]).strip():
                example_row = {
                    "row_index": i,
                    "cells": [self._clean_text(cell) for cell in row.tolist()],
                    "row_type": self._determine_row_type(row)
                }
                example_rows.append(example_row)
        
        structure["example_rows"] = example_rows
        
        # 识别功能部分
        function_sections = []
        current_function = None
        
        for i, row in enumerate(example_rows):
            cells = row["cells"]
            if len(cells) > 0 and cells[0] and "Function" in cells[0]:
                # 新的功能部分
                if current_function:
                    function_sections.append(current_function)
                
                current_function = {
                    "function_name": cells[0],
                    "start_row": row["row_index"],
                    "end_row": None,
                    "rows": [row]
                }
            elif current_function:
                current_function["rows"].append(row)
        
        if current_function:
            if function_sections:
                current_function["start_row"] = function_sections[-1]["end_row"] + 1 if function_sections[-1]["end_row"] else current_function["start_row"]
            current_function["end_row"] = current_function["rows"][-1]["row_index"]
            function_sections.append(current_function)
        
        structure["function_sections"] = function_sections
        
        logger.info(f"从HARA模板中提取了 {len(structure['columns'])} 列, {len(structure['example_rows'])} 行示例, {len(structure['function_sections'])} 个功能部分")
        return structure
    
    def get_scenarios_library(self) -> List[Dict[str, Any]]:
        """
        获取场景库
        从Scenarios_Library sheet的B列(Operating scenarios)获取场景
        
        Returns:
            场景列表
        """
        if self.excel_data is None:
            if not self.load_excel():
                return []
        
        scenarios = []
        sheet_name = "Scenarios_Library"
        
        if sheet_name not in self.excel_data:
            sheet_name = "Scenarios_Libarary"
            if sheet_name not in self.excel_data:
                logger.error(f"Sheet 'Scenarios_Library' 和 'Scenarios_Libarary' 都不存在")
                return scenarios
        
        df = self.excel_data[sheet_name]
        
        header_row_idx = -1
        for i in range(min(10, len(df))):
            row = df[i]
            if len(row) >= 2 and "Operating scenarios" in str(row[1]):
                header_row_idx = i
                break
        
        if header_row_idx == -1:
            for i in range(min(10, len(df))):
                row = df[i]
                if len(row) >= 2 and not self._is_na(row[1]):
                    header_row_idx = i
                    break
        
        if header_row_idx == -1:
            logger.warning("未找到场景库表头，尝试从第0行开始读取")
            header_row_idx = 0
        
        col_b_idx = 1
        
        for i in range(header_row_idx + 1, min(header_row_idx + 100, len(df))):
            row = df[i]
            if len(row) > col_b_idx:
                operating_scenario = self._clean_text(row[col_b_idx])
                
                vehicle_state = self._clean_text(row[2]) if len(row) > 2 else ""
                vehicle_speed = self._clean_text(row[3]) if len(row) > 3 else ""
                weather_conditions = self._clean_text(row[4]) if len(row) > 4 else ""
                road_surface_conditions = self._clean_text(row[5]) if len(row) > 5 else ""
                
                if operating_scenario and operating_scenario.strip():
                    scenario = {
                        "scenario": operating_scenario,
                        "operating_scenario": operating_scenario,
                        "vehicle_state": vehicle_state,
                        "vehicle_speed": vehicle_speed,
                        "weather_conditions": weather_conditions,
                        "road_surface_conditions": road_surface_conditions
                    }
                    scenarios.append(scenario)
        
        logger.info(f"从Scenarios_Library sheet的B列提取了 {len(scenarios)} 个场景")
        return scenarios
    
    def get_asil_table(self) -> Dict[str, Any]:
        """
        获取ASIL表
        
        Returns:
            ASIL表结构
        """
        if self.excel_data is None:
            if not self.load_excel():
                return {}
        
        asil_table = {
            "matrix": {},
            "rules": []
        }
        
        sheet_name = "ASIL_Table"
        
        if sheet_name not in self.excel_data:
            logger.error(f"Sheet '{sheet_name}' 不存在")
            return asil_table
        
        df = self.excel_data[sheet_name]
        
        # 查找ASIL矩阵
        matrix_data = []
        for i in range(min(20, len(df))):
            row = df[i]
            row_values = [self._clean_text(cell) for cell in row.tolist()]
            
            # 检查是否包含ASIL等级
            if any("QM" in str(val) or "A" in str(val) or "B" in str(val) or "C" in str(val) or "D" in str(val) for val in row_values):
                matrix_data.append(row_values)
        
        # 简化处理：返回找到的矩阵数据
        asil_table["matrix_data"] = matrix_data
        
        logger.info(f"从ASIL表中提取了 {len(matrix_data)} 行矩阵数据")
        return asil_table
    
    def get_rating_tables(self) -> Dict[str, Any]:
        """
        获取打分表（严重度、暴露度、可控度）
        
        Returns:
            打分表数据
        """
        if self.excel_data is None:
            if not self.load_excel():
                return {}
        
        rating_tables = {}
        
        # 严重度表
        severity_data = self._extract_rating_table("Severity", "Severity")
        if severity_data:
            rating_tables["severity"] = severity_data
        
        # 暴露度表
        exposure_data = self._extract_rating_table("Exposure", "Exposure")
        if exposure_data:
            rating_tables["exposure"] = exposure_data
        
        # 可控度表
        controllability_data = self._extract_rating_table("Controllability", "Controllability")
        if controllability_data:
            rating_tables["controllability"] = controllability_data
        
        logger.info(f"提取了 {len(rating_tables)} 个打分表")
        return rating_tables
    
    def get_severity_criteria(self) -> Dict[str, Any]:
        """
        获取Severity sheet中C8:F8的内容结构描述
        用于严重度合理性分析
        
        Returns:
            严重度评判标准，包含道路使用者类型和各等级描述
        """
        if self.excel_data is None:
            if not self.load_excel():
                return {}
        
        sheet_name = "Severity"
        if sheet_name not in self.excel_data:
            logger.error(f"Sheet '{sheet_name}' 不存在")
            return {}
        
        df = self.excel_data[sheet_name]
        
        criteria = {
            "road_users": [],
            "severity_levels": {},
            "c8_f8_content": {}
        }
        
        row_8_idx = 7
        if row_8_idx < len(df):
            row = df[row_8_idx]
            c8 = self._clean_text(row[2]) if len(row) > 2 else ""
            d8 = self._clean_text(row[3]) if len(row) > 3 else ""
            e8 = self._clean_text(row[4]) if len(row) > 4 else ""
            f8 = self._clean_text(row[5]) if len(row) > 5 else ""
            
            criteria["c8_f8_content"] = {
                "C": c8,
                "D": d8,
                "E": e8,
                "F": f8
            }
            
            if c8:
                criteria["road_users"].append(c8)
            if d8:
                criteria["road_users"].append(d8)
            if e8:
                criteria["road_users"].append(e8)
            if f8:
                criteria["road_users"].append(f8)
        
        for i in range(8, min(20, len(df))):
            row = df[i]
            if len(row) >= 2:
                level = self._clean_text(row[0])
                if level and re.match(r'S[0-3]', level):
                    description = self._clean_text(row[1]) if len(row) > 1 else ""
                    criteria["severity_levels"][level] = description
        
        logger.info(f"提取了Severity标准:道路使用者={criteria['road_users']},等级={list(criteria['severity_levels'].keys())}")
        return criteria
    
    def get_exposure_criteria(self) -> Dict[str, Any]:
        """
        获取Exposure sheet中的评估标准
        - C8:F8 基于场景发生时长的评估
        - C27:F27 基于场景发生频次的评估
        
        Returns:
            暴露度评判标准
        """
        if self.excel_data is None:
            if not self.load_excel():
                return {}
        
        sheet_name = "Exposure"
        if sheet_name not in self.excel_data:
            logger.error(f"Sheet '{sheet_name}' 不存在")
            return {}
        
        df = self.excel_data[sheet_name]
        
        criteria = {
            "duration_based": {},
            "frequency_based": {},
            "exposure_levels": {}
        }
        
        row_8_idx = 7
        if row_8_idx < len(df):
            row = df[row_8_idx]
            c8 = self._clean_text(row[2]) if len(row) > 2 else ""
            d8 = self._clean_text(row[3]) if len(row) > 3 else ""
            e8 = self._clean_text(row[4]) if len(row) > 4 else ""
            f8 = self._clean_text(row[5]) if len(row) > 5 else ""
            
            criteria["duration_based"] = {
                "C": c8,
                "D": d8, 
                "E": e8,
                "F": f8
            }
        
        row_27_idx = 26
        if row_27_idx < len(df):
            row = df[row_27_idx]
            c27 = self._clean_text(row[2]) if len(row) > 2 else ""
            d27 = self._clean_text(row[3]) if len(row) > 3 else ""
            e27 = self._clean_text(row[4]) if len(row) > 4 else ""
            f27 = self._clean_text(row[5]) if len(row) > 5 else ""
            
            criteria["frequency_based"] = {
                "C": c27,
                "D": d27,
                "E": e27,
                "F": f27
            }
        
        for i in range(8, min(35, len(df))):
            row = df[i]
            if len(row) >= 2:
                level = self._clean_text(row[0])
                if level and re.match(r'E[0-4]', level):
                    description = self._clean_text(row[1]) if len(row) > 1 else ""
                    duration_desc = self._clean_text(row[2]) if len(row) > 2 else ""
                    frequency_desc = self._clean_text(row[3]) if len(row) > 3 else ""
                    criteria["exposure_levels"][level] = {
                        "description": description,
                        "duration": duration_desc,
                        "frequency": frequency_desc
                    }
        
        logger.info(f"提取了Exposure标准:等级={list(criteria['exposure_levels'].keys())}")
        return criteria
    
    def get_vda702_exposure_criteria(self) -> Dict[str, Any]:
        """
        获取VDA702 Full sheet中的暴露度评估标准
        用于更精细的E值打分
        
        Returns:
            VDA702暴露度评判标准，包含时长和频次的具体值
        """
        if self.excel_data is None:
            if not self.load_excel():
                return {}
        
        sheet_name = "VDA702 Full"
        if sheet_name not in self.excel_data:
            logger.warning(f"Sheet '{sheet_name}' 不存在，使用默认标准")
            return self._get_default_exposure_criteria()
        
        df = self.excel_data[sheet_name]
        
        criteria = {
            "duration_rules": [],
            "frequency_rules": [],
            "keywords_mapping": {}
        }
        
        keyword_e_mapping = {
            "高速公路": "E4", "高速": "E4", "highway": "E4",
            "市区": "E3", "城市": "E3", "urban": "E3", "普通道路": "E3",
            "夜间": "E2", "雨天": "E2", "雪天": "E2", "恶劣天气": "E2",
            "山路": "E2", "特殊": "E1", "rare": "E1", "极端": "E1"
        }
        
        for i in range(min(50, len(df))):
            row = df[i]
            row_text = " ".join([str(self._clean_text(cell)) for cell in row if not self._is_na(cell)])
            
            if any(level in row_text for level in ["E4", "E3", "E2", "E1", "E0"]):
                level_match = re.search(r'E[0-4]', row_text)
                if level_match:
                    level = level_match.group()
                    criteria["keywords_mapping"][level] = row_text
        
        logger.info(f"从VDA702 Full提取了 {len(criteria['keywords_mapping'])} 条暴露度规则")
        return criteria
    
    def _get_default_exposure_criteria(self) -> Dict[str, Any]:
        """获取默认暴露度评判标准"""
        return {
            "duration_rules": [
                {"range": ">10%", "level": "E4", "description": "运行时间>10%"},
                {"range": "1-10%", "level": "E3", "description": "运行时间1-10%"},
                {"range": "0.1-1%", "level": "E2", "description": "运行时间0.1-1%"},
                {"range": "<0.1%", "level": "E1", "description": "运行时间<0.1%"},
                {"range": "0%", "level": "E0", "description": "运行时间0%"}
            ],
            "frequency_rules": [
                {"range": ">10次/年", "level": "E4", "description": "每年超过10次"},
                {"range": "1-10次/年", "level": "E3", "description": "每年1-10次"},
                {"range": "<1次/年", "level": "E2", "description": "每年少于1次"},
                {"range": "极罕见", "level": "E1", "description": "极罕见发生"}
            ],
            "keywords_mapping": {}
        }
    
    def _extract_rating_table(self, sheet_name: str, table_type: str) -> Optional[Dict[str, Any]]:
        """
        提取打分表数据
        
        Args:
            sheet_name: sheet名称
            table_type: 表类型
            
        Returns:
            打分表数据
        """
        if sheet_name not in self.excel_data:
            return None
        
        df = self.excel_data[sheet_name]
        table_data = {
            "type": table_type,
            "headers": [],
            "rows": []
        }
        
        # 查找表头
        header_found = False
        for i in range(min(10, len(df))):
            row = df[i]
            row_values = [self._clean_text(cell) for cell in row.tolist()]
            
            # 检查是否包含表头关键词
            if any(table_type.lower() in str(val).lower() for val in row_values):
                table_data["headers"] = row_values
                header_found = True
                start_row = i + 1
                break
        
        if not header_found:
            return None
        
        # 读取数据行
        max_rows = 50
        for i in range(start_row, min(start_row + max_rows, len(df))):
            row = df[i]
            row_values = [self._clean_text(cell) for cell in row.tolist()]
            
            # 检查是否为空行
            if all(self._is_na(cell) or str(cell).strip() == "" for cell in row_values):
                continue
            
            # 检查是否包含评分等级（S0-S3, E0-E4, C0-C3）
            if any(re.search(r'[SEC][0-4]', str(val)) for val in row_values):
                table_data["rows"].append({
                    "row_index": i,
                    "values": row_values
                })
        
        return table_data if table_data["rows"] else None
    
    def _is_na(self, value) -> bool:
        """检查值是否为空（替代 pandas.isna）"""
        return value is None or (isinstance(value, str) and value.strip() == "")
    
    def _clean_text(self, text) -> str:
        """
        清理文本
        
        Args:
            text: 原始文本
            
        Returns:
            清理后的文本
        """
        if self._is_na(text):
            return ""
        
        text_str = str(text)
        
        # 移除多余空格和换行
        text_str = re.sub(r'\s+', ' ', text_str)
        text_str = text_str.strip()
        
        return text_str
    
    def _col_index_to_letter(self, col_idx: int) -> str:
        """
        将列索引转换为字母（A, B, C, ...）
        
        Args:
            col_idx: 列索引（0-based）
            
        Returns:
            列字母
        """
        result = ""
        while col_idx >= 0:
            result = chr(col_idx % 26 + 65) + result
            col_idx = col_idx // 26 - 1
        return result
    
    def _determine_row_type(self, row) -> str:
        """
        确定行类型
        
        Args:
            row: 行数据
            
        Returns:
            行类型
        """
        cells = [self._clean_text(cell) for cell in row.tolist()]
        
        if len(cells) > 0:
            first_cell = cells[0]
            
            if "Function" in first_cell:
                return "function_header"
            elif first_cell and "HARA_" in first_cell:
                return "hazard_row"
            elif len(cells) > 3 and cells[3] and cells[3].strip():  # 引导词列
                return "guideword_row"
            elif all(not cell or cell.strip() == "" for cell in cells):
                return "empty_row"
        
        return "data_row"
    
    def export_to_json(self, output_path: str) -> bool:
        """
        将提取的数据导出为JSON文件
        
        Args:
            output_path: 输出文件路径
            
        Returns:
            是否导出成功
        """
        try:
            data = {
                "excel_file": os.path.basename(self.excel_path),
                "ai_process_steps": self.get_ai_process_steps(),
                "hazop_guidewords": self.get_hazop_guidewords(),
                "hara_template_structure": self.get_hara_template_structure(),
                "scenarios_library": self.get_scenarios_library(),
                "asil_table": self.get_asil_table(),
                "rating_tables": self.get_rating_tables()
            }
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"数据已导出到: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"导出数据失败: {e}")
            return False
    
    def get_all_data(self) -> Dict[str, Any]:
        """
        获取所有提取的数据
        
        Returns:
            所有数据
        """
        return {
            "ai_process_steps": self.get_ai_process_steps(),
            "hazop_guidewords": self.get_hazop_guidewords(),
            "hara_template_structure": self.get_hara_template_structure(),
            "scenarios_library": self.get_scenarios_library(),
            "asil_table": self.get_asil_table(),
            "rating_tables": self.get_rating_tables()
        }
    
    def get_sheet_data(self, sheet_name: str) -> List[List[Any]]:
        """
        获取指定sheet的数据
        
        Args:
            sheet_name: Sheet名称
            
        Returns:
            Sheet数据列表
        """
        if self.excel_data is None:
            if not self.load_excel():
                return []
        
        if sheet_name in self.excel_data:
            return self.excel_data[sheet_name]
        else:
            logger.warning(f"Sheet '{sheet_name}' 不存在")
            return []

    def get_severity_hazard_events(self) -> Dict[int, str]:
        """
        获取Severity sheet的L列Hazard Event数据
        
        Returns:
            行索引到危害事件描述的映射
        """
        if self.excel_data is None:
            if not self.load_excel():
                return {}
        
        sheet_name = "Severity"
        if sheet_name not in self.excel_data:
            logger.warning(f"Sheet '{sheet_name}' 不存在")
            return {}
        
        df = self.excel_data[sheet_name]
        hazard_events = {}
        
        # 查找L列（Hazard Event列）索引
        # L列是第12列，索引为11
        hazard_event_col_idx = 11
        
        # 查找表头行以确定数据起始行
        header_row_idx = -1
        for i in range(min(10, len(df))):
            if len(df[i]) > hazard_event_col_idx:
                header = self._clean_text(df[i][hazard_event_col_idx])
                if header and "hazard" in header.lower() and "event" in header.lower():
                    header_row_idx = i
                    logger.info(f"找到Hazard Event表头在行 {i}: {header}")
                    break
        
        start_row = header_row_idx + 1 if header_row_idx >= 0 else 2
        
        # 提取L列的危害事件
        for i in range(start_row, min(start_row + 100, len(df))):
            if len(df[i]) > hazard_event_col_idx:
                hazard_event = self._clean_text(df[i][hazard_event_col_idx])
                if hazard_event and hazard_event.strip():
                    hazard_events[i] = hazard_event
        
        logger.info(f"从Severity sheet的L列提取了 {len(hazard_events)} 个危害事件")
        return hazard_events


def test_excel_processor():
    """测试Excel处理器"""
    excel_path = "/home/workspace/attachments/HARA_Template_AI_20260327.xlsx"
    
    processor = ExcelProcessor(excel_path)
    
    if processor.load_excel():
        print(f"Sheet数量: {len(processor.get_sheet_names())}")
        print(f"Sheet名称: {processor.get_sheet_names()}")
        
        # 测试各个功能
        steps = processor.get_ai_process_steps()
        print(f"\nAI-process步骤数量: {len(steps)}")
        for step in steps[:3]:  # 显示前3个步骤
            print(f"步骤 {step['step_number']}: {step['activity']}")
        
        guidewords = processor.get_hazop_guidewords()
        print(f"\nHAZOP引导词数量: {len(guidewords)}")
        for gw in guidewords[:5]:  # 显示前5个引导词
            print(f"{gw['guideword']}: {gw['description']}")
        
        scenarios = processor.get_scenarios_library()
        print(f"\n场景数量: {len(scenarios)}")
        for scenario in scenarios[:3]:  # 显示前3个场景
            print(f"场景: {scenario['operating_scenario']}")
        
        # 导出数据
        output_path = "/home/workspace/hara-auto-fill/references/excel_data.json"
        processor.export_to_json(output_path)
        
        return True
    else:
        print("加载Excel文件失败")
        return False


if __name__ == "__main__":
    test_excel_processor()
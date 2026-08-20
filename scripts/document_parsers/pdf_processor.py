#!/usr/bin/env python3
"""
PDF文档处理器
用于读取和解析GB/T 34590.3标准PDF文档
"""

import os
import re
import json
import logging
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class PDFProcessor:
    """PDF文档处理类"""
    
    def __init__(self):
        """初始化PDF处理器"""
        self.supported_formats = ['.pdf']
        self.key_sections = {
            'severity_definition': ['严重度等级', '表1', 'S0', 'S1', 'S2', 'S3'],
            'exposure_definition': ['暴露概率等级', '表2', 'E0', 'E1', 'E2', 'E3', 'E4'],
            'controllability_definition': ['可控性等级', '表3', 'C0', 'C1', 'C2', 'C3'],
            'asil_table': ['表4', 'ASIL 确定', 'S1 E1 C1', 'S3 E4 C3']
        }
        
        self.severity_levels = {
            'S0': {'description': '无伤害', 'reference': 'AIS0及AIS1-6可能性小于10%'},
            'S1': {'description': '轻度和中度伤害', 'reference': 'AIS1-6可能性大于10%(不属于S2和S3)'},
            'S2': {'description': '严重的和危及生命的伤害(有存活的可能)', 'reference': 'AIS3-6可能性大于10%(不属于S3)'},
            'S3': {'description': '危及生命的伤害(存活不确定),致命的伤害', 'reference': 'AIS5-6可能性大于10%'}
        }
        
        self.exposure_levels = {
            'E0': {'description': '不可能'},
            'E1': {'description': '非常低的概率'},
            'E2': {'description': '低概率'},
            'E3': {'description': '中等概率'},
            'E4': {'description': '高概率'}
        }
        
        self.controllability_levels = {
            'C0': {'description': '可控'},
            'C1': {'description': '简单可控'},
            'C2': {'description': '一般可控'},
            'C3': {'description': '难以控制或不可控'}
        }
        
    def extract_gbt_standard(self, file_path: str) -> Dict[str, Any]:
        """
        提取GB/T 34590.3标准内容
        
        Args:
            file_path: PDF文档路径
            
        Returns:
            提取的内容字典
        """
        logger.info(f"开始提取GB/T 34590.3标准文档: {file_path}")
        
        content = self._read_pdf(file_path)
        if not content:
            return {"error": "无法读取PDF内容"}
        
        result = {
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "sections": {},
            "definitions": {},
            "tables": {}
        }
        
        # 提取关键章节
        for section_name, keywords in self.key_sections.items():
            section_content = self._extract_section(content, keywords)
            if section_content:
                result["sections"][section_name] = section_content
        
        # 提取严重度定义
        severity_section = result["sections"].get('severity_definition')
        if severity_section:
            result["definitions"]["severity"] = self._extract_severity_definitions(
                severity_section
            )
        
        # 提取暴露概率定义
        exposure_section = result["sections"].get('exposure_definition')
        if exposure_section:
            result["definitions"]["exposure"] = self._extract_exposure_definitions(
                exposure_section
            )
        
        # 提取可控性定义
        controllability_section = result["sections"].get('controllability_definition')
        if controllability_section:
            result["definitions"]["controllability"] = self._extract_controllability_definitions(
                controllability_section
            )
        
        # 提取ASIL表
        asil_section = result["sections"].get('asil_table')
        if asil_section:
            result["tables"]["asil"] = self._extract_asil_table(asil_section)
        
        # 提取其他重要信息
        result["definitions"]["basic_concepts"] = self._extract_basic_concepts(content)
        result["definitions"]["harm_events"] = self._extract_harm_event_examples(content)
        
        logger.info(f"标准文档提取完成，找到{len(result['definitions'])}类定义")
        return result
    
    def _read_pdf(self, file_path: str) -> Optional[str]:
        """读取PDF文件内容"""
        try:
            # 尝试使用PyPDF2
            import PyPDF2
            
            with open(file_path, 'rb') as f:
                pdf_reader = PyPDF2.PdfReader(f)
                text_content = []
                
                for page_num in range(len(pdf_reader.pages)):
                    page = pdf_reader.pages[page_num]
                    text = page.extract_text()
                    
                    # 清理文本
                    text = self._clean_pdf_text(text)
                    if text.strip():
                        text_content.append(text)
                
                return '\n'.join(text_content) if text_content else None
                
        except ImportError:
            logger.warning("PyPDF2未安装，尝试使用pdfplumber")
            return self._read_pdf_with_pdfplumber(file_path)
        
        except Exception as e:
            logger.error(f"使用PyPDF2读取PDF失败: {e}")
            return self._read_pdf_with_pdfplumber(file_path)
    
    def _read_pdf_with_pdfplumber(self, file_path: str) -> Optional[str]:
        """使用pdfplumber读取PDF"""
        try:
            import pdfplumber
            
            text_content = []
            
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    
                    if text:
                        text = self._clean_pdf_text(text)
                        text_content.append(text)
            
            return '\n'.join(text_content) if text_content else None
            
        except ImportError:
            logger.error("pdfplumber未安装，无法读取PDF文件")
            logger.info("请安装: pip install pdfplumber PyPDF2")
            return None
            
        except Exception as e:
            logger.error(f"使用pdfplumber读取PDF失败: {e}")
            return None
    
    def _clean_pdf_text(self, text: str) -> str:
        """清理PDF提取的文本"""
        # 合并换行导致的断词
        text = re.sub(r'(\w)-\s*\n\s*(\w)', r'\1\2', text)
        
        # 移除多余的空白字符
        text = re.sub(r'\s+', ' ', text)
        
        # 修复常见字符问题
        replacements = {
            r'ﬁ': 'fi',
            r'ﬂ': 'fl',
            r'ﬀ': 'ff',
            r'ﬃ': 'ffi',
            r'ﬄ': 'ffl',
            r'…': '...',
            r'–': '-',
            r'—': '-',
            r'“': '"',
            r'”': '"',
            r'‘': "'",
            r'’': "'"
        }
        
        for old, new in replacements.items():
            text = text.replace(old, new)
        
        return text.strip()
    
    def _extract_section(self, content: str, keywords: List[str]) -> Optional[str]:
        """提取包含关键词的章节"""
        lines = content.split('\n')
        section_lines = []
        in_section = False
        
        for line in lines:
            line_lower = line.lower()
            
            # 检查是否包含关键词
            contains_keyword = any(
                keyword.lower() in line_lower for keyword in keywords
            )
            
            if contains_keyword:
                in_section = True
            
            if in_section:
                section_lines.append(line)
                
                # 检查是否应该结束章节
                if len(section_lines) > 10:
                    # 检查是否已经过了关键词区域
                    if not self._is_relevant_line(line, keywords):
                        # 检查接下来几行是否也不相关
                        next_lines = lines[lines.index(line) + 1:lines.index(line) + 5]
                        if not any(self._is_relevant_line(l, keywords) for l in next_lines):
                            break
        
        if section_lines:
            return '\n'.join(section_lines)
        return None
    
    def _is_relevant_line(self, line: str, keywords: List[str]) -> bool:
        """检查行是否相关"""
        line_lower = line.lower()
        return any(keyword.lower() in line_lower for keyword in keywords)
    
    def _extract_severity_definitions(self, content: str) -> Dict[str, Dict]:
        """提取严重度定义"""
        result = {}
        
        # 查找表格模式
        lines = content.split('\n')
        table_started = False
        
        for i, line in enumerate(lines):
            line = line.strip()
            
            # 查找表头
            if '严重度等级' in line or 'S0' in line and 'S1' in line:
                table_started = True
                continue
            
            if table_started:
                # 尝试匹配表格行
                # 模式: 等级 描述 参考
                severity_match = re.search(
                    r'(S[0-3])\s+(.+?)\s+(.+?)(?=\s+S[0-3]|$)',
                    line
                )
                
                if severity_match:
                    level = severity_match.group(1)
                    description = severity_match.group(2).strip()
                    reference = severity_match.group(3).strip()
                    
                    result[level] = {
                        "description": description,
                        "reference": reference,
                        "examples": []
                    }
                else:
                    # 如果没有匹配到，尝试其他模式
                    for level in ['S0', 'S1', 'S2', 'S3']:
                        if level in line:
                            # 提取描述
                            desc_match = re.search(
                                rf'{level}\s+(.+?)(?:\s+(?:AIS|示例)|$)',
                                line
                            )
                            if desc_match:
                                description = desc_match.group(1).strip()
                                result[level] = {
                                    "description": description,
                                    "reference": "",
                                    "examples": []
                                }
        
        # 如果没有提取到，使用默认定义
        if not result:
            result = self.severity_levels.copy()
        
        # 补充示例
        if 'S0' in result:
            result['S0']['examples'].extend([
                '冲撞路边设施',
                '轻微刮痕损害'
            ])
        
        if 'S1' in result:
            result['S1']['examples'].extend([
                '低速碰撞静止物体',
                '低速追尾'
            ])
        
        if 'S2' in result:
            result['S2']['examples'].extend([
                '中速碰撞静止物体',
                '中速追尾'
            ])
        
        if 'S3' in result:
            result['S3']['examples'].extend([
                '高速碰撞静止物体',
                '高速追尾'
            ])
        
        return result
    
    def _extract_exposure_definitions(self, content: str) -> Dict[str, Dict]:
        """提取暴露概率定义"""
        result = {}
        
        lines = content.split('\n')
        table_started = False
        
        for line in lines:
            line = line.strip()
            
            if '暴露概率等级' in line or 'E0' in line:
                table_started = True
                continue
            
            if table_started:
                # 匹配暴露等级
                exposure_match = re.search(
                    r'(E[0-4])\s+(.+?)(?:\s+(?:持续时间|发生频率)|$)',
                    line
                )
                
                if exposure_match:
                    level = exposure_match.group(1)
                    description = exposure_match.group(2).strip()
                    
                    result[level] = {
                        "description": description,
                        "duration_ranges": {},
                        "frequency_ranges": {},
                        "examples": []
                    }
        
        # 如果没有提取到，使用默认定义
        if not result:
            result = self.exposure_levels.copy()
            for level in result:
                result[level] = {"description": result[level]['description']}
        
        # 补充信息
        if 'E0' in result:
            result['E0']['description'] = '不可能'
            result['E0']['examples'] = ['极其不寻常或不可能同时发生的情况', '自然灾害']
        
        if 'E1' in result:
            result['E1']['description'] = '非常低的概率'
            result['E1']['duration_ranges'] = {'percentage': '<1%的平均运行时间'}
        
        if 'E2' in result:
            result['E2']['description'] = '低概率'
            result['E2']['duration_ranges'] = {'percentage': '<1%的平均运行时间'}
        
        if 'E3' in result:
            result['E3']['description'] = '中等概率'
            result['E3']['duration_ranges'] = {'percentage': '1%-10%的平均运行时间'}
        
        if 'E4' in result:
            result['E4']['description'] = '高概率'
            result['E4']['duration_ranges'] = {'percentage': '>10%的平均运行时间'}
        
        return result
    
    def _extract_controllability_definitions(self, content: str) -> Dict[str, Dict]:
        """提取可控性定义"""
        result = {}
        
        lines = content.split('\n')
        table_started = False
        
        for line in lines:
            line = line.strip()
            
            if '可控性等级' in line or 'C0' in line:
                table_started = True
                continue
            
            if table_started:
                # 匹配可控等级
                control_match = re.search(
                    r'(C[0-3])\s+(.+?)(?:\s+(?:驾驶员因素|场景示例)|$)',
                    line
                )
                
                if control_match:
                    level = control_match.group(1)
                    description = control_match.group(2).strip()
                    
                    result[level] = {
                        "description": description,
                        "driver_factor": "",
                        "scenario_examples": [],
                        "control_probability": ""
                    }
        
        # 如果没有提取到，使用默认定义
        if not result:
            result = self.controllability_levels.copy()
            for level in result:
                result[level] = {"description": result[level]['description']}
        
        # 补充信息
        if 'C0' in result:
            result['C0']['driver_factor'] = '常规可控'
            result['C0']['control_probability'] = '99%以上'
        
        if 'C1' in result:
            result['C1']['driver_factor'] = '简单可控'
            result['C1']['control_probability'] = '90%-99%'
        
        if 'C2' in result:
            result['C2']['driver_factor'] = '一般可控'
            result['C2']['control_probability'] = '85%-90%'
        
        if 'C3' in result:
            result['C3']['driver_factor'] = '难以控制或不可控'
            result['C3']['control_probability'] = '<90%'
        
        return result
    
    def _extract_asil_table(self, content: str) -> Dict[str, Any]:
        """提取ASIL表"""
        result = {
            "description": "ASIL等级确定表",
            "table_data": {},
            "notes": []
        }
        
        # 尝试提取表格数据
        lines = content.split('\n')
        collecting_data = False
        
        for line in lines:
            line = line.strip()
            
            if 'ASIL' in line and ('确定' in line or 'determination' in line):
                collecting_data = True
                continue
            
            if collecting_data:
                # 尝试匹配ASIL表格行
                # 格式: S1 E1 C1 QM 等
                asil_match = re.search(
                    r'(S[0-3])\s+(E[0-4])\s+(C[0-3])\s+(QM|[A-D])',
                    line
                )
                
                if asil_match:
                    s_level = asil_match.group(1)
                    e_level = asil_match.group(2)
                    c_level = asil_match.group(3)
                    asil_result = asil_match.group(4)
                    
                    key = f"{s_level}_{e_level}_{c_level}"
                    result["table_data"][key] = asil_result
        
        # 如果没提取到，使用标准ASIL表
        if not result["table_data"]:
            # 基于GB/T 34590.3表4的简化版本
            asil_table = {
                # S1系列
                "S1_E1_C1": "QM", "S1_E1_C2": "QM", "S1_E1_C3": "QM",
                "S1_E2_C1": "QM", "S1_E2_C2": "QM", "S1_E2_C3": "QM",
                "S1_E3_C1": "QM", "S1_E3_C2": "QM", "S1_E3_C3": "A",
                "S1_E4_C1": "QM", "S1_E4_C2": "A", "S1_E4_C3": "B",
                
                # S2系列
                "S2_E1_C1": "QM", "S2_E1_C2": "QM", "S2_E1_C3": "QM",
                "S2_E2_C1": "QM", "S2_E2_C2": "QM", "S2_E2_C3": "A",
                "S2_E3_C1": "QM", "S2_E3_C2": "A", "S2_E3_C3": "B",
                "S2_E4_C1": "A", "S2_E4_C2": "B", "S2_E4_C3": "C",
                
                # S3系列
                "S3_E1_C1": "QM", "S3_E1_C2": "QM", "S3_E1_C3": "A",
                "S3_E2_C1": "QM", "S3_E2_C2": "A", "S3_E2_C3": "B",
                "S3_E3_C1": "A", "S3_E3_C2": "B", "S3_E3_C3": "C",
                "S3_E4_C1": "B", "S3_E4_C2": "C", "S3_E4_C3": "D"
            }
            
            result["table_data"] = asil_table
        
        # 添加说明
        result["notes"].extend([
            "QM: 质量管理(质量管理流程足以管理已识别的风险)",
            "ASIL A: 最低安全完整性等级",
            "ASIL B: 较低安全完整性等级",
            "ASIL C: 较高安全完整性等级",
            "ASIL D: 最高安全完整性等级"
        ])
        
        return result
    
    def _extract_basic_concepts(self, content: str) -> Dict[str, str]:
        """提取基本概念"""
        concepts = {}
        
        patterns = {
            '相关项': r'相关项\s*[：:]\s*(.+?)(?=\n|$)',
            '危害事件': r'危害事件\s*[：:]\s*(.+?)(?=\n|$)',
            '安全目标': r'安全目标\s*[：:]\s*(.+?)(?=\n|$)',
            'ASIL': r'ASIL\s*[：:]\s*(.+?)(?=\n|$)'
        }
        
        for concept_name, pattern in patterns.items():
            match = re.search(pattern, content)
            if match:
                concepts[concept_name] = match.group(1).strip()
        
        return concepts
    
    def _extract_harm_event_examples(self, content: str) -> List[Dict[str, str]]:
        """提取危害事件示例"""
        examples = []
        
        # 查找示例部分
        lines = content.split('\n')
        in_examples_section = False
        
        for line in lines:
            line = line.strip()
            
            if '示例' in line or '例子' in line or 'example' in line.lower():
                in_examples_section = True
            
            if in_examples_section:
                # 匹配危害事件描述
                if ':' in line or '：' in line:
                    parts = re.split(r'[:：]', line, 1)
                    if len(parts) == 2:
                        description = parts[1].strip()
                        if description and len(description) > 10:
                            examples.append({
                                "id": f"EXAMPLE_{len(examples) + 1:03d}",
                                "description": description,
                                "source_line": line
                            })
        
        return examples
    
    def save_extraction_results(self, data: Dict, output_path: str) -> bool:
        """保存提取结果"""
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"提取结果已保存至: {output_path}")
            return True
        except Exception as e:
            logger.error(f"保存结果失败: {e}")
            return False


def test_extraction():
    """测试提取功能"""
    import sys
    
    if len(sys.argv) < 2:
        print("使用方法: python pdf_processor.py <PDF文件路径> [输出路径]")
        sys.exit(1)
    
    file_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "pdf_extraction_results.json"
    
    processor = PDFProcessor()
    result = processor.extract_gbt_standard(file_path)
    
    if "error" in result:
        print(f"提取失败: {result['error']}")
        sys.exit(1)
    
    # 保存结果
    processor.save_extraction_results(result, output_path)
    
    # 打印摘要
    print(f"PDF提取完成: {result['file_name']}")
    print(f"找到章节数量: {len(result['sections'])}")
    print(f"找到定义数量: {len(result['definitions'])}")
    
    if 'severity' in result['definitions']:
        print(f"严重度等级: {list(result['definitions']['severity'].keys())}")
    
    print(f"结果已保存至: {output_path}")


if __name__ == "__main__":
    test_extraction()
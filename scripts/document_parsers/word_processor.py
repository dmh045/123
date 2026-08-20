#!/usr/bin/env python3
"""
Word文档处理器
用于读取和解析Item Definition文档
"""

import os
import re
import json
import logging
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class WordProcessor:
    """Word文档处理类"""
    
    def __init__(self, file_path: Optional[str] = None):
        """
        初始化Word处理器
        
        Args:
            file_path: 文件路径（可选）
        """
        self.file_path = file_path
        self.supported_formats = ['.docx', '.doc', '.txt', '.md']
        self.section_patterns = {
            'function_description': [
                r'相关项功能描述',
                r'功能描述',
                r'第[2-3]章.*功能',
                r'Function.*Description'
            ],
            'output_description': [
                r'输出描述',
                r'功能输出',
                r'Output.*Description',
                r'第[3]章.*输出'
            ],
            'hazard_event': [
                r'危害事件',
                r'Hazard.*Event',
                r'第3\.9\.2章',
                r'潜在危害'
            ],
            'item_definition': [
                r'相关项定义',
                r'Item.*Definition',
                r'第[2-3]章'
            ]
        }
        
    def extract_item_definition(self, file_path: str) -> Dict[str, Any]:
        """
        提取Item Definition文档内容
        
        Args:
            file_path: Word文档路径
            
        Returns:
            提取的内容字典
        """
        logger.info(f"开始提取Item Definition文档: {file_path}")
        
        content = self._read_file(file_path)
        if not content:
            return {"error": "无法读取文档内容"}
        
        result = {
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "sections": {},
            "functions": [],
            "outputs": [],
            "hazard_events": []
        }
        
        # 提取章节内容
        for section_name, patterns in self.section_patterns.items():
            section_content = self._extract_section(content, patterns)
            if section_content:
                result["sections"][section_name] = section_content
        
        # 提取功能描述（优先从表格提取）
        tables = self.extract_tables_raw(file_path)
        item_semantics = self._extract_item_semantics(tables)
        result["item_semantics"] = item_semantics
        func_list = self._extract_functions_from_tables(tables, item_semantics)
        if not func_list:
            for section_name in ['function_description', 'item_definition']:
                if section_name in result["sections"]:
                    func_list = self._extract_functions(result["sections"][section_name])
                    if func_list:
                        break
        if func_list:
            result["functions"] = func_list
        
        # 提取输出描述
        if 'output_description' in result["sections"]:
            result["outputs"] = self._extract_outputs(
                result["sections"]['output_description']
            )
        
        # 提取危害事件
        if 'hazard_event' in result["sections"]:
            result["hazard_events"] = self._extract_hazard_events(
                result["sections"]['hazard_event']
            )
        
        # 关联功能与输出
        result["function_output_mapping"] = self._map_functions_to_outputs(
            result["functions"], result["outputs"]
        )
        
        logger.info(f"文档提取完成，找到{len(result['functions'])}个功能")
        return result
    
    def extract_functions(self) -> List[str]:
        """
        提取功能描述列表
        
        Returns:
            功能描述字符串列表
        """
        if not self.file_path:
            logger.error("未指定文件路径")
            return []
        
        data = self.extract_item_definition(self.file_path)
        functions = []
        
        for func in data.get("functions", []):
            if "description" in func:
                functions.append(func["description"])
        
        return functions
    
    def extract_outputs(self) -> List[str]:
        """
        提取输出描述列表

        Returns:
            输出描述字符串列表
        """
        if not self.file_path:
            logger.error("未指定文件路径")
            return []

        data = self.extract_item_definition(self.file_path)
        outputs = []

        for output in data.get("outputs", []):
            if "description" in output:
                outputs.append(output["description"])

        return outputs

    def extract_tables_raw(self, file_path: str) -> List[List[List[str]]]:
        """提取所有表格数据，行=List[cell], 表=List[行]"""
        import zipfile
        import xml.etree.ElementTree as ET

        W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

        def get_paragraph_text(p_elem):
            texts = []
            for t in p_elem.iter(f'{{{W}}}t'):
                if t.text:
                    texts.append(t.text)
            return ''.join(texts)

        tables = []
        try:
            with zipfile.ZipFile(file_path, 'r') as z:
                if 'word/document.xml' not in z.namelist():
                    return tables
                with z.open('word/document.xml') as f:
                    tree = ET.parse(f)
            root = tree.getroot()
            body = root.find(f'.//{{{W}}}body')
            if body is not None:
                for elem in body:
                    tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                    if tag == 'tbl':
                        table_rows = []
                        for tr in elem.iter(f'{{{W}}}tr'):
                            cells = []
                            for tc in tr.iter(f'{{{W}}}tc'):
                                cell_texts = []
                                for p in tc.iter(f'{{{W}}}p'):
                                    pt = get_paragraph_text(p)
                                    if pt:
                                        cell_texts.append(pt)
                                cells.append(' '.join(cell_texts))
                            table_rows.append(cells)
                        tables.append(table_rows)
        except Exception:
            pass
        return tables

    @staticmethod
    def _derive_function_output(function_name: str, description: str) -> str:
        """Derive the observable output without copying the requirement sentence."""
        name = str(function_name or '').strip()
        desc = str(description or '').strip()

        explicit_outputs = {
            '开启功能': '功能开启请求',
            '激活功能': '功能激活状态',
            '退出功能': '功能退出状态',
            '关闭功能': '功能关闭请求',
            '输出驻车制动': '车辆刹停请求及EPB拉起请求',
            '报警提示': '仪表或中控图像/文字提示',
        }
        if name in explicit_outputs:
            return explicit_outputs[name]

        send_match = re.search(r'(?:发给|发送)([^，。；;]+)', desc)
        if send_match:
            output = send_match.group(1).strip()
            output = re.sub(r'^[A-Z][A-Z0-9_-]*', '', output).strip()
            output = re.sub(r'(?:，|,)?判断后.*$', '', output).strip()
            if output and output != desc:
                return output

        if name.startswith('输出') and len(name) > 2:
            return name[2:]
        return f'{name}执行结果' if name else ''

    @staticmethod
    def _classify_function_semantics(function_name: str, description: str) -> Dict[str, Any]:
        """Split a function sentence into auditable semantic fields."""
        name = str(function_name or '').strip()
        desc = str(description or '').strip()
        preconditions: List[str] = []
        triggers: List[str] = []
        expected_effects: List[str] = []

        condition = re.match(r'^当(.+?)时[，,]', desc)
        if condition:
            preconditions.append(condition.group(1).strip())
            triggers.append('所述条件成立')
        if '开启功能后' in desc:
            preconditions.append('功能已开启')
            triggers.append('系统激活判定通过')
        if '通过功能开关' in desc:
            action = '开启' if '开启' in desc else '关闭' if '关闭' in desc else '操作'
            triggers.append(f'驾驶员通过功能开关执行{action}')

        signal_match = re.search(r'(?:发给|发送)([^，。；;]+)', desc)
        if signal_match and not triggers:
            triggers.append(signal_match.group(1).strip())
        if '判断后' in desc:
            preconditions.append('控制逻辑判断通过')

        effect_match = re.search(r'判断后进行([^，。；;]+)', desc)
        if effect_match:
            expected_effects.append(f'车辆{effect_match.group(1).strip()}')
        elif name == '开启功能':
            expected_effects.append('功能进入开启状态')
        elif name == '激活功能':
            expected_effects.append('功能进入激活状态')
        elif name == '退出功能':
            expected_effects.append('功能退出自动控制')
        elif name == '关闭功能':
            expected_effects.append('功能进入关闭状态')
        elif name == '报警提示':
            expected_effects.append('向用户显示当前状态或报警信息')

        return {
            'preconditions': list(dict.fromkeys(preconditions)),
            'triggers': list(dict.fromkeys(triggers)),
            'expected_effects': list(dict.fromkeys(expected_effects)),
            # Harm is not inferred here. Missing source evidence stays explicit.
            'consequences': [],
            'consequence_status': 'not_explicit_in_function_source',
        }

    @staticmethod
    def _extract_item_semantics(tables: List[List[List[str]]]) -> Dict[str, Any]:
        """Extract item-scoped ODD and fallback evidence from their source tables."""
        odd_constraints: List[Dict[str, Any]] = []
        fallback_behaviors: List[Dict[str, Any]] = []
        state_transitions: List[Dict[str, Any]] = []
        quantitative_constraints: List[Dict[str, Any]] = []
        odd_keywords = {
            '停车场', '上下坡', '停车位类型', '停车位样式', '标线车位类型',
            '空间车位类型', '标线车位颜色', '天气', '雨天', '雾霾', '光照度',
        }

        for table_index, table in enumerate(tables):
            if not table:
                continue
            header = [re.sub(r'\s+', '', str(c or '')) for c in table[0]]
            if '项目' in header and '描述' in header:
                key_col, value_col = header.index('项目'), header.index('描述')
                for row_index, row in enumerate(table[1:], start=2):
                    key = str(row[key_col] or '').strip() if key_col < len(row) else ''
                    value = str(row[value_col] or '').strip() if value_col < len(row) else ''
                    if key in odd_keywords and value:
                        odd_constraints.append({
                            'id': f'ODD_{len(odd_constraints) + 1:03d}',
                            'parameter': key,
                            'value': value,
                            'source_table': table_index,
                            'source_row': row_index,
                        })

            if '故障类型' in header and '处理策略' in header:
                fault_col, strategy_col = header.index('故障类型'), header.index('处理策略')
                desc_col = header.index('故障描述') if '故障描述' in header else None
                seen = set()
                for row_index, row in enumerate(table[1:], start=2):
                    strategy = str(row[strategy_col] or '').strip() if strategy_col < len(row) else ''
                    if not strategy or strategy in seen:
                        continue
                    seen.add(strategy)
                    fallback_behaviors.append({
                        'id': f'FB_{len(fallback_behaviors) + 1:03d}',
                        'fault_type': str(row[fault_col] or '').strip() if fault_col < len(row) else '',
                        'fault_description': (
                            str(row[desc_col] or '').strip()
                            if desc_col is not None and desc_col < len(row) else ''
                        ),
                        'behavior': strategy,
                        'source_table': table_index,
                        'source_row': row_index,
                    })

            if '跳转状态' in header and '转换条件' in header:
                state_col, condition_col = header.index('跳转状态'), header.index('转换条件')
                for row_index, row in enumerate(table[1:], start=2):
                    state = str(row[state_col] or '').strip() if state_col < len(row) else ''
                    condition = str(row[condition_col] or '').strip() if condition_col < len(row) else ''
                    if state and condition:
                        state_transitions.append({
                            'id': f'TR_{len(state_transitions) + 1:03d}',
                            'transition': state,
                            'condition': condition,
                            'source_table': table_index,
                            'source_row': row_index,
                        })

            # Preserve numeric limits separately from narrative ODD fields.
            # Blank category cells inherit the previous category in the table.
            compact_header = [re.sub(r'\s+', '', str(c or '')) for c in table[0]]
            category_col = parameter_col = value_col = None
            if all(key in compact_header for key in ('规格项', '规格描述', '规格定义')):
                category_col = compact_header.index('规格项')
                parameter_col = compact_header.index('规格描述')
                value_col = compact_header.index('规格定义')
            elif all(key in compact_header for key in ('序号', '规格项', '规格定义')):
                parameter_col = compact_header.index('规格项')
                value_col = compact_header.index('规格定义')
                category_col = parameter_col
            elif compact_header[:2] == ['项目', '描述']:
                category_col, parameter_col, value_col = 0, 0, 1

            if value_col is not None:
                current_category = ''
                for row_index, row in enumerate(table[1:], start=2):
                    raw_category = (
                        str(row[category_col] or '').strip()
                        if category_col is not None and category_col < len(row) else ''
                    )
                    if raw_category:
                        current_category = raw_category
                    parameter = (
                        str(row[parameter_col] or '').strip()
                        if parameter_col is not None and parameter_col < len(row) else ''
                    ) or current_category
                    value = str(row[value_col] or '').strip() if value_col < len(row) else ''
                    if not value or not re.search(r'\d|[≤≥＜＞<>±%°]', value):
                        continue
                    quantitative_constraints.append({
                        'id': f'QC_{len(quantitative_constraints) + 1:03d}',
                        'category': current_category,
                        'parameter': parameter,
                        'value': value,
                        'source_table': table_index,
                        'source_row': row_index,
                    })

        return {
            'odd_constraints': odd_constraints,
            'fallback_behaviors': fallback_behaviors,
            'state_transitions': state_transitions,
            'quantitative_constraints': quantitative_constraints,
        }

    def _extract_functions_from_tables(
        self,
        tables: List[List[List[str]]],
        item_semantics: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """从结构化功能表中提取功能，按表头语义定位列。"""
        functions = []
        name_aliases = {
            '功能', '功能名称', '主要功能', '整车功能', '系统功能',
            'function', 'function name', 'vehicle function', 'system function',
        }
        description_aliases = {
            '描述', '功能描述', '功能说明', '输出', '功能输出',
            'description', 'function description', 'output', 'output description',
        }
        id_aliases = {'序号', '编号', 'id', 'no', 'no.'}

        def normalize_header(value: str) -> str:
            return re.sub(r'\s+', ' ', str(value or '').strip().lower())

        for table_index, table in enumerate(tables):
            if not table or len(table) < 2:
                continue
            header = [normalize_header(c) for c in table[0]]
            name_col = next((i for i, h in enumerate(header) if h in name_aliases), None)
            desc_col = next((i for i, h in enumerate(header) if h in description_aliases), None)
            id_col = next((i for i, h in enumerate(header) if h in id_aliases), None)
            if name_col is None or desc_col is None:
                continue

            header_str = '|'.join(header)
            is_non_function_table = any(kw in header_str for kw in [
                'operating scenario', 'operating mode', 'scope of application',
                'foreseeable misuse', 'change history', 'reference',
                'abbreviation', 'applicable', 'cyber', 'cs no', 'no.csa',
                'nooperating', 'no.operating', '外部接口', '内部接口',
                '规格项', '转换条件', '故障类型',
            ])
            if is_non_function_table:
                continue

            for row_index, row in enumerate(table[1:], start=2):
                if name_col >= len(row):
                    continue
                func_name = str(row[name_col] or '').strip()
                func_desc = str(row[desc_col] or '').strip() if desc_col < len(row) else ''
                if not func_name or len(func_name) < 2:
                    continue
                skip_keywords = ['no.', 'no', 'no,', 'n/a', 'item', 'null', '/', '-']
                if func_name.lower().strip(' .:') in skip_keywords:
                    continue
                if func_name.startswith('Table') or func_name.startswith('图'):
                    continue
                if not func_desc or len(func_desc) < 3:
                    continue

                seen_names = {f['name'] for f in functions}
                if func_name in seen_names:
                    continue

                semantics = self._classify_function_semantics(func_name, func_desc)
                output = self._derive_function_output(func_name, func_desc)
                shared_semantics = item_semantics or {}
                transition_tokens = {
                    '开启功能': ('从OFF切换到Standby',),
                    '激活功能': ('从Standby 切换到Active', '从Standby切换到Active'),
                    '退出功能': ('切换到Finish', '切换到Abort', '切换到Error'),
                    '关闭功能': ('切换到OFF',),
                }.get(func_name, ())
                transition_evidence = [
                    item for item in shared_semantics.get('state_transitions', [])
                    if any(token in item.get('transition', '') for token in transition_tokens)
                ]
                if transition_evidence:
                    if not semantics['preconditions']:
                        semantics['preconditions'] = [item['condition'] for item in transition_evidence]
                    if not semantics['triggers']:
                        semantics['triggers'] = [item['transition'] for item in transition_evidence]
                functions.append({
                    'id': 'FUN_%03d' % (len(functions) + 1),
                    'name': func_name,
                    'description': func_desc,
                    'output': output,
                    'preconditions': semantics['preconditions'],
                    'triggers': semantics['triggers'],
                    'expected_effects': semantics['expected_effects'],
                    'consequences': semantics['consequences'],
                    'consequence_status': semantics['consequence_status'],
                    'odd_constraints': shared_semantics.get('odd_constraints', []),
                    'fallback_behaviors': shared_semantics.get('fallback_behaviors', []),
                    'transition_evidence': transition_evidence,
                    'source': 'table',
                    'source_table': table_index,
                    'source_row': row_index,
                    'source_id': (
                        str(row[id_col] or '').strip()
                        if id_col is not None and id_col < len(row) else ''
                    ),
                    'confidence': 1.0,
                })

        return functions

    def _read_file(self, file_path: str) -> Optional[str]:
        """读取文件内容"""
        try:
            file_ext = Path(file_path).suffix.lower()
            
            if file_ext == '.txt':
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            
            elif file_ext == '.md':
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            
            elif file_ext == '.docx':
                # 尝试使用python-docx读取
                try:
                    import docx
                    doc = docx.Document(file_path)
                    return '\n'.join([para.text for para in doc.paragraphs])
                except ImportError:
                    logger.warning("python-docx未安装，尝试其他方法读取.docx文件")
                    # 尝试使用zip解压
                    return self._read_docx_as_zip(file_path)
            
            elif file_ext == '.doc':
                logger.warning(".doc格式需要特殊处理，建议转换为.docx格式")
                return self._read_doc_file(file_path)
            
            else:
                logger.error(f"不支持的文件格式: {file_ext}")
                return None
                
        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            return None
    
    def _read_docx_as_zip(self, file_path: str) -> Optional[str]:
        """将.docx文件作为zip文件读取"""
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            
            text_content = []
            
            with zipfile.ZipFile(file_path, 'r') as z:
                # 读取document.xml
                if 'word/document.xml' in z.namelist():
                    with z.open('word/document.xml') as f:
                        xml_content = f.read().decode('utf-8')
                        
                        # 简单提取文本
                        # 移除XML标签
                        text = re.sub(r'<[^>]+>', ' ', xml_content)
                        text = re.sub(r'\s+', ' ', text).strip()
                        text_content.append(text)
                
                # 也可以读取其他部分
                for name in z.namelist():
                    if name.startswith('word/') and name.endswith('.xml'):
                        try:
                            with z.open(name) as f:
                                xml_content = f.read().decode('utf-8')
                                text = re.sub(r'<[^>]+>', ' ', xml_content)
                                text = re.sub(r'\s+', ' ', text).strip()
                                if text:
                                    text_content.append(text)
                        except:
                            continue
            
            return '\n'.join(text_content) if text_content else None
            
        except Exception as e:
            logger.error(f"读取.docx文件失败: {e}")
            return None
    
    def _read_doc_file(self, file_path: str) -> Optional[str]:
        """读取.doc文件（简化版本）"""
        try:
            # 尝试使用antiword或其他工具
            import subprocess
            result = subprocess.run(
                ['antiword', file_path],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            if result.returncode == 0:
                return result.stdout
            else:
                logger.warning("antiword不可用，尝试其他方法")
                return self._read_binary_as_text(file_path)
        except Exception as e:
            logger.error(f"读取.doc文件失败: {e}")
            return self._read_binary_as_text(file_path)
    
    def _read_binary_as_text(self, file_path: str) -> Optional[str]:
        """以二进制方式读取文件并提取文本"""
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
                # 尝试解码为UTF-8
                try:
                    return content.decode('utf-8', errors='ignore')
                except:
                    # 尝试其他编码
                    return content.decode('latin-1', errors='ignore')
        except Exception as e:
            logger.error(f"二进制读取失败: {e}")
            return None
    
    def _extract_section(self, content: str, patterns: List[str]) -> Optional[str]:
        """提取特定章节内容"""
        for pattern in patterns:
            # 查找章节标题
            section_match = re.search(
                rf'(?:^|\n)\s*(?:#+\s*)?{pattern}.*?(?:\n|$)',
                content,
                re.IGNORECASE | re.MULTILINE
            )
            
            if section_match:
                section_start = section_match.end()
                
                # 查找下一个章节标题或文档结束
                next_section_match = re.search(
                    r'(?:^|\n)\s*(?:#+\s*)?(?:第\d+章|Chapter|\d+\.|References|附录)',
                    content[section_start:],
                    re.MULTILINE
                )
                
                if next_section_match:
                    section_end = section_start + next_section_match.start()
                    return content[section_start:section_end].strip()
                else:
                    return content[section_start:].strip()
        
        return None
    
    def _extract_functions(self, content: str) -> List[Dict[str, str]]:
        """提取功能描述"""
        functions = []
        
        # 多种匹配模式
        patterns = [
            # 编号功能：1. 功能名称：功能描述
            r'(?:\d+[\.\)]|\*|\-)\s*([^：:\n]+)[：:]\s*(.+?)(?=\n\s*(?:\d+[\.\)]|\*|\-|$))',
            # Function: 描述
            r'[Ff]unction\s*[：:]\s*(.+)',
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, content, re.DOTALL)
            for match in matches:
                if len(match.groups()) >= 2:
                    name = match.group(1).strip()
                    description = match.group(2).strip()
                else:
                    name = f"Function_{len(functions) + 1}"
                    description = match.group(1).strip()
                
                functions.append({
                    "id": f"FUN_{len(functions) + 1:03d}",
                    "name": name,
                    "description": description,
                    "source_pattern": pattern
                })
        
        # 禁止将普通长段落回退为Function。无可信结构时由上层阻断。
        
        return functions
    
    def _extract_outputs(self, content: str) -> List[Dict[str, str]]:
        """提取输出描述"""
        outputs = []
        
        patterns = [
            # 输出：描述
            r'[Oo]utput\s*[：:]\s*(.+)',
            # 输出信号/输出参数
            r'输出(?:信号|参数)?[：:]\s*(.+)',
            # 编号输出
            r'(?:\d+[\.\)]|\*|\-)\s*输出[：:]\s*(.+?)(?=\n\s*(?:\d+[\.\)]|\*|\-|$))',
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, content, re.DOTALL)
            for match in matches:
                description = match.group(1).strip()
                outputs.append({
                    "id": f"OUT_{len(outputs) + 1:03d}",
                    "description": description,
                    "associated_function": None
                })
        
        # 如果没有找到，尝试按行分割
        if not outputs:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                line = line.strip()
                if line and ('输出' in line or 'output' in line.lower()):
                    outputs.append({
                        "id": f"OUT_{i+1:03d}",
                        "description": line,
                        "associated_function": None
                    })
        
        return outputs
    
    def _extract_hazard_events(self, content: str) -> List[Dict[str, str]]:
        """提取危害事件"""
        hazard_events = []
        
        patterns = [
            # 危害事件：描述
            r'危害事件\s*[：:]\s*(.+)',
            r'[Hh]azard\s*[Ee]vent\s*[：:]\s*(.+)',
            # 编号危害事件
            r'(?:\d+[\.\)]|\*|\-)\s*(?:危害事件|[Hh]azard)[：:]\s*(.+?)(?=\n\s*(?:\d+[\.\)]|\*|\-|$))',
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, content, re.DOTALL)
            for match in matches:
                description = match.group(1).strip()
                hazard_events.append({
                    "id": f"HZ_{len(hazard_events) + 1:03d}",
                    "description": description,
                    "severity_level": None,
                    "exposure_level": None,
                    "controllability_level": None
                })
        
        return hazard_events
    
    def _map_functions_to_outputs(self, functions: List[Dict], outputs: List[Dict]) -> List[Dict]:
        """关联功能与输出"""
        mappings = []
        
        # 简单的一对一映射（按顺序）
        min_len = min(len(functions), len(outputs))
        for i in range(min_len):
            mappings.append({
                "function_id": functions[i]["id"],
                "function_name": functions[i]["name"],
                "output_id": outputs[i]["id"] if i < len(outputs) else None,
                "output_description": outputs[i]["description"] if i < len(outputs) else None
            })
        
        # 如果有额外的功能或输出
        if len(functions) > len(outputs):
            for i in range(len(outputs), len(functions)):
                mappings.append({
                    "function_id": functions[i]["id"],
                    "function_name": functions[i]["name"],
                    "output_id": None,
                    "output_description": None
                })
        
        return mappings
    
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
        print("使用方法: python word_processor.py <文件路径> [输出路径]")
        sys.exit(1)
    
    file_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "extraction_results.json"
    
    processor = WordProcessor()
    result = processor.extract_item_definition(file_path)
    
    if "error" in result:
        print(f"提取失败: {result['error']}")
        sys.exit(1)
    
    # 保存结果
    processor.save_extraction_results(result, output_path)
    
    # 打印摘要
    print(f"文档提取完成: {result['file_name']}")
    print(f"找到功能数量: {len(result['functions'])}")
    print(f"找到输出数量: {len(result['outputs'])}")
    print(f"找到危害事件数量: {len(result['hazard_events'])}")
    print(f"结果已保存至: {output_path}")


if __name__ == "__main__":
    test_extraction()

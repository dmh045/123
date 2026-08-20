# HARA自动化填写技能

## 概述

HARA（Hazard Analysis and Risk Assessment，危害分析和风险评估）自动化填写技能是基于GB/T 34590.3-2022《道路车辆 功能安全 第3部分：概念阶段》标准的自动化工具。该技能能够根据功能安全标准文档和HARA模板，自动完成危害分析和风险评估的填写工作。

## 核心功能

### 1. 多文档格式支持
- **Word文档**：解析Item Definition文档，提取功能描述和输出定义
- **PDF文档**：解析GB/T 34590.3标准文档，提取严重度、暴露概率、可控性定义
- **Excel文档**：读取HARA模板，提取引导词、场景库、ASIL表等关键信息

### 2. 自动化HARA分析流程
实现17步HARA分析流程的自动化：
1. 罗列功能（从Item Definition文档提取）
2. 罗列输出描述
3. 罗列对应功能的引导词
4. 生成Malfunction并进行合理性检查
5. 分析危害事件
6. 组合场景并进行逻辑性检查
7. 细化组合场景描述
8. 分析危害事件
9. 严重度合理性分析
10. 严重度打分
11. 暴露度合理性分析
12. 暴露度打分
13. 可控度合理性分析
14. 可控度打分
15. 判定ASIL等级
16. 填写安全目标
17. 确定安全状态

### 3. 智能逻辑检查
- **Malfunction合理性检查**：检查功能与引导词组合的合理性，排除不合理组合
- **场景组合逻辑检查**：检查Malfunction与场景组合的合理性，评估风险等级
- **语义兼容性检查**：基于语义规则检查组合的合理性

### 4. 报告生成
- **Excel报告**：按照HARA模板格式生成完整的Excel报告
- **HTML报告**：生成可视化摘要报告
- **Markdown报告**：生成文本格式摘要报告
- **JSON数据**：导出原始分析数据

## 技术架构

### 目录结构
```
hara-auto-fill/
├── SKILL.md                    # 技能定义文件
├── scripts/                    # 主要脚本目录
│   ├── main_executor.py       # 主执行脚本
│   ├── hara_engine.py         # HARA引擎核心
│   ├── report_generator.py    # 报告生成器
│   ├── example.py             # 使用示例
│   ├── document_parsers/      # 文档解析器
│   │   ├── word_processor.py  # Word文档处理器
│   │   ├── pdf_processor.py   # PDF文档处理器
│   │   └── excel_processor.py # Excel文档处理器
│   └── logic_checkers/        # 逻辑检查器
│       ├── malfunction_checker.py  # Malfunction检查器
│       └── scenario_checker.py     # 场景检查器
├── references/                # 参考文档
│   └── README.md             # 本文件
├── assets/                   # 资源文件
│   └── sample_item_definition.md  # 示例文档
└── output/                   # 输出目录（运行时生成）
```

### 核心组件

#### 1. 文档解析器 (`document_parsers/`)
- **WordProcessor**: 解析Word/Text/Markdown格式的Item Definition文档
- **PDFProcessor**: 解析GB/T 34590.3标准PDF文档
- **ExcelProcessor**: 解析HARA模板Excel，提取关键信息

#### 2. 逻辑检查器 (`logic_checkers/`)
- **MalfunctionChecker**: 检查功能与引导词组合的合理性
- **ScenarioChecker**: 检查Malfunction与场景组合的合理性

#### 3. HARA引擎 (`hara_engine.py`)
- 实现17步HARA分析流程
- 管理分析上下文和数据流
- 协调各个组件的执行

#### 4. 报告生成器 (`report_generator.py`)
- 生成符合HARA模板的Excel报告
- 生成可视化HTML报告和文本报告
- 导出原始分析数据

## 使用方法

### 基本使用流程

```python
from scripts.hara_engine import HARAEngine
from scripts.report_generator import HARAReportGenerator

# 1. 初始化HARA引擎
engine = HARAEngine()
engine.initialize(
    item_definition_path="path/to/item_definition.docx",
    excel_template_path="path/to/HARA_template.xlsx",
    standard_pdf_path="path/to/GB_T_34590.3.pdf"
)

# 2. 执行HARA分析
result = engine.execute_all_steps(1, 17)  # 执行所有步骤

# 3. 生成报告
report_generator = HARAReportGenerator()
context = engine.get_context()

# 生成Excel报告
excel_result = report_generator.generate_hara_report(
    context, 
    "path/to/HARA_template.xlsx",
    "output/hara_report.xlsx"
)

# 生成摘要报告
summary_result = report_generator.generate_summary_report(
    context,
    "output/"
)
```

### 命令行使用

```bash
# 运行示例
cd /home/workspace/hara-auto-fill
python scripts/example.py

# 单独测试组件
python scripts/document_parsers/excel_processor.py
python scripts/logic_checkers/malfunction_checker.py
python scripts/logic_checkers/scenario_checker.py
```

## 输入文件要求

### 1. Item Definition文档
- **格式**: Word (.docx), Text (.txt), Markdown (.md)
- **内容要求**:
  - 包含功能描述章节（如"2.5 相关项功能描述"）
  - 包含输出描述章节（如"3 输出描述"）
  - 包含危害事件章节（如"3.9.2 危害事件示例"）

### 2. HARA模板Excel
- **格式**: Excel (.xlsx)
- **必要Sheet页**:
  - `AI-process`: HARA分析流程步骤
  - `04_HAZOP`: HAZOP引导词定义
  - `05_HARA`: HARA报告主表格
  - `Scenarios_Libarary`: 场景库定义
  - `ASIL_Table`: ASIL等级计算规则
  - `Severity/Exposure/Controllability`: 三类参数打分依据

### 3. GB/T 34590.3标准PDF
- **格式**: PDF (.pdf)
- **内容**: 国家标准文档，包含HARA分析的相关定义和规则

## 输出文件

### 1. Excel报告 (`hara_report.xlsx`)
- 完整的HARA分析表格
- 按照模板格式填充
- 包含样式和格式

### 2. 摘要报告
- **HTML报告** (`hara_summary.html`): 可视化报告，适合浏览器查看
- **Markdown报告** (`hara_summary.md`): 文本格式报告，适合文档集成
- **JSON数据** (`hara_data.json`): 原始分析数据，适合程序处理

### 3. 分析结果
- **HARA上下文** (`hara_context.json`): 分析过程中的上下文数据
- **HARA结果** (`hara_results.json`): 各步骤的执行结果
- **Malfunction分析** (`malfunction_analysis.json`): Malfunction合理性分析结果
- **场景分析** (`scenario_analysis.json`): 场景组合分析结果

## 配置和定制

### 1. 修改逻辑检查规则
可以修改以下文件中的规则：
- `scripts/logic_checkers/malfunction_checker.py`: Malfunction合理性检查规则
- `scripts/logic_checkers/scenario_checker.py`: 场景组合检查规则

### 2. 调整HARA流程
可以修改以下文件：
- `scripts/hara_engine.py`: HARA分析流程步骤
- 各个步骤类的实现

### 3. 自定义报告模板
可以修改以下文件：
- `scripts/report_generator.py`: 报告生成逻辑和样式
- Excel模板文件本身

## 故障排除

### 常见问题

#### 1. 文档解析失败
- **问题**: Word/PDF文档无法解析
- **解决**: 确保文档格式正确，检查文件路径

#### 2. Excel模板格式不符
- **问题**: 无法找到预期的Sheet页或数据
- **解决**: 检查Excel模板是否符合要求格式

#### 3. 逻辑检查结果不理想
- **问题**: 有效组合太少或太多
- **解决**: 调整逻辑检查规则，适应具体应用场景

#### 4. 报告生成错误
- **问题**: Excel报告生成失败
- **解决**: 检查Excel模板路径，确保有写入权限

### 调试模式

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# 然后运行HARA分析
```

## 性能考虑

### 1. 处理时间
- 小型项目（<10个功能）: 1-2分钟
- 中型项目（10-50个功能）: 5-10分钟
- 大型项目（>50个功能）: 可能需要优化

### 2. 内存使用
- 主要内存消耗在文档解析和数据分析
- 建议为大型项目分配足够内存

### 3. 优化建议
- 分批处理大量功能
- 缓存解析结果
- 使用增量分析

## 扩展开发

### 1. 添加新的文档格式支持
继承基类并实现新的文档解析器

### 2. 添加新的逻辑检查规则
在相应的检查器中添加新的规则

### 3. 集成其他工具
可以通过API或文件接口集成其他安全分析工具

### 4. 支持更多输出格式
扩展报告生成器支持更多输出格式

## 相关标准

### 国家标准
- GB/T 34590.3-2022 道路车辆 功能安全 第3部分：概念阶段
- GB/T 34590 系列标准（功能安全标准）

### 行业标准
- ISO 26262 道路车辆功能安全
- SAE J2980 考虑功能安全的汽车系统设计

### 相关工具
- HAZOP (Hazard and Operability Study) 分析
- FMEA (Failure Mode and Effects Analysis) 分析
- FTA (Fault Tree Analysis) 故障树分析

## 许可证

本项目基于MIT许可证开源。

## 支持

如有问题或建议，请提交Issue或联系开发团队。

## 版本历史

### v1.0.0 (2026-04-03)
- 初始版本发布
- 支持Word/PDF/Excel文档解析
- 实现17步HARA分析流程
- 包含逻辑检查和报告生成功能
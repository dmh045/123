# HARA自动化填写技能使用指南

## 快速开始

### 1. 环境准备

确保系统已安装以下依赖：
- Python 3.8+
- 必要的Python库：pandas, openpyxl, PyPDF2, pdfplumber, python-docx

### 2. 文件准备

准备以下输入文件：
1. **Item Definition文档**：功能定义文档（Word/Text/Markdown格式）
2. **HARA模板Excel**：包含HARA分析模板的Excel文件
3. **GB/T 34590.3标准PDF**：功能安全国家标准文档（可选）

### 3. 运行示例

```bash
cd /home/workspace/hara-auto-fill
python scripts/example.py
```

## 详细使用步骤

### 步骤1: 准备输入文件

#### Item Definition文档示例
```markdown
# Item Definition: 电动门锁系统

## 2.5 相关项功能描述

### 2.5.1 功能1: 电动释放门锁
- 通过拉动门把手电动释放门锁
- 响应时间: <200ms
- 工作电压: 12V DC

### 2.5.2 功能2: 远程上锁/解锁
- 通过遥控钥匙远程控制门锁
- 通信协议: RF 433MHz
- 有效距离: 10米

## 3 输出描述

### 3.1 功能1输出
- 门锁电机从锁定状态切换到解锁状态
- 状态反馈: 解锁成功信号

### 3.2 功能2输出
- 门锁状态改变信号
- 遥控响应: LED闪烁反馈

## 3.9.2 危害事件示例
- 车辆行驶中车门意外打开
- 车辆静止时无法打开车门
- 儿童误操作打开车门
```

### 步骤2: 配置分析参数

创建配置文件 `config.json`:
```json
{
  "item_definition_path": "/path/to/item_definition.docx",
  "excel_template_path": "/path/to/HARA_Template_AI.xlsx",
  "standard_pdf_path": "/path/to/GB_T_34590.3.pdf",
  "output_dir": "/path/to/output",
  "analysis_steps": [1, 2, 3, 4, 5, 6],
  "enable_logging": true,
  "log_level": "INFO"
}
```

### 步骤3: 执行HARA分析

使用Python脚本执行分析：

```python
import json
from scripts.hara_engine import HARAEngine
from scripts.report_generator import HARAReportGenerator

# 加载配置
with open('config.json', 'r') as f:
    config = json.load(f)

# 初始化引擎
engine = HARAEngine()
engine.initialize(
    item_definition_path=config['item_definition_path'],
    excel_template_path=config['excel_template_path'],
    standard_pdf_path=config.get('standard_pdf_path')
)

# 执行分析步骤
steps = config.get('analysis_steps', list(range(1, 18)))
start_step = min(steps)
end_step = max(steps)

result = engine.execute_all_steps(start_step, end_step)

if result['success']:
    print(f"分析成功完成! 成功率: {result.get('success_rate', 0)}%")
else:
    print("分析过程中出现错误:")
    for failed in result.get('failed_steps', []):
        print(f"  步骤{failed['step']}: {failed['error']}")
```

### 步骤4: 生成报告

```python
# 生成Excel报告
report_generator = HARAReportGenerator()
context = engine.get_context()

excel_result = report_generator.generate_hara_report(
    context,
    config['excel_template_path'],
    f"{config['output_dir']}/hara_report.xlsx"
)

if excel_result['success']:
    print(f"Excel报告生成成功: {excel_result['output_path']}")
    print(f"填充了 {excel_result['filled_rows']} 行数据")
else:
    print(f"Excel报告生成失败: {excel_result.get('error', '')}")

# 生成摘要报告
summary_result = report_generator.generate_summary_report(
    context,
    config['output_dir']
)

if summary_result['success']:
    print("摘要报告生成成功")
    print(f"  HTML报告: {summary_result['html_report']}")
    print(f"  Markdown报告: {summary_result['markdown_report']}")
```

### 步骤5: 查看结果

检查输出目录中的文件：
```
output/
├── hara_report.xlsx          # 完整的HARA Excel报告
├── hara_summary.html         # HTML格式摘要报告
├── hara_summary.md           # Markdown格式摘要报告
├── hara_data.json            # 原始分析数据
├── hara_context.json         # 分析上下文
├── hara_results.json         # 各步骤结果
├── malfunction_analysis.json # Malfunction分析结果
└── scenario_analysis.json    # 场景分析结果
```

## 高级功能

### 1. 自定义逻辑检查规则

可以修改逻辑检查器中的规则：

```python
from scripts.logic_checkers.malfunction_checker import MalfunctionChecker

# 创建自定义检查器
checker = MalfunctionChecker()

# 添加自定义规则
checker.invalid_patterns.append(
    (r"自定义模式", "自定义原因")
)

# 添加功能类别
checker.function_categories["自定义类别"] = ["关键词1", "关键词2"]

# 添加有效组合
checker.valid_combinations["自定义类别"] = ["guideword1", "guideword2"]
```

### 2. 批量处理多个项目

```python
import os
from concurrent.futures import ThreadPoolExecutor

def analyze_project(project_dir):
    """分析单个项目"""
    item_def = os.path.join(project_dir, "item_definition.docx")
    output_dir = os.path.join(project_dir, "output")
    
    engine = HARAEngine()
    engine.initialize(item_def, "template.xlsx")
    
    result = engine.execute_all_steps(1, 6)
    
    if result['success']:
        report_generator = HARAReportGenerator()
        context = engine.get_context()
        report_generator.generate_summary_report(context, output_dir)
    
    return result

# 批量处理多个项目
project_dirs = ["project1", "project2", "project3"]

with ThreadPoolExecutor(max_workers=3) as executor:
    results = list(executor.map(analyze_project, project_dirs))
```

### 3. 集成到现有工作流

```python
def integrate_with_existing_workflow():
    """集成到现有工作流"""
    
    # 1. 从数据库获取项目信息
    projects = get_projects_from_database()
    
    for project in projects:
        # 2. 下载相关文件
        item_def = download_file(project['item_def_url'])
        template = download_file(project['template_url'])
        
        # 3. 执行HARA分析
        engine = HARAEngine()
        engine.initialize(item_def, template)
        result = engine.execute_all_steps(1, 17)
        
        # 4. 保存结果到数据库
        if result['success']:
            save_results_to_database(project['id'], engine.get_context())
            
            # 5. 生成报告并上传
            report_generator = HARAReportGenerator()
            excel_report = report_generator.generate_hara_report(
                engine.get_context(),
                template,
                f"temp/hara_report_{project['id']}.xlsx"
            )
            
            if excel_report['success']:
                upload_file(excel_report['output_path'], project['report_url'])
        
        # 6. 更新项目状态
        update_project_status(project['id'], 'completed' if result['success'] else 'failed')
```

## 配置选项

### 完整配置示例

```json
{
  "input_files": {
    "item_definition": {
      "path": "/path/to/item_definition.docx",
      "format": "docx",
      "encoding": "utf-8"
    },
    "excel_template": {
      "path": "/path/to/HARA_template.xlsx",
      "required_sheets": ["AI-process", "04_HAZOP", "05_HARA", "Scenarios_Libarary"]
    },
    "standard_document": {
      "path": "/path/to/GB_T_34590.3.pdf",
      "optional": true
    }
  },
  
  "analysis_settings": {
    "steps_to_execute": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17],
    "enable_validation": true,
    "validation_strictness": "medium",
    "max_combinations_per_function": 100,
    "risk_threshold": "medium"
  },
  
  "output_settings": {
    "directory": "/path/to/output",
    "formats": ["excel", "html", "markdown", "json"],
    "excel_settings": {
      "preserve_template_formatting": true,
      "auto_adjust_column_width": true,
      "add_summary_sheet": true
    },
    "report_settings": {
      "include_statistics": true,
      "include_examples": true,
      "max_examples_per_category": 5,
      "language": "zh-CN"
    }
  },
  
  "logging_settings": {
    "level": "INFO",
    "file": "/path/to/logs/hara_analysis.log",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "max_file_size": "10MB",
    "backup_count": 5
  },
  
  "performance_settings": {
    "max_workers": 4,
    "chunk_size": 10,
    "cache_results": true,
    "cache_ttl": 3600
  }
}
```

## 故障排除

### 常见错误及解决方法

#### 错误1: 文件不存在
```
错误: 文件不存在: /path/to/item_definition.docx
解决: 检查文件路径是否正确，确保文件存在且有读取权限
```

#### 错误2: Excel格式不匹配
```
错误: Sheet '05_HARA' 不存在
解决: 检查Excel模板是否包含必需的Sheet页
```

#### 错误3: 内存不足
```
错误: MemoryError
解决: 减少同时处理的功能数量，增加系统内存
```

#### 错误4: 权限问题
```
错误: Permission denied
解决: 确保有写入输出目录的权限
```

### 调试模式

启用详细日志：

```python
import logging

# 设置日志级别为DEBUG
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('hara_debug.log'),
        logging.StreamHandler()
    ]
)

# 然后运行分析
```

### 性能优化建议

1. **对于大型项目**：
   - 分批处理功能
   - 启用结果缓存
   - 调整工作线程数

2. **内存优化**：
   - 及时释放不再使用的数据
   - 使用生成器处理大数据
   - 考虑使用数据库存储中间结果

3. **I/O优化**：
   - 使用SSD存储
   - 减少不必要的文件操作
   - 批量读写数据

## 最佳实践

### 1. 文件命名规范
- Item Definition: `项目名称_ItemDefinition_版本.docx`
- HARA模板: `HARA_Template_版本.xlsx`
- 输出文件: `项目名称_HARA_分析日期_版本.xlsx`

### 2. 版本控制
- 为每个分析项目创建独立目录
- 保存配置文件和输入文件的副本
- 记录分析参数和版本信息

### 3. 质量保证
- 验证输入文件的完整性和正确性
- 检查分析结果的合理性
- 进行人工复核关键结果

### 4. 文档记录
- 记录分析过程和参数设置
- 保存日志文件和错误报告
- 生成分析报告文档

## 扩展开发

### 添加新的文档格式支持

```python
from scripts.document_parsers.base_processor import BaseProcessor

class CustomDocumentProcessor(BaseProcessor):
    """自定义文档处理器"""
    
    def __init__(self, file_path):
        super().__init__(file_path)
    
    def extract_functions(self):
        """提取功能描述"""
        # 实现自定义提取逻辑
        pass
    
    def extract_outputs(self):
        """提取输出描述"""
        # 实现自定义提取逻辑
        pass
```

### 添加新的分析步骤

```python
from scripts.hara_engine import HARAStep

class CustomStep(HARAStep):
    """自定义步骤"""
    
    def execute(self, context):
        """执行自定义步骤"""
        # 实现自定义逻辑
        pass
```

### 集成外部API

```python
class ExternalIntegration:
    """外部API集成"""
    
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url
    
    def validate_results(self, analysis_results):
        """使用外部API验证结果"""
        # 调用外部API
        pass
    
    def export_to_external_system(self, report_data):
        """导出到外部系统"""
        # 上传到外部系统
        pass
```

## 技术支持

### 获取帮助
- 查看详细文档
- 运行示例代码
- 检查日志文件

### 报告问题
1. 描述问题现象
2. 提供错误信息
3. 附上相关文件
4. 说明复现步骤

### 功能建议
欢迎提出功能建议和改进意见。

---

*本使用指南最后更新: 2026-04-03*
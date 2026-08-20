#!/usr/bin/env python3
"""
HARA自动化填写技能示例
展示如何使用该技能进行完整的HARA分析
"""

import os
import sys
import json
from typing import Dict, List, Any

# 添加脚本目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.document_parsers.word_processor import WordProcessor
from scripts.document_parsers.pdf_processor import PDFProcessor
from scripts.document_parsers.excel_processor import ExcelProcessor
from scripts.logic_checkers.malfunction_checker import MalfunctionChecker
from scripts.logic_checkers.scenario_checker import ScenarioChecker
from scripts.hara_engine import HARAEngine
from scripts.report_generator import HARAReportGenerator


def example_1_basic_usage():
    """示例1: 基本使用流程"""
    print("=" * 60)
    print("示例1: HARA自动化填写基本使用流程")
    print("=" * 60)
    
    # 1. 准备输入文件
    item_def_path = "/home/workspace/attachments/sample_item_definition.md"
    excel_template_path = "/home/workspace/attachments/HARA_Template_AI_20260327.xlsx"
    standard_pdf_path = "/home/workspace/attachments/GB T 34590.3-2022 道路车辆　功能安全　第3部分：概念阶段 2023-2-17 205329 5.pdf"
    
    # 2. 创建示例Item Definition文档
    create_sample_item_definition(item_def_path)
    
    # 3. 初始化HARA引擎
    print("\n1. 初始化HARA引擎...")
    engine = HARAEngine()
    engine.initialize(item_def_path, excel_template_path, standard_pdf_path)
    
    # 4. 执行HARA分析
    print("\n2. 执行HARA分析...")
    result = engine.execute_all_steps(1, 6)  # 执行步骤1-6
    
    if result["success"]:
        print(f"  分析完成! 成功率: {result.get('success_rate', 0)}%")
        print(f"  成功步骤: {result['successful_steps']}/{result['total_steps']}")
    else:
        print(f"  分析失败!")
        for failed in result.get('failed_steps', []):
            print(f"    步骤{failed['step']}: {failed['error']}")
    
    # 5. 生成报告
    print("\n3. 生成HARA报告...")
    
    # 获取分析结果
    context = engine.get_context()
    
    # 初始化报告生成器
    report_generator = HARAReportGenerator()
    
    # 生成Excel报告
    output_excel = "/home/workspace/hara-auto-fill/output/hara_report.xlsx"
    excel_result = report_generator.generate_hara_report(context, excel_template_path, output_excel)
    
    if excel_result["success"]:
        print(f"  Excel报告生成成功: {excel_result['output_path']}")
        print(f"  填充了 {excel_result['filled_rows']} 行数据")
    else:
        print(f"  Excel报告生成失败: {excel_result.get('error', '')}")
    
    # 生成摘要报告
    output_dir = "/home/workspace/hara-auto-fill/output"
    summary_result = report_generator.generate_summary_report(context, output_dir)
    
    if summary_result["success"]:
        print(f"  摘要报告生成成功:")
        print(f"    HTML报告: {summary_result['html_report']}")
        print(f"    Markdown报告: {summary_result['markdown_report']}")
        print(f"    JSON数据: {summary_result['json_data']}")
    
    # 6. 导出所有结果
    print("\n4. 导出分析结果...")
    engine.export_results(output_dir)
    print(f"  结果已导出到: {output_dir}")
    
    print("\n" + "=" * 60)
    print("示例1完成!")
    print("=" * 60)


def example_2_malfunction_analysis():
    """示例2: Malfunction合理性分析"""
    print("\n" + "=" * 60)
    print("示例2: Malfunction合理性分析")
    print("=" * 60)
    
    # 测试数据
    test_functions = [
        "电动门锁释放",
        "车辆加速控制",
        "刹车系统",
        "转向控制",
        "灯光控制"
    ]
    
    # 从Excel模板获取引导词
    excel_path = "/home/workspace/attachments/HARA_Template_AI_20260327.xlsx"
    excel_processor = ExcelProcessor(excel_path)
    
    if excel_processor.load_excel():
        guidewords = excel_processor.get_hazop_guidewords()
        
        # 使用MalfunctionChecker分析
        checker = MalfunctionChecker()
        results = checker.check_multiple_combinations(test_functions, guidewords)
        
        # 统计结果
        total = len(results)
        valid = len([r for r in results if r["check_result"]["is_valid"]])
        invalid = total - valid
        
        print(f"\n分析结果:")
        print(f"  总组合数: {total}")
        print(f"  有效组合: {valid} ({valid/total*100:.1f}%)")
        print(f"  无效组合: {invalid} ({invalid/total*100:.1f}%)")
        
        # 显示示例
        print(f"\n有效组合示例:")
        valid_results = [r for r in results if r["check_result"]["is_valid"]]
        for i, result in enumerate(valid_results[:5], 1):
            print(f"  {i}. {result['function']} + {result['guideword']}")
            print(f"     故障: {result['malfunction_description']}")
            print(f"     功能类别: {result['check_result']['function_category']}")
            print(f"     引导词类别: {result['check_result']['guideword_category']}")
        
        print(f"\n无效组合示例:")
        invalid_results = [r for r in results if not r["check_result"]["is_valid"]]
        for i, result in enumerate(invalid_results[:3], 1):
            print(f"  {i}. {result['function']} + {result['guideword']}")
            print(f"     原因: {', '.join(result['check_result']['reasons'][:2])}")
        
        # 导出结果
        output_path = "/home/workspace/hara-auto-fill/output/malfunction_analysis.json"
        checker.export_check_results(results, output_path)
        print(f"\n详细结果已导出到: {output_path}")
    
    print("\n" + "=" * 60)
    print("示例2完成!")
    print("=" * 60)


def example_3_scenario_analysis():
    """示例3: 场景组合分析"""
    print("\n" + "=" * 60)
    print("示例3: 场景组合分析")
    print("=" * 60)
    
    # 测试数据
    test_malfunctions = [
        "刹车系统失效",
        "转向系统失效",
        "加速系统失控",
        "灯光系统失效"
    ]
    
    # 从Excel模板获取场景库
    excel_path = "/home/workspace/attachments/HARA_Template_AI_20260327.xlsx"
    excel_processor = ExcelProcessor(excel_path)
    
    if excel_processor.load_excel():
        scenarios = excel_processor.get_scenarios_library()
        
        if scenarios:
            # 使用ScenarioChecker分析
            checker = ScenarioChecker()
            results = checker.check_multiple_scenario_combinations(test_malfunctions, scenarios)
            
            # 统计结果
            total = len(results)
            valid = len([r for r in results if r["check_result"]["is_valid"]])
            invalid = total - valid
            
            print(f"\n分析结果:")
            print(f"  总组合数: {total}")
            print(f"  有效组合: {valid} ({valid/total*100:.1f}%)")
            print(f"  无效组合: {invalid} ({invalid/total*100:.1f}%)")
            
            # 风险等级统计
            risk_levels = {}
            for result in results:
                if result["check_result"]["is_valid"]:
                    risk_level = result["check_result"]["risk_level"]
                    risk_levels[risk_level] = risk_levels.get(risk_level, 0) + 1
            
            print(f"\n风险等级分布:")
            for level, count in risk_levels.items():
                print(f"  {level}: {count} ({count/valid*100:.1f}%)")
            
            # 显示高风险组合示例
            print(f"\n高风险组合示例:")
            high_risk_results = [
                r for r in results 
                if r["check_result"]["is_valid"] and r["check_result"]["risk_level"] in ["高", "极高"]
            ]
            
            for i, result in enumerate(high_risk_results[:3], 1):
                print(f"  {i}. {result['malfunction']}")
                print(f"     场景: {result['scenario_summary']}")
                print(f"     危害事件: {result['hazard_event']}")
                print(f"     风险等级: {result['check_result']['risk_level']}")
            
            # 导出结果
            output_path = "/home/workspace/hara-auto-fill/output/scenario_analysis.json"
            checker.export_check_results(results, output_path)
            print(f"\n详细结果已导出到: {output_path}")
    
    print("\n" + "=" * 60)
    print("示例3完成!")
    print("=" * 60)


def example_4_document_parsing():
    """示例4: 文档解析功能"""
    print("\n" + "=" * 60)
    print("示例4: 文档解析功能")
    print("=" * 60)
    
    # 1. Word文档解析
    print("\n1. Word文档解析:")
    word_path = "/home/workspace/attachments/sample_item_definition.md"
    create_sample_item_definition(word_path)
    
    word_processor = WordProcessor(word_path)
    functions = word_processor.extract_functions()
    outputs = word_processor.extract_outputs()
    
    print(f"  提取到 {len(functions)} 个功能:")
    for func in functions:
        print(f"    - {func}")
    
    print(f"  提取到 {len(outputs)} 个输出:")
    for output in outputs:
        print(f"    - {output}")
    
    # 2. PDF文档解析
    print("\n2. PDF文档解析:")
    pdf_path = "/home/workspace/attachments/GB T 34590.3-2022 道路车辆　功能安全　第3部分：概念阶段 2023-2-17 205329 5.pdf"
    
    if os.path.exists(pdf_path):
        pdf_processor = PDFProcessor(pdf_path)
        
        # 提取严重度定义
        severity_def = pdf_processor.extract_severity_definition()
        if severity_def:
            print(f"  提取到严重度定义:")
            for level, desc in severity_def.items():
                print(f"    {level}: {desc}")
        
        # 提取暴露概率定义
        exposure_def = pdf_processor.extract_exposure_definition()
        if exposure_def:
            print(f"  提取到暴露概率定义:")
            for level, desc in exposure_def.items():
                print(f"    {level}: {desc}")
    
    # 3. Excel文档解析
    print("\n3. Excel文档解析:")
    excel_path = "/home/workspace/attachments/HARA_Template_AI_20260327.xlsx"
    
    excel_processor = ExcelProcessor(excel_path)
    if excel_processor.load_excel():
        # 获取步骤信息
        steps = excel_processor.get_ai_process_steps()
        print(f"  提取到 {len(steps)} 个HARA步骤")
        
        # 获取引导词
        guidewords = excel_processor.get_hazop_guidewords()
        print(f"  提取到 {len(guidewords)} 个HAZOP引导词")
        
        # 获取场景库
        scenarios = excel_processor.get_scenarios_library()
        print(f"  提取到 {len(scenarios)} 个场景")
        
        # 导出所有数据
        output_path = "/home/workspace/hara-auto-fill/output/excel_data.json"
        excel_processor.export_to_json(output_path)
        print(f"  Excel数据已导出到: {output_path}")
    
    print("\n" + "=" * 60)
    print("示例4完成!")
    print("=" * 60)


def create_sample_item_definition(file_path: str):
    """
    创建示例Item Definition文档
    
    Args:
        file_path: 文件路径
    """
    content = """# Item Definition: 电动门锁系统

## 项目概述
本文件定义了电动门锁系统的相关项，包括功能描述、输出定义和危害分析。

## 2.5 相关项功能描述

### 2.5.1 功能1: 电动释放门锁
- 通过拉动门把手电动释放门锁
- 响应时间: <200ms
- 工作电压: 12V DC
- 工作温度: -40°C 到 +85°C

### 2.5.2 功能2: 远程上锁/解锁
- 通过遥控钥匙远程控制门锁
- 通信协议: RF 433MHz
- 有效距离: 10米
- 响应时间: <500ms

### 2.5.3 功能3: 儿童安全锁
- 防止后车门从内部打开
- 可通过驾驶员控制面板启用/禁用
- 状态指示: LED指示灯
- 手动覆盖: 机械钥匙覆盖

### 2.5.4 功能4: 速度感应自动上锁
- 车速超过20km/h时自动上锁
- 防止行驶中车门意外打开
- 可配置: 可通过设置启用/禁用
- 声音反馈: 上锁确认音

## 3 输出描述

### 3.1 功能1输出
- 门锁电机从锁定状态切换到解锁状态
- 状态反馈: 解锁成功信号
- 故障指示: 电机堵转检测

### 3.2 功能2输出
- 门锁状态改变信号
- 遥控响应: LED闪烁反馈
- 安全验证: 滚动码加密

### 3.3 功能3输出
- 后门锁止机构状态
- 控制面板状态显示
- 安全锁启用/禁用确认

### 3.4 功能4输出
- 车速感应上锁状态
- 上锁事件记录
- 系统状态报告

## 3.9 危害分析

### 3.9.1 潜在危害
1. 车辆行驶中车门意外打开
2. 车辆静止时无法打开车门
3. 儿童误操作打开车门
4. 遥控信号干扰导致误操作
5. 车速感应失效导致安全隐患

### 3.9.2 危害事件示例
- 车辆高速行驶时车门意外打开，导致乘员跌落
- 车辆发生事故后车门无法打开，阻碍救援
- 儿童在行驶中误开车门，造成安全风险
- 遥控信号被干扰，导致车辆在公共场所意外解锁
- 车速感应失效，车辆行驶中未自动上锁

## 4 操作场景

### 4.1 典型使用场景
1. 日常通勤: 城市道路，车速30-50km/h
2. 高速公路: 车速100-120km/h
3. 停车场: 低速移动，寻找车位
4. 接送儿童: 学校区域，频繁启停
5. 长途旅行: 混合路况，长时间驾驶

### 4.2 特殊场景
1. 极端天气: 暴雨、大雪、高温
2. 紧急情况: 事故、故障、救援
3. 维护保养: 维修车间，专业操作
4. 充电场景: 充电站，车辆静止
5. 洗车场景: 自动洗车机，车辆移动

## 5 性能要求

### 5.1 功能性能
- 解锁时间: <200ms
- 上锁时间: <150ms
- 可靠性: MTBF > 100,000小时
- 耐久性: > 100,000次循环

### 5.2 安全性能
- 故障检测: 实时监控
- 安全状态: 故障时进入安全状态
- 冗余设计: 关键功能冗余
- 诊断功能: 故障代码记录

## 6 环境条件

### 6.1 工作环境
- 温度范围: -40°C 到 +85°C
- 湿度范围: 5% 到 95% RH
- 振动等级: 符合ISO 16750-3
- 防护等级: IP67

### 6.2 电磁兼容
- EMI: 符合CISPR 25
- EMS: 符合ISO 11452
- ESD: 符合ISO 10605
"""
    
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"创建了示例Item Definition文档: {file_path}")


def main():
    """主函数"""
    print("HARA自动化填写技能示例")
    print("=" * 60)
    
    # 创建输出目录
    output_dir = "/home/workspace/hara-auto-fill/output"
    os.makedirs(output_dir, exist_ok=True)
    
    # 运行所有示例
    example_1_basic_usage()
    example_2_malfunction_analysis()
    example_3_scenario_analysis()
    example_4_document_parsing()
    
    print("\n" + "=" * 60)
    print("所有示例完成!")
    print("=" * 60)
    print(f"\n输出文件位于: {output_dir}")
    print("\n可查看以下文件:")
    print("1. hara_report.xlsx - 完整的HARA Excel报告")
    print("2. hara_summary.html - HTML格式摘要报告")
    print("3. hara_summary.md - Markdown格式摘要报告")
    print("4. hara_data.json - 原始分析数据")
    print("5. malfunction_analysis.json - Malfunction分析结果")
    print("6. scenario_analysis.json - 场景分析结果")
    print("7. excel_data.json - Excel模板解析数据")


if __name__ == "__main__":
    main()
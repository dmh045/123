#!/usr/bin/env python3
"""
场景与危害事件引擎
实现HARA步骤6-8：
- Step6: 组合场景 - 使用ScenarioChecker将Malfunction与场景组合
- Step7: 细化场景 - 补充车辆状态、车速、天气、路面条件等维度
- Step8: 分析危害事件 - 优先级从Item Definition/模板提取，无则推理生成

输入: malfunction_output.json (function_malfunctions, function_hazards)
输出: scen_hazevent_output.json (function_scenarios, hazard_events)
"""

import os
import sys
import json
import ast
import re
import hashlib
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hara_agent.domains import AVPDomainPolicy, load_domain_profile

try:
    from document_parsers.excel_processor import ExcelProcessor
    from logic_checkers.scenario_checker import ScenarioChecker
except ImportError as e:
    logging.warning(f"导入模块失败，使用本地实现: {e}")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class LocalScenarioChecker:
    """本地ScenarioChecker实现（当导入失败时使用）"""

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

    BEHAVIOR_MAPPING = {
        "unintended": "车辆非预期触发功能导致<行为结果>",
        "always active": "车辆持续执行异常功能导致<行为结果>",
        "loss": "车辆关键功能丧失导致<行为结果>",
        "too large": "车辆参数过大导致<行为结果>",
        "too small": "车辆参数过小导致<行为结果>",
        "too early": "车辆过早执行操作导致<行为结果>",
        "too late": "车辆过晚执行操作导致<行为结果>",
        "too fast": "车辆速度过快导致<行为结果>",
        "too slow": "车辆速度过慢导致<行为结果>",
        "too long": "车辆功能持续过长导致<行为结果>",
        "too short": "车辆功能过早终止导致<行为结果>",
        "incomplete": "车辆功能不完整导致<行为结果>",
        "different to": "车辆行为偏离预期导致<行为结果>",
        "as well as": "车辆多重功能冲突导致<行为结果>"
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

    INJURY_MAPPING = {
        "unintended": "驾驶员或乘员遭受碰撞伤害或被甩出车外",
        "always active": "驾驶员被迫反复应对异常，造成疲劳并引发碰撞或甩出伤害",
        "loss": "驾驶员失去对车辆的控制，导致严重碰撞、甩出或窒息伤害",
        "too large": "驾驶员难以精确控制车辆，造成碰撞、侧翻或甩出伤害",
        "too small": "驾驶员无法获得足够的车辆功能支持，造成被困或延误救援伤害",
        "too early": "驾驶员未做好应对准备，造成应急响应失败后的碰撞或夹挤伤害",
        "too late": "驾驶员错过最佳应对时机，造成不可避免的严重碰撞或甩出伤害",
        "too fast": "驾驶员来不及反应，造成高速碰撞或甩出伤害",
        "too slow": "驾驶员判断延迟，造成追尾或被追尾伤害",
        "too long": "驾驶员长时间处于紧张状态，造成疲劳伤害，若车辆持续异常可引发碰撞或甩出",
        "too short": "驾驶员无法确认操作完成，造成二次碰撞或夹挤伤害",
        "incomplete": "驾驶员面对不一致的车辆状态，造成判断失误导致碰撞、甩出或夹挤伤害",
        "different to": "驾驶员误判车辆状态，造成操作失误导致碰撞或甩出伤害",
        "as well as": "驾驶员同时面对多重异常，造成应对失败导致碰撞、甩出或电击伤害"
    }

    INVALID_SCENARIO_COMBINATIONS = [
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

    MALFUNCTION_SEVERITY = {
        "high_severity": ["刹车系统失效", "转向系统失效", "加速系统失控",
                          "安全气囊误触发", "电池热失控"],
        "medium_severity": ["灯光系统失效", "雨刷器失效", "空调系统失效",
                            "娱乐系统故障", "车窗控制失效"],
        "low_severity": ["座椅调节失效", "后视镜调节失效", "氛围灯故障",
                         "音响系统故障", "充电口故障"]
    }

    FUNCTION_SCENARIO_CORRELATION = {
        "刹车": ["Highway", "Urban", "Rural", "Intersection", "Roundabout", "Parking"],
        "制动": ["Highway", "Urban", "Rural", "Intersection", "Roundabout", "Parking"],
        "转向": ["Urban", "Intersection", "Roundabout", "Parking", "Residential"],
        "加速": ["Highway", "Urban", "Rural", "Parking"],
        "灯光": ["Night", "Tunnel", "Dawn", "Dusk", "Parking", "Heavy Rain", "Fog"],
        "车门": ["Parking", "Residential", "Urban"],
        "车窗": ["Parking", "Urban", "Rain"],
        "气囊": ["Crash", "Collision", "Highway"],
        "雨刷": ["Highway", "Urban", "Rural", "Rain", "Parking"],
        "雨刮": ["Highway", "Urban", "Rural", "Rain", "Parking"],
        "wiper": ["Highway", "Urban", "Rural", "Rain", "Parking"],
    }

    def __init__(self):
        self.standard_main_scenarios = []
        self.scenario_library = []

    def check_malfunction_scenario_combo(self, malfunction: str, scenario: Dict[str, str]) -> Dict[str, Any]:
        """检查Malfunction与场景组合的合理性"""
        result = {
            "is_valid": True,
            "reasons": [],
            "risk_level": "低"
        }

        if not malfunction or not scenario:
            result["is_valid"] = False
            result["reasons"].append("Malfunction或场景为空")
            return result

        scenario_text = " ".join([str(v) for v in scenario.values() if v])
        combo_text = f"{malfunction} {scenario_text}"

        for pattern, reason in self.INVALID_SCENARIO_COMBINATIONS:
            if re.search(pattern, combo_text, re.IGNORECASE):
                result["is_valid"] = False
                result["reasons"].append(f"匹配到不合理模式: {reason}")

        scenario_risk = self._analyze_scenario_risk(scenario)
        malfunction_severity = self._get_malfunction_severity(malfunction)

        if malfunction_severity == "high" and scenario_risk == "high":
            result["risk_level"] = "极高"
        elif malfunction_severity == "high" or scenario_risk == "high":
            result["risk_level"] = "高"
        elif malfunction_severity == "medium" or scenario_risk == "medium":
            result["risk_level"] = "中"

        if result["is_valid"]:
            result["reasons"].append(f"Malfunction'{malfunction}'与场景组合合理")
            result["reasons"].append(f"风险等级: {result['risk_level']}")

        return result

    def check_multiple_scenario_combinations(self, malfunctions: List[str],
                                             scenarios: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """检查多个Malfunction与场景的组合"""
        results = []
        for malfunction in malfunctions:
            for scenario in scenarios:
                check_result = self.check_malfunction_scenario_combo(malfunction, scenario)
                hazard_event = self.generate_hazard_event(malfunction, scenario) if check_result["is_valid"] \
                    else f"不合理组合: {malfunction}"
                results.append({
                    "malfunction": malfunction,
                    "scenario": scenario,
                    "scenario_summary": self._summarize_scenario(scenario),
                    "check_result": check_result,
                    "hazard_event": hazard_event,
                    "should_include": check_result["is_valid"]
                })
        return results

    def filter_valid_combinations(self, combinations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """过滤出有效的组合"""
        return [c for c in combinations if c["check_result"]["is_valid"]]

    def generate_hazard_event(self, malfunction: str, scenario: Dict[str, str]) -> str:
        """生成危害事件描述: <故障状态>导致<具体车辆行为>，<人员伤害>"""
        guideword = self._detect_guideword(malfunction)

        fault_state = self.FAULT_STATE_MAPPING.get(guideword, f"{malfunction}导致系统异常")

        specific_behaviors = self.SPECIFIC_BEHAVIOR_RESULTS.get(guideword, {})
        vehicle_behavior = None
        if specific_behaviors:
            for kw, behavior in specific_behaviors.items():
                if kw != "default" and kw in malfunction:
                    vehicle_behavior = behavior
                    break
            if vehicle_behavior is None:
                vehicle_behavior = specific_behaviors.get("default", "车辆发生碰撞事故")

        injury = self.INJURY_MAPPING.get(guideword, "造成人员伤害")

        return f"<{fault_state}>导致<{vehicle_behavior}>，<{injury}>"

    def _summarize_scenario(self, scenario: Dict[str, str]) -> str:
        """汇总场景描述"""
        parts = []
        if scenario.get("operating_scenario"):
            parts.append(scenario["operating_scenario"])
        if scenario.get("vehicle_state"):
            parts.append(scenario["vehicle_state"])
        if scenario.get("vehicle_speed"):
            parts.append(f"车速{scenario['vehicle_speed']}")
        conditions = []
        if scenario.get("weather_conditions"):
            conditions.append(scenario["weather_conditions"])
        if scenario.get("road_surface_conditions"):
            conditions.append(scenario["road_surface_conditions"])
        if conditions:
            parts.append(f"({'、'.join(conditions)})")
        return "，".join(parts)

    def _analyze_scenario_risk(self, scenario: Dict[str, str]) -> str:
        """分析场景风险等级"""
        risk_score = 0
        vehicle_speed = scenario.get("vehicle_speed", "")
        if "高速" in vehicle_speed or "100+" in vehicle_speed:
            risk_score += 3
        elif "70-100" in vehicle_speed or "50-70" in vehicle_speed:
            risk_score += 2
        elif "30-50" in vehicle_speed:
            risk_score += 1

        operating_scenario = scenario.get("operating_scenario", "")
        if any(road in operating_scenario for road in ["高速公路", "高速", "国道", "主干道"]):
            risk_score += 2
        elif any(road in operating_scenario for road in ["城市道路", "市区", "居民区"]):
            risk_score += 1

        weather = scenario.get("weather_conditions", "")
        if any(cond in weather for cond in ["暴雨", "大雪", "大雾", "冰雹"]):
            risk_score += 2
        elif any(cond in weather for cond in ["雨天", "雪天", "雾天"]):
            risk_score += 1

        road_surface = scenario.get("road_surface_conditions", "")
        if any(cond in road_surface for cond in ["结冰", "积雪", "湿滑", "泥泞"]):
            risk_score += 2

        if risk_score >= 4:
            return "high"
        elif risk_score >= 2:
            return "medium"
        return "low"

    def _get_malfunction_severity(self, malfunction: str) -> str:
        """获取故障严重度"""
        for severity_level, malfunctions in self.MALFUNCTION_SEVERITY.items():
            for m in malfunctions:
                if m in malfunction:
                    return severity_level.split('_')[0]
        return "low"

    def check_scenario_completeness(self, used_scenarios: List[str]) -> Dict[str, Any]:
        """检查14个标准主场景的完整性"""
        standard_scenarios = self.standard_main_scenarios or [
            "Driving on the highway in lane 1 (straight ahead)",
            "Driving on the highway and lane change to right",
            "Driving on the highway and lane change to left",
            "Driving on the highway and overtaking on the right",
            "Driving on the highway and overtaking on the left",
            "Driving on the highway and taking the exit to the right",
            "Driving in urban area and turning left",
            "Driving in urban area and turning right",
            "Driving in urban area and going straight",
            "Driving in urban area and lane change to right",
            "Driving in urban area and lane change to left",
            "Driving in residential area and going straight",
            "Driving in residential area and turning left",
            "Driving in residential area and turning right"
        ]

        used_lower = [s.lower() for s in used_scenarios if s]
        missing = []
        for std in standard_scenarios:
            std_lower = std.lower()
            if not any(std_lower in u or any(kw in std_lower for kw in u.split()
                           if len(kw) > 3) for u in used_lower):
                missing.append(std)

        return {
            "complete": len(missing) == 0,
            "total_standard": len(standard_scenarios),
            "used_count": len(standard_scenarios) - len(missing),
            "missing_count": len(missing),
            "missing_scenarios": missing
        }

    def get_related_scenarios(self, func: str, scenarios: List[Dict], all_type_scenarios: List) -> List:
        """根据功能与场景类型的关联强度返回相关场景"""
        func_lower = func.lower()
        has_strong_correlation = False
        related_keywords = []

        for keyword, scenario_types in self.FUNCTION_SCENARIO_CORRELATION.items():
            if keyword.lower() in func_lower:
                has_strong_correlation = True
                related_keywords.extend(scenario_types)

        if has_strong_correlation and related_keywords:
            related = []
            for sc in scenarios:
                scenario_text = str(sc.get("scenario", "")).lower()
                operating_text = str(sc.get("operating_scenario", "")).lower()
                for kw in related_keywords:
                    if kw.lower() in scenario_text or kw.lower() in operating_text:
                        related.append(sc)
                        break
            if related:
                return related
            return all_type_scenarios if all_type_scenarios else scenarios[:5]
        else:
            return all_type_scenarios if all_type_scenarios else scenarios[:5]


class ScenHazEventEngine:
    """场景与危害事件引擎"""

    def __init__(self, input_json_path: str = None, excel_template_path: str = None,
                 domain_profile_path: str = None):
        """
        初始化引擎

        Args:
            input_json_path: 上游malfunction_output.json路径
            excel_template_path: HARA模板Excel路径（用于获取场景库）
        """
        self.input_json_path = input_json_path
        self.excel_template_path = excel_template_path
        self.input_data = None
        self.scenarios_library = []
        self.output_data = {}
        self.source_document = ""
        self.subsystem = ""
        self.domain_profile = load_domain_profile(
            "avp", path=domain_profile_path, require_approved=False
        )
        self.avp_policy = AVPDomainPolicy(self.domain_profile)
        self.hazard_event_index = self._load_hazard_event_index()
        self.hazard_ref_entries = self._load_hazard_ref_entries()
        self.scenario_ref = self._load_scenario_ref()

        self.checker = None
        self._init_checker()

    def _init_checker(self):
        """初始化场景检查器"""
        try:
            self.checker = ScenarioChecker()
            logger.info("使用导入的ScenarioChecker")
        except Exception:
            self.checker = LocalScenarioChecker()
            logger.info("使用本地ScenarioChecker")

    def load_input(self, input_json_path: str = None) -> bool:
        """
        加载上游输入JSON

        Args:
            input_json_path: JSON文件路径

        Returns:
            是否加载成功
        """
        if input_json_path:
            self.input_json_path = input_json_path

        if not self.input_json_path or not os.path.exists(self.input_json_path):
            logger.error(f"输入文件不存在: {self.input_json_path}")
            return False

        try:
            with open(self.input_json_path, 'r', encoding='utf-8') as f:
                self.input_data = json.load(f)

            logger.info(f"成功加载输入文件: {self.input_json_path}")
            logger.info(f"  function_malfunctions: {len(self.input_data.get('function_malfunctions', {}))} 个功能")
            logger.info(f"  function_hazards: {len(self.input_data.get('function_hazards', {}))} 个功能")

            if "metadata" in self.input_data:
                self.source_document = self.input_data["metadata"].get("source_document", "")
                self.subsystem = self.input_data["metadata"].get("subsystem", "")
                if not self.subsystem and self.source_document:
                    doc_lower = self.source_document.lower()
                    for kw, name in {
                        "wiper": "wiper", "雨刷": "wiper", "雨刮": "wiper",
                        "window": "window", "车窗": "window",
                        "door": "door", "车门": "door",
                        "light": "light", "灯": "light",
                        "seat": "seat", "座椅": "seat",
                        "ota": "ota", "ota update": "ota",
                        "hud": "hud",
                    }.items():
                        if kw in doc_lower:
                            self.subsystem = name
                            break

            if not self.subsystem and self._is_avp_context():
                self.subsystem = "avp"

            return True
        except Exception as e:
            logger.error(f"加载输入文件失败: {e}")
            return False

    def load_scenarios_library(self, excel_template_path: str = None) -> bool:
        """
        从HARA模板加载场景库

        Args:
            excel_template_path: Excel模板路径

        Returns:
            是否加载成功
        """
        if excel_template_path:
            self.excel_template_path = excel_template_path

        if not self.excel_template_path:
            logger.warning("未提供Excel模板路径，跳过场景库加载")
            return False

        if not os.path.exists(self.excel_template_path):
            logger.warning(f"Excel模板文件不存在: {self.excel_template_path}")
            return False

        try:
            excel_processor = ExcelProcessor(self.excel_template_path)
            if not excel_processor.load_excel():
                logger.error("加载Excel失败")
                return False

            self.scenarios_library = excel_processor.get_scenarios_library()
            logger.info(f"从模板加载了 {len(self.scenarios_library)} 个场景")
            return True

        except Exception as e:
            logger.error(f"加载场景库失败: {e}")
            return False

    def run(self, excel_template_path: str = None) -> Dict[str, Any]:
        """
        执行Step6-8流程

        Args:
            excel_template_path: Excel模板路径（可选）

        Returns:
            执行结果
        """
        if excel_template_path:
            self.excel_template_path = excel_template_path

        if not self.input_data:
            if not self.load_input():
                return {"success": False, "error": "加载输入数据失败"}

        self.load_scenarios_library()

        logger.info("=" * 60)
        logger.info("开始执行场景与危害事件引擎 (Step6-8)")
        logger.info("=" * 60)

        function_malfunctions = self.input_data.get("function_malfunctions", {})
        function_hazards = self.input_data.get("function_hazards", {})

        if not function_malfunctions:
            logger.warning("function_malfunctions为空，尝试从其他格式加载")
            function_malfunctions = self._extract_from_hazard_events()

        step6_result = self._step6_combine_scenarios(function_malfunctions)
        if not step6_result["success"]:
            return step6_result

        step7_result = self._step7_refine_scenarios(step6_result["function_scenarios"])
        if not step7_result["success"]:
            return step7_result

        step6_result["function_scenarios"] = self._merge_refined_into_function_scenarios(
            step6_result["function_scenarios"],
            step7_result["refined_function_scenarios"]
        )
        scenario_catalog = self._build_scenario_catalog(step6_result["function_scenarios"])

        step8_result = self._step8_analyze_hazard_events(
            function_hazards,
            step6_result["function_scenarios"],
            step7_result["refined_function_scenarios"]
        )

        step8_hazards = step8_result["hazard_events"]
        for func, malf_events in step8_hazards.items():
            if func not in step6_result["function_scenarios"]:
                continue
            for malf, event_list in malf_events.items():
                if malf in step6_result["function_scenarios"][func]:
                    scenario_list = step6_result["function_scenarios"][func][malf]
                    if event_list and scenario_list:
                        events_by_id = {
                            event.get("scenario_id", ""): event.get("hazard_event", "")
                            for event in event_list if isinstance(event, dict)
                        }
                        for idx, sc in enumerate(scenario_list):
                            step8_he = events_by_id.get(sc.get("scenario_id", ""), "")
                            if not step8_he and idx < len(event_list):
                                event = event_list[idx]
                                step8_he = event.get("hazard_event", "") if isinstance(event, dict) else ""
                            if step8_he and step8_he != "N/A":
                                sc["hazard_event"] = step8_he

        self.output_data = {
            "function_scenarios": step6_result["function_scenarios"],
            "refined_function_scenarios": step7_result["refined_function_scenarios"],
            "hazard_events": step8_result["hazard_events"],
            "scenario_catalog": scenario_catalog,
            "rejected_combination_audit": step6_result.get("rejected_combination_audit", []),
            "metadata": {
                "source_document": self.source_document,
                "timestamp": datetime.now().isoformat(),
                "engine": "scen_hazevent_engine",
                "step6_total_combinations": step6_result["total_combinations"],
                "step6_skipped_malfunctions": step6_result.get("skipped_malfunctions", 0),
                "step6_rejected_combinations": step6_result.get("rejected_combinations", 0),
                "scenario_selection_policy": "odd_and_function_relevance;soft_target_1_to_3;retain_distinct_risk_candidates_then_phase3_equivalence",
                "subsystem": self.subsystem,
                "domain_profile": {
                    "name": self.domain_profile.name,
                    "version": self.domain_profile.version,
                    "approval_status": self.domain_profile.approval_status,
                    "source": self.domain_profile.source,
                    "path": str(self.domain_profile.path),
                } if self._is_avp_context() else None,
                "step7_total_refined": step7_result["total_refined"],
                "step8_total_events": step8_result["total_events"],
                "scenarios_library_count": len(self.scenarios_library)
            }
        }

        logger.info("=" * 60)
        logger.info(f"场景与危害事件引擎完成")
        logger.info(f"  Step6: {step6_result['total_combinations']} 个有效组合")
        logger.info(f"  Step7: {step7_result['total_refined']} 个细化场景")
        logger.info(f"  Step8: {step8_result['total_events']} 个危害事件")
        logger.info("=" * 60)

        return {
            "success": True,
            "function_scenarios": step6_result["function_scenarios"],
            "refined_function_scenarios": step7_result["refined_function_scenarios"],
            "hazard_events": step8_result["hazard_events"],
            "scenario_catalog": scenario_catalog,
            "rejected_combination_audit": step6_result.get("rejected_combination_audit", []),
            "metadata": self.output_data["metadata"]
        }

    def _extract_from_hazard_events(self) -> Dict[str, List]:
        """从function_hazard_events中提取function_malfunctions格式"""
        function_hazard_events = self.input_data.get("function_hazard_events", {})
        result = {}
        for func, malf_data in function_hazard_events.items():
            malfunctions = []
            if isinstance(malf_data, dict):
                for malf, events in malf_data.items():
                    if isinstance(events, list) and events:
                        for event in events:
                            if isinstance(event, dict) and "hazard_event" in event:
                                malfunctions.append(event["hazard_event"])
                            elif isinstance(event, str):
                                malfunctions.append(event)
                    elif isinstance(events, str):
                        malfunctions.append(events)
            result[func] = malfunctions
        return result

    def _merge_refined_into_function_scenarios(
        self,
        function_scenarios: Dict,
        refined_function_scenarios: Dict
    ) -> Dict[str, Any]:
        """将 Step7 细化的场景字段合并到 function_scenarios 中。

        Step6 输出的 function_scenarios 包含 {scenario_summary, scenario, hazard_event, risk_level}。
        Step7 输出的 refined_function_scenarios 包含 {refined_scenario, vehicle_state,
        vehicle_speed, weather_conditions, road_surface_conditions, hazard_event}。

        合并后每条场景条目额外包含 Step7 的细化字段，供 report_generator 填充 G/H 列使用。

        Args:
            function_scenarios: Step6 输出的 function_scenarios
            refined_function_scenarios: Step7 输出的 refined_function_scenarios

        Returns:
            已合并细化字段的 function_scenarios
        """
        for func, malf_scenarios in function_scenarios.items():
            ref_malf_scenarios = refined_function_scenarios.get(func, {})
            for malf, scenarios in malf_scenarios.items():
                ref_scenarios = ref_malf_scenarios.get(malf, [])
                for sc in scenarios:
                    scenario_id = sc.get("scenario_id", "")
                    sc_main_raw = sc.get("scenario_summary", sc.get("scenario", ""))
                    if isinstance(sc_main_raw, dict):
                        sc_main_raw = str(sc_main_raw)
                    sc_main = sc_main_raw.split("，")[0].strip().split("[")[0].strip()
                    for ref_sc in ref_scenarios:
                        if scenario_id and ref_sc.get("scenario_id") != scenario_id:
                            continue
                        orig = ref_sc.get("original_scenario", "")
                        if isinstance(orig, dict):
                            ref_main = str(orig.get("scenario", "")).split("，")[0].strip().split("[")[0].strip()
                        elif isinstance(orig, str) and orig.strip().startswith("{"):
                            try:
                                parsed = ast.literal_eval(orig)
                                ref_main = str(parsed.get("scenario", orig)).split("，")[0].strip().split("[")[0].strip()
                            except (ValueError, SyntaxError):
                                ref_main = orig.split("，")[0].strip().split("[")[0].strip()
                        else:
                            ref_main = str(orig).split("，")[0].strip().split("[")[0].strip()
                        if scenario_id or (sc_main and sc_main == ref_main):
                            rs_val = ref_sc.get("refined_scenario", "")
                            if isinstance(rs_val, str) and rs_val.strip().startswith("{"):
                                try:
                                    rs_dict = ast.literal_eval(rs_val)
                                    if isinstance(rs_dict, dict):
                                        parts = [rs_dict.get("scenario", "")]
                                        for f in ["vehicle_state", "vehicle_speed", "weather_conditions", "road_surface_conditions"]:
                                            v = rs_dict.get(f, "")
                                            if v and v != "N/A":
                                                parts.append(f"{f}={v}")
                                        rs_val = f"{parts[0]} [{', '.join(parts[1:])}]"
                                except (ValueError, SyntaxError):
                                    pass
                            if not isinstance(rs_val, str) or len(rs_val) < 5:
                                rs_val = sc.get("scenario_summary", "")
                            sc["situational_description"] = self._build_situational_summary(ref_sc)
                            sc["situational_detailing"] = self._build_situational_detailing(ref_sc)
                            break
                    if "situational_description" not in sc:
                        fallback_raw = sc.get("situational_description", sc.get("scenario_summary", ""))
                        if isinstance(fallback_raw, dict):
                            fallback_raw = str(fallback_raw)
                        # Build summary from raw text (no ref_sc available for detailing)
                        sc["situational_description"] = self._fallback_summarize(fallback_raw)
                        sc["situational_detailing"] = "N/A"
        return function_scenarios

    def _fallback_summarize(self, raw: str) -> str:
        """当没有 ref_sc 时，从 scenario_summary 中提取关键词摘要。"""
        if not raw or raw == "N/A":
            return raw or "N/A"
        text = str(raw).strip()
        main = text.split("，")[0].split(",")[0].strip()
        main = self._shorten_scenario_name(main)
        parts = [main] if main else []
        # Extract Chinese-labeled values
        for label in ["车辆状态", "车辆速度", "天气情况", "路面情况", "车速", "天气", "路面"]:
            m = re.search(rf'{label}[=：]\s*([^，,；;]+)', text)
            if m:
                parts.append(self._shorten_value(m.group(1).strip()))
        return ", ".join(parts) if parts else text[:100]

    @staticmethod
    def _shorten_scenario_name(raw: str) -> str:
        """将完整场景名缩写为关键词形式。"""
        if not raw:
            return ""
        main_text = str(raw).split('[')[0].strip()
        SCEN_MAP = {
            "Highway / Motorway": "Highway",
            "Urban Road / City Street": "Urban Road",
            "Rural Road / Country Lane": "Rural Road",
            "Railway Crossing / Crossroad": "Railway Crossing",
            "Montain road": "Mountain Road",
            "Parking Lot / Garage": "Parking Lot",
            "Service Area / Rest Stop": "Service Area",
            "Maintenance": "Maintenance",
            "Tunnel": "Tunnel",
            "Residential Area / School Zone": "Residential Area",
            "Off-road / Unpaved Path": "Off-road",
        }
        for full_name, short in SCEN_MAP.items():
            if full_name in main_text:
                main_text = short
                break
        # Remove Chinese translations in parentheses
        for paren in ["(高速公路)", "(城市道路)", "(乡村道路)", "(铁路道口/交叉路口)",
                       "（山路）", "(停车场/库)", "(服务区/休息站)", "(维修车间)",
                       "(隧道)", "(居民区/学校区域)", "(越野/非铺装路面)"]:
            main_text = main_text.replace(paren, "")
        return main_text.strip()

    @staticmethod
    def _shorten_value(val: str) -> str:
        """将维度值缩写为关键词（去掉中文翻译）。"""
        if not val:
            return ""
        # Take English part before Chinese translation
        short = val.split(' (')[0].split(' / ')[0].strip()
        return short if short else val

    def _build_situational_summary(self, ref_scenario: Dict) -> str:
        """构建 G 列场景概述：关键词逗号分隔的紧凑格式。"""
        parts = []
        raw_main = ref_scenario.get("original_scenario", "")
        main = self._shorten_scenario_name(raw_main)
        if main and main.lower() != "n/a":
            parts.append(main)
        for dim in [
            "vehicle_state", "vehicle_speed", "maneuver", "parking_direction",
            "parking_space_type", "object_type", "object_position",
            "relative_distance", "slope", "driver_state",
            "weather_conditions", "road_surface_conditions",
        ]:
            val = ref_scenario.get(dim, "")
            if val and val != "N/A" and "all" not in str(val).lower():
                short = self._shorten_value(val)
                if short:
                    parts.append(short)
        return ", ".join(parts) if parts else str(raw_main)[:100]

    def _build_situational_detailing(self, ref_scenario: Dict) -> str:
        """构建 H 列场景详情：中文标签 + 完整维度值的详细格式。"""
        parts = []
        for dim, label in [
            ("vehicle_state", "车辆状态"),
            ("vehicle_speed", "车速"),
            ("maneuver", "泊车阶段"),
            ("parking_direction", "泊入/泊出方向"),
            ("parking_space_type", "车位类型"),
            ("object_type", "关键目标物"),
            ("object_position", "目标物位置"),
            ("relative_distance", "初始距离"),
            ("relative_speed_kph", "相对速度(km/h)"),
            ("longitudinal_acceleration_mps2", "纵向加速度(m/s²)"),
            ("acceleration_mode", "加减速依据"),
            ("steering_angle_deg", "目标转角(°)"),
            ("steering_angle_status", "目标转角状态"),
            ("steering_control_error_deg", "转向控制误差(°)"),
            ("slope", "坡度"),
            ("driver_state", "驾驶员状态"),
            ("weather_conditions", "天气"),
            ("road_surface_conditions", "路面"),
        ]:
            val = ref_scenario.get(dim, "")
            if val is not None and val != "" and val != "N/A" and "all" not in str(val).lower():
                parts.append(f"{label}: {val}")
        return "；".join(parts) if parts else "N/A"

    def _load_hazard_event_index(self) -> List[Dict[str, Any]]:
        """从HazardRef.json加载危害事件索引，供_infer_hazard_event匹配使用。"""
        try:
            ref_path = os.path.join(os.path.dirname(__file__), "HazardRef.json")
            if os.path.exists(ref_path):
                with open(ref_path, 'r', encoding='utf-8') as f:
                    ref_data = json.load(f)
                entries = ref_data.get("hazard_event_index", {}).get("entries", [])
                logger.info(f"从HazardRef.json加载了 {len(entries)} 个危害事件索引")
                return entries
        except Exception as e:
            logger.warning(f"加载危害事件索引失败: {e}")
        return []

    def _load_scenario_ref(self) -> Dict[str, Any]:
        """
        从 ScenarioRef.json 加载:
        - main_scenarios: 主场景典型车速映射，供 Step7 使用
        - entries: 场景参考条目（含 subsystem/function/malfunction 关键词），供 Step6 关键词检索使用
        - weather_overlays: 天气覆盖层，供 Step7 天气条件选择使用
        - subsystem_weather_overrides: 子系统天气覆写规则，供 Step7 强制天气使用
        """
        try:
            ref_path = os.path.join(os.path.dirname(__file__), "ScenarioRef.json")
            if os.path.exists(ref_path):
                with open(ref_path, 'r', encoding='utf-8') as f:
                    ref_data = json.load(f)
                main_scenarios = ref_data.get("main_scenarios", [])
                entries = ref_data.get("entries", [])
                weather_overlays = ref_data.get("weather_overlays", [])
                subsystem_weather_overrides = ref_data.get("subsystem_weather_overrides", {})
                logger.info(f"从 ScenarioRef.json 加载了 {len(main_scenarios)} 个主场景车速映射, {len(entries)} 条场景参考, "
                            f"{len(weather_overlays)} 个天气覆盖层, {len(subsystem_weather_overrides)} 个子系统天气覆写")
                return {
                    "main_scenarios": main_scenarios,
                    "entries": entries,
                    "weather_overlays": weather_overlays,
                    "subsystem_weather_overrides": subsystem_weather_overrides
                }
        except Exception as e:
            logger.warning(f"加载 ScenarioRef.json 失败: {e}")
        return {"main_scenarios": [], "entries": [], "weather_overlays": [], "subsystem_weather_overrides": {}}

    def _load_hazard_ref_entries(self) -> List[Dict[str, Any]]:
        """从 HazardRef.json 加载所有危害条目（含 subsystem_keywords / malfunction_keywords / hazards 等）"""
        try:
            ref_path = os.path.join(os.path.dirname(__file__), "HazardRef.json")
            if os.path.exists(ref_path):
                with open(ref_path, 'r', encoding='utf-8') as f:
                    ref_data = json.load(f)
                entries = ref_data.get("entries", [])
                logger.info(f"从 HazardRef.json 加载了 {len(entries)} 个危害条目")
                return entries
        except Exception as e:
            logger.warning(f"加载 HazardRef 条目失败: {e}")
        return []

    def _apply_weather_override(self, supplementary_values: Dict[str, set]) -> None:
        """
        根据子系统天气覆写规则修改天气条件。
        仅当 subsystem 在 ScenarioRef.json 的 subsystem_weather_overrides 中且有 force_weather=true
        时才生效。
        """
        scenario_ref = getattr(self, "scenario_ref", {})
        if not scenario_ref:
            return

        overrides = scenario_ref.get("subsystem_weather_overrides", {})
        weather_overlays = scenario_ref.get("weather_overlays", [])
        if not overrides or not weather_overlays:
            return

        sub = getattr(self, "subsystem", "")
        if not sub:
            return

        # Find matching override (case-insensitive)
        override = None
        for key, val in overrides.items():
            if key.lower() == sub.lower():
                override = val
                break

        if not override or not override.get("force_weather", False):
            return

        allowed_ids = override.get("allowed_weather_ids", [])
        excluded_ids = set(override.get("excluded_weather_ids", []))

        forced_weathers = []
        for we in weather_overlays:
            wid = we.get("id", "")
            if wid in allowed_ids and wid not in excluded_ids:
                # Use Chinese name (name field contains both CN and EN)
                forced_weathers.append(str(we.get("name", wid)))

        if forced_weathers:
            old_vals = supplementary_values.get("weather_conditions", set())
            supplementary_values["weather_conditions"] = set(forced_weathers)
            logger.info(f"  [Weather Override] subsystem={self.subsystem}: "
                        f"天气条件从 {{{', '.join(old_vals)}}} 覆写为 {{{', '.join(forced_weathers)}}}")

        # Also override road surface for weather-dependent subsystems
        # For rain weather, the road surface should be wet
        default_weather_id = override.get("default_weather", "")
        for we in weather_overlays:
            if we.get("id") == default_weather_id:
                road_surface = we.get("road_surface", "")
                if road_surface:
                    old_rs = supplementary_values.get("road_surface_conditions", set())
                    # Filter out dry surfaces when weather is rain
                    new_rs = set()
                    for rs in old_rs:
                        rs_lower = str(rs).lower()
                        if "dry" in rs_lower or "干燥" in rs_lower:
                            continue
                        new_rs.add(rs)
                    if not new_rs:
                        new_rs.add(road_surface)
                    if new_rs != old_rs:
                        supplementary_values["road_surface_conditions"] = new_rs
                        logger.info(f"  [Road Surface Override] 从 {{{', '.join(old_rs)}}} "
                                    f"调整为 {{{', '.join(new_rs)}}}")
                break

    def _find_scenarios_by_keywords(self, malfunction: str = "", function: str = "", subsystem: str = "") -> List[Dict[str, Any]]:
        """
        基于子系统/功能/malfunction 关键词在 ScenarioRef.json 中检索相关场景。
        返回 [entry, ...]，按匹配分数降序排列。
        """
        combined = f"{subsystem} {function} {malfunction}".lower()
        tokens = set(combined.split())
        if not tokens:
            return []

        matched = []
        for entry in self.scenario_ref.get("entries", []):
            entry_kws = []
            for kw_field in ["subsystem_keywords", "function_keywords", "malfunction_keywords", "hazard_keywords"]:
                entry_kws.extend(entry.get(kw_field, []))
            entry_set = set(" ".join(entry_kws).lower().split())
            overlap = tokens & entry_set
            if overlap:
                score = len(overlap)
                sub_overlap = set(" ".join(entry.get("subsystem_keywords", [])).lower().split()) & tokens
                func_overlap = set(" ".join(entry.get("function_keywords", [])).lower().split()) & tokens
                malf_overlap = set(" ".join(entry.get("malfunction_keywords", [])).lower().split()) & tokens
                score += len(sub_overlap) * 2 + len(func_overlap) * 2 + len(malf_overlap)
                matched.append((score, entry))

        matched.sort(key=lambda x: x[0], reverse=True)
        logger.info(f"  ScenarioRef 关键词匹配: {len(matched)} 个条目 (subsystem={subsystem}, function={function})")
        return [m[1] for m in matched]

    def _get_all_main_scenarios(self) -> List[Dict[str, Any]]:
        """
        从 scenarios_library 中提取 11 个主场景（按 Operating Scenario 去重）。
        """
        if not self.scenarios_library:
            return []
        seen = {}
        for item in self.scenarios_library:
            op = item.get("operating_scenario", item.get("scenario", ""))
            if op and op not in seen:
                seen[op] = item
        return list(seen.values())[:14]

    def _find_hazard_by_keywords(self, malfunction: str = "", function: str = "",
                                  subsystem: str = "", scenario: str = "") -> Optional[Dict[str, Any]]:
        """
        基于子系统/功能/malfunction/场景 关键词在 HazardRef.json entries 中查找危害条目。
        返回最佳匹配条目或其 hazards[0]，无匹配返回 None。
        """
        combined = f"{subsystem} {function} {malfunction} {scenario}".lower()
        if not combined.strip():
            return None

        def matched_keywords(keywords: List[str], source: str) -> List[str]:
            matches = []
            source_lower = str(source or "").lower()
            for keyword in keywords or []:
                kw = str(keyword or "").strip().lower()
                if len(kw) >= 2 and kw in source_lower:
                    matches.append(kw)
            return matches

        best_score = 0
        best_entry = None
        for entry in self.hazard_ref_entries:
            sub_matches = matched_keywords(entry.get("subsystem_keywords", []), subsystem)
            func_matches = matched_keywords(entry.get("function_keywords", []), function)
            malf_matches = matched_keywords(entry.get("malfunction_keywords", []), malfunction)
            ctx_matches = matched_keywords(entry.get("context_keywords", []), scenario)

            # A subsystem/ODD word such as "parking" is not sufficient.  A
            # reference entry must also match the function or malfunction.
            if not malf_matches and not func_matches:
                continue
            if subsystem and entry.get("subsystem_keywords") and not sub_matches:
                continue

            score = (
                len(sub_matches) * 2
                + len(func_matches) * 3
                + len(malf_matches) * 4
                + len(ctx_matches)
            )

            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry and best_score >= 3:
            hazards = best_entry.get("hazards", [])
            if hazards:
                return hazards[0]
            return best_entry
        return None

    def _get_ref_speeds(self, main_scenario: str) -> List[str]:
        """
        两层逻辑查找典型车速：
        Layer1: 优先从 ScenarioRef.json 的 main_scenarios 中查找（典型值，精度高）
        Layer2: 备用从 scenarios_library 模板中提取（实际数据）
        """
        if not main_scenario or not isinstance(main_scenario, str):
            return []
        
        ms = main_scenario.strip()
        if not ms:
            return []
        
        # === Layer 1: ScenarioRef ===
        ref_scenarios = self.scenario_ref.get("main_scenarios", [])
        if ref_scenarios:
            # 1a. Exact match
            for r in ref_scenarios:
                rname = r.get("name", "").strip()
                if ms == rname:
                    speeds = r.get("vehicle_speed", [])
                    if speeds:
                        return speeds
                    break
            
            # 1b. Scenario name contains ref name (e.g., "Highway / Motorway" contains "Highway")
            for r in ref_scenarios:
                rname = r.get("name", "").strip()
                if rname and rname in ms:
                    speeds = r.get("vehicle_speed", [])
                    if speeds:
                        return speeds
                    break
            
            # 1c. Ref name contains scenario name (e.g., "Tunnel (隧道)" contains "Tunnel")
            ms_upper = ms.upper()
            for r in ref_scenarios:
                rname = r.get("name", "").strip()
                if rname and rname.upper().startswith(ms_upper):
                    speeds = r.get("vehicle_speed", [])
                    if speeds:
                        return speeds
                    break
            
            # 1d. Keyword token match — split both on / space / parentheses, find overlap >= 1 meaningful token
            ms_tokens = set(tok.lower().strip(" ()（）,;/-") for tok in re.split(r'[\s()/（）,;\-]+', ms) if tok.strip())
            for r in ref_scenarios:
                rname = r.get("name", "").strip()
                r_tokens = set(tok.lower().strip(" ()（）,;/-") for tok in re.split(r'[\s()/（）,;\-]+', rname) if tok.strip())
                overlap = ms_tokens & r_tokens
                if overlap and len(overlap) >= 1:
                    speeds = r.get("vehicle_speed", [])
                    if speeds:
                        return speeds
                    break
        
        # === Layer 2: scenarios_library fallback ===
        if hasattr(self, 'scenarios_library') and self.scenarios_library:
            for item in self.scenarios_library:
                item_sc = item.get("scenario", item.get("operating_scenario", ""))
                if item_sc and isinstance(item_sc, str) and item_sc.strip() == ms:
                    dim_val = item.get("vehicle_speed")
                    if dim_val:
                        return [str(dim_val)]
                    break
            # Also try partial match in library
            for item in self.scenarios_library:
                item_sc = item.get("scenario", item.get("operating_scenario", ""))
                if item_sc and isinstance(item_sc, str):
                    item_tokens = set(tok.lower().strip(" ()（）,;/-") for tok in re.split(r'[\s()/（）,;\-]+', item_sc) if tok.strip())
                    if item_tokens & ms_tokens and len(item_tokens & ms_tokens) >= 1:
                        dim_val = item.get("vehicle_speed")
                        if dim_val:
                            return [str(dim_val)]
                        break
        
        return []

    def _is_avp_context(self, function: str = "") -> bool:
        """Detect an AVP item without relying on a particular input filename."""
        functions = []
        if isinstance(self.input_data, dict):
            functions = self.input_data.get("functions", []) or []
        text = " ".join([
            str(self.subsystem or ""), str(self.source_document or ""), str(function or ""),
            *[str(item) for item in functions],
        ]).lower()
        return self.avp_policy.matches(text)

    def _build_avp_representative_scenario(self, function: str, malfunction: str) -> Dict[str, Any]:
        """Build the common AVP scenario facts used by candidate risk scenarios.

        This method is retained as the base-scenario constructor for compatibility.
        It must not be interpreted as a policy that limits a malfunction to one
        HARA scenario; `_build_avp_candidate_scenarios` expands the rating-relevant
        object/speed/distance variants before Phase 3 scoring.
        """
        text = f"{function} {malfunction}"
        policy = self.domain_profile.section("scenario_policy")
        quantitative = (self.input_data or {}).get("item_semantics", {}).get("quantitative_constraints", [])

        def find_limit(*keywords: str) -> Optional[Dict[str, Any]]:
            for item in quantitative:
                haystack = f"{item.get('category', '')} {item.get('parameter', '')}"
                if all(keyword in haystack for keyword in keywords):
                    return item
            return None

        parking_speed_ref = find_limit("泊车", "最大车速") or find_limit("停车控制", "停车控制")
        parking_speed_kph = float(policy["fallback_parking_speed_kph"])
        if parking_speed_ref:
            match = re.search(r'(\d+(?:\.\d+)?)\s*km/h', parking_speed_ref.get("value", ""), re.IGNORECASE)
            if match:
                parking_speed_kph = float(match.group(1))

        normal_decel_ref = find_limit("减速度", "正常巡航")
        start_accel_ref = find_limit("起步加速度", "起步加速度")
        steering_error_ref = find_limit("转向控制", "转向控制")
        if "驻车制动" in text:
            vehicle_state = "坡道泊车完成，车辆静止且驾驶员已离车"
            vehicle_speed = "0 km/h / Standstill"
            maneuver = "驻车保持"
            parking_direction = "坡道泊车完成"
            object_position = "车辆下坡方向"
            slope = "8%下坡"
        elif any(token in function for token in ("开启", "激活")):
            vehicle_state = "车辆静止，用户准备启动AVP"
            vehicle_speed = "0 km/h / Standstill"
            maneuver = "功能激活"
            parking_direction = "泊入前"
            object_position = "车辆前后方"
            slope = "0%"
        else:
            vehicle_state = "AVP低速车尾泊入过程中，驾驶员位于车外"
            vehicle_speed = f"{parking_speed_kph:g} km/h (Item Definition泊车上限)"
            maneuver = "低速泊车运动"
            parking_direction = "车尾泊入"
            object_position = "车辆预计轨迹前方或侧后方"
            slope = "0%～8%"

        is_standstill = vehicle_speed.startswith("0 ")
        if "驱动扭矩" in text:
            longitudinal_acceleration = float(policy["drive_acceleration_mps2"])
            acceleration_mode = "起步/低速驱动加速度上限"
            acceleration_ref = start_accel_ref
        elif "制动" in text:
            longitudinal_acceleration = float(policy["brake_deceleration_mps2"])
            acceleration_mode = "正常泊车制动减速度上限"
            acceleration_ref = normal_decel_ref
        else:
            longitudinal_acceleration = 0.0
            acceleration_mode = "代表场景未施加纵向加减速"
            acceleration_ref = None

        if "转向" in text:
            object_type = "柱体或墙体"
        elif is_standstill:
            object_type = "相邻车辆或墙体"
        else:
            object_type = "儿童行人"

        contexts = policy.get("driver_context_candidates", [])
        desired_position = "outside" if any(
            token in vehicle_state for token in ("车外", "离车")
        ) else "inside"
        driver_context = next(
            (item for item in contexts if item.get("driver_position") == desired_position),
            contexts[0] if contexts else {},
        )

        return {
            "scenario": policy["scenario_name"],
            "operating_scenario": policy["scenario_name"],
            "vehicle_state": vehicle_state,
            "vehicle_speed": vehicle_speed,
            "weather_conditions": policy["weather_conditions"],
            "road_surface_conditions": policy["road_surface_conditions"],
            "operating_mode": maneuver,
            "maneuver": maneuver,
            "parking_direction": parking_direction,
            "parking_space_type": policy["parking_space_type"],
            "object_type": object_type,
            "object_position": object_position,
            "relative_distance": policy["default_relative_distance"],
            "slope": slope,
            "driver_context_id": driver_context.get("context_id", ""),
            "driver_position": driver_context.get("driver_position", ""),
            "driver_state": driver_context.get("driver_state", ""),
            "direct_vehicle_control": driver_context.get("direct_vehicle_control"),
            "intervention_channels": driver_context.get("intervention_channels", []),
            "ego_speed_kph": 0.0 if is_standstill else parking_speed_kph,
            "relative_speed_kph": 0.0 if is_standstill else parking_speed_kph,
            "relative_speed_basis": "目标物静止，取自车接近速度" if not is_standstill else "自车与目标物均静止",
            "longitudinal_acceleration_mps2": longitudinal_acceleration,
            "acceleration_mode": acceleration_mode,
            "steering_angle_deg": None,
            "steering_angle_status": "源文档未给出方向盘/车轮目标转角，不自动推断",
            "steering_control_error_deg": policy["steering_control_error_deg"] if "转向" in text else None,
            "parameter_sources": {
                "vehicle_speed": parking_speed_ref.get("id") if parking_speed_ref else None,
                "longitudinal_acceleration": acceleration_ref.get("id") if acceleration_ref else None,
                "steering_control_error": steering_error_ref.get("id") if steering_error_ref else None,
            },
            "coverage_type": "representative",
            "odd_valid": True,
            "selection_reason": "AVP ODD内代表场景；保留会影响危害或S/E/C判定的参数",
        }

    def _build_avp_candidate_scenarios(self, function: str,
                                       malfunction: str) -> List[Dict[str, Any]]:
        """Return ODD-valid AVP candidates that may lead to distinct risk results.

        Candidate generation varies only facts that can change the hazardous event
        or S/E/C result (object, collision geometry, relative speed and distance).
        One to three candidates is a review target, not a hard limit: additional
        candidates are retained when they represent a distinct risk class.  Phase 3
        performs the final risk-equivalence grouping after scoring.
        """
        base = self._build_avp_representative_scenario(function, malfunction)
        text = f"{function} {malfunction}".lower()
        drive_degradation = (
            "驱动扭矩" in text
            and any(token in text for token in (
                "未输出", "丢失", "过小", "过晚", "过慢", "过短", "不完整"
            ))
        )

        # Only failures that cannot initiate vehicle motion are collapsed to the
        # controlled-standstill availability case.  Drive-torque degradation is
        # deliberately excluded: while parking it can also produce an unexpected
        # stop or rollback, so its moving risk candidates must reach S/E/C scoring.
        availability_only = (
            (any(token in text for token in ("开启功能", "激活功能"))
                and any(token in text for token in (
                    "未输出", "丢失", "过晚", "过慢", "过短", "不完整"
                )))
            or ("报警提示" in text and any(token in text for token in (
                "非预期", "持续激活", "过早", "过快"
            )))
        )

        def variant(**overrides: Any) -> Dict[str, Any]:
            item = dict(base)
            item.update(overrides)
            item["coverage_type"] = "risk_candidate"
            item["odd_valid"] = True
            return item

        ego_speed_kph = float(base.get("ego_speed_kph", 0.0) or 0.0)

        def policy_variant(candidate_id: str, **overrides: Any) -> Dict[str, Any]:
            candidate = self.avp_policy.candidate(candidate_id, ego_speed_kph)
            candidate.update(overrides)
            return variant(**candidate)

        if availability_only:
            availability_overrides = {}
            if any(token in text for token in ("开启功能", "激活功能")):
                availability_overrides = {
                    "vehicle_state": "车辆静止，AVP无法开始或继续泊车运动",
                    "vehicle_speed": "0 km/h / Standstill",
                    "maneuver": "安全静止",
                    "parking_direction": "无泊车运动",
                    "ego_speed_kph": 0.0,
                    "longitudinal_acceleration_mps2": 0.0,
                    "acceleration_mode": "可用性失效场景不施加纵向加减速",
                    "slope": "0%",
                }
            return [policy_variant(
                "controlled_standstill",
                selection_reason="可用性失效：车辆保持静止，仅保留无人员伤害风险类别",
                **availability_overrides,
            )]

        candidates = [
            policy_variant("near_pedestrian"),
            policy_variant("near_vehicle_10"),
            policy_variant("near_vehicle_30"),
        ]

        # A degraded drive-torque command has two materially different outcomes:
        # the vehicle may remain safely stopped, or it may decelerate unexpectedly /
        # roll back while already moving. Keep the QM baseline without allowing it
        # to suppress the moving safety-relevant path.
        if drive_degradation:
            candidates.insert(0, policy_variant(
                "controlled_standstill",
                vehicle_state="车辆静止，AVP无法开始或继续泊车运动",
                vehicle_speed="0 km/h / Standstill",
                maneuver="安全静止",
                parking_direction="无泊车运动",
                ego_speed_kph=0.0,
                longitudinal_acceleration_mps2=0.0,
                acceleration_mode="可用性失效场景不施加纵向加减速",
                slope="0%",
                selection_reason="驱动扭矩退化的安全静止基线；与运动中异常减速/回退风险分开评分",
            ))

        # A fixed-object collision is a separate engineering risk class from a
        # pedestrian or vehicle collision.  It is especially relevant to AVP
        # motion-control functions in narrow parking spaces and must not be dropped
        # merely because the default review target has already reached three rows.
        if any(token in text for token in ("制动扭矩", "驱动扭矩", "转向扭矩", "驻车制动")):
            steering_related = "转向" in text
            candidate_id = "lateral_fixed_object" if steering_related else "longitudinal_fixed_object"
            candidates.append(policy_variant(candidate_id))

        return candidates

    @staticmethod
    def _canonical_scenario_facts(scenario: Dict[str, Any]) -> Dict[str, Any]:
        fields = (
            "operating_scenario", "vehicle_state", "vehicle_speed", "weather_conditions",
            "road_surface_conditions", "operating_mode", "maneuver", "parking_direction",
            "parking_space_type", "object_type", "object_position", "relative_distance",
            "slope", "driver_context_id", "driver_position", "driver_state",
            "direct_vehicle_control", "intervention_channels", "ego_speed_kph", "relative_speed_kph",
            "relative_speed_basis", "longitudinal_acceleration_mps2", "acceleration_mode",
            "steering_angle_deg", "steering_angle_status", "steering_control_error_deg",
            "parameter_sources", "coverage_type", "odd_valid", "selection_reason",
            "scenario_variant", "target_speed_kph", "collision_geometry",
            "exposure_level_candidate", "exposure_method", "exposure_basis",
        )
        return {field: scenario.get(field) for field in fields}

    @classmethod
    def _make_scenario_id(cls, scenario: Dict[str, Any]) -> str:
        facts = cls._canonical_scenario_facts(scenario)
        canonical = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "SCN_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12].upper()

    def _build_scenario_catalog(self, function_scenarios: Dict[str, Any]) -> Dict[str, Any]:
        catalog: Dict[str, Any] = {}
        for malfunctions in function_scenarios.values():
            for scenarios in malfunctions.values():
                for scenario in scenarios:
                    scenario_id = scenario.get("scenario_id")
                    if not scenario_id:
                        continue
                    facts = dict(scenario.get("scenario_facts", {}))
                    facts["scenario_id"] = scenario_id
                    facts["situational_description"] = scenario.get("situational_description", "")
                    facts["situational_detailing"] = scenario.get("situational_detailing", "")
                    catalog[scenario_id] = facts
        return catalog

    @staticmethod
    def _scenario_identity(scenario: Dict[str, Any]) -> tuple:
        return tuple(str(scenario.get(key, "")).strip().lower() for key in (
            "operating_scenario", "scenario", "vehicle_state", "vehicle_speed",
            "maneuver", "parking_direction", "parking_space_type", "object_type",
            "object_position", "relative_distance", "slope", "driver_state",
            "weather_conditions", "road_surface_conditions"
        ))

    def _select_relevant_scenarios(
        self,
        function: str,
        malfunction: str,
        all_main_scenarios: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Select ODD/function-relevant scenarios before combination.

        AVP uses rating-relevant candidates and lets Phase 3 group equivalent risk
        results.  Other subsystems retain every explicitly matched ScenarioRef
        requirement.  Only the no-evidence fallback is limited to three examples;
        the old behaviour of supplementing every template scenario remains removed.
        """
        if self._is_avp_context(function):
            return self._build_avp_candidate_scenarios(function, malfunction)

        matched_entries = self._find_scenarios_by_keywords(
            malfunction=malfunction,
            function=function,
            subsystem=self.subsystem,
        )
        candidates = []
        for entry in matched_entries or []:
            for required in entry.get("required_scenarios", []):
                name = str(required.get("scenario", "")).strip()
                if not name:
                    continue
                library_item = next((
                    item for item in self.scenarios_library
                    if name.lower() in str(item.get("scenario", "")).lower()
                    or name.lower() in str(item.get("operating_scenario", "")).lower()
                ), None)
                candidates.append(library_item or {
                    "scenario": name,
                    "operating_scenario": name,
                    "coverage_type": "reference",
                    "selection_reason": f"ScenarioRef:{entry.get('id', '')}",
                })

        if not candidates:
            fallback_pool = all_main_scenarios or self.scenarios_library
            candidates = list(fallback_pool[:3])

        selected = []
        seen = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                candidate = {"scenario": str(candidate), "operating_scenario": str(candidate)}
            name = str(candidate.get("operating_scenario", candidate.get("scenario", "")))
            if not name or "all opeating" in name.lower() or "all operating" in name.lower():
                continue
            identity = self._scenario_identity(candidate)
            if identity in seen:
                continue
            seen.add(identity)
            selected.append(candidate)
        return selected

    def _step6_combine_scenarios(self, function_malfunctions: Dict) -> Dict[str, Any]:
        """
        Step6: 先筛选适用失效和相关场景，再生成代表性组合。

        场景选择策略:
        1. not_applicable/duplicate 不进入Phase 2
        2. 场景必须满足Item ODD和功能相关性
        3. 每个失效默认审阅目标为1～3个；会改变危害或S/E/C的风险场景允许超过3个
        """
        logger.info("[Step6] 开始组合场景 (适用性与ODD筛选模式)")

        all_main_scenarios = self._get_all_main_scenarios()
        function_scenarios = {}
        total_combinations = 0
        skipped_malfunctions = 0
        rejected_combinations = 0
        rejected_combination_audit = []

        for func, malfunctions in function_malfunctions.items():
            if isinstance(malfunctions, dict):
                malf_items = [m for m in malfunctions.values() if m]
            elif isinstance(malfunctions, list):
                malf_items = [m for m in malfunctions if m]
            else:
                malf_items = [malfunctions]

            if not malf_items:
                continue

            malfunction_scenarios = {}
            for malf_item in malf_items:
                if isinstance(malf_item, dict):
                    malf = malf_item.get("malfunction", malf_item.get("malfunction_description", str(malf_item)))
                    guideword = malf_item.get("guideword", "")
                    analysis_status = malf_item.get("analysis_status", "applicable")
                else:
                    malf = str(malf_item)
                    guideword = ""
                    analysis_status = "applicable"

                if analysis_status != "applicable" or "不适用" in malf:
                    skipped_malfunctions += 1
                    continue

                related_scenarios = self._select_relevant_scenarios(func, malf, all_main_scenarios)
                logger.info(f"  功能'{func}' / '{malf}': 选择 {len(related_scenarios)} 个相关场景")

                composite_key = f"{malf}||{guideword}" if guideword else malf
                malfunction_scenarios[composite_key] = []

                # 组合: 当前 malfunction × 所有 related_scenarios
                for sc_idx, sc in enumerate(related_scenarios):
                    if isinstance(sc, dict):
                        sc_name = sc.get("scenario") or sc.get("operating_scenario") or str(sc)
                    else:
                        sc_name = str(sc)

                    if self._is_avp_context(func) and isinstance(sc, dict) and not sc.get("odd_valid", False):
                        rejected_combinations += 1
                        rejected_combination_audit.append({
                            "function": func,
                            "malfunction": malf,
                            "guideword": guideword,
                            "scenario": sc_name,
                            "scenario_variant": sc.get("scenario_variant", ""),
                            "reasons": ["候选场景未通过AVP ODD约束"],
                        })
                        continue

                    results = self.checker.check_multiple_scenario_combinations([malf], [sc])
                    valid = self.checker.filter_valid_combinations(results)

                    if valid:
                        for r in valid:
                            scenario_facts = self._canonical_scenario_facts(sc) if isinstance(sc, dict) else {}
                            scenario_id = self._make_scenario_id(sc) if isinstance(sc, dict) else ""
                            if isinstance(sc, dict):
                                for audit_field in ("engineering_status", "rule_version", "review_reason"):
                                    if sc.get(audit_field):
                                        scenario_facts[audit_field] = sc[audit_field]
                            malfunction_scenarios[composite_key].append({
                                "scenario_id": scenario_id,
                                "scenario_facts": scenario_facts,
                                "scenario": sc_name,
                                "scenario_summary": r["scenario_summary"],
                                "hazard_event": r["hazard_event"],
                                "risk_level": r["check_result"]["risk_level"],
                                "_guideword": guideword,
                                "_malf_desc": malf,
                                "_scenario_idx": sc_idx,
                                "_main_scenario": sc.get("operating_scenario", sc.get("scenario", "")) if isinstance(sc, dict) else sc_name,
                                "vehicle_state": sc.get("vehicle_state", "") if isinstance(sc, dict) else "",
                                "vehicle_speed": sc.get("vehicle_speed", "") if isinstance(sc, dict) else "",
                                "weather_conditions": sc.get("weather_conditions", "") if isinstance(sc, dict) else "",
                                "road_surface_conditions": sc.get("road_surface_conditions", "") if isinstance(sc, dict) else "",
                                "maneuver": sc.get("maneuver", "") if isinstance(sc, dict) else "",
                                "parking_direction": sc.get("parking_direction", "") if isinstance(sc, dict) else "",
                                "parking_space_type": sc.get("parking_space_type", "") if isinstance(sc, dict) else "",
                                "object_type": sc.get("object_type", "") if isinstance(sc, dict) else "",
                                "object_position": sc.get("object_position", "") if isinstance(sc, dict) else "",
                                "relative_distance": sc.get("relative_distance", "") if isinstance(sc, dict) else "",
                                "slope": sc.get("slope", "") if isinstance(sc, dict) else "",
                                "driver_state": sc.get("driver_state", "") if isinstance(sc, dict) else "",
                                "coverage_type": sc.get("coverage_type", "") if isinstance(sc, dict) else "",
                                "selection_reason": sc.get("selection_reason", "") if isinstance(sc, dict) else "",
                                "ego_speed_kph": sc.get("ego_speed_kph") if isinstance(sc, dict) else None,
                                "relative_speed_kph": sc.get("relative_speed_kph") if isinstance(sc, dict) else None,
                                "relative_speed_basis": sc.get("relative_speed_basis", "") if isinstance(sc, dict) else "",
                                "longitudinal_acceleration_mps2": sc.get("longitudinal_acceleration_mps2") if isinstance(sc, dict) else None,
                                "acceleration_mode": sc.get("acceleration_mode", "") if isinstance(sc, dict) else "",
                                "steering_angle_deg": sc.get("steering_angle_deg") if isinstance(sc, dict) else None,
                                "steering_angle_status": sc.get("steering_angle_status", "") if isinstance(sc, dict) else "",
                                "steering_control_error_deg": sc.get("steering_control_error_deg") if isinstance(sc, dict) else None,
                                "parameter_sources": sc.get("parameter_sources", {}) if isinstance(sc, dict) else {},
                                "scenario_variant": sc.get("scenario_variant", "") if isinstance(sc, dict) else "",
                                "target_speed_kph": sc.get("target_speed_kph") if isinstance(sc, dict) else None,
                                "collision_geometry": sc.get("collision_geometry", "") if isinstance(sc, dict) else "",
                                "exposure_level_candidate": sc.get("exposure_level_candidate", "") if isinstance(sc, dict) else "",
                                "exposure_method": sc.get("exposure_method", "") if isinstance(sc, dict) else "",
                                "exposure_basis": sc.get("exposure_basis", "") if isinstance(sc, dict) else "",
                            })
                            total_combinations += 1
                    else:
                        # A rejected combination must not be silently restored
                        # with a fabricated default hazard event.
                        rejected_combinations += 1
                        rejected_combination_audit.append({
                            "function": func,
                            "malfunction": malf,
                            "guideword": guideword,
                            "scenario": sc_name,
                            "scenario_variant": sc.get("scenario_variant", "") if isinstance(sc, dict) else "",
                            "reasons": [
                                reason
                                for result in results
                                for reason in result.get("check_result", {}).get("reasons", [])
                            ],
                        })

            function_scenarios[func] = malfunction_scenarios

        logger.info(f"[Step6] 完成: 生成 {total_combinations} 个有效组合")

        return {
            "success": True,
            "function_scenarios": function_scenarios,
            "total_combinations": total_combinations,
            "skipped_malfunctions": skipped_malfunctions,
            "rejected_combinations": rejected_combinations,
            "rejected_combination_audit": rejected_combination_audit,
        }

    def _step7_refine_scenarios(self, function_scenarios: Dict) -> Dict[str, Any]:
        """
        Step7: 细化场景
        补充车辆状态、车速、天气、路面条件等维度
        """
        logger.info("[Step7] 开始细化场景")

        import itertools

        supplementary_dims = {
            "vehicle_state": ("C", "车辆状态"),
            "vehicle_speed": ("D", "车辆速度"),
            "weather_conditions": ("E", "天气情况"),
            "road_surface_conditions": ("F", "路面情况")
        }

        supplementary_values = {dim: set() for dim in supplementary_dims.keys()}

        for item in self.scenarios_library:
            for dim in supplementary_dims.keys():
                if dim in item and item[dim]:
                    supplementary_values[dim].add(str(item[dim]))

        for dim in supplementary_values:
            if not supplementary_values[dim]:
                supplementary_values[dim].add("N/A")

        # Apply subsystem weather overrides from ScenarioRef.json
        self._apply_weather_override(supplementary_values)

        logger.info(f"  补充维度: {[(k, len(v)) for k, v in supplementary_values.items()]}")

        refined_function_scenarios = {}
        total_refined = 0
        MAX_REFINED_PER_MALF = 10

        for func, malf_scenarios in function_scenarios.items():
            refined_malf_scenarios = {}

            for malf, scenarios in malf_scenarios.items():
                refined_scenarios = []

                for s in scenarios:
                    main_scenario = s.get("scenario", s.get("scenario_summary", ""))
                    if isinstance(main_scenario, dict):
                        main_scenario = str(main_scenario)

                    library_item = next(
                        (item for item in self.scenarios_library
                         if item.get("scenario") == main_scenario
                         or item.get("operating_scenario") == main_scenario),
                        {}
                    )
                    scenario_source = dict(library_item)
                    for field in (
                        "vehicle_state", "vehicle_speed", "weather_conditions",
                        "road_surface_conditions", "maneuver", "parking_direction",
                        "parking_space_type", "object_type", "object_position",
                        "relative_distance", "slope", "driver_state",
                        "coverage_type", "selection_reason", "ego_speed_kph",
                        "relative_speed_kph", "relative_speed_basis",
                        "longitudinal_acceleration_mps2", "acceleration_mode",
                        "steering_angle_deg", "steering_angle_status",
                        "steering_control_error_deg", "parameter_sources",
                        "scenario_variant", "target_speed_kph", "collision_geometry",
                        "exposure_level_candidate", "exposure_method", "exposure_basis"
                    ):
                        if field in s and s.get(field) is not None and s.get(field) != "":
                            scenario_source[field] = s[field]

                    current_supp_values = {}
                    for dim_key, (col_letter, dim_name) in supplementary_dims.items():
                        if dim_key == "vehicle_speed":
                            if scenario_source.get(dim_key):
                                current_supp_values[dim_name] = [str(scenario_source[dim_key])]
                            else:
                                ref_speeds = self._get_ref_speeds(main_scenario)
                                if ref_speeds:
                                    current_supp_values[dim_name] = ref_speeds
                                else:
                                    all_vals = list(supplementary_values[dim_key])
                                    current_supp_values[dim_name] = all_vals[:2] if all_vals else ["N/A"]
                        else:
                            if scenario_source.get(dim_key):
                                current_supp_values[dim_name] = [str(scenario_source[dim_key])]
                            else:
                                all_vals = list(supplementary_values[dim_key])
                                current_supp_values[dim_name] = all_vals[:2] if all_vals else ["N/A"]

                    keys = list(current_supp_values.keys())
                    values = list(current_supp_values.values())

                    for combination in itertools.product(*values):
                        if len(refined_scenarios) >= MAX_REFINED_PER_MALF:
                            break

                        comb_dict = dict(zip(keys, combination))
                        supp_parts = [f"{dim}={val}" for dim, val in comb_dict.items()]
                        refined_scenario_text = f"{main_scenario} [{', '.join(supp_parts)}]"

                        refined_scenarios.append({
                            "scenario_id": s.get("scenario_id", ""),
                            "scenario_facts": dict(s.get("scenario_facts", {})),
                            "original_scenario": main_scenario,
                            "refined_scenario": refined_scenario_text,
                            "vehicle_state": comb_dict.get("车辆状态", ""),
                            "vehicle_speed": comb_dict.get("车辆速度", ""),
                            "weather_conditions": comb_dict.get("天气情况", ""),
                            "road_surface_conditions": comb_dict.get("路面情况", ""),
                            "maneuver": scenario_source.get("maneuver", ""),
                            "parking_direction": scenario_source.get("parking_direction", ""),
                            "parking_space_type": scenario_source.get("parking_space_type", ""),
                            "object_type": scenario_source.get("object_type", ""),
                            "object_position": scenario_source.get("object_position", ""),
                            "relative_distance": scenario_source.get("relative_distance", ""),
                            "slope": scenario_source.get("slope", ""),
                            "driver_state": scenario_source.get("driver_state", ""),
                            "ego_speed_kph": scenario_source.get("ego_speed_kph"),
                            "relative_speed_kph": scenario_source.get("relative_speed_kph"),
                            "relative_speed_basis": scenario_source.get("relative_speed_basis", ""),
                            "longitudinal_acceleration_mps2": scenario_source.get("longitudinal_acceleration_mps2"),
                            "acceleration_mode": scenario_source.get("acceleration_mode", ""),
                            "steering_angle_deg": scenario_source.get("steering_angle_deg"),
                            "steering_angle_status": scenario_source.get("steering_angle_status", ""),
                            "steering_control_error_deg": scenario_source.get("steering_control_error_deg"),
                            "parameter_sources": scenario_source.get("parameter_sources", {}),
                            "scenario_variant": scenario_source.get("scenario_variant", ""),
                            "target_speed_kph": scenario_source.get("target_speed_kph"),
                            "collision_geometry": scenario_source.get("collision_geometry", ""),
                            "exposure_level_candidate": scenario_source.get("exposure_level_candidate", ""),
                            "exposure_method": scenario_source.get("exposure_method", ""),
                            "exposure_basis": scenario_source.get("exposure_basis", ""),
                            "hazard_event": s.get("hazard_event", "")
                        })
                        total_refined += 1

                    if len(refined_scenarios) >= MAX_REFINED_PER_MALF:
                        break

                refined_malf_scenarios[malf] = refined_scenarios

            refined_function_scenarios[func] = refined_malf_scenarios

        logger.info(f"[Step7] 完成: 细化 {total_refined} 个场景")

        return {
            "success": True,
            "refined_function_scenarios": refined_function_scenarios,
            "total_refined": total_refined
        }

    def _step8_analyze_hazard_events(self, function_hazards: Dict,
                                     function_scenarios: Dict,
                                     refined_function_scenarios: Dict) -> Dict[str, Any]:
        """
        Step8: 分析危害事件
        优先级: Item Definition/模板提取 -> 推理生成
        语义模板: <故障状态>导致<车辆行为>，<人员伤害>
        """
        logger.info("[Step8] 开始分析危害事件")

        hazard_events_results = {}
        total_events = 0

        for func in function_hazards.keys():
            malf_hazard_events = {}
            func_scenarios = function_scenarios.get(func, {})
            func_refined = refined_function_scenarios.get(func, {})

            hazards = function_hazards.get(func, [])
            if not hazards:
                hazards = [{"malfunction": m, "guideword": "", "hazard_description": "", "hazard_category": ""}
                           for m in func_scenarios.keys()]

            for hazard_data in hazards:
                if isinstance(hazard_data, dict):
                    malf = hazard_data.get("malfunction", "")
                    guideword = hazard_data.get("guideword", "")
                    hazard_desc = hazard_data.get("hazard_description") or hazard_data.get("description", "")
                    hazard_category = hazard_data.get("hazard_category") or hazard_data.get("category", "")
                else:
                    malf = str(hazard_data)
                    guideword = ""
                    hazard_desc = ""
                    hazard_category = ""

                composite_key = f"{malf}||{guideword}" if guideword else malf
                malf_scenarios = func_scenarios.get(composite_key, func_scenarios.get(malf, []))
                malf_refined = func_refined.get(composite_key, func_refined.get(malf, []))

                if malf_scenarios:
                    scenario_hazards = []
                    for sc in malf_scenarios:
                        scenario = sc.get("scenario_summary", sc.get("scenario", ""))
                        effective_guideword = sc.get("_guideword", guideword)
                        hazard_event = self._infer_hazard_event(
                            malf, hazard_desc, hazard_category, sc,
                            subsystem=self.subsystem, function=func,
                            guideword=effective_guideword,
                        )
                        scenario_hazards.append({
                            "scenario_id": sc.get("scenario_id", ""),
                            "scenario_facts": dict(sc.get("scenario_facts", {})),
                            "refined_scenario": scenario,
                            "hazard_event": hazard_event,
                            "_guideword": sc.get("_guideword", guideword),
                            "_malf_desc": sc.get("_malf_desc", malf)
                        })
                        total_events += 1
                    malf_hazard_events[composite_key] = scenario_hazards
                elif malf_refined:
                    scenario_hazards = []
                    for sc in malf_refined:
                        scenario = sc.get("refined_scenario", "")
                        hazard_event = self._infer_hazard_event(
                            malf, hazard_desc, hazard_category, sc,
                            subsystem=self.subsystem, function=func,
                            guideword=guideword,
                        )
                        scenario_hazards.append({
                            "scenario_id": sc.get("scenario_id", ""),
                            "scenario_facts": dict(sc.get("scenario_facts", {})),
                            "refined_scenario": scenario,
                            "hazard_event": hazard_event,
                            "_guideword": guideword,
                            "_malf_desc": malf
                        })
                        total_events += 1
                    malf_hazard_events[composite_key] = scenario_hazards
                else:
                    hazard_event = self._infer_hazard_event(
                        malf, hazard_desc, hazard_category, "",
                        subsystem=self.subsystem, function=func,
                        guideword=guideword,
                    )
                    malf_hazard_events[composite_key] = [{
                        "refined_scenario": "",
                        "hazard_event": hazard_event,
                        "_guideword": guideword,
                        "_malf_desc": malf
                    }]
                    total_events += 1

            hazard_events_results[func] = malf_hazard_events

        logger.info(f"[Step8] 完成: 生成 {total_events} 个危害事件")

        return {
            "success": True,
            "hazard_events": hazard_events_results,
            "total_events": total_events
        }

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

    BEHAVIOR_MAPPING = {
        "unintended": "车辆非预期触发功能导致<行为结果>",
        "always active": "车辆持续执行异常功能导致<行为结果>",
        "loss": "车辆关键功能丧失导致<行为结果>",
        "too large": "车辆参数过大导致<行为结果>",
        "too small": "车辆参数过小导致<行为结果>",
        "too early": "车辆过早执行操作导致<行为结果>",
        "too late": "车辆过晚执行操作导致<行为结果>",
        "too fast": "车辆速度过快导致<行为结果>",
        "too slow": "车辆速度过慢导致<行为结果>",
        "too long": "车辆功能持续过长导致<行为结果>",
        "too short": "车辆功能过早终止导致<行为结果>",
        "incomplete": "车辆功能不完整导致<行为结果>",
        "different to": "车辆行为偏离预期导致<行为结果>",
        "as well as": "车辆多重功能冲突导致<行为结果>"
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
            "default": "车辆发生高速碰撞"
        },
        "too slow": {
            "刹车": "车辆发生追尾碰撞",
            "加速": "车辆被后车追尾",
            "wiper": "雨刷速度过慢无法及时清除雨水，驾驶员前方视野持续受阻，在雨天低速行驶时与行人发生碰撞",
            "雨刷": "雨刷速度过慢无法及时清除雨水，驾驶员前方视野持续受阻，在雨天低速行驶时与行人发生碰撞",
            "default": "车辆发生追尾碰撞"
        },
        "too large": {
            "刹车": "车辆紧急制动导致车轮抱死，发生侧翻或侧向碰撞",
            "加速": "车辆突然加速失控，发生前方碰撞",
            "default": "车辆发生碰撞事故"
        },
        "too small": {
            "刹车": "车辆制动不足，发生前方碰撞",
            "加速": "车辆加速过慢，被后车追尾",
            "default": "车辆发生碰撞事故"
        },
        "too early": {
            "刹车": "车辆过早制动导致后车追尾",
            "加速": "车辆在危险条件下过早加速，导致前方碰撞",
            "转向": "车辆在盲区过早转向，与侧方来车发生碰撞",
            "default": "车辆与来车发生碰撞"
        },
        "too late": {
            "刹车": "车辆错过最佳制动时机，发生前方或追尾碰撞",
            "加速": "车辆错过安全间隙，与行人或障碍物发生碰撞",
            "转向": "车辆错过路口，与侧向来车发生碰撞",
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
            "default": "车辆异常状态持续，导致碰撞事故"
        },
        "too short": {
            "刹车": "车辆制动过短导致停车距离不足，发生前方碰撞",
            "加速": "车辆加速过短导致汇入失败，发生侧向碰撞",
            "default": "车辆操作中断导致碰撞事故"
        },
        "incomplete": {
            "刹车": "车辆制动不完整，发生前方碰撞",
            "转向": "车辆转向不完整，发生侧向碰撞",
            "default": "车辆功能不完整导致碰撞事故"
        },
        "different to": {
            "刹车": "车辆制动效果与预期不符，导致碰撞",
            "转向": "车辆转向方向与预期不符，导致侧向碰撞",
            "default": "车辆行为偏离驾驶员预期，导致碰撞"
        },
        "as well as": {
            "刹车": "车辆同时执行多重指令导致失控，发生碰撞",
            "default": "车辆多重功能冲突导致碰撞事故"
        }
    }

    INJURY_MAPPING = {
        "unintended": "驾驶员或乘员遭受碰撞伤害或被甩出车外",
        "always active": "驾驶员被迫反复应对异常，造成疲劳并引发碰撞或甩出伤害",
        "loss": "驾驶员失去对车辆的控制，导致严重碰撞、甩出或窒息伤害",
        "too large": "驾驶员难以精确控制车辆，造成碰撞、侧翻或甩出伤害",
        "too small": "驾驶员无法获得足够的车辆功能支持，造成被困或延误救援伤害",
        "too early": "驾驶员未做好应对准备，造成应急响应失败后的碰撞或夹挤伤害",
        "too late": "驾驶员错过最佳应对时机，造成不可避免的严重碰撞或甩出伤害",
        "too fast": "驾驶员来不及反应，造成高速碰撞或甩出伤害",
        "too slow": "驾驶员判断延迟，造成追尾或被追尾伤害",
        "too long": "驾驶员长时间处于紧张状态，造成疲劳伤害，若车辆持续异常可引发碰撞或甩出",
        "too short": "驾驶员无法确认操作完成，造成二次碰撞或夹挤伤害",
        "incomplete": "驾驶员面对不一致的车辆状态，造成判断失误导致碰撞、甩出或夹挤伤害",
        "different to": "驾驶员误判车辆状态，造成操作失误导致碰撞或甩出伤害",
        "as well as": "驾驶员同时面对多重异常，造成应对失败导致碰撞、甩出或电击伤害"
    }

    def _infer_avp_hazard_event(self, function: str, guideword: str,
                                malfunction: str,
                                scenario_facts: Optional[Dict[str, Any]] = None) -> str:
        """Build an AVP-domain hazardous event without borrowing another subsystem.

        This is deliberately function/guideword based.  A generic parking keyword
        is not sufficient evidence for reusing an EPB, wiper, door, or other
        subsystem event from HazardRef.
        """
        func = (function or "").lower()
        gw = (guideword or "").lower()
        fault_state = self.FAULT_STATE_MAPPING.get(
            gw, f"{malfunction}导致AVP功能异常" if malfunction else "AVP功能异常"
        )
        facts = scenario_facts or {}
        object_type = str(facts.get("object_type", ""))
        object_position = str(facts.get("object_position", "周边"))
        relative_speed = facts.get("relative_speed_kph")
        relative_distance = str(facts.get("relative_distance", ""))
        if "行人" in object_type:
            target = f"位于{object_position}的近距离行人"
            default_harm = "可能碰撞行人并造成轻中度伤害"
        elif any(token in object_type for token in ("车辆", "车")):
            target = f"位于{object_position}的接近车辆"
            default_harm = "可能与接近车辆碰撞并造成乘员伤害"
        elif "无近距离" in object_type:
            target = "安全停车区域内无近距离冲突目标"
            default_harm = "车辆保持静止或受控停车，无人员伤害且无安全相关后果"
        else:
            target = "周边人员或车辆"
            default_harm = "可能碰撞周边人员或车辆并造成人员伤害"
        kinematics = ""
        if relative_speed is not None:
            speed_text = f"{relative_speed:g}" if isinstance(relative_speed, (int, float)) else str(relative_speed)
            kinematics = f"（相对速度约{speed_text} km/h"
            if relative_distance:
                kinematics += f"，初始距离{relative_distance}"
            kinematics += "）"

        def risk(behavior: str, harm: str = "") -> str:
            return (
                f"<{fault_state}>导致<{behavior}并接近{target}{kinematics}>，<{harm or default_harm}；"
                "驾驶员位于车外，异常可能毫无征兆且无法及时直接接管>"
            )

        def availability(behavior: str) -> str:
            return (
                f"<{fault_state}>导致<{behavior}>，"
                "<车辆保持静止或执行受控制动进入安全停车；无人员伤害且无安全相关后果>"
            )

        if "驻车制动" in func:
            if gw in {"loss", "too small", "too late", "too slow", "too short", "incomplete", "different to"}:
                return risk("坡道驻车保持不足，车辆向下坡方向溜车")
            return risk("驻车制动非预期施加或释放，使车辆突然停止或在坡道发生非预期运动")

        if "制动扭矩" in func:
            if gw in {"unintended", "always active", "too large", "too early", "too fast"}:
                return risk("车辆在泊车轨迹中突然过度制动，后续车辆或近距离人员来不及避让")
            return risk("车辆制动距离增加或无法按预期停止")

        if "驱动扭矩" in func:
            if gw in {"loss", "too small", "too late", "too slow", "too short", "incomplete"}:
                if (
                    str(facts.get("scenario_variant", "")) == "controlled_standstill"
                    or "无近距离" in object_type
                ):
                    return availability("车辆无法起步或中止泊车，停留在当前安全位置")
                return risk("驱动力不足使车辆异常减速，或在坡道上发生反向溜车")
            return risk("车辆产生非预期或过大的低速驱动力并偏离预定轨迹")

        if "转向扭矩" in func:
            return risk("车辆转向角或转向时序异常并偏离泊车轨迹")

        if any(token in func for token in ("开启功能", "激活功能")):
            if gw in {"loss", "too late", "too slow", "too short", "incomplete"}:
                return availability("AVP无法启动或启动被拒绝，车辆保持静止")
            return risk("AVP在前置条件未满足时启动，车辆发生非预期运动并接近周边人员或车辆")

        if any(token in func for token in ("退出功能", "关闭功能")):
            if gw in {"unintended", "too early", "too fast"}:
                return risk("AVP控制被非预期提前解除，车辆运动未按预期完成安全停车")
            return risk("AVP在退出请求后继续控制车辆，车辆持续运动并接近周边人员或车辆")

        if "报警提示" in func:
            if gw in {"unintended", "always active", "too early", "too fast"}:
                return availability("产生错误或过早报警，但车辆运动控制保持正常")
            return risk(
                "关键危险未被及时、完整或正确提示，远程用户无法在可用时间内触发停车",
                "可能导致车辆继续接近行人并发生轻中度伤害"
            )

        return risk("车辆发生非预期低速运动并偏离泊车轨迹")

    def _infer_hazard_event(self, malfunction: str, hazard_desc: str,
                            hazard_category: str, scenario: Any,
                            subsystem: str = "", function: str = "",
                            guideword: str = "") -> str:
        """
        推理生成危害事件
        优先级:
        1. hazard_event_index guideword精确匹配 (最高优：引导词+子系统双重匹配)
        2. HazardRef.json entries 关键词匹配 (基于子系统/功能/malfunction/场景)
        3. SPECIFIC_BEHAVIOR_RESULTS 关键词匹配 (推理逻辑兜底)
        """
        malfunction_lower = malfunction.lower() if malfunction else ""
        guideword = (guideword or "").lower()
        scenario_facts = scenario if isinstance(scenario, dict) else {}
        scenario_text = (
            scenario_facts.get("scenario_summary")
            or scenario_facts.get("scenario")
            or scenario_facts.get("refined_scenario")
            or ""
        ) if scenario_facts else str(scenario or "")

        # Backward-compatible fallback for callers that do not yet pass the
        # structured guideword.  The primary path uses the Phase-1 field.
        if not guideword:
            for gw in self.FAULT_STATE_MAPPING.keys():
                if gw in malfunction_lower:
                    guideword = gw
                    break

        if self._is_avp_context(function):
            return self._infer_avp_hazard_event(
                function, guideword, malfunction, scenario_facts=scenario_facts
            )

        fault_state = self.FAULT_STATE_MAPPING.get(guideword,
            f"{malfunction}导致系统异常" if malfunction else "系统异常")

        vehicle_behavior = None

        # === PRIORITY 1 (NEW): hazard_event_index 引导词精确匹配 ===
        if self.hazard_event_index and guideword:
            combined_text = f"{subsystem} {function} {malfunction}".lower()
            for entry in self.hazard_event_index:
                idx_kws = entry.get("index_keywords", [])
                if (entry.get("guideword") == guideword and
                    any(kw.lower() in combined_text for kw in idx_kws)):
                    he = entry.get("hazard_event", None)
                    if he:
                        vehicle_behavior = he
                        logger.info(f"  [Step8] hazard_event_index guideword匹配: "
                                    f"GW={guideword}, hazard={he[:60]}")
                        break

        # === PRIORITY 2: HazardRef.json entries 关键词匹配 ===
        if not vehicle_behavior and self.hazard_ref_entries:
            ref_entry = self._find_hazard_by_keywords(
                malfunction=malfunction,
                function=function,
                subsystem=subsystem or self.subsystem,
                scenario=scenario_text
            )
            if ref_entry and ref_entry.get("description"):
                vehicle_behavior = ref_entry["description"]
                logger.info(f"  [Step8] HazardRef匹配: {ref_entry.get('description', '')[:80]}")

        # === PRIORITY 3: SPECIFIC_BEHAVIOR_RESULTS 推理兜底 ===
        if not vehicle_behavior:
            specific_behaviors = self.SPECIFIC_BEHAVIOR_RESULTS.get(guideword, {})
            if specific_behaviors:
                for kw, behavior in specific_behaviors.items():
                    if kw != "default" and kw in malfunction:
                        vehicle_behavior = behavior
                        break
                if not vehicle_behavior:
                    vehicle_behavior = specific_behaviors.get("default", "车辆发生碰撞事故")

        if not vehicle_behavior:
            vehicle_behavior = "车辆发生碰撞事故"

        injury = self.INJURY_MAPPING.get(guideword,
            "造成人员伤害")

        return f"<{fault_state}>导致<{vehicle_behavior}>，<{injury}>"

    def save_output(self, output_path: str = None) -> bool:
        """
        保存输出JSON

        Args:
            output_path: 输出文件路径

        Returns:
            是否保存成功
        """
        if not self.output_data:
            logger.error("没有可保存的输出数据，请先调用run()")
            return False

        if not output_path:
            if self.input_json_path:
                input_dir = os.path.dirname(self.input_json_path)
                output_path = os.path.join(input_dir, "scen_hazevent_output.json")
            else:
                output_path = "scen_hazevent_output.json"

        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(self.output_data, f, ensure_ascii=False, indent=2)

            logger.info(f"输出已保存到: {output_path}")
            return True
        except Exception as e:
            logger.error(f"保存输出失败: {e}")
            return False

    def get_output(self) -> Dict[str, Any]:
        """获取输出数据"""
        return self.output_data


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="场景与危害事件引擎 (Step6-8)")
    parser.add_argument("--input", "-i", type=str, required=True,
                        help="上游malfunction_output.json路径")
    parser.add_argument("--template", "-t", type=str,
                        help="HARA模板Excel路径")
    parser.add_argument("--output", "-o", type=str,
                        help="输出JSON路径")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="详细输出")
    parser.add_argument("--domain-profile", help="显式Domain Profile JSON路径")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    input_path = args.input

    engine = ScenHazEventEngine(input_json_path=input_path,
                                 excel_template_path=args.template,
                                 domain_profile_path=args.domain_profile)
    result = engine.run()

    if not result["success"]:
        logger.error(f"引擎执行失败: {result.get('error', '未知错误')}")
        return 1

    if args.output:
        output_path = args.output
    else:
        output_path = None

    if engine.save_output(output_path):
        logger.info("执行成功")
        return 0
    else:
        logger.error("保存输出失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())

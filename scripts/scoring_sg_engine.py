#!/usr/bin/env python3
"""
Scoring & Safety Goal Engine (Steps 9-17)
从 hara_engine.py 提取的严重度、暴露度、可控度打分及安全目标生成模块
"""

import os
import sys
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import logging
import argparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hara_agent.domains import AVPDomainPolicy, load_domain_profile
from hara_agent.services.analysis import (
    DomainScoringService,
    FTTIService,
    RiskAggregationService,
    SafetyGoalCatalogService,
    TemplateASILService,
)
from hara_agent.services.extraction import TemplateInputReader

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SeverityReasoningEngine:
    """严重度合理性分析引擎（Step9）"""

    DEFAULT_ROAD_USERS = ["驾驶员", "乘员", "行人", "自行车驾驶员", "摩托车驾驶员", "维修人员", "应急响应人员"]

    HAZARD_EVENT_MAP = {
        "collision": {"keywords": ["collision", "collisions", "碰撞", "撞击", "追尾"],
                      "s_normal": None, "note": "需按速度映射表判定"},
        "personal or things falling out of vehicle": {"keywords": ["falling out", "掉落", "跌落", "甩出"],
                      "s_speed_high": "S3", "s_speed_low": "S2", "speed_threshold": 15},
        "squeeze or pinch": {"keywords": ["squeeze", "pinch", "夹伤", "挤压", "夹持"],
                      "s_adult": "S1", "s_child": "S2", "s_driver": "S2"},
        "possible rib fracture and asphyxia": {"keywords": ["rib fracture", "asphyxia", "肋骨骨折", "窒息", "缺氧"],
                      "s": "S3"},
        "skin-deep wounds": {"keywords": ["skin-deep wound", "皮肤浅表伤", "表皮伤", "轻微伤"],
                      "s": "S1"},
        "possible fracture of the passenger's finger": {"keywords": ["finger fracture", "手指骨折"],
                      "s": "S1"},
        "fail to escape the vechie after crash": {"keywords": ["fail to escape", "逃生失败", "无法逃离"],
                      "s": "S3"},
        "asphyxia": {"keywords": ["asphyxia", "窒息", "缺氧", "窒息风险"],
                      "s": "S3"},
        "electric shock": {"keywords": ["electric shock", "电击", "触电", "电伤害"],
                      "s": "S3"},
        "destabilization or lane departure": {"keywords": ["destabilization", "lane departure", "偏离车道", "失稳", "车道偏离"],
                      "s": "S3"},
    }

    I_COLUMN_COLLISION_MAP = [
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

    S_LEVEL_DEFINITIONS = {
        "S0": {"description": "No injuries (无伤害)", "ais": "AIS 0 and less than 10% probability of AIS 1-6"},
        "S1": {"description": "Light and moderate injuries (轻中度伤害)", "ais": "More than 10% probability of AIS 1-6 (and not S2 or S3)"},
        "S2": {"description": "Severe and life-threatening injuries, survival probable (严重伤害，可能存活)", "ais": "More than 10% probability of AIS 3-6 (and not S3)"},
        "S3": {"description": "Life-threatening injuries, survival uncertain, fatal (危及生命/致命)", "ais": "More than 10% probability of AIS 5-6"}
    }

    def __init__(self, domain_policy: Optional[AVPDomainPolicy] = None):
        self.domain_policy = domain_policy

    def analyze(self, hazard_event: str, refined_scenario: Dict = None, road_users: List[str] = None) -> Dict[str, Any]:
        """执行严重度合理性分析

        原则：严重度S值的合理性应基于危害事件和场景描述（车速信息）分析。
        """
        if refined_scenario is None:
            refined_scenario = {}
        if road_users is None:
            road_users = self.DEFAULT_ROAD_USERS

        logger.info(f"分析危害事件严重度: {hazard_event[:60]}...")

        vehicle_speed = 0
        scenario_text = ""
        if refined_scenario:
            scenario_text = self._extract_scenario_text(refined_scenario)
            # Collision severity is driven by impact/relative speed where the
            # structured AVP scenario provides it; ego speed is only a fallback.
            vs = refined_scenario.get("relative_speed_kph")
            if vs is None or vs == "":
                vs = refined_scenario.get("vehicle_speed", "")
            if vs:
                speed_match = re.search(r'(\d+)', str(vs))
                if speed_match:
                    vehicle_speed = int(speed_match.group(1))
            if vehicle_speed == 0 and scenario_text:
                speed_m = re.search(r'(?:车辆)?速度[=:]?\s*(\d+)', scenario_text)
                if not speed_m:
                    speed_m = re.search(r'vehicle.?speed[=:]?\s*(\d+)', scenario_text, re.IGNORECASE)
                if not speed_m:
                    speed_m = re.search(r'Traffic Speed[=:]?\s*(\d+)', scenario_text, re.IGNORECASE)
                if not speed_m:
                    speed_m = re.search(r'(\d+)\s*<[^=]*v[^=]*[≤<]', scenario_text)
                if not speed_m:
                    speed_m = re.search(r'车速\s*(\d+)', scenario_text)
                if speed_m:
                    vehicle_speed = int(speed_m.group(1))

        policy_decision = self.domain_policy.severity_decision(refined_scenario) if self.domain_policy else None
        if self.domain_policy and self.domain_policy.is_structured_scenario(refined_scenario) and not policy_decision:
            raise ValueError("结构化AVP场景缺少Severity Domain Policy判据，禁止回退到关键词评分")
        hazard_lower = hazard_event.lower()
        no_injury_markers = [
            "无人员伤害", "无安全相关后果", "no injury", "no safety-related consequence",
        ]
        if policy_decision:
            matched_level = policy_decision["score"]
        elif any(marker in hazard_lower for marker in no_injury_markers):
            matched_level = "S0"
        else:
            matched_level = self._match_hazard_event(hazard_lower, vehicle_speed)

        if matched_level is None:
            matched_level = self._infer_from_ais(hazard_lower)

        if matched_level is None:
            matched_level = "S1"

        reason = self._build_reason(matched_level, hazard_event, scenario_text, vehicle_speed, road_users)

        result = {
            "hazard_event": hazard_event,
            "severity_score": matched_level,
            "reasoning": reason,
            "affected_road_users": road_users[:4]
        }
        if policy_decision:
            result.update({
                "engineering_rule_id": policy_decision["rule_id"],
                "engineering_rule_version": policy_decision["rule_version"],
                "engineering_status": policy_decision["engineering_status"],
                "engineering_basis": policy_decision["basis"],
            })
        return result

    def _extract_scenario_text(self, refined_scenario: Dict) -> str:
        """从场景对象中提取场景描述文本，按优先级查找"""
        for key in ["refined_scenario", "situational_description", "scenario_summary", "scenario"]:
            val = refined_scenario.get(key, "")
            if val and str(val).strip():
                return str(val).strip()
        return ""

    def _match_hazard_event(self, hazard_lower: str, vehicle_speed: int) -> Optional[str]:
        is_collision = any(kw in hazard_lower for kw in
                          ["collision", "crash", "rear-end", "frontal", "side impact",
                           "car-pedestrian", "car cyclists", "追尾", "正面碰撞", "侧面碰撞",
                           "行人碰撞", "自行车碰撞", "碰撞", "lane departure", "偏离车道"])

        if is_collision:
            return self._match_collision_speed(hazard_lower, vehicle_speed)

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

        return None

    def _match_collision_speed(self, hazard_lower: str, vehicle_speed: int) -> str:
        collision_type = None
        # Vulnerable road-user classification takes precedence over positional
        # words such as "前方/后方" that may also occur in the scenario text.
        if any(kw in hazard_lower for kw in ["pedestrian", "行人", "行人与", "碰撞行人"]):
            collision_type = "car-pedestrian"
        elif any(kw in hazard_lower for kw in ["cyclist", "bike", "bicycle", "自行车", "骑行者", "骑车"]):
            collision_type = "car cyclists"
        elif any(kw in hazard_lower for kw in ["rear-end", "追尾", "rear", "前车", "撞上前车", "与前车发生碰撞"]):
            collision_type = "rear-end collision"
        elif any(kw in hazard_lower for kw in ["frontal", "正面", "front", "后车", "被后车撞"]):
            collision_type = "frontal collision"
        elif any(kw in hazard_lower for kw in ["side", "侧面", "side impact", "侧面碰撞"]):
            collision_type = "side collision"
        elif any(kw in hazard_lower for kw in ["destabilization", "lane departure", "偏离", "失稳", "车道偏离"]):
            return "S3"

        if not collision_type:
            if any(kw in hazard_lower for kw in ["碰撞", "crash", "impact", "撞击"]):
                collision_type = "rear-end collision"
            else:
                return "S1"

        for entry in self.I_COLUMN_COLLISION_MAP:
            if collision_type.lower() in entry[0].lower():
                try:
                    if entry[3](vehicle_speed):
                        return entry[2]
                except:
                    continue

        return "S2"

    def _infer_from_ais(self, hazard_lower: str) -> Optional[str]:
        prob_over_10 = not any(kw in hazard_lower for kw in
                              ["low probability", "rare", "unlikely", "improbable",
                               "less than 10%", "<10%", "极低概率", "罕见"])

        ais_6 = any(kw in hazard_lower for kw in
                   ["extremely critical", "fatal injury", "fatalities", "multiple fatalities",
                    "spinal fractures.*above.*cervical", "exsanguination", "decapitation",
                    "极危", "惨死", "多器官衰竭"])

        ais_5 = any(kw in hazard_lower for kw in
                   ["critical injury", "life-threatening.*uncertain", "survival uncertain",
                    "cardiac tear", "intestinal tear", "spinal fracture.*cord damage",
                    "严重危及生命", "存活不确定"]) or \
                any(kw in hazard_lower for kw in
                    ["fatal", "death", "kill", "mortality", "致命", "死亡", "asphyxia", "electric shock"])

        ais_4 = any(kw in hazard_lower for kw in
                   ["severe.*life-threatening", "survival probable", "concussion.*12h",
                    "paradoxical breathing", "intracranial bleeding", "severe organ",
                    "严重受伤.*危及", "开颅"])

        ais_3 = any(kw in hazard_lower for kw in
                   ["severe but not life-threatening", "skull fracture", "spinal dislocation",
                    "uncomplicated rib fracture", "multiple rib fracture", "rib fracture",
                    "肋骨骨折", "严重受伤", "多处骨折", "颅骨骨折"])

        ais_2 = any(kw in hazard_lower for kw in
                   ["moderate injury", "deep flesh wound", "concussion.*unconsciousness",
                    "uncomplicated long bone fracture", "骨折", "中度伤", "深层"])

        ais_1 = any(kw in hazard_lower for kw in
                   ["light injury", "minor injury", "whiplash", "skin-deep wound", "muscle pain",
                    "abrasion", "superficial", "light fracture", "finger fracture",
                    "squeeze", "pinch", "bruise", "cut", "laceration",
                    "轻伤", "擦伤", "挫伤", "夹伤", "挤压", "擦碰",
                    "injured", "injury", "wound", "hurt", "受伤", "碰撞", "撞击"])

        if ais_6 or ais_5:
            ais_max = 6 if ais_6 else 5
        elif ais_4:
            ais_max = 4
        elif ais_3:
            ais_max = 3
        elif ais_2:
            ais_max = 2
        elif ais_1:
            ais_max = 1
        else:
            return None

        if prob_over_10:
            if ais_max >= 5:
                return "S3"
            elif ais_max >= 3:
                return "S2"
            elif ais_max >= 1:
                return "S1"
            else:
                return "S0"
        else:
            if ais_max >= 5:
                return "S2"
            elif ais_max >= 3:
                return "S1"
            else:
                return "S0"

    def _build_reason(self, level: str, hazard_event: str, scenario_text: str,
                      vehicle_speed: int, road_users: List[str]) -> str:
        level_def = self.S_LEVEL_DEFINITIONS.get(level, {})
        level_desc = level_def.get("description", "")
        ais_ref = level_def.get("ais", "")
        users_str = "、".join(road_users) if road_users else "道路使用者"

        potential_harm = "驾驶员可能因功能异常而遭受伤害"
        if level == "S3":
            potential_harm = "驾驶员面临危及生命的严重伤害风险"
        elif level == "S2":
            potential_harm = f"对{users_str}造成严重的和危及生命的伤害"
        elif level == "S1":
            potential_harm = f"对{users_str}造成轻中度伤害"
        elif level == "S0":
            potential_harm = "对安全影响轻微或无伤害"

        step1 = f"危害事件: {hazard_event[:80]}..." if len(hazard_event) > 80 else f"危害事件: {hazard_event}"
        step2 = f"在{scenario_text[:60]}场景下({vehicle_speed} km/h)，{potential_harm}"
        step3 = f"依据{level} - {level_desc}"
        step4 = f"参考GB/T 34590 Table B.1: {ais_ref}"
        final = f"评定Severity={level}"

        return f"{step1}。\n{step2}。\n{step3}，\n{step4}，\n{final}。"


class ExposureReasoningEngine:
    """暴露度合理性分析引擎（Step11）

    原则：从场景描述中获取主场景、车辆状态、天气等信息关键词，然后评估暴露度E值的合理性。
    """

    MAIN_SCENARIO_KEYWORDS = {
        "Highway": ["highway", "motorway", "高速", "高速公路", " Autobahn", "expressway", "州际公路"],
        "Urban": ["urban", "city", "市区", "城市道路", "metropolitan", "市中心"],
        "Rural": ["rural", "country", "乡村", "农村", "乡间道路", "suburban"],
        "Parking": ["parking", "停车场", "garage", "停车"],
        "Residential": ["residential", "住宅区", "residence", "living area"],
        "Service": ["service", "服务区", "休息区", "rest area"],
        "Maintenance": ["maintenance", "维修区", "service bay", "保养"],
        "Tunnel": ["tunnel", "隧道"],
        "Railway": ["railway", "铁路", "road-rail crossing", "道口"],
        "Mountain": ["mountain", "山路", "山区", "mountain road"],
        "Off-road": ["off-road", "越野", "非铺装"],
    }

    def __init__(self, domain_policy: Optional[AVPDomainPolicy] = None):
        self.domain_policy = domain_policy

    def extract_scenario_keywords(self, refined_scenario: Dict) -> Dict[str, str]:
        """从场景描述中提取主场景、车辆状态、天气、路面等关键词"""
        full_text = ""
        for key in ["refined_scenario", "situational_description", "scenario_summary", "scenario"]:
            val = refined_scenario.get(key, "")
            if val and str(val).strip():
                full_text += str(val).strip() + " "
        if not full_text.strip():
            return {"main_scenario": "", "vehicle_state": "", "weather": "", "road_surface": "", "vehicle_speed": ""}

        main_scenario = ""
        text_lower = full_text.lower()
        for scenario_name, keywords in self.MAIN_SCENARIO_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                main_scenario = scenario_name
                break

        vehicle_state = refined_scenario.get("vehicle_state", "")
        if not vehicle_state and "车辆状态" in full_text:
            st_match = re.search(r"车辆状态[=:]\s*([^,\];\]]+)", full_text)
            if st_match:
                vehicle_state = st_match.group(1).strip()

        vehicle_speed = ""
        if "vehicle_speed" in refined_scenario:
            vehicle_speed = str(refined_scenario.get("vehicle_speed", ""))
        elif "车速" in full_text or "vehicle.?speed" in text_lower:
            vp_match = re.search(r"(?:车辆)?速度[=:]\s*(\d+)", full_text)
            if not vp_match:
                vp_match = re.search(r"vehicle.?speed[=:]\s*(\d+)", full_text, re.IGNORECASE)
            if not vp_match:
                vp_match = re.search(r"车速\s*(\d+)", full_text)
            if not vp_match:
                vp_match = re.search(r"(\d+)\s*<[^=]*v[^=]*[≤<]", full_text)
            if vp_match:
                vehicle_speed = f"{vp_match.group(1)} km/h"
        elif "speed" in text_lower or "km/h" in text_lower:
            vp_match = re.search(r"(\d+)\s*(?:km/?h)?", full_text)
            if vp_match:
                vehicle_speed = f"{vp_match.group(1)} km/h"

        weather = refined_scenario.get("weather_conditions", "")
        if not weather and "天气" in full_text:
            we_match = re.search(r"天气[=:]\s*([^,\];\]]+)", full_text)
            if we_match:
                weather = we_match.group(1).strip()
        elif not weather and ("weather" in text_lower or "rain" in text_lower or "snow" in text_lower or "clear" in text_lower):
            for kw in ["clear", "sunny", "晴天", "rain", "snow", "雨天", "雪天", "fog", "雾"]:
                if kw in text_lower:
                    weather = kw
                    break

        road_surface = refined_scenario.get("road_surface_conditions", "")
        if not road_surface and "路面" in full_text:
            rs_match = re.search(r"路面[=:]\s*([^,\];\]]+)", full_text)
            if rs_match:
                road_surface = rs_match.group(1).strip()

        return {
            "main_scenario": main_scenario,
            "vehicle_state": vehicle_state,
            "vehicle_speed": vehicle_speed,
            "weather": weather,
            "road_surface": road_surface,
        }

    def analyze(self, refined_scenario: Dict) -> Dict[str, Any]:
        """执行暴露度合理性分析"""
        # A project/reference scenario matrix is stronger evidence than generic
        # keyword rules.  Keep the basis in the result so the score is auditable.
        policy_decision = self.domain_policy.exposure_decision(refined_scenario) if self.domain_policy else None
        if self.domain_policy and self.domain_policy.is_structured_scenario(refined_scenario) and not policy_decision:
            raise ValueError("结构化AVP场景缺少Exposure Domain Policy判据，禁止回退到关键词评分")
        hinted_score = str(refined_scenario.get("exposure_level_candidate", "")).upper()
        if policy_decision or hinted_score in {"E0", "E1", "E2", "E3", "E4"}:
            score = policy_decision["score"] if policy_decision else hinted_score
            basis = policy_decision["basis"] if policy_decision else str(refined_scenario.get("exposure_basis", "")).strip()
            result = {
                "scenario": self._extract_display_scenario(refined_scenario),
                "exposure_score": score,
                "exposure_reason": basis or f"结构化场景暴露矩阵 → {score}",
                "reasoning": basis or f"结构化场景暴露矩阵 → {score}",
                "exposure_method": (
                    policy_decision.get("exposure_method", "")
                    if policy_decision else str(refined_scenario.get("exposure_method", "")).upper()
                ),
                "_extracted": self.extract_scenario_keywords(refined_scenario),
                "_source": "structured_scenario_matrix",
            }
            if policy_decision:
                result.update({
                    "engineering_rule_id": policy_decision["rule_id"],
                    "engineering_rule_version": policy_decision["rule_version"],
                    "engineering_status": policy_decision["engineering_status"],
                })
            return result
        extracted = self.extract_scenario_keywords(refined_scenario)
        main_scenario = extracted["main_scenario"]
        vehicle_speed = extracted["vehicle_speed"]
        weather = extracted["weather"]
        road_surface = extracted["road_surface"]
        vehicle_state = extracted["vehicle_state"]

        scenario_text = ""
        for key in ["refined_scenario", "situational_description", "scenario_summary", "scenario"]:
            val = refined_scenario.get(key, "")
            if val and str(val).strip():
                scenario_text = str(val).strip()
                break

        logger.info(f"分析暴露度: scenario_text={scenario_text[:60]}, "
                    f"main={main_scenario}, speed={vehicle_speed}, weather={weather}")

        scenario_lower = (scenario_text or "").lower()

        exposure_rules = {
            "E4": {
                "keywords": ["highway", "motorway", "高速", "高速公路", "expressway", "州际公路",
                             "常用", "日常", "通勤", "frequent", "urban road", "日常驾驶", ">10%",
                             " Autobahn", "ring road", "interstate", "avp parking", "代客泊车停车场",
                             "parking lot", "garage", "停车场"],
                "reason_template": "该场景在日常驾驶中频繁出现，{context}符合E4定义: 运行时间>10%"
            },
            "E3": {
                "keywords": ["市区", "城市道路", "urban", "city driving", "市郊", "suburban", "suburb",
                             "city", "moderate frequency", "weekly", "1-10%", "普通道路", "metropolitan"],
                "reason_template": "该场景在常见驾驶环境下经常出现，{context}符合E3定义: 运行时间1-10%"
            },
            "E2": {
                "keywords": ["雨天", "雪天", "夜间", "恶劣天气", "山路", "night", "unusual weather",
                             "adverse conditions", "winter", "specific road", "0.1-1%",
                             "rural", "country road", "山道", "tunnel", "隧道",
                             "maintenance", "维修区", "service", "服务区"],
                "reason_template": "该场景仅在特定条件下偶尔出现，{context}符合E2定义: 运行时间0.1-1%"
            },
            "E1": {
                "keywords": ["极罕见", "特殊", "极端", "edge case", "rare", "exceptional", "<0.1%",
                             "off-road", "越野", "railway", "道口"],
                "reason_template": "该场景极少出现，{context}符合E1定义: 运行时间<0.1%"
            },
            "E0": {
                "keywords": ["impossible", "improbable", "theoretical", "理论上"],
                "reason_template": "该场景几乎不可能出现，符合E0定义"
            }
        }

        speed_value = re.search(r'(\d+)', vehicle_speed) if vehicle_speed else None
        if speed_value:
            speed = int(speed_value.group(1))
            if speed >= 120:
                reason_ctx = self._build_context(main_scenario, vehicle_speed, weather, road_surface, vehicle_state)
                return {
                    "scenario": scenario_text,
                    "exposure_score": "E4",
                    "exposure_reason": f"该场景为高速场景({main_scenario})，车速{vehicle_speed}，暴露时长占比高，符合E4定义: 运行时间>10%",
                    "reasoning": f"该场景为高速行驶场景(120km/h以上)，暴露时长占比高，符合E4定义: 运行时间>10%",
                    "_extracted": extracted
                }
            elif speed >= 80:
                reason_ctx = self._build_context(main_scenario, vehicle_speed, weather, road_surface, vehicle_state)
                return {
                    "scenario": scenario_text,
                    "exposure_score": "E3",
                    "exposure_reason": f"该场景为中高速场景({main_scenario})，车速{vehicle_speed}，符合E3定义: 运行时间1-10%",
                    "reasoning": f"该场景为中高速行驶场景(80-120km/h)，符合E3定义: 运行时间1-10%",
                    "_extracted": extracted
                }

        for level, rule in exposure_rules.items():
            if any(kw in scenario_lower for kw in rule["keywords"]):
                reason_ctx = self._build_context(main_scenario, vehicle_speed, weather, road_surface, vehicle_state)
                reason = rule["reason_template"].format(context=reason_ctx)
                return {
                    "scenario": scenario_text,
                    "exposure_score": level,
                    "exposure_reason": reason,
                    "reasoning": reason,
                    "_extracted": extracted
                }

        reason_ctx = self._build_context(main_scenario, vehicle_speed, weather, road_surface, vehicle_state)
        return {
            "scenario": scenario_text,
            "exposure_score": "E2",
            "exposure_reason": f"该场景具有一般发生概率，{reason_ctx}，默认为E2",
            "reasoning": f"该场景具有一般发生概率，{reason_ctx}，默认为E2",
            "_extracted": extracted
        }

    @staticmethod
    def _extract_display_scenario(refined_scenario: Dict) -> str:
        for key in ("situational_description", "refined_scenario", "scenario_summary", "scenario"):
            value = refined_scenario.get(key, "")
            if value:
                return str(value)
        return ""

    def _build_context(self, main_scenario: str, vehicle_speed: str,
                       weather: str, road_surface: str, vehicle_state: str) -> str:
        parts = []
        if main_scenario:
            parts.append(f"主场景:{main_scenario}")
        if vehicle_speed:
            parts.append(f"车速:{vehicle_speed}")
        if vehicle_state:
            parts.append(f"车辆状态:{vehicle_state}")
        if weather:
            parts.append(f"天气:{weather}")
        if road_surface:
            parts.append(f"路面:{road_surface}")
        return "，".join(parts) if parts else "在当前驾驶场景下"


class ControllabilityReasoningEngine:
    """可控度合理性分析引擎（Step13）"""

    CONTROLLABILITY_LEVELS = {
        "C3": {"label": "难以控制", "keywords": ["difficult to control", "uncontrollable", "difficult to control or uncontrollable"]},
        "C2": {"label": "一般可控", "keywords": ["normally controllable", "normal to control", "controllable in general"]},
        "C1": {"label": "简单可控", "keywords": ["simply controllable", "easy to control"]},
        "C0": {"label": "可控", "keywords": ["well controllable", "controllable", "simply controllable"]}
    }

    def __init__(self, domain_policy: Optional[AVPDomainPolicy] = None):
        self.domain_policy = domain_policy
        self.ctrl_ref = None
        self._ctrl_ref_loaded = False

    def _ensure_controllability_ref(self):
        if not self._ctrl_ref_loaded:
            self.ctrl_ref = self._load_controllability_ref()
            self._ctrl_ref_loaded = True

    def _load_controllability_ref(self) -> Optional[Dict[str, Any]]:
        """从 ScorabilityRef.json 加载可控度覆写规则"""
        try:
            ref_path = os.path.join(os.path.dirname(__file__), "ScorabilityRef.json")
            if os.path.exists(ref_path):
                with open(ref_path, 'r', encoding='utf-8') as f:
                    ref_data = json.load(f)
                rules = ref_data.get("rules", [])
                logger.info(f"从 ScorabilityRef.json 加载了 {len(rules)} 条可控度规则")
                return ref_data
        except Exception as e:
            logger.warning(f"加载 ScorabilityRef.json 失败: {e}")
        return None

    def _match_controllability_rule(self, subsystem: str, guideword: str,
                                     hazard_event: str) -> Optional[Dict[str, Any]]:
        """匹配 ScorabilityRef 规则，匹配成功返回覆写结果"""
        self._ensure_controllability_ref()
        if not self.ctrl_ref:
            return None
        rules = self.ctrl_ref.get("rules", [])
        if not rules:
            return None

        sub_lower = subsystem.lower() if subsystem else ""
        gw_lower = guideword.lower() if guideword else ""
        haz_lower = hazard_event.lower()

        for rule in rules:
            conditions = rule.get("match_conditions", {})
            sub_kws = [k.lower() for k in conditions.get("subsystem_keywords", [])]
            haz_kws = [k.lower() for k in conditions.get("hazard_keywords", [])]
            gw_kws = [k.lower() for k in conditions.get("guideword_keywords", [])]

            sub_match = any(kw in sub_lower for kw in sub_kws) if sub_kws and sub_lower else False
            haz_match = any(kw in haz_lower for kw in haz_kws) if haz_kws else False
            gw_match = any(kw in gw_lower for kw in gw_kws) if gw_kws and gw_lower else False

            # Match logic:
            # 1. If hazard_keywords specified: subsystem AND hazard must match
            if haz_kws:
                if sub_match and haz_match:
                    matched = True
                elif sub_match and gw_kws and gw_match:
                    matched = True
                else:
                    matched = False
            elif gw_kws:  # No hazard keywords, only guideword filter
                if sub_match and gw_match:
                    matched = True
                else:
                    matched = False
            else:  # No optional filters, subsystem-only match
                if sub_match:
                    matched = True
                else:
                    matched = False

            if matched:
                default_c = rule.get("default_controllability", "")
                rationale = rule.get("rationale_template", "")
                logger.info(f"  [Controllability Rule] 规则 {rule.get('rule_id', '')} 匹配: "
                            f"subsystem={subsystem}, GW={guideword} → C={default_c}")
                return {
                    "hazard_event": hazard_event,
                    "controllability_score": default_c,
                    "reasoning": rationale,
                    "logic_types_used": ["ScorabilityRef rule override"],
                    "_rule_id": rule.get("rule_id", "")
                }
        return None

    def analyze(self, hazard_event: str, subsystem: str = "", guideword: str = "",
                refined_scenario: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行可控度合理性分析"""
        logger.info(f"分析可控度: {hazard_event[:60]}...")

        policy_decision = self.domain_policy.controllability_decision(refined_scenario or {}) if self.domain_policy else None
        if (self.domain_policy and self.domain_policy.is_structured_scenario(refined_scenario or {})
                and not policy_decision):
            raise ValueError("结构化AVP场景缺少Controllability Domain Policy判据，禁止回退到关键词评分")
        if policy_decision:
            return {
                "hazard_event": hazard_event,
                "controllability_score": policy_decision["score"],
                "reasoning": policy_decision["basis"],
                "logic_types_used": ["Domain Profile structured driver-state rule"],
                "engineering_rule_id": policy_decision["rule_id"],
                "engineering_rule_version": policy_decision["rule_version"],
                "engineering_status": policy_decision["engineering_status"],
            }

        # Compatibility path for unstructured or non-domain inputs.
        rule_result = self._match_controllability_rule(subsystem, guideword, hazard_event)
        if rule_result:
            return rule_result

        hazard_lower = hazard_event.lower()
        logic_analysis = self._analyze_logic_types(hazard_lower)
        level = self._determine_level(hazard_lower, logic_analysis)
        reason = self._build_reason(level, logic_analysis, hazard_event)
        used_logic = [lt for lt, info in logic_analysis.items() if info["matched"]]

        return {
            "hazard_event": hazard_event,
            "controllability_score": level,
            "reasoning": reason,
            "logic_types_used": used_logic
        }

    def _analyze_logic_types(self, hazard_lower: str) -> Dict[str, Dict]:
        reaction_blocked = any(kw in hazard_lower for kw in [
            "无法及时", "来不及", "毫无征兆", "unable to react", "no warning",
        ])
        physical_blocked = any(kw in hazard_lower for kw in [
            "无法直接接管", "无法接管", "unable to take over", "driver outside",
        ])
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
                              "timely", "及时", "足够"]) and not reaction_blocked
            },
            "物理干预": {
                "keywords": ["brake", "steering", "steer", "change lane", "stop", "avoid", "apply brakes",
                            "apply steering", "slow down", "slowing", "wiper", "switch", "break the window",
                            "打开车门", "刹车", "转向", "变道", "停车", "躲避", "制动"],
                "matched": any(kw in hazard_lower for kw in ["brake", "steering", "steer", "change lane",
                              "stop", "avoid", "slow down", "wiper", "switch", "刹车", "转向", "变道", "停"])
                           and not physical_blocked
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

    def _determine_level(self, hazard_lower: str, logic_analysis: Dict) -> str:
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

    def _build_reason(self, level: str, logic_analysis: Dict, hazard_event: str) -> str:
        level_info = self.CONTROLLABILITY_LEVELS.get(level, {"label": "一般可控"})
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

        hazard_lower = hazard_event.lower()
        if level == "C3" and any(kw in hazard_lower for kw in [
            "驾驶员位于车外", "无法及时", "来不及", "毫无征兆", "no warning",
        ]):
            logic_description = "驾驶员位于车外且异常缺少充分预警，无法通过方向盘或制动踏板及时直接接管"
        else:
            logic_description = "，".join(logic_parts) if logic_parts else "通过常规驾驶操作可控制"

        return f"[{level} {level_label}] - {logic_description}。{hazard_event[:60]}..."


class ASILDeterminer:
    """ASIL判定引擎（Step15）：以模板 ASIL_Table 为唯一判定源。"""

    def __init__(self, template_path: Optional[str] = None):
        self.lookup = TemplateASILService(template_path)
        self.matrix = self.lookup.matrix
        self.source = self.lookup.source

    def determine(self, s: str, e: str, c: str) -> str:
        return self.lookup.determine(s, e, c)


class SafetyGoalGenerator:
    """安全目标生成引擎（Step16）"""

    def generate(self, malfunction: str, hazard_event: str, scenario: str,
                 guideword: str = "") -> str:
        guideword_text = guideword if guideword else "功能异常"
        malfunction_text = malfunction if malfunction else "未知失效"
        scenario_text = scenario.split(" (补充条件:")[0] if scenario else "特定场景"
        hazard_text = hazard_event if hazard_event else "安全隐患"

        return f"避免因{guideword_text}{malfunction_text}导致在{scenario_text}发生{hazard_text}"


AVPSafetyGoalCatalog = SafetyGoalCatalogService


class SafetyStateGenerator:
    """安全状态推理引擎（Step17）"""

    def __init__(self):
        self.state_ref = None
        self._state_ref_loaded = False

    def _ensure_state_ref(self):
        if not self._state_ref_loaded:
            self.state_ref = self._load_safety_state_ref()
            self._state_ref_loaded = True

    def _load_safety_state_ref(self) -> Optional[Dict[str, Any]]:
        """从 SafetyStateRef.json 加载安全状态推理规则"""
        try:
            ref_path = os.path.join(os.path.dirname(__file__), "SafetyStateRef.json")
            if os.path.exists(ref_path):
                with open(ref_path, 'r', encoding='utf-8') as f:
                    ref_data = json.load(f)
                patterns = ref_data.get("patterns", [])
                logger.info(f"从 SafetyStateRef.json 加载了 {len(patterns)} 条安全状态规则")
                return ref_data
        except Exception as e:
            logger.warning(f"加载 SafetyStateRef.json 失败: {e}")
        return None

    def _match_safe_state(self, subsystem: str, guideword: str,
                          safety_goal: str) -> Optional[str]:
        """匹配 SafetyStateRef 规则，匹配成功返回安全状态"""
        self._ensure_state_ref()
        if not self.state_ref:
            return None
        patterns = self.state_ref.get("patterns", [])
        if not patterns:
            return None

        sub_lower = subsystem.lower() if subsystem else ""
        gw_lower = guideword.lower() if guideword else ""
        goal_lower = safety_goal.lower() if safety_goal else ""

        # Try exact subsystem + guideword match first
        for pattern in patterns:
            sub_kws = [k.lower() for k in pattern.get("subsystem_keywords", [])]
            gw_kws = [k.lower() for k in pattern.get("guideword_keywords", [])]

            # A subsystem-scoped rule must never match when the upstream
            # subsystem is empty.  The previous `else True` was the source of
            # AVP safety states being populated with Wiper text.
            sub_match = any(kw in sub_lower for kw in sub_kws) if sub_kws and sub_lower else not sub_kws
            gw_match = any(kw in gw_lower for kw in gw_kws) if gw_kws and gw_lower else False

            # Match requires subsystem match AND guideword match (if both specified)
            if sub_kws:
                if not sub_match:
                    continue
            if gw_kws and gw_lower:
                if not gw_match:
                    continue
            safe_state = pattern.get("safe_state_en", pattern.get("safe_state_cn", ""))
            if safe_state:
                logger.info(f"  [SafetyState Rule] 规则 {pattern.get('pattern_id', '')} 匹配: "
                            f"subsystem={subsystem}, GW={guideword}")
                return safe_state

        # Fallback: try subsystem-only match (without guideword)
        for pattern in patterns:
            sub_kws = [k.lower() for k in pattern.get("subsystem_keywords", [])]
            gw_kws = pattern.get("guideword_keywords", [])
            if not gw_kws and sub_kws and sub_lower:
                sub_match = any(kw in sub_lower for kw in sub_kws)
                if sub_match:
                    safe_state = pattern.get("safe_state_en", pattern.get("safe_state_cn", ""))
                    if safe_state:
                        return safe_state
        return None

    def infer(self, safety_goal: str, function_name: str = "",
              subsystem: str = "", guideword: str = "") -> str:
        goal_lower = safety_goal.lower()

        # AVP needs an item-specific minimum-risk condition.  Evaluate it
        # before the reference library's generic catch-all rule.
        if "avp" in (subsystem or "").lower() or "泊车" in goal_lower:
            if "驻车制动" in function_name:
                return "停止车辆，切断驱动扭矩并施加可用的冗余驻车保持能力，向远程用户报警"
            if "报警" in function_name:
                return "触发独立故障提示；若无法确认远程用户已收到警告，则受控制动至静止并保持驻车"
            if any(token in function_name for token in ("开启", "激活")):
                return "禁止AVP激活，保持车辆静止并向远程用户报告前置条件或系统故障"
            return "受控制动至静止，驱动扭矩置零并保持驻车；退出AVP控制并向远程用户报警"

        # First check SafetyStateRef rules
        if subsystem or guideword:
            ref_result = self._match_safe_state(subsystem, guideword, safety_goal)
            if ref_result:
                return ref_result

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
            return f"关闭{function_name}输出并提醒驾驶员"


class ScoringSGEngine:
    """打分与安全目标引擎主类"""

    def __init__(self, malfunction_path: str, scen_hazevent_path: str,
                 template_path: Optional[str] = None,
                 domain_profile_path: Optional[str] = None):
        """
        初始化引擎

        Args:
            malfunction_path: malfunction_output.json 路径
            scen_hazevent_path: scen_hazevent_output.json 路径
        """
        self.malfunction_path = malfunction_path
        self.scen_hazevent_path = scen_hazevent_path
        self.template_path = template_path
        self.domain_profile = load_domain_profile(
            "avp", path=domain_profile_path, require_approved=False
        )
        self.domain_policy = AVPDomainPolicy(self.domain_profile)
        self.asil_determiner = ASILDeterminer(template_path)
        template_inputs = TemplateInputReader().read(self.asil_determiner.lookup.template_path)
        self.domain_scoring_service = DomainScoringService(
            self.domain_policy, template_inputs.scoring_standards
        )
        self.data = {}
        self.results = {}

        self.severity_engine = SeverityReasoningEngine()
        self.exposure_engine = ExposureReasoningEngine()
        self.controllability_engine = ControllabilityReasoningEngine()
        self.ftti_estimator = FTTIService()
        self.risk_aggregation_service = RiskAggregationService()
        self.safety_goal_generator = SafetyGoalGenerator()
        self.safety_state_generator = SafetyStateGenerator()
        self.avp_safety_goal_catalog = AVPSafetyGoalCatalog(self.domain_profile)

    @staticmethod
    def _tag_legacy_scoring_result(result: Dict[str, Any], dimension: str):
        if "engineering_rule_id" not in result:
            result.update({
                "engineering_rule_id": f"LEGACY-TEXT-{dimension.upper()}",
                "engineering_rule_version": "v13-compatibility",
                "engineering_status": "PENDING",
                "_source": "legacy_text_scoring_compatibility",
            })

    @staticmethod
    def _object_risk_class(scenario: Dict[str, Any]) -> str:
        object_type = str(scenario.get("object_type", "")).lower()
        if "行人" in object_type or "pedestrian" in object_type:
            return "pedestrian"
        if any(token in object_type for token in ("车辆", "vehicle", "car")):
            return "vehicle"
        if any(token in object_type for token in ("墙", "柱", "wall", "column")):
            return "fixed_object"
        if any(token in object_type for token in ("无近距离", "none", "无冲突")):
            return "no_conflict_object"
        return object_type or "unspecified"

    @staticmethod
    def _speed_band(value: Any) -> str:
        try:
            speed = abs(float(value))
        except (TypeError, ValueError):
            return "unknown"
        if speed == 0:
            return "standstill"
        if speed <= 5:
            return "avp_low"
        if speed <= 15:
            return "approaching_low"
        return "approaching_high"

    @classmethod
    def _motion_risk_class(cls, scenario: Dict[str, Any]) -> str:
        """Describe motion facts that must survive numeric S/E/C grouping."""
        maneuver = str(scenario.get("maneuver", "")).strip().lower()
        direction = str(scenario.get("parking_direction", "")).strip().lower()
        target_band = cls._speed_band(scenario.get("target_speed_kph"))
        ego_band = cls._speed_band(scenario.get("ego_speed_kph"))
        return "|".join((maneuver, direction, ego_band, target_band))

    @staticmethod
    def _driver_intervention_class(scenario: Dict[str, Any]) -> str:
        driver_state = str(scenario.get("driver_state", "")).lower()
        if any(token in driver_state for token in (
            "车外", "已离车", "无法直接", "outside", "remote"
        )):
            return "remote_no_direct_control"
        if any(token in driver_state for token in (
            "车内", "可接管", "inside", "takeover"
        )):
            return "direct_intervention_available"
        return "unspecified"

    @classmethod
    def _select_risk_representatives(
        cls,
        scenarios: List[Dict[str, Any]],
        severity: List[Dict[str, Any]],
        exposure: List[Dict[str, Any]],
        controllability: List[Dict[str, Any]],
        asil: List[Dict[str, Any]],
        safety_goals: List[Dict[str, Any]],
        ftti_results: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[int], List[Dict[str, Any]]]:
        """Choose one row per safety-relevant risk equivalence class.

        Grouping is intentionally performed after S/E/C scoring.  The object
        class and collision geometry remain part of the signature so a pedestrian
        event is never collapsed into a vehicle or fixed-object event merely
        because the numerical ratings happen to match.
        """
        seen = {}
        selected = []
        groups = []
        for idx, scenario in enumerate(scenarios):
            s = severity[idx].get("severity_score", "") if idx < len(severity) else ""
            e = exposure[idx].get("exposure_score", "") if idx < len(exposure) else ""
            c = controllability[idx].get("controllability_score", "") if idx < len(controllability) else ""
            a = asil[idx].get("ASIL", "") if idx < len(asil) else ""
            sg = safety_goals[idx] if idx < len(safety_goals) else {}
            sg_key = sg.get("sg_id") or sg.get("safety_goal", "")
            ftti = ftti_results[idx] if ftti_results and idx < len(ftti_results) else {}
            ftti_key = (
                ftti.get("ftti_status", ""),
                ftti.get("ftti_value_s"),
                ftti.get("formula_id", ""),
            )
            signature = (
                cls._object_risk_class(scenario),
                str(scenario.get("collision_geometry", "")).lower(),
                cls._motion_risk_class(scenario),
                cls._driver_intervention_class(scenario),
                s, e, c, a, sg_key, ftti_key,
            )
            scenario_id = scenario.get("scenario_id", "")
            if signature in seen:
                groups[seen[signature]]["covered_scenario_ids"].append(scenario_id)
                continue
            seen[signature] = len(groups)
            selected.append(idx)
            groups.append({
                "risk_signature": list(signature),
                "representative_scenario_id": scenario_id,
                "covered_scenario_ids": [scenario_id],
            })
        return selected, groups

    def load_inputs(self) -> bool:
        """加载上游输入数据"""
        try:
            with open(self.malfunction_path, 'r', encoding='utf-8') as f:
                malf_data = json.load(f)
            self.data["malfunction"] = malf_data

            with open(self.scen_hazevent_path, 'r', encoding='utf-8') as f:
                scen_data = json.load(f)
            self.data["scen_hazevent"] = scen_data

            logger.info(f"成功加载上游数据:")
            logger.info(f"  - malfunction_output: {len(malf_data.get('functions', []))} 个功能")
            logger.info(f"  - scen_hazevent_output: {len(scen_data.get('function_scenarios', {}))} 个功能场景")
            return True

        except FileNotFoundError as e:
            logger.error(f"上游文件不存在: {e}")
            return False
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            return False
        except Exception as e:
            logger.error(f"加载输入失败: {e}")
            return False

    def run(self) -> Dict[str, Any]:
        """执行完整的Step9-17流程"""
        # A controller may retry the same engine instance.  Aggregation must
        # be rebuilt from the current run instead of retaining old links.
        self.avp_safety_goal_catalog = AVPSafetyGoalCatalog(self.domain_profile)
        if not self.load_inputs():
            return {"success": False, "error": "加载输入数据失败"}

        logger.info("=" * 60)
        logger.info("开始执行 Step9-17: 严重度/暴露度/可控度打分及安全目标生成")
        logger.info("=" * 60)

        malf_data = self.data["malfunction"]
        scen_data = self.data["scen_hazevent"]

        functions = malf_data.get("functions", [])
        function_malfunctions = malf_data.get("function_malfunctions", {})
        function_hazards = malf_data.get("function_hazards", {})
        function_scenarios = scen_data.get("function_scenarios", {})
        hazard_events_data = scen_data.get("hazard_events", {})
        scenario_catalog = scen_data.get("scenario_catalog", {})

        # Extract subsystem from metadata
        subsystem = ""
        metadata = malf_data.get("metadata", {})
        if metadata:
            subsystem = metadata.get("subsystem", "")
            if not subsystem:
                doc = metadata.get("source_document", "")
                doc_lower = doc.lower() if doc else ""
                for kw in ["wiper", "雨刷", "雨刮", "window", "车窗", "door", "车门",
                           "light", "照明", "seat", "座椅", "ota", "hud"]:
                    if kw in doc_lower:
                        subsystem = kw.split(" ")[0].title() if "." not in kw else kw.lower()
                        break
        if not subsystem:
            function_text = " ".join(str(func) for func in functions).lower()
            if ("avp" in function_text or "泊车" in function_text
                    or all(token in function_text for token in ("制动扭矩", "驱动扭矩", "转向扭矩"))):
                subsystem = "avp"

        severity_results = {}
        exposure_results = {}
        controllability_results = {}
        asil_results = {}
        ftti_results = {}
        safety_goals = {}
        safe_states = {}
        report_scenario_ids = {}
        risk_group_results = {}

        total_s = 0
        total_e = 0
        total_c = 0
        total_asil = 0
        total_ftti = 0
        total_sg = 0
        total_ss = 0
        total_report_rows = 0

        for func in functions:
            malf_list = function_malfunctions.get(func, [])
            hazards_list = function_hazards.get(func, [])
            scenarios_by_malf = function_scenarios.get(func, {})
            haz_events_by_malf = hazard_events_data.get(func, {})

            func_severity = {}
            func_exposure = {}
            func_controllability = {}
            func_asil = {}
            func_ftti = {}
            func_sg = {}
            func_ss = {}
            func_report_scenarios = {}
            func_risk_groups = {}

            for malf_item in malf_list:
                malf_desc = malf_item.get("malfunction_description", malf_item.get("malfunction", ""))
                guideword = malf_item.get("guideword", "")
                if not malf_desc:
                    continue

                composite_key = f"{malf_desc}||{guideword}" if guideword else malf_desc
                malf_scenarios = scenarios_by_malf.get(composite_key, scenarios_by_malf.get(malf_desc, []))
                malf_haz_events = haz_events_by_malf.get(composite_key, haz_events_by_malf.get(malf_desc, []))

                severity_by_gw = {}
                exposure_by_gw = {}
                controllability_by_gw = {}
                asil_by_gw = {}
                ftti_by_gw = {}
                sg_by_gw = {}
                ss_by_gw = {}

                for i, scenario in enumerate(malf_scenarios):
                    if isinstance(scenario, dict):
                        refined = dict(scenario)
                    else:
                        refined = {"refined_scenario": scenario}
                    scenario_id = refined.get("scenario_id", "")
                    if scenario_id and scenario_id in scenario_catalog:
                        # The catalog is the single source of truth.  Per-phase
                        # scenario dictionaries are compatibility/display views.
                        canonical = dict(scenario_catalog[scenario_id])
                        canonical["scenario_id"] = scenario_id
                        canonical["hazard_event"] = refined.get("hazard_event", "")
                        refined = canonical
                    situation_desc = refined.get("situational_description", "")
                    if not situation_desc:
                        situation_desc = refined.get("refined_scenario", refined.get("scenario_summary", ""))
                    if situation_desc and "refined_scenario" not in refined:
                        refined["refined_scenario"] = situation_desc

                    hazard_event = ""
                    if i < len(malf_haz_events):
                        haz = malf_haz_events[i]
                        if isinstance(haz, dict):
                            hazard_event = haz.get("hazard_event", "")
                        elif isinstance(haz, str):
                            hazard_event = haz
                        elif isinstance(haz, list) and haz:
                            hazard_event = haz[0].get("hazard_event", "") if isinstance(haz[0], dict) else str(haz[0])
                    if not hazard_event:
                        hazard_event = refined.get("hazard_event", "")

                    if self.domain_policy.is_structured_scenario(refined):
                        domain_scores = self.domain_scoring_service.score(refined, hazard_event)
                        s_result = domain_scores["severity"]
                        e_result = domain_scores["exposure"]
                        c_result = domain_scores["controllability"]
                    else:
                        s_result = self.severity_engine.analyze(hazard_event, refined)
                        e_result = self.exposure_engine.analyze(refined)
                        c_result = self.controllability_engine.analyze(
                            hazard_event,
                            subsystem=subsystem,
                            guideword=guideword,
                            refined_scenario=refined,
                        )
                    self._tag_legacy_scoring_result(s_result, "severity")
                    self._tag_legacy_scoring_result(e_result, "exposure")
                    self._tag_legacy_scoring_result(c_result, "controllability")
                    trace = {"scenario_id": scenario_id}
                    s_result.update(trace)
                    e_result.update(trace)
                    c_result.update(trace)

                    asil = self.asil_determiner.determine(
                        s_result["severity_score"],
                        e_result["exposure_score"],
                        c_result["controllability_score"]
                    )

                    if guideword not in severity_by_gw:
                        severity_by_gw[guideword] = []
                        exposure_by_gw[guideword] = []
                        controllability_by_gw[guideword] = []
                        asil_by_gw[guideword] = []
                        ftti_by_gw[guideword] = []
                        sg_by_gw[guideword] = []
                        ss_by_gw[guideword] = []

                    severity_by_gw[guideword].append(s_result)
                    exposure_by_gw[guideword].append(e_result)
                    controllability_by_gw[guideword].append(c_result)
                    asil_by_gw[guideword].append({
                        "scenario_id": scenario_id,
                        "severity": s_result["severity_score"],
                        "exposure": e_result["exposure_score"],
                        "controllability": c_result["controllability_score"],
                        "ASIL": asil
                    })
                    ftti_result = self.ftti_estimator.evaluate(refined, hazard_event, asil)
                    ftti_by_gw[guideword].append(ftti_result)
                    total_s += 1
                    total_e += 1
                    total_c += 1
                    total_asil += 1
                    total_ftti += 1

                    if asil in ["A", "B", "C", "D"]:
                        avp_sg_id = self.avp_safety_goal_catalog.classify(func) if subsystem.lower() == "avp" else None
                        if subsystem.lower() == "avp" and not avp_sg_id:
                            raise ValueError(
                                f"AVP功能缺少Safety Goal Domain Profile映射: {func}"
                            )
                        if avp_sg_id:
                            sg_ref = self.avp_safety_goal_catalog.register(
                                avp_sg_id, func, malf_desc, guideword,
                                scenario_id, hazard_event, asil, ftti_result,
                            )
                            sg_by_gw[guideword].append({
                                "scenario_id": scenario_id,
                                "ASIL": asil,
                                "sg_id": sg_ref["sg_id"],
                                "safety_goal": sg_ref["safety_goal"],
                                "ftti_requirement": ftti_result["ftti_requirement"],
                                "ftti_status": ftti_result["ftti_status"],
                            })
                        else:
                            sg = self.safety_goal_generator.generate(
                                malf_desc, hazard_event,
                                refined.get("refined_scenario", ""),
                                guideword
                            )
                            sg_by_gw[guideword].append({
                                "scenario_id": scenario_id, "ASIL": asil, "safety_goal": sg,
                                "ftti_requirement": ftti_result["ftti_requirement"],
                                "ftti_status": ftti_result["ftti_status"],
                            })
                    else:
                        sg_by_gw[guideword].append({"scenario_id": scenario_id, "ASIL": asil, "safety_goal": "NA"})
                    total_sg += 1

                    last_sg = sg_by_gw[guideword][-1]
                    if last_sg["safety_goal"] == "NA":
                        ss_by_gw[guideword].append({"scenario_id": scenario_id, "safety_goal": "NA", "safety_state": "NA"})
                    else:
                        sg_id = last_sg.get("sg_id", "")
                        if sg_id in self.avp_safety_goal_catalog.definitions:
                            ss = self.avp_safety_goal_catalog.definitions[sg_id]["safety_state"]
                        else:
                            ss = self.safety_state_generator.infer(last_sg["safety_goal"], func,
                                                                    subsystem=subsystem, guideword=guideword)
                        ss_by_gw[guideword].append({
                            "scenario_id": scenario_id,
                            "sg_id": sg_id,
                            "safety_goal": last_sg["safety_goal"],
                            "safety_state": ss,
                        })
                    total_ss += 1

                # Candidate scenarios are evaluated first, then deduplicated by
                # their safety-relevant risk signature.  This preserves different
                # objects/S/E/C results without exporting numeric permutations that
                # lead to the same engineering conclusion.
                if guideword in severity_by_gw:
                    selected_indices, groups = self.risk_aggregation_service.select(
                        malf_scenarios,
                        severity_by_gw.get(guideword, []),
                        exposure_by_gw.get(guideword, []),
                        controllability_by_gw.get(guideword, []),
                        asil_by_gw.get(guideword, []),
                        sg_by_gw.get(guideword, []),
                        ftti_by_gw.get(guideword, []),
                    )
                    for collection in (
                        severity_by_gw, exposure_by_gw, controllability_by_gw,
                        asil_by_gw, ftti_by_gw, sg_by_gw, ss_by_gw,
                    ):
                        values = collection.get(guideword, [])
                        collection[guideword] = [
                            values[idx] for idx in selected_indices if idx < len(values)
                        ]
                    func_report_scenarios[composite_key] = {
                        guideword: [
                            malf_scenarios[idx].get("scenario_id", "")
                            for idx in selected_indices if idx < len(malf_scenarios)
                        ]
                    }
                    func_risk_groups[composite_key] = {guideword: groups}
                    total_report_rows += len(selected_indices)

                if severity_by_gw:
                    func_severity[composite_key] = severity_by_gw
                if exposure_by_gw:
                    func_exposure[composite_key] = exposure_by_gw
                if controllability_by_gw:
                    func_controllability[composite_key] = controllability_by_gw
                if asil_by_gw:
                    func_asil[composite_key] = asil_by_gw
                if ftti_by_gw:
                    func_ftti[composite_key] = ftti_by_gw
                if sg_by_gw:
                    func_sg[composite_key] = sg_by_gw
                if ss_by_gw:
                    func_ss[composite_key] = ss_by_gw

            if func_severity:
                severity_results[func] = func_severity
            if func_exposure:
                exposure_results[func] = func_exposure
            if func_controllability:
                controllability_results[func] = func_controllability
            if func_asil:
                asil_results[func] = func_asil
            if func_ftti:
                ftti_results[func] = func_ftti
            if func_sg:
                safety_goals[func] = func_sg
            if func_ss:
                safe_states[func] = func_ss
            if func_report_scenarios:
                report_scenario_ids[func] = func_report_scenarios
            if func_risk_groups:
                risk_group_results[func] = func_risk_groups

        self.results = {
            "severity_results": severity_results,
            "exposure_results": exposure_results,
            "controllability_results": controllability_results,
            "asil_results": asil_results,
            "ftti_results": ftti_results,
            "safety_goals": safety_goals,
            "safe_states": safe_states,
            "report_scenario_ids": report_scenario_ids,
            "risk_group_results": risk_group_results,
            "safety_goal_catalog": self.avp_safety_goal_catalog.to_dict(),
            "scenario_catalog": scenario_catalog,
            "metadata": {
                "source_document": os.path.basename(self.malfunction_path),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "engine": "scoring_sg_engine",
                "subsystem": subsystem,
                "domain_profile": {
                    "name": self.avp_safety_goal_catalog.profile.name,
                    "version": self.avp_safety_goal_catalog.profile.version,
                    "approval_status": self.avp_safety_goal_catalog.profile.approval_status,
                    "source": self.avp_safety_goal_catalog.profile.source,
                    "path": str(self.avp_safety_goal_catalog.profile.path),
                } if subsystem.lower() == "avp" else None,
                "total_severity": total_s,
                "total_exposure": total_e,
                "total_controllability": total_c,
                "total_asil": total_asil,
                "total_ftti": total_ftti,
                "total_safety_goals": total_sg,
                "total_unique_safety_goals": len(self.avp_safety_goal_catalog.catalog),
                "total_safe_states": total_ss,
                "total_evaluated_scenarios": total_asil,
                "total_report_risk_classes": total_report_rows,
                "risk_grouping_policy": "object_class+collision_geometry+motion_relation+driver_intervention+S+E+C+ASIL+safety_goal+FTTI",
                "ftti_method": "scenario_candidate_then_minimum_per_safety_goal",
                "ftti_formula_status": "DRAFT unless upstream value is explicitly approved",
                "asil_determination_method": "template_matrix_lookup",
                "asil_matrix_source": self.asil_determiner.source,
                "asil_matrix_entries": len(self.asil_determiner.matrix),
            }
        }

        logger.info(f"执行完成:")
        logger.info(f"  - 严重度分析: {total_s} 项")
        logger.info(f"  - 暴露度分析: {total_e} 项")
        logger.info(f"  - 可控度分析: {total_c} 项")
        logger.info(f"  - ASIL判定: {total_asil} 项")
        logger.info(f"  - FTTI候选/评审状态: {total_ftti} 项")
        logger.info(f"  - 安全目标生成: {total_sg} 项")
        logger.info(f"  - 安全状态推理: {total_ss} 项")

        return {"success": True, **self.results}

    def save_output(self, output_path: str) -> bool:
        """保存输出结果"""
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            logger.info(f"结果已保存到: {output_path}")
            return True
        except Exception as e:
            logger.error(f"保存结果失败: {e}")
            return False


def main():
    parser = argparse.ArgumentParser(description="Scoring & Safety Goal Engine (Steps 9-17)")
    parser.add_argument("--malfunction", "-m", required=True, help="malfunction_output.json 路径")
    parser.add_argument("--scen-hazevent", "-s", required=True, help="scen_hazevent_output.json 路径")
    parser.add_argument(
        "--template", "-t", required=False,
        help="包含 ASIL_Table 的 HARA Excel 模板；未指定时使用项目默认模板",
    )
    parser.add_argument("--output", "-o", required=True, help="scoring_sg_output.json 输出路径")
    parser.add_argument("--domain-profile", help="显式Domain Profile JSON路径")
    args = parser.parse_args()

    engine = ScoringSGEngine(
        args.malfunction, args.scen_hazevent, args.template, args.domain_profile
    )
    result = engine.run()

    if result.get("success"):
        engine.save_output(args.output)
        print(f"\n成功! 输出文件: {args.output}")
    else:
        print(f"\n失败: {result.get('error')}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())

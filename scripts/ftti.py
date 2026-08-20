#!/usr/bin/env python3
"""Auditable FTTI candidate calculation for HARA outputs.

TTC is recorded only as a screening input.  It is never copied into the FTTI
field.  Calculated values remain NEEDS_REVIEW until an upstream record supplies
an explicitly approved FTTI value and source.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, Optional


FTTI_FINALIZED = "FINALIZED"
FTTI_NEEDS_REVIEW = "NEEDS_REVIEW"
FTTI_NOT_REQUIRED = "NOT_REQUIRED"


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def format_ftti_requirement(value_s: Optional[float], status: str) -> str:
    if status == FTTI_NOT_REQUIRED:
        return "NA"
    if value_s is None:
        return FTTI_NEEDS_REVIEW
    return f"≤ {value_s:.2f} s"


class FTTIEstimator:
    """Calculate a scenario-level FTTI candidate plus its audit evidence.

    The default longitudinal candidate follows the Mentor reference formula:
    sqrt(2 * clearance / |a_action|).  The default action deceleration is only
    a draft engineering assumption, so the result cannot become FINALIZED.
    """

    DEFAULT_ACTION_DECELERATION_MPS2 = 12.0
    FORMULA_SOURCE = "Mentor HARA七步流程参考: longitudinal_t1_t2 / lateral_simple"

    def __init__(self, action_deceleration_mps2: float = DEFAULT_ACTION_DECELERATION_MPS2):
        self.action_deceleration_mps2 = abs(float(action_deceleration_mps2))

    @staticmethod
    def _is_no_conflict(scenario: Dict[str, Any], hazard_event: str) -> bool:
        geometry = str(scenario.get("collision_geometry", "")).lower()
        object_type = str(scenario.get("object_type", "")).lower()
        if geometry in {"none", "no collision", "n/a"}:
            return True
        if any(token in object_type for token in ("无近距离", "无冲突", "no conflict")):
            return True
        return not hazard_event and not geometry

    @staticmethod
    def _explicit_value(scenario: Dict[str, Any]) -> Optional[float]:
        for key in ("ftti_value_s", "ftti_seconds", "ftti_s"):
            value = _number(scenario.get(key))
            if value is not None and value > 0:
                return value
        return None

    def evaluate(self, scenario: Dict[str, Any], hazard_event: str, asil: str) -> Dict[str, Any]:
        scenario = scenario or {}
        scenario_id = str(scenario.get("scenario_id", ""))
        base = {
            "scenario_id": scenario_id,
            "ASIL": asil,
            "ftti_value_s": None,
            "ftti_requirement": "NA" if asil == "QM" else FTTI_NEEDS_REVIEW,
            "ftti_status": FTTI_NOT_REQUIRED if asil == "QM" else FTTI_NEEDS_REVIEW,
            "formula_id": "not_applicable" if asil == "QM" else "unresolved",
            "formula_status": "NOT_APPLICABLE" if asil == "QM" else "DRAFT",
            "confidence": "N/A" if asil == "QM" else "LOW",
            "ttc_screening_s": None,
            "calculation_inputs": {},
            "assumptions": [],
            "basis": "QM记录不生成Safety Goal或FTTI要求。" if asil == "QM" else "FTTI需工程评审。",
            "review_reason": "" if asil == "QM" else "缺少经项目批准的FTTI参数或值。",
            "source": self.FORMULA_SOURCE,
        }
        if asil == "QM":
            return base

        explicit = self._explicit_value(scenario)
        if explicit is not None:
            status_text = str(
                scenario.get("ftti_approval_status", scenario.get("ftti_status", ""))
            ).upper()
            source = str(scenario.get("ftti_source", "")).strip()
            finalized = status_text in {"FINALIZED", "APPROVED"} and bool(source)
            base.update({
                "ftti_value_s": round(explicit, 3),
                "ftti_status": FTTI_FINALIZED if finalized else FTTI_NEEDS_REVIEW,
                "formula_id": "upstream_explicit",
                "formula_status": "FINALIZED" if finalized else "DRAFT",
                "confidence": "HIGH" if finalized else "MEDIUM",
                "calculation_inputs": {"upstream_ftti_value_s": explicit},
                "basis": f"上游FTTI值≤{explicit:.2f}s；" + ("已批准。" if finalized else "来源/批准状态待评审。"),
                "review_reason": "" if finalized else "上游FTTI未同时提供批准状态和可追溯来源。",
                "source": source or self.FORMULA_SOURCE,
            })
            base["ftti_requirement"] = format_ftti_requirement(explicit, base["ftti_status"])
            return base

        distance_m = _number(
            scenario.get("obj_distance_m", scenario.get("relative_distance"))
        )
        relative_speed_kph = _number(scenario.get("relative_speed_kph"))
        if distance_m is not None and relative_speed_kph is not None and relative_speed_kph > 0:
            base["ttc_screening_s"] = round(distance_m / (relative_speed_kph / 3.6), 3)

        geometry = str(scenario.get("collision_geometry", "")).lower()
        if self._is_no_conflict(scenario, hazard_event):
            base.update({
                "formula_id": "non_collision_pending",
                "calculation_inputs": {
                    "distance_m": distance_m,
                    "relative_speed_kph": relative_speed_kph,
                },
                "basis": "非碰撞类Safety Goal；需基于功能时序和故障反应链确定FTTI。",
                "review_reason": "缺少诊断、决策、通信和执行器时序预算。",
            })
            return base

        if distance_m is None or distance_m <= 0:
            base.update({
                "formula_id": "missing_clearance",
                "basis": "已识别安全相关危害，但无有效相对距离，无法计算FTTI候选值。",
                "review_reason": "缺少可追溯的初始间隙/相对距离。",
            })
            return base

        inputs = {
            "distance_m": round(distance_m, 3),
            "relative_speed_kph": relative_speed_kph,
        }
        if any(token in geometry for token in ("side", "cross", "lateral", "侧", "交叉")):
            ego_lat = _number(scenario.get("v_ego_lateral_mps"))
            other_lat = _number(scenario.get("v_other_lateral_mps"))
            inputs.update({"v_ego_lateral_mps": ego_lat, "v_other_lateral_mps": other_lat})
            closing_lat = (ego_lat or 0.0) + (other_lat or 0.0)
            if closing_lat <= 0:
                base.update({
                    "formula_id": "lateral_simple",
                    "calculation_inputs": inputs,
                    "basis": "侧向FTTI公式已匹配，但横向接近速度缺失。",
                    "review_reason": "缺少自车/目标物横向速度。",
                })
                return base
            candidate = distance_m / closing_lat
            formula_id = "lateral_simple"
            assumptions = ["侧向相对速度在计算窗口内保持不变"]
        else:
            candidate = math.sqrt(2.0 * distance_m / self.action_deceleration_mps2)
            formula_id = "longitudinal_t1_t2_draft"
            inputs["action_deceleration_mps2"] = self.action_deceleration_mps2
            assumptions = [
                f"采用参考制动减速度{self.action_deceleration_mps2:.1f}m/s²",
                "T1/T2时序预算尚未由项目数据标定",
            ]

        candidate = round(candidate, 3)
        base.update({
            "ftti_value_s": candidate,
            "ftti_status": FTTI_NEEDS_REVIEW,
            "ftti_requirement": format_ftti_requirement(candidate, FTTI_NEEDS_REVIEW),
            "formula_id": formula_id,
            "formula_status": "DRAFT",
            "confidence": "MEDIUM",
            "calculation_inputs": inputs,
            "assumptions": assumptions,
            "basis": f"候选FTTI≤{candidate:.2f}s（{formula_id}）；仅用于工程评审。",
            "review_reason": "参考公式及时序预算未经本项目批准，不得用于正式发布。",
        })
        return base


def aggregate_sg_ftti(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    values = [
        float(item["ftti_value_s"])
        for item in results
        if item.get("ftti_value_s") is not None
    ]
    statuses = [str(item.get("ftti_status", "")) for item in results]
    value_s = min(values) if values else None
    finalized = bool(statuses) and all(status == FTTI_FINALIZED for status in statuses)
    status = FTTI_FINALIZED if finalized else FTTI_NEEDS_REVIEW
    return {
        "ftti_value_s": value_s,
        "ftti_requirement": format_ftti_requirement(value_s, status),
        "ftti_status": status,
        "ftti_basis": (
            f"最严值≤{value_s:.2f}s；" + ("已批准。" if finalized else "公式/参数待评审。")
            if value_s is not None
            else "无可用候选值；需补充项目FTTI时序与边界条件。"
        ),
    }

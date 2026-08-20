from __future__ import annotations

import math
import re
from typing import Any, Optional


FTTI_FINALIZED = "FINALIZED"
FTTI_NEEDS_REVIEW = "NEEDS_REVIEW"
FTTI_NOT_REQUIRED = "NOT_REQUIRED"


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    number = float(match.group(0)) if match else None
    return number if number is not None and math.isfinite(number) else None


def _requirement(value_s: Optional[float], status: str) -> str:
    if status == FTTI_NOT_REQUIRED:
        return "NA"
    if value_s is None:
        return FTTI_NEEDS_REVIEW
    return f"≤ {value_s:.2f} s"


class FTTIService:
    """Calculate auditable FTTI candidates without treating TTC as FTTI."""

    DEFAULT_ACTION_DECELERATION_MPS2 = 12.0
    FORMULA_SOURCE = "Mentor HARA七步流程参考: longitudinal_t1_t2 / lateral_simple"

    def __init__(self, action_deceleration_mps2: float = DEFAULT_ACTION_DECELERATION_MPS2):
        value = float(action_deceleration_mps2)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("FTTI action_deceleration_mps2必须为有限正数")
        self.action_deceleration_mps2 = value

    def evaluate(self, scenario: dict[str, Any], hazard_event: str, asil: str) -> dict[str, Any]:
        scenario = scenario or {}
        base = self._base(scenario, asil)
        if asil == "QM":
            return base

        explicit = self._explicit_value(scenario)
        if explicit is not None:
            return self._from_explicit(base, scenario, explicit)

        distance_m = _number(scenario.get("obj_distance_m", scenario.get("relative_distance")))
        relative_speed_kph = _number(scenario.get("relative_speed_kph"))
        if distance_m is not None and relative_speed_kph is not None and relative_speed_kph > 0:
            base["ttc_screening_s"] = round(distance_m / (relative_speed_kph / 3.6), 3)

        geometry = str(scenario.get("collision_geometry", "")).lower()
        if self._is_no_conflict(scenario, hazard_event):
            base.update({
                "formula_id": "non_collision_pending",
                "calculation_inputs": {"distance_m": distance_m, "relative_speed_kph": relative_speed_kph},
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

        inputs = {"distance_m": round(distance_m, 3), "relative_speed_kph": relative_speed_kph}
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
            "ftti_requirement": _requirement(candidate, FTTI_NEEDS_REVIEW),
            "formula_id": formula_id,
            "formula_status": "DRAFT",
            "confidence": "MEDIUM",
            "calculation_inputs": inputs,
            "assumptions": assumptions,
            "basis": f"候选FTTI≤{candidate:.2f}s（{formula_id}）；仅用于工程评审。",
            "review_reason": "参考公式及时序预算未经本项目批准，不得用于正式发布。",
        })
        return base

    def _base(self, scenario: dict[str, Any], asil: str) -> dict[str, Any]:
        is_qm = asil == "QM"
        return {
            "scenario_id": str(scenario.get("scenario_id", "")),
            "ASIL": asil,
            "ftti_value_s": None,
            "ftti_requirement": "NA" if is_qm else FTTI_NEEDS_REVIEW,
            "ftti_status": FTTI_NOT_REQUIRED if is_qm else FTTI_NEEDS_REVIEW,
            "formula_id": "not_applicable" if is_qm else "unresolved",
            "formula_status": "NOT_APPLICABLE" if is_qm else "DRAFT",
            "confidence": "N/A" if is_qm else "LOW",
            "ttc_screening_s": None,
            "calculation_inputs": {},
            "assumptions": [],
            "basis": "QM记录不生成Safety Goal或FTTI要求。" if is_qm else "FTTI需工程评审。",
            "review_reason": "" if is_qm else "缺少经项目批准的FTTI参数或值。",
            "source": self.FORMULA_SOURCE,
        }

    @staticmethod
    def _is_no_conflict(scenario: dict[str, Any], hazard_event: str) -> bool:
        geometry = str(scenario.get("collision_geometry", "")).lower()
        object_type = str(scenario.get("object_type", "")).lower()
        return (
            geometry in {"none", "no collision", "n/a"}
            or any(token in object_type for token in ("无近距离", "无冲突", "no conflict"))
            or (not hazard_event and not geometry)
        )

    @staticmethod
    def _explicit_value(scenario: dict[str, Any]) -> Optional[float]:
        for key in ("ftti_value_s", "ftti_seconds", "ftti_s"):
            value = _number(scenario.get(key))
            if value is not None and value > 0:
                return value
        return None

    def _from_explicit(self, base: dict[str, Any], scenario: dict[str, Any],
                       explicit: float) -> dict[str, Any]:
        status = str(scenario.get("ftti_approval_status", scenario.get("ftti_status", ""))).upper()
        source = str(scenario.get("ftti_source", "")).strip()
        finalized = status in {"FINALIZED", "APPROVED"} and bool(source)
        final_status = FTTI_FINALIZED if finalized else FTTI_NEEDS_REVIEW
        base.update({
            "ftti_value_s": round(explicit, 3),
            "ftti_status": final_status,
            "ftti_requirement": _requirement(explicit, final_status),
            "formula_id": "upstream_explicit",
            "formula_status": "FINALIZED" if finalized else "DRAFT",
            "confidence": "HIGH" if finalized else "MEDIUM",
            "calculation_inputs": {"upstream_ftti_value_s": explicit},
            "basis": f"上游FTTI值≤{explicit:.2f}s；" + ("已批准。" if finalized else "来源/批准状态待评审。"),
            "review_reason": "" if finalized else "上游FTTI未同时提供批准状态和可追溯来源。",
            "source": source or self.FORMULA_SOURCE,
        })
        return base

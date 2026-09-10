from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _status(evidence: Any) -> str:
    value = getattr(evidence, "status", "")
    return str(getattr(value, "value", value))


class EngineeringReportTextMapper:
    """Map committed runtime status to compact Chinese review text.

    This layer owns presentation wording only.  It neither supplements project
    facts nor makes a risk-method decision.
    """

    _PENDING_VALUE = "Pending"

    @staticmethod
    def _has_reason(value: Any, token: str) -> bool:
        return token in str(value or "").upper()

    @staticmethod
    def _display_mode(mode: Any) -> str:
        value = str(mode or "").strip()
        return value.capitalize() if value else ""

    @staticmethod
    def _speed_text(constraint: Any) -> str:
        if not isinstance(constraint, Mapping):
            return ""
        minimum = constraint.get("speed_min_kph")
        maximum = constraint.get("speed_max_kph")
        if minimum is None or maximum is None:
            return ""
        return f"车辆速度为 {minimum:g}–{maximum:g} km/h"

    def scenario(self, scenario: Any) -> tuple[str, str]:
        """Project only supplied scenario facts; unresolved facts stay in detail."""
        facts = getattr(scenario, "facts", {}) or {}
        base = str(getattr(scenario, "operating_scenario", "") or "").strip().rstrip("。；;")
        mode = self._display_mode(getattr(scenario, "operating_mode", "") or facts.get("operating_mode", ""))
        parts = [part for part in (base, f"AVP 处于 {mode} 状态" if mode else "", self._speed_text(facts.get("ego_speed_constraint"))) if part]
        operational = "，".join(parts) + "。" if parts else "运行场景未提供。"

        details: list[str] = []
        if facts.get("weather_conditions"):
            details.append(f"天气条件：{facts['weather_conditions']}")
        if facts.get("road_surface_conditions"):
            details.append(f"路面条件：{facts['road_surface_conditions']}")

        dimensions = facts.get("method_scenario_dimensions", {})
        if isinstance(dimensions, Mapping):
            def unresolved(name: str) -> bool:
                item = dimensions.get(name, {})
                return isinstance(item, Mapping) and bool(item.get("unresolved_reason"))

            if unresolved("ROAD"):
                details.append("道路条件未明确")
            if unresolved("TRAFFIC_PATTERN"):
                details.append("交通参与者信息未提供")
            if unresolved("OBJECT"):
                details.append("对象信息未提供")
            if unresolved("EGO_X_ROAD"):
                details.append("道路交互条件未提供")
        return operational, "；".join(details) + "。" if details else "未提供额外场景上下文。"

    def pending_value(self, evidence: Any) -> str:
        return "Not applicable" if _status(evidence) == "NOT_APPLICABLE" else self._PENDING_VALUE

    def potential_harm(self, value: Any, *, severity: Any) -> str:
        if str(value or "").strip():
            return str(value)
        if _status(severity) != "FINALIZED":
            return "Pending（上游风险评定未完成）"
        return self._PENDING_VALUE

    def severity_rationale(self, evidence: Any, trace: Mapping[str, Any]) -> str:
        if _status(evidence) == "FINALIZED":
            return "已按当前方法完成 S 评定。"
        reason = str(trace.get("pending_reason", "") or getattr(evidence, "review_reason", ""))
        if self._has_reason(reason, "MISSING_RELATIVE_SPEED"):
            return "缺少该危险事件的实际相对速度，S 暂不评定。"
        if self._has_reason(reason, "MISSING_ROAD_USER_TYPE"):
            return "缺少交通参与者类型，S 暂不评定。"
        if self._has_reason(reason, "MISSING_COLLISION_TYPE"):
            return "缺少碰撞类型信息，S 暂不评定。"
        return "缺少 S 评定所需项目事实，S 暂不评定。"

    def exposure_rationale(self, evidence: Any, trace: Mapping[str, Any]) -> str:
        if _status(evidence) == "FINALIZED":
            return "已按当前方法完成 E 评定。"
        reason = trace.get("missing_method_semantics", "") or getattr(evidence, "review_reason", "")
        if self._has_reason(reason, "EXPOSURE_DIMENSION_COVERAGE"):
            return "Exposure 场景维度覆盖规则未定义，E 暂不评定。"
        return "缺少 E 评定所需方法语义或项目事实，E 暂不评定。"

    def controllability_rationale(self, evidence: Any, trace: Mapping[str, Any]) -> str:
        if _status(evidence) == "FINALIZED":
            return "已按当前方法完成 C 评定。"
        reason = trace.get("decision_status", "") or getattr(evidence, "review_reason", "")
        if self._has_reason(reason, "METHOD_BRANCH_UNRESOLVED") or self._has_reason(reason, "UNKNOWN_BRANCH_POLICY_UNSPECIFIED"):
            return "UNKNOWN 分支策略未定义，C 暂不评定。"
        return "缺少 C 评定所需控制上下文或方法分支，C 暂不评定。"

    def asil_rationale(self, evidence: Any, trace: Mapping[str, Any]) -> str:
        if _status(evidence) == "FINALIZED":
            return "已按当前 ASIL 矩阵完成评定。"
        return "S/E/C 未全部确定，ASIL 暂不评定。"

    @staticmethod
    def ftti_rationale() -> str:
        return "当前运行未启用 FTTI 计算。"


__all__ = ["EngineeringReportTextMapper"]

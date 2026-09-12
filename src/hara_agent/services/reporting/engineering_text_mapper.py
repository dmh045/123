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

    @staticmethod
    def _dimensions(scenario: Any) -> Mapping[str, Any]:
        facts = getattr(scenario, "facts", {}) or {}
        value = facts.get("method_scenario_dimensions", {})
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _resolved_value(dimensions: Mapping[str, Any], name: str) -> str:
        binding = dimensions.get(name, {})
        if not isinstance(binding, Mapping):
            return ""
        if str(binding.get("resolution_status", "")).upper() != "RESOLVED":
            return ""
        value = str(binding.get("method_value", "") or "").strip()
        return value.split("|", 1)[-1].strip() if "|" in value else value

    @staticmethod
    def _dimension_detail(dimensions: Mapping[str, Any], name: str) -> str:
        if name not in dimensions:
            return ""
        binding = dimensions.get(name, {})
        if not isinstance(binding, Mapping):
            return ""
        if str(binding.get("resolution_status", "")).upper() == "RESOLVED":
            value = str(binding.get("method_value", "") or "").strip()
            if "|" in value:
                value = value.split("|", 1)[-1].strip()
            return f"{name}={value}" if value else f"{name}=已解析"
        labels = {
            "WHERE": "场所未解析",
            "ROAD": "道路条件未解析",
            "EGO_ACTION": "自车动作未解析",
            "EGO_X_ROAD": "自车与道路关系未解析",
            "TRAFFIC_PATTERN": "交通关系/交通模式未解析",
            "EGO_DYNAMICS": "自车动态未解析",
            "OBJECT": "对象/交通参与者未解析",
        }
        return f"{name}={labels.get(name, '未解析')}"

    def scenario(self, scenario: Any) -> tuple[str, str]:
        """Project supplied child facts without deriving new scenario semantics."""
        facts = getattr(scenario, "facts", {}) or {}
        dimensions = self._dimensions(scenario)
        where = self._resolved_value(dimensions, "WHERE")
        action = self._resolved_value(dimensions, "EGO_ACTION")
        dynamics = self._resolved_value(dimensions, "EGO_DYNAMICS")
        object_value = self._resolved_value(dimensions, "OBJECT")
        base = where or str(
            getattr(scenario, "operating_scenario", "") or ""
        ).strip().rstrip("。；;")
        mode = self._display_mode(getattr(scenario, "operating_mode", "") or facts.get("operating_mode", ""))
        parts = [
            part for part in (
                f"场所：{base}" if base else "",
                f"AVP 处于 {mode} 状态" if mode else "",
                f"自车动作：{action}" if action else "",
                f"自车动态：{dynamics}" if dynamics else "",
                f"对象：{object_value}" if object_value else "",
                self._speed_text(facts.get("ego_speed_constraint")),
            ) if part
        ]
        operational = "；".join(parts) + "。" if parts else "运行场景未提供。"

        details = [
            value for value in (
                self._dimension_detail(dimensions, name)
                for name in (
                    "WHERE", "ROAD", "EGO_ACTION", "EGO_X_ROAD",
                    "TRAFFIC_PATTERN", "EGO_DYNAMICS", "OBJECT",
                )
            ) if value
        ]
        if facts.get("weather_conditions"):
            details.append(f"天气条件：{facts['weather_conditions']}")
        if facts.get("road_surface_conditions") and not self._resolved_value(dimensions, "ROAD"):
            details.append(f"路面条件：{facts['road_surface_conditions']}")
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
            requested = str(trace.get("requested_domain", "") or "")
            actual = str(trace.get("domain", trace.get("actual_domain", "")) or "")
            bindings = trace.get("atom_bindings", [])
            atoms = []
            if isinstance(bindings, list):
                for item in bindings:
                    if not isinstance(item, Mapping) or not item.get("used"):
                        continue
                    atom_id = str(item.get("atom_id", "")).strip()
                    level = str(item.get("E_class", "")).strip()
                    if atom_id and level:
                        atoms.append(f"{atom_id}={level}")
            coupling = trace.get("dependency_coupling", {})
            coupling = coupling if isinstance(coupling, Mapping) else {}
            branch = str(coupling.get("policy_branch", "") or "")
            branch_text = {
                "all_e4": "有效 atom 均为 E4，按 FUSA v1 all-E4 规则",
                "e3_e4_mix": "有效 atom 包含 E3 与 E4，按 FUSA v1 混合高等级规则",
                "min_when_unequal": "有效 atom 等级不相同，按 FUSA v1 取较低等级",
                "same_independent_minus_one": "同等级 atom 为 independent，按 FUSA v1 降一级",
                "same_coupled_no_change": "同等级 atom 为 coupled，按 FUSA v1 不降级",
            }.get(branch, "按已记录的 FUSA v1 聚合规则")
            domain_text = (
                f"请求 {requested} 域，实际使用 {actual} 域"
                if requested and actual else "已记录 Exposure 域"
            )
            fallback = "；请求域无可用值，已回退到实际域" if trace.get("scenario_level_fallback") else ""
            atom_text = "、".join(atoms) if atoms else "无 atom（S0 短路）"
            final_value = str(trace.get("result", getattr(evidence, "value", "")) or "")
            return f"{domain_text}；{atom_text}；{branch_text}{fallback}，最终 {final_value}。"
        readiness = trace.get("input_readiness", {})
        readiness = readiness if isinstance(readiness, Mapping) else {}
        readiness_status = str(readiness.get("status", ""))
        relevant = readiness.get("unresolved_relevant_dimensions", [])
        relevant_text = "、".join(str(item) for item in relevant) if isinstance(relevant, list) else ""
        if readiness_status == "PENDING_RELEVANT_DIMENSION":
            return (
                f"当前 atom 集不足以确定 E；{relevant_text or '仍有'} Exposure 相关场景维度未解析，"
                "其允许的 source-defined atom 可改变当前结果，E 暂不评定。"
            )
        if readiness_status == "PENDING_ATOM_BINDING":
            return "已解析场景维度的 atom 绑定不完整或未纳入当前 atom 集，E 暂不评定。"
        if readiness_status == "PENDING_AMBIGUOUS_ATOM_SET":
            return "Exposure 相关场景维度存在多个未消解的 atom 候选，E 暂不评定。"
        if readiness_status == "SOURCE_CONFLICT":
            return "Exposure 的 MethodContract 来源或组件域存在冲突，E 暂不评定。"
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

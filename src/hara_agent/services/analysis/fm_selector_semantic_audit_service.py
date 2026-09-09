"""Offline, deterministic semantic audit for finalized FM selector values.

This module is intentionally a review projection.  It never reclassifies a
malfunction, changes a selector value, or calls a Provider.  The term checks
are deliberately conservative: they only record whether the selected
canonical taxonomy definition has textual support in the already persisted
malfunction and function records.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import inspect
from typing import Any

from hara_agent.contracts import MethodContract
from hara_agent.models import MalfunctionCandidate, ReviewStatus
from hara_agent.services.semantic.malfunction_agent import MalfunctionHazardAgent

from .scenario_method_service import ScenarioMethodService


_FAILURE_INDICATORS: dict[str, tuple[str, ...]] = {
    "loss": (
        "loss", "unavailable", "not available", "missing", "unable", "failed",
        "未显示", "未生成", "未进入", "未执行", "未拉起", "丢失", "无法", "失效",
    ),
    "unintended_activation": (
        "unintended", "unexpected", "unplanned", "should not", "非预期", "误触发",
        "错误触发", "异常激活", "不应", "未满足", "不满足", "额外弹出", "仍显示",
    ),
    "degraded": (
        "degraded", "reduced", "partial", "incomplete", "insufficient", "部分",
        "不完整", "不足", "降低", "仅", "缩短",
    ),
    "excessive": (
        "excessive", "too much", "over", "more than", "过度", "过多", "超出", "过量",
    ),
    "delayed": (
        "delayed", "late", "slow", "delay", "延迟", "迟", "超时", "未及时", "滞后",
    ),
    "stuck": (
        "stuck", "fixed", "cannot exit", "unable to transition", "remains", "卡死",
        "不更新", "无法退出", "无法切换", "保持", "停留", "卡在",
    ),
    "wrong_value": (
        "wrong", "opposite", "erroneous", "incorrect", "wrong path", "错误", "反向",
        "偏离", "不相关", "无关", "其他功能", "错误值", "错误方向",
    ),
    "oscillation": ("oscillation", "oscillate", "振荡", "抖动"),
    "intermittent": ("intermittent", "sporadic", "间歇"),
}

_COMPONENT_INDICATORS: dict[str, tuple[str, ...]] = {
    "hmi": ("hmi", "界面", "显示", "提示", "按键", "输入"),
    "actuator_longitudinal": ("制动", "刹车", "epb", "驻车", "挂p档", "档位"),
    "actuator_lateral": ("转向", "方向盘"),
    "actuator_propulsion": ("驱动", "动力", "加速", "电机"),
    "sensor_camera": ("camera", "摄像"),
    "sensor_ultrasonic": ("ultrasonic", "超声"),
    "sensor_radar": ("radar", "雷达"),
    "sensor_lidar": ("lidar", "激光雷达"),
    "sensor_imu": ("imu", "惯导"),
    "localization": ("定位", "自构图", "地图"),
    "communication": ("can", "ethernet", "通信", "报文"),
    "power": ("供电", "电源"),
    "lighting": ("灯", "照明"),
    "wiper": ("雨刮",),
}
_COMPUTING_INDICATORS = (
    "logic", "state machine", "software", "system", "controller", "规划", "控制逻辑",
    "状态机", "算法", "调度", "计算", "系统",
)
_EARLY_INDICATORS = ("early", "提前", "过早")


def _matches(text: str, indicators: tuple[str, ...]) -> list[str]:
    folded = text.casefold()
    return [term for term in indicators if term.casefold() in folded]


class FMSelectorSemanticAuditService:
    """Produce a read-only audit of one persisted review run."""

    def __init__(self, method: MethodContract):
        self.method = method
        self.scenario_method = ScenarioMethodService(method)
        taxonomy = method.scenario_model.scenario_method.failure_mode_selector_taxonomy
        self.failure_definitions = {
            item.canonical_id: item.description
            for item in (taxonomy.failure_types if taxonomy is not None else ())
        }
        self.component_definitions = {
            item.canonical_id: item.description
            for item in (taxonomy.component_categories if taxonomy is not None else ())
        }

    @staticmethod
    def _candidate(raw: dict[str, Any]) -> MalfunctionCandidate | None:
        try:
            return MalfunctionCandidate(
                malfunction_id=str(raw["malfunction_id"]),
                function_id=str(raw["function_id"]),
                guideword=str(raw["guideword"]),
                guideword_id=str(raw.get("guideword_id", raw["guideword"])),
                description=str(raw["description"]),
                functional_effect=str(raw["functional_effect"]),
                vehicle_level_hazard=str(raw["vehicle_level_hazard"]),
                causal_chain=[str(item) for item in raw.get("causal_chain", [])],
                status=ReviewStatus(str(raw.get("status", ReviewStatus.PENDING.value))),
                confidence=float(raw.get("confidence", 0.0)),
                component_category=str(raw.get("component_category", "")),
                failure_type=str(raw.get("failure_type", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _failure_audit(self, raw: dict[str, Any]) -> dict[str, Any]:
        failure_type = str(raw.get("failure_type", ""))
        text = "\n".join((
            str(raw.get("description", "")), str(raw.get("functional_effect", "")),
        ))
        matched = _matches(text, _FAILURE_INDICATORS.get(failure_type, ()))
        guideword_id = str(raw.get("guideword_id", ""))
        early_terms = _matches(text, _EARLY_INDICATORS)
        result: dict[str, Any] = {
            "status": "SUPPORTED" if matched else "UNDETERMINED",
            "taxonomy_definition": self.failure_definitions.get(failure_type, ""),
            "matched_terms": matched,
            "evidence_fields": [
                field for field in ("malfunction_description", "functional_effect")
                if str(raw.get("description" if field == "malfunction_description" else "functional_effect", "")).strip()
            ],
        }
        if guideword_id == "GW-EARLY" or early_terms:
            unplanned_terms = _matches(text, _FAILURE_INDICATORS["unintended_activation"])
            if unplanned_terms:
                result["early_interpretation"] = "UNPLANNED_ACTIVATION"
                result["early_terms"] = early_terms
            elif early_terms:
                result.update({
                    "status": "SEMANTIC_MISMATCH_CANDIDATE",
                    "early_interpretation": "CORRECT_FUNCTION_TOO_EARLY",
                    "early_terms": early_terms,
                    "taxonomy_gap": "TAXONOMY_EXPRESSIVENESS_GAP",
                    "observation": (
                        "The active taxonomy defines delayed but no early-timing "
                        "failure type; this is not evidence of a Provider contract failure."
                    ),
                })
        return result

    def _component_audit(self, raw: dict[str, Any], function: dict[str, Any]) -> dict[str, Any]:
        component = str(raw.get("component_category", ""))
        text = "\n".join((
            str(function.get("name", "")), str(function.get("output", "")),
            str(function.get("description", "")), str(raw.get("description", "")),
            str(raw.get("functional_effect", "")),
        ))
        direct_terms = _matches(text, _COMPONENT_INDICATORS.get(component, ()))
        if component == "computing":
            computing_terms = _matches(text, _COMPUTING_INDICATORS)
            other_direct = {
                category: _matches(text, terms)
                for category, terms in _COMPONENT_INDICATORS.items()
                if category != "computing" and _matches(text, terms)
            }
            if computing_terms:
                status = "SUPPORTED"
            elif other_direct:
                status = "OVER_GENERIC_CANDIDATE"
            else:
                status = "UNDETERMINED"
            return {
                "status": status,
                "taxonomy_definition": self.component_definitions.get(component, ""),
                "matched_terms": computing_terms,
                "alternative_direct_evidence": other_direct,
            }
        return {
            "status": "SUPPORTED" if direct_terms else "UNDETERMINED",
            "taxonomy_definition": self.component_definitions.get(component, ""),
            "matched_terms": direct_terms,
        }

    @staticmethod
    def _prompt_inspection() -> dict[str, Any]:
        source = inspect.getsource(MalfunctionHazardAgent._request)
        direct_patterns = ("No/Loss => loss", "Less => degraded", "More => excessive")
        hits = [pattern for pattern in direct_patterns if pattern in source]
        prohibition = "do not map it mechanically from the guideword" in source
        return {
            "inspection_scope": "MalfunctionHazardAgent._request static source",
            "explicit_guideword_to_failure_type_mapping": bool(hits),
            "direct_mapping_hits": hits,
            "mechanical_mapping_prohibition_present": prohibition,
            "classification": "IMPLEMENTED_HARDCODE" if hits else "MODEL_LEARNED_CORRELATION",
        }

    def generate(self, records: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        functions = {
            str(item.get("function_id", "")): item
            for item in records.get("function", []) if isinstance(item, dict)
        }
        guidewords = {
            (str(item.get("function_id", "")), str(item.get("guideword_id", ""))): item
            for item in records.get("guideword_assessment", []) if isinstance(item, dict)
        }
        rows: list[dict[str, Any]] = []
        matrix: dict[str, Counter[str]] = defaultdict(Counter)
        component_matrix: dict[str, Counter[str]] = defaultdict(Counter)
        qualification_counts: Counter[str] = Counter()
        failure_status_counts: Counter[str] = Counter()
        component_status_counts: Counter[str] = Counter()
        early_rows: list[dict[str, Any]] = []
        other_rows: list[dict[str, Any]] = []

        for raw in records.get("malfunction", []):
            if not isinstance(raw, dict):
                continue
            candidate = self._candidate(raw)
            if candidate is None:
                continue
            function = functions.get(candidate.function_id, {})
            guideword = guidewords.get((candidate.function_id, candidate.guideword_id), {})
            failure_audit = self._failure_audit(raw)
            component_audit = self._component_audit(raw, function)
            result = self.scenario_method.match_fm_template(candidate)
            qualification = {
                "STRONG_MATCH": "STRONG", "WEAK_MATCH": "WEAK",
            }.get(result.status, result.status)
            row = {
                "function_id": candidate.function_id,
                "function_name": str(function.get("name", "")),
                "function_output": str(function.get("output", "")),
                "guideword_id": candidate.guideword_id,
                "guideword": candidate.guideword,
                "guideword_disposition": str(guideword.get("disposition", "")),
                "malfunction_id": candidate.malfunction_id,
                "malfunction_description": candidate.description,
                "functional_effect": candidate.functional_effect,
                "component_category": candidate.component_category,
                "failure_type": candidate.failure_type,
                "failure_semantic_audit": failure_audit,
                "component_semantic_audit": component_audit,
                "template_qualification": qualification,
                "template_match_status": result.status,
                "template_match": {
                    "matching_template_ids": list(result.matching_template_ids),
                    "matched_by": list(result.matched_by),
                    "matched_terms": list(result.matched_terms),
                    "reason": result.reason,
                    "selector_resolution": (
                        result.selector_resolution.to_dict()
                        if result.selector_resolution is not None else {}
                    ),
                    "selector_adapter_resolution": [
                        item.to_dict() for item in result.template_selector_resolution
                    ],
                },
            }
            rows.append(row)
            matrix[candidate.guideword_id][candidate.failure_type] += 1
            component_matrix[candidate.function_id][candidate.component_category] += 1
            qualification_counts[qualification] += 1
            failure_status_counts[str(failure_audit["status"])] += 1
            component_status_counts[str(component_audit["status"])] += 1
            if candidate.guideword_id == "GW-EARLY":
                early_rows.append(row)
            if candidate.guideword_id == "GW-OTHER":
                other_rows.append(row)

        guideword_rows = []
        for guideword_id in sorted(matrix):
            counts = matrix[guideword_id]
            total = sum(counts.values())
            dominant_type, dominant_count = max(counts.items(), key=lambda item: (item[1], item[0]))
            guideword_rows.append({
                "guideword_id": guideword_id,
                "guideword": next(
                    (row["guideword"] for row in rows if row["guideword_id"] == guideword_id), "",
                ),
                "total": total,
                "failure_type_counts": dict(sorted(counts.items())),
                "dominant_failure_type": dominant_type,
                "dominant_ratio": dominant_count / total if total else 0.0,
                "unique_failure_types": sorted(counts),
                "risk_flags": (
                    ["GUIDEWORD_SELECTOR_COLLAPSE_RISK"]
                    if total and dominant_count == total else []
                ),
            })

        early_gap_count = sum(
            row["failure_semantic_audit"].get("taxonomy_gap") == "TAXONOMY_EXPRESSIVENESS_GAP"
            for row in early_rows
        )
        other_distribution = Counter(row["failure_type"] for row in other_rows)
        return {
            "artifact_version": "fm-selector-semantic-audit-v1",
            "runtime_behavior_changed": False,
            "llm_calls_added": 0,
            "scope": "OFFLINE_REVIEW_ONLY",
            "contract_status": {
                "malfunction_count": len(rows),
                "canonical_component_category_count": sum(bool(row["component_category"]) for row in rows),
                "canonical_failure_type_count": sum(bool(row["failure_type"]) for row in rows),
                "selector_resolution_status": "PERSISTED_PROVIDER_OUTPUT_REAUDITED_OFFLINE",
            },
            "guideword_failure_type_matrix": {
                "rows": guideword_rows,
                "failure_type_totals": dict(sorted(Counter(
                    row["failure_type"] for row in rows
                ).items())),
            },
            "failure_semantic_audit_summary": dict(sorted(failure_status_counts.items())),
            "early_audit": {
                "count": len(early_rows),
                "unplanned_activation_count": sum(
                    row["failure_semantic_audit"].get("early_interpretation") == "UNPLANNED_ACTIVATION"
                    for row in early_rows
                ),
                "correct_function_too_early_count": sum(
                    row["failure_semantic_audit"].get("early_interpretation") == "CORRECT_FUNCTION_TOO_EARLY"
                    for row in early_rows
                ),
                "taxonomy_expressiveness_gap_count": early_gap_count,
                "taxonomy_expressiveness_gap": early_gap_count > 0,
                "rows": [row["malfunction_id"] for row in early_rows],
            },
            "other_than_audit": {
                "count": len(other_rows),
                "failure_type_counts": dict(sorted(other_distribution.items())),
                "semantic_distinction_observed": len(other_distribution) > 1,
                "rows": [row["malfunction_id"] for row in other_rows],
            },
            "function_component_category_matrix": {
                "rows": [
                    {
                        "function_id": function_id,
                        "function_name": str(functions.get(function_id, {}).get("name", "")),
                        "component_category_counts": dict(sorted(counts.items())),
                    }
                    for function_id, counts in sorted(component_matrix.items())
                ],
                "component_category_totals": dict(sorted(Counter(
                    row["component_category"] for row in rows
                ).items())),
                "semantic_audit_summary": dict(sorted(component_status_counts.items())),
            },
            "prompt_inspection": self._prompt_inspection(),
            "template_qualification": {
                "counts": {
                    label: qualification_counts[label]
                    for label in ("STRONG", "WEAK", "AMBIGUOUS", "NO_MATCH")
                },
                "historical_pre_c9f_67_malfunctions": {
                    "STRONG": 0, "WEAK": 9, "AMBIGUOUS": 0, "NO_MATCH": 58,
                },
                "comparison_basis": (
                    "Historical values are the supplied pre-C9F 67-MF baseline; "
                    "the current count is a fresh offline rerun using C9D qualification "
                    "and the C9F selector adapter."
                ),
            },
            "malfunctions": rows,
        }


__all__ = ["FMSelectorSemanticAuditService"]

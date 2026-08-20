#!/usr/bin/env python3
"""
Scoring Checker - 步骤9-17输出内容的完整性检查器
核心检查规则：除非引导词被标记为不适用或S/E/C值为S0/E0/C0，否则导出内容不应为空
"""

import json
import os
import re
from typing import Dict, List, Any, Optional
import logging
import argparse
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from hara_agent.services.analysis import (
    FTTI_FINALIZED,
    FTTI_NEEDS_REVIEW,
    FTTI_NOT_REQUIRED,
    TemplateASILService,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ScoringChecker:
    """Scoring & Safety Goal 输出检查器"""

    def __init__(self, scoring_output_path: str, template_path: Optional[str] = None,
                 allow_draft: bool = False):
        """
        初始化检查器

        Args:
            scoring_output_path: scoring_sg_output.json 文件路径
        """
        self.scoring_output_path = scoring_output_path
        self.template_path = template_path
        self.data = {}
        self.errors = []
        self.warnings = []
        self.infos = []
        self.allow_draft = allow_draft

    def load(self) -> bool:
        """加载评分输出数据"""
        try:
            if not os.path.exists(self.scoring_output_path):
                logger.error(f"文件不存在: {self.scoring_output_path}")
                return False

            with open(self.scoring_output_path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)

            logger.info(f"已加载: {self.scoring_output_path}")
            return True

        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            return False
        except Exception as e:
            logger.error(f"加载失败: {e}")
            return False

    def check_all(self) -> Dict[str, Any]:
        """
        执行所有检查

        Returns:
            检查结果字典
        """
        self.errors = []
        self.warnings = []
        self.infos = []

        if not self.data:
            return {
                "status": "fail",
                "errors": [{"type": "data_loading", "message": "未加载到数据"}]
            }

        self._check_severity_results()
        self._check_exposure_results()
        self._check_controllability_results()
        self._check_asil_results()
        self._check_ftti_results()
        self._check_safety_goals()
        self._check_safe_states()
        self._check_scenario_traceability()
        self._check_domain_profile()
        self._check_legacy_scoring_compatibility()

        for error in self.errors:
            error.setdefault("severity", "ERROR")
        for warning in self.warnings:
            warning.setdefault("severity", "WARNING")
        self.infos.append({
            "type": "quality_gate_completed", "severity": "INFO",
            "message": "Scoring/SG/FTTI质量门检查已完成。",
        })
        status = "fail" if self.errors else ("warning" if self.warnings else "pass")

        result = {
            "status": status,
            "execution_success": True,
            "analysis_valid": not self.errors and not self.warnings,
            "total_errors": len(self.errors),
            "total_warnings": len(self.warnings),
            "total_info": len(self.infos),
            "errors": self.errors,
            "warnings": self.warnings,
            "info": self.infos,
            "summary": self._generate_summary()
        }

        logger.info(f"检查完成: status={status}, errors={len(self.errors)}")
        return result

    def _ftti_issue(self, issue: Dict[str, Any], reviewable: bool = False):
        if reviewable and self.allow_draft:
            self.warnings.append(issue)
        else:
            self.errors.append(issue)

    def _check_domain_profile(self):
        """Fail closed when domain-specific rules are missing or unapproved."""
        metadata = self.data.get("metadata", {})
        subsystem = str(metadata.get("subsystem", "")).strip().lower()
        if subsystem != "avp":
            return

        profile = metadata.get("domain_profile")
        if not isinstance(profile, dict):
            self.errors.append({
                "type": "missing_domain_profile",
                "field": "metadata.domain_profile",
                "message": "AVP结果缺少Domain Profile版本与审批状态。",
            })
            return

        approval_status = str(profile.get("approval_status", "")).strip().lower()
        if approval_status != "approved":
            issue = {
                "type": "domain_profile_not_approved",
                "field": "metadata.domain_profile.approval_status",
                "profile": profile.get("name", ""),
                "profile_version": profile.get("version", ""),
                "approval_status": profile.get("approval_status", ""),
                "message": "Domain Profile尚未获工程批准，只允许生成Draft报告。",
            }
            if self.allow_draft:
                self.warnings.append(issue)
            else:
                self.errors.append(issue)

    def _check_legacy_scoring_compatibility(self):
        """Block silent publication of keyword-based compatibility scoring."""
        affected = []
        for section in ("severity_results", "exposure_results", "controllability_results"):
            for item in self._iter_leaf_items(self.data.get(section, {})):
                if item.get("_source") == "legacy_text_scoring_compatibility":
                    affected.append({
                        "section": section,
                        "scenario_id": item.get("scenario_id", ""),
                        "rule_id": item.get("engineering_rule_id", ""),
                    })
        if not affected:
            return
        issue = {
            "type": "legacy_text_scoring_used",
            "count": len(affected),
            "affected": affected[:50],
            "message": "评分使用旧关键词兼容路径；须补充结构化事实和获批Domain Policy后才能正式发布。",
        }
        if self.allow_draft:
            self.warnings.append(issue)
        else:
            self.errors.append(issue)

    def _check_ftti_results(self):
        """Require an auditable FTTI state for every ASIL result and SG."""
        ftti_results = self.data.get("ftti_results", {})
        if not ftti_results:
            self.errors.append({
                "type": "missing_section", "field": "ftti_results",
                "message": "缺少ftti_results；不能将TTC或空值作为FTTI替代。",
            })
            return

        ftti_by_key = {
            (item.get("scenario_id", ""), item.get("ASIL", "")): item
            for item in self._iter_leaf_items(ftti_results)
            if item.get("scenario_id")
        }
        for item in self._iter_leaf_items(self.data.get("asil_results", {})):
            scenario_id = item.get("scenario_id", "")
            asil = item.get("ASIL", "")
            ftti = ftti_by_key.get((scenario_id, asil))
            if not ftti:
                self.errors.append({
                    "type": "ftti_trace_missing", "field": "ftti_results",
                    "scenario_id": scenario_id,
                    "message": "ASIL记录缺少同scenario_id的FTTI结果。",
                })
                continue
            status = str(ftti.get("ftti_status", ""))
            value = ftti.get("ftti_value_s")
            if asil == "QM":
                if status != FTTI_NOT_REQUIRED or ftti.get("ftti_requirement") != "NA":
                    self.errors.append({
                        "type": "qm_ftti_state_invalid", "field": "ftti_results",
                        "scenario_id": scenario_id,
                    })
                continue
            if status not in {FTTI_FINALIZED, FTTI_NEEDS_REVIEW}:
                self.errors.append({
                    "type": "ftti_status_invalid", "field": "ftti_results",
                    "scenario_id": scenario_id, "actual": status,
                })
                continue
            if status == FTTI_FINALIZED and (not isinstance(value, (int, float)) or value <= 0):
                self.errors.append({
                    "type": "finalized_ftti_value_invalid", "field": "ftti_results",
                    "scenario_id": scenario_id, "actual": value,
                })
            if str(ftti.get("formula_id", "")).lower().startswith("ttc"):
                self.errors.append({
                    "type": "ttc_used_as_ftti", "field": "ftti_results",
                    "scenario_id": scenario_id,
                    "message": "TTC只能作为筛查输入，不能直接作为FTTI。",
                })
            if status == FTTI_NEEDS_REVIEW:
                self._ftti_issue({
                    "type": "ftti_needs_review", "field": "ftti_results",
                    "scenario_id": scenario_id,
                    "candidate_value_s": value,
                    "message": f"场景{scenario_id}的FTTI公式或参数尚未获项目批准。",
                }, reviewable=True)

        catalog = self.data.get("safety_goal_catalog", {})
        for sg_id, entry in catalog.items():
            status = str(entry.get("ftti_status", ""))
            value = entry.get("ftti_value_s")
            association_values = [
                association.get("ftti", {}).get("ftti_value_s")
                for association in entry.get("associations", [])
                if association.get("ftti", {}).get("ftti_value_s") is not None
            ]
            expected = min(association_values) if association_values else None
            if status not in {FTTI_FINALIZED, FTTI_NEEDS_REVIEW}:
                self.errors.append({
                    "type": "sg_ftti_status_missing", "field": "safety_goal_catalog",
                    "sg_id": sg_id,
                })
            if expected is not None and value != expected:
                self.errors.append({
                    "type": "sg_ftti_not_most_stringent", "field": "safety_goal_catalog",
                    "sg_id": sg_id, "expected": expected, "actual": value,
                })
            if status == FTTI_NEEDS_REVIEW:
                self._ftti_issue({
                    "type": "sg_ftti_needs_review", "field": "safety_goal_catalog",
                    "sg_id": sg_id, "candidate_value_s": value,
                    "message": f"{sg_id}的最严FTTI仍需人工批准。",
                }, reviewable=True)

    @staticmethod
    def _iter_leaf_items(value):
        if isinstance(value, list):
            for item in value:
                yield from ScoringChecker._iter_leaf_items(item)
        elif isinstance(value, dict):
            if "scenario_id" in value:
                yield value
            else:
                for item in value.values():
                    yield from ScoringChecker._iter_leaf_items(item)

    def _check_scenario_traceability(self):
        """Every Phase 3 result must reference the canonical Phase 2 scenario."""
        catalog = self.data.get("scenario_catalog", {})
        if not catalog:
            self.errors.append({
                "type": "scenario_catalog_missing",
                "field": "scenario_catalog",
                "message": "评分输出缺少唯一场景事实源",
            })
            return

        invalid = []
        for section in (
            "severity_results", "exposure_results", "controllability_results",
            "asil_results", "ftti_results", "safety_goals", "safe_states",
        ):
            for item in self._iter_leaf_items(self.data.get(section, {})):
                scenario_id = item.get("scenario_id", "")
                if not scenario_id or scenario_id not in catalog:
                    invalid.append({"section": section, "scenario_id": scenario_id})
        if invalid:
            self.errors.append({
                "type": "scenario_traceability_invalid",
                "count": len(invalid),
                "items": invalid[:10],
            })

    def _check_severity_results(self):
        """检查 severity_results 中非S0的项是否有值"""
        severity_results = self.data.get("severity_results", {})
        if not severity_results:
            self.errors.append({
                "type": "missing_section",
                "field": "severity_results",
                "message": "缺少 severity_results 字段"
            })
            return

        for func, malf_results in severity_results.items():
            if not isinstance(malf_results, dict):
                continue
            for malf, items in malf_results.items():
                if not isinstance(items, list):
                    continue
                for i, item in enumerate(items):
                    score = item.get("severity_score", "")
                    reasoning = item.get("reasoning", "")

                    if score == "S0":
                        continue

                    if not reasoning or reasoning.strip() == "" or reasoning.strip().upper() == "N/A":
                        self.errors.append({
                            "type": "empty_content",
                            "field": "severity_results",
                            "function_id": func,
                            "malfunction": malf,
                            "index": i,
                            "severity_score": score,
                            "message": f"功能'{func}'的Malfunction'{malf}'严重度为{score}但合理性分析为空"
                        })

    def _check_exposure_results(self):
        """检查 exposure_results 中非E0的项是否有值"""
        exposure_results = self.data.get("exposure_results", {})
        if not exposure_results:
            self.errors.append({
                "type": "missing_section",
                "field": "exposure_results",
                "message": "缺少 exposure_results 字段"
            })
            return

        for func, malf_results in exposure_results.items():
            if not isinstance(malf_results, dict):
                continue
            for malf, items in malf_results.items():
                if not isinstance(items, list):
                    continue
                for i, item in enumerate(items):
                    score = item.get("exposure_score", "")
                    reasoning = item.get("reasoning", "")
                    method = str(item.get("exposure_method", "")).upper()

                    if score and method not in {"T", "F"}:
                        self.errors.append({
                            "type": "invalid_exposure_method",
                            "field": "exposure_results",
                            "function_id": func,
                            "malfunction": malf,
                            "index": i,
                            "exposure_score": score,
                            "exposure_method": method,
                            "message": (
                                f"功能'{func}'的Malfunction'{malf}'缺少有效Exposure方法；"
                                "T表示平均运行时间占比，F表示场景发生频率"
                            ),
                        })

                    if score == "E0":
                        continue

                    if not reasoning or reasoning.strip() == "" or reasoning.strip().upper() == "N/A":
                        self.errors.append({
                            "type": "empty_content",
                            "field": "exposure_results",
                            "function_id": func,
                            "malfunction": malf,
                            "index": i,
                            "exposure_score": score,
                            "message": f"功能'{func}'的Malfunction'{malf}'暴露度为{score}但合理性分析为空"
                        })

    def _check_controllability_results(self):
        """检查 controllability_results 中非C0的项是否有值"""
        controllability_results = self.data.get("controllability_results", {})
        if not controllability_results:
            self.errors.append({
                "type": "missing_section",
                "field": "controllability_results",
                "message": "缺少 controllability_results 字段"
            })
            return

        for func, malf_results in controllability_results.items():
            if not isinstance(malf_results, dict):
                continue
            for malf, items in malf_results.items():
                if not isinstance(items, list):
                    continue
                for i, item in enumerate(items):
                    score = item.get("controllability_score", "")
                    reasoning = item.get("reasoning", "")

                    if score == "C0":
                        continue

                    if not reasoning or reasoning.strip() == "" or reasoning.strip().upper() == "N/A":
                        self.errors.append({
                            "type": "empty_content",
                            "field": "controllability_results",
                            "function_id": func,
                            "malfunction": malf,
                            "index": i,
                            "controllability_score": score,
                            "message": f"功能'{func}'的Malfunction'{malf}'可控度为{score}但合理性分析为空"
                        })

    def _check_asil_results(self):
        """检查 asil_results 中非QM的项是否有完整评级"""
        asil_results = self.data.get("asil_results", {})
        if not asil_results:
            self.errors.append({
                "type": "missing_section",
                "field": "asil_results",
                "message": "缺少 asil_results 字段"
            })
            return

        asil_matrix = None
        if self.template_path:
            try:
                asil_matrix = TemplateASILService(self.template_path)
            except (FileNotFoundError, ValueError) as exc:
                self.errors.append({
                    "type": "asil_matrix_unavailable",
                    "field": "asil_results",
                    "message": f"无法使用模板ASIL_Table复核评级: {exc}",
                })

        for func, malf_results in asil_results.items():
            if not isinstance(malf_results, dict):
                continue
            for malf, nested_items in malf_results.items():
                for i, item in enumerate(self._iter_leaf_items(nested_items)):
                    asil = item.get("ASIL", "")
                    severity = item.get("severity", "")
                    exposure = item.get("exposure", "")
                    controllability = item.get("controllability", "")

                    if asil not in {"QM", "A", "B", "C", "D"}:
                        self.errors.append({
                            "type": "missing_asil",
                            "field": "asil_results",
                            "function_id": func,
                            "malfunction": malf,
                            "index": i,
                            "message": f"功能'{func}'的Malfunction'{malf}'缺少ASIL评级"
                        })

                    missing_components = []
                    if not severity or severity.strip() == "":
                        missing_components.append("S")
                    if not exposure or exposure.strip() == "":
                        missing_components.append("E")
                    if not controllability or controllability.strip() == "":
                        missing_components.append("C")

                    if missing_components:
                        self.errors.append({
                            "type": "incomplete_asil_components",
                            "field": "asil_results",
                            "function_id": func,
                            "malfunction": malf,
                            "index": i,
                            "missing": missing_components,
                            "message": f"功能'{func}'的Malfunction'{malf}'ASIL={asil}缺少分量: {', '.join(missing_components)}"
                        })
                        continue

                    if asil_matrix is not None:
                        try:
                            expected_asil = asil_matrix.determine(
                                severity, exposure, controllability
                            )
                        except ValueError as exc:
                            self.errors.append({
                                "type": "invalid_sec_for_asil_matrix",
                                "field": "asil_results",
                                "function_id": func,
                                "malfunction": malf,
                                "index": i,
                                "message": str(exc),
                            })
                            continue
                        if asil != expected_asil:
                            self.errors.append({
                                "type": "asil_matrix_mismatch",
                                "field": "asil_results",
                                "function_id": func,
                                "malfunction": malf,
                                "index": i,
                                "severity": severity,
                                "exposure": exposure,
                                "controllability": controllability,
                                "expected": expected_asil,
                                "actual": asil,
                                "message": (
                                    f"ASIL评级与模板ASIL_Table不一致: "
                                    f"{severity}/{exposure}/{controllability} "
                                    f"应为{expected_asil}，实际为{asil}"
                                ),
                            })

    def _check_safety_goals(self):
        """检查事件级引用与顶层 Safety Goal 目录的一致性。"""
        safety_goals = self.data.get("safety_goals", {})
        if not safety_goals:
            self.errors.append({
                "type": "missing_section",
                "field": "safety_goals",
                "message": "缺少 safety_goals 字段"
            })
            return

        catalog = self.data.get("safety_goal_catalog", {})
        subsystem = str(self.data.get("metadata", {}).get("subsystem", "")).lower()
        if subsystem == "avp" and not catalog:
            self.errors.append({
                "type": "safety_goal_catalog_missing",
                "field": "safety_goal_catalog",
                "message": "AVP评分输出缺少顶层Safety Goal语义目录",
            })

        referenced_ids = set()
        for func, malf_goals in safety_goals.items():
            for i, item in enumerate(self._iter_leaf_items(malf_goals)):
                asil = item.get("ASIL", "")
                sg = item.get("safety_goal", "")
                sg_id = item.get("sg_id", "")
                if asil == "QM":
                    if sg not in ("NA", "N/A", "") or sg_id:
                        self.errors.append({
                            "type": "qm_has_safety_goal",
                            "field": "safety_goals",
                            "function_id": func,
                            "index": i,
                        })
                    continue
                if not sg or sg.strip().upper() in ("NA", "N/A"):
                    self.errors.append({
                        "type": "empty_safety_goal", "field": "safety_goals",
                        "function_id": func, "index": i, "ASIL": asil,
                    })
                if catalog:
                    referenced_ids.add(sg_id)
                    entry = catalog.get(sg_id)
                    if not sg_id or not entry:
                        self.errors.append({
                            "type": "safety_goal_reference_invalid", "field": "safety_goals",
                            "function_id": func, "index": i, "sg_id": sg_id,
                        })
                    elif sg != entry.get("safety_goal"):
                        self.errors.append({
                            "type": "safety_goal_text_mismatch", "field": "safety_goals",
                            "function_id": func, "index": i, "sg_id": sg_id,
                        })

        if catalog:
            asil_rank = {"QM": 0, "A": 1, "B": 2, "C": 3, "D": 4}
            texts = []
            for key, entry in catalog.items():
                if key != entry.get("sg_id"):
                    self.errors.append({"type": "safety_goal_id_mismatch", "sg_id": key})
                text = str(entry.get("safety_goal", "")).strip()
                texts.append(text)
                associations = entry.get("associations", [])
                expected_max = max(
                    (str(item.get("asil", "QM")) for item in associations),
                    key=lambda value: asil_rank.get(value, 0), default="QM",
                )
                if entry.get("max_asil") != expected_max:
                    self.errors.append({
                        "type": "safety_goal_max_asil_invalid", "sg_id": key,
                        "expected": expected_max, "actual": entry.get("max_asil"),
                    })
            if len(texts) != len(set(texts)):
                self.errors.append({"type": "duplicate_safety_goal_text", "field": "safety_goal_catalog"})
            unused = set(catalog) - referenced_ids
            if unused:
                self.errors.append({
                    "type": "unreferenced_safety_goal", "field": "safety_goal_catalog",
                    "sg_ids": sorted(unused),
                })

    def _check_safe_states(self):
        """检查 safe_states 中项是否有安全状态描述"""
        safe_states = self.data.get("safe_states", {})
        if not safe_states:
            self.errors.append({
                "type": "missing_section",
                "field": "safe_states",
                "message": "缺少 safe_states 字段"
            })
            return

        catalog = self.data.get("safety_goal_catalog", {})
        for func, malf_states in safe_states.items():
            for i, item in enumerate(self._iter_leaf_items(malf_states)):
                safety_goal = item.get("safety_goal", "")
                ss = item.get("safety_state", "")
                sg_id = item.get("sg_id", "")
                if safety_goal == "NA":
                    continue
                if not ss or ss.strip().upper() in ("", "N/A", "NA"):
                    self.errors.append({
                        "type": "empty_safe_state", "field": "safe_states",
                        "function_id": func, "index": i, "safety_goal": safety_goal,
                    })
                if catalog:
                    entry = catalog.get(sg_id)
                    if not entry or ss != entry.get("safety_state"):
                        self.errors.append({
                            "type": "safe_state_catalog_mismatch", "field": "safe_states",
                            "function_id": func, "index": i, "sg_id": sg_id,
                        })

    def _generate_summary(self) -> Dict[str, Any]:
        """生成检查摘要"""
        error_by_type = {}
        error_by_field = {}

        for err in self.errors:
            t = err.get("type", "unknown")
            error_by_type[t] = error_by_type.get(t, 0) + 1

            f = err.get("field", "unknown")
            error_by_field[f] = error_by_field.get(f, 0) + 1

        warning_by_type = {}
        for warning in self.warnings:
            t = warning.get("type", "unknown")
            warning_by_type[t] = warning_by_type.get(t, 0) + 1

        return {
            "error_by_type": error_by_type,
            "error_by_field": error_by_field,
            "warning_by_type": warning_by_type,
        }


def main():
    parser = argparse.ArgumentParser(description="Scoring Checker - 步骤9-17输出完整性检查")
    parser.add_argument("--input", "-i", required=True, help="scoring_sg_output.json 路径")
    parser.add_argument("--output", "-o", help="检查结果输出路径 (可选)")
    parser.add_argument("--template", "-t", help="包含ASIL_Table的HARA模板路径")
    parser.add_argument("--allow-draft", action="store_true", help="将待人工评审的FTTI降级为WARNING")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细错误信息")
    args = parser.parse_args()

    checker = ScoringChecker(args.input, args.template, allow_draft=args.allow_draft)

    if not checker.load():
        print(f"加载失败: {args.input}")
        return 1

    result = checker.check_all()

    print(f"\n检查结果: {result['status'].upper()}")
    print(f"错误总数: {result['total_errors']}")
    print(f"警告总数: {result['total_warnings']}")

    if result['summary']:
        print(f"\n按字段统计:")
        for field, count in result['summary'].get('error_by_field', {}).items():
            print(f"  {field}: {count}")

        print(f"\n按类型统计:")
        for etype, count in result['summary'].get('error_by_type', {}).items():
            print(f"  {etype}: {count}")

    if args.verbose and result['errors']:
        print(f"\n详细错误列表:")
        for i, err in enumerate(result['errors'], 1):
            print(f"  {i}. [{err.get('field', '')}] {err.get('message', '')}")

    if args.output:
        try:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"\n检查结果已保存到: {args.output}")
        except Exception as e:
            print(f"\n保存结果失败: {e}")

    return 0 if result['status'] in ('pass', 'warning') else 1


if __name__ == "__main__":
    exit(main())

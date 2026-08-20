#!/usr/bin/env python3
"""Function extraction quality gate."""

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List


class FunctionChecker:
    """Reject headings, fragments, conditions and non-functional text."""

    FRAGMENT_PREFIXES = ("，", "。", "；", "后，", "如下", "其中", "具体条件", "已识别的")
    NON_FUNCTION_KEYWORDS = (
        "潜在后果", "质量要求", "性能要求", "可用性要求", "标定值",
        "根据具体车型", "如果适用", "如有",
    )
    CONDITION_PREFIXES = ("当", "如果", "若", "用户点击", "车辆进入")

    def check(self, functions: List[Dict[str, Any]]) -> Dict[str, Any]:
        valid: List[Dict[str, Any]] = []
        invalid: List[Dict[str, Any]] = []
        seen = set()

        for item in functions:
            name = str(item.get("name", "")).strip()
            description = str(item.get("description", "")).strip()
            output = str(item.get("output", "")).strip()
            reasons = []
            review_notes = []
            if not name:
                reasons.append("功能名称为空")
            if len(name) > 40:
                reasons.append("功能名称过长，疑似完整需求段落")
            if name.startswith(self.FRAGMENT_PREFIXES):
                reasons.append("功能名称疑似段落残片")
            if name.startswith(self.CONDITION_PREFIXES):
                reasons.append("内容疑似条件或操作流程，而非功能名称")
            if any(keyword in name for keyword in self.NON_FUNCTION_KEYWORDS):
                reasons.append("内容属于参数、后果或非功能要求")
            if re.fullmatch(r"[\d\s、.．()（）-]+", name):
                reasons.append("内容仅包含编号或符号")
            if name in seen:
                reasons.append("功能名称重复")
            if description and SequenceMatcher(None, name, description).ratio() > 0.95:
                reasons.append("功能名称与描述高度重复")
            if not output:
                reasons.append("功能Output为空")
            if output and description and SequenceMatcher(None, output, description).ratio() > 0.95:
                reasons.append("Output与完整功能描述高度重复，未完成语义拆分")
            if item.get("source") not in ("table", "numbered_list", "explicit_label"):
                reasons.append("缺少可信的结构化来源")
            if item.get("source") == "table" and (
                item.get("source_table") is None or item.get("source_row") is None
            ):
                reasons.append("缺少表格来源位置")

            semantic_list_fields = (
                "preconditions", "triggers", "expected_effects", "consequences",
                "odd_constraints", "fallback_behaviors",
            )
            for field in semantic_list_fields:
                if field not in item or not isinstance(item.get(field), list):
                    reasons.append(f"缺少结构化语义字段: {field}")
            if not item.get("preconditions"):
                review_notes.append("源功能行未明确Precondition，需人工评审")
            if not item.get("triggers"):
                review_notes.append("源功能行未明确Trigger，需人工评审")
            if not item.get("consequences"):
                review_notes.append("源功能行未明确Consequence，禁止自动推断伤害")

            checked = dict(item)
            checked["validation_reasons"] = reasons
            checked["review_notes"] = review_notes
            checked["review_status"] = "rejected" if reasons else "approved"
            if reasons:
                invalid.append(checked)
            else:
                valid.append(checked)
                seen.add(name)

        return {
            "is_valid": bool(valid) and not invalid,
            "valid_functions": valid,
            "invalid_functions": invalid,
            "valid_count": len(valid),
            "invalid_count": len(invalid),
        }

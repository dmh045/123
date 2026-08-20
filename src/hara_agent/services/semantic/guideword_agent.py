from __future__ import annotations

import sys
import time

from hara_agent.infrastructure.llm import LLMClient, LLMRequest
from hara_agent.models import FunctionDefinition, GuidewordAssessment, ReviewStatus

from .parsing import CONFIDENCE_PROMPT_CONTRACT, parse_confidence
from .traceability import resolve_guideword_sources


class GuidewordApplicabilityAgent:
    """Assess every template guideword without generating a Cartesian product."""

    PROMPT_VERSION = "guideword-applicability-v5"
    SYSTEM_PROMPT = """你是汽车功能安全HAZOP分析助手。针对给定Function/Output逐项判断模板Guideword是否具有明确的功能语义和可形成的偏差。必须覆盖输入中的每个Guideword且只出现一次。不适用不是遗漏，必须给出具体理由；不得为了凑数量判为适用。结论是候选，证据不足时标记PENDING。"""

    def __init__(self, client: LLMClient):
        self.client = client

    def assess(self, function: FunctionDefinition, guidewords: list[str]
               ) -> tuple[list[GuidewordAssessment], dict]:
        started = time.monotonic()
        expected = [str(value).strip() for value in guidewords if str(value).strip()]
        if not expected or len(expected) != len(set(expected)):
            raise ValueError("Guideword输入必须非空且唯一")
        request = LLMRequest(
            task="assess_guideword_applicability",
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=(
                f"Function={function.name}\nOutput={function.output}\nDescription={function.description}\n"
                f"Guidewords={expected}\n"
                "只返回一个JSON object，顶层必须且只能使用assessments字段："
                "{\"assessments\":[...]}。assessments每项包含guideword、applicable、rationale、"
                "confidence、status；guideword必须为string，applicable必须为boolean，rationale必须为非空string，"
                "confidence必须为0.0到1.0的JSON number。必须逐项覆盖输入的全部Guidewords，不得省略rationale。"
                "source由系统从Function证据确定性传播，禁止生成source字段。"
                + CONFIDENCE_PROMPT_CONTRACT
            ),
            schema_name="GuidewordAssessmentList",
            prompt_version=self.PROMPT_VERSION,
            metadata={"function_id": function.function_id},
            max_tokens=4096,
        )
        response = self.client.complete_json(request)
        raw = response.data.get("assessments")
        if not isinstance(raw, list):
            raise ValueError("LLM输出缺少assessments数组")

        assessments: list[GuidewordAssessment] = []
        parse_errors: list[dict[str, object]] = []
        for idx, item in enumerate(raw):
            assessment, parse_error = self._parse_item(function, item, idx)
            assessments.append(assessment)
            if parse_error is not None:
                parse_errors.append(parse_error)

        actual = [item.guideword for item in assessments]
        if len(actual) != len(set(actual)):
            raise ValueError("Guideword评估存在重复项")
        missing = [g for g in expected if g not in actual]
        extra = [item for item in actual if item not in expected]
        if missing or extra:
            raise ValueError(f"Guideword覆盖不完整: missing={missing}, extra={extra}")
        complete_count = sum(item.is_semantically_complete for item in assessments)
        incomplete_count = len(assessments) - complete_count
        review_finalized_count = sum(item.status == ReviewStatus.FINALIZED for item in assessments)
        review_pending_count = sum(item.status == ReviewStatus.PENDING for item in assessments)
        elapsed_seconds = time.monotonic() - started
        print(
            "[HARA] guideword assessment completed "
            f"function={function.function_id} expected={len(expected)} "
            f"received={len(assessments)} complete={complete_count} "
            f"incomplete={incomplete_count} review_pending={review_pending_count} "
            f"review_finalized={review_finalized_count} parse_errors={len(parse_errors)} "
            f"elapsed={elapsed_seconds:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        metadata = {
            "task": request.task,
            "function_id": function.function_id,
            "prompt_version": request.prompt_version,
            "model": response.model,
            "request_id": response.request_id,
            "usage": response.usage,
            "coverage_count": len(assessments),
            "complete_count": complete_count,
            "incomplete_count": incomplete_count,
            "review_finalized_count": review_finalized_count,
            "review_pending_count": review_pending_count,
            # Backward-compatible review lifecycle counters.
            "finalized_count": review_finalized_count,
            "pending_count": review_pending_count,
            "parse_error_count": len(parse_errors),
            "applicable_count": sum(
                item.applicable and item.is_semantically_complete
                for item in assessments
            ),
            "elapsed_seconds": round(elapsed_seconds, 3),
        }
        if parse_errors:
            metadata["parse_errors"] = parse_errors
        return assessments, metadata

    @classmethod
    def _parse_item(
        cls, function: FunctionDefinition, item: object, index: int,
    ) -> tuple[GuidewordAssessment, dict[str, object] | None]:
        raw_keys = sorted(str(key) for key in item) if isinstance(item, dict) else []
        guideword = str(item.get("guideword", "")).strip() if isinstance(item, dict) else ""
        raw_status = str(item.get("status", "")).strip() if isinstance(item, dict) else ""
        missing_fields = []
        if not guideword:
            missing_fields.append("guideword")
        if not isinstance(item, dict) or not isinstance(item.get("applicable"), bool):
            missing_fields.append("applicable")
        rationale = str(item.get("rationale", "")).strip() if isinstance(item, dict) else ""
        if not rationale:
            missing_fields.append("rationale")
        if missing_fields:
            print(
                "[HARA] guideword item validation failed "
                f"function={function.function_id} index={index} "
                f"guideword={guideword or '<missing>'} raw_keys={raw_keys} "
                f"missing_fields={missing_fields} raw_status={raw_status or '<missing>'}",
                file=sys.stderr,
                flush=True,
            )
        if any(field in missing_fields for field in ("guideword", "applicable")):
            if "guideword" in missing_fields:
                raise ValueError("GuidewordAssessment缺少guideword")
            raise ValueError("GuidewordAssessment.applicable必须为boolean")
        assessment = cls._parse(function, item)
        if "rationale" not in missing_fields:
            return assessment, None
        parse_error = {
            "function_id": function.function_id,
            "index": index,
            "guideword": guideword,
            "field": "rationale",
            "error": "missing",
            "raw_status": raw_status or "<missing>",
        }
        print(
            "[HARA] guideword item degraded "
            f"function={function.function_id} index={index} guideword={guideword} "
            "status=PENDING reason=missing_rationale",
            file=sys.stderr,
            flush=True,
        )
        return assessment, parse_error

    @staticmethod
    def _parse(function: FunctionDefinition, item: object) -> GuidewordAssessment:
        if not isinstance(item, dict):
            raise ValueError("assessments数组元素必须为object")
        if not isinstance(item.get("applicable"), bool):
            raise ValueError("Guideword applicable必须为boolean")
        rationale = str(item.get("rationale", "")).strip()
        status = (
            ReviewStatus.FINALIZED
            if rationale and str(item.get("status", "")).upper() == "FINALIZED"
            else ReviewStatus.PENDING
        )
        return GuidewordAssessment(
            function_id=function.function_id,
            guideword=str(item.get("guideword", "")).strip(),
            applicable=item["applicable"],
            rationale=rationale,
            sources=resolve_guideword_sources(function),
            status=status,
            confidence=parse_confidence(
                item.get("confidence"),
                field_name=(
                    "Guideword confidence validation failed: "
                    f"function={function.function_id} guideword={item.get('guideword', '')}"
                ),
            ),
        )

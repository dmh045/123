from __future__ import annotations

import sys
from typing import Any

from hara_agent.infrastructure.llm import LLMClient, LLMRequest
from hara_agent.models import (
    FunctionDefinition,
    GuidewordAssessment,
    MalfunctionCandidate,
    ReviewStatus,
)

from .parsing import CONFIDENCE_PROMPT_CONTRACT, parse_confidence
from .traceability import resolve_malfunction_sources


class MalfunctionHazardAgent:
    """Generate traceable malfunction and vehicle-level hazard candidates."""

    PROMPT_VERSION = "malfunction-hazard-v4"
    SYSTEM_PROMPT = """你是汽车功能安全HARA分析助手。只处理已判定适用的Guideword。Malfunction描述功能相对预期行为的偏差；functional_effect描述功能/车辆行为影响；vehicle_level_hazard描述可能造成伤害的车辆级危险状态，不得直接写人员伤亡。causal_chain必须明确从失效到车辆级危险状态的至少两段因果关系。不得复制无关子系统模板。"""

    INJURY_TERMS = ("死亡", "致命伤", "骨折", "窒息", "fatality", "death", "injury")

    def __init__(self, client: LLMClient):
        self.client = client

    def generate(self, function: FunctionDefinition,
                 assessments: list[GuidewordAssessment]
                 ) -> tuple[list[MalfunctionCandidate], dict[str, Any]]:
        applicable = [
            item for item in assessments
            if item.is_semantically_complete and item.applicable
        ]
        if any(item.function_id != function.function_id for item in assessments):
            raise ValueError("GuidewordAssessment与Function不一致")
        if not applicable:
            has_applicable = any(item.applicable for item in assessments)
            skip_reason = (
                "no_complete_applicable_guidewords"
                if has_applicable else "no_applicable_guidewords"
            )
            complete_count = sum(item.is_semantically_complete for item in assessments)
            incomplete_count = len(assessments) - complete_count
            print(
                "[HARA] malfunction skipped "
                f"function={function.function_id} reason={skip_reason} "
                f"complete={complete_count} incomplete={incomplete_count} "
                "applicable_complete=0",
                file=sys.stderr,
                flush=True,
            )
            return [], {
                "task": "derive_malfunctions_and_hazards",
                "function_id": function.function_id,
                "prompt_version": self.PROMPT_VERSION,
                "applicable_guidewords": 0,
                "candidate_count": 0,
                "skipped": True,
                "skip_reason": skip_reason,
                "complete_guidewords": complete_count,
                "incomplete_guidewords": incomplete_count,
            }
        request = LLMRequest(
            task="derive_malfunctions_and_hazards",
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=(
                f"Function={function.name}\nOutput={function.output}\nDescription={function.description}\n"
                f"ApplicableGuidewords={[item.guideword for item in applicable]}\n"
                "必须返回一个JSON object，顶层必须包含candidates字段，且candidates必须为JSON array。"
                "当前任务不需要额外顶层字段。不得使用malfunctions、results、items、candidate_list或hazards"
                "替代candidates；不得直接返回JSON array；不得在JSON前后添加说明文字。"
                "candidates每项包含malfunction_id、guideword、description、functional_effect、"
                "vehicle_level_hazard、causal_chain、confidence、status。"
                "source由系统从Function/Guideword证据确定性传播，禁止生成source字段。"
                + CONFIDENCE_PROMPT_CONTRACT
            ),
            schema_name="MalfunctionHazardCandidateList",
            prompt_version=self.PROMPT_VERSION,
            metadata={"function_id": function.function_id},
            max_tokens=8192,
        )
        response = self.client.complete_json(request)
        raw = response.data.get("candidates")
        if not isinstance(raw, list):
            raise ValueError("LLM输出缺少candidates数组")
        assessment_by_guideword = {item.guideword: item for item in assessments}
        candidates = [
            self._parse(function, item, assessment_by_guideword.get(
                str(item.get("guideword", "")).strip() if isinstance(item, dict) else ""
            ))
            for item in raw
        ]
        self._validate(candidates, applicable)
        return candidates, {
            "task": request.task,
            "function_id": function.function_id,
            "prompt_version": request.prompt_version,
            "model": response.model,
            "request_id": response.request_id,
            "usage": response.usage,
            "applicable_guidewords": len(applicable),
            "candidate_count": len(candidates),
        }

    @staticmethod
    def _parse(
        function: FunctionDefinition,
        item: Any,
        assessment: GuidewordAssessment | None = None,
    ) -> MalfunctionCandidate:
        if not isinstance(item, dict):
            raise ValueError("candidates数组元素必须为object")
        status = ReviewStatus.FINALIZED if str(item.get("status", "")).upper() == "FINALIZED" else ReviewStatus.PENDING
        sources, source_origin = resolve_malfunction_sources(function, assessment)
        candidate = MalfunctionCandidate(
            malfunction_id=str(item.get("malfunction_id", "")).strip(),
            function_id=function.function_id,
            guideword=str(item.get("guideword", "")).strip(),
            description=str(item.get("description", "")).strip(),
            functional_effect=str(item.get("functional_effect", "")).strip(),
            vehicle_level_hazard=str(item.get("vehicle_level_hazard", "")).strip(),
            causal_chain=[str(value).strip() for value in item.get("causal_chain", []) if str(value).strip()],
            sources=sources,
            status=status,
            confidence=parse_confidence(
                item.get("confidence"),
                field_name=(
                    "Malfunction confidence validation failed: "
                    f"function={function.function_id} malfunction={item.get('malfunction_id', '')}"
                ),
            ),
        )
        if sources:
            print(
                "[HARA] malfunction source resolved "
                f"function={function.function_id} malfunction={candidate.malfunction_id} "
                f"guideword={candidate.guideword} source_count={len(sources)} "
                f"source_origin={source_origin}",
                file=sys.stderr,
                flush=True,
            )
        else:
            guideword_count = len(assessment.sources) if assessment is not None else 0
            print(
                "[HARA] malfunction source unresolved "
                f"function={function.function_id} malfunction={candidate.malfunction_id} "
                f"guideword={candidate.guideword} guideword_sources={guideword_count} "
                f"function_sources={len(function.sources)}",
                file=sys.stderr,
                flush=True,
            )
        return candidate

    def _validate(self, candidates: list[MalfunctionCandidate],
                  applicable: list[GuidewordAssessment]):
        allowed = {item.guideword for item in applicable}
        ids = [item.malfunction_id for item in candidates]
        descriptions = [item.description for item in candidates]
        if len(ids) != len(set(ids)) or len(descriptions) != len(set(descriptions)):
            raise ValueError("Malfunction候选存在重复ID或重复描述")
        invalid = sorted({item.guideword for item in candidates if item.guideword not in allowed})
        if invalid:
            raise ValueError(f"Malfunction使用了未判定适用的Guideword: {invalid}")
        covered = {item.guideword for item in candidates}
        missing = sorted(allowed - covered)
        if missing:
            raise ValueError(f"适用Guideword缺少Malfunction候选: {missing}")
        for item in candidates:
            hazard_lower = item.vehicle_level_hazard.lower()
            if any(term in hazard_lower for term in self.INJURY_TERMS):
                raise ValueError(f"Hazard不得直接写伤害结果: {item.malfunction_id}")
            if not item.sources or not item.sources[0].location:
                raise ValueError(f"Malfunction缺少来源位置: {item.malfunction_id}")

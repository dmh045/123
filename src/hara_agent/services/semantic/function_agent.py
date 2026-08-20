from __future__ import annotations

from typing import Any

from hara_agent.infrastructure.llm import LLMClient, LLMRequest
from hara_agent.models import FunctionDefinition, ReviewStatus, SourceRef
from hara_agent.services.validation import FunctionValidator

from .parsing import CONFIDENCE_PROMPT_CONTRACT, parse_confidence


def _optional_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"functions.{field} must be an array or null")
    return [str(entry) for entry in value]


class FunctionExtractionAgent:
    """Use an LLM for semantic candidates, then enforce a deterministic contract."""

    PROMPT_VERSION = "function-extraction-v3"
    SYSTEM_PROMPT = """你是汽车功能安全HARA分析助手。只抽取车辆级功能，不得把标题、条件、步骤、后果、质量要求或段落残片当作功能。Function必须是简短能力名称，Output必须描述其可观察输出。所有字段必须引用输入中的source_id和location；证据不足时标记PENDING，不得补写通用项目事实。"""

    def __init__(self, client: LLMClient, validator: FunctionValidator | None = None):
        self.client = client
        self.validator = validator or FunctionValidator()

    def extract(self, document_text: str, source_id: str) -> tuple[list[FunctionDefinition], dict[str, Any]]:
        if not document_text.strip():
            raise ValueError("Item Definition文本为空")
        request = LLMRequest(
            task="extract_item_functions",
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=(
                "从以下Item Definition抽取functions数组。每项必须包含function_id、name、output、"
                "description、preconditions、triggers、odd_constraints、fallback_behavior、consequences、"
                "source_location、source_excerpt、confidence、status。最多返回20个Function；每项列表字段最多10条，"
                + CONFIDENCE_PROMPT_CONTRACT +
                "source_excerpt最多160个字符。不得输出解释、Markdown或额外字段。\n\n" + document_text
            ),
            schema_name="FunctionDefinitionList",
            prompt_version=self.PROMPT_VERSION,
            metadata={"source_id": source_id},
            max_tokens=12288,
        )
        response = self.client.complete_json(request)
        raw_items = response.data.get("functions")
        # If response has a single function directly at top level, wrap it to array
        if raw_items is None and 'function_id' in response.data and 'name' in response.data:
            raw_items = [response.data]
        elif not isinstance(raw_items, list):
            # If it's a single object instead of array, wrap it
            if isinstance(raw_items, dict):
                raw_items = [raw_items]
            else:
                keys = sorted(str(key) for key in response.data)[:12]
                raise ValueError(f"LLM输出缺少functions数组；顶层字段: {keys}")
        functions = [self._parse(item, source_id) for item in raw_items]
        self._validate_unique(functions)
        self.validator.ensure_valid(functions)
        audit = {
            "task": request.task,
            "prompt_version": request.prompt_version,
            "model": response.model,
            "request_id": response.request_id,
            "usage": response.usage,
            "candidate_count": len(functions),
        }
        return functions, audit

    @staticmethod
    def _parse(item: Any, source_id: str) -> FunctionDefinition:
        if not isinstance(item, dict):
            raise ValueError("functions数组元素必须为object")
        status_text = str(item.get("status", "PENDING")).upper()
        status = ReviewStatus.FINALIZED if status_text == "FINALIZED" else ReviewStatus.PENDING
        return FunctionDefinition(
            function_id=str(item.get("function_id", "")).strip(),
            name=str(item.get("name", "")).strip(),
            output=str(item.get("output", "")).strip(),
            description=str(item.get("description", "")).strip(),
            preconditions=_optional_string_list(item.get("preconditions"), "preconditions"),
            triggers=_optional_string_list(item.get("triggers"), "triggers"),
            odd_constraints=_optional_string_list(item.get("odd_constraints"), "odd_constraints"),
            fallback_behavior=str(item.get("fallback_behavior", "")).strip(),
            consequences=_optional_string_list(item.get("consequences"), "consequences"),
            sources=[SourceRef(
                "item_definition", source_id,
                str(item.get("source_location", "")),
                str(item.get("source_excerpt", "")),
            )],
            status=status,
            confidence=parse_confidence(
                item.get("confidence"),
                field_name=f"Function confidence (function={item.get('function_id', '')})",
            ),
        )

    @staticmethod
    def _validate_unique(functions: list[FunctionDefinition]):
        ids = [item.function_id for item in functions]
        names = [item.name for item in functions]
        if len(ids) != len(set(ids)):
            raise ValueError("LLM输出包含重复function_id")
        if len(names) != len(set(names)):
            raise ValueError("LLM输出包含重复Function名称")

from __future__ import annotations

from typing import Any

from hara_agent.models import FunctionDefinition, ReviewStatus, SourceRef

from .parsing import parse_confidence


def _optional_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"functions.{field} must be an array or null")
    return [str(entry) for entry in value]


class FunctionNormalizer:
    """Normalize functions returned by the single item-artifact extraction call."""

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

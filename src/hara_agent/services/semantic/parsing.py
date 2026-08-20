from __future__ import annotations

import math
from typing import Any


CONFIDENCE_PROMPT_CONTRACT = (
    "confidence必须为0.0到1.0范围内的JSON number，例如0.85；"
    "禁止使用high、medium、low等定性字符串。"
)


def parse_confidence(
    value: Any,
    *,
    field_name: str = "confidence",
    default: float | None = 0.0,
) -> float:
    """Parse the shared Semantic confidence contract without qualitative guessing."""
    if value is None:
        if default is None:
            raise ValueError(f"{field_name}不能为空；expected=number[0.0,1.0]")
        value = default
    if isinstance(value, bool):
        raise ValueError(
            f"{field_name}必须为0.0~1.0数值；actual_type=bool；"
            "expected=number[0.0,1.0]"
        )
    if isinstance(value, (int, float)):
        confidence = float(value)
    elif isinstance(value, str):
        try:
            confidence = float(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"{field_name}必须为0.0~1.0数值；actual={value!r}；"
                "expected=number[0.0,1.0]"
            ) from exc
    else:
        raise ValueError(
            f"{field_name}必须为0.0~1.0数值；actual_type={type(value).__name__}；"
            "expected=number[0.0,1.0]"
        )
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError(
            f"{field_name}必须位于0.0~1.0；actual={confidence!r}；"
            "expected=number[0.0,1.0]"
        )
    return confidence

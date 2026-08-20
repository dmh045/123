from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMRequest:
    task: str
    system_prompt: str
    user_prompt: str
    schema_name: str
    prompt_version: str
    metadata: dict[str, Any] = field(default_factory=dict)
    max_tokens: int | None = None
    # Canonical schema is carried for audit/snapshot purposes even when the
    # configured Provider route has no verified native json_schema support.
    response_schema: dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMResponse:
    data: dict[str, Any]
    model: str
    request_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    def complete_json(self, request: LLMRequest) -> LLMResponse:
        """Return a parsed JSON object matching the requested semantic contract."""
        ...

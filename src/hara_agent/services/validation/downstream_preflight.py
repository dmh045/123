from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hara_agent.workflow.state import HARAState


class DownstreamPreflightService:
    """Read-only checks that must finish before expensive Scenario LLM work."""

    @staticmethod
    def _unique(values: list[Any], key, label: str) -> None:
        seen = set()
        for value in values:
            identity = key(value)
            if not identity:
                raise ValueError(f"Downstream preflight: {label} ID不能为空")
            if identity in seen:
                raise ValueError(
                    f"Downstream preflight: {label} ID不唯一，禁止进入Scenario阶段: {identity!r}"
                )
            seen.add(identity)

    def validate(self, state: "HARAState") -> dict[str, Any]:
        self._unique(
            state.functions, lambda item: str(item.get("function_id", "")), "Function",
        )
        self._unique(
            state.malfunctions, lambda item: str(item.get("malfunction_id", "")), "Malfunction",
        )
        function_ids = {str(item.get("function_id", "")) for item in state.functions}
        missing_function_fks = sorted({
            str(item.get("function_id", ""))
            for item in state.malfunctions
            if str(item.get("function_id", "")) not in function_ids
        })
        if missing_function_fks:
            raise ValueError(
                f"Downstream preflight: Malfunction引用未知Function: {missing_function_fks}"
            )
        result = {
            "identity": "OK",
            "function_count": len(state.functions),
            "malfunction_count": len(state.malfunctions),
        }
        print(
            "[HARA] downstream preflight "
            f"identity=OK functions={len(state.functions)} malfunctions={len(state.malfunctions)} "
            "foreign_keys=OK",
            file=sys.stderr,
            flush=True,
        )
        return result

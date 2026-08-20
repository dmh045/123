from __future__ import annotations

from typing import Any, Protocol

from hara_agent.models import SourceRef


class ScenarioScoringService(Protocol):
    def score(
        self, scenario: dict[str, Any], hazard_event: str
    ) -> dict[str, dict[str, Any]]:
        ...


class ASILLookupService(Protocol):
    source: str

    def determine(self, severity: str, exposure: str, controllability: str) -> str:
        ...

    def evidence_source(
        self, severity: str, exposure: str, controllability: str
    ) -> SourceRef:
        ...


class SafetyGoalService(Protocol):
    @property
    def is_approved(self) -> bool:
        ...

    def classify(self, function_name: str) -> str | None:
        ...

    def register(self, **kwargs: Any) -> dict[str, str]:
        ...

    def to_dict(self) -> dict[str, dict[str, Any]]:
        ...

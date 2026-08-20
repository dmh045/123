from __future__ import annotations

from dataclasses import dataclass, field

from .common import ReviewStatus, SourceRef


@dataclass
class FunctionDefinition:
    function_id: str
    name: str
    output: str
    description: str = ""
    preconditions: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    odd_constraints: list[str] = field(default_factory=list)
    fallback_behavior: str = ""
    consequences: list[str] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)
    status: ReviewStatus = ReviewStatus.PENDING
    confidence: float = 0.0

    def __post_init__(self):
        if not self.function_id or not self.name or not self.output:
            raise ValueError("FunctionDefinition必须包含ID、简短功能名和Output")
        if self.name.strip() == self.output.strip():
            raise ValueError("Function与Output不得完全相同")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Function confidence必须在0到1之间")


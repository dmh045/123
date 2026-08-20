from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Generic, Optional, TypeVar


class ReviewStatus(str, Enum):
    FINALIZED = "FINALIZED"
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FactProvenance(str, Enum):
    """Authority/provenance of a fact, independent from evidence kind."""

    PROJECT_INPUT = "PROJECT_INPUT"
    METHOD_CONTRACT = "METHOD_CONTRACT"
    DOMAIN_POLICY = "DOMAIN_POLICY"
    LEGACY_MIGRATION = "LEGACY_MIGRATION"
    DERIVED = "DERIVED"
    LLM_INFERENCE = "LLM_INFERENCE"
    HUMAN_CONFIRMATION = "HUMAN_CONFIRMATION"


# Architecture documents use authority and provenance for the same axis.
# Expose an alias instead of maintaining two value systems that can drift.
FactAuthority = FactProvenance


@dataclass(frozen=True)
class SourceRef:
    source_type: str
    source_id: str
    location: str = ""
    excerpt: str = ""


T = TypeVar("T")


@dataclass
class EvidenceValue(Generic[T]):
    value: Optional[T]
    status: ReviewStatus
    sources: list[SourceRef] = field(default_factory=list)
    confidence: Optional[float] = None
    rule_version: str = ""
    review_reason: str = ""

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.status is ReviewStatus.FINALIZED and self.value is None:
            raise ValueError("FINALIZED evidence must contain a value")
        if self.status is ReviewStatus.FINALIZED and not (self.sources or self.rule_version):
            raise ValueError("FINALIZED evidence must have a source or approved rule version")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result

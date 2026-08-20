from __future__ import annotations

from dataclasses import dataclass, field

from .common import ReviewStatus, SourceRef


@dataclass
class GuidewordAssessment:
    function_id: str
    guideword: str
    applicable: bool
    rationale: str
    sources: list[SourceRef] = field(default_factory=list)
    status: ReviewStatus = ReviewStatus.PENDING
    confidence: float = 0.0

    def __post_init__(self):
        if not str(self.function_id).strip():
            raise ValueError("GuidewordAssessment缺少function_id")
        if not str(self.guideword).strip():
            raise ValueError("GuidewordAssessment缺少guideword")
        if not isinstance(self.applicable, bool):
            raise ValueError("GuidewordAssessment.applicable必须为boolean")
        if self.status == ReviewStatus.FINALIZED and not str(self.rationale).strip():
            raise ValueError("FINALIZED GuidewordAssessment缺少rationale")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Guideword confidence必须在0到1之间")

    @property
    def is_semantically_complete(self) -> bool:
        """Whether the assessment can safely drive draft derivation."""
        return bool(str(self.rationale).strip())


@dataclass
class MalfunctionCandidate:
    malfunction_id: str
    function_id: str
    guideword: str
    description: str
    functional_effect: str
    vehicle_level_hazard: str
    causal_chain: list[str]
    sources: list[SourceRef] = field(default_factory=list)
    status: ReviewStatus = ReviewStatus.PENDING
    confidence: float = 0.0
    model_local_id: str = ""

    def __post_init__(self):
        required = (
            self.malfunction_id, self.function_id, self.guideword, self.description,
            self.functional_effect, self.vehicle_level_hazard,
        )
        if not all(str(value).strip() for value in required):
            raise ValueError("MalfunctionCandidate缺少必填语义字段")
        if len(self.causal_chain) < 2:
            raise ValueError("MalfunctionCandidate至少需要两段因果链")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Malfunction confidence必须在0到1之间")

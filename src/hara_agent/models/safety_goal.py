from __future__ import annotations

from dataclasses import dataclass, field

from .common import ReviewStatus


@dataclass
class SafetyGoal:
    sg_id: str
    text: str
    safe_state: str
    max_asil: str
    associated_scenario_ids: list[str] = field(default_factory=list)
    status: ReviewStatus = ReviewStatus.PENDING

    def __post_init__(self):
        if not self.sg_id or not self.text or not self.safe_state:
            raise ValueError("SafetyGoal必须包含ID、目标文本和安全状态")

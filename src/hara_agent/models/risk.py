from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from .common import EvidenceValue


@dataclass
class RiskAssessment:
    assessment_id: str
    scenario_id: str
    severity: EvidenceValue[str]
    exposure: EvidenceValue[str]
    controllability: EvidenceValue[str]
    asil: EvidenceValue[str]
    ftti_seconds: EvidenceValue[float]
    malfunction_id: str = ""
    hazardous_event: str = ""
    potential_harm: str = ""
    exposure_tf: str = ""
    safety_goal_id: str = ""

    def __post_init__(self):
        if not self.assessment_id or not self.scenario_id:
            raise ValueError("RiskAssessment必须具有assessment_id并关联scenario_id")

    def to_dict(self):
        result = asdict(self)
        for key in ("severity", "exposure", "controllability", "asil", "ftti_seconds"):
            result[key] = getattr(self, key).to_dict()
        return result


@dataclass
class RiskGroup:
    representative_scenario_id: str
    covered_scenario_ids: list[str]
    signature: tuple

    def __post_init__(self):
        if self.representative_scenario_id not in self.covered_scenario_ids:
            raise ValueError("代表场景必须包含在covered_scenario_ids中")

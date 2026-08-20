from __future__ import annotations

from typing import Any

from hara_agent.domains import DomainPolicy
from hara_agent.services.extraction import TemplateScoreLevel, TemplateScoringStandards


class DomainScoringService:
    """Score structured scenarios through a versioned Domain Policy.

    This service owns the active structured scoring path. It deliberately does
    not parse hazard prose or load legacy reference JSON files.
    """

    def __init__(self, policy: DomainPolicy, standards: TemplateScoringStandards):
        self.policy = policy
        self.standards = standards

    def score(self, scenario: dict[str, Any], hazard_event: str) -> dict[str, dict[str, Any]]:
        if not self.policy.is_structured_scenario(scenario):
            raise ValueError("DomainScoringService只接受结构化Domain场景")

        severity = self.policy.severity_decision(scenario)
        exposure = self.policy.exposure_decision(scenario)
        controllability = self.policy.controllability_decision(scenario)
        missing = [
            name for name, value in (
                ("Severity", severity),
                ("Exposure", exposure),
                ("Controllability", controllability),
            ) if value is None
        ]
        if missing:
            raise ValueError(
                f"结构化场景缺少{', '.join(missing)} Domain Policy判据，禁止回退到关键词评分"
            )

        exposure_method = str(exposure.get("exposure_method", "")).upper()
        references = {
            "severity": self.standards.reference("severity", severity["score"]),
            "exposure": self.standards.reference(
                "exposure", exposure["score"], exposure_method=exposure_method
            ),
            "controllability": self.standards.reference(
                "controllability", controllability["score"]
            ),
        }
        return {
            "severity": self._severity_result(
                severity, references["severity"], scenario, hazard_event
            ),
            "exposure": self._exposure_result(exposure, references["exposure"], scenario),
            "controllability": self._controllability_result(
                controllability, references["controllability"], hazard_event
            ),
        }

    def _audit(
        self,
        decision: dict[str, Any],
        standard: TemplateScoreLevel,
    ) -> dict[str, Any]:
        return {
            "engineering_source_type": "domain_profile",
            "engineering_source": str(self.policy.profile.path),
            "engineering_rule_id": decision["rule_id"],
            "engineering_rule_version": decision["rule_version"],
            "engineering_status": decision["engineering_status"],
            "engineering_basis": decision["basis"],
            "template_standard_source": str(self.standards.source_path),
            "template_standard_sheet": standard.sheet,
            "template_standard_location": standard.location,
            "template_standard_description": standard.description,
            "template_standard_criterion": standard.criterion,
            "_source": "domain_policy_structured_scoring",
        }

    def _severity_result(self, decision: dict[str, Any], standard: TemplateScoreLevel,
                         scenario: dict[str, Any],
                         hazard_event: str) -> dict[str, Any]:
        result = {
            "hazard_event": hazard_event,
            "severity_score": decision["score"],
            "reasoning": decision["basis"],
            "affected_road_users": self._affected_users(scenario),
        }
        result.update(self._audit(decision, standard))
        return result

    def _exposure_result(self, decision: dict[str, Any], standard: TemplateScoreLevel,
                         scenario: dict[str, Any]) -> dict[str, Any]:
        display = str(
            scenario.get("situational_description")
            or scenario.get("refined_scenario")
            or scenario.get("scenario_summary")
            or ""
        )
        result = {
            "scenario": display,
            "exposure_score": decision["score"],
            "exposure_reason": decision["basis"],
            "reasoning": decision["basis"],
            "exposure_method": str(decision["exposure_method"]).upper(),
        }
        result.update(self._audit(decision, standard))
        return result

    def _controllability_result(self, decision: dict[str, Any], standard: TemplateScoreLevel,
                                hazard_event: str) -> dict[str, Any]:
        result = {
            "hazard_event": hazard_event,
            "controllability_score": decision["score"],
            "reasoning": decision["basis"],
            "logic_types_used": ["Domain Profile structured control-context mapping"],
        }
        result.update(self._audit(decision, standard))
        return result

    @staticmethod
    def _affected_users(scenario: dict[str, Any]) -> list[str]:
        object_type = str(scenario.get("object_type", "")).lower()
        if "行人" in object_type or "pedestrian" in object_type:
            return ["行人", "车辆乘员"]
        if any(token in object_type for token in ("车辆", "vehicle", "car")):
            return ["本车乘员", "其他车辆乘员"]
        if any(token in object_type for token in ("墙", "柱", "wall", "column")):
            return ["车辆乘员"]
        return ["道路使用者"]

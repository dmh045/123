from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any, Optional

from hara_agent.domains import DomainPolicy
from hara_agent.models import FactProvenance, ReviewStatus, ScenarioCandidate, SourceRef
from hara_agent.services.analysis.project_fact_resolver import (
    ProjectFactResolver,
    SpeedResolutionResult,
    UnresolvedProjectContextError,
)
from hara_agent.services.semantic.scenario_batching import ScenarioFactConsistencyError
from hara_agent.services.semantic.scenario_contract import SCENARIO_CONTRACT_VERSION


class DomainScenarioCandidateService:
    """Build auditable risk-category candidates; never create a Cartesian product."""

    def __init__(self, policy: DomainPolicy):
        self.policy = policy

    def generate(
        self,
        ego_speed_kph: Optional[float] = None,
        project_source_id: str = "",
        project_source_location: str = "",
        project_driver_contexts: Optional[list[dict[str, Any]]] = None,
        project_exposure_inputs: Optional[list[dict[str, Any]]] = None,
        operating_mode: str = "",
        speed_resolution: SpeedResolutionResult | None = None,
        allow_legacy_speed_fallback: bool = True,
    ) -> tuple[list[ScenarioCandidate], dict]:
        scenario_policy = self.policy.scenario
        if speed_resolution is None and ego_speed_kph is not None:
            explicit_sources = []
            if project_source_id:
                explicit_sources.append(SourceRef(
                    "project_input", project_source_id, project_source_location,
                    f"ego_speed_kph={float(ego_speed_kph):g}",
                ))
            speed_resolution = ProjectFactResolver.explicit_project_speed(
                operating_mode or "unspecified_explicit_context",
                float(ego_speed_kph),
                sources=explicit_sources,
            )
        if speed_resolution is None:
            if not allow_legacy_speed_fallback:
                raise UnresolvedProjectContextError(
                    operating_mode,
                    "project speed unresolved and legacy Domain Profile fallback was not authorized",
                )
            legacy_speed = float(scenario_policy.get("fallback_parking_speed_kph", 0.0))
            speed_resolution = ProjectFactResolver.legacy_migration_speed(
                operating_mode or "unspecified_legacy_context",
                legacy_speed,
                SourceRef(
                    "domain_profile", str(self.policy.profile.path),
                    "scenario_policy.fallback_parking_speed_kph",
                    self.policy.profile.source,
                ),
            )
        speed = float(speed_resolution.resolved_value)
        if speed < 0:
            raise ValueError("ego_speed_kph不得为负数")
        explicit_driver_contexts = bool(project_driver_contexts)
        driver_contexts = list(project_driver_contexts or scenario_policy.get("driver_context_candidates", []))
        if not driver_contexts:
            raise ValueError("Item Definition和Domain Profile均未提供驾驶员控制上下文")
        evidence_final = (
            self.policy.profile.is_approved
            and speed_resolution.provenance is FactProvenance.PROJECT_INPUT
            and speed_resolution.approval is ReviewStatus.FINALIZED
            and bool(speed_resolution.source_refs)
            and explicit_driver_contexts
        )
        if speed_resolution.provenance is FactProvenance.LEGACY_MIGRATION:
            speed_source_status = "MIGRATION_FALLBACK"
        elif speed_resolution.fallback_used:
            speed_source_status = "EXPLICIT_AGGREGATE_FALLBACK"
        elif speed_resolution.source_refs:
            speed_source_status = "PROJECT_INPUT"
        else:
            speed_source_status = "PROJECT_INPUT_UNTRACED"
        status = ReviewStatus.FINALIZED if evidence_final else ReviewStatus.PENDING
        review_reason = "" if evidence_final else (
            "Domain Profile或项目车速来源尚未批准；当前候选仅用于Draft评审"
        )
        sources = [SourceRef(
            "domain_profile", str(self.policy.profile.path), "risk_candidates",
            self.policy.profile.source,
        )]
        sources.extend(
            source for source in speed_resolution.source_refs if source not in sources
        )
        profile_source = SourceRef(
            "domain_profile", str(self.policy.profile.path), "risk_candidates",
            self.policy.profile.source,
        )
        profile_approval = (
            ReviewStatus.FINALIZED if self.policy.profile.is_approved else ReviewStatus.PENDING
        )

        candidates = []
        for candidate_id in self.policy.risk_candidates:
            atomic_candidates = self.policy.atomic_candidates(candidate_id, speed)
            for atomic_variant, base_facts in atomic_candidates:
              for context_index, context in enumerate(driver_contexts, start=1):
                facts = dict(base_facts)
                fact_provenance = {
                    key: self._fact_metadata(
                        self.policy.profile.fact_provenance,
                        profile_approval,
                        [profile_source],
                        origin="domain_risk_candidate",
                    )
                    for key in facts
                }
                context_id = str(context.get("context_id", f"context_{context_index}"))
                exposure_override = self._exposure_override(candidate_id, project_exposure_inputs or [])
                if exposure_override:
                    facts.update(exposure_override)
                    facts["exposure_source_status"] = "PROJECT_INPUT"
                    exposure_sources = self._source_refs(context=exposure_override)
                    for key in ("exposure_level_candidate", "exposure_method", "exposure_basis"):
                        fact_provenance[key] = self._fact_metadata(
                            FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING,
                            exposure_sources, origin="project_exposure_input",
                        )
                else:
                    facts["exposure_source_status"] = "DOMAIN_CANDIDATE_PENDING"
                facts.update({
                "ego_speed_kph": speed,
                "driver_context_id": context_id,
                "driver_position": str(context.get("driver_position", "")),
                "driver_state": str(context.get("driver_state", "")),
                "direct_vehicle_control": context.get("direct_vehicle_control"),
                "intervention_channels": list(context.get("intervention_channels", [])),
                "driver_context_source_status": (
                    "PROJECT_INPUT" if explicit_driver_contexts else "DOMAIN_CANDIDATE_PENDING"
                ),
                "weather_conditions": scenario_policy.get("weather_conditions", ""),
                "road_surface_conditions": scenario_policy.get("road_surface_conditions", ""),
                "parking_space_type": scenario_policy.get("parking_space_type", ""),
                "speed_source_status": speed_source_status,
                })
                if operating_mode:
                    facts["operating_mode"] = operating_mode
                fact_provenance["ego_speed_kph"] = self._fact_metadata(
                    speed_resolution.provenance, speed_resolution.approval,
                    list(speed_resolution.source_refs),
                    fallback_used=speed_resolution.fallback_used,
                    resolution_source=speed_resolution.resolution_source,
                )
                driver_provenance = (
                    FactProvenance.PROJECT_INPUT
                    if explicit_driver_contexts else self.policy.profile.fact_provenance
                )
                driver_approval = (
                    ReviewStatus.PENDING if explicit_driver_contexts else profile_approval
                )
                driver_sources = (
                    self._source_refs(context=context) if explicit_driver_contexts else [profile_source]
                )
                for key in (
                    "driver_context_id", "driver_position", "driver_state",
                    "direct_vehicle_control", "intervention_channels",
                ):
                    fact_provenance[key] = self._fact_metadata(
                        driver_provenance, driver_approval, driver_sources,
                        origin=("project_driver_context" if explicit_driver_contexts else "domain_driver_candidate"),
                    )
                for key in (
                    "weather_conditions", "road_surface_conditions", "parking_space_type",
                ):
                    fact_provenance[key] = self._fact_metadata(
                        self.policy.profile.fact_provenance, profile_approval,
                        [profile_source], origin="domain_scenario_policy",
                    )
                if operating_mode:
                    fact_provenance["operating_mode"] = self._fact_metadata(
                        FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, [],
                        origin="explicit_run_config",
                    )
                self._validate_fact_consistency(candidate_id, atomic_variant, facts)
                fingerprint = self._semantic_fingerprint(candidate_id, atomic_variant, facts)
                scenario_id = "SCN-" + "-".join(
                    self._identity_token(value)
                    for value in (candidate_id, atomic_variant, context_id)
                )
                object_type = str(facts.get("object_type", "目标物"))
                object_position = str(facts.get("object_position", ""))
                distance = str(facts.get("relative_distance", ""))
                description = str(
                    scenario_policy.get("scenario_name", self.policy.profile.name)
                )
                detail_parts = [
                f"自车{speed:g}km/h",
                f"目标物：{object_type}",
                f"位置：{object_position}" if object_position else "",
                f"距离：{distance}" if distance else "",
                f"驾驶员：{facts['driver_state']}" if facts["driver_state"] else "",
                ]
                candidates.append(ScenarioCandidate(
                    scenario_id=scenario_id,
                    operating_scenario=description,
                    situational_description=description,
                    situational_detailing="；".join(part for part in detail_parts if part),
                    operating_mode=operating_mode,
                    facts=facts,
                    context_resolution={"ego_speed_kph": speed_resolution.to_dict()},
                    fact_provenance=fact_provenance,
                    status=status,
                    sources=list(sources),
                    rule_version=self.policy.profile.version,
                    review_reason=review_reason,
                    source_scenario_id=candidate_id,
                    atomic_variant=atomic_variant,
                    semantic_fingerprint=fingerprint,
                    scenario_contract_version=SCENARIO_CONTRACT_VERSION,
                ))
                print(
                    "[HARA] scenario atomic expansion "
                    f"source_scenario_id={candidate_id} atomic_scenario_id={scenario_id} "
                    f"atomic_variant={atomic_variant} "
                    f"scenario_contract_version={SCENARIO_CONTRACT_VERSION} "
                    f"semantic_fingerprint={fingerprint} fact_consistency=OK",
                    file=sys.stderr, flush=True,
                )
        return candidates, {
            "candidate_count": len(candidates),
            "candidate_ids": list(self.policy.risk_candidates),
            "combination_strategy": "domain_risk_categories_no_cartesian_product",
            "ego_speed_kph": speed,
            "operating_mode": operating_mode,
            "speed_source_status": speed_source_status,
            "speed_resolution": speed_resolution.to_dict(),
            "driver_context_source_status": (
                "PROJECT_INPUT" if explicit_driver_contexts else "DOMAIN_CANDIDATE_PENDING"
            ),
            "driver_context_count": len(driver_contexts),
            "status": status.value,
            "scenario_contract_version": SCENARIO_CONTRACT_VERSION,
            "atomic_candidate_count": len(candidates),
        }

    @staticmethod
    def _identity_token(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
        if not token:
            raise ValueError("Scenario semantic identity component不能为空")
        return token

    @staticmethod
    def _semantic_fingerprint(
        source_scenario_id: str, atomic_variant: str, facts: dict[str, Any],
    ) -> str:
        semantic_facts = {
            key: value for key, value in facts.items()
            if not key.endswith("_source_status") and key not in {
                "engineering_status", "review_reason", "rule_version",
            }
        }
        material = {
            "contract": SCENARIO_CONTRACT_VERSION,
            "source_scenario_id": source_scenario_id,
            "atomic_variant": atomic_variant,
            "facts": semantic_facts,
        }
        return hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _validate_fact_consistency(
        source_scenario_id: str, atomic_variant: str, facts: dict[str, Any],
    ) -> None:
        mode = str(facts.get("relative_speed_mode", "")).strip()
        ego = facts.get("ego_speed_kph")
        target = facts.get("target_speed_kph")
        relative = facts.get("relative_speed_kph")
        if not mode or ego is None or target is None or relative is None:
            raise ScenarioFactConsistencyError(
                f"Scenario fact consistency=FAIL source_scenario_id={source_scenario_id} "
                f"atomic_variant={atomic_variant} missing relative-speed semantics"
            )
        expected = {
            "zero": 0.0,
            "ego": abs(float(ego)),
            "ego_plus_target": abs(float(ego)) + abs(float(target)),
        }.get(mode)
        if expected is None or abs(float(relative) - expected) > 1e-9:
            raise ScenarioFactConsistencyError(
                f"Scenario fact consistency=FAIL source_scenario_id={source_scenario_id} "
                f"atomic_variant={atomic_variant} field=relative_speed_kph actual={relative!r} "
                f"expected={expected!r} basis={mode!r}"
            )

    @staticmethod
    def _exposure_override(
        candidate_id: str,
        evidence_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        for evidence in evidence_items:
            if str(evidence.get("scenario_variant", "")) != candidate_id:
                continue
            level = str(evidence.get("level", "")).upper()
            method = str(evidence.get("method", "")).upper()
            if level in {"E0", "E1", "E2", "E3", "E4"} and method in {"T", "F"}:
                return {
                    "exposure_level_candidate": level,
                    "exposure_method": method,
                    "exposure_basis": str(evidence.get("basis", "")).strip(),
                }
        return {}

    @staticmethod
    def _source_refs(*, context: dict[str, Any]) -> list[SourceRef]:
        return [
            item if isinstance(item, SourceRef) else SourceRef(**item)
            for item in context.get("source_refs", context.get("sources", []))
            if isinstance(item, (SourceRef, dict))
        ]

    @staticmethod
    def _fact_metadata(
        provenance: FactProvenance,
        approval: ReviewStatus,
        sources: list[SourceRef],
        **metadata: Any,
    ) -> dict[str, Any]:
        return {
            "provenance": provenance.value,
            "approval": approval.value,
            "source_refs": [
                {
                    "source_type": source.source_type,
                    "source_id": source.source_id,
                    "location": source.location,
                    "excerpt": source.excerpt,
                }
                for source in sources
            ],
            **metadata,
        }


AVPScenarioCandidateService = DomainScenarioCandidateService

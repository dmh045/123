from __future__ import annotations

import hashlib
import json
import re
from itertools import product
from typing import Any

from hara_agent.contracts import MethodContract, ScenarioDimension
from hara_agent.models import (
    FactProvenance,
    ItemDefinitionFacts,
    ReviewStatus,
    ScenarioCandidate,
    SourceRef,
)
from hara_agent.services.analysis.project_fact_resolver import SpeedResolutionResult
from .method_risk_fact_service import MethodRiskFactBindingService
from hara_agent.services.semantic.scenario_contract import SCENARIO_CONTRACT_VERSION


class MethodScenarioCandidateService:
    """Bind grounded ProjectFacts to MethodContract scenario dimensions."""

    def __init__(self, method: MethodContract):
        self.method = method
        self.risk_facts = MethodRiskFactBindingService(method)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^\w]+", "", value.casefold(), flags=re.UNICODE)

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in values if item.strip()))

    def _text_binding(
        self, dimension: ScenarioDimension, project_value: str
    ) -> dict[str, str]:
        token = self._normalize(project_value)
        matches = [
            item for item in dimension.values
            if token and (
                token in self._normalize(item) or self._normalize(item) in token
            )
        ]
        return {
            "project_value": project_value,
            "method_value": matches[0] if len(matches) == 1 else "",
            "binding_status": (
                "EXACT" if len(matches) == 1
                else "AMBIGUOUS" if len(matches) > 1
                else "UNRESOLVED"
            ),
            "dimension_source": (
                f"{dimension.source_ref.sheet}!{dimension.source_ref.range}"
            ),
        }

    def _speed_binding(
        self, dimension: ScenarioDimension, speed_kph: float
    ) -> dict[str, str]:
        matches = [
            value for value in dimension.values
            if self._speed_value_matches(value, speed_kph)
        ]
        return {
            "project_value": f"{speed_kph:g} km/h",
            "method_value": matches[0] if len(matches) == 1 else "",
            "binding_status": (
                "EXACT" if len(matches) == 1
                else "AMBIGUOUS" if len(matches) > 1
                else "UNRESOLVED"
            ),
            "dimension_source": (
                f"{dimension.source_ref.sheet}!{dimension.source_ref.range}"
            ),
        }

    @staticmethod
    def _speed_value_matches(raw: str, speed_kph: float) -> bool:
        text = raw.casefold().replace("≤", "<=").replace("≥", ">=")
        compact = re.sub(r"\s+", "", text)
        numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", compact)]
        if not numbers:
            return False
        if len(numbers) == 1 and ("standstill" in text or "静止" in text):
            return speed_kph == numbers[0]
        if len(numbers) < 2:
            return False
        lower, upper = numbers[0], numbers[1]
        lower_ok = speed_kph > lower if f"{lower:g}<v" in compact else speed_kph >= lower
        upper_ok = speed_kph <= upper if f"v<={upper:g}" in compact else speed_kph < upper
        return lower_ok and upper_ok

    def _project_values(
        self,
        dimension: ScenarioDimension,
        facts: ItemDefinitionFacts,
        operating_mode: str,
        speed: SpeedResolutionResult,
    ) -> list[dict[str, str]]:
        name = dimension.canonical_name
        if name == "OPERATING_SCENARIO":
            values = self._unique(facts.odd_locations or facts.odd_road_types)
        elif name == "VEHICLE_STATE":
            values = self._unique([operating_mode])
        elif name == "WEATHER":
            values = self._unique(facts.odd_weather_conditions)
        elif name == "ROAD_SURFACE":
            values = self._unique(facts.odd_road_surfaces)
        elif name == "VEHICLE_SPEED":
            return [self._speed_binding(dimension, speed.resolved_value)]
        else:
            values = []
        if not values:
            return [{
                "project_value": "",
                "method_value": "",
                "binding_status": "MISSING",
                "dimension_source": (
                    f"{dimension.source_ref.sheet}!{dimension.source_ref.range}"
                ),
            }]
        return [self._text_binding(dimension, value) for value in values]

    @staticmethod
    def _source_dict(source: SourceRef) -> dict[str, str]:
        return {
            "source_type": source.source_type,
            "source_id": source.source_id,
            "location": source.location,
            "excerpt": source.excerpt,
        }

    @staticmethod
    def _fact_metadata(
        provenance: FactProvenance,
        approval: ReviewStatus,
        sources: list[SourceRef],
    ) -> dict[str, Any]:
        return {
            "provenance": provenance.value,
            "approval": approval.value,
            "source_refs": [
                MethodScenarioCandidateService._source_dict(item) for item in sources
            ],
        }

    def generate(
        self,
        *,
        project_facts: ItemDefinitionFacts,
        operating_mode: str,
        speed_resolution: SpeedResolutionResult,
    ) -> tuple[list[ScenarioCandidate], dict[str, Any]]:
        dimensions = list(self.method.scenario_model.dimensions)
        options = [
            self._project_values(
                dimension, project_facts, operating_mode, speed_resolution
            )
            for dimension in dimensions
        ]
        candidates: list[ScenarioCandidate] = []
        risk_fact_audits: list[dict[str, Any]] = []
        unresolved_count = 0
        project_sources = list(project_facts.sources)
        speed_sources = list(speed_resolution.source_refs)
        for dimension_values in product(*options):
            bindings = {
                dimension.canonical_name: value
                for dimension, value in zip(dimensions, dimension_values)
            }
            unresolved = [
                name for name, value in bindings.items()
                if value["binding_status"] != "EXACT"
            ]
            unresolved_count += bool(unresolved)
            facts: dict[str, Any] = {
                "method_scenario_dimensions": bindings,
                "operating_mode": operating_mode,
                "ego_speed_kph": speed_resolution.resolved_value,
            }
            canonical_keys = {
                "OPERATING_SCENARIO": "operating_scenario",
                "VEHICLE_STATE": "vehicle_state",
                "WEATHER": "weather_conditions",
                "ROAD_SURFACE": "road_surface_conditions",
            }
            for dimension_name, key in canonical_keys.items():
                binding = bindings.get(dimension_name, {})
                facts[key] = binding.get("method_value") or binding.get("project_value", "")
            fact_provenance: dict[str, Any] = {}
            for dimension_name, key in canonical_keys.items():
                dimension = next(
                    item for item in dimensions
                    if item.canonical_name == dimension_name
                )
                method_source = SourceRef(
                    "method_contract",
                    str(self.method.metadata["template_hash"]),
                    f"{dimension.source_ref.sheet}!{dimension.source_ref.range}",
                    dimension.source_ref.raw_text,
                )
                binding = bindings.get(dimension_name, {})
                binding_approval = (
                    project_facts.status
                    if binding.get("binding_status") == "EXACT"
                    else ReviewStatus.PENDING
                )
                fact_provenance[key] = self._fact_metadata(
                    FactProvenance.DERIVED,
                    binding_approval,
                    [*project_sources, method_source],
                )
            fact_provenance["ego_speed_kph"] = self._fact_metadata(
                speed_resolution.provenance,
                speed_resolution.approval,
                speed_sources,
            )
            material = {
                "contract": SCENARIO_CONTRACT_VERSION,
                "template_hash": self.method.metadata["template_hash"],
                "bindings": bindings,
            }
            fingerprint = hashlib.sha256(
                json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            scenario_id = f"SCN-METHOD-{fingerprint[:16].upper()}"
            risk_binding = self.risk_facts.bind(project_facts, {
                **facts,
                "scenario_id": scenario_id,
            })
            facts.update(risk_binding.values)
            fact_provenance.update(risk_binding.provenance)
            risk_fact_audits.append(risk_binding.audit)
            summary = "；".join(
                value["method_value"] or value["project_value"] or f"{name}:未解析"
                for name, value in bindings.items()
            )
            finalized = (
                not unresolved
                and project_facts.status is ReviewStatus.FINALIZED
                and speed_resolution.approval is ReviewStatus.FINALIZED
                and bool(project_sources)
            )
            method_sources = [
                SourceRef(
                    "method_contract",
                    str(self.method.metadata["template_hash"]),
                    f"{item.source_ref.sheet}!{item.source_ref.range}",
                    item.source_ref.raw_text,
                )
                for item in dimensions
            ]
            fact_provenance["operating_mode"] = self._fact_metadata(
                FactProvenance.PROJECT_INPUT,
                project_facts.status,
                project_sources,
            )
            fact_provenance["method_scenario_dimensions"] = self._fact_metadata(
                FactProvenance.DERIVED,
                ReviewStatus.FINALIZED if finalized else ReviewStatus.PENDING,
                [*project_sources, *method_sources],
            )
            candidates.append(ScenarioCandidate(
                scenario_id=scenario_id,
                operating_scenario=facts.get("operating_scenario", operating_mode),
                situational_description=summary,
                situational_detailing=summary,
                facts=facts,
                operating_mode=operating_mode,
                context_resolution={
                    "ego_speed_kph": speed_resolution.to_dict(),
                    "dimension_bindings": bindings,
                    "risk_fact_binding": risk_binding.audit,
                },
                fact_provenance=fact_provenance,
                status=(ReviewStatus.FINALIZED if finalized else ReviewStatus.PENDING),
                sources=list(dict.fromkeys([
                    *project_sources, *speed_sources, *method_sources,
                    *risk_binding.sources,
                ])),
                rule_version=self.method.contract_version,
                review_reason=(
                    "" if finalized
                    else "Project facts or MethodContract dimension bindings require review."
                ),
                source_scenario_id="METHOD_SCENARIO_ONTOLOGY",
                atomic_variant="method_dimensions",
                semantic_fingerprint=fingerprint,
                scenario_contract_version=SCENARIO_CONTRACT_VERSION,
            ))
        return candidates, {
            "candidate_count": len(candidates),
            "atomic_candidate_count": len(candidates),
            "combination_strategy": "method_dimensions_constrained_by_project_facts",
            "template_hash": self.method.metadata["template_hash"],
            "scenario_dimension_count": len(dimensions),
            "unresolved_binding_candidate_count": unresolved_count,
            "ego_speed_kph": speed_resolution.resolved_value,
            "operating_mode": operating_mode,
            "speed_source_status": speed_resolution.resolution_status.value,
            "speed_resolution": speed_resolution.to_dict(),
            "bound_risk_fact_count": sum(
                len(item["bound_fact_types"]) for item in risk_fact_audits
            ),
            "missing_risk_fact_types": sorted({
                fact_type for item in risk_fact_audits
                for fact_type in item["missing_fact_types"]
            }),
            "conflicting_risk_fact_types": sorted({
                fact_type for item in risk_fact_audits
                for fact_type in item["conflicting_fact_types"]
            }),
            "risk_fact_template_hash_mismatch_count": max(
                (item["template_hash_mismatch_count"] for item in risk_fact_audits),
                default=0,
            ),
            "scenario_contract_version": SCENARIO_CONTRACT_VERSION,
        }

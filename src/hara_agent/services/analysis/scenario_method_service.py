"""Typed consumers for confirmed Scenario method knowledge.

The service deliberately returns context only.  It neither emits causal evidence
nor changes candidate, S/E/C, ASIL, or FTTI evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from hara_agent.contracts import FMScenarioTemplate, MethodContract
from hara_agent.models import MalfunctionCandidate, ScenarioCandidate

from .failure_mode_selector_resolver import (
    FMTemplateSelectorAdapterResolver, FailureModeSelectorResolution,
    FailureModeSelectorResolver, TemplateSelectorResolution,
)


@dataclass(frozen=True)
class FMTemplateMatchResult:
    status: str
    template: FMScenarioTemplate | None
    matching_template_ids: tuple[str, ...]
    original_precedence_template_id: str
    reason: str
    matched_by: tuple[str, ...] = ()
    matched_terms: tuple[str, ...] = ()
    template_selector_resolution: tuple[TemplateSelectorResolution, ...] = ()
    selector_resolution: FailureModeSelectorResolution | None = None
    qualification_tier: str = ""

    @property
    def injectable(self) -> bool:
        return self.status == "STRONG_MATCH" and self.template is not None


class ScenarioMethodService:
    """Resolve explicitly compiled Scenario method context without YAML access."""

    def __init__(self, method: MethodContract):
        self.method = method
        self.selector_resolver = FailureModeSelectorResolver(method)
        self.template_selector_resolver = FMTemplateSelectorAdapterResolver(method)

    def _selector_template_ids(
        self, selector_type: str, canonical_selector: str,
    ) -> tuple[str, ...]:
        """Return all source templates supported by one canonical selector.

        This is qualification-only metadata.  Candidate discovery remains the
        original keyword OR component OR failure selector relation.
        """
        if not canonical_selector:
            return ()
        catalog = self.method.scenario_model.scenario_method.fm_template_catalog
        if catalog is None:
            return ()
        identifiers: list[str] = []
        for template in catalog.templates:
            raw_values = (
                template.match.component_categories
                if selector_type == "COMPONENT_CATEGORY"
                else template.match.failure_types
            )
            if any(
                self.template_selector_resolver.resolve(
                    selector_type, raw,
                ).canonical_template_selector == canonical_selector
                for raw in raw_values
            ):
                identifiers.append(template.template_id)
        return tuple(identifiers)

    def _qualify_matches(
        self,
        matches: list[tuple[
            FMScenarioTemplate, tuple[str, ...], tuple[str, ...],
            tuple[TemplateSelectorResolution, ...],
        ]],
        *, component: str, failure_type: str,
    ) -> tuple[str, int | None, str]:
        """Select only source-supported unique template evidence.

        A structured component/failure disagreement is not resolved by tier;
        it remains fail-closed.  Keyword evidence is discovery-only and never
        lowers a unique structured qualification.
        """
        component_hits = [
            index for index, item in enumerate(matches)
            if "COMPONENT_CATEGORY" in item[1]
        ]
        failure_hits = [
            index for index, item in enumerate(matches)
            if "FAILURE_TYPE" in item[1]
        ]
        both_hits = [
            index for index in component_hits if index in set(failure_hits)
        ]
        if len(both_hits) == 1:
            return "STRONG_MATCH", both_hits[0], "TIER_1_COMPONENT_AND_FAILURE"
        if len(both_hits) > 1:
            return "AMBIGUOUS", None, "TIER_1_COMPONENT_AND_FAILURE"
        if component_hits and failure_hits and set(component_hits) != set(failure_hits):
            return "AMBIGUOUS", None, "STRUCTURED_SELECTOR_CONFLICT"
        component_templates = self._selector_template_ids(
            "COMPONENT_CATEGORY", component,
        )
        if len(component_templates) == 1 and len(component_hits) == 1:
            return "STRONG_MATCH", component_hits[0], "TIER_2_UNIQUE_COMPONENT"
        failure_templates = self._selector_template_ids("FAILURE_TYPE", failure_type)
        if len(failure_templates) == 1 and len(failure_hits) == 1:
            return "STRONG_MATCH", failure_hits[0], "TIER_3_UNIQUE_FAILURE_TYPE"
        keyword_only = [
            index for index, item in enumerate(matches)
            if item[1] == ("KEYWORD",)
        ]
        if keyword_only and not component_hits and not failure_hits and len(keyword_only) == 1:
            return "WEAK_MATCH", keyword_only[0], "TIER_4_KEYWORD_ONLY"
        return "AMBIGUOUS", None, "UNRESOLVED_SOURCE_SUPPORT"

    def match_fm_template(self, malfunction: MalfunctionCandidate) -> FMTemplateMatchResult:
        selector_resolution = self.selector_resolver.resolve(malfunction)
        catalog = self.method.scenario_model.scenario_method.fm_template_catalog
        if catalog is None:
            return FMTemplateMatchResult(
                "NO_MATCH", None, (), "", "NO_TEMPLATE_CATALOG",
                selector_resolution=selector_resolution,
            )
        fm_text = " ".join((
            malfunction.description, malfunction.functional_effect,
            malfunction.vehicle_level_hazard, malfunction.guideword,
        )).casefold()
        component = selector_resolution.canonical_component_category
        failure_type = selector_resolution.canonical_failure_type
        matches: list[tuple[
            FMScenarioTemplate,
            tuple[str, ...],
            tuple[str, ...],
            tuple[TemplateSelectorResolution, ...],
        ]] = []
        for template in catalog.templates:
            match = template.match
            keyword_terms = tuple(
                keyword for keyword in match.keywords if keyword.casefold() in fm_text
            )
            component_resolutions = tuple(
                self.template_selector_resolver.resolve("COMPONENT_CATEGORY", value)
                for value in match.component_categories
            )
            failure_type_resolutions = tuple(
                self.template_selector_resolver.resolve("FAILURE_TYPE", value)
                for value in match.failure_types
            )
            matched_component_selectors = tuple(
                item for item in component_resolutions
                if component and item.canonical_template_selector == component
            )
            matched_failure_selectors = tuple(
                item for item in failure_type_resolutions
                if failure_type and item.canonical_template_selector == failure_type
            )
            component_terms = tuple(
                item.raw_template_selector for item in matched_component_selectors
            )
            failure_type_terms = tuple(
                item.raw_template_selector for item in matched_failure_selectors
            )
            matched_by = tuple(name for name, terms in (
                ("KEYWORD", keyword_terms),
                ("COMPONENT_CATEGORY", component_terms),
                ("FAILURE_TYPE", failure_type_terms),
            ) if terms)
            if matched_by:
                matches.append((
                    template, matched_by,
                    keyword_terms + component_terms + failure_type_terms,
                    matched_component_selectors + matched_failure_selectors,
                ))
        if not matches:
            return FMTemplateMatchResult(
                "NO_MATCH", None, (), "", "NO_SOURCE_DEFINED_MATCH",
                selector_resolution=selector_resolution,
            )
        original_first = min(matches, key=lambda item: item[0].original_precedence)[0]
        identifiers = tuple(item[0].template_id for item in matches)
        qualification, selected_index, qualification_tier = self._qualify_matches(
            matches, component=component, failure_type=failure_type,
        )
        if selected_index is None:
            return FMTemplateMatchResult(
                "AMBIGUOUS", None, identifiers, original_first.template_id,
                (
                    "MULTIPLE_SOURCE_DEFINED_TEMPLATES_MATCH; original first-match "
                    "precedence recorded but not consumed"
                ),
                selector_resolution=selector_resolution,
                qualification_tier=qualification_tier,
            )
        template, matched_by, matched_terms, template_selector_resolution = matches[selected_index]
        return FMTemplateMatchResult(
            qualification, template, identifiers, original_first.template_id,
            (
                "STRUCTURED_SELECTOR_MATCH"
                if qualification == "STRONG_MATCH" else "WEAK_KEYWORD_ONLY"
            ),
            matched_by=matched_by, matched_terms=matched_terms,
            template_selector_resolution=template_selector_resolution,
            selector_resolution=selector_resolution,
            qualification_tier=qualification_tier,
        )

    @staticmethod
    def _context_value(scenario: Any, keys: tuple[str, ...]) -> Any:
        for key in keys:
            value = scenario.facts.get(key)
            if value not in (None, ""):
                return value
        return None

    def bind_template_context(
        self, malfunction: MalfunctionCandidate, scenario: ScenarioCandidate,
    ) -> tuple[ScenarioCandidate, dict[str, Any]]:
        """Return an ephemeral M×Scenario context view; never mutate the global candidate."""
        result = self.match_fm_template(malfunction)
        audit: dict[str, Any] = {
            "malfunction_id": malfunction.malfunction_id,
            "scenario_id": scenario.scenario_id,
            "template_id": result.template.template_id if result.template else "",
            "qualification": result.status,
            "matched_by": list(result.matched_by),
            "matched_terms": list(result.matched_terms),
            "template_selector_resolution": [
                item.to_dict() for item in result.template_selector_resolution
            ],
            "injected": False,
            "reason": result.reason,
        }
        if not result.injectable:
            return scenario, audit
        assert result.template is not None
        mapping = {
            "obj_type": ("object_type", "object", "road_user_type"),
            "obj_position": ("object_position",),
            "obj_distance_m": ("object_distance_m", "relative_distance_m", "distance_m"),
            "obj_v_kph": ("object_speed_kph", "obj_v_kph"),
            "collision_type": ("collision_type",),
        }
        compatible = []
        conflicts = []
        for option in result.template.required_scenarios:
            option_values = {
                "obj_type": option.obj_type, "obj_position": option.obj_position,
                "obj_distance_m": option.obj_distance_m, "obj_v_kph": option.obj_v_kph,
                "collision_type": option.collision_type,
            }
            option_conflicts = []
            for field, keys in mapping.items():
                current = self._context_value(scenario, keys)
                if current is not None and current != option_values[field]:
                    option_conflicts.append({"field": field, "scenario_value": current,
                                             "template_value": option_values[field]})
            if option_conflicts:
                conflicts.extend(option_conflicts)
            else:
                compatible.append((option, option_values))
        if not compatible:
            audit.update({
                "reason": "METHOD_SOURCE_CONFLICT", "conflicts": conflicts,
                "not_injected_reason": "METHOD_SOURCE_CONFLICT",
            })
            return scenario, audit
        if len(compatible) != 1:
            audit.update({
                "reason": "MULTIPLE_TEMPLATE_CONTEXT_OPTIONS",
                "not_injected_reason": "ATOMIC_SCENARIO_ALIGNMENT_PENDING",
                "candidate_context_count": len(compatible),
            })
            return scenario, audit
        option, values = compatible[0]
        context = {
            "source_kind": "METHOD_TEMPLATE",
            "template_id": result.template.template_id,
            "qualification": result.status,
            "match_basis": list(result.matched_by),
            "matched_terms": list(result.matched_terms),
            "values": values,
            "source_ref": {
                "asset": option.source_ref.workbook,
                "location": option.source_ref.range,
                "hash": option.source_ref.source_hash,
            },
            "method_dimension_binding": "METHOD_DIMENSION_BINDING_PENDING",
            "causal_evidence_eligible": False,
        }
        contextual = replace(
            scenario,
            context_resolution={
                **scenario.context_resolution,
                "malfunction_template_context": context,
            },
        )
        audit.update({"injected": True, "reason": "STRONG_MATCH", "injected_context_fields": list(values)})
        return contextual, audit

    def fallback_terms(self) -> tuple[dict[str, object], ...]:
        """Expose unmapped fallback terms for audit; never guess a target dimension."""
        domain = self.method.scenario_model.scenario_method.domain_knowledge
        if domain is None:
            return ()
        return tuple({
            "source_dimension": item.source_dimension,
            "terms": list(item.terms),
            "target_dimension": item.target_dimension,
            "target_status": item.target_status,
            "source_role": item.source_role,
            "source_ref": {
                "asset": item.source_ref.workbook,
                "hash": item.source_ref.source_hash,
                "location": item.source_ref.range,
            },
        } for item in domain.fallback_dimensions)

    def fallback_context_for_missing_project_fact(
        self, *, source_dimension: str, explicit_project_value: str,
    ) -> dict[str, object]:
        """Return declared fallback only for an absent fact and never map it by guesswork."""
        if explicit_project_value.strip():
            return {
                "status": "EXPLICIT_PROJECT_FACT_PRESENT",
                "source_dimension": source_dimension,
                "terms": [],
            }
        for item in self.fallback_terms():
            if item["source_dimension"] == source_dimension:
                return {"status": "TARGET_DIMENSION_PENDING", **item}
        return {"status": "NO_CONFIRMED_FALLBACK", "source_dimension": source_dimension, "terms": []}

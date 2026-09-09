"""Offline trace and qualification audit for governed FM scenario templates.

The audit replays the compiled raw template selectors and the governed C9F
adapter.  It does not read YAML at runtime, alter the candidate matcher, or
resolve an ambiguous template by source order.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from hara_agent.contracts import FMScenarioTemplate, MethodContract
from hara_agent.models import MalfunctionCandidate, ReviewStatus

from .failure_mode_selector_resolver import FMTemplateSelectorAdapterResolver
from .scenario_method_service import ScenarioMethodService


@dataclass(frozen=True)
class _TemplateSupport:
    template: FMScenarioTemplate
    keyword_terms: tuple[str, ...]
    component_terms: tuple[str, ...]
    failure_terms: tuple[str, ...]
    component_resolution: tuple[dict[str, Any], ...]
    failure_resolution: tuple[dict[str, Any], ...]

    @property
    def matched_by(self) -> tuple[str, ...]:
        return tuple(name for name, terms in (
            ("KEYWORD", self.keyword_terms),
            ("COMPONENT_CATEGORY", self.component_terms),
            ("FAILURE_TYPE", self.failure_terms),
        ) if terms)

    @property
    def candidate(self) -> bool:
        return bool(self.matched_by)

    @property
    def component(self) -> bool:
        return bool(self.component_terms)

    @property
    def failure(self) -> bool:
        return bool(self.failure_terms)

    @property
    def keyword(self) -> bool:
        return bool(self.keyword_terms)


class FMTemplateAmbiguityAuditService:
    """Replay raw and canonical selector support for review only."""

    def __init__(self, method: MethodContract):
        self.method = method
        self.scenario_method = ScenarioMethodService(method)
        self.adapter = FMTemplateSelectorAdapterResolver(method)
        catalog = method.scenario_model.scenario_method.fm_template_catalog
        self.templates = catalog.templates if catalog is not None else ()
        self._component_catalog = self._catalog_index("COMPONENT_CATEGORY")
        self._failure_catalog = self._catalog_index("FAILURE_TYPE")

    def _catalog_index(self, selector_type: str) -> dict[str, tuple[str, ...]]:
        values: dict[str, list[str]] = defaultdict(list)
        for template in self.templates:
            selectors = (
                template.match.component_categories
                if selector_type == "COMPONENT_CATEGORY"
                else template.match.failure_types
            )
            for raw in selectors:
                resolved = self.adapter.resolve(selector_type, raw)
                if resolved.canonical_template_selector:
                    values[resolved.canonical_template_selector].append(template.template_id)
        return {key: tuple(sorted(set(value))) for key, value in values.items()}

    @staticmethod
    def _candidate(raw: dict[str, Any]) -> MalfunctionCandidate | None:
        try:
            return MalfunctionCandidate(
                malfunction_id=str(raw["malfunction_id"]),
                function_id=str(raw["function_id"]),
                guideword=str(raw["guideword"]),
                guideword_id=str(raw.get("guideword_id", raw["guideword"])),
                description=str(raw["description"]),
                functional_effect=str(raw["functional_effect"]),
                vehicle_level_hazard=str(raw["vehicle_level_hazard"]),
                causal_chain=[str(value) for value in raw.get("causal_chain", [])],
                status=ReviewStatus(str(raw.get("status", ReviewStatus.PENDING.value))),
                confidence=float(raw.get("confidence", 0.0)),
                component_category=str(raw.get("component_category", "")),
                failure_type=str(raw.get("failure_type", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _supports(self, candidate: MalfunctionCandidate) -> list[_TemplateSupport]:
        resolution = self.scenario_method.selector_resolver.resolve(candidate)
        text = " ".join((
            candidate.description, candidate.functional_effect,
            candidate.vehicle_level_hazard, candidate.guideword,
        )).casefold()
        supports: list[_TemplateSupport] = []
        for template in self.templates:
            keyword_terms = tuple(
                value for value in template.match.keywords if value.casefold() in text
            )
            component = tuple(
                self.adapter.resolve("COMPONENT_CATEGORY", value)
                for value in template.match.component_categories
            )
            failure = tuple(
                self.adapter.resolve("FAILURE_TYPE", value)
                for value in template.match.failure_types
            )
            supports.append(_TemplateSupport(
                template=template,
                keyword_terms=keyword_terms,
                component_terms=tuple(
                    item.raw_template_selector for item in component
                    if resolution.canonical_component_category
                    and item.canonical_template_selector == resolution.canonical_component_category
                ),
                failure_terms=tuple(
                    item.raw_template_selector for item in failure
                    if resolution.canonical_failure_type
                    and item.canonical_template_selector == resolution.canonical_failure_type
                ),
                component_resolution=tuple(item.to_dict() for item in component),
                failure_resolution=tuple(item.to_dict() for item in failure),
            ))
        return supports

    @staticmethod
    def _qualification_preview(
        supports: list[_TemplateSupport], *, component_template_ids: tuple[str, ...],
        failure_template_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        candidates = [item for item in supports if item.candidate]
        both = [item for item in candidates if item.component and item.failure]
        component = [item for item in candidates if item.component]
        failure = [item for item in candidates if item.failure]
        keywords = [item for item in candidates if item.keyword and not (item.component or item.failure)]
        candidate_ids = [item.template.template_id for item in candidates]
        component_ids = [item.template.template_id for item in component]
        failure_ids = [item.template.template_id for item in failure]
        if len(both) == 1:
            return {
                "status": "STRONG_MATCH", "tier": "TIER_1_COMPONENT_AND_FAILURE",
                "template_id": both[0].template.template_id,
                "candidate_template_ids": candidate_ids,
            }
        if len(both) > 1:
            return {
                "status": "AMBIGUOUS", "tier": "TIER_1_COMPONENT_AND_FAILURE",
                "template_id": "", "candidate_template_ids": candidate_ids,
            }
        # Conflicting structured selectors must fail closed; keyword never creates this conflict.
        if component and failure and set(component_ids) != set(failure_ids):
            return {
                "status": "AMBIGUOUS", "tier": "STRUCTURED_SELECTOR_CONFLICT",
                "template_id": "", "candidate_template_ids": candidate_ids,
            }
        if len(component_template_ids) == 1 and len(component) == 1:
            return {
                "status": "STRONG_MATCH", "tier": "TIER_2_UNIQUE_COMPONENT",
                "template_id": component[0].template.template_id,
                "candidate_template_ids": candidate_ids,
            }
        if len(failure_template_ids) == 1 and len(failure) == 1:
            return {
                "status": "STRONG_MATCH", "tier": "TIER_3_UNIQUE_FAILURE_TYPE",
                "template_id": failure[0].template.template_id,
                "candidate_template_ids": candidate_ids,
            }
        if keywords and not component and not failure and len(keywords) == 1:
            return {
                "status": "WEAK_MATCH", "tier": "TIER_4_KEYWORD_ONLY",
                "template_id": keywords[0].template.template_id,
                "candidate_template_ids": candidate_ids,
            }
        return {
            "status": "AMBIGUOUS" if len(candidates) > 1 else "NO_MATCH",
            "tier": "UNRESOLVED_SOURCE_SUPPORT",
            "template_id": "", "candidate_template_ids": candidate_ids,
        }

    @staticmethod
    def _original_candidate_qualification(supports: list[_TemplateSupport]) -> str:
        """The pre-C9G qualification: any multi-template OR hit was ambiguous."""
        candidates = [item for item in supports if item.candidate]
        if not candidates:
            return "NO_MATCH"
        if len(candidates) > 1:
            return "AMBIGUOUS"
        return (
            "STRONG_MATCH"
            if candidates[0].component or candidates[0].failure
            else "WEAK_MATCH"
        )

    @staticmethod
    def _raw_replay(
        supports: list[_TemplateSupport],
    ) -> dict[str, Any]:
        component_to_templates: dict[str, list[str]] = defaultdict(list)
        failure_to_templates: dict[str, list[str]] = defaultdict(list)
        parent_subtypes: set[str] = set()
        for item in supports:
            for resolved in item.component_resolution:
                raw = str(resolved["raw_template_selector"])
                if raw in item.component_terms:
                    component_to_templates[raw].append(item.template.template_id)
                    if resolved["mapping_semantics"] == "FUNCTIONAL_PARENT":
                        parent_subtypes.add(raw)
            for resolved in item.failure_resolution:
                raw = str(resolved["raw_template_selector"])
                if raw in item.failure_terms:
                    failure_to_templates[raw].append(item.template.template_id)
        return {
            "component_raw_selector_matches": {
                key: sorted(set(value)) for key, value in sorted(component_to_templates.items())
            },
            "failure_raw_selector_matches": {
                key: sorted(set(value)) for key, value in sorted(failure_to_templates.items())
            },
            "functional_parent_source_subtypes": sorted(parent_subtypes),
        }

    @staticmethod
    def _root_causes(
        supports: list[_TemplateSupport], raw_replay: dict[str, Any],
        *, component_template_ids: tuple[str, ...], failure_template_ids: tuple[str, ...],
    ) -> tuple[str, list[str]]:
        candidates = [item for item in supports if item.candidate]
        structured = [item for item in candidates if item.component or item.failure]
        keyword_only = [item for item in candidates if item.keyword and not (item.component or item.failure)]
        original_raw_ambiguous = any(
            len(ids) > 1
            for ids in (
                *raw_replay["component_raw_selector_matches"].values(),
                *raw_replay["failure_raw_selector_matches"].values(),
            )
        )
        parent_collapse = len(raw_replay["functional_parent_source_subtypes"]) > 1
        generic_failure = len(failure_template_ids) > 1 and any(item.failure for item in candidates)
        cross_hit = bool(structured and keyword_only)
        equal_structured = sum(item.component and item.failure for item in candidates) > 1
        causes: list[str] = []
        if original_raw_ambiguous:
            causes.append("ORIGINAL_SOURCE_AMBIGUITY")
        if parent_collapse:
            causes.append("FUNCTIONAL_PARENT_COLLAPSE")
        if generic_failure:
            causes.append("GENERIC_FAILURE_TYPE_OVERLAP")
        if cross_hit:
            causes.append("KEYWORD_STRUCTURED_CROSS_HIT")
        if equal_structured:
            causes.append("MULTI_STRUCTURED_EQUAL_SUPPORT")
        if not causes:
            causes.append("OTHER")
        if parent_collapse:
            primary = "FUNCTIONAL_PARENT_COLLAPSE"
        elif generic_failure:
            primary = "GENERIC_FAILURE_TYPE_OVERLAP"
        elif cross_hit:
            primary = "KEYWORD_STRUCTURED_CROSS_HIT"
        elif original_raw_ambiguous:
            primary = "ORIGINAL_SOURCE_AMBIGUITY"
        elif equal_structured:
            primary = "MULTI_STRUCTURED_EQUAL_SUPPORT"
        else:
            primary = "OTHER"
        return primary, causes

    def generate(self, records: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        before = Counter()
        after = Counter()
        root_counts = Counter()
        root_all_counts = Counter()
        origin_counts = Counter()
        adapter_expanded = 0
        original_ambiguity = 0
        parent_collapse = 0
        for raw in records.get("malfunction", []):
            if not isinstance(raw, dict):
                continue
            candidate = self._candidate(raw)
            if candidate is None:
                continue
            supports = self._supports(candidate)
            original_status = self._original_candidate_qualification(supports)
            before[original_status] += 1
            current = self.scenario_method.match_fm_template(candidate)
            component_ids = self._component_catalog.get(
                current.selector_resolution.canonical_component_category
                if current.selector_resolution is not None else "", ()
            )
            failure_ids = self._failure_catalog.get(
                current.selector_resolution.canonical_failure_type
                if current.selector_resolution is not None else "", ()
            )
            preview = self._qualification_preview(
                supports, component_template_ids=component_ids,
                failure_template_ids=failure_ids,
            )
            after[preview["status"]] += 1
            if original_status != "AMBIGUOUS":
                continue
            raw_replay = self._raw_replay(supports)
            primary, causes = self._root_causes(
                supports, raw_replay,
                component_template_ids=component_ids,
                failure_template_ids=failure_ids,
            )
            root_counts[primary] += 1
            root_all_counts.update(causes)
            original = "ORIGINAL_SOURCE_AMBIGUITY" in causes
            if original:
                original_ambiguity += 1
            if "FUNCTIONAL_PARENT_COLLAPSE" in causes:
                parent_collapse += 1
            expanded = len(preview["candidate_template_ids"]) > max(
                [len(value) for value in raw_replay["component_raw_selector_matches"].values()]
                + [len(value) for value in raw_replay["failure_raw_selector_matches"].values()]
                + [0]
            )
            if expanded:
                adapter_expanded += 1
            origin = (
                "ADAPTER_EXPANDED_AMBIGUITY" if original and expanded else
                "ORIGINAL_SOURCE_AMBIGUITY" if original else
                "ADAPTER_INTRODUCED_AMBIGUITY" if expanded else
                "QUALIFICATION_ONLY_AMBIGUITY"
            )
            origin_counts[origin] += 1
            rows.append({
                "malfunction_id": candidate.malfunction_id,
                "function_id": candidate.function_id,
                "guideword_id": candidate.guideword_id,
                "guideword": candidate.guideword,
                "component_category": candidate.component_category,
                "failure_type": candidate.failure_type,
                "matching_templates": [
                    {
                        "template_id": item.template.template_id,
                        "source_order": item.template.original_precedence,
                        "raw_component_selectors": list(item.template.match.component_categories),
                        "canonical_component_selectors": [
                            value["canonical_template_selector"]
                            for value in item.component_resolution
                        ],
                        "raw_failure_selectors": list(item.template.match.failure_types),
                        "canonical_failure_selectors": [
                            value["canonical_template_selector"]
                            for value in item.failure_resolution
                        ],
                        "keywords": list(item.template.match.keywords),
                        "matched_by": list(item.matched_by),
                        "matched_terms": list(
                            item.keyword_terms + item.component_terms + item.failure_terms
                        ),
                        "adapter_mappings": [
                            value for value in (
                                *item.component_resolution, *item.failure_resolution
                            ) if value.get("mapping_id")
                        ],
                        "support_count": sum((item.keyword, item.component, item.failure)),
                        "support_dimensions": list(item.matched_by),
                    }
                    for item in supports if item.candidate
                ],
                "original_first_match_template": current.original_precedence_template_id,
                "original_raw_replay": raw_replay,
                "root_causes": causes,
                "primary_root_cause": primary,
                "original_source_ambiguity": original,
                "ambiguity_origin": origin,
                "qualification_preview": preview,
            })

        return {
            "artifact_version": "fm-template-ambiguity-audit-v1",
            "runtime_behavior_changed": False,
            "llm_calls_added": 0,
            "scope": "OFFLINE_REVIEW_AND_QUALIFICATION_PREVIEW_ONLY",
            "first_match_precedence": {
                "source_order_present": True,
                "normative_priority_declared": False,
                "finding": (
                    "The compiled source order is recorded for audit, but the raw template "
                    "asset does not declare a normative priority field."
                ),
            },
            "selector_discriminative_power": {
                "failure_type": {
                    key: {"matching_template_ids": list(value), "matching_template_count": len(value),
                          "classification": "NON_DISCRIMINATIVE_SELECTOR" if len(value) > 1 else "DISCRIMINATIVE_SELECTOR"}
                    for key, value in sorted(self._failure_catalog.items())
                },
                "component_category": {
                    key: {"matching_template_ids": list(value), "matching_template_count": len(value),
                          "classification": "NON_DISCRIMINATIVE_PARENT_SELECTOR" if len(value) > 1 else "DISCRIMINATIVE_SELECTOR"}
                    for key, value in sorted(self._component_catalog.items())
                },
            },
            "before_qualification": {
                key: before[key] for key in ("STRONG_MATCH", "WEAK_MATCH", "AMBIGUOUS", "NO_MATCH")
            },
            "qualification_preview": {
                "after": {
                    key: after[key] for key in ("STRONG_MATCH", "WEAK_MATCH", "AMBIGUOUS", "NO_MATCH")
                },
                "resolved_by": dict(sorted(Counter(
                    row["qualification_preview"]["tier"] for row in rows
                    if row["qualification_preview"]["status"] == "STRONG_MATCH"
                ).items())),
                "unresolved_count": sum(
                    row["qualification_preview"]["status"] == "AMBIGUOUS" for row in rows
                ),
            },
            "ambiguity_summary": {
                "total_ambiguous": len(rows),
                "by_root_cause": dict(sorted(root_all_counts.items())),
                "by_primary_root_cause": dict(sorted(root_counts.items())),
                "by_ambiguity_origin": dict(sorted(origin_counts.items())),
                "original_source_ambiguity_count": original_ambiguity,
                "adapter_expanded_ambiguity_count": adapter_expanded,
                "functional_parent_collapse_count": parent_collapse,
            },
            "ambiguous_matches": rows,
        }


__all__ = ["FMTemplateAmbiguityAuditService"]

"""Exact, governed normalization for Failure Mode template selectors.

The resolver consumes only ``MethodContract``.  It is intentionally not a
classifier: no semantic similarity, keyword inference, or Python-authored
synonyms are used to fill a missing selector.
"""

from __future__ import annotations

from dataclasses import dataclass

from hara_agent.contracts import (
    FMTemplateSelectorAdapter, FailureModeSelectorTaxonomy, MethodContract,
)
from hara_agent.models import MalfunctionCandidate


RESOLVED_EXACT = "RESOLVED_EXACT"
RESOLVED_ALIAS = "RESOLVED_ALIAS"
PENDING_MISSING_SOURCE_VALUE = "PENDING_MISSING_SOURCE_VALUE"
PENDING_NO_GOVERNED_MAPPING = "PENDING_NO_GOVERNED_MAPPING"
INVALID_TAXONOMY_VALUE = "INVALID_TAXONOMY_VALUE"
RESOLVED_TEMPLATE_ADAPTER = "RESOLVED_TEMPLATE_ADAPTER"
SELECTOR_MAPPING_AMBIGUOUS = "SELECTOR_MAPPING_AMBIGUOUS"


@dataclass(frozen=True)
class FailureModeSelectorResolution:
    raw_component_category: str
    canonical_component_category: str
    component_resolution_status: str
    component_rule_ref: dict[str, str]
    raw_failure_type: str
    canonical_failure_type: str
    failure_type_resolution_status: str
    failure_type_rule_ref: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "raw_component_category": self.raw_component_category,
            "canonical_component_category": self.canonical_component_category,
            "component_resolution_status": self.component_resolution_status,
            "component_rule_ref": dict(self.component_rule_ref),
            "raw_failure_type": self.raw_failure_type,
            "canonical_failure_type": self.canonical_failure_type,
            "failure_type_resolution_status": self.failure_type_resolution_status,
            "failure_type_rule_ref": dict(self.failure_type_rule_ref),
        }


@dataclass(frozen=True)
class TemplateSelectorResolution:
    """One raw FM template selector resolved through the compiled adapter."""

    selector_type: str
    raw_template_selector: str
    canonical_template_selector: str
    resolution_status: str
    mapping_id: str
    mapping_semantics: str
    template_rule_ref: dict[str, str]
    taxonomy_rule_ref: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "selector_type": self.selector_type,
            "raw_template_selector": self.raw_template_selector,
            "canonical_template_selector": self.canonical_template_selector,
            "resolution_status": self.resolution_status,
            "mapping_id": self.mapping_id,
            "mapping_semantics": self.mapping_semantics,
            "template_rule_ref": dict(self.template_rule_ref),
            "taxonomy_rule_ref": dict(self.taxonomy_rule_ref),
        }


class FailureModeSelectorResolver:
    """Resolve source-declared FM selector values without altering an FM."""

    def __init__(self, method: MethodContract):
        self.taxonomy = method.scenario_model.scenario_method.failure_mode_selector_taxonomy

    @staticmethod
    def _source_ref(value: object) -> dict[str, str]:
        source = getattr(value, "source_ref", value)
        if not all(hasattr(source, field) for field in ("workbook", "range", "source_hash")):
            source = None
        if source is None:
            return {}
        return {
            "asset": str(source.workbook),
            "location": str(source.range),
            "hash": str(source.source_hash),
        }

    @staticmethod
    def _resolve_value(
        raw_value: str, values: tuple[object, ...] | None,
    ) -> tuple[str, str, dict[str, str]]:
        raw = str(raw_value or "").strip()
        if not raw:
            return "", PENDING_MISSING_SOURCE_VALUE, {}
        if values is None:
            return "", PENDING_NO_GOVERNED_MAPPING, {}
        for value in values:
            if raw == value.canonical_id:
                return value.canonical_id, RESOLVED_EXACT, FailureModeSelectorResolver._source_ref(value)
        for value in values:
            if raw in value.aliases:
                return value.canonical_id, RESOLVED_ALIAS, FailureModeSelectorResolver._source_ref(value)
        return "", INVALID_TAXONOMY_VALUE, {}

    def resolve(self, malfunction: MalfunctionCandidate) -> FailureModeSelectorResolution:
        taxonomy: FailureModeSelectorTaxonomy | None = self.taxonomy
        components = taxonomy.component_categories if taxonomy is not None else None
        failure_types = taxonomy.failure_types if taxonomy is not None else None
        component, component_status, component_ref = self._resolve_value(
            malfunction.component_category, components,
        )
        failure_type, failure_status, failure_ref = self._resolve_value(
            malfunction.failure_type, failure_types,
        )
        return FailureModeSelectorResolution(
            raw_component_category=str(malfunction.component_category or "").strip(),
            canonical_component_category=component,
            component_resolution_status=component_status,
            component_rule_ref=component_ref,
            raw_failure_type=str(malfunction.failure_type or "").strip(),
            canonical_failure_type=failure_type,
            failure_type_resolution_status=failure_status,
            failure_type_rule_ref=failure_ref,
        )


class FMTemplateSelectorAdapterResolver:
    """Resolve compiled raw template selectors without loading YAML at runtime."""

    def __init__(self, method: MethodContract):
        scenario_method = method.scenario_model.scenario_method
        self.taxonomy: FailureModeSelectorTaxonomy | None = (
            scenario_method.failure_mode_selector_taxonomy
        )
        self.adapter: FMTemplateSelectorAdapter | None = (
            scenario_method.fm_template_selector_adapter
        )
        self._mappings = {
            (item.selector_type, item.source_template_value): item
            for item in (self.adapter.mappings if self.adapter is not None else ())
        }

    def resolve(self, selector_type: str, raw_template_selector: str) -> TemplateSelectorResolution:
        raw = str(raw_template_selector or "").strip()
        values = (
            self.taxonomy.component_categories
            if self.taxonomy is not None and selector_type == "COMPONENT_CATEGORY"
            else self.taxonomy.failure_types
            if self.taxonomy is not None and selector_type == "FAILURE_TYPE"
            else ()
        )
        for value in values:
            if raw == value.canonical_id:
                return TemplateSelectorResolution(
                    selector_type, raw, value.canonical_id, RESOLVED_EXACT,
                    "", "EXACT_EQUIVALENT", {},
                    FailureModeSelectorResolver._source_ref(value),
                )
        mapping = self._mappings.get((selector_type, raw))
        if mapping is None:
            return TemplateSelectorResolution(
                selector_type, raw, "", PENDING_NO_GOVERNED_MAPPING,
                "", "", {}, {},
            )
        template_ref = FailureModeSelectorResolver._source_ref(mapping.template_source_ref)
        taxonomy_ref = FailureModeSelectorResolver._source_ref(mapping.taxonomy_source_ref)
        if mapping.runtime_status == "ACTIVE":
            return TemplateSelectorResolution(
                selector_type, raw, mapping.canonical_target,
                RESOLVED_TEMPLATE_ADAPTER, mapping.mapping_id,
                mapping.mapping_semantics, template_ref, taxonomy_ref,
            )
        return TemplateSelectorResolution(
            selector_type, raw, "", SELECTOR_MAPPING_AMBIGUOUS,
            mapping.mapping_id, mapping.mapping_semantics, template_ref, taxonomy_ref,
        )

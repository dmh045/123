"""Offline audit of selected confirmed YAML utilization.

This is deliberately a review projection.  It does not mutate checkpoints or
turn template/default context into Scenario facts or causal evidence.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from hara_agent.contracts import MethodContract
from hara_agent.models import MalfunctionCandidate, ReviewStatus

from .scenario_method_service import ScenarioMethodService


class ConfirmedYamlUtilizationService:
    def __init__(self, method: MethodContract):
        self.method = method
        self.scenario_method = ScenarioMethodService(method)

    @staticmethod
    def _malfunction(raw: dict[str, Any]) -> MalfunctionCandidate | None:
        required = ("malfunction_id", "function_id", "guideword", "description",
                    "functional_effect", "vehicle_level_hazard")
        if not all(str(raw.get(key, "")).strip() for key in required):
            return None
        try:
            return MalfunctionCandidate(
                malfunction_id=str(raw["malfunction_id"]), function_id=str(raw["function_id"]),
                guideword=str(raw["guideword"]), description=str(raw["description"]),
                functional_effect=str(raw["functional_effect"]),
                vehicle_level_hazard=str(raw["vehicle_level_hazard"]),
                causal_chain=[str(item) for item in raw.get("causal_chain", ["source", "effect"])],
                status=ReviewStatus(str(raw.get("status", ReviewStatus.PENDING.value))),
                confidence=float(raw.get("confidence", 0.0)),
                component_category=str(raw.get("component_category", "")),
                failure_type=str(raw.get("failure_type", "")),
                guideword_id=str(raw.get("guideword_id", raw["guideword"])),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _asset_row(asset: str, digest: str) -> dict[str, Any]:
        return {
            "asset": asset,
            "section": "bundle_asset",
            "provenance_hash": digest,
            "confirmed_role": "HASH_BOUND_BASELINE_ASSET",
            "original_fusa_consumer": "not recorded in current runtime contract",
            "originally_executed": False,
            "compiled_to_contract": False,
            "runtime_consumer": "none",
            "used_count": 0,
            "unused_reason": "HASH_BOUND_NOT_SELECTED_FOR_THIS_CONTRACT_ROLE",
        }

    def generate(
        self,
        records: dict[str, list[dict[str, Any]]],
        gap_payload: dict[str, Any],
    ) -> dict[str, Any]:
        match_rows: list[dict[str, Any]] = []
        status_counts: Counter[str] = Counter()
        component_resolution_counts: Counter[str] = Counter()
        failure_type_resolution_counts: Counter[str] = Counter()
        raw_component_count = 0
        canonical_component_count = 0
        raw_failure_type_count = 0
        canonical_failure_type_count = 0
        component_selector_coverage = 0
        failure_type_selector_coverage = 0
        catalog = self.method.scenario_model.scenario_method.fm_template_catalog
        templates = catalog.templates if catalog is not None else ()
        component_selector_vocabulary = {
            value for template in templates for value in template.match.component_categories
        }
        failure_type_selector_vocabulary = {
            value for template in templates for value in template.match.failure_types
        }
        taxonomy = self.method.scenario_model.scenario_method.failure_mode_selector_taxonomy
        adapter = self.method.scenario_model.scenario_method.fm_template_selector_adapter
        taxonomy_vocabulary = {
            "COMPONENT_CATEGORY": {
                item.canonical_id for item in (taxonomy.component_categories if taxonomy else ())
            },
            "FAILURE_TYPE": {
                item.canonical_id for item in (taxonomy.failure_types if taxonomy else ())
            },
        }
        template_vocabulary = {
            "COMPONENT_CATEGORY": component_selector_vocabulary,
            "FAILURE_TYPE": failure_type_selector_vocabulary,
        }
        adapter_mappings = adapter.mappings if adapter is not None else ()

        def reconciliation(selector_type: str) -> dict[str, object]:
            raw_values = template_vocabulary[selector_type]
            canonical_values = taxonomy_vocabulary[selector_type]
            mappings = [item for item in adapter_mappings if item.selector_type == selector_type]
            active = [item for item in mappings if item.runtime_status == "ACTIVE"]
            ambiguous = [item for item in mappings if item.runtime_status == "UNRESOLVED"]
            covered = canonical_values | {item.source_template_value for item in mappings}
            return {
                "canonical": len(canonical_values),
                "raw": len(raw_values),
                "exact_overlap": sorted(raw_values.intersection(canonical_values)),
                "template_only": sorted(raw_values - canonical_values),
                "taxonomy_only": sorted(canonical_values - raw_values),
                "mapped_unambiguous": sorted(item.source_template_value for item in active),
                "ambiguous": sorted(item.source_template_value for item in ambiguous),
                "unmapped": sorted(raw_values - covered),
                "active_mapping_count": len(active),
                "unresolved_mapping_count": len(ambiguous),
            }
        scenario_fact_counts: Counter[str] = Counter()
        for raw in records.get("malfunction", []):
            if not isinstance(raw, dict):
                continue
            malfunction = self._malfunction(raw)
            if malfunction is None:
                continue
            result = self.scenario_method.match_fm_template(malfunction)
            assert result.selector_resolution is not None
            selector_resolution = result.selector_resolution.to_dict()
            raw_component_count += int(bool(selector_resolution["raw_component_category"]))
            canonical_component_count += int(bool(selector_resolution["canonical_component_category"]))
            raw_failure_type_count += int(bool(selector_resolution["raw_failure_type"]))
            canonical_failure_type_count += int(bool(selector_resolution["canonical_failure_type"]))
            component_selector_coverage += int(any(
                item.selector_type == "COMPONENT_CATEGORY"
                for item in result.template_selector_resolution
            ))
            failure_type_selector_coverage += int(any(
                item.selector_type == "FAILURE_TYPE"
                for item in result.template_selector_resolution
            ))
            status_counts[result.status] += 1
            component_resolution_counts[
                str(selector_resolution["component_resolution_status"])
            ] += 1
            failure_type_resolution_counts[
                str(selector_resolution["failure_type_resolution_status"])
            ] += 1
            row: dict[str, Any] = {
                "malfunction_id": malfunction.malfunction_id,
                "function_id": malfunction.function_id,
                **selector_resolution,
                "match_status": result.status,
                "matching_template_ids": list(result.matching_template_ids),
                "original_precedence_template_id": result.original_precedence_template_id,
                "reason": result.reason,
                "matched_by": list(result.matched_by),
                "matched_terms": list(result.matched_terms),
                "template_selector_resolution": [
                    item.to_dict() for item in result.template_selector_resolution
                ],
                "injected_context_fields": [],
                "not_injected_reason": (
                    "WEAK_KEYWORD_ONLY" if result.status == "WEAK_MATCH" else
                    "AMBIGUOUS" if result.status == "AMBIGUOUS" else
                    "NO_MATCH" if result.status == "NO_MATCH" else
                    "ATOMIC_SCENARIO_ALIGNMENT_PENDING"
                ),
            }
            if result.template is not None:
                contexts = []
                for item in result.template.required_scenarios:
                    contexts.append({
                        "label": item.label, "obj_type": item.obj_type,
                        "obj_position": item.obj_position,
                        "obj_distance_m": item.obj_distance_m,
                        "obj_v_kph": item.obj_v_kph,
                        "collision_type": item.collision_type,
                        "source": "METHOD_TEMPLATE",
                        "template_id": result.template.template_id,
                        "source_ref": {
                            "asset": item.source_ref.workbook,
                            "location": item.source_ref.range,
                            "hash": item.source_ref.source_hash,
                        },
                    })
                    scenario_fact_counts["object_context"] += 1
                    scenario_fact_counts["geometry_context"] += 1
                    scenario_fact_counts["distance_or_object_speed_context"] += 1
                row["template_context"] = contexts
            match_rows.append(row)

        assets = dict(self.method.metadata.get("asset_hashes", {}))
        matrix = [self._asset_row(asset, str(digest)) for asset, digest in sorted(assets.items())]
        by_asset = {item["asset"]: item for item in matrix}

        def set_row(asset: str, **updates: Any) -> None:
            if asset in by_asset:
                by_asset[asset].update(updates)

        set_row("raw/vda702_atoms.yaml", section="atoms", confirmed_role="DETERMINISTIC_METHOD_RULE",
                original_fusa_consumer="Step 3a atom selection / exposure atoms", originally_executed=True,
                compiled_to_contract=True, runtime_consumer="MethodAtomResolver / StructuredRiskMethod",
                used_count=len(self.method.metadata.get("scenario_atom_catalog", [])), unused_reason="")
        set_row("raw/e_dimension_rules.yaml", section="exposure rules", confirmed_role="NUMERIC_AUTHORITY",
                original_fusa_consumer="domain E resolver", originally_executed=True,
                compiled_to_contract=False, runtime_consumer="none",
                used_count=0, unused_reason="STRUCTURED_RISK_METHOD_USES_VDA_ATOMS_IN_CURRENT_BASELINE")
        set_row("raw/component_taxonomy.yaml", section="taxonomy", confirmed_role="TAXONOMY",
                original_fusa_consumer="FailureMode schema coercion", originally_executed=True,
                compiled_to_contract=True, runtime_consumer="FailureModeSelectorResolver",
                used_count=component_resolution_counts["RESOLVED_EXACT"] + component_resolution_counts["RESOLVED_ALIAS"],
                unused_reason="")
        set_row("raw/failure_type_taxonomy.yaml", section="taxonomy", confirmed_role="TAXONOMY",
                original_fusa_consumer="FailureMode schema coercion", originally_executed=True,
                compiled_to_contract=True, runtime_consumer="FailureModeSelectorResolver",
                used_count=failure_type_resolution_counts["RESOLVED_EXACT"] + failure_type_resolution_counts["RESOLVED_ALIAS"],
                unused_reason="")
        set_row("raw/fm_layer_classification.yaml", section="classification", confirmed_role="FM_LAYER_CLASSIFICATION",
                original_fusa_consumer="Step 2 resolve_scope_layers", originally_executed=True,
                compiled_to_contract=False, runtime_consumer="none", used_count=0,
                unused_reason="OUT_OF_SCOPE: layer/chain semantics are not promoted in C9E")
        set_row("normalized/fm_template_selector_map.yaml", section="selector_adapter",
                confirmed_role="FM_TEMPLATE_SELECTOR_ADAPTER",
                original_fusa_consumer="none; C9F governed vocabulary reconciliation",
                originally_executed=False, compiled_to_contract=True,
                runtime_consumer="FMTemplateSelectorAdapterResolver",
                used_count=sum(1 for item in adapter_mappings if item.runtime_status == "ACTIVE"),
                unused_reason="")
        set_row("raw/atom_spec.yaml", section="specs", confirmed_role="DETERMINISTIC_METHOD_RULE",
                original_fusa_consumer="Step 3b feasible range support", originally_executed=True,
                compiled_to_contract=True, runtime_consumer="MethodAtomResolver", used_count=len(self.method.metadata.get("scenario_atom_catalog", [])), unused_reason="")
        set_row("raw/dimension_structure.yaml", section="dimension_dependency/strong_coupling", confirmed_role="DETERMINISTIC_METHOD_RULE",
                original_fusa_consumer="Scenario structure", originally_executed=True,
                compiled_to_contract=True, runtime_consumer="MethodScenarioCandidateService", used_count=len(self.method.scenario_model.dimensions), unused_reason="")
        set_row("raw/fm_scenario_templates.yaml", section="templates", confirmed_role="SCENARIO_TEMPLATE_CONSTRAINT",
                original_fusa_consumer="match_fm_template / Step 3b template injection", originally_executed=True,
                compiled_to_contract=True, runtime_consumer="ScenarioMethodService (review audit)",
                used_count=status_counts["STRONG_MATCH"], unused_reason="")
        set_row("raw/domain_rules/avp_low_speed.yaml", section="triggering_state_mapping", confirmed_role="DOMAIN_RULE",
                original_fusa_consumer="DomainPlugin.infer_collision_type", originally_executed=True,
                compiled_to_contract=True, runtime_consumer="ScenarioMethodContract (not score-active)",
                used_count=len(self.method.scenario_model.scenario_method.domain_knowledge.triggering_state_mappings), unused_reason="")
        domain_hash = str(assets.get("raw/domain_rules/avp_low_speed.yaml", ""))
        matrix.extend([
            {
                **self._asset_row("raw/domain_rules/avp_low_speed.yaml", domain_hash),
                "section": "standard_kinematic_values", "confirmed_role": "DOMAIN_DEFAULT",
                "original_fusa_consumer": "DomainPlugin standard speed accessors", "originally_executed": True,
                "compiled_to_contract": True, "runtime_consumer": "ScenarioMethodContract (not actual Scenario facts)",
                "used_count": 1, "unused_reason": "",
            },
            {
                **self._asset_row("raw/domain_rules/avp_low_speed.yaml", domain_hash),
                "section": "fallback_scenario_dimensions", "confirmed_role": "FALLBACK_KNOWLEDGE",
                "original_fusa_consumer": "DomainPlugin accessor only", "originally_executed": False,
                "compiled_to_contract": True, "runtime_consumer": "ScenarioMethodService audit only",
                "used_count": len(self.scenario_method.fallback_terms()),
                "unused_reason": "TARGET_DIMENSION_PENDING; no explicit adapter exists",
            },
            {
                **self._asset_row("raw/domain_rules/avp_low_speed.yaml", domain_hash),
                "section": "exposure/controllability/severity", "confirmed_role": "NUMERIC_AUTHORITY",
                "original_fusa_consumer": "DomainPlugin numeric S/E/C accessors", "originally_executed": True,
                "compiled_to_contract": True, "runtime_consumer": "none in this stage",
                "used_count": 0, "unused_reason": "COMPILED_NOT_SCORE_ACTIVE_IN_THIS_STAGE",
            },
        ])
        for asset, consumer in (
            ("raw/coupling_examples.yaml", "LLM prompt examples"),
            ("raw/infeasible_examples.yaml", "LLM few-shot examples"),
        ):
            set_row(asset, section="examples", confirmed_role="LLM_EXAMPLE_ONLY",
                    original_fusa_consumer=consumer, originally_executed=False,
                    compiled_to_contract=True, runtime_consumer="none", used_count=0,
                    unused_reason="EXAMPLE_ONLY_NOT_PROMOTED_TO_DETERMINISTIC_RUNTIME_RULE")

        gaps = gap_payload.get("gaps", []) if isinstance(gap_payload, dict) else []
        taxonomy_payload = {
            "component_taxonomy": {
                "compiled": taxonomy is not None,
                "canonical_values": len(taxonomy.component_categories) if taxonomy else 0,
                "resolution_status_counts": dict(sorted(component_resolution_counts.items())),
                "resolved": sum(component_resolution_counts[item] for item in ("RESOLVED_EXACT", "RESOLVED_ALIAS")),
                "missing": component_resolution_counts["PENDING_MISSING_SOURCE_VALUE"],
                "invalid": component_resolution_counts["INVALID_TAXONOMY_VALUE"],
            },
            "failure_type_taxonomy": {
                "compiled": taxonomy is not None,
                "canonical_values": len(taxonomy.failure_types) if taxonomy else 0,
                "resolution_status_counts": dict(sorted(failure_type_resolution_counts.items())),
                "resolved": sum(failure_type_resolution_counts[item] for item in ("RESOLVED_EXACT", "RESOLVED_ALIAS")),
                "missing": failure_type_resolution_counts["PENDING_MISSING_SOURCE_VALUE"],
                "invalid": failure_type_resolution_counts["INVALID_TAXONOMY_VALUE"],
            },
        }
        selector_vocabulary = {
            "template_count": len(templates),
            "templates_declaring_keywords": sum(bool(item.match.keywords) for item in templates),
            "templates_declaring_component_category": sum(
                bool(item.match.component_categories) for item in templates
            ),
            "templates_declaring_failure_type": sum(
                bool(item.match.failure_types) for item in templates
            ),
            "keywords": sorted({value for template in templates for value in template.match.keywords}),
            "component_categories": sorted(component_selector_vocabulary),
            "failure_types": sorted(failure_type_selector_vocabulary),
            "malfunction_coverage": {
                "raw_component_category": raw_component_count,
                "canonical_component_category": canonical_component_count,
                "component_category_in_template_vocabulary": component_selector_coverage,
                "raw_failure_type": raw_failure_type_count,
                "canonical_failure_type": canonical_failure_type_count,
                "failure_type_in_template_vocabulary": failure_type_selector_coverage,
            },
            "contract_reconciliation": {
                "component_taxonomy": reconciliation("COMPONENT_CATEGORY"),
                "failure_type_taxonomy": reconciliation("FAILURE_TYPE"),
                "adapter_mapping_count": len(adapter_mappings),
            },
        }
        return {
            "artifact_version": "confirmed-yaml-utilization-v3",
            "method_contract_hash": str(self.method.metadata.get("template_hash", "")),
            "runtime_behavior_changed": False,
            "llm_calls_added": 0,
            "matrix": matrix,
            "fm_selector_taxonomy": taxonomy_payload,
            "fm_template_selector_vocabulary": selector_vocabulary,
            "fm_template_matching": {
                "total_malfunctions": len(match_rows),
                "status_counts": dict(sorted(status_counts.items())),
                "candidate_matches": sum(
                    status_counts[item] for item in ("STRONG_MATCH", "WEAK_MATCH", "AMBIGUOUS")
                ),
                "strong_matches": status_counts["STRONG_MATCH"],
                "weak_matches": status_counts["WEAK_MATCH"],
                "ambiguous": status_counts["AMBIGUOUS"],
                "no_match": status_counts["NO_MATCH"],
                "matches": match_rows,
                "context_counts": dict(sorted(scenario_fact_counts.items())),
            },
            "fallback_dimensions": list(self.scenario_method.fallback_terms()),
            "scenario_gap_reaudit": {
                "before_gap_count": len(gaps),
                "after_gap_count": len(gaps),
                "reason": "Template context is review-only in this stage; atom/dimension bindings remain unchanged.",
                "pending_dimensions": sorted({
                    str(item.get("dimension", "")) for item in gaps if isinstance(item, dict)
                }),
            },
            "numeric_sections_not_score_active": ["exposure", "controllability", "severity"],
            "source_conflicts": [],
        }

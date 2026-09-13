"""Governed, bounded Scenario atom synthesis over a compiled MethodContract.

The service deliberately excludes Exposure ratings from semantic ranking.  It
also never widens an empty ODD/semantic match back to the full atom catalog.
Legacy FUSA used both behaviours; neither is method authority in V13.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re
from typing import Any, Iterable

from hara_agent.contracts import (
    AnalyticalScenarioInstantiation, CandidateOrigin, CoverageLabel,
    MethodContract, ScenarioAtomCandidateSet, ScenarioBindingAuthority,
    ScenarioDimensionApplicability,
    ScenarioDimensionCandidate, ScenarioSynthesisAssessment,
    ScenarioSynthesisInput, ScenarioSynthesisStatus,
    SynthesisValidationStatus,
)
from hara_agent.models import (
    FactProvenance, MalfunctionCandidate, ReviewStatus, ScenarioCandidate, SourceRef,
)

from .scenario_constraint_service import ScenarioConstraintExecutor, ScenarioConstraintStatus
from .scenario_method_service import ScenarioMethodService
from .scenario_selection_quality import (
    normalize_object_category,
    ScenarioBindingPolicy, ScenarioCandidateRanker, ScenarioCoveragePlanner,
    ScenarioDimensionApplicabilityService, ScenarioSemanticQueryBuilder,
    ScenarioVariantDiversityValidator,
)


class ScenarioSynthesisValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ConstrainedScenarioSynthesisService:
    """Create bounded candidate sets and materialize validated analytical children."""

    candidate_cap_per_dimension = 12

    def __init__(self, method: MethodContract):
        self.method = method
        raw = method.metadata.get("scenario_atom_catalog", [])
        self.catalog = tuple(item for item in raw if isinstance(item, dict))
        self.by_id = {
            str(item.get("atom_id", "")): item
            for item in self.catalog if str(item.get("atom_id", ""))
        }
        self.dimensions = tuple(
            item.canonical_name for item in method.scenario_model.dimensions
        )
        self.constraint_executor = ScenarioConstraintExecutor()
        self.binding_policy = ScenarioBindingPolicy(self.by_id)
        self.applicability_service = ScenarioDimensionApplicabilityService()
        self.coverage_planner = ScenarioCoveragePlanner()
        self.ranker = ScenarioCandidateRanker()
        self.diversity_validator = ScenarioVariantDiversityValidator()
        self.scenario_method = ScenarioMethodService(method)
        if not self.dimensions or not self.catalog:
            raise ValueError("Scenario synthesis requires a compiled atom ontology")

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _speed_envelope(parent: ScenarioCandidate) -> tuple[float | None, float | None]:
        raw = parent.facts.get("ego_speed_constraint", {})
        if not isinstance(raw, dict):
            return None, None
        lower = raw.get("min_kph", raw.get("speed_min_kph"))
        upper = raw.get("max_kph", raw.get("speed_max_kph"))
        return (
            float(lower) if isinstance(lower, (int, float)) and not isinstance(lower, bool) else None,
            float(upper) if isinstance(upper, (int, float)) and not isinstance(upper, bool) else None,
        )

    @staticmethod
    def _speed_range(atom: dict[str, Any]) -> tuple[float | None, float | None] | None:
        raw = atom.get("speed_range_kph")
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None
        return (
            float(raw[0]) if raw[0] is not None else None,
            float(raw[1]) if raw[1] is not None else None,
        )

    @classmethod
    def _speed_compatible(
        cls, atom: dict[str, Any], envelope: tuple[float | None, float | None],
    ) -> bool:
        atom_range = cls._speed_range(atom)
        if atom_range is None:
            return True
        lower_values = [item for item in (envelope[0], atom_range[0]) if item is not None]
        upper_values = [item for item in (envelope[1], atom_range[1]) if item is not None]
        lower = max(lower_values) if lower_values else None
        upper = min(upper_values) if upper_values else None
        return lower is None or upper is None or lower <= upper

    @staticmethod
    def _range_intersection(
        left: tuple[float | None, float | None] | None,
        right: tuple[float | None, float | None] | None,
    ) -> tuple[float | None, float | None] | None:
        if left is None or right is None:
            return None
        lowers = [value for value in (left[0], right[0]) if value is not None]
        uppers = [value for value in (left[1], right[1]) if value is not None]
        lower = max(lowers) if lowers else None
        upper = min(uppers) if uppers else None
        if lower is not None and upper is not None and lower > upper:
            return None
        return lower, upper

    @staticmethod
    def _project_slope_max(project_context: dict[str, Any]) -> float | None:
        explicit = project_context.get("slope_max_pct")
        if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
            return float(explicit)
        text = json.dumps(project_context, ensure_ascii=False, sort_keys=True)
        matches = re.findall(
            r"(?:不超过|不高于|最大(?:坡度)?|至多|≤|<=)\s*(\d+(?:\.\d+)?)\s*%",
            text, re.IGNORECASE,
        )
        directional = re.findall(
            r"(?:上坡|下坡)\s*(\d+(?:\.\d+)?)\s*%",
            text, re.IGNORECASE,
        )
        bounds = [*map(float, matches)]
        if directional:
            bounds.append(max(map(float, directional)))
        return min(bounds) if bounds else None

    @classmethod
    def _slope_compatible(
        cls, atom: dict[str, Any], project_context: dict[str, Any],
    ) -> bool:
        project_max = cls._project_slope_max(project_context)
        if project_max is None:
            return True
        semantics = atom.get("physical_semantics", {})
        semantics = semantics if isinstance(semantics, dict) else {}
        slope = semantics.get("slope", {})
        slope = slope if isinstance(slope, dict) else {}
        raw_range = slope.get("range_pct")
        lower = raw_range[0] if isinstance(raw_range, list) and len(raw_range) == 2 else slope.get("pct_min")
        if not isinstance(lower, (int, float)) or isinstance(lower, bool):
            return True
        if float(lower) < project_max:
            return True
        if float(lower) > project_max:
            return False
        return bool(slope.get("min_inclusive", True))

    @staticmethod
    def _weather_compatible(
        atom: dict[str, Any], project_context: dict[str, Any],
    ) -> bool:
        label = str(atom.get("label", "")).casefold()
        context = json.dumps(project_context, ensure_ascii=False, sort_keys=True).casefold()
        snow_or_ice = ("snow", "ice", "雪", "冰")
        return not any(term in label for term in snow_or_ice) or any(
            term in context for term in snow_or_ice
        )

    def _candidate(
        self, atom: dict[str, Any], *, origin: CandidateOrigin,
        refs: Iterable[str], reason: str, validated: bool = False,
        binding_authority: str = ScenarioBindingAuthority.ANALYTICAL_SELECTION.value,
        template_relationship: str = "NONE",
        ranking_scores: dict[str, float] | None = None,
    ) -> ScenarioDimensionCandidate:
        atom_id = str(atom.get("atom_id", "")).strip()
        canonical = str(atom.get("v2") or atom.get("v2_proper") or atom_id).strip()
        return ScenarioDimensionCandidate(
            atom_id=atom_id,
            canonical_atom_id=canonical,
            dimensions=tuple(str(item) for item in atom.get("filled_dimensions", [])),
            label=str(atom.get("label", "")),
            source_asset=str(atom.get("source_asset", "")),
            source_rule=str(atom.get("source_rule", "")),
            source_tag=str(atom.get("source_tag", "")),
            candidate_origin=origin,
            supporting_context_refs=tuple(dict.fromkeys(str(item) for item in refs if item)),
            selection_reason=reason,
            method_semantics=deepcopy(atom.get("physical_semantics", {})),
            validation_status=(
                SynthesisValidationStatus.VALIDATED
                if validated else SynthesisValidationStatus.PENDING
            ),
            speed_range_kph=self._speed_range(atom),
            binding_authority=binding_authority,
            template_relationship=template_relationship,
            ranking_scores=dict(ranking_scores or {}),
        )

    @staticmethod
    def _binding_atom_id(binding: dict[str, Any]) -> str:
        value = str(binding.get("atom_id") or binding.get("canonical_atom_id") or "").strip()
        return value.split("|", 1)[0].strip()

    @staticmethod
    def _source_ref_payload(source: Any) -> dict[str, str]:
        return {
            "source_hash": str(getattr(source, "source_hash", "")),
            "source_asset": str(getattr(source, "workbook", "")),
            "source_rule": (
                f"{getattr(source, 'sheet', '')}!{getattr(source, 'range', '')}"
            ).strip("!"),
        }

    def _fm_template_evidence(
        self, malfunction: dict[str, Any], parent: ScenarioCandidate,
    ) -> dict[str, Any]:
        catalog = self.method.scenario_model.scenario_method.fm_template_catalog
        if catalog is None:
            return {}
        instance = parent.analysis_instance if isinstance(parent.analysis_instance, dict) else {}
        template_id = str(instance.get("source_template_id", "")).strip()
        template = next(
            (item for item in catalog.templates if item.template_id == template_id),
            None,
        )
        qualification = "PARENT_SOURCE_TEMPLATE" if template is not None else ""
        matched_by: tuple[str, ...] = ()
        matched_terms: tuple[str, ...] = ()
        if template is None:
            causal_chain = malfunction.get("causal_chain", [])
            typed = MalfunctionCandidate(
                malfunction_id=str(malfunction.get("malfunction_id", "")),
                function_id=str(malfunction.get("function_id", "")),
                guideword=str(malfunction.get("guideword", "")),
                description=str(malfunction.get("description", "")),
                functional_effect=str(malfunction.get("functional_effect", "")),
                vehicle_level_hazard=str(malfunction.get("vehicle_level_hazard", "")),
                causal_chain=(
                    list(causal_chain) if isinstance(causal_chain, list) and len(causal_chain) >= 2
                    else ["MALFUNCTION", "VEHICLE_LEVEL_HAZARD"]
                ),
                component_category=str(malfunction.get("component_category", "")),
                failure_type=str(malfunction.get("failure_type", "")),
            )
            match = self.scenario_method.match_fm_template(typed)
            if not match.injectable:
                return {}
            template = match.template
            qualification = match.qualification_tier
            matched_by = match.matched_by
            matched_terms = match.matched_terms
        assert template is not None

        constraints = []
        for index, option in enumerate(template.required_scenarios, start=1):
            constraints.append({
                "source_option_id": f"{template.template_id}:OPTION:{index}",
                "label": option.label,
                "obj_type": option.obj_type,
                "obj_position": option.obj_position,
                "obj_distance_m": option.obj_distance_m,
                "obj_v_kph": option.obj_v_kph,
                "collision_type": option.collision_type,
                "source": self._source_ref_payload(option.source_ref),
            })
        active_option_id = str(instance.get("source_option_id", "")).strip()
        active_option = next(
            (item for item in constraints if item["source_option_id"] == active_option_id),
            {},
        )
        return {
            "template_id": template.template_id,
            "source": self._source_ref_payload(template.source_ref),
            "source_role": template.source_role,
            "qualification": qualification,
            "matched_by": list(matched_by),
            "matched_terms": list(matched_terms),
            "applicable_semantics": {
                "keywords": list(template.match.keywords),
                "component_categories": list(template.match.component_categories),
                "failure_types": list(template.match.failure_types),
                "matching_semantics": template.match.matching_semantics,
            },
            "dimension_requirements_preferences": {
                "source_fields_by_dimension": {
                    "OBJECT": ["obj_type", "obj_position"],
                    "EGO_ACTION": ["collision_type"],
                },
                "derivation_basis": "FMTemplateScenario source fields; no new Method atom IDs",
            },
            "supported_interaction_categories": sorted({
                str(item.collision_type) for item in template.required_scenarios
            }),
            "source_governed_constraints": constraints,
            "active_option": active_option,
        }

    def hard_filter_decision(
        self, *, dimension: str, atom: dict[str, Any], parent: ScenarioCandidate,
        project_context: dict[str, Any], query: dict[str, Any],
        applicability: Any, decision: Any, exact_locks: dict[str, str],
        fm_template: dict[str, Any],
    ) -> tuple[bool, str]:
        atom_id = str(atom.get("atom_id", ""))
        if dimension not in atom.get("filled_dimensions", []):
            return False, "DIMENSION_NOT_FILLED"
        if applicability.status is ScenarioDimensionApplicability.NOT_APPLICABLE:
            return False, "DIMENSION_NOT_APPLICABLE"
        if not self._speed_compatible(atom, self._speed_envelope(parent)):
            return False, "ODD_SPEED_INCOMPATIBLE"
        if dimension in {"ROAD", "EGO_X_ROAD"} and not self._slope_compatible(
            atom, project_context,
        ):
            return False, "ODD_SLOPE_INCOMPATIBLE"
        if dimension == "ROAD" and not self._weather_compatible(atom, project_context):
            return False, "ODD_WEATHER_INCOMPATIBLE"
        if any(
            other in exact_locks and exact_locks[other] != atom_id
            for other in atom.get("filled_dimensions", [])
            if other != dimension
        ):
            return False, "COMPOUND_EXACT_LOCK_CONFLICT"

        categories = self.ranker.atom_categories(atom)
        actions = set(map(str, query.get("action_categories", [])))
        objects = set(map(str, query.get("object_categories", [])))
        traffic = set(map(str, query.get("traffic_relations", [])))
        road = set(map(str, query.get("road_relations", [])))
        locations = set(map(str, query.get("location_categories", [])))
        label_tokens = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", str(atom.get("label", "")).casefold()))
        context_tokens = set(map(str, query.get("query_tokens", [])))

        if decision.parent_atom_id == atom_id:
            return True, "PASS_PARENT_BINDING"
        if dimension == "TRAFFIC_PATTERN":
            return (
                (True, "PASS_TRAFFIC_RELATION_MATCH")
                if categories & traffic else
                (False, "SEMANTIC_TRAFFIC_RELATION_MISMATCH")
            )
        if dimension == "EGO_X_ROAD":
            return (
                (True, "PASS_ROAD_RELATION_MATCH")
                if categories & road else
                (False, "SEMANTIC_ROAD_RELATION_MISMATCH")
            )
        if dimension == "OBJECT" and objects:
            if not categories & objects:
                return False, "SEMANTIC_OBJECT_CATEGORY_MISMATCH"
            active = fm_template.get("active_option", {})
            if isinstance(active, dict) and active:
                expected_category = normalize_object_category(
                    active.get("obj_type", "")
                )
                if expected_category and expected_category not in categories:
                    return False, "FM_TEMPLATE_OBJECT_MISMATCH"
            return True, "PASS_OBJECT_CATEGORY_MATCH"
        if dimension == "EGO_ACTION" and (actions or traffic):
            return (
                (True, "PASS_ACTION_OR_INTERACTION_MATCH")
                if categories & (actions | traffic) else
                (False, "SEMANTIC_ACTION_CATEGORY_MISMATCH")
            )
        if dimension == "EGO_DYNAMICS" and actions:
            return (
                (True, "PASS_DYNAMICS_ACTION_MATCH")
                if categories & actions else
                (False, "SEMANTIC_DYNAMICS_ACTION_MISMATCH")
            )
        if dimension == "WHERE":
            return (
                (True, "PASS_LOCATION_CATEGORY_MATCH")
                if categories & locations else
                (False, "SEMANTIC_LOCATION_CATEGORY_MISMATCH")
            )
        if dimension == "ROAD" and road:
            return (
                (True, "PASS_ROAD_CATEGORY_MATCH")
                if categories & road else
                (False, "SEMANTIC_ROAD_CATEGORY_MISMATCH")
            )
        if dimension == "EGO_DYNAMICS" and decision.authority is ScenarioBindingAuthority.RANGE_CONTAINMENT:
            return (
                (True, "PASS_SPEED_REFINEMENT")
                if self._speed_range(atom) else
                (False, "SEMANTIC_DYNAMICS_NO_SPEED_REFINEMENT")
            )
        if label_tokens & context_tokens:
            return True, "PASS_LEXICAL_CONTEXT_MATCH"
        if dimension == "ROAD":
            return True, "PASS_ODD_ROAD_CATALOG"
        return False, "SEMANTIC_LEXICAL_CONTEXT_MISMATCH"

    def _traffic_representation_elsewhere(
        self, query: dict[str, Any], parent: ScenarioCandidate,
    ) -> dict[str, list[dict[str, Any]]]:
        relations = set(map(str, query.get("traffic_relations", [])))
        result: dict[str, list[dict[str, Any]]] = {}
        if not relations:
            return result
        envelope = self._speed_envelope(parent)
        for atom in self.catalog:
            dimensions = tuple(map(str, atom.get("filled_dimensions", [])))
            if "TRAFFIC_PATTERN" in dimensions or not dimensions:
                continue
            if not self._speed_compatible(atom, envelope):
                continue
            categories = self.ranker.atom_categories(atom)
            for relation in sorted(relations & categories):
                result.setdefault(relation, []).append({
                    "atom_id": str(atom.get("atom_id", "")),
                    "canonical_atom_id": str(
                        atom.get("v2") or atom.get("v2_proper")
                        or atom.get("atom_id", "")
                    ),
                    "label": str(atom.get("label", "")),
                    "filled_dimensions": list(dimensions),
                    "source_asset": str(atom.get("source_asset", "")),
                    "source_rule": str(atom.get("source_rule", "")),
                })
        return result

    def candidate_sets(
        self, *, malfunction: dict[str, Any], parent: ScenarioCandidate,
        assessment: dict[str, Any], project_context: dict[str, Any],
        query: dict[str, Any], fm_template: dict[str, Any],
    ) -> tuple[ScenarioAtomCandidateSet, ...]:
        envelope = self._speed_envelope(parent)
        decisions = {
            dimension: self.binding_policy.decide(parent, dimension, envelope)
            for dimension in self.dimensions
        }
        applicability = {
            dimension: self.applicability_service.assess(
                dimension, query, decisions[dimension],
            )
            for dimension in self.dimensions
        }
        exact_locks = {
            dimension: decision.parent_atom_id
            for dimension, decision in decisions.items()
            if decision.parent_atom_id and not decision.refinable
        }
        bindings = parent.facts.get("method_scenario_dimensions", {})
        result = []
        for dimension in self.dimensions:
            catalog = [
                item for item in self.catalog
                if dimension in item.get("filled_dimensions", [])
            ]
            before = str(
                (bindings.get(dimension, {}) if isinstance(bindings, dict) else {}).get(
                    "resolution_status", "PENDING"
                )
            )
            decision = decisions[dimension]
            applicable = applicability[dimension]
            if dimension in exact_locks:
                atom = self.by_id[exact_locks[dimension]]
                locked = self._candidate(
                    atom, origin=CandidateOrigin.DIRECT_PROJECT_BINDING,
                    refs=decision.source_refs,
                    reason=decision.basis, validated=True,
                    binding_authority=decision.authority.value,
                    ranking_scores={
                        "template_score": 0.0, "mechanism_score": 0.0,
                        "action_score": 0.0, "object_score": 0.0,
                        "traffic_relation_score": 0.0, "odd_score": 0.0,
                        "causal_score": 0.0, "lexical_score": 0.0,
                        "category_context_score": 0.0,
                        "structured_source_score": 0.0,
                        "source_evidence_tier": 5.0,
                        "final_rank_score": 0.0,
                    },
                )
                result.append(ScenarioAtomCandidateSet(
                    dimension=dimension, catalog_size=len(catalog),
                    hard_filtered_pool_size=1, candidates=(locked,),
                    applicability=applicable, binding_decision=decision,
                    locked_atom_ids=(locked.atom_id,),
                    resolution_status_before=before,
                    generation_status="LOCKED_EXACT_AUTHORITY",
                    reason=decision.basis,
                ))
                continue

            hard_pool = []
            for atom in catalog:
                passed, _ = self.hard_filter_decision(
                    dimension=dimension, atom=atom, parent=parent,
                    project_context=project_context, query=query,
                    applicability=applicable, decision=decision,
                    exact_locks=exact_locks, fm_template=fm_template,
                )
                if passed:
                    hard_pool.append(atom)
            corpus = [str(atom.get("label", "")) for atom in hard_pool]
            ranked = []
            for atom in hard_pool:
                scores, rank_reason = self.ranker.score(
                    dimension=dimension, atom=atom, query=query,
                    fm_template=fm_template, corpus_labels=corpus, odd_passed=True,
                )
                relationship = (
                    "SOURCE_TEMPLATE_COMPATIBLE"
                    if scores["template_score"] > 0 else "NONE"
                )
                atom_id = str(atom.get("atom_id", ""))
                origin = (
                    CandidateOrigin.DIRECT_PROJECT_BINDING
                    if atom_id == decision.parent_atom_id
                    else CandidateOrigin.METHOD_TEMPLATE
                    if relationship != "NONE"
                    else CandidateOrigin.BINDING_REFINEMENT
                    if decision.parent_atom_id and decision.refinable
                    else CandidateOrigin.ODD_CONSTRAINED_CATALOG
                    if dimension in {"WHERE", "ROAD", "EGO_X_ROAD", "EGO_DYNAMICS"}
                    else CandidateOrigin.HAZARD_CAUSAL_SEMANTIC_CANDIDATE
                )
                refs = [
                    "PROJECT.ODD", "MF.description", "MF.functional_effect",
                    "MF.vehicle_level_hazard", "HE.hazardous_event",
                    "CAUSAL.summary", "PARENT.scenario",
                    "METHOD.scenario_atom_catalog",
                ]
                if relationship != "NONE":
                    refs.append("METHOD.fm_scenario_template")
                candidate = self._candidate(
                    atom, origin=origin, refs=refs,
                    reason=(
                        f"{rank_reason}; {decision.basis}; hard Method/ODD/"
                        "compound/applicability filters passed"
                    ),
                    binding_authority=(
                        decision.authority.value
                        if atom_id == decision.parent_atom_id
                        else ScenarioBindingAuthority.ANALYTICAL_SELECTION.value
                    ),
                    template_relationship=relationship,
                    ranking_scores=scores,
                )
                ranked.append(candidate)
            ranked.sort(key=lambda item: self.ranker.rank_key(
                item.atom_id, item.ranking_scores,
            ))
            candidates = tuple(ranked[:self.candidate_cap_per_dimension])
            if applicable.status is ScenarioDimensionApplicability.NOT_APPLICABLE:
                status = "NOT_APPLICABLE"
            elif not candidates and applicable.status is ScenarioDimensionApplicability.REQUIRED:
                status = "METHOD_GAP"
            elif not candidates:
                status = "OPTIONAL_NO_SUPPORTED_ATOM"
            else:
                status = "CANDIDATES_AVAILABLE"
            result.append(ScenarioAtomCandidateSet(
                dimension=dimension, catalog_size=len(catalog),
                hard_filtered_pool_size=len(hard_pool), candidates=candidates,
                applicability=applicable, binding_decision=decision,
                resolution_status_before=before, generation_status=status,
                reason=(
                    "Required semantic relation has no compatible Method atom."
                    if status == "METHOD_GAP" else
                    applicable.reason if status == "NOT_APPLICABLE" else
                    "Candidates passed hard Method/ODD/compound/applicability filters "
                    "and were ranked by structured features plus BM25."
                ),
                shortlist_truncated=len(ranked) > len(candidates),
            ))

        present = {
            item.dimension: {candidate.atom_id for candidate in item.candidates}
            for item in result
        }
        incomplete_compounds = {
            candidate.atom_id
            for item in result for candidate in item.candidates
            if len({dim for dim in candidate.dimensions if dim in self.dimensions}) > 1
            and any(
                candidate.atom_id not in present.get(dim, set())
                for dim in candidate.dimensions if dim in self.dimensions
            )
        }
        if incomplete_compounds:
            updated = []
            for item in result:
                candidates = tuple(
                    candidate for candidate in item.candidates
                    if candidate.atom_id not in incomplete_compounds
                )
                status = item.generation_status
                if not candidates and item.applicability.status is ScenarioDimensionApplicability.REQUIRED:
                    status = "METHOD_GAP"
                elif not candidates and item.applicability.status is ScenarioDimensionApplicability.OPTIONAL:
                    status = "OPTIONAL_NO_SUPPORTED_ATOM"
                updated.append(replace(
                    item, candidates=candidates, generation_status=status,
                    reason=item.reason + " Incomplete compound candidates were removed.",
                ))
            result = updated
        return tuple(result)

    def build_input(
        self, *, malfunction: dict[str, Any], parent: ScenarioCandidate,
        assessment: dict[str, Any], project_context: dict[str, Any],
    ) -> ScenarioSynthesisInput:
        fm_template = self._fm_template_evidence(malfunction, parent)
        query = ScenarioSemanticQueryBuilder.build(
            malfunction=malfunction, parent=parent, assessment=assessment,
            project_context=project_context, fm_template=fm_template,
        )
        query["traffic_relations_represented_elsewhere"] = (
            self._traffic_representation_elsewhere(query, parent)
        )
        candidate_sets = self.candidate_sets(
            malfunction=malfunction, parent=parent, assessment=assessment,
            project_context=project_context, query=query,
            fm_template=fm_template,
        )
        coverage_plan = self.coverage_planner.plan(
            query=query, candidate_sets=candidate_sets,
        )
        hazard_id = str(assessment.get("hazardous_event_id", "")).strip()
        if not hazard_id:
            causal = assessment.get("causal_assessment", {})
            chain = causal.get("causal_chain", []) if isinstance(causal, dict) else []
            hazard_node = str(chain[-1]) if chain else "HAZARD"
            hazard_id = f"HE::{malfunction.get('malfunction_id','')}::{parent.scenario_id}::{hazard_node}"
        signature = {
            "method_hash": self.method.metadata.get("method_source_hash", ""),
            "malfunction": {
                key: malfunction.get(key, "") for key in (
                    "function_id", "guideword", "description", "functional_effect",
                    "vehicle_level_hazard", "component_category", "failure_type",
                )
            },
            "hazard": {
                "hazardous_event": assessment.get("hazardous_event", ""),
                "causal_status": assessment.get("causal_assessment", {}).get("status", "")
                if isinstance(assessment.get("causal_assessment"), dict) else "",
            },
            "parent": {
                "scenario_id": parent.scenario_id,
                "operating_mode": parent.operating_mode,
                "semantic_fingerprint": parent.semantic_fingerprint,
                "object_type": parent.facts.get("object_type", ""),
                "collision_type": parent.facts.get("collision_type", ""),
            },
            "candidate_sets": {
                item.dimension: [candidate.atom_id for candidate in item.candidates]
                for item in candidate_sets
            },
            "applicability": {
                item.dimension: item.applicability.status.value
                for item in candidate_sets
            },
            "coverage_plan": coverage_plan.to_dict(),
            "fm_template_id": fm_template.get("template_id", ""),
        }
        group_id = "SYNTH-" + hashlib.sha256(
            self._canonical_json(signature).encode("utf-8")
        ).hexdigest()[:20].upper()
        return ScenarioSynthesisInput(
            malfunction_id=str(malfunction.get("malfunction_id", "")),
            parent_scenario_id=parent.scenario_id,
            hazardous_event_id=hazard_id,
            function_id=str(malfunction.get("function_id", "")),
            malfunction=dict(malfunction), parent_scenario=parent.to_dict(),
            causal_assessment=dict(assessment.get("causal_assessment", {})),
            project_context=dict(project_context),
            method_contract_hash=str(self.method.metadata.get("method_source_hash", "")),
            dimension_candidate_sets=candidate_sets,
            semantic_group_id=group_id,
            structured_semantic_query=query,
            coverage_plan=coverage_plan,
            fm_scenario_template=fm_template,
        )

    def _selection_reasons(
        self, synthesis_input: ScenarioSynthesisInput,
        selected: dict[str, tuple[str, ...]], *, partial: bool = False,
    ) -> list[str]:
        candidate_sets = {item.dimension: item for item in synthesis_input.dimension_candidate_sets}
        reasons: list[str] = []
        expected = set(self.dimensions)
        if not partial and set(selected) != expected:
            reasons.append("DIMENSION_KEY_SET_MISMATCH")
        for dimension, atom_ids in selected.items():
            candidate_set = candidate_sets.get(dimension)
            if candidate_set is None:
                reasons.append(f"UNKNOWN_DIMENSION:{dimension}")
                continue
            allowed = {item.atom_id for item in candidate_set.candidates}
            if len(atom_ids) != len(set(atom_ids)) or len(atom_ids) > 1:
                reasons.append(f"INVALID_ATOM_CARDINALITY:{dimension}")
            if candidate_set.locked_atom_ids and tuple(atom_ids) != candidate_set.locked_atom_ids:
                reasons.append(f"LOCKED_BINDING_CHANGED:{dimension}")
            for atom_id in atom_ids:
                if atom_id not in allowed:
                    atom = self.by_id.get(atom_id)
                    if atom is None:
                        reasons.append(f"INVENTED_ATOM_ID:{dimension}:{atom_id}")
                    elif dimension not in atom.get("filled_dimensions", []):
                        reasons.append(f"WRONG_DIMENSION:{dimension}:{atom_id}")
                    else:
                        reasons.append(f"ATOM_OUTSIDE_CANDIDATE_SET:{dimension}:{atom_id}")
                    continue
                atom = self.by_id.get(atom_id)
                if atom is None:
                    reasons.append(f"UNKNOWN_ATOM:{atom_id}")
                elif dimension not in atom.get("filled_dimensions", []):
                    reasons.append(f"WRONG_DIMENSION:{dimension}:{atom_id}")
        if partial:
            return sorted(set(reasons))
        for dimension in self.dimensions:
            if dimension not in selected:
                continue
            candidate_set = candidate_sets[dimension]
            applicability = candidate_set.applicability.status
            if not selected[dimension] and applicability is ScenarioDimensionApplicability.REQUIRED:
                reasons.append(f"REQUIRED_DIMENSION_EMPTY:{dimension}")
            if selected[dimension] and applicability is ScenarioDimensionApplicability.NOT_APPLICABLE:
                reasons.append(f"NOT_APPLICABLE_DIMENSION_SELECTED:{dimension}")
            decision = candidate_set.binding_decision
            if (
                decision.refinable and decision.parent_atom_id
                and selected[dimension]
                and selected[dimension] != (decision.parent_atom_id,)
            ):
                candidate = next(
                    (item for item in candidate_set.candidates if item.atom_id == selected[dimension][0]),
                    None,
                )
                ranking = candidate.ranking_scores if candidate is not None else {}
                if not any(float(ranking.get(name, 0.0)) > 0 for name in (
                    "template_score", "mechanism_score", "action_score", "object_score",
                    "traffic_relation_score", "causal_score", "lexical_score",
                )):
                    reasons.append(f"UNSUPPORTED_BINDING_REFINEMENT:{dimension}")
        selected_by_dimension = {
            dimension: set(atom_ids) for dimension, atom_ids in selected.items()
        }
        selected_union = {
            atom_id for atom_ids in selected_by_dimension.values() for atom_id in atom_ids
        }
        for atom_id in selected_union:
            atom = self.by_id.get(atom_id)
            if atom is None:
                continue
            filled = {
                str(item) for item in atom.get("filled_dimensions", [])
                if str(item) in expected
            }
            missing = sorted(
                dimension for dimension in filled
                if atom_id not in selected_by_dimension.get(dimension, set())
            )
            if missing:
                reasons.append(f"COMPOUND_ATOM_INCOMPLETE:{atom_id}:{','.join(missing)}")
            conflicts = sorted(
                dimension for dimension in filled
                if selected_by_dimension.get(dimension, set()) - {atom_id}
            )
            if conflicts:
                reasons.append(f"COMPOUND_ATOM_CONFLICT:{atom_id}:{','.join(conflicts)}")
        parent = synthesis_input.parent_scenario
        parent_facts = parent.get("facts", {}) if isinstance(parent, dict) else {}
        envelope_raw = parent_facts.get("ego_speed_constraint", {})
        envelope = (
            envelope_raw.get("min_kph", envelope_raw.get("speed_min_kph")),
            envelope_raw.get("max_kph", envelope_raw.get("speed_max_kph")),
        ) if isinstance(envelope_raw, dict) else (None, None)
        for atom_id in selected_union:
            atom = self.by_id.get(atom_id)
            if atom is not None and not self._speed_compatible(atom, envelope):
                reasons.append(f"ODD_SPEED_INCOMPATIBLE:{atom_id}")
        bindings = {
            dimension: {
                "resolution_status": "RESOLVED" if atom_ids else "PENDING",
                "atom_id": atom_ids[0] if atom_ids else "",
                "method_value": atom_ids[0] if atom_ids else "",
            }
            for dimension, atom_ids in selected.items()
        }
        constraint = self.constraint_executor.evaluate(
            bindings, self.method.scenario_model.constraint_rules,
        )
        if constraint.status in {ScenarioConstraintStatus.DROP, ScenarioConstraintStatus.CONFLICT}:
            reasons.append(f"METHOD_CONSTRAINT:{constraint.status.value}:{constraint.reason}")
        return sorted(set(reasons))

    @staticmethod
    def _primary_validation_reason(reasons: list[str]) -> str:
        priority = (
            "INVENTED_ATOM_ID:", "WRONG_DIMENSION:",
            "ATOM_OUTSIDE_CANDIDATE_SET:", "UNKNOWN_DIMENSION:",
            "DIMENSION_KEY_SET_MISMATCH", "INVALID_ATOM_CARDINALITY:",
            "LOCKED_BINDING_CHANGED:", "REQUIRED_DIMENSION_EMPTY:",
            "NOT_APPLICABLE_DIMENSION_SELECTED:",
            "UNSUPPORTED_BINDING_REFINEMENT:",
            "COMPOUND_ATOM_INCOMPLETE:", "COMPOUND_ATOM_CONFLICT:",
            "ODD_SPEED_INCOMPATIBLE:", "METHOD_CONSTRAINT:",
        )
        return next(
            (reason for prefix in priority for reason in reasons if reason.startswith(prefix)),
            reasons[0],
        )

    def validate_provider_payload(
        self, synthesis_input: ScenarioSynthesisInput, payload: dict[str, Any],
    ) -> tuple[ScenarioSynthesisAssessment, ...]:
        if not isinstance(payload, dict) or set(payload) != {"variants"}:
            raise ScenarioSynthesisValidationError(
                "SCHEMA_TOP_LEVEL", "Provider output must contain only variants"
            )
        variants = payload.get("variants")
        desired = synthesis_input.coverage_plan.desired_variant_count
        if not isinstance(variants, list) or len(variants) != desired:
            raise ScenarioSynthesisValidationError(
                "COVERAGE_VARIANT_COUNT",
                f"Coverage Plan requires exactly {desired} variants",
            )
        expected_labels = [
            str(item.get("coverage_label", ""))
            for item in synthesis_input.coverage_plan.variant_intents
        ]
        assessments = []
        fingerprints: set[str] = set()
        for index, raw in enumerate(variants):
            if not isinstance(raw, dict) or set(raw) != {
                "coverage_label", "selected_atoms", "semantic_rationale", "context_refs",
            }:
                raise ScenarioSynthesisValidationError(
                    "SCHEMA_VARIANT_FIELDS", f"Variant {index} has invalid fields"
                )
            try:
                coverage = CoverageLabel(str(raw["coverage_label"]))
            except ValueError as exc:
                raise ScenarioSynthesisValidationError(
                    "INVALID_COVERAGE_LABEL", f"Variant {index} coverage label is invalid"
                ) from exc
            if coverage.value != expected_labels[index]:
                raise ScenarioSynthesisValidationError(
                    "COVERAGE_LABEL_PLAN_MISMATCH",
                    f"Variant {index} must implement {expected_labels[index]}",
                )
            selected_raw = raw["selected_atoms"]
            if not isinstance(selected_raw, dict):
                raise ScenarioSynthesisValidationError(
                    "SCHEMA_SELECTED_ATOMS", "selected_atoms must be an object"
                )
            selected: dict[str, tuple[str, ...]] = {}
            for dimension, values in selected_raw.items():
                if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                    raise ScenarioSynthesisValidationError(
                        "SCHEMA_ATOM_LIST", f"{dimension} must be an atom ID array"
                    )
                selected[str(dimension)] = tuple(values)
            rationale = raw["semantic_rationale"]
            refs = raw["context_refs"]
            if not isinstance(rationale, str) or not rationale.strip():
                raise ScenarioSynthesisValidationError(
                    "MISSING_RATIONALE", "Every variant requires a rationale"
                )
            if not isinstance(refs, list) or not refs or any(
                not isinstance(item, str) or not item.strip() for item in refs
            ):
                raise ScenarioSynthesisValidationError(
                    "INVALID_CONTEXT_REFS", "Every variant requires supplied context refs"
                )
            allowed_refs = {
                "PROJECT.ODD", "MF.description", "MF.functional_effect",
                "MF.vehicle_level_hazard", "HE.hazardous_event",
                "CAUSAL.summary", "PARENT.scenario", "METHOD.scenario_atom_catalog",
                "METHOD.fm_scenario_template",
            }
            if any(item not in allowed_refs for item in refs):
                raise ScenarioSynthesisValidationError(
                    "CONTEXT_REF_OUTSIDE_SUPPLIED_SET", "Provider invented a context ref"
                )
            reasons = self._selection_reasons(synthesis_input, selected)
            if reasons:
                raise ScenarioSynthesisValidationError(
                    self._primary_validation_reason(reasons), "; ".join(reasons)
                )
            candidate_sets = {
                item.dimension: {candidate.atom_id: candidate.canonical_atom_id
                                 for candidate in item.candidates}
                for item in synthesis_input.dimension_candidate_sets
            }
            fingerprint = self._canonical_json({
                dimension: [candidate_sets[dimension][atom_id] for atom_id in atom_ids]
                for dimension, atom_ids in selected.items()
            })
            if fingerprint in fingerprints:
                raise ScenarioSynthesisValidationError(
                    "DUPLICATE_VARIANT", "Equivalent canonical atom sets are duplicated"
                )
            fingerprints.add(fingerprint)
            assessments.append(ScenarioSynthesisAssessment(
                semantic_group_id=synthesis_input.semantic_group_id,
                coverage_label=coverage, selected_atoms=selected,
                semantic_rationale=rationale.strip(), context_refs=tuple(refs),
                validation_status=SynthesisValidationStatus.VALIDATED,
            ))
        diversity_reasons = self.diversity_validator.reasons(
            synthesis_input.coverage_plan,
            (item.selected_atoms for item in assessments),
        )
        if diversity_reasons:
            raise ScenarioSynthesisValidationError(
                diversity_reasons[0], "; ".join(diversity_reasons)
            )
        return tuple(assessments)

    def materialize(
        self, *, synthesis_input: ScenarioSynthesisInput,
        assessment: ScenarioSynthesisAssessment, parent: ScenarioCandidate,
        provider_evidence: dict[str, Any],
    ) -> tuple[ScenarioCandidate, AnalyticalScenarioInstantiation]:
        reasons = self._selection_reasons(synthesis_input, assessment.selected_atoms)
        if reasons:
            raise ScenarioSynthesisValidationError(
                self._primary_validation_reason(reasons), "; ".join(reasons)
            )
        selected_candidates: dict[str, ScenarioDimensionCandidate] = {}
        candidate_sets = {item.dimension: item for item in synthesis_input.dimension_candidate_sets}
        for dimension, atom_ids in assessment.selected_atoms.items():
            by_id = {item.atom_id: item for item in candidate_sets[dimension].candidates}
            for atom_id in atom_ids:
                selected_candidates[atom_id] = by_id[atom_id]
        material = {
            "contract": synthesis_input.contract_version,
            "method_contract_hash": synthesis_input.method_contract_hash,
            "malfunction_id": synthesis_input.malfunction_id,
            "parent_scenario_id": synthesis_input.parent_scenario_id,
            "hazardous_event_id": synthesis_input.hazardous_event_id,
            "selected_atoms": {
                key: list(value) for key, value in sorted(assessment.selected_atoms.items())
            },
        }
        fingerprint = hashlib.sha256(
            self._canonical_json(material).encode("utf-8")
        ).hexdigest()
        child_id = f"SCN-ANALYTICAL-{fingerprint[:16].upper()}"
        bindings: dict[str, dict[str, Any]] = {}
        for dimension in self.dimensions:
            atom_ids = assessment.selected_atoms.get(dimension, ())
            candidate_set = candidate_sets[dimension]
            decision = candidate_set.binding_decision
            applicability = candidate_set.applicability
            if not atom_ids:
                not_applicable = (
                    applicability.status is ScenarioDimensionApplicability.NOT_APPLICABLE
                )
                bindings[dimension] = {
                    "project_value": "", "method_value": "",
                    "binding_status": "NOT_APPLICABLE" if not_applicable else "MISSING",
                    "resolution_status": "NOT_APPLICABLE" if not_applicable else "PENDING",
                    "unresolved_reason": (
                        applicability.reason if not_applicable else candidate_set.generation_status
                    ),
                    "candidate_atom_ids": [
                        item.atom_id for item in candidate_set.candidates
                    ],
                    "applicability_status": applicability.status.value,
                    "applicability_reason": applicability.reason,
                    "applicability_trigger_evidence": list(applicability.trigger_evidence),
                    "binding_authority": decision.authority.value,
                    "refinable": decision.refinable,
                    "parent_atom_id": decision.parent_atom_id,
                    "binding_source_refs": list(decision.source_refs),
                    "binding_basis": decision.basis,
                    "dimension_source": "MethodContract.scenario_model",
                }
                continue
            candidate = selected_candidates[atom_ids[0]]
            refined = bool(
                decision.parent_atom_id
                and candidate.atom_id != decision.parent_atom_id
            )
            project_envelope = decision.project_speed_envelope_kph
            child_range = candidate.speed_range_kph
            bindings[dimension] = {
                "project_value": str(
                    parent.facts.get("method_scenario_dimensions", {})
                    .get(dimension, {}).get("project_value", "")
                ),
                "method_value": f"{candidate.atom_id} | {candidate.label}",
                "binding_status": (
                    "REFINED" if refined else
                    "EXACT" if not decision.refinable else "ANALYTICAL"
                ),
                "resolution_status": "RESOLVED",
                "unresolved_reason": "", "candidate_atom_ids": list(atom_ids),
                "atom_id": candidate.atom_id,
                "canonical_atom_id": candidate.canonical_atom_id,
                "filled_dimensions": list(candidate.dimensions),
                "atom_provenance": {
                    "source_asset": candidate.source_asset,
                    "source_rule": candidate.source_rule,
                    "source_tag": candidate.source_tag,
                },
                "method_semantics": deepcopy(candidate.method_semantics),
                "selection_origin": candidate.candidate_origin.value,
                "selection_reason": assessment.semantic_rationale,
                "applicability_status": applicability.status.value,
                "applicability_reason": applicability.reason,
                "applicability_trigger_evidence": list(applicability.trigger_evidence),
                "binding_authority": (
                    ScenarioBindingAuthority.ANALYTICAL_SELECTION.value
                    if refined else decision.authority.value
                ),
                "refinable": decision.refinable,
                "parent_atom_id": decision.parent_atom_id,
                "parent_method_atom": decision.parent_atom_id,
                "child_refined_atom": candidate.atom_id if refined else "",
                "binding_source_refs": list(decision.source_refs),
                "binding_basis": decision.basis,
                "project_speed_envelope_kph": (
                    list(project_envelope) if project_envelope is not None else None
                ),
                "parent_speed_range_kph": (
                    list(decision.parent_speed_range_kph)
                    if decision.parent_speed_range_kph is not None else None
                ),
                "child_speed_range_kph": (
                    list(child_range) if child_range is not None else None
                ),
                "speed_intersection_kph": (
                    list(intersection)
                    if (
                        intersection := self._range_intersection(
                            project_envelope, child_range,
                        )
                    ) is not None else None
                ),
                "ranking_scores": dict(candidate.ranking_scores),
                "template_relationship": candidate.template_relationship,
                "dimension_source": "MethodContract.scenario_model",
            }
        method_source = SourceRef(
            "method_contract", synthesis_input.method_contract_hash,
            "scenario_model.scenario_atom_catalog", "bounded Scenario atom selection",
        )
        facts = deepcopy(parent.facts)
        facts["method_scenario_dimensions"] = bindings
        facts["scenario_atom_ids"] = list(dict.fromkeys(
            selected_candidates[atom_id].canonical_atom_id
            for dimension in self.dimensions
            for atom_id in assessment.selected_atoms.get(dimension, ())
        ))
        for dimension, fact_key in {
            "WHERE": "operating_scenario",
            "ROAD": "road_surface_conditions",
            "EGO_ACTION": "vehicle_state",
            "EGO_DYNAMICS": "ego_dynamics",
            "OBJECT": "scenario_object_atom",
            "TRAFFIC_PATTERN": "traffic_pattern",
            "EGO_X_ROAD": "ego_road_relation",
        }.items():
            atom_ids = assessment.selected_atoms.get(dimension, ())
            if atom_ids:
                facts[fact_key] = selected_candidates[atom_ids[0]].label
        scope = {
            "malfunction_id": synthesis_input.malfunction_id,
            "scenario_id": child_id,
            "parent_scenario_id": synthesis_input.parent_scenario_id,
            "hazardous_event_id": synthesis_input.hazardous_event_id,
        }
        provenance = deepcopy(parent.fact_provenance)
        provenance["scenario_atom_ids"] = {
            "provenance": FactProvenance.DERIVED.value,
            "origin": "METHOD_DEFINED",
            "approval": ReviewStatus.FINALIZED.value,
            "source_refs": [{
                "source_type": method_source.source_type,
                "source_id": method_source.source_id,
                "location": method_source.location,
                "excerpt": method_source.excerpt,
            }],
            "applicable_scope": scope,
            "selection_authority": assessment.selection_authority,
        }
        validations = (
            {"check": "candidate_membership", "status": "PASS"},
            {"check": "binding_refinement_policy", "status": "PASS"},
            {"check": "dimension_applicability", "status": "PASS"},
            {"check": "compound_atom_integrity", "status": "PASS"},
            {"check": "odd_speed_intersection", "status": "PASS"},
            {"check": "method_constraints", "status": "PASS"},
            {"check": "coverage_plan", "status": "PASS"},
            {"check": "sibling_non_trivial_diversity", "status": "PASS"},
            {"check": "e_biased_ranking", "status": "NOT_USED"},
        )
        instantiation = AnalyticalScenarioInstantiation(
            scenario_id=child_id,
            parent_scenario_id=synthesis_input.parent_scenario_id,
            malfunction_id=synthesis_input.malfunction_id,
            hazardous_event_id=synthesis_input.hazardous_event_id,
            selected_atoms=tuple(selected_candidates.values()),
            dimension_bindings=bindings,
            deterministic_validations=validations,
            provider_evidence=dict(provider_evidence),
            project_facts_used=("PROJECT.ODD", "PARENT.scenario"),
            method_facts_used=tuple(sorted({
                f"{item.source_asset}:{item.source_rule}"
                for item in selected_candidates.values()
            })),
        )
        instance = {
            "instance_id": child_id,
            "parent_scenario_id": synthesis_input.parent_scenario_id,
            "malfunction_id": synthesis_input.malfunction_id,
            "hazardous_event_id": synthesis_input.hazardous_event_id,
            "semantic_group_id": synthesis_input.semantic_group_id,
            "coverage_plan": synthesis_input.coverage_plan.to_dict(),
            "fm_scenario_template_id": synthesis_input.fm_scenario_template.get(
                "template_id", ""
            ),
            "selected_atoms": facts["scenario_atom_ids"],
            "dimension_bindings": bindings,
            "project_facts_used": list(instantiation.project_facts_used),
            "method_facts_used": list(instantiation.method_facts_used),
            "provider_evidence": provider_evidence,
            "deterministic_validations": list(validations),
            "base_analysis_instance": deepcopy(parent.analysis_instance),
            "synthesis_version": synthesis_input.contract_version,
            "validation_status": "VALIDATED",
            "applicable_scope": scope,
        }
        detail = " | ".join(
            f"{dimension}={','.join(atom_ids) if atom_ids else 'PENDING'}"
            for dimension, atom_ids in assessment.selected_atoms.items()
        )
        child = replace(
            parent, scenario_id=child_id,
            operating_scenario=str(facts.get("operating_scenario", parent.operating_scenario)),
            situational_description=detail, situational_detailing=detail,
            facts=facts, context_resolution={
                **deepcopy(parent.context_resolution),
                "scenario_synthesis": {
                    "semantic_group_id": synthesis_input.semantic_group_id,
                    "coverage_label": assessment.coverage_label.value,
                    "status": ScenarioSynthesisStatus.METHOD_VALID.value,
                },
            },
            fact_provenance=provenance, status=ReviewStatus.PENDING,
            sources=list(dict.fromkeys([*parent.sources, method_source])),
            review_reason=(
                "Bounded analytical Scenario is method-valid; causal reuse/revalidation "
                "and any physical engineering assumptions remain separate gates."
            ),
            source_scenario_id=parent.scenario_id,
            atomic_variant=f"scenario_synthesis:{assessment.coverage_label.value}",
            semantic_fingerprint=fingerprint,
            analysis_instance=instance,
        )
        return child, instantiation


__all__ = [
    "ConstrainedScenarioSynthesisService", "ScenarioSynthesisValidationError",
]

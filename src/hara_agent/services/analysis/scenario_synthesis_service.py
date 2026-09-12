"""Governed, bounded Scenario atom synthesis over a compiled MethodContract.

The service deliberately excludes Exposure ratings from semantic ranking.  It
also never widens an empty ODD/semantic match back to the full atom catalog.
Legacy FUSA used both behaviours; neither is method authority in V13.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import itertools
import json
import re
from typing import Any, Iterable

from hara_agent.contracts import (
    AnalyticalScenarioInstantiation, CandidateOrigin, CoverageLabel,
    MethodContract, ScenarioAtomCandidateSet, ScenarioCombinationCandidate,
    ScenarioDimensionCandidate, ScenarioSynthesisAssessment,
    ScenarioSynthesisInput, ScenarioSynthesisStatus,
    SynthesisValidationStatus,
)
from hara_agent.models import FactProvenance, ReviewStatus, ScenarioCandidate, SourceRef

from .scenario_constraint_service import ScenarioConstraintExecutor, ScenarioConstraintStatus


class ScenarioSynthesisValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ConstrainedScenarioSynthesisService:
    """Create bounded candidate sets and materialize validated analytical children."""

    candidate_cap_per_dimension = 12
    combination_cap = 32
    optional_dimensions = frozenset({"EGO_X_ROAD", "TRAFFIC_PATTERN"})

    # Translation terms are shortlist signals only.  They never resolve a
    # binding, create an atom, or override Method/ODD constraints.
    _CONCEPT_MARKERS = {
        "parking": ("停车", "泊车", "车位", "parking", "garage", "avp"),
        "reverse": ("倒车", "泊出", "reverse", "reversing"),
        "stopping": ("停车", "刹停", "停止", "驻车", "stop", "hold"),
        "pedestrian": ("行人", "pedestrian", "person", "vru"),
        "cyclist": ("两轮", "自行车", "骑行", "bicycle", "cyclist"),
        "vehicle": ("车辆", "汽车", "乘用车", "vehicle", "passenger_car", "rear_end"),
        "obstacle": ("障碍", "锥桶", "柱子", "obstacle", "cone", "pillar"),
        "rain": ("小雨", "雨", "rain", "wet"),
        "visibility": ("能见度", "雾", "visibility", "fog"),
        "slope": ("坡", "slope", "gradient", "mountain"),
    }

    _DIMENSION_TERMS = {
        "WHERE": {
            "parking": ("park", "garage", "verkehrsberuhigte", "traffic calmed"),
        },
        "ROAD": {
            "parking": ("normal friction",),
            "rain": ("wet", "rain", "friction"),
            "visibility": ("visibility", "fog"),
            "slope": ("slope", "gradient"),
        },
        "EGO_ACTION": {
            "parking": ("park", "hold", "maneuver", "slow driv"),
            "reverse": ("revers", "park"),
            "stopping": ("stop", "hold", "park"),
        },
        "EGO_X_ROAD": {
            "parking": ("park", "halten"),
            "slope": ("slope", "mountain", "hang"),
        },
        "TRAFFIC_PATTERN": {
            "vehicle": ("following", "traffic", "cutting", "delta speed"),
            "reverse": ("reverse",),
        },
        "OBJECT": {
            "pedestrian": ("person", "pedestrian"),
            "cyclist": ("cycl", "bicycle", "two-wheel"),
            "vehicle": ("vehicle", "following", "traffic"),
            "obstacle": ("object", "obstacle"),
        },
    }

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
        if not self.dimensions or not self.catalog:
            raise ValueError("Scenario synthesis requires a compiled atom ontology")

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {
            item for item in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", value.casefold())
            if item not in {"the", "and", "with", "from", "into", "while"}
        }

    @classmethod
    def _concepts(cls, text: str) -> set[str]:
        folded = text.casefold()
        return {
            concept for concept, markers in cls._CONCEPT_MARKERS.items()
            if any(marker.casefold() in folded for marker in markers)
        }

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
    ) -> ScenarioDimensionCandidate:
        atom_id = str(atom.get("atom_id", "")).strip()
        canonical = str(
            atom.get("v2") or atom.get("v2_proper") or atom_id
        ).strip()
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
        )

    @staticmethod
    def _binding_atom_id(binding: dict[str, Any]) -> str:
        value = str(binding.get("atom_id") or binding.get("canonical_atom_id") or "").strip()
        return value.split("|", 1)[0].strip()

    def _resolved_lock(
        self, parent: ScenarioCandidate, dimension: str,
    ) -> ScenarioDimensionCandidate | None:
        bindings = parent.facts.get("method_scenario_dimensions", {})
        binding = bindings.get(dimension, {}) if isinstance(bindings, dict) else {}
        if not isinstance(binding, dict) or str(binding.get("resolution_status", "")).upper() != "RESOLVED":
            return None
        atom_id = self._binding_atom_id(binding)
        atom = self.by_id.get(atom_id)
        if atom is None or dimension not in atom.get("filled_dimensions", []):
            return None
        return self._candidate(
            atom, origin=CandidateOrigin.DIRECT_PROJECT_BINDING,
            refs=(f"PARENT.{dimension}", "METHOD.scenario_atom_catalog"),
            reason="Existing source-valid RESOLVED binding is locked.", validated=True,
        )

    def _context_text(
        self, *, malfunction: dict[str, Any], parent: ScenarioCandidate,
        assessment: dict[str, Any], project_context: dict[str, Any],
    ) -> str:
        material = {
            "function_id": malfunction.get("function_id", ""),
            "guideword": malfunction.get("guideword", ""),
            "description": malfunction.get("description", ""),
            "functional_effect": malfunction.get("functional_effect", ""),
            "vehicle_level_hazard": malfunction.get("vehicle_level_hazard", ""),
            "hazardous_event": assessment.get("hazardous_event", ""),
            "potential_harm": assessment.get("potential_harm", ""),
            "causal_chain": assessment.get("causal_chain", {}),
            "parent": {
                "operating_scenario": parent.operating_scenario,
                "operating_mode": parent.operating_mode,
                "facts": {
                    key: value for key, value in parent.facts.items()
                    if key not in {"method_scenario_dimensions"}
                },
                "analysis_instance": parent.analysis_instance,
            },
            "odd": project_context,
        }
        return self._canonical_json(material)

    def _semantic_terms(self, dimension: str, context: str) -> tuple[str, ...]:
        concepts = self._concepts(context)
        terms = []
        for concept in sorted(concepts):
            terms.extend(self._DIMENSION_TERMS.get(dimension, {}).get(concept, ()))
        return tuple(dict.fromkeys(item.casefold() for item in terms))

    def _origin_for(
        self, dimension: str, parent: ScenarioCandidate,
    ) -> CandidateOrigin:
        if dimension in {"WHERE", "ROAD", "EGO_X_ROAD", "EGO_DYNAMICS"}:
            return CandidateOrigin.ODD_CONSTRAINED_CATALOG
        instance = parent.analysis_instance if isinstance(parent.analysis_instance, dict) else {}
        if dimension == "OBJECT" and instance.get("source_template_id"):
            return CandidateOrigin.METHOD_TEMPLATE
        return CandidateOrigin.HAZARD_CAUSAL_SEMANTIC_CANDIDATE

    def _ranked_pool(
        self, *, dimension: str, context: str, parent: ScenarioCandidate,
        project_context: dict[str, Any],
        locked_by_dimension: dict[str, ScenarioDimensionCandidate],
    ) -> list[tuple[float, dict[str, Any], str]]:
        terms = self._semantic_terms(dimension, context)
        context_tokens = self._tokens(context)
        envelope = self._speed_envelope(parent)
        ranked = []
        for atom in self.catalog:
            if dimension not in atom.get("filled_dimensions", []):
                continue
            if not self._speed_compatible(atom, envelope):
                continue
            if dimension in {"ROAD", "EGO_X_ROAD"} and not self._slope_compatible(
                atom, project_context,
            ):
                continue
            if dimension == "ROAD" and not self._weather_compatible(atom, project_context):
                continue
            atom_id = str(atom.get("atom_id", ""))
            compound_conflict = any(
                other in locked_by_dimension
                and locked_by_dimension[other].atom_id != atom_id
                for other in atom.get("filled_dimensions", [])
                if other != dimension
            )
            if compound_conflict:
                continue
            label = str(atom.get("label", "")).casefold()
            term_hits = [term for term in terms if term in label]
            lexical_hits = context_tokens & self._tokens(label)
            score = 5.0 * len(term_hits) + float(len(lexical_hits))
            if score <= 0:
                continue
            reason_parts = []
            if term_hits:
                reason_parts.append("semantic shortlist=" + ",".join(term_hits))
            if lexical_hits:
                reason_parts.append("lexical overlap=" + ",".join(sorted(lexical_hits)))
            ranked.append((score, atom, "; ".join(reason_parts)))
        return sorted(ranked, key=lambda item: (-item[0], str(item[1].get("atom_id", ""))))

    def candidate_sets(
        self, *, malfunction: dict[str, Any], parent: ScenarioCandidate,
        assessment: dict[str, Any], project_context: dict[str, Any],
    ) -> tuple[ScenarioAtomCandidateSet, ...]:
        context = self._context_text(
            malfunction=malfunction, parent=parent, assessment=assessment,
            project_context=project_context,
        )
        locks = {
            dimension: lock for dimension in self.dimensions
            if (lock := self._resolved_lock(parent, dimension)) is not None
        }
        result = []
        bindings = parent.facts.get("method_scenario_dimensions", {})
        for dimension in self.dimensions:
            catalog_size = sum(
                dimension in item.get("filled_dimensions", []) for item in self.catalog
            )
            before = str(
                (bindings.get(dimension, {}) if isinstance(bindings, dict) else {}).get(
                    "resolution_status", "PENDING"
                )
            )
            lock = locks.get(dimension)
            if lock is not None:
                result.append(ScenarioAtomCandidateSet(
                    dimension=dimension, catalog_size=catalog_size,
                    candidates=(lock,), locked_atom_ids=(lock.atom_id,),
                    resolution_status_before=before,
                    generation_status="LOCKED_RESOLVED",
                    reason="Parent source-valid binding retained without Provider authority.",
                ))
                continue
            dimension_context = context
            if dimension == "WHERE":
                dimension_context = self._canonical_json({
                    "odd_locations": project_context.get("odd_locations", []),
                    "odd_road_types": project_context.get("odd_road_types", []),
                    "parent_operating_scenario": parent.operating_scenario,
                })
            elif dimension == "ROAD":
                dimension_context = self._canonical_json({
                    "odd_weather_conditions": project_context.get("odd_weather_conditions", []),
                    "odd_road_surfaces": project_context.get("odd_road_surfaces", []),
                })
            elif dimension == "EGO_X_ROAD":
                dimension_context = self._canonical_json({
                    "odd_road_surfaces": project_context.get("odd_road_surfaces", []),
                    "malfunction": malfunction,
                    "parent_operating_scenario": parent.operating_scenario,
                })
            pool = self._ranked_pool(
                dimension=dimension, context=dimension_context, parent=parent,
                project_context=project_context,
                locked_by_dimension=locks,
            )
            shortlisted = pool[:self.candidate_cap_per_dimension]
            candidates = tuple(
                self._candidate(
                    atom, origin=self._origin_for(dimension, parent),
                    refs=(
                        "PROJECT.ODD", "MF.description", "MF.functional_effect",
                        "HE.hazardous_event", "PARENT.scenario", "METHOD.scenario_atom_catalog",
                    ),
                    reason=(
                        reason + "; shortlist signal is non-authoritative and requires bounded selection"
                    ),
                )
                for _, atom, reason in shortlisted
            )
            zero_status = (
                "PENDING_NO_COMPATIBLE_ATOM" if not candidates
                else "CANDIDATES_AVAILABLE"
            )
            result.append(ScenarioAtomCandidateSet(
                dimension=dimension, catalog_size=catalog_size,
                candidates=candidates, resolution_status_before=before,
                generation_status=zero_status,
                reason=(
                    "No ODD-compatible semantic match; full-catalog fallback is prohibited."
                    if not candidates else
                    "Method/ODD-compatible candidates narrowed without Exposure ratings."
                ),
                search_truncated=len(pool) > len(shortlisted),
            ))
        # Compound atoms remain candidates only when independent narrowing kept
        # them in every claimed Method dimension.  This prevents a traffic
        # match from smuggling an ODD-incompatible WHERE atom into the set.
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
            result = [replace(
                item,
                candidates=tuple(
                    candidate for candidate in item.candidates
                    if candidate.atom_id not in incomplete_compounds
                ),
                generation_status=(
                    item.generation_status
                    if any(candidate.atom_id not in incomplete_compounds for candidate in item.candidates)
                    else "PENDING_NO_COMPATIBLE_ATOM"
                ),
                reason=(
                    item.reason + " Incomplete cross-dimension compound candidates were removed."
                ),
            ) for item in result]
        return tuple(result)

    def build_input(
        self, *, malfunction: dict[str, Any], parent: ScenarioCandidate,
        assessment: dict[str, Any], project_context: dict[str, Any],
    ) -> ScenarioSynthesisInput:
        candidate_sets = self.candidate_sets(
            malfunction=malfunction, parent=parent, assessment=assessment,
            project_context=project_context,
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
            if not selected[dimension] and dimension not in self.optional_dimensions:
                reasons.append(f"REQUIRED_DIMENSION_EMPTY:{dimension}")
        selected_by_dimension = {
            dimension: set(atom_ids) for dimension, atom_ids in selected.items()
        }
        selected_union = set(itertools.chain.from_iterable(selected_by_dimension.values()))
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
        if not isinstance(variants, list) or not 1 <= len(variants) <= 3:
            raise ScenarioSynthesisValidationError(
                "SCHEMA_VARIANT_COUNT", "Provider must return one to three variants"
            )
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
            fingerprint = self._canonical_json({
                dimension: list(atom_ids) for dimension, atom_ids in selected.items()
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
        return tuple(assessments)

    def bounded_combinations(
        self, synthesis_input: ScenarioSynthesisInput,
    ) -> tuple[ScenarioCombinationCandidate, ...]:
        beams: list[tuple[dict[str, tuple[str, ...]], float]] = [({}, 0.0)]
        truncated = False
        for candidate_set in synthesis_input.dimension_candidate_sets:
            if candidate_set.locked_atom_ids:
                choices = [candidate_set.locked_atom_ids]
            else:
                choices = [(item.atom_id,) for item in candidate_set.candidates[:3]]
                if candidate_set.dimension in self.optional_dimensions:
                    choices.append(())
            if not choices:
                choices = [()]
            expanded = []
            for selected, score in beams:
                for rank, choice in enumerate(choices):
                    value = {**selected, candidate_set.dimension: choice}
                    reasons = self._selection_reasons(synthesis_input, value, partial=True)
                    if not reasons:
                        expanded.append((value, score + 1.0 / (rank + 1)))
            expanded.sort(key=lambda item: (-item[1], self._canonical_json(item[0])))
            if len(expanded) > self.combination_cap:
                truncated = True
            beams = expanded[:self.combination_cap]
        results = []
        for index, (selected, score) in enumerate(beams, start=1):
            reasons = self._selection_reasons(synthesis_input, selected)
            status = (
                SynthesisValidationStatus.VALIDATED
                if not reasons else SynthesisValidationStatus.REJECTED
            )
            material = self._canonical_json(selected)
            results.append(ScenarioCombinationCandidate(
                combination_id="COMB-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16].upper(),
                selected_atoms=selected, semantic_score=round(score, 6),
                validation_status=status,
                validation_reasons=tuple([
                    *(reasons or []),
                    *(["CANDIDATE_SEARCH_TRUNCATED"] if truncated and index == len(beams) else []),
                ]),
            ))
        return tuple(results)

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
            if not atom_ids:
                bindings[dimension] = {
                    "project_value": "", "method_value": "",
                    "binding_status": "MISSING", "resolution_status": "PENDING",
                    "unresolved_reason": "METHOD_IRRELEVANCE_NOT_PROVEN",
                    "candidate_atom_ids": [
                        item.atom_id for item in candidate_sets[dimension].candidates
                    ],
                    "dimension_source": "MethodContract.scenario_model",
                }
                continue
            candidate = selected_candidates[atom_ids[0]]
            bindings[dimension] = {
                "project_value": str(
                    parent.facts.get("method_scenario_dimensions", {})
                    .get(dimension, {}).get("project_value", "")
                ),
                "method_value": f"{candidate.atom_id} | {candidate.label}",
                "binding_status": "EXACT", "resolution_status": "RESOLVED",
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
            {"check": "locked_bindings", "status": "PASS"},
            {"check": "compound_atom_integrity", "status": "PASS"},
            {"check": "odd_speed_intersection", "status": "PASS"},
            {"check": "method_constraints", "status": "PASS"},
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

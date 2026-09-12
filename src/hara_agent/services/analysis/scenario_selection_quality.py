"""Deterministic policy and ranking for governed Scenario selection.

The Provider never defines applicability, binding authority, coverage intent,
or Method membership.  This module derives those decisions from structured
project, malfunction, hazard, causal, and compiled MethodContract evidence.
Free-text parsing is deliberately limited to explicit engineering terms.
"""

from __future__ import annotations

from collections import Counter
import json
import math
import re
from typing import Any, Iterable

from hara_agent.contracts import (
    ScenarioAtomCandidateSet, ScenarioBindingAuthority,
    ScenarioBindingDecision, ScenarioCoveragePlan,
    ScenarioDimensionApplicability,
    ScenarioDimensionApplicabilityDecision,
)
from hara_agent.models import ScenarioCandidate


_CATEGORY_MARKERS: dict[str, tuple[str, ...]] = {
    "ACTION_PARK": ("parking", "park", "avp", "slow driving", "泊车", "停车场", "车库"),
    "LOCATION_PARKING": ("parking", "car park", "garage", "parkhaus", "停车场", "车库"),
    "LOCATION_MOTORWAY": ("motorway", "autobahn", "高速公路"),
    "LOCATION_EXPRESSWAY": ("expressway", "快速路"),
    "LOCATION_CITY": ("city road", "city traffic", "innerstädt", "城市道路"),
    "LOCATION_RURAL": ("rural road", "country road", "landstraße", "乡村道路"),
    "ACTION_REVERSE": ("reverse", "reversing", "backing", "倒车", "后退"),
    "ACTION_HOLD": ("holding", "hold capability", "rollaway", "parking brake", "stands", "standing", "halten", "驻车", "溜车"),
    "ACTION_STOP": ("stopping", "stop", "braking", "deceleration", "制动", "停车"),
    "ACTION_ACCELERATE": ("acceleration", "accelerate", "propulsion", "加速", "驱动"),
    "ACTION_TURN": ("steering", "turn", "lateral", "转向", "横向"),
    "OBJECT_PEDESTRIAN": ("pedestrian", "person", "vru", "行人"),
    "OBJECT_CYCLIST": ("cyclist", "bicycle", "two-wheel", "骑行", "自行车"),
    "OBJECT_VEHICLE": ("vehicle", "passenger_car", "rear vehicle", "front vehicle", "车辆", "后车"),
    "OBJECT_STATIC": ("static obstacle", "obstacle", "pillar", "cone", "静态障碍", "障碍物"),
    "OBJECT_OCCUPANT": ("occupant", "passenger", "乘员"),
    "TRAFFIC_FOLLOWING": ("following", "rear-end", "rear end", "rear vehicle", "追尾", "跟车", "后车"),
    "TRAFFIC_ONCOMING": ("oncoming", "head-on", "opposing traffic", "对向", "迎面"),
    "TRAFFIC_CROSSING": ("crossing traffic", "cross traffic", "intersection traffic", "交叉交通", "横穿车辆"),
    "TRAFFIC_CUT_IN": ("cut-in", "cutting-in", "cut in", "切入", "加塞"),
    "TRAFFIC_REVERSE": ("reverse interaction", "reversing into", "backing into", "倒车碰", "倒车驶入"),
    "TRAFFIC_PARKING": ("parking traffic", "parking-lot traffic", "停车场交通"),
    "ROAD_SLOPE": ("slope", "gradient", "incline", "坡道", "坡度", "斜坡"),
    "ROAD_LOW_FRICTION": ("low friction", "reduced friction", "wet", "rain", "低附", "湿滑", "雨"),
}

_ACTION_CATEGORIES = frozenset(name for name in _CATEGORY_MARKERS if name.startswith("ACTION_"))
_OBJECT_CATEGORIES = frozenset(name for name in _CATEGORY_MARKERS if name.startswith("OBJECT_"))
_TRAFFIC_CATEGORIES = frozenset(name for name in _CATEGORY_MARKERS if name.startswith("TRAFFIC_"))
_ROAD_CATEGORIES = frozenset(name for name in _CATEGORY_MARKERS if name.startswith("ROAD_"))


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", value.casefold())
        if token not in {"the", "and", "with", "from", "into", "while", "that", "this"}
    )


def _explicit_categories(text: str) -> set[str]:
    folded = text.casefold()
    return {
        category for category, markers in _CATEGORY_MARKERS.items()
        if any(marker.casefold() in folded for marker in markers)
    }


def _normalize_object(value: Any) -> str:
    folded = str(value).strip().casefold()
    if any(token in folded for token in ("pedestrian", "person", "vru", "行人")):
        return "OBJECT_PEDESTRIAN"
    if any(token in folded for token in ("cycl", "bicycle", "自行车", "骑行")):
        return "OBJECT_CYCLIST"
    if any(token in folded for token in ("vehicle", "car", "truck", "车辆", "汽车")):
        return "OBJECT_VEHICLE"
    if any(token in folded for token in ("obstacle", "pillar", "cone", "障碍", "柱")):
        return "OBJECT_STATIC"
    if any(token in folded for token in ("occupant", "passenger", "乘员")):
        return "OBJECT_OCCUPANT"
    return ""


class ScenarioSemanticQueryBuilder:
    """Build compact structured features without making engineering inferences."""

    @staticmethod
    def build(
        *, malfunction: dict[str, Any], parent: ScenarioCandidate,
        assessment: dict[str, Any], project_context: dict[str, Any],
        fm_template: dict[str, Any],
    ) -> dict[str, Any]:
        causal = assessment.get("causal_assessment", {})
        causal = causal if isinstance(causal, dict) else {}
        facts = parent.facts if isinstance(parent.facts, dict) else {}
        explicit_fields = {
            "MF.description": malfunction.get("description", ""),
            "MF.functional_effect": malfunction.get("functional_effect", ""),
            "MF.vehicle_level_hazard": malfunction.get("vehicle_level_hazard", ""),
            "HE.hazardous_event": assessment.get(
                "hazardous_event", causal.get("hazardous_event", "")
            ),
            "CAUSAL.summary": causal.get("causal_chain", []),
            "PARENT.scenario": {
                "operating_scenario": parent.operating_scenario,
                "operating_mode": parent.operating_mode,
                "vehicle_state": facts.get("vehicle_state", ""),
                "object_type": facts.get("object_type", ""),
                "road_user_type": facts.get("road_user_type", ""),
                "collision_type": facts.get("collision_type", ""),
                "object_position": facts.get("object_position", ""),
            },
        }
        text = " ".join(_json_text(value) for value in explicit_fields.values())
        categories = _explicit_categories(text)
        structured_objects = {
            normalized for value in (facts.get("object_type"), facts.get("road_user_type"))
            if (normalized := _normalize_object(value))
        }
        if structured_objects:
            categories.difference_update(_OBJECT_CATEGORIES)
            categories.update(structured_objects)

        option = fm_template.get("active_option", {})
        if isinstance(option, dict):
            if normalized := _normalize_object(option.get("obj_type", "")):
                categories.difference_update(_OBJECT_CATEGORIES)
                categories.add(normalized)
            categories.update(_explicit_categories(_json_text(option)))

        collision = str(facts.get("collision_type", "")).casefold()
        position = str(facts.get("object_position", "")).casefold()
        object_vehicle = "OBJECT_VEHICLE" in categories
        if object_vehicle and (collision == "rear" or position == "rear"):
            categories.add("TRAFFIC_FOLLOWING")
        if "ACTION_REVERSE" in categories and categories & _OBJECT_CATEGORIES:
            categories.add("TRAFFIC_REVERSE")

        evidence = {
            category: tuple(
                ref for ref, value in explicit_fields.items()
                if category in _explicit_categories(_json_text(value))
            )
            for category in sorted(categories)
        }
        location_text = _json_text({
            "parent_operating_scenario": parent.operating_scenario,
            "odd_locations": project_context.get("odd_locations", []),
            "odd_road_types": project_context.get("odd_road_types", []),
        })
        odd_road_text = _json_text({
            "odd_weather_conditions": project_context.get("odd_weather_conditions", []),
            "odd_road_surfaces": project_context.get("odd_road_surfaces", []),
        })
        return {
            "failure_type": str(malfunction.get("failure_type", "")),
            "guideword": str(malfunction.get("guideword", "")),
            "component_category": str(malfunction.get("component_category", "")),
            "operating_mode": parent.operating_mode,
            "action_categories": sorted(categories & _ACTION_CATEGORIES),
            "object_categories": sorted(categories & _OBJECT_CATEGORIES),
            "traffic_relations": sorted(categories & _TRAFFIC_CATEGORIES),
            "road_relations": sorted(categories & _ROAD_CATEGORIES),
            "location_categories": sorted(
                category for category in _explicit_categories(location_text)
                if category.startswith("LOCATION_")
            ),
            "odd_road_categories": sorted(
                category for category in _explicit_categories(odd_road_text)
                if category.startswith("ROAD_")
            ),
            "explicit_category_evidence": {
                key: list(value) for key, value in evidence.items()
            },
            "query_tokens": sorted(set(_tokens(text))),
            "causal_tokens": sorted(set(_tokens(_json_text({
                "hazard": explicit_fields["HE.hazardous_event"],
                "causal": explicit_fields["CAUSAL.summary"],
            })))),
            "source_refs": list(explicit_fields),
            "project_odd_present": bool(project_context),
        }


class ScenarioBindingPolicy:
    """Separate binding resolution from exact, non-refinable authority."""

    def __init__(self, by_id: dict[str, dict[str, Any]]):
        self.by_id = by_id

    @staticmethod
    def _atom_id(binding: dict[str, Any]) -> str:
        return str(binding.get("atom_id") or binding.get("canonical_atom_id") or "").split("|", 1)[0].strip()

    @staticmethod
    def _speed_range(atom: dict[str, Any] | None) -> tuple[float | None, float | None] | None:
        raw = (atom or {}).get("speed_range_kph")
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None
        return tuple(float(value) if value is not None else None for value in raw)  # type: ignore[return-value]

    def decide(
        self, parent: ScenarioCandidate, dimension: str,
        project_speed_envelope: tuple[float | None, float | None],
    ) -> ScenarioBindingDecision:
        bindings = parent.facts.get("method_scenario_dimensions", {})
        binding = bindings.get(dimension, {}) if isinstance(bindings, dict) else {}
        binding = binding if isinstance(binding, dict) else {}
        atom_id = self._atom_id(binding)
        atom = self.by_id.get(atom_id)
        provenance = binding.get("atom_provenance", {})
        provenance = provenance if isinstance(provenance, dict) else {}
        refs = tuple(dict.fromkeys(filter(None, (
            f"PARENT.{dimension}",
            ":".join(filter(None, (
                str(provenance.get("source_asset", "")),
                str(provenance.get("source_rule", "")),
            ))),
            str(binding.get("dimension_source", "")),
        ))))
        if not refs:
            refs = (f"PARENT.{dimension}",)
        resolved = str(binding.get("resolution_status", "")).upper() == "RESOLVED"
        explicit = str(binding.get("binding_authority", "")).upper()
        speed_constraint = binding.get("speed_constraint", {})
        speed_constraint = speed_constraint if isinstance(speed_constraint, dict) else {}
        resolved_by = str(speed_constraint.get("resolved_by", "")).upper()
        origin = str(binding.get("selection_origin", "")).upper()

        if explicit in ScenarioBindingAuthority._value2member_map_:
            authority = ScenarioBindingAuthority(explicit)
        elif resolved_by == ScenarioBindingAuthority.RANGE_CONTAINMENT.value:
            authority = ScenarioBindingAuthority.RANGE_CONTAINMENT
        elif origin == "APPROVED_ALIAS":
            authority = ScenarioBindingAuthority.APPROVED_ALIAS
        elif origin == "METHOD_TEMPLATE":
            authority = ScenarioBindingAuthority.METHOD_TEMPLATE_INFERENCE
        elif origin == "DETERMINISTIC_DERIVATION" or len((atom or {}).get("filled_dimensions", [])) > 1:
            authority = ScenarioBindingAuthority.DERIVED_COMPOUND
        elif origin in {"HAZARD_CAUSAL_SEMANTIC_CANDIDATE", "ODD_CONSTRAINED_CATALOG", "LLM_SELECTED_FROM_APPROVED_CANDIDATES"}:
            authority = ScenarioBindingAuthority.ANALYTICAL_SELECTION
        elif resolved and origin == "DIRECT_PROJECT_BINDING":
            authority = ScenarioBindingAuthority.EXACT_PROJECT_FACT
        elif resolved and atom_id:
            authority = ScenarioBindingAuthority.EXACT_METHOD_MAPPING
        else:
            authority = ScenarioBindingAuthority.ANALYTICAL_SELECTION

        refinable = authority in {
            ScenarioBindingAuthority.RANGE_CONTAINMENT,
            ScenarioBindingAuthority.METHOD_TEMPLATE_INFERENCE,
            ScenarioBindingAuthority.ANALYTICAL_SELECTION,
        }
        basis = {
            ScenarioBindingAuthority.RANGE_CONTAINMENT: "Broad Method range establishes compatibility, not an exact child value.",
            ScenarioBindingAuthority.METHOD_TEMPLATE_INFERENCE: "Template inference is source-governed but may be refined by compatible child semantics.",
            ScenarioBindingAuthority.ANALYTICAL_SELECTION: "Analytical parent selection is not immutable child authority.",
            ScenarioBindingAuthority.EXACT_PROJECT_FACT: "Exact project fact is authoritative and non-refinable.",
            ScenarioBindingAuthority.EXACT_METHOD_MAPPING: "Exact one-to-one Method mapping is authoritative and non-refinable.",
            ScenarioBindingAuthority.APPROVED_ALIAS: "Approved exact alias is authoritative and non-refinable.",
            ScenarioBindingAuthority.DERIVED_COMPOUND: "Source-defined compound membership must remain internally consistent.",
        }[authority]
        return ScenarioBindingDecision(
            dimension=dimension, authority=authority, refinable=refinable,
            parent_atom_id=atom_id if resolved and atom is not None else "",
            source_refs=refs, basis=basis,
            project_speed_envelope_kph=(
                project_speed_envelope if dimension == "EGO_DYNAMICS" else None
            ),
            parent_speed_range_kph=(
                self._speed_range(atom) if dimension == "EGO_DYNAMICS" else None
            ),
        )


class ScenarioDimensionApplicabilityService:
    """Determine per-group dimension applicability before Provider selection."""

    @staticmethod
    def assess(
        dimension: str, query: dict[str, Any], binding: ScenarioBindingDecision,
    ) -> ScenarioDimensionApplicabilityDecision:
        evidence_map = query.get("explicit_category_evidence", {})
        evidence_map = evidence_map if isinstance(evidence_map, dict) else {}
        traffic = tuple(str(item) for item in query.get("traffic_relations", []))
        road = tuple(str(item) for item in query.get("road_relations", []))
        actions = tuple(str(item) for item in query.get("action_categories", []))
        objects = tuple(str(item) for item in query.get("object_categories", []))
        refs: list[str] = []
        for category in (*traffic, *road, *actions, *objects):
            raw = evidence_map.get(category, [])
            if isinstance(raw, list):
                refs.extend(str(item) for item in raw)
        refs.extend(binding.source_refs)
        refs = list(dict.fromkeys(refs or ["PARENT.scenario"]))

        if binding.parent_atom_id and not binding.refinable:
            status = ScenarioDimensionApplicability.REQUIRED
            reason = "An exact authoritative parent binding must be preserved."
            trigger = (f"exact_parent_atom={binding.parent_atom_id}",)
        elif dimension == "TRAFFIC_PATTERN":
            if traffic:
                status = ScenarioDimensionApplicability.REQUIRED
                reason = "Explicit traffic-interaction semantics require a Method traffic relation."
                trigger = traffic
            elif "OBJECT_VEHICLE" in objects:
                status = ScenarioDimensionApplicability.OPTIONAL
                reason = "A vehicle object exists, but no explicit inter-vehicle traffic relation is proven."
                trigger = ("OBJECT_VEHICLE",)
            else:
                status = ScenarioDimensionApplicability.NOT_APPLICABLE
                reason = "No explicit traffic relation or interacting vehicle is present."
                trigger = tuple(objects) or ("NO_TRAFFIC_RELATION",)
        elif dimension == "EGO_X_ROAD":
            slope_driven = "ROAD_SLOPE" in road and bool({"ACTION_HOLD", "ACTION_REVERSE"} & set(actions))
            if slope_driven:
                status = ScenarioDimensionApplicability.REQUIRED
                reason = "The active holding/reverse mechanism is explicitly road-slope relative."
                trigger = ("ROAD_SLOPE", *sorted({"ACTION_HOLD", "ACTION_REVERSE"} & set(actions)))
            elif "ROAD_SLOPE" in road:
                status = ScenarioDimensionApplicability.OPTIONAL
                reason = "A slope is explicit, but child road-relative semantics are not required by the mechanism."
                trigger = ("ROAD_SLOPE",)
            else:
                status = ScenarioDimensionApplicability.NOT_APPLICABLE
                reason = "No explicit road-relative vehicle relation is present."
                trigger = ("NO_ROAD_RELATION",)
        else:
            status = ScenarioDimensionApplicability.REQUIRED
            reason = "This core Scenario dimension is required by the compiled synthesis contract."
            trigger = ("CORE_SCENARIO_DIMENSION",)
        return ScenarioDimensionApplicabilityDecision(
            dimension=dimension, status=status, reason=reason,
            trigger_evidence=tuple(trigger), source_refs=tuple(refs),
        )


class ScenarioCoveragePlanner:
    """Create evidence-driven sibling coverage intent for one semantic group."""

    @staticmethod
    def _meaningful_signature(candidate: Any) -> tuple[Any, ...] | None:
        """Collapse atoms that carry the same coverage evidence.

        Lexical/BM25 differences are deliberately excluded: a small text-score
        change does not by itself establish a distinct engineering scenario.
        """
        scores = candidate.ranking_scores
        structured = tuple(
            bool(float(scores.get(name, 0.0))) for name in (
                "template_score", "mechanism_score", "action_score",
                "object_score", "traffic_relation_score", "causal_score",
            )
        )
        physical = candidate.method_semantics or {}
        speed_range = candidate.speed_range_kph
        supported = any(structured) or bool(physical) or speed_range is not None
        if not supported:
            return None
        return (
            structured,
            candidate.template_relationship,
            _json_text(physical) if physical else "",
            tuple(speed_range) if speed_range is not None else (),
        )

    @classmethod
    def plan(
        cls, *, query: dict[str, Any], candidate_sets: Iterable[ScenarioAtomCandidateSet],
    ) -> ScenarioCoveragePlan:
        sets = tuple(candidate_sets)
        fixed = tuple(
            item.dimension for item in sets
            if item.binding_decision.parent_atom_id and not item.binding_decision.refinable
        )
        actions = set(map(str, query.get("action_categories", [])))
        traffic = set(map(str, query.get("traffic_relations", [])))
        road = set(map(str, query.get("road_relations", [])))
        objects = set(map(str, query.get("object_categories", [])))
        applicable = {
            item.dimension for item in sets
            if item.applicability.status is not ScenarioDimensionApplicability.NOT_APPLICABLE
            and item.dimension not in fixed
        }
        if "ROAD_SLOPE" in road and "ACTION_HOLD" in actions:
            ordered_primary = ("ROAD", "EGO_X_ROAD", "EGO_ACTION")
        elif "ACTION_REVERSE" in actions and "OBJECT_PEDESTRIAN" in objects:
            ordered_primary = ("EGO_ACTION", "OBJECT", "TRAFFIC_PATTERN")
        elif traffic:
            ordered_primary = ("TRAFFIC_PATTERN", "OBJECT", "EGO_ACTION")
        elif objects:
            ordered_primary = ("OBJECT", "EGO_ACTION", "EGO_DYNAMICS")
        else:
            ordered_primary = ("EGO_ACTION", "EGO_DYNAMICS", "OBJECT")
        primary = tuple(item for item in ordered_primary if item in applicable)
        if not primary:
            primary = tuple(item.dimension for item in sets if item.dimension in applicable)[:1]
        secondary = tuple(
            item for item in ("WHERE", "ROAD", "EGO_X_ROAD", "EGO_DYNAMICS", "OBJECT")
            if item in applicable and item not in primary
        )
        prohibited = tuple(item for item in ("WHERE", "ROAD") if item in secondary)
        supported_counts = {
            item.dimension: len({
                signature for candidate in item.candidates
                if (signature := cls._meaningful_signature(candidate)) is not None
            })
            for item in sets if item.dimension in primary
        }
        # Every additional sibling must be supported along a primary mechanism
        # axis. Using the strongest single axis prevents unrelated shortlist
        # multiplicity from manufacturing a third scenario.
        desired = max(1, min(3, max(supported_counts.values(), default=1)))
        labels = ("typical", "boundary", "extreme")[:desired]
        definitions = {
            "typical": "Most representative valid scenario for the active failure mechanism.",
            "boundary": "Project-valid scenario near a relevant Method, ODD, or interaction boundary.",
            "extreme": "More demanding but still project-valid scenario for the same mechanism.",
        }
        intents = []
        for index, label in enumerate(labels, start=1):
            required_axes = (
                () if label == "typical" else tuple(
                    dimension for dimension in primary
                    if supported_counts.get(dimension, 0) >= index
                )
            )
            intents.append({
                "coverage_label": label,
                "engineering_semantics": definitions[label],
                "required_variation_dimensions": list(required_axes),
                "supported_primary_signature_counts": supported_counts,
                "risk_classification_objective": False,
            })
        mechanism_parts = [
            *sorted(actions), *sorted(traffic), *sorted(road), *sorted(objects),
            str(query.get("failure_type", "")), str(query.get("guideword", "")),
        ]
        mechanism = " | ".join(item for item in mechanism_parts if item) or "SOURCE_BOUNDED_SCENARIO_MECHANISM"
        return ScenarioCoveragePlan(
            active_mechanism=mechanism, fixed_dimensions=fixed,
            primary_variation_dimensions=primary,
            secondary_variation_dimensions=secondary,
            prohibited_trivial_only_dimensions=prohibited,
            desired_variant_count=desired, variant_intents=tuple(intents),
            source_refs=tuple(dict.fromkeys(map(str, query.get("source_refs", [])))) or ("PARENT.scenario",),
        )


class ScenarioCandidateRanker:
    """Structured scoring plus a compact Okapi BM25 lexical component."""

    @staticmethod
    def _bm25(query_tokens: Iterable[str], document: str, corpus: Iterable[str]) -> float:
        query = set(query_tokens)
        docs = [list(_tokens(value)) for value in corpus]
        target = list(_tokens(document))
        if not query or not target or not docs:
            return 0.0
        average_length = sum(map(len, docs)) / len(docs) or 1.0
        counts = Counter(target)
        score = 0.0
        for token in query:
            frequency = counts[token]
            if not frequency:
                continue
            containing = sum(token in item for item in docs)
            inverse = math.log(1.0 + (len(docs) - containing + 0.5) / (containing + 0.5))
            score += inverse * (frequency * 2.2) / (
                frequency + 1.2 * (0.25 + 0.75 * len(target) / average_length)
            )
        return round(score, 6)

    @staticmethod
    def atom_categories(atom: dict[str, Any]) -> set[str]:
        categories = _explicit_categories(str(atom.get("label", "")))
        semantics = atom.get("physical_semantics", {})
        semantics = semantics if isinstance(semantics, dict) else {}
        obj = semantics.get("object", {})
        obj = obj if isinstance(obj, dict) else {}
        if normalized := _normalize_object(obj.get("type", "")):
            categories.add(normalized)
        return categories

    def score(
        self, *, dimension: str, atom: dict[str, Any], query: dict[str, Any],
        fm_template: dict[str, Any], corpus_labels: Iterable[str], odd_passed: bool,
    ) -> tuple[dict[str, float], str]:
        categories = self.atom_categories(atom)
        actions = set(map(str, query.get("action_categories", [])))
        objects = set(map(str, query.get("object_categories", [])))
        traffic = set(map(str, query.get("traffic_relations", [])))
        road = set(map(str, query.get("road_relations", [])))
        road.update(map(str, query.get("odd_road_categories", [])))
        locations = set(map(str, query.get("location_categories", [])))
        action_score = float(bool(categories & actions))
        object_score = float(bool(categories & objects))
        traffic_score = float(bool(categories & traffic))
        semantic_similarity_score = float(
            bool(categories & locations) if dimension == "WHERE" else
            bool(categories & road) if dimension in {"ROAD", "EGO_X_ROAD"} else
            False
        )
        label = str(atom.get("label", ""))
        failure_tokens = set(_tokens(str(query.get("failure_type", ""))))
        guideword_tokens = set(_tokens(str(query.get("guideword", ""))))
        mechanism_score = float(bool((failure_tokens | guideword_tokens) & set(_tokens(label))))
        lexical_score = self._bm25(query.get("query_tokens", []), label, corpus_labels)
        causal_score = self._bm25(query.get("causal_tokens", []), label, corpus_labels)

        template_score = 0.0
        active_option = fm_template.get("active_option", {})
        options = [active_option] if isinstance(active_option, dict) and active_option else fm_template.get("source_governed_constraints", [])
        for option in options if isinstance(options, list) else []:
            if not isinstance(option, dict):
                continue
            expected = _normalize_object(option.get("obj_type", ""))
            if expected and expected in categories:
                template_score = 1.0
                break
        odd_score = 1.0 if odd_passed and dimension in {"WHERE", "ROAD", "EGO_X_ROAD", "EGO_DYNAMICS"} else 0.0
        scores = {
            "template_score": template_score,
            "mechanism_score": mechanism_score,
            "action_score": action_score,
            "object_score": object_score,
            "traffic_relation_score": traffic_score,
            "odd_score": odd_score,
            "causal_score": causal_score,
            "lexical_score": lexical_score,
            "semantic_similarity_score": semantic_similarity_score,
        }
        scores["final_rank_score"] = round(
            5.0 * template_score + 4.0 * traffic_score + 3.0 * action_score
            + 3.0 * object_score + 3.0 * semantic_similarity_score
            + 2.0 * mechanism_score
            + odd_score + causal_score + lexical_score,
            6,
        )
        evidence = ",".join(key for key, value in scores.items() if key != "final_rank_score" and value)
        return scores, f"structured/BM25 rank evidence={evidence or 'hard_filter_only'}"


class ScenarioVariantDiversityValidator:
    """Reject sibling sets whose differences do not satisfy their coverage plan."""

    @staticmethod
    def reasons(
        plan: ScenarioCoveragePlan, selections: Iterable[dict[str, tuple[str, ...]]],
    ) -> list[str]:
        variants = tuple(selections)
        if len(variants) <= 1:
            return []
        dimensions = sorted({key for item in variants for key in item})
        primary = set(plan.primary_variation_dimensions)
        prohibited = set(plan.prohibited_trivial_only_dimensions)
        reasons = []
        for left_index, left in enumerate(variants):
            for right in variants[left_index + 1:]:
                pair_varying = {
                    dimension for dimension in dimensions
                    if tuple(left.get(dimension, ())) != tuple(right.get(dimension, ()))
                }
                if not pair_varying & primary or (
                    pair_varying and pair_varying <= prohibited
                ):
                    reasons.append("TRIVIAL_VARIANT_DIVERSITY")
        baseline = variants[0]
        for variant, intent in zip(variants[1:], plan.variant_intents[1:]):
            required = set(map(str, intent.get("required_variation_dimensions", [])))
            if required and not any(
                tuple(baseline.get(dimension, ()))
                != tuple(variant.get(dimension, ()))
                for dimension in required
            ):
                reasons.append("TRIVIAL_VARIANT_DIVERSITY")
        return sorted(set(reasons))


__all__ = [
    "ScenarioBindingPolicy", "ScenarioCandidateRanker", "ScenarioCoveragePlanner",
    "ScenarioDimensionApplicabilityService", "ScenarioSemanticQueryBuilder",
    "ScenarioVariantDiversityValidator",
]

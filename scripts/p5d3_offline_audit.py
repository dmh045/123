"""Generate the zero-Provider P5-D3 selector audit artifacts.

This script operates on the accepted parent checkpoint and the compiled Method
contract.  It never constructs an LLM client and never writes a runtime run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.services.analysis.scenario_ranking_recall import (
    independent_evidence_tier, top_k_recall_audit,
)
from hara_agent.services.analysis.scenario_selection_quality import (
    ScenarioCandidateRanker,
)
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow.checkpoints import CheckpointRepository
from hara_agent.workflow.scenario_synthesis import ScenarioSynthesisRunner


SOURCE_RUN = "hara-full-baseline-20260912-r1"
START_COMMIT = "abd61c0629644d605435c45910f606a0148ac4a5"
PRE_GAPS = ROOT / "output/P5D3_PreRepair_Traffic_Gaps.json"
CAP = 12
TRAFFIC_ROOT_CAUSE_CLASSES = (
    "TRUE_METHOD_GAP",
    "SEMANTIC_QUERY_FALSE_POSITIVE",
    "SEMANTIC_QUERY_OVERGENERALIZATION",
    "ATOM_CATEGORY_EXTRACTION_FALSE_NEGATIVE",
    "DIMENSION_MAPPING_ERROR",
    "HARD_FILTER_FALSE_NEGATIVE",
    "COMPOUND_ATOM_REPRESENTED_ELSEWHERE",
    "FM_TEMPLATE_MAPPING_GAP",
    "SOURCE_ONTOLOGY_AMBIGUITY",
    "UNRESOLVED_NEEDS_ENGINEERING_REVIEW",
)
HISTORICAL = {
    "r3_synthesis_checkpoint": (
        ROOT / "runtime/agent/hara-full-baseline-20260912-r1-synthesis-r3.checkpoint.json",
        "2DBBCBFCF7AECC09332A8BBC2642513D98A2558E1CD33305F432415351E7D357",
    ),
    "causal_r2_provider_trace": (
        ROOT / "runtime/review/hara-full-baseline-20260912-r1-synthesis-r3-causal-r2/causal_revalidation_provider_trace.json",
        "8359E7A35EB4DF8E77D9E16D2CAD6151D4327CEC6D7EA57B48603BA159E25BC7",
    ),
    "risk_rescore_r2_checkpoint": (
        ROOT / "runtime/agent/hara-full-baseline-20260912-r1-synthesis-r3-causal-r2-risk-rescore-r2.checkpoint.json",
        "56150B23AA487FA780D1780E2F250035618DC057CFBA5F9F6F594916E57C0100",
    ),
}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: Any) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _method():
    report = TemplateRoleCompiler().compile_method(
        ROOT / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    return YamlBaselineCompiler().compile(
        ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )


def _identity(value: Any) -> tuple[str, str, str]:
    if isinstance(value, dict):
        return (
            str(value.get("malfunction_id", "")),
            str(value.get("parent_scenario_id", "")),
            str(value.get("hazardous_event_id", "")),
        )
    return (
        value.malfunction_id, value.parent_scenario_id, value.hazardous_event_id,
    )


def _source_value(item: dict[str, Any], ref: str) -> Any:
    malfunction = item.get("malfunction", {})
    causal = item.get("causal_assessment", {})
    parent = item.get("parent_scenario", {})
    facts = parent.get("facts", {}) if isinstance(parent, dict) else {}
    values = {
        "MF.description": malfunction.get("description", ""),
        "MF.functional_effect": malfunction.get("functional_effect", ""),
        "MF.vehicle_level_hazard": malfunction.get("vehicle_level_hazard", ""),
        "HE.hazardous_event": causal.get("hazardous_event", ""),
        "CAUSAL.summary": causal.get("causal_chain", []),
        "PARENT.scenario": parent.get("operating_scenario", ""),
    }
    if ref.startswith("PARENT.facts."):
        return facts.get(ref.rsplit(".", 1)[-1], "")
    return values.get(ref, "")


def _independent_evidence(
    *, atom: dict[str, Any], dimension: str, query: dict[str, Any],
    candidate_set: Any, scores: dict[str, float],
) -> list[str]:
    classes: list[str] = []
    atom_id = str(atom.get("atom_id", ""))
    authority = candidate_set.binding_decision.authority.value
    if candidate_set.binding_decision.parent_atom_id == atom_id:
        classes.append("EXACT_BINDING")
    if authority == "APPROVED_ALIAS" and candidate_set.binding_decision.parent_atom_id == atom_id:
        classes.append("APPROVED_ALIAS")
    if dimension == "OBJECT" and scores.get("template_score"):
        classes.append("FM_TEMPLATE_SOURCE_MATCH")

    sources = ScenarioCandidateRanker.atom_category_sources(atom)
    categories = set(sources)
    relevant_categories = (
        set(map(str, query.get("location_categories", [])))
        if dimension == "WHERE" else
        set(map(str, query.get("road_relations", [])))
        | set(map(str, query.get("odd_road_categories", [])))
        if dimension in {"ROAD", "EGO_X_ROAD"} else
        set(map(str, query.get("object_categories", [])))
        if dimension == "OBJECT" else
        set(map(str, query.get("traffic_relations", [])))
        if dimension == "TRAFFIC_PATTERN" else
        set(map(str, query.get("action_categories", [])))
        | set(map(str, query.get("traffic_relations", [])))
        if dimension == "EGO_ACTION" else
        set(map(str, query.get("action_categories", [])))
        if dimension == "EGO_DYNAMICS" else
        set()
    )
    structured = set(map(str, query.get("structured_source_categories", [])))
    if categories & relevant_categories & structured:
        classes.append("EXACT_STRUCTURED_SOURCE_MATCH")

    matched = categories & relevant_categories
    if matched:
        classes.append("FIELD_CORRECT_CATEGORY_MATCH")
    if any(sources.get(category, "").startswith("physical_semantics") for category in matched):
        classes.append("METHOD_PHYSICAL_SEMANTICS_MATCH")

    evidence = query.get("explicit_category_evidence", {})
    evidence = evidence if isinstance(evidence, dict) else {}
    explicit_refs = {
        str(ref) for category in matched for ref in evidence.get(category, [])
        if str(ref) in {
            "MF.vehicle_level_hazard", "HE.hazardous_event", "CAUSAL.summary",
        }
    }
    if explicit_refs:
        classes.append("EXPLICIT_HAZARD_OR_CAUSAL_MATCH")
    return list(dict.fromkeys(classes))


def _stage(reason: str) -> str:
    if reason == "DIMENSION_NOT_APPLICABLE":
        return "APPLICABILITY"
    if reason.startswith("ODD_"):
        return "ODD_PROJECT_FILTER"
    if reason.startswith("COMPOUND_"):
        return "COMPOUND_COMPATIBILITY"
    if reason.startswith("SEMANTIC_") or reason.startswith("FM_TEMPLATE_"):
        return "SEMANTIC_HARD_FILTER"
    if reason == "TOP_K_CUTOFF":
        return "TOP_K"
    if reason == "RETAINED":
        return "SHORTLIST"
    return "BINDING_AUTHORITY"


def _funnel_for_group(service, synthesis_input, parent) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    exact_locks = {
        item.dimension: item.binding_decision.parent_atom_id
        for item in synthesis_input.dimension_candidate_sets
        if item.binding_decision.parent_atom_id and not item.binding_decision.refinable
    }
    dimensions: dict[str, Any] = {}
    recall_rows: list[dict[str, Any]] = []
    for candidate_set in synthesis_input.dimension_candidate_sets:
        dimension = candidate_set.dimension
        catalog = [
            atom for atom in service.catalog
            if dimension in atom.get("filled_dimensions", [])
        ]
        decisions: list[dict[str, Any]] = []
        passed_atoms: list[dict[str, Any]] = []
        probe_corpus = [str(atom.get("label", "")) for atom in catalog]
        for atom in catalog:
            atom_id = str(atom.get("atom_id", ""))
            if dimension in exact_locks:
                passed = atom_id == exact_locks[dimension]
                reason = "PASS_EXACT_AUTHORITY" if passed else "EXACT_AUTHORITY_LOCK"
            else:
                passed, reason = service.hard_filter_decision(
                    dimension=dimension, atom=atom, parent=parent,
                    project_context=synthesis_input.project_context,
                    query=synthesis_input.structured_semantic_query,
                    applicability=candidate_set.applicability,
                    decision=candidate_set.binding_decision,
                    exact_locks=exact_locks,
                    fm_template=synthesis_input.fm_scenario_template,
                )
            probe_scores, _ = service.ranker.score(
                dimension=dimension, atom=atom,
                query=synthesis_input.structured_semantic_query,
                fm_template=synthesis_input.fm_scenario_template,
                corpus_labels=probe_corpus, odd_passed=True,
            )
            probe_classes = _independent_evidence(
                atom=atom, dimension=dimension,
                query=synthesis_input.structured_semantic_query,
                candidate_set=candidate_set, scores=probe_scores,
            )
            row = {
                "atom_id": atom_id,
                "canonical_atom_id": str(
                    atom.get("v2") or atom.get("v2_proper") or atom_id
                ),
                "label": str(atom.get("label", "")),
                "filled_dimensions": list(atom.get("filled_dimensions", [])),
                "physical_semantics": atom.get("physical_semantics", {}),
                "source_asset": str(atom.get("source_asset", "")),
                "source_rule": str(atom.get("source_rule", "")),
                "hard_filter_passed": passed,
                "exclusion_stage": "" if passed else _stage(reason),
                "exclusion_reason": "" if passed else reason,
                "independent_evidence": probe_classes,
                "independent_evidence_tier": independent_evidence_tier(
                    probe_classes
                ),
            }
            decisions.append(row)
            if passed:
                passed_atoms.append(atom)

        corpus = [str(atom.get("label", "")) for atom in passed_atoms]
        ranked: list[dict[str, Any]] = []
        for atom in passed_atoms:
            scores, _ = service.ranker.score(
                dimension=dimension, atom=atom,
                query=synthesis_input.structured_semantic_query,
                fm_template=synthesis_input.fm_scenario_template,
                corpus_labels=corpus, odd_passed=True,
            )
            classes = _independent_evidence(
                atom=atom, dimension=dimension,
                query=synthesis_input.structured_semantic_query,
                candidate_set=candidate_set, scores=scores,
            )
            ranked.append({
                "atom_id": str(atom.get("atom_id", "")),
                "label": str(atom.get("label", "")),
                **scores,
                "independent_evidence": classes,
                "independent_evidence_tier": independent_evidence_tier(classes),
            })
        ranked.sort(key=lambda item: service.ranker.rank_key(
            str(item["atom_id"]), item,
        ))
        actual_ids = {item.atom_id for item in candidate_set.candidates}
        by_id = {item["atom_id"]: item for item in decisions}
        for rank, row in enumerate(ranked, start=1):
            row["rank"] = rank
            row["shortlisted"] = row["atom_id"] in actual_ids
            target = by_id[row["atom_id"]]
            target["ranking"] = row
            if not row["shortlisted"]:
                target["exclusion_stage"] = "TOP_K" if rank > CAP else "COMPOUND_COMPATIBILITY"
                target["exclusion_reason"] = (
                    "TOP_K_CUTOFF" if rank > CAP else
                    "COMPOUND_CROSS_DIMENSION_INCOMPLETE"
                )
            else:
                target["exclusion_stage"] = ""
                target["exclusion_reason"] = ""

        recall = top_k_recall_audit(ranked, cap=CAP)
        recall.update({
            "malfunction_id": synthesis_input.malfunction_id,
            "parent_scenario_id": synthesis_input.parent_scenario_id,
            "hazardous_event_id": synthesis_input.hazardous_event_id,
            "semantic_group_id": synthesis_input.semantic_group_id,
            "dimension": dimension,
        })
        recall_rows.append(recall)
        reason_counts = Counter(
            item["exclusion_reason"] or "RETAINED" for item in decisions
        )
        dimensions[dimension] = {
            "catalog_size": len(catalog),
            "hard_filtered_pool_size": candidate_set.hard_filtered_pool_size,
            "generation_status": candidate_set.generation_status,
            "generation_reason": candidate_set.reason,
            "shortlist_truncated": candidate_set.shortlist_truncated,
            "applicability": candidate_set.applicability.to_dict(),
            "binding_authority": candidate_set.binding_decision.to_dict(),
            "stage_counts": {
                "catalog": len(catalog),
                "applicability": sum(
                    item["exclusion_stage"] != "APPLICABILITY" for item in decisions
                ),
                "odd_project_filter": sum(
                    item["exclusion_stage"] not in {"APPLICABILITY", "ODD_PROJECT_FILTER"}
                    for item in decisions
                ),
                "compound_compatibility": sum(
                    item["exclusion_stage"] not in {
                        "APPLICABILITY", "ODD_PROJECT_FILTER", "COMPOUND_COMPATIBILITY",
                    }
                    for item in decisions
                ),
                "semantic_hard_filter": len(passed_atoms),
                "scored_pool": len(ranked),
                "top_k_shortlist": len(actual_ids),
            },
            "reason_counts": dict(sorted(reason_counts.items())),
            "atoms": decisions,
            "top_k_recall": recall,
        }
    return {
        "malfunction_id": synthesis_input.malfunction_id,
        "parent_scenario_id": synthesis_input.parent_scenario_id,
        "hazardous_event_id": synthesis_input.hazardous_event_id,
        "semantic_group_id": synthesis_input.semantic_group_id,
        "dimensions": dimensions,
    }, recall_rows


def _triage_class(pre: dict[str, Any], current: Any) -> tuple[str, str, str]:
    query = pre["structured_semantic_query"]
    relations = set(map(str, query.get("traffic_relations", [])))
    evidence = query.get("explicit_category_evidence", {})
    if "TRAFFIC_REVERSE" in relations and not evidence.get("TRAFFIC_REVERSE"):
        return (
            "SEMANTIC_QUERY_OVERGENERALIZATION",
            "P5-D2 inferred TRAFFIC_REVERSE from reverse action plus any object; that does not prove a separate traffic relation.",
            "Remove the over-broad deterministic inference and retain the action/object evidence in their own dimensions.",
        )
    current_relations = set(map(
        str, current.structured_semantic_query.get("traffic_relations", [])
    ))
    if relations and not current_relations:
        return (
            "SEMANTIC_QUERY_FALSE_POSITIVE",
            "The previous query matched a traffic marker only as a substring or in a field whose semantic role does not establish a traffic relation.",
            "Use field-scoped, phrase-aware relation markers and keep the traffic dimension non-required.",
        )
    if relations and all(
        set(map(str, evidence.get(relation, []))) <= {"MF.description"}
        for relation in relations
    ):
        return (
            "SEMANTIC_QUERY_FALSE_POSITIVE",
            "A marker matched only inside malfunction description; no HE, hazard, causal, or structured relation supports it.",
            "Use field-scoped relation markers and keep the traffic dimension non-required.",
        )
    represented = current.structured_semantic_query.get(
        "traffic_relations_represented_elsewhere", {}
    )
    if relations and all(represented.get(relation) for relation in relations):
        return (
            "COMPOUND_ATOM_REPRESENTED_ELSEWHERE",
            "The compiled Method already represents the explicit relation with source-defined atoms in other dimensions.",
            "Use the existing compound/action representation; do not invent a duplicate TRAFFIC_PATTERN atom.",
        )
    return (
        "TRUE_METHOD_GAP",
        "The relation is explicit and no compatible source-defined Method representation was found.",
        "Keep METHOD_GAP fail-closed and request FuSa Method-owner review.",
    )


def _traffic_triage(pre_groups, current_by_key, funnel_by_key, service) -> dict[str, Any]:
    inventory = [
        {
            "atom_id": str(atom.get("atom_id", "")),
            "canonical_atom_id": str(
                atom.get("v2") or atom.get("v2_proper") or atom.get("atom_id", "")
            ),
            "label": str(atom.get("label", "")),
            "filled_dimensions": list(atom.get("filled_dimensions", [])),
            "physical_semantics": atom.get("physical_semantics", {}),
            "source_asset": str(atom.get("source_asset", "")),
            "source_rule": str(atom.get("source_rule", "")),
        }
        for atom in service.catalog if "TRAFFIC_PATTERN" in atom.get("filled_dimensions", [])
    ]
    records = []
    counts = Counter()
    for pre in pre_groups:
        key = _identity(pre)
        current = current_by_key[key]
        funnel = funnel_by_key[key]["dimensions"]["TRAFFIC_PATTERN"]
        root, rationale, action = _triage_class(pre, current)
        counts[root] += 1
        pre_query = pre["structured_semantic_query"]
        source_evidence = []
        for relation in pre_query.get("traffic_relations", []):
            refs = pre_query.get("explicit_category_evidence", {}).get(relation, [])
            for ref in refs:
                source_evidence.append({
                    "relation": relation,
                    "exact_source_field": ref,
                    "exact_source_phrase_or_value": _source_value(pre, ref),
                    "deterministic_inference_rule": "P5_D2_WHOLE_TEXT_MARKER",
                })
            if not refs:
                rule = (
                    "ACTION_REVERSE + any OBJECT => TRAFFIC_REVERSE"
                    if relation == "TRAFFIC_REVERSE" else
                    "OBJECT_VEHICLE + rear collision/position => TRAFFIC_FOLLOWING"
                    if relation == "TRAFFIC_FOLLOWING" else
                    "P5_D2_UNATTRIBUTED_DETERMINISTIC_INFERENCE"
                )
                source_evidence.append({
                    "relation": relation,
                    "exact_source_field": "DERIVED.P5D2",
                    "exact_source_phrase_or_value": {
                        "action_categories": pre_query.get("action_categories", []),
                        "object_categories": pre_query.get("object_categories", []),
                        "parent_collision_type": pre.get("parent_scenario", {})
                        .get("facts", {}).get("collision_type", ""),
                        "parent_object_position": pre.get("parent_scenario", {})
                        .get("facts", {}).get("object_position", ""),
                    },
                    "deterministic_inference_rule": rule,
                })
        related = set(map(str, pre_query.get("traffic_relations", []))) | set(
            map(str, current.structured_semantic_query.get("traffic_relations", []))
        )
        potentially_relevant = []
        for atom in funnel["atoms"]:
            source_atom = service.by_id[atom["atom_id"]]
            if ScenarioCandidateRanker.atom_categories(source_atom) & related:
                potentially_relevant.append(atom)
        records.append({
            "malfunction_id": current.malfunction_id,
            "parent_scenario_id": current.parent_scenario_id,
            "hazardous_event_id": current.hazardous_event_id,
            "malfunction": current.malfunction.get("description", ""),
            "vehicle_level_hazard": current.malfunction.get("vehicle_level_hazard", ""),
            "hazardous_event": current.causal_assessment.get("hazardous_event", ""),
            "causal_summary": current.causal_assessment.get("causal_chain", []),
            "classified_semantic_query_before": {
                field: pre_query.get(field, []) for field in (
                    "action_categories", "object_categories", "traffic_relations",
                    "road_relations",
                )
            },
            "classified_semantic_query_after": {
                field: current.structured_semantic_query.get(field, []) for field in (
                    "action_categories", "object_categories", "traffic_relations",
                    "road_relations",
                )
            },
            "traffic_relation_evidence": source_evidence,
            "traffic_pattern_applicability_before": next(
                item for item in pre["dimension_candidate_sets"]
                if item["dimension"] == "TRAFFIC_PATTERN"
            )["applicability"],
            "traffic_pattern_applicability_after": funnel["applicability"],
            "full_traffic_pattern_method_atom_inventory": inventory,
            "hard_filter_survivors": [
                item for item in funnel["atoms"] if item["hard_filter_passed"]
            ],
            "ranked_survivors": [
                item for item in funnel["atoms"] if item.get("ranking")
            ],
            "potentially_relevant_excluded_atoms": potentially_relevant,
            "represented_elsewhere": current.structured_semantic_query.get(
                "traffic_relations_represented_elsewhere", {}
            ),
            "fm_template_evidence": current.fm_scenario_template,
            "root_cause_class": root,
            "root_cause_rationale": rationale,
            "recommended_action": action,
        })
    return {
        "artifact_version": "p5-d3-traffic-method-gap-triage-v1",
        "starting_commit": START_COMMIT,
        "provider_calls": 0,
        "initial_required_method_gaps": len(records),
        "root_cause_counts": {
            name: counts.get(name, 0) for name in TRAFFIC_ROOT_CAUSE_CLASSES
        },
        "records": records,
    }


def _cluster_metrics(inputs) -> dict[str, Any]:
    rows = []
    largest = 0
    for dimension in ("EGO_ACTION", "OBJECT", "TRAFFIC_PATTERN", "EGO_DYNAMICS"):
        for limit in (1, 3, CAP):
            clusters: dict[tuple[str, ...], list[Any]] = defaultdict(list)
            for item in inputs:
                candidate_set = next(
                    value for value in item.dimension_candidate_sets
                    if value.dimension == dimension
                )
                key = tuple(candidate.atom_id for candidate in candidate_set.candidates[:limit])
                if key:
                    clusters[key].append(item)
            repeated = [values for values in clusters.values() if len(values) > 1]
            cross = [
                values for values in repeated
                if len({item.malfunction_id for item in values}) > 1
            ]
            biggest = max(map(len, cross), default=0)
            largest = max(largest, biggest)
            rows.append({
                "dimension": dimension, "top_n": limit,
                "identical_shortlist_clusters": len(repeated),
                "cross_malfunction_identical_clusters": len(cross),
                "largest_cross_malfunction_cluster": biggest,
            })
    return {"rows": rows, "largest_mechanism_shortlist_convergence_cluster": largest}


def _stratum(item) -> str:
    query = item.structured_semantic_query
    objects = set(query.get("object_categories", []))
    actions = set(query.get("action_categories", []))
    traffic = set(query.get("traffic_relations", []))
    if "OBJECT_OCCUPANT" in objects:
        return "occupant"
    if "ACTION_REVERSE" in actions:
        return "reversing"
    if "OBJECT_STATIC" in objects:
        return "static_obstacle"
    if actions & {"ACTION_STOP", "ACTION_HOLD"}:
        return "stopping_holding"
    if objects & {"OBJECT_PEDESTRIAN", "OBJECT_CYCLIST"}:
        return "vru_pedestrian"
    if traffic or "OBJECT_VEHICLE" in objects:
        return "vehicle_traffic"
    return "miscellaneous"


def _ranking_sample(inputs, funnel_by_key, target: int = 105) -> list[dict[str, Any]]:
    by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in sorted(inputs, key=_identity):
        group = funnel_by_key[_identity(item)]
        for dimension in ("EGO_ACTION", "OBJECT", "TRAFFIC_PATTERN", "EGO_DYNAMICS"):
            candidates = [
                atom["ranking"] for atom in group["dimensions"][dimension]["atoms"]
                if atom.get("ranking")
            ]
            if not candidates:
                continue
            row = dict(candidates[0])
            row.update({
                "malfunction_id": item.malfunction_id,
                "parent_scenario_id": item.parent_scenario_id,
                "hazardous_event_id": item.hazardous_event_id,
                "dimension": dimension,
                "sample_stratum": _stratum(item),
            })
            by_stratum[_stratum(item)].append(row)
    selected: list[dict[str, Any]] = []
    used: set[tuple[str, str]] = set()
    for name in (
        "vehicle_traffic", "vru_pedestrian", "reversing", "static_obstacle",
        "occupant", "stopping_holding", "miscellaneous",
    ):
        for row in by_stratum.get(name, [])[:15]:
            selected.append(row)
            used.add((row["parent_scenario_id"], row["dimension"]))
    if len(selected) < target:
        for rows in by_stratum.values():
            for row in rows:
                key = (row["parent_scenario_id"], row["dimension"])
                if key in used:
                    continue
                selected.append(row)
                used.add(key)
                if len(selected) >= target:
                    break
            if len(selected) >= target:
                break
    if len(selected) < 100:
        raise RuntimeError(f"P5-D3 ranking sample contains only {len(selected)} decisions")
    return selected[:target]


def _coverage_metrics(inputs: Iterable[Any]) -> dict[str, Any]:
    items = tuple(inputs)
    return {
        "variant_count_distribution": {
            str(count): sum(item.coverage_plan.desired_variant_count == count for item in items)
            for count in (1, 2, 3)
        },
        "primary_axis_distribution": dict(sorted(Counter(
            dimension for item in items
            for dimension in item.coverage_plan.primary_variation_dimensions
        ).items())),
        "secondary_axis_distribution": dict(sorted(Counter(
            dimension for item in items
            for dimension in item.coverage_plan.secondary_variation_dimensions
        ).items())),
    }


def _pre_coverage_metrics(pre_groups: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = tuple(pre_groups)
    return {
        "variant_count_distribution": dict(sorted(Counter(
            str(item["coverage_plan"]["desired_variant_count"]) for item in items
        ).items())),
        "primary_axis_distribution": dict(sorted(Counter(
            dimension for item in items
            for dimension in item["coverage_plan"]["primary_variation_dimensions"]
        ).items())),
    }


def _generic_intensity_groups(inputs) -> list[dict[str, Any]]:
    markers = (
        "deceler", "brak", "emergency", "slow", "speed", "km/h", "v <", "v >",
        "normal distance", "short distance",
    )
    records = []
    for item in inputs:
        if "EGO_DYNAMICS" not in item.coverage_plan.primary_variation_dimensions:
            continue
        dynamics = next(
            value for value in item.dimension_candidate_sets
            if value.dimension == "EGO_DYNAMICS"
        )
        family = [
            candidate.atom_id for candidate in dynamics.candidates
            if any(marker in candidate.label.casefold() for marker in markers)
        ]
        if len(family) >= 3:
            records.append({
                "malfunction_id": item.malfunction_id,
                "parent_scenario_id": item.parent_scenario_id,
                "hazardous_event_id": item.hazardous_event_id,
                "primary_axes": list(item.coverage_plan.primary_variation_dimensions),
                "generic_family_atom_ids": family,
                "warning": "GENERIC_INTENSITY_AXIS_RISK",
            })
    return records


def _md_table(rows: list[list[Any]], headers: list[str]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(map(str, row)) + " |" for row in rows)
    return "\n".join(lines)


def main() -> None:
    if not PRE_GAPS.is_file():
        raise RuntimeError(f"Missing pre-repair gap snapshot: {PRE_GAPS}")
    pre_payload = json.loads(PRE_GAPS.read_text(encoding="utf-8"))
    pre_groups = pre_payload["groups"]
    if len(pre_groups) != 101:
        raise RuntimeError(f"Expected 101 pre-repair traffic gaps, got {len(pre_groups)}")

    method = _method()
    state = CheckpointRepository(ROOT / "runtime/agent").load(SOURCE_RUN)
    runner = ScenarioSynthesisRunner(
        method=method, client=None, run_dir=ROOT / "runtime/agent",
        review_root=ROOT / "runtime/review",
    )
    inputs, scenarios, _malfunctions, _assessments, preparation = runner._prepare(state)
    current_by_key = {_identity(item): item for item in inputs}

    funnel_groups = []
    recall_rows = []
    for synthesis_input in inputs:
        group, recalls = _funnel_for_group(
            runner.synthesis, synthesis_input,
            scenarios[synthesis_input.parent_scenario_id],
        )
        funnel_groups.append(group)
        recall_rows.extend(recalls)
    funnel_by_key = {_identity(item): item for item in funnel_groups}

    triage = _traffic_triage(
        pre_groups, current_by_key, funnel_by_key, runner.synthesis,
    )
    _write_json(ROOT / "output/P5D3_Traffic_Method_Gap_Triage.json", triage)
    triage_rows = [
        [
            item["malfunction_id"], item["parent_scenario_id"],
            ", ".join(item["classified_semantic_query_before"]["traffic_relations"]) or "-",
            item["root_cause_class"],
        ]
        for item in triage["records"]
    ]
    _write(ROOT / "output/P5D3_Traffic_Method_Gap_Triage.md", "\n".join((
        "# P5-D3 TRAFFIC_PATTERN Gap Triage", "",
        "All 101 P5-D2 REQUIRED/METHOD_GAP groups were classified with source-role evidence. No Provider was called and no Method atom was added.", "",
        "## Root-cause counts", "",
        _md_table(
            [[key, value] for key, value in triage["root_cause_counts"].items()],
            ["Root cause", "Groups"],
        ), "", "## Per-group classification", "",
        _md_table(triage_rows, ["Malfunction", "Parent", "P5-D2 relation", "Primary root cause"]),
        "",
    )))

    true_gaps = [
        item for item in triage["records"]
        if item["root_cause_class"] == "TRUE_METHOD_GAP"
    ]
    _write(ROOT / "output/P5D3_True_Method_Gap_Proposals.md", "\n".join((
        "# P5-D3 True Method Gap Proposals", "",
        f"Confirmed TRUE_METHOD_GAP groups: **{len(true_gaps)}**.", "",
        (
            "No new Method content is proposed. Every initially missing traffic relation was either a classification defect or already represented by source-defined Method atoms in another dimension."
            if not true_gaps else
            "Each entry below remains fail-closed pending FuSa Method-owner authority."
        ), "",
        *(
            f"- `{item['malfunction_id']}` / `{item['parent_scenario_id']}`: {item['root_cause_rationale']}"
            for item in true_gaps
        ),
    )))

    funnel = {
        "artifact_version": "p5-d3-candidate-funnel-audit-v1",
        "source_run_id": SOURCE_RUN,
        "provider_calls": 0,
        "parent_groups": len(inputs),
        "candidate_cap_per_dimension": CAP,
        "stage_order": [
            "catalog", "applicability", "binding_authority",
            "odd_project_filter", "compound_compatibility",
            "semantic_hard_filter", "scored_pool", "top_k_shortlist",
        ],
        "groups": funnel_groups,
    }
    _write_json(ROOT / "output/P5D3_Candidate_Funnel_Audit.json", funnel)

    hard_false_negatives = []
    for group in funnel_groups:
        for dimension, value in group["dimensions"].items():
            for atom in value["atoms"]:
                ranking = atom.get("ranking", {})
                if (
                    atom.get("exclusion_stage") == "SEMANTIC_HARD_FILTER"
                    and str(atom.get("exclusion_reason", "")).startswith("SEMANTIC_")
                    and int(atom.get("independent_evidence_tier", 0)) >= 2
                ):
                    hard_false_negatives.append({
                        **{key: group[key] for key in (
                            "malfunction_id", "parent_scenario_id", "hazardous_event_id",
                        )},
                        "dimension": dimension, "atom": atom,
                    })

    recall_risks = [item for item in recall_rows if item["top_k_recall_risk"]]
    convergence = _cluster_metrics(inputs)
    recall = {
        "artifact_version": "p5-d3-ranking-recall-audit-v1",
        "provider_calls": 0,
        "parent_groups": len(inputs),
        "truncated_parent_groups": sum(any(
            candidate_set.shortlist_truncated
            for candidate_set in item.dimension_candidate_sets
        ) for item in inputs),
        "top_k_recall_risk_groups": len({
            (item["malfunction_id"], item["parent_scenario_id"], item["hazardous_event_id"])
            for item in recall_risks
        }),
        "top_k_recall_risk_decisions": len(recall_risks),
        "source_strong_candidates_below_cutoff": sum(
            item["source_strong_candidates_below_cutoff"] for item in recall_rows
        ),
        "hard_filter_false_negatives": len(hard_false_negatives),
        "recall_decisions": recall_rows,
        "hard_filter_false_negative_records": hard_false_negatives,
        "shortlist_convergence": convergence,
    }
    _write(ROOT / "output/P5D3_Ranking_Recall_Audit.md", "\n".join((
        "# P5-D3 Ranking Recall Audit", "",
        f"- Truncated parent groups: {recall['truncated_parent_groups']}",
        f"- Top-K recall-risk groups: {recall['top_k_recall_risk_groups']}",
        f"- Source-strong candidates below cutoff: {recall['source_strong_candidates_below_cutoff']}",
        f"- Hard-filter false negatives: {recall['hard_filter_false_negatives']}",
        f"- Largest mechanism-shortlist convergence cluster: {convergence['largest_mechanism_shortlist_convergence_cluster']}",
        "", "## Convergence", "",
        _md_table([
            [row["dimension"], row["top_n"], row["identical_shortlist_clusters"],
             row["cross_malfunction_identical_clusters"], row["largest_cross_malfunction_cluster"]]
            for row in convergence["rows"]
        ], ["Dimension", "Top N", "Identical clusters", "Cross-MF clusters", "Largest cross-MF cluster"]),
        "",
        "The recall decision uses independent source-evidence classes; final_rank_score alone is not treated as gold engineering accuracy.",
    )))

    sample = _ranking_sample(inputs, funnel_by_key)
    sample_order = (
        "vehicle_traffic", "vru_pedestrian", "reversing", "static_obstacle",
        "occupant", "stopping_holding", "miscellaneous",
    )
    eligible_sample_strata = Counter(_stratum(item) for item in inputs)
    selected_sample_strata = Counter(item["sample_stratum"] for item in sample)
    _write(ROOT / "output/P5D3_Ranking_Decision_Sample.md", "\n".join((
        "# P5-D3 Ranking Decision Sample", "",
        f"Stratified decisions: **{len(sample)}**. Provider calls: **0**.", "",
        _md_table([
            [
                name, eligible_sample_strata.get(name, 0),
                selected_sample_strata.get(name, 0),
                (
                    "No field-correct source group; no decision fabricated."
                    if not eligible_sample_strata.get(name, 0) else "Sampled"
                ),
            ]
            for name in sample_order
        ], ["Requested stratum", "Eligible groups", "Sample decisions", "Disposition"]), "",
        _md_table([
            [
                row["sample_stratum"], row["malfunction_id"], row["dimension"],
                row["rank"], row["atom_id"], row["template_score"],
                row["mechanism_score"], row["action_score"], row["object_score"],
                row["traffic_relation_score"], row["category_context_score"],
                row["odd_score"], row["causal_score"], row["lexical_score"],
                row["final_rank_score"], row["independent_evidence_tier"],
            ]
            for row in sample
        ], [
            "Stratum", "Group MF", "Dimension", "Rank", "Atom", "Template",
            "Mechanism", "Action", "Object", "Traffic", "Road/location",
            "ODD", "Causal BM25", "Lexical BM25", "Final", "Source tier",
        ]), "",
    )))

    current_traffic_sets = [
        next(value for value in item.dimension_candidate_sets if value.dimension == "TRAFFIC_PATTERN")
        for item in inputs
    ]
    remaining_gap_records = []
    for item in inputs:
        funnel_group = funnel_by_key[_identity(item)]
        for candidate_set in item.dimension_candidate_sets:
            if candidate_set.generation_status != "METHOD_GAP":
                continue
            funnel_dimension = funnel_group["dimensions"][candidate_set.dimension]
            actions = set(map(
                str, item.structured_semantic_query.get("action_categories", [])
            ))
            traffic = set(map(
                str, item.structured_semantic_query.get("traffic_relations", [])
            ))
            if (
                candidate_set.dimension == "OBJECT"
                and "OBJECT_STATIC" in candidate_set.applicability.trigger_evidence
            ):
                root_cause = "SOURCE_METHOD_GAP_STATIC_OBJECT"
                recommendation = (
                    "Keep fail-closed and ask the FuSa Method owner whether a "
                    "static-object atom is source-authorized."
                )
            elif (
                candidate_set.dimension == "EGO_ACTION"
                and not actions and not traffic
            ):
                root_cause = "SOURCE_ONTOLOGY_AMBIGUITY_NO_ACTIVE_ACTION"
                recommendation = (
                    "Keep fail-closed and obtain source-level active-action semantics; "
                    "do not fill the core dimension by lexical fallback."
                )
            else:
                root_cause = "UNRESOLVED_NEEDS_ENGINEERING_REVIEW"
                recommendation = (
                    "Keep fail-closed and request FuSa Method-owner review; "
                    "do not widen the catalog or invent an atom."
                )
            remaining_gap_records.append({
                "malfunction_id": item.malfunction_id,
                "parent_scenario_id": item.parent_scenario_id,
                "hazardous_event_id": item.hazardous_event_id,
                "dimension": candidate_set.dimension,
                "applicability": candidate_set.applicability.to_dict(),
                "generation_status": candidate_set.generation_status,
                "generation_reason": candidate_set.reason,
                "structured_semantic_query": {
                    field: item.structured_semantic_query.get(field, [])
                    for field in (
                        "action_categories", "object_categories",
                        "traffic_relations", "road_relations",
                    )
                },
                "hard_filtered_pool_size": candidate_set.hard_filtered_pool_size,
                "hard_filter_reason_counts": funnel_dimension["reason_counts"],
                "root_cause": root_cause,
                "recommended_action": recommendation,
            })
    remaining_gap_counts = dict(sorted(Counter(
        item["dimension"] for item in remaining_gap_records
    ).items()))
    remaining_gap_root_causes = dict(sorted(Counter(
        item["root_cause"] for item in remaining_gap_records
    ).items()))
    remaining_gap_groups = sum(any(
        value.generation_status == "METHOD_GAP"
        for value in item.dimension_candidate_sets
    ) for item in inputs)
    provider_ready = sum(runner._provider_ready(item) for item in inputs)
    generic = _generic_intensity_groups(inputs)
    before_after = {
        "artifact_version": "p5-d3-selector-before-after-v1",
        "provider_calls": 0,
        "before": {
            "traffic_pattern_required": len(pre_groups),
            "traffic_pattern_method_gap": len(pre_groups),
            "provider_ready_groups": 311,
            "coverage": _pre_coverage_metrics(pre_groups),
        },
        "after": {
            "traffic_pattern_required": sum(
                item.applicability.status.value == "REQUIRED"
                for item in current_traffic_sets
            ),
            "traffic_pattern_method_gap": sum(
                item.generation_status == "METHOD_GAP" for item in current_traffic_sets
            ),
            "false_positive_required_removed": sum(
                value for key, value in triage["root_cause_counts"].items()
                if key in {"SEMANTIC_QUERY_FALSE_POSITIVE", "SEMANTIC_QUERY_OVERGENERALIZATION"}
            ),
            "represented_elsewhere_reclassified": triage["root_cause_counts"].get(
                "COMPOUND_ATOM_REPRESENTED_ELSEWHERE", 0
            ),
            "remaining_method_gap_groups_all_dimensions": remaining_gap_groups,
            "remaining_method_gap_decisions_by_dimension": remaining_gap_counts,
            "remaining_method_gap_root_causes": remaining_gap_root_causes,
            "remaining_method_gap_records": remaining_gap_records,
            "provider_ready_groups": provider_ready,
            "dimension_applicability_distribution": {
                dimension: dict(sorted(Counter(
                    next(
                        value for value in item.dimension_candidate_sets
                        if value.dimension == dimension
                    ).applicability.status.value
                    for item in inputs
                ).items()))
                for dimension in runner.synthesis.dimensions
            },
            "ego_dynamics_broad_refinable_count": sum(
                next(value for value in item.dimension_candidate_sets if value.dimension == "EGO_DYNAMICS")
                .binding_decision.refinable
                for item in inputs
            ),
            "average_hard_pool": round(sum(
                value.hard_filtered_pool_size for item in inputs
                for value in item.dimension_candidate_sets
            ) / (len(inputs) * 7), 3),
            "average_shortlist": round(sum(
                len(value.candidates) for item in inputs
                for value in item.dimension_candidate_sets
            ) / (len(inputs) * 7), 3),
            "truncated_groups": recall["truncated_parent_groups"],
            "top_k_recall_risk_groups": recall["top_k_recall_risk_groups"],
            "coverage": _coverage_metrics(inputs),
            "generic_intensity_axis_risk_groups": len(generic),
            "generic_intensity_axis_risk_records": generic,
        },
    }
    _write(ROOT / "output/P5D3_Selector_Before_After.md", "\n".join((
        "# P5-D3 Selector Before/After", "",
        _md_table([
            ["TRAFFIC_PATTERN REQUIRED", 101, before_after["after"]["traffic_pattern_required"]],
            ["TRAFFIC_PATTERN METHOD_GAP", 101, before_after["after"]["traffic_pattern_method_gap"]],
            ["Provider-ready groups", 311, provider_ready],
            ["All-dimension METHOD_GAP groups", 101, remaining_gap_groups],
            ["Top-K recall-risk groups", "not independently audited", recall["top_k_recall_risk_groups"]],
            ["Generic intensity-axis warnings", "not audited", len(generic)],
        ], ["Metric", "Before", "After"]), "",
        "## Remaining fail-closed Method gaps", "",
        _md_table(
            [[dimension, count] for dimension, count in remaining_gap_counts.items()],
            ["Dimension", "Decisions"],
        ), "",
        _md_table(
            [[cause, count] for cause, count in remaining_gap_root_causes.items()],
            ["Source-grounded disposition", "Decisions"],
        ), "",
        _md_table([
            [
                item["malfunction_id"], item["parent_scenario_id"],
                item["dimension"],
                ", ".join(item["applicability"]["trigger_evidence"]),
                item["hard_filtered_pool_size"], item["root_cause"],
            ]
            for item in remaining_gap_records
        ], ["Malfunction", "Parent", "Dimension", "Required evidence", "Hard pool", "Disposition"]), "",
        "## Applicability distribution after repair", "",
        _md_table([
            [
                dimension,
                counts.get("REQUIRED", 0), counts.get("OPTIONAL", 0),
                counts.get("NOT_APPLICABLE", 0),
            ]
            for dimension, counts in before_after["after"]["dimension_applicability_distribution"].items()
        ], ["Dimension", "Required", "Optional", "Not applicable"]), "",
        "## Ranking and coverage after repair", "",
        _md_table([
            ["EGO_DYNAMICS broad refinable", before_after["after"]["ego_dynamics_broad_refinable_count"]],
            ["Average hard pool", before_after["after"]["average_hard_pool"]],
            ["Average shortlist", before_after["after"]["average_shortlist"]],
            ["Truncated groups", before_after["after"]["truncated_groups"]],
            ["Top-K recall-risk groups", before_after["after"]["top_k_recall_risk_groups"]],
            ["1-variant plans", before_after["after"]["coverage"]["variant_count_distribution"]["1"]],
            ["2-variant plans", before_after["after"]["coverage"]["variant_count_distribution"]["2"]],
            ["3-variant plans", before_after["after"]["coverage"]["variant_count_distribution"]["3"]],
        ], ["Metric", "Value"]), "",
        "Primary axes: " + json.dumps(
            before_after["after"]["coverage"]["primary_axis_distribution"],
            ensure_ascii=False, sort_keys=True,
        ), "",
        "The after-state does not weaken a REQUIRED traffic relation: false relations were removed, while source-represented relations use their existing compound/action atoms. Newly exposed non-traffic Method gaps remain fail-closed.",
    )))

    baseline = """# P5-D3 Current Ranking Baseline

## Starting point

- GitHub commit: `abd61c0629644d605435c45910f606a0148ac4a5`
- Provider calls: `0`
- Candidate cap: `12` per dimension.
- P5-D2 tie-break: descending `final_rank_score`, then ascending `atom_id`.

## P5-D2 scalar formula verified from source

```text
final_rank_score =
    5 * template_score
  + 4 * traffic_relation_score
  + 3 * action_score
  + 3 * object_score
  + 3 * semantic_similarity_score
  + 2 * mechanism_score
  + 1 * odd_score
  + causal_score (BM25)
  + lexical_score (BM25)
```

The former `semantic_similarity_score` was only a binary road/location category match. It was not embedding or vector similarity.

## Hard-filter order verified

1. dimension membership;
2. per-group applicability;
3. ODD speed compatibility;
4. slope compatibility;
5. weather compatibility;
6. exact cross-dimension compound locks;
7. semantic category compatibility;
8. score and Top-12 cutoff;
9. cross-dimension compound completeness.

FM-template object compatibility contributes `template_score`; structured `physical_semantics.object.type` supplements label classification; query and causal tokens contribute separate BM25 values.

## P5-D3 corrected ranking contract

The misleading field is renamed to `category_context_score` with no alias. `structured_source_score` and `source_evidence_tier` expose provenance strength. Ordering is now lexicographic: source-evidence tier, then the unchanged scalar score within tier, then `atom_id`. BM25 can break ties within a source tier but cannot resurrect a hard-incompatible atom or outrank a stronger source tier.
"""
    _write(ROOT / "output/P5D3_Current_Ranking_Baseline.md", baseline)

    guard_records = []
    for name, (path, before_hash) in HISTORICAL.items():
        after_hash = _sha256(path)
        guard_records.append({
            "artifact": name, "path": str(path), "sha256_before": before_hash,
            "sha256_after": after_hash, "byte_identical": before_hash == after_hash,
        })
    guard = {
        "artifact_version": "p5-d3-historical-artifact-guard-v1",
        "provider_calls": 0,
        "synthesis_r4_executed": False,
        "records": guard_records,
        "byte_identical": all(item["byte_identical"] for item in guard_records),
    }
    _write_json(ROOT / "output/P5D3_Historical_Artifact_Guard.json", guard)

    final_state = (
        "NOT_READY_METHOD_GAPS" if remaining_gap_groups else
        "NOT_READY_CLASSIFICATION_PRECISION" if hard_false_negatives else
        "NOT_READY_RANKING_RECALL" if recall_risks else
        "READY_FOR_R4_SMOKE"
    )
    print(json.dumps({
        "provider_calls": 0,
        "synthesis_r4_executed": False,
        "initial_traffic_gaps": len(pre_groups),
        "traffic_root_causes": triage["root_cause_counts"],
        "traffic_required_after": before_after["after"]["traffic_pattern_required"],
        "remaining_method_gap_groups": remaining_gap_groups,
        "provider_ready_groups": provider_ready,
        "truncated_parent_groups": recall["truncated_parent_groups"],
        "top_k_recall_risk_groups": recall["top_k_recall_risk_groups"],
        "hard_filter_false_negatives": recall["hard_filter_false_negatives"],
        "source_strong_below_cutoff": recall["source_strong_candidates_below_cutoff"],
        "generic_intensity_axis_risk_groups": len(generic),
        "coverage": before_after["after"]["coverage"],
        "historical_byte_identical": guard["byte_identical"],
        "final_state": final_state,
        "preparation_stats": preparation["stats"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

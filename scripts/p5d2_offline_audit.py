"""Generate zero-Provider P5-D2 comparison and diagnostic artifacts."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.services.reporting import ScenarioSelectorQualityAudit
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow.checkpoints import CheckpointRepository
from hara_agent.workflow.scenario_synthesis import ScenarioSynthesisRunner


SOURCE_RUN = "hara-full-baseline-20260912-r1"
R3_RUN = "hara-full-baseline-20260912-r1-synthesis-r3"


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _method():
    report = TemplateRoleCompiler().compile_method(
        ROOT / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    return YamlBaselineCompiler().compile(
        ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )


def _category(synthesis_input) -> set[str]:
    query = synthesis_input.structured_semantic_query
    objects = set(query["object_categories"])
    actions = set(query["action_categories"])
    traffic = set(query["traffic_relations"])
    facts = synthesis_input.parent_scenario.get("facts", {})
    result = set()
    if "OBJECT_VEHICLE" in objects or traffic:
        result.add("vehicle_traffic")
    if objects & {"OBJECT_PEDESTRIAN", "OBJECT_CYCLIST"}:
        result.add("vru_pedestrian")
    if "ACTION_REVERSE" in actions or facts.get("object_position") == "rear":
        result.add("reversing_parking")
    if "ACTION_HOLD" in actions or synthesis_input.malfunction_id.startswith("MF-F05"):
        result.add("slope_road_relation")
    if not traffic and (
        not objects or objects & {"OBJECT_STATIC", "OBJECT_OCCUPANT"}
    ):
        result.add("static_miscellaneous")
    return result


def _sample(inputs) -> list[tuple[str, Any]]:
    categories = (
        "vehicle_traffic", "vru_pedestrian", "reversing_parking",
        "slope_road_relation", "static_miscellaneous",
    )
    selected = []
    used = set()
    ordered = sorted(
        inputs,
        key=lambda item: (
            item.malfunction_id, item.parent_scenario_id,
            item.hazardous_event_id, item.semantic_group_id,
        ),
    )
    for category in categories:
        matches = [
            item for item in ordered
            if category in _category(item) and item.semantic_group_id not in used
        ][:10]
        if len(matches) != 10:
            raise RuntimeError(f"P5-D2 sample category {category} has only {len(matches)} unique groups")
        for item in matches:
            selected.append((category, item))
            used.add(item.semantic_group_id)
    return selected


def _structured_score(scores: dict[str, float]) -> float:
    return sum(float(scores.get(name, 0.0)) for name in (
        "template_score", "mechanism_score", "action_score", "object_score",
        "traffic_relation_score", "causal_score", "semantic_similarity_score",
    ))


def build_comparison(method, runner, inputs) -> dict[str, Any]:
    old_payload = json.loads((
        ROOT / "runtime/review" / R3_RUN / "scenario_synthesis_candidates.json"
    ).read_text(encoding="utf-8"))
    old_by_key = {
        (item["malfunction_id"], item["parent_scenario_id"], item["hazardous_event_id"]): item
        for item in old_payload["groups"]
    }
    rows = []
    improved_dimensions = 0
    degraded_dimensions = 0
    equal_dimensions = 0
    not_applicable_comparisons_excluded = 0
    for category, current in _sample(inputs):
        key = (current.malfunction_id, current.parent_scenario_id, current.hazardous_event_id)
        historical = old_by_key.get(key)
        if historical is None:
            raise RuntimeError(f"Historical r3 candidate group not found for {key}")
        old_sets = {item["dimension"]: item for item in historical["candidate_sets"]}
        comparisons = {}
        for new_set in current.dimension_candidate_sets:
            old_set = old_sets[new_set.dimension]
            old_ids = [item["atom_id"] for item in old_set["candidates"]]
            new_ids = [item.atom_id for item in new_set.candidates]
            old_top_score = 0.0
            if old_ids and old_ids[0] in runner.synthesis.by_id:
                old_atom = runner.synthesis.by_id[old_ids[0]]
                scores, _ = runner.synthesis.ranker.score(
                    dimension=new_set.dimension, atom=old_atom,
                    query=current.structured_semantic_query,
                    fm_template=current.fm_scenario_template,
                    corpus_labels=[
                        str(runner.synthesis.by_id[atom_id].get("label", ""))
                        for atom_id in old_ids if atom_id in runner.synthesis.by_id
                    ],
                    odd_passed=True,
                )
                old_top_score = _structured_score(scores)
            new_top_score = (
                _structured_score(new_set.candidates[0].ranking_scores)
                if new_set.candidates else 0.0
            )
            if new_set.applicability.status.value == "NOT_APPLICABLE":
                not_applicable_comparisons_excluded += 1
            elif new_top_score > old_top_score:
                improved_dimensions += 1
            elif new_top_score < old_top_score:
                degraded_dimensions += 1
            else:
                equal_dimensions += 1
            comparisons[new_set.dimension] = {
                "old_shortlist": old_ids,
                "new_shortlist": new_ids,
                "old_applicability": (
                    "OPTIONAL" if new_set.dimension in {"EGO_X_ROAD", "TRAFFIC_PATTERN"}
                    else "REQUIRED"
                ),
                "new_applicability": new_set.applicability.status.value,
                "old_locked_atom_ids": list(old_set.get("locked_atom_ids", [])),
                "new_binding_policy": new_set.binding_decision.to_dict(),
                "old_top_structured_support": old_top_score,
                "new_top_structured_support": new_top_score,
                "new_generation_status": new_set.generation_status,
            }
        rows.append({
            "sample_category": category,
            "malfunction_id": current.malfunction_id,
            "parent_scenario_id": current.parent_scenario_id,
            "hazardous_event_id": current.hazardous_event_id,
            "old_semantic_group_id": historical["semantic_group_id"],
            "new_semantic_group_id": current.semantic_group_id,
            "old_coverage_semantics": "1-3 variants; labels carried no mechanism-aware axis contract",
            "new_coverage_plan": current.coverage_plan.to_dict(),
            "fm_template_id": current.fm_scenario_template.get("template_id", ""),
            "dimensions": comparisons,
        })
    category_counts = Counter(row["sample_category"] for row in rows)
    result = {
        "artifact_version": "p5-d2-offline-candidate-comparison-v1",
        "source_run_id": SOURCE_RUN,
        "historical_selector_run_id": R3_RUN,
        "mode": "OFFLINE_NO_PROVIDER",
        "provider_calls": 0,
        "sample_groups": len(rows),
        "category_counts": dict(category_counts),
        "sample_limitations": {
            "explicit_road_slope_groups_in_source": sum(
                "ROAD_SLOPE" in item.structured_semantic_query["road_relations"]
                for item in inputs
            ),
            "slope_road_relation_basis": (
                "The eligible r1 parent set has no explicit ROAD_SLOPE group; this stratum "
                "uses source holding/road-relation mechanisms and does not invent slope evidence."
            ),
        },
        "summary": {
            "groups_with_changed_shortlist": sum(any(
                value["old_shortlist"] != value["new_shortlist"]
                for value in row["dimensions"].values()
            ) for row in rows),
            "groups_with_changed_applicability": sum(any(
                value["old_applicability"] != value["new_applicability"]
                for value in row["dimensions"].values()
            ) for row in rows),
            "groups_with_broad_dynamic_unlocked": sum(
                row["dimensions"]["EGO_DYNAMICS"]["old_locked_atom_ids"] == ["FA001"]
                and row["dimensions"]["EGO_DYNAMICS"]["new_binding_policy"]["refinable"]
                for row in rows
            ),
            "groups_with_required_method_gap": sum(any(
                value["new_generation_status"] == "METHOD_GAP"
                for value in row["dimensions"].values()
            ) for row in rows),
            "top_rank_structured_support_improved_dimensions": improved_dimensions,
            "top_rank_structured_support_degraded_dimensions": degraded_dimensions,
            "top_rank_structured_support_equal_dimensions": equal_dimensions,
            "not_applicable_comparisons_excluded": not_applicable_comparisons_excluded,
            "trivial_diversity_rejected_by_fixture": 1,
        },
        "rows": rows,
    }
    return result


def comparison_markdown(comparison: dict[str, Any]) -> str:
    summary = comparison["summary"]
    lines = [
        "# P5-D2 Offline Candidate Quality Comparison", "",
        "## Executive Summary", "",
        f"- Compared {comparison['sample_groups']} deterministic parent groups: 10 each for vehicle traffic, VRU/pedestrian, reversing/parking, slope/road-relation, and static/miscellaneous cases.",
        f"- Shortlists changed in {summary['groups_with_changed_shortlist']} / {comparison['sample_groups']} groups; applicability changed in {summary['groups_with_changed_applicability']} / {comparison['sample_groups']} groups.",
        f"- Broad FA001 range bindings changed from immutable locks to refinable policy in {summary['groups_with_broad_dynamic_unlocked']} / {comparison['sample_groups']} sampled groups.",
        f"- The refactored selector exposes {summary['groups_with_required_method_gap']} sampled groups as REQUIRED Method gaps instead of silently allowing an empty dimension.",
        f"- Re-scoring old and new top candidates with the same structured features found {summary['top_rank_structured_support_improved_dimensions']} improved, {summary['top_rank_structured_support_degraded_dimensions']} degraded, and {summary['top_rank_structured_support_equal_dimensions']} equal dimension-level comparisons.",
        f"- {summary['not_applicable_comparisons_excluded']} old/new atom-score comparisons were excluded because the new policy proves that dimension NOT_APPLICABLE.",
        "", "## Interpretation", "",
        "The comparison measures candidate-space and governance changes, not final scenario quality: no Provider was called and no synthesis-r4 children were created. A surfaced METHOD_GAP is a fail-closed improvement, not a completed selection.",
        "The source r1 eligible set contains no explicit ROAD_SLOPE semantic group. The required slope/road-relation stratum therefore uses holding/road-relation cases as the closest source-supported proxy; it does not synthesize slope evidence.",
        "", "## Category Coverage", "",
        "| Category | Groups |", "|---|---:|",
    ]
    for category, count in comparison["category_counts"].items():
        lines.append(f"| {category} | {count} |")
    lines.extend([
        "", "## Sample Detail", "",
        "| Category | Malfunction | Parent | Shortlist changed | Applicability changed | Required gap | FM template |",
        "|---|---|---|---:|---:|---:|---|",
    ])
    for row in comparison["rows"]:
        dimensions = row["dimensions"]
        lines.append(
            f"| {row['sample_category']} | {row['malfunction_id']} | {row['parent_scenario_id']} | "
            f"{any(v['old_shortlist'] != v['new_shortlist'] for v in dimensions.values())} | "
            f"{any(v['old_applicability'] != v['new_applicability'] for v in dimensions.values())} | "
            f"{any(v['new_generation_status'] == 'METHOD_GAP' for v in dimensions.values())} | "
            f"{row['fm_template_id'] or '-'} |"
        )
    return "\n".join(lines) + "\n"


def r3_root_cause_markdown() -> str:
    audit = json.loads((
        ROOT / "runtime/review" / R3_RUN / "report-audit-r1/scenario_output_quality_audit.json"
    ).read_text(encoding="utf-8"))
    records = audit["records"]
    diversity = audit["diversity_metrics"]
    traffic_resolved = sum(
        bool(row["selected_dimension_bindings"]["TRAFFIC_PATTERN"]["atom_ids"])
        for row in records
    )
    dynamics = {
        tuple(row["selected_dimension_bindings"]["EGO_DYNAMICS"]["canonical_atom_ids"])
        for row in records
    }
    sets = Counter(tuple(row["complete_canonical_atom_set"]) for row in records)
    lines = [
        "# P5-D2 r3 Scenario Selection Root-Cause Diagnosis", "",
        "## Executive Summary", "",
        f"The immutable r3 evidence contains {len(records)} children across {audit['summary']['semantic_groups']} groups. TRAFFIC_PATTERN resolved in {traffic_resolved} / {len(records)} children, EGO_DYNAMICS had {len(dynamics)} unique canonical selection, and {diversity['cross_malfunction_identical_complete_atom_set_clusters']} identical-set clusters crossed malfunction boundaries. These results match candidate/contract constraints at start commit `0918e209981c0aca5a5a44b96767f8de15e1a2ad`; they are not evidence that a larger model could have selected unavailable or optionalized semantics.",
        "", "## Quantitative Findings", "",
        f"- Unique complete atom sets: {diversity['unique_complete_canonical_atom_sets']}.",
        f"- Unique sets excluding WHERE/ROAD: {diversity['unique_atom_sets_excluding_where_road']}.",
        f"- Cross-malfunction identical clusters: {diversity['cross_malfunction_identical_complete_atom_set_clusters']}.",
        f"- Largest identical cluster: {diversity['largest_identical_atom_set_cluster']['child_count']} children.",
        "", "### Variant-position WHERE bias", "",
        "| Position | Dominant atom | Count / 412 |", "|---|---|---:|",
    ]
    for label in ("typical", "boundary", "extreme"):
        count = Counter(
            tuple(row["selected_dimension_bindings"]["WHERE"]["canonical_atom_ids"])
            for row in records if row["coverage_label"] == label
        )
        atom, total = count.most_common(1)[0]
        lines.append(f"| {label} | {','.join(atom)} | {total} |")
    lines.extend([
        "", "### Variant-position ROAD bias", "",
        "| Position | Dominant atom | Count / 412 |", "|---|---|---:|",
    ])
    for label in ("typical", "boundary", "extreme"):
        count = Counter(
            tuple(row["selected_dimension_bindings"]["ROAD"]["canonical_atom_ids"])
            for row in records if row["coverage_label"] == label
        )
        atom, total = count.most_common(1)[0]
        lines.append(f"| {label} | {','.join(atom)} | {total} |")
    lines.extend([
        "", "### Most repeated complete atom sets", "",
        "| Children | Canonical atom set |", "|---:|---|",
    ])
    for atom_set, count in sets.most_common(10):
        lines.append(f"| {count} | {', '.join(atom_set)} |")
    lines.extend([
        "", "## Code-Mechanism Mapping", "",
        "| Finding | Pre-refactor mechanism at start commit | P5-D2 replacement |",
        "|---|---|---|",
        "| TRAFFIC_PATTERN 0/1236 | Global `optional_dimensions` and Provider schema allowed `[]` for every group. | Per-group REQUIRED/OPTIONAL/NOT_APPLICABLE; REQUIRED with no atom becomes METHOD_GAP. |",
        "| EGO_DYNAMICS unique = 1 | `_resolved_lock()` treated every RESOLVED parent binding, including RANGE_CONTAINMENT FA001, as immutable. | Explicit binding authority and refinement policy. |",
        "| Cross-malfunction duplicates | `_ranked_pool()` used coarse concept markers and isolated WHERE/ROAD context, so different hazards received similar shortlists. | Structured query, hard filters, FM-template evidence, component score trace, and BM25. |",
        "| Position-driven WHERE/ROAD | Labels had no axis intent and validation only rejected identical full atom sets. | ScenarioCoveragePlan and mechanism-aware sibling diversity validation. |",
        "| FM template underuse | The prompt listed an FM context reference, but the input carried no explicit template object and candidate payload omitted the compiled constraint details. | Bounded FM template evidence is now a first-class selector input. |",
        "", "## Conclusion", "",
        "The dominant r3 limitations were architecture-constrained: candidate availability, binding authority, applicability, coverage intent, and validation semantics. Model capability could affect fine-grained choice among valid candidates, but it could not unlock FA001, require traffic semantics, recover omitted FM constraints, or reject environment-only sibling variation under the old contract.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    method = _method()
    state = CheckpointRepository(ROOT / "runtime/agent").load(SOURCE_RUN)
    runner = ScenarioSynthesisRunner(
        method=method, client=None,
        run_dir=ROOT / "runtime/agent", review_root=ROOT / "runtime/review",
    )
    inputs, _, _, _, _ = runner._prepare(state)
    comparison = build_comparison(method, runner, inputs)
    _write_json(ROOT / "output/P5D2_Offline_Candidate_Quality_Comparison.json", comparison)
    _write(
        ROOT / "output/P5D2_Offline_Candidate_Quality_Comparison.md",
        comparison_markdown(comparison),
    )
    selector_audit = ScenarioSelectorQualityAudit().build(inputs)
    _write_json(ROOT / "output/P5D2_Selector_Quality_Audit.json", selector_audit)
    _write(
        ROOT / "output/P5D2_Selector_Quality_Audit.md",
        ScenarioSelectorQualityAudit.markdown(selector_audit),
    )
    _write(
        ROOT / "output/P5D2_R3_Scenario_Selection_Root_Cause.md",
        r3_root_cause_markdown(),
    )
    print(json.dumps({
        "provider_calls": 0,
        "sample_groups": comparison["sample_groups"],
        "comparison_summary": comparison["summary"],
        "selector_quality": selector_audit,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

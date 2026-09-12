from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
from typing import Any

from hara_agent.contracts import MethodContract
from hara_agent.services.analysis import ExposureInputReadinessService
from hara_agent.workflow.state import HARAState


DIMENSIONS = (
    "WHERE", "ROAD", "EGO_ACTION", "EGO_X_ROAD", "TRAFFIC_PATTERN",
    "EGO_DYNAMICS", "OBJECT",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _coverage_label(scenario: Any) -> str:
    value = str(getattr(scenario, "atomic_variant", "") or "")
    return value.rsplit(":", 1)[-1] if value else ""


class ScenarioOutputQualityAuditService:
    """Audit synthesized child output without changing runtime truth or using an LLM."""

    _PARTICIPANT_RULES = (
        (re.compile(r"行人|pedestrian", re.IGNORECASE), "PEDESTRIAN", (
            "pedestrian", "road user", "person in restricted danger zone",
            "persons in restricted danger zone",
        )),
        (re.compile(r"骑行者|自行车|摩托车|cyclist|bicycle|motorcycle", re.IGNORECASE),
         "VRU", ("cyclist", "bicycle", "motorcycle", "road user")),
        (re.compile(r"其他车辆|周边车辆|前车|后车|对向车|来车|与车辆|vehicle collision",
                    re.IGNORECASE),
         "VEHICLE", ("vehicle", "car", "road user")),
    )
    _ACTION_RULES = (
        (re.compile(r"倒车|反向行驶|revers", re.IGNORECASE), "REVERSING", (
            "revers", "parking in/out",
        )),
        (re.compile(r"泊入|parking[- ]?in", re.IGNORECASE), "PARKING_IN", (
            "parking in/out", "parking in", "parking-in", "parking",
            "revers", "maneuver", "stop", "drive",
        )),
        (re.compile(r"泊出|parking[- ]?out", re.IGNORECASE), "PARKING_OUT", (
            "parking in/out", "parking out", "parking-out", "parking",
            "revers", "maneuver", "stop", "drive",
        )),
        (re.compile(r"停车(?!场|库)|stopping|holding", re.IGNORECASE), "STOPPING", (
            "stop", "stopping", "holding", "parking", "revers", "maneuver",
        )),
    )

    def __init__(self, method: MethodContract):
        self.method = method
        self.exposure = ExposureInputReadinessService(method)

    @staticmethod
    def _causal_assessments(
        causal_trace: Mapping[str, Any] | None,
    ) -> dict[tuple[str, str], Mapping[str, Any]]:
        values: dict[tuple[str, str], Mapping[str, Any]] = {}
        for audit in (causal_trace or {}).get("audits", []):
            for item in _mapping(audit).get("item_salvage_audit", []):
                parsed = _mapping(_mapping(item).get("parsed_assessment"))
                pair = (
                    str(parsed.get("malfunction_id", "")),
                    str(parsed.get("scenario_id", "")),
                )
                if all(pair):
                    values[pair] = parsed
        return values

    @staticmethod
    def _dimension(binding: Mapping[str, Any]) -> dict[str, Any]:
        resolved = str(binding.get("resolution_status", "")).upper() == "RESOLVED"
        atom_id = str(binding.get("atom_id", "") or "").strip()
        canonical = str(binding.get("canonical_atom_id", "") or atom_id).strip()
        provenance = _mapping(binding.get("atom_provenance"))
        return {
            "resolution_status": str(binding.get("resolution_status", "PENDING")),
            "atom_ids": [atom_id] if resolved and atom_id else [],
            "canonical_atom_ids": [canonical] if resolved and canonical else [],
            "method_value": str(binding.get("method_value", "") or ""),
            "project_value": str(binding.get("project_value", "") or ""),
            "source_provenance": {
                "source_asset": str(provenance.get("source_asset", "") or ""),
                "source_rule": str(provenance.get("source_rule", "") or ""),
                "source_tag": str(provenance.get("source_tag", "") or ""),
                "dimension_source": str(binding.get("dimension_source", "") or ""),
            },
            "candidate_origin": str(binding.get("selection_origin", "") or ""),
            "selection_reason": str(binding.get("selection_reason", "") or ""),
            "unresolved_reason": str(binding.get("unresolved_reason", "") or ""),
        }

    @staticmethod
    def _canonical_set(
        dimensions: Mapping[str, Mapping[str, Any]], *, exclude_environment: bool,
    ) -> tuple[str, ...]:
        excluded = {"WHERE", "ROAD"} if exclude_environment else set()
        return tuple(sorted({
            atom_id
            for name, binding in dimensions.items()
            if name not in excluded
            for atom_id in binding.get("canonical_atom_ids", [])
            if atom_id
        }))

    @classmethod
    def _object_check(cls, text: str, object_value: str) -> dict[str, Any]:
        expectations = []
        accepted: set[str] = set()
        for pattern, label, tokens in cls._PARTICIPANT_RULES:
            if pattern.search(text):
                expectations.append(label)
                accepted.update(tokens)
        if not expectations:
            return {"status": "NOT_ASSESSABLE", "explicit_classes": [], "object": object_value}
        folded = object_value.casefold()
        matched = sorted(token for token in accepted if token in folded)
        return {
            "status": "PASS" if matched else "MISMATCH",
            "explicit_classes": expectations,
            "accepted_object_terms": sorted(accepted),
            "matched_object_terms": matched,
            "object": object_value,
        }

    @classmethod
    def _action_check(cls, text: str, action_value: str) -> dict[str, Any]:
        expectations = []
        accepted: set[str] = set()
        for pattern, label, tokens in cls._ACTION_RULES:
            if pattern.search(text):
                expectations.append(label)
                accepted.update(tokens)
        if not expectations:
            return {"status": "NOT_ASSESSABLE", "explicit_actions": [], "action": action_value}
        folded = action_value.casefold()
        matched = sorted(token for token in accepted if token in folded)
        return {
            "status": "PASS" if matched else "MISMATCH",
            "explicit_actions": expectations,
            "accepted_action_terms": sorted(accepted),
            "matched_action_terms": matched,
            "action": action_value,
        }

    def audit(
        self, state: HARAState, *, causal_trace: Mapping[str, Any] | None = None,
        checkpoint_path: str | Path = "", synthesis_review_dir: str | Path = "",
    ) -> dict[str, Any]:
        malfunction_by_id = {
            str(item.get("malfunction_id", "")): item for item in state.malfunctions
        }
        risk_by_pair = {
            (item.malfunction_id, item.scenario_id): item for item in state.risk_results
        }
        causal = self._causal_assessments(causal_trace)
        records: list[dict[str, Any]] = []
        group_records: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for scenario in state.scenarios:
            instance = _mapping(scenario.analysis_instance)
            malfunction_id = str(instance.get("malfunction_id", ""))
            malfunction = _mapping(malfunction_by_id.get(malfunction_id))
            dimensions_raw = _mapping(
                _mapping(scenario.facts).get("method_scenario_dimensions")
            )
            dimensions = {
                name: self._dimension(_mapping(dimensions_raw.get(name)))
                for name in DIMENSIONS
            }
            risk = risk_by_pair.get((malfunction_id, scenario.scenario_id))
            parsed = causal.get((malfunction_id, scenario.scenario_id), {})
            if parsed:
                causal_status = (
                    "CAUSAL_REVALIDATED" if bool(parsed.get("final_retain"))
                    else "CAUSAL_GAP"
                )
            else:
                causal_status = "CAUSAL_REVALIDATION_REQUIRED"
            readiness_input = {
                **dict(_mapping(scenario.facts)),
                "component_category": str(malfunction.get("component_category", "")),
            }
            readiness = self.exposure.assess(readiness_input)
            hazard_event = str(
                parsed.get("hazardous_event", "")
                or getattr(risk, "hazardous_event", "")
            )
            action_text = " ".join((
                str(malfunction.get("description", "")),
                str(malfunction.get("functional_effect", "")),
                str(malfunction.get("vehicle_level_hazard", "")),
                hazard_event,
            ))
            object_text = (
                hazard_event or str(malfunction.get("vehicle_level_hazard", ""))
            )
            full_set = self._canonical_set(
                dimensions, exclude_environment=False
            )
            non_environment_set = self._canonical_set(
                dimensions, exclude_environment=True
            )
            object_value = dimensions["OBJECT"]["method_value"]
            action_value = dimensions["EGO_ACTION"]["method_value"]
            selected_ids = {
                atom_id
                for binding in dimensions.values()
                for atom_id in binding["atom_ids"]
            }
            odd_violations = sorted(selected_ids & {"PH014", "FB007"})
            record = {
                "malfunction_id": malfunction_id,
                "parent_scenario_id": str(
                    scenario.source_scenario_id
                    or instance.get("parent_scenario_id", "")
                ),
                "child_scenario_id": scenario.scenario_id,
                "hazardous_event_id": str(instance.get("hazardous_event_id", "")),
                "coverage_label": _coverage_label(scenario),
                "semantic_group_id": str(instance.get("semantic_group_id", "")),
                "malfunction_summary": str(malfunction.get("description", "")),
                "hazard_summary": str(malfunction.get("vehicle_level_hazard", "")),
                "hazardous_event_summary": hazard_event,
                "selected_dimension_bindings": dimensions,
                "complete_canonical_atom_set": list(full_set),
                "canonical_atom_set_excluding_where_road": list(non_environment_set),
                "exposure_readiness_status": str(readiness.get("status", "")),
                "exposure_readiness_reason": str(readiness.get("reason_code", "")),
                "causal_delta_status": causal_status,
                "object_consistency": self._object_check(object_text, object_value),
                "ego_action_consistency": self._action_check(action_text, action_value),
                "odd_violations": odd_violations,
                "audit_findings": [],
            }
            if record["object_consistency"]["status"] == "MISMATCH":
                record["audit_findings"].append("HAZARD_OBJECT_MISMATCH")
            if record["ego_action_consistency"]["status"] == "MISMATCH":
                record["audit_findings"].append("HAZARD_ACTION_MISMATCH")
            if odd_violations:
                record["audit_findings"].append("ODD_CONSTRAINT_VIOLATION")
            records.append(record)
            group_records[record["semantic_group_id"]].append(record)

        full_clusters: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            full_clusters[tuple(record["complete_canonical_atom_set"])].append(record)
        duplicate_clusters = {
            signature: items for signature, items in full_clusters.items()
            if len(items) > 1
        }
        cross_malfunction_clusters = []
        for signature, items in duplicate_clusters.items():
            malfunction_ids = sorted({item["malfunction_id"] for item in items})
            if len(malfunction_ids) < 2:
                continue
            cross_malfunction_clusters.append({
                "canonical_atom_set": list(signature),
                "child_count": len(items),
                "malfunction_ids": malfunction_ids,
                "child_scenario_ids": sorted(item["child_scenario_id"] for item in items),
            })
        cross_malfunction_clusters.sort(
            key=lambda item: (-item["child_count"], item["canonical_atom_set"])
        )

        only_environment_groups: list[str] = []
        object_identical_groups: list[str] = []
        action_identical_groups: list[str] = []
        duplicate_within_groups: list[str] = []
        low_diversity_groups: list[str] = []
        for group_id, items in group_records.items():
            if len(items) != 3:
                continue
            full = {tuple(item["complete_canonical_atom_set"]) for item in items}
            non_environment = {
                tuple(item["canonical_atom_set_excluding_where_road"])
                for item in items
            }
            labels = {item["coverage_label"] for item in items}
            if len(non_environment) == 1 and len(full) > 1:
                only_environment_groups.append(group_id)
                if labels == {"typical", "boundary", "extreme"}:
                    low_diversity_groups.append(group_id)
                    for item in items:
                        item["audit_findings"].append(
                            "SCENARIO_VARIANT_LOW_DIVERSITY"
                        )
            if len({
                item["selected_dimension_bindings"]["OBJECT"]["method_value"]
                for item in items
            }) == 1:
                object_identical_groups.append(group_id)
            if len({
                item["selected_dimension_bindings"]["EGO_ACTION"]["method_value"]
                for item in items
            }) == 1:
                action_identical_groups.append(group_id)
            if len(full) < len(items):
                duplicate_within_groups.append(group_id)
                signatures = Counter(
                    tuple(item["complete_canonical_atom_set"]) for item in items
                )
                duplicated = {signature for signature, count in signatures.items() if count > 1}
                for item in items:
                    if tuple(item["complete_canonical_atom_set"]) in duplicated:
                        item["audit_findings"].append(
                            "DUPLICATE_SEMANTICS_ACROSS_COVERAGE_LABELS"
                        )

        for group_id, items in group_records.items():
            for item in items:
                differences = []
                for dimension in DIMENSIONS:
                    value = item["selected_dimension_bindings"][dimension]["method_value"]
                    sibling_values = {
                        sibling["selected_dimension_bindings"][dimension]["method_value"]
                        for sibling in items
                    }
                    if len(sibling_values) > 1:
                        differences.append(dimension)
                item["sibling_distinguishing_dimensions"] = differences

        value_counts = {
            dimension: len({
                record["selected_dimension_bindings"][dimension]["method_value"]
                for record in records
                if record["selected_dimension_bindings"][dimension]["resolution_status"]
                == "RESOLVED"
            })
            for dimension in DIMENSIONS
        }
        largest = cross_malfunction_clusters[0] if cross_malfunction_clusters else {
            "canonical_atom_set": [], "child_count": 0,
            "malfunction_ids": [], "child_scenario_ids": [],
        }
        diversity = {
            "children": len(records),
            "unique_complete_canonical_atom_sets": len(full_clusters),
            "unique_atom_sets_excluding_where_road": len({
                tuple(item["canonical_atom_set_excluding_where_road"])
                for item in records
            }),
            "unique_dimension_values": value_counts,
            "children_per_semantic_group_distribution": dict(sorted(Counter(
                len(items) for items in group_records.values()
            ).items())),
            "parent_groups_where_all_three_differ_only_by_where_road": len(
                only_environment_groups
            ),
            "parent_group_ids_where_all_three_differ_only_by_where_road": sorted(
                only_environment_groups
            ),
            "parent_groups_where_object_identical_across_all_three": len(
                object_identical_groups
            ),
            "parent_groups_where_ego_action_identical_across_all_three": len(
                action_identical_groups
            ),
            "parent_groups_with_duplicate_complete_canonical_atom_sets": len(
                duplicate_within_groups
            ),
            "duplicate_complete_atom_set_clusters": len(duplicate_clusters),
            "duplicate_complete_atom_set_child_excess": sum(
                len(items) - 1 for items in duplicate_clusters.values()
            ),
            "cross_malfunction_identical_complete_atom_set_clusters": len(
                cross_malfunction_clusters
            ),
            "largest_identical_atom_set_cluster": largest,
            "scenario_variant_low_diversity_groups": len(low_diversity_groups),
            "scenario_variant_low_diversity_group_ids": sorted(low_diversity_groups),
            "cross_malfunction_clusters": cross_malfunction_clusters,
        }
        object_mismatches = sum(
            item["object_consistency"]["status"] == "MISMATCH" for item in records
        )
        action_mismatches = sum(
            item["ego_action_consistency"]["status"] == "MISMATCH" for item in records
        )
        odd_violations = sum(bool(item["odd_violations"]) for item in records)
        ph014 = sum(
            "PH014" in binding["atom_ids"]
            for item in records
            for binding in item["selected_dimension_bindings"].values()
        )
        fb007 = sum(
            "FB007" in binding["atom_ids"]
            for item in records
            for binding in item["selected_dimension_bindings"].values()
        )
        projection_hidden = sum(
            bool(item["selected_dimension_bindings"][dimension]["method_value"])
            for item in records for dimension in (
                "EGO_ACTION", "EGO_X_ROAD", "EGO_DYNAMICS", "OBJECT"
            )
        )
        synthesis_quality_problem = bool(
            low_diversity_groups or duplicate_within_groups
            or cross_malfunction_clusters or object_mismatches
            or action_mismatches
        )
        return {
            "artifact_version": "p5-d1-scenario-output-quality-audit-v1",
            "run_id": state.run_id,
            "checkpoint_path": str(Path(checkpoint_path).resolve()) if checkpoint_path else "",
            "synthesis_review_dir": (
                str(Path(synthesis_review_dir).resolve()) if synthesis_review_dir else ""
            ),
            "provider_calls": 0,
            "scenario_regeneration": False,
            "risk_recomputation": False,
            "causal_resume": {
                "source_run_id": str(
                    _mapping(causal_trace).get("source_run_id", state.run_id)
                ),
                "target_run_id": str(_mapping(causal_trace).get("run_id", "")),
                "completed_malfunction_groups": int(
                    _mapping(causal_trace).get("completed_malfunctions", 0)
                ),
                "target_malfunction_groups": int(
                    _mapping(causal_trace).get("target_malfunctions", 0)
                ),
                "deferred_malfunction_group_ids": sorted(
                    _mapping(_mapping(causal_trace).get("deferred_malfunctions"))
                ),
                "resume_parser": (
                    "ScenarioCausalRevalidationRunner._recover_completed"
                ),
            },
            "methodology": {
                "source_of_truth": "committed synthesis checkpoint and existing review artifacts",
                "complete_atom_set": (
                    "sorted unique canonical_atom_id values from resolved bindings"
                ),
                "excluding_where_road": (
                    "same set after omitting WHERE and ROAD bindings"
                ),
                "hazard_consistency": (
                    "explicit keyword comparison only; NOT_ASSESSABLE is not a mismatch"
                ),
            },
            "summary": {
                "children_audited": len(records),
                "semantic_groups": len(group_records),
                "exposure_readiness": dict(sorted(Counter(
                    item["exposure_readiness_status"] for item in records
                ).items())),
                "causal_delta_status": dict(sorted(Counter(
                    item["causal_delta_status"] for item in records
                ).items())),
            },
            "diversity_metrics": diversity,
            "hazard_consistency": {
                "hazard_object_mismatches": object_mismatches,
                "hazard_action_mismatches": action_mismatches,
                "odd_violations": odd_violations,
                "PH014_selected": ph014,
                "FB007_selected": fb007,
            },
            "finding": {
                "projection_problem_present": projection_hidden > 0,
                "projection_only_problem": (
                    projection_hidden > 0 and not synthesis_quality_problem
                ),
                "synthesis_quality_problem": synthesis_quality_problem,
                "resolved_child_dimension_values_previously_hidden": projection_hidden,
            },
            "records": records,
        }

    @staticmethod
    def render_markdown(audit: Mapping[str, Any]) -> str:
        summary = _mapping(audit.get("summary"))
        diversity = _mapping(audit.get("diversity_metrics"))
        consistency = _mapping(audit.get("hazard_consistency"))
        finding = _mapping(audit.get("finding"))
        resume = _mapping(audit.get("causal_resume"))
        largest = _mapping(diversity.get("largest_identical_atom_set_cluster"))
        lines = [
            "# Synthesized scenarios are more detailed than the old workbook, but measurable reuse remains",
            "",
            "## Executive Summary",
            "",
            f"- Audited all {summary.get('children_audited', 0)} accepted analytical children with zero Provider calls.",
            f"- Found {diversity.get('unique_complete_canonical_atom_sets', 0)} unique complete canonical atom sets and {diversity.get('unique_atom_sets_excluding_where_road', 0)} after excluding WHERE/ROAD.",
            f"- {diversity.get('scenario_variant_low_diversity_groups', 0)} semantic groups meet the deterministic low-diversity flag definition; the largest cross-malfunction identical-set cluster contains {largest.get('child_count', 0)} children.",
            f"- Explicit-term checks found {consistency.get('hazard_object_mismatches', 0)} object mismatches, {consistency.get('hazard_action_mismatches', 0)} action mismatches, and {consistency.get('odd_violations', 0)} ODD violations.",
            "",
            "## Scope and controls",
            "",
            f"- Run: `{audit.get('run_id', '')}`",
            "- Source of truth: committed synthesis checkpoint and existing review artifacts; Excel was not used as input.",
            "- No scenarios were regenerated and S/E/C/ASIL were not recomputed.",
            "- `NOT_ASSESSABLE` explicit-term checks are not counted as mismatches.",
            "",
            "## Diversity metrics",
            "",
            f"- Children: {summary.get('children_audited', 0)}",
            f"- Semantic groups: {summary.get('semantic_groups', 0)}",
            f"- Unique complete canonical atom sets: {diversity.get('unique_complete_canonical_atom_sets', 0)}",
            f"- Unique atom sets excluding WHERE/ROAD: {diversity.get('unique_atom_sets_excluding_where_road', 0)}",
            f"- Groups whose three variants differ only by WHERE/ROAD: {diversity.get('parent_groups_where_all_three_differ_only_by_where_road', 0)}",
            f"- Groups with identical OBJECT across all three: {diversity.get('parent_groups_where_object_identical_across_all_three', 0)}",
            f"- Groups with identical EGO_ACTION across all three: {diversity.get('parent_groups_where_ego_action_identical_across_all_three', 0)}",
            f"- Groups containing duplicate complete atom sets: {diversity.get('parent_groups_with_duplicate_complete_canonical_atom_sets', 0)}",
            f"- Duplicate complete atom-set child excess: {diversity.get('duplicate_complete_atom_set_child_excess', 0)}",
            f"- Cross-malfunction identical-set clusters: {diversity.get('cross_malfunction_identical_complete_atom_set_clusters', 0)}",
            "",
            "## Deterministic consistency checks",
            "",
            f"- Hazard/object mismatches: {consistency.get('hazard_object_mismatches', 0)}",
            f"- Hazard/action mismatches: {consistency.get('hazard_action_mismatches', 0)}",
            f"- ODD violations: {consistency.get('odd_violations', 0)}",
            f"- PH014 selected: {consistency.get('PH014_selected', 0)}",
            f"- FB007 selected: {consistency.get('FB007_selected', 0)}",
            "",
            "## Finding",
            "",
            f"- Projection problem present: {'yes' if finding.get('projection_problem_present') else 'no'}.",
            f"- Projection-only explanation sufficient: {'yes' if finding.get('projection_only_problem') else 'no'}.",
            f"- Synthesis-quality finding present: {'yes' if finding.get('synthesis_quality_problem') else 'no'}.",
            "- The corrected workbook can expose hidden structured differences, but it does not erase measured duplicate or low-diversity findings. No regeneration is authorized by this audit.",
            "",
            "## Preserved causal resume",
            "",
            f"- Completed malfunction groups: {resume.get('completed_malfunction_groups', 0)} / {resume.get('target_malfunction_groups', 0)}",
            f"- Deferred malfunction groups: {len(resume.get('deferred_malfunction_group_ids', []))}",
            "- The read-only resume parser recovers completed groups and the unchanged command skips them, continuing only deferred groups under the same target run ID.",
            "",
            "```powershell",
            "$env:PYTHONPATH=(Resolve-Path 'src').Path",
            "",
            "python -m hara_agent revalidate-synthesized-scenarios `",
            f"  --source-run-id {resume.get('source_run_id', '')} `",
            f"  --target-run-id {resume.get('target_run_id', '')} `",
            "  --max-workers 8",
            "```",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _sample_category(record: Mapping[str, Any]) -> str:
        dimensions = _mapping(record.get("selected_dimension_bindings"))
        object_value = str(_mapping(dimensions.get("OBJECT")).get("method_value", ""))
        text = " ".join((
            str(record.get("malfunction_summary", "")),
            str(record.get("hazard_summary", "")),
            str(record.get("hazardous_event_summary", "")),
            object_value,
        )).casefold()
        if any(token in text for token in (
            "行人", "骑行", "自行车", "摩托车", "pedestrian", "cyclist",
            "bicycle", "motorcycle", "road user",
        )):
            return "pedestrian_vru_object_interaction"
        if any(token in text for token in (
            "碰撞", "相撞", "车辆交互", "vehicle collision", "other vehicle",
        )):
            return "vehicle_collision_interaction"
        if any(token in text for token in (
            "倒车", "泊入", "泊出", "移动", "行驶", "制动", "加速", "转向",
            "revers", "parking",
        )):
            return "movement_action_failure"
        return "miscellaneous_static_other"

    @classmethod
    def render_sample(cls, audit: Mapping[str, Any]) -> str:
        records = [dict(item) for item in audit.get("records", [])]
        categories = (
            "vehicle_collision_interaction",
            "pedestrian_vru_object_interaction",
            "movement_action_failure",
            "miscellaneous_static_other",
        )
        chosen: list[tuple[str, Mapping[str, Any]]] = []
        used: set[str] = set()
        for category in categories:
            matches = [
                item for item in records
                if cls._sample_category(item) == category
                and item["child_scenario_id"] not in used
            ][:5]
            for item in matches:
                used.add(item["child_scenario_id"])
                chosen.append((category, item))
        if len(chosen) < 20:
            for item in records:
                if item["child_scenario_id"] in used:
                    continue
                chosen.append((cls._sample_category(item), item))
                used.add(item["child_scenario_id"])
                if len(chosen) == 20:
                    break
        lines = [
            "# HARA P5-D Scenario Quality Sample",
            "",
            "This is a zero-Provider, read-only sample from the accepted synthesis checkpoint. It does not finalize S/E/C/ASIL.",
            "",
        ]
        for index, (category, item) in enumerate(chosen, start=1):
            dimensions = _mapping(item.get("selected_dimension_bindings"))
            findings = item.get("audit_findings", [])
            distinguish = item.get("sibling_distinguishing_dimensions", [])
            lines.extend([
                f"## {index}. {item['child_scenario_id']} ({category})",
                "",
                f"- Malfunction: {item['malfunction_summary']}",
                f"- Hazard: {item['hazardous_event_summary'] or item['hazard_summary']}",
                f"- Coverage label: {item['coverage_label']}",
            ])
            for dimension in DIMENSIONS:
                binding = _mapping(dimensions.get(dimension))
                value = binding.get("method_value") or binding.get("resolution_status")
                lines.append(f"- {dimension}: {value}")
            lines.extend([
                "- Why this child is distinguishable from siblings: "
                + (", ".join(distinguish) if distinguish else "no canonical dimension difference"),
                f"- Exposure readiness: {item['exposure_readiness_status']}",
                f"- Current causal status: {item['causal_delta_status']}",
                "- Audit finding: " + (", ".join(findings) if findings else "none"),
                "",
            ])
        return "\n".join(lines)


__all__ = ["DIMENSIONS", "ScenarioOutputQualityAuditService"]

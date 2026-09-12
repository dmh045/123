"""Read-only audit of committed FUSA Exposure executions and their inputs."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from typing import Any, Mapping

from hara_agent.contracts import MethodContract

from .exposure_input_readiness_service import ExposureInputReadinessService


_E_VALUES = ("E0", "E1", "E2", "E3", "E4", "Pending")
_AGGREGATION_RULES = (
    "all_e4", "e3_e4_mix", "min_when_unequal",
    "same_independent_minus_one", "same_coupled_no_change", "other",
)
_READY = {"READY_COMPLETE", "READY_METHOD_IRRELEVANT_GAPS"}
_PARTIAL = {
    "PENDING_RELEVANT_DIMENSION", "PENDING_ATOM_BINDING",
    "PENDING_AMBIGUOUS_ATOM_SET",
}


def _counter_template(keys: tuple[str, ...]) -> dict[str, int]:
    return {key: 0 for key in keys}


def _top(counter: Counter[Any], limit: int = 20, *, name: str) -> list[dict[str, Any]]:
    return [
        {name: (list(key) if isinstance(key, tuple) else key), "count": count}
        for key, count in counter.most_common(limit)
    ]


class ExposureInputAuditService:
    """Project a per-record audit from persisted checkpoint and risk trace only."""

    def __init__(self, method: MethodContract):
        self.method = method
        self.readiness = ExposureInputReadinessService(method)

    @staticmethod
    def _scenario_by_id(checkpoint: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            str(item.get("scenario_id", "")): dict(item)
            for item in checkpoint.get("scenarios", [])
            if isinstance(item, Mapping) and str(item.get("scenario_id", ""))
        }

    @staticmethod
    def _dimensions(bindings: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for dimension, raw in sorted(bindings.items()):
            binding = dict(raw) if isinstance(raw, Mapping) else {}
            filled = binding.get("filled_dimensions", ())
            filled_dimensions = [str(item) for item in filled] if isinstance(filled, (list, tuple)) else []
            atom_id = str(binding.get("atom_id", ""))
            canonical_atom_id = str(binding.get("canonical_atom_id", ""))
            rows.append({
                "dimension": str(dimension),
                "project_value": binding.get("project_value", ""),
                "resolution_status": str(binding.get("resolution_status", "PENDING")),
                "binding_status": str(binding.get("binding_status", "")),
                "atom_id": atom_id,
                "canonical_atom_id": canonical_atom_id,
                "unresolved_reason": str(binding.get("unresolved_reason", "")),
                "compound_fill": len(filled_dimensions) > 1,
                "filled_by_atom_id": str(binding.get("filled_by_atom_id", "")) or (
                    canonical_atom_id or atom_id if dimension in filled_dimensions else ""
                ),
                "filled_dimensions": filled_dimensions,
                "candidate_atom_ids": [str(item) for item in binding.get("candidate_atom_ids", [])]
                if isinstance(binding.get("candidate_atom_ids", []), (list, tuple)) else [],
            })
        return rows

    @staticmethod
    def _scenario_signature(scenario: Mapping[str, Any], dimensions: list[dict[str, Any]]) -> str:
        return json.dumps({
            "operating_mode": str(scenario.get("operating_mode", "")),
            "dimensions": [
                (item["dimension"], str(item["project_value"]), item["resolution_status"])
                for item in dimensions
            ],
        }, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _post_distribution(trace: Mapping[str, Any] | None) -> dict[str, int]:
        values = _counter_template(_E_VALUES)
        if not isinstance(trace, Mapping):
            return values
        for row in trace.get("assessments", []):
            if not isinstance(row, Mapping) or not row.get("risk_scoring_invoked"):
                continue
            value = str(dict(row.get("exposure", {})).get("result", ""))
            values[value if value in _E_VALUES[:-1] else "Pending"] += 1
        return values

    def generate(
        self, *, checkpoint: Mapping[str, Any], trace: Mapping[str, Any],
    ) -> dict[str, Any]:
        scenarios = self._scenario_by_id(checkpoint)
        trace_rows = [
            dict(row) for row in trace.get("assessments", [])
            if isinstance(row, Mapping) and row.get("risk_scoring_invoked")
        ]
        records: list[dict[str, Any]] = []
        exposure_distribution = _counter_template(_E_VALUES)
        requested_domain = _counter_template(("Z", "F"))
        actual_domain = _counter_template(("Z", "F"))
        aggregation = _counter_template(_AGGREGATION_RULES)
        atom_sets: Counter[tuple[str, ...]] = Counter()
        atoms: Counter[str] = Counter()
        unresolved_dimensions: Counter[str] = Counter()
        readiness_status = Counter()
        dimension_fallback_count = 0
        compound_fills: list[dict[str, Any]] = []
        wiring_defects: list[dict[str, Any]] = []
        contexts_by_atom_set: dict[tuple[str, ...], set[str]] = defaultdict(set)
        record_ids_by_atom_set: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)

        for row in trace_rows:
            scenario_id = str(row.get("scenario_id", ""))
            checkpoint_scenario = scenarios.get(scenario_id, {})
            facts = checkpoint_scenario.get("facts", {})
            facts = dict(facts) if isinstance(facts, Mapping) else {}
            exposure_trace = row.get("exposure", {})
            exposure_trace = dict(exposure_trace) if isinstance(exposure_trace, Mapping) else {}
            bindings = facts.get("method_scenario_dimensions", exposure_trace.get("scenario_terms", {}))
            bindings = dict(bindings) if isinstance(bindings, Mapping) else {}
            scenario_atoms = facts.get("scenario_atom_ids", exposure_trace.get("atom_ids", []))
            scenario_atoms = [str(item) for item in scenario_atoms] if isinstance(scenario_atoms, (list, tuple)) else []
            scenario = {
                **facts,
                "scenario_id": scenario_id,
                "operating_mode": (
                    checkpoint_scenario.get("operating_mode")
                    or facts.get("operating_mode", "")
                ),
                "component_category": exposure_trace.get("component_category", ""),
                "scenario_atom_ids": scenario_atoms,
                "method_scenario_dimensions": bindings,
                "atoms_coupling": dict(exposure_trace.get("dependency_coupling", {})).get("coupling", ""),
            }
            readiness = self.readiness.assess(scenario)
            dimensions = self._dimensions(bindings)
            atom_set = tuple(sorted(dict.fromkeys(scenario_atoms)))
            value = str(exposure_trace.get("result", ""))
            result_key = value if value in _E_VALUES[:-1] else "Pending"
            exposure_distribution[result_key] += 1
            requested = str(exposure_trace.get("requested_domain", "")).upper()
            actual = str(exposure_trace.get("domain", exposure_trace.get("actual_domain", ""))).upper()
            if requested in requested_domain:
                requested_domain[requested] += 1
            if actual in actual_domain:
                actual_domain[actual] += 1
            rule = str(dict(exposure_trace.get("dependency_coupling", {})).get(
                "policy_branch", exposure_trace.get("aggregation_rule", ""),
            )).lower()
            aggregation[rule if rule in aggregation and rule != "other" else "other"] += 1
            dimension_fallback_count += int(bool(exposure_trace.get("scenario_level_fallback", False)))
            atom_sets[atom_set] += 1
            atoms.update(atom_set)
            readiness_status[readiness["status"]] += 1
            for item in dimensions:
                if item["resolution_status"] != "RESOLVED":
                    unresolved_dimensions[item["dimension"]] += 1
                if item["compound_fill"]:
                    compound_fills.append({
                        "malfunction_id": str(row.get("malfunction_id", "")),
                        "scenario_id": scenario_id,
                        **item,
                    })
            for issue in readiness["binding_issues"]:
                wiring_defects.append({
                    "malfunction_id": str(row.get("malfunction_id", "")),
                    "scenario_id": scenario_id,
                    **issue,
                })
            signature = self._scenario_signature(scenario, dimensions)
            contexts_by_atom_set[atom_set].add(signature)
            record_ids_by_atom_set[atom_set].append({
                "malfunction_id": str(row.get("malfunction_id", "")),
                "scenario_id": scenario_id,
            })
            atom_sources = [
                dict(item.get("source", {})) for item in exposure_trace.get("atom_bindings", [])
                if isinstance(item, Mapping) and isinstance(item.get("source", {}), Mapping)
            ]
            records.append({
                "hara_risk_identity": {
                    "malfunction_id": str(row.get("malfunction_id", "")),
                    "scenario_id": scenario_id,
                    "hazardous_event_id": str(row.get("hazardous_event_id", "")),
                },
                "scenario": {
                    "operating_mode": scenario["operating_mode"],
                    "scenario_atom_ids": scenario_atoms,
                    "method_scenario_dimensions": bindings,
                },
                "exposure": {
                    "requested_domain": requested,
                    "actual_domain": actual,
                    "dimension_fallback": bool(exposure_trace.get("scenario_level_fallback", False)),
                    "atom_details": list(exposure_trace.get("atom_bindings", [])),
                    "aggregation_rule": rule,
                    "coupling_consumed": bool(dict(exposure_trace.get("dependency_coupling", {})).get("coupling_consumed", False)),
                    "coupling": str(dict(exposure_trace.get("dependency_coupling", {})).get("coupling", "")),
                    "exposure_result": value,
                    "rule_id": str((exposure_trace.get("rule_ids", []) or [""])[0]),
                    "source_ref": atom_sources,
                },
                "method_scenario_dimensions": dimensions,
                "readiness": readiness,
                "findings": {
                    "EXPOSURE_INPUT_COMPLETENESS_SUSPECT": readiness["status"] in _PARTIAL,
                    "resolved_atom_absent_from_scenario_atom_ids": any(
                        issue.get("code") == "RESOLVED_DIMENSION_ATOM_NOT_SUPPLIED"
                        for issue in readiness["binding_issues"]
                    ),
                },
            })

        atom_set_context_collapse = [
            {
                "atom_set": list(atom_set),
                "record_count": count,
                "materially_different_scenario_context_count": len(contexts_by_atom_set[atom_set]),
                "examples": record_ids_by_atom_set[atom_set][:20],
            }
            for atom_set, count in atom_sets.most_common(20)
            if len(contexts_by_atom_set[atom_set]) > 1
        ]
        all_rows = len(records)
        common_global_atoms = [
            {"atom_id": atom_id, "count": count}
            for atom_id, count in atoms.most_common(20)
            if all_rows and count == all_rows
        ]
        readiness_counts = {
            key: readiness_status[key]
            for key in (
                "READY_COMPLETE", "READY_METHOD_IRRELEVANT_GAPS",
                "PENDING_RELEVANT_DIMENSION", "PENDING_ATOM_BINDING",
                "PENDING_AMBIGUOUS_ATOM_SET", "SOURCE_CONFLICT",
            )
        }
        diagnosis = {
            "CURRENT_FINALIZED_E4": {
                "genuinely_complete": sum(
                    item["exposure"]["exposure_result"] == "E4"
                    and item["readiness"]["status"] in _READY for item in records
                ),
                "likely_partial_input": sum(
                    item["exposure"]["exposure_result"] == "E4"
                    and item["readiness"]["status"] in _PARTIAL for item in records
                ),
                "unresolved_relevance_unknown": sum(
                    item["exposure"]["exposure_result"] == "E4"
                    and item["readiness"]["status"] not in _READY | _PARTIAL for item in records
                ),
            },
        }
        return {
            "artifact_version": "exposure-input-audit-v1",
            "run_id": str(trace.get("run_id", "")),
            "source_checkpoint_run_id": str(checkpoint.get("run_id", "")),
            "method_contract_hash": str(trace.get("method_contract_hash", "")),
            "provider_calls": 0,
            "eligible_records": len(records),
            "summary": {
                "exposure_result_distribution": exposure_distribution,
                "requested_domain": requested_domain,
                "actual_domain": actual_domain,
                "dimension_fallback_count": dimension_fallback_count,
                "aggregation_rule_distribution": aggregation,
                "scenario_atom_ids_cardinality": {
                    "1_atom": sum(count for atom_set, count in atom_sets.items() if len(atom_set) == 1),
                    "2_atoms": sum(count for atom_set, count in atom_sets.items() if len(atom_set) == 2),
                    "3_plus_atoms": sum(count for atom_set, count in atom_sets.items() if len(atom_set) >= 3),
                },
                "unique_atom_set_count": len(atom_sets),
                "top_20_atom_sets": _top(atom_sets, name="atom_set"),
                "unique_individual_atoms_used": len(atoms),
                "top_20_atoms_by_frequency": _top(atoms, name="atom_id"),
                "unresolved_dimension_frequency": dict(sorted(unresolved_dimensions.items())),
                "readiness": readiness_counts,
            },
            "diagnosis": diagnosis,
            "suspicious_patterns": {
                "common_global_atoms": common_global_atoms,
                "compound_fill_records": compound_fills,
                "identical_atom_sets_across_materially_different_scenarios": atom_set_context_collapse,
                "resolved_atom_wiring_defects": wiring_defects,
            },
            "records": records,
        }

    @staticmethod
    def render_markdown(payload: Mapping[str, Any]) -> str:
        summary = dict(payload.get("summary", {}))
        diagnosis = dict(payload.get("diagnosis", {})).get("CURRENT_FINALIZED_E4", {})
        lines = [
            "# Exposure Input Completeness Audit",
            "",
            f"- Eligible records: {payload.get('eligible_records', 0)}",
            f"- Provider calls: {payload.get('provider_calls', 0)}",
            "",
            "## Exposure result distribution",
            "",
        ]
        for key, value in summary.get("exposure_result_distribution", {}).items():
            lines.append(f"- {key}: {value}")
        lines.extend(["", "## Current finalized E4 diagnosis", ""])
        for key, value in diagnosis.items():
            lines.append(f"- {key}: {value}")
        lines.extend(["", "## Atom-set and domain evidence", ""])
        lines.append(f"- Requested Z/F: {summary.get('requested_domain', {})}")
        lines.append(f"- Actual Z/F: {summary.get('actual_domain', {})}")
        lines.append(f"- Aggregation branches: {summary.get('aggregation_rule_distribution', {})}")
        lines.append(f"- Unique atom sets: {summary.get('unique_atom_set_count', 0)}")
        lines.append(f"- Top atom sets: {summary.get('top_20_atom_sets', [])}")
        lines.append(f"- Top atoms: {summary.get('top_20_atoms_by_frequency', [])}")
        lines.extend(["", "## Unresolved dimensions and readiness", ""])
        lines.append(f"- Unresolved dimension frequency: {summary.get('unresolved_dimension_frequency', {})}")
        lines.append(f"- Readiness: {summary.get('readiness', {})}")
        lines.extend(["", "## Suspicious patterns", ""])
        patterns = dict(payload.get("suspicious_patterns", {}))
        lines.append(f"- Common/global atoms: {patterns.get('common_global_atoms', [])}")
        lines.append(
            "- Atom-set context collapse groups: "
            f"{len(patterns.get('identical_atom_sets_across_materially_different_scenarios', []))}"
        )
        lines.append(f"- Resolved-atom wiring defects: {len(patterns.get('resolved_atom_wiring_defects', []))}")
        lines.append("")
        return "\n".join(lines)

    @classmethod
    def render_root_cause_report(
        cls, payload: Mapping[str, Any], *, post_rescore_trace: Mapping[str, Any] | None,
    ) -> str:
        summary = dict(payload.get("summary", {}))
        diagnosis = dict(payload.get("diagnosis", {})).get("CURRENT_FINALIZED_E4", {})
        after = cls._post_distribution(post_rescore_trace)
        patterns = dict(payload.get("suspicious_patterns", {}))
        return "\n".join([
            "# HARA Exposure E4 Root Cause Report", "",
            "## 1. Why all 412 rows were E4", "",
            "The historical executor aggregated every supplied usable atom. The audit records whether "
            "that supplied set was complete; it does not infer missing project facts.", "",
            "## 2. Top atom sets", "", str(summary.get("top_20_atom_sets", [])), "",
            "## 3. Top individual atoms", "", str(summary.get("top_20_atoms_by_frequency", [])), "",
            "## 4. Domain Z/F distribution", "",
            f"Requested: {summary.get('requested_domain', {})}",
            f"Actual: {summary.get('actual_domain', {})}", "",
            "## 5. Aggregation-rule distribution", "", str(summary.get("aggregation_rule_distribution", {})), "",
            "## 6. Unresolved Exposure-relevant dimensions", "",
            str(summary.get("unresolved_dimension_frequency", {})), "",
            "## 7. Rows considered genuinely complete", "",
            str(diagnosis.get("genuinely_complete", 0)), "",
            "## 8. Rows considered prematurely finalized", "",
            str(diagnosis.get("likely_partial_input", 0)), "",
            "## 9. New post-rescore E distribution", "",
            *[f"- {key}: {value}" for key, value in after.items()], "",
            "## 10. Remaining method/input gaps", "",
            f"Readiness: {summary.get('readiness', {})}",
            f"Common/global atoms: {patterns.get('common_global_atoms', [])}",
            f"Resolved-atom wiring defects: {len(patterns.get('resolved_atom_wiring_defects', []))}",
            "",
        ])


__all__ = ["ExposureInputAuditService"]

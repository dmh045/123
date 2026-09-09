from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from hara_agent.contracts import MethodContract

from .method_atom_resolver import MethodAtomResolver


class ScenarioAliasProposalService:
    """Build review-only alias proposals from persisted Scenario binding gaps.

    This service deliberately does not infer terminology.  A proposal is only
    emitted when an existing governed Method atom can be deterministically
    resolved; all other terms remain explicit review findings.
    """

    def __init__(self, method: MethodContract):
        self.method = method
        self.resolver = MethodAtomResolver(method)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^\w]+", "", str(value).casefold(), flags=re.UNICODE)

    @staticmethod
    def _functions_by_scenario(records: dict[str, list[dict[str, Any]]]) -> dict[str, set[str]]:
        function_by_malfunction = {
            str(item.get("malfunction_id", "")): str(item.get("function_id", ""))
            for item in records.get("malfunction", [])
            if str(item.get("malfunction_id", "")).strip()
            and str(item.get("function_id", "")).strip()
        }
        result: dict[str, set[str]] = defaultdict(set)
        for candidate in records.get("scenario_candidate", []):
            scenario_id = str(candidate.get("scenario_id", "")).strip()
            if not scenario_id:
                continue
            for malfunction_id in candidate.get("generated_for_malfunction_ids", []):
                function_id = function_by_malfunction.get(str(malfunction_id))
                if function_id:
                    result[scenario_id].add(function_id)
        return result

    @staticmethod
    def _operating_modes_by_scenario(
        records: dict[str, list[dict[str, Any]]],
    ) -> dict[str, str]:
        return {
            str(item.get("scenario_id", "")): str(item.get("operating_mode", ""))
            for item in records.get("scenario_candidate", [])
            if str(item.get("scenario_id", "")).strip()
        }

    def generate(
        self, gap_payload: dict[str, Any], records: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        raw_gaps = gap_payload.get("gaps", [])
        if not isinstance(raw_gaps, list):
            raise ValueError("scenario_binding_gaps.gaps must be a list")
        functions_by_scenario = self._functions_by_scenario(records)
        modes_by_scenario = self._operating_modes_by_scenario(records)
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for gap in raw_gaps:
            if not isinstance(gap, dict):
                continue
            dimension = str(gap.get("dimension", "")).strip()
            terms = [str(gap.get("source_value", "")).strip()]
            terms.extend(
                str(item).strip() for item in gap.get("candidate_values", [])
            )
            for term in terms:
                if not dimension or not term:
                    continue
                key = (dimension, self._normalize(term))
                item = grouped.setdefault(key, {
                    "dimension": dimension,
                    "source_term": term,
                    "scenario_ids": set(),
                    "function_ids": set(),
                    "reasons": set(),
                    "evidence": [],
                    "operating_modes": set(),
                })
                scenario_id = str(gap.get("scenario_id", "")).strip()
                if scenario_id:
                    item["scenario_ids"].add(scenario_id)
                    item["function_ids"].update(functions_by_scenario.get(scenario_id, set()))
                    mode = modes_by_scenario.get(scenario_id, "").strip()
                    if mode:
                        item["operating_modes"].add(mode)
                item["reasons"].add(str(gap.get("reason", "PENDING_BINDING")))
                evidence = gap.get("evidence", {})
                if isinstance(evidence, dict) and evidence:
                    item["evidence"].append(evidence)

        proposals: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        for index, value in enumerate(
            sorted(grouped.values(), key=lambda item: (item["dimension"], item["source_term"])),
            start=1,
        ):
            dimension = value["dimension"]
            term = value["source_term"]
            base = {
                "dimension": dimension,
                "source_term": term,
                "used_by_scenario_ids": sorted(value["scenario_ids"]),
                "used_by_function_ids": sorted(value["function_ids"]),
                "gap_reasons": sorted(value["reasons"]),
                "gap_evidence": value["evidence"],
            }
            if dimension == "EGO_ACTION" and any(
                self._normalize(term) == self._normalize(mode)
                for mode in value["operating_modes"]
            ):
                findings.append({
                    **base,
                    "resolution": "SEMANTIC_LEVEL_MISMATCH",
                    "detail": (
                        "The source term is a Scenario operating_mode value, while "
                        "EGO_ACTION is a Method action dimension."
                    ),
                })
                continue
            if value["reasons"] == {"SPEED_CONSTRAINT_NOT_CONCRETE"}:
                findings.append({
                    **base,
                    "resolution": "OUT_OF_SCOPE_RANGE_CONSTRAINT",
                    "detail": "Numeric range binding is not Scenario terminology aliasing.",
                })
                continue
            resolution = self.resolver.resolve(term, dimension)
            if resolution.status == "RESOLVED":
                proposals.append({
                    **base,
                    "proposal_id": f"SCN_ALIAS_PROPOSAL_{index:03d}",
                    "status": "PROPOSED",
                    "resolution": "DETERMINISTIC_TARGET_REQUIRES_APPROVAL",
                    "canonical_target": {
                        "atom_id": resolution.atom_id,
                        "canonical_label": self.resolver.by_id[resolution.atom_id].get("label", ""),
                    },
                    "mapping_type": resolution.reason,
                    "provenance": resolution.provenance or {},
                })
            elif resolution.status == "AMBIGUOUS":
                findings.append({
                    **base,
                    "resolution": "AMBIGUOUS",
                    "candidate_targets": list(resolution.candidate_atom_ids),
                })
            else:
                findings.append({
                    **base,
                    "resolution": "NO_GOVERNED_ALIAS_CANDIDATE",
                    "candidate_targets": [],
                })
        coverage_before = gap_payload.get("scenario_binding_coverage", {})
        coverage_before = coverage_before if isinstance(coverage_before, dict) else {}
        before_dimensions = coverage_before.get("dimensions", {})
        before_dimensions = (
            before_dimensions if isinstance(before_dimensions, dict) else {}
        )
        coverage_after: dict[str, dict[str, int]] = {}
        resolved_by_alias = 0
        for dimension, entry in before_dimensions.items():
            values = entry.get("context_values", []) if isinstance(entry, dict) else []
            summary = {"total": 0, "resolved": 0, "ambiguous": 0, "pending": 0}
            for value in values:
                if not isinstance(value, dict):
                    continue
                source_term = str(value.get("source_value", "")).strip()
                if not source_term:
                    continue
                summary["total"] += 1
                resolution = self.resolver.resolve(source_term, str(dimension))
                if resolution.status == "RESOLVED":
                    summary["resolved"] += 1
                    resolved_by_alias += int(
                        resolution.reason == "APPROVED_GOVERNED_ALIAS"
                    )
                elif resolution.status == "AMBIGUOUS":
                    summary["ambiguous"] += 1
                else:
                    summary["pending"] += 1
            coverage_after[str(dimension)] = summary
        return {
            "artifact_version": "scenario-alias-proposals-v1",
            "method_contract_hash": str(self.method.metadata.get("template_hash", "")),
            "unique_unresolved_terms": len(grouped),
            "proposals": proposals,
            "findings": findings,
            "approved_alias_count": len(self.resolver.scenario_aliases),
            "alias_impact": {
                "before": {
                    str(dimension): {
                        key: int(entry.get(key, 0))
                        for key in ("total", "resolved", "ambiguous", "pending")
                    }
                    for dimension, entry in before_dimensions.items()
                    if isinstance(entry, dict)
                },
                "with_approved_aliases": coverage_after,
                "resolved_by_alias": resolved_by_alias,
            },
            "automatic_approval_performed": False,
        }

"""Review-only Scenario coverage governance proposals."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from hara_agent.contracts import MethodContract


class ScenarioCoverageProposalService:
    """Turn observed binding gaps and Function context into non-executable review work."""

    def __init__(self, method: MethodContract):
        self.method = method
        self.dimensions = tuple(
            item.canonical_name for item in method.scenario_model.dimensions
        )

    @staticmethod
    def _normalized(value: Any) -> str:
        return re.sub(r"[^\w]+", "", str(value).casefold(), flags=re.UNICODE)

    def _semantic_key(self, function: dict[str, Any]) -> str:
        material = {
            key: (
                [self._normalized(item) for item in function.get(key, [])]
                if isinstance(function.get(key), list) else self._normalized(function.get(key, ""))
            )
            for key in (
                "name", "output", "description", "preconditions", "triggers", "odd_constraints",
            )
        }
        return hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def generate(
        self,
        gap_payload: dict[str, Any],
        records: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        raw_coverage = gap_payload.get("scenario_binding_coverage", {})
        raw_dimensions = (
            raw_coverage.get("dimensions", {})
            if isinstance(raw_coverage, dict) else {}
        )
        observed: dict[str, dict[str, Any]] = {}
        for dimension in self.dimensions:
            entry = raw_dimensions.get(dimension, {})
            entry = entry if isinstance(entry, dict) else {}
            context_values = entry.get("context_values", [])
            context_values = [
                item for item in context_values if isinstance(item, dict)
            ]
            observed[dimension] = {
                "has_project_context": bool(context_values),
                "source_values": sorted({
                    str(item.get("source_value", "")).strip()
                    for item in context_values if str(item.get("source_value", "")).strip()
                }),
                "binding_reasons": dict(entry.get("reasons", {})),
            }

        proposals: list[dict[str, Any]] = []
        deduplicated_function_ids: list[str] = []
        seen: set[str] = set()
        for raw_function in records.get("function", []):
            if not isinstance(raw_function, dict):
                continue
            function_id = str(raw_function.get("function_id", "")).strip()
            if not function_id:
                continue
            semantic_key = self._semantic_key(raw_function)
            if semantic_key in seen:
                deduplicated_function_ids.append(function_id)
                continue
            seen.add(semantic_key)
            dimensions = []
            for dimension in self.dimensions:
                context = observed[dimension]
                dimensions.append({
                    "dimension": dimension,
                    "coverage_status": (
                        "PENDING_OBSERVED_PROJECT_CONTEXT"
                        if context["has_project_context"]
                        else "PENDING_NO_FUNCTION_COVERAGE_EVIDENCE"
                    ),
                    "why_relevant": (
                        "Observed project context supplies this Method dimension, but no "
                        "APPROVED Function coverage rule establishes its relevance."
                        if context["has_project_context"] else
                        "No APPROVED Function coverage rule establishes whether this Method "
                        "dimension is relevant or not applicable."
                    ),
                    "source_values": context["source_values"],
                    "binding_reasons": context["binding_reasons"],
                })
            proposals.append({
                "proposal_id": f"SCN_COVERAGE_PROPOSAL_{semantic_key[:12].upper()}",
                "status": "PROPOSED",
                "function_id": function_id,
                "function_semantic_category": "PENDING_GOVERNANCE_CLASSIFICATION",
                "function_context": {
                    key: raw_function.get(key, [] if key in {
                        "preconditions", "triggers", "odd_constraints",
                    } else "")
                    for key in (
                        "name", "output", "description", "preconditions", "triggers", "odd_constraints",
                    )
                },
                "candidate_dimensions": dimensions,
                "coverage_status": "PENDING_NO_APPROVED_COVERAGE_RULE",
                "approval_status": "PROPOSED",
                "source_evidence": {
                    "function_source_refs": raw_function.get("source_refs", raw_function.get("sources", [])),
                    "knowledge_sources": list(
                        self.method.metadata.get("scenario_coverage_knowledge_sources", [])
                    ),
                    "binding_gap_artifact": "scenario_binding_gaps.json",
                },
                "rationale": (
                    "This is review-only. It does not establish an atom mapping, a Method "
                    "dimension, or executable coverage semantics."
                ),
            })
        governance = self.method.metadata.get("scenario_coverage_governance", {})
        return {
            "artifact_version": "scenario-coverage-proposals-v1",
            "method_contract_hash": str(self.method.metadata.get("template_hash", "")),
            "proposals": proposals,
            "proposal_count": len(proposals),
            "deduplicated_function_ids": sorted(deduplicated_function_ids),
            "approved_coverage_rule_count": int(
                governance.get("approved_count", len(self.method.metadata.get("scenario_coverage_rules", [])))
            ),
            "runtime_behavior_changed": bool(self.method.metadata.get("scenario_coverage_rules", [])),
            "automatic_approval_performed": False,
        }

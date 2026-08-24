from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hara_agent.services.validation import ReleaseGateValidator

from .causal_contract import CausalContractHarness

if TYPE_CHECKING:
    from hara_agent.workflow.state import HARAState


class HARAValidationHarness:
    """Read-only end-to-end metrics over an already-produced HARAState."""

    def evaluate(self, state: "HARAState") -> dict[str, Any]:
        causal_payloads = [
            item["causal_assessment"]
            for item in state.item_definition.get("scenario_assessments", [])
            if isinstance(item, dict) and isinstance(item.get("causal_assessment"), dict)
        ]
        causal = CausalContractHarness().evaluate(causal_payloads)
        release = ReleaseGateValidator().evaluate(state)
        risk_count = len(state.risk_results)

        def completed(field: str) -> int:
            return sum(
                getattr(item, field).value not in {None, ""}
                for item in state.risk_results
            )

        draft_checks = release.checks
        ready_for_draft = all((
            draft_checks["method_contract_bound"],
            draft_checks["project_facts_structurally_valid"],
            draft_checks["risk_causal_assessments"]["missing_count"] == 0,
            draft_checks["risk_causal_assessments"]["invalid_count"] == 0,
            not state.errors,
        ))
        return {
            "benchmark_version": "synthetic-hara-benchmark-v1",
            "classification": "EVALUATION_ONLY",
            "scenario": {
                "scenario_count": len(state.scenarios),
                "causal_validation_rate": causal["metrics"]["causal_validation_rate"],
                "causal_gap_rate": causal["metrics"]["causal_gap_rate"],
                "unsupported_chain_rate": causal["metrics"]["unsupported_chain_rate"],
            },
            "evidence": {
                "edge_evidence_coverage": causal["metrics"]["edge_evidence_coverage"],
                "missing_evidence_rate": causal["metrics"]["missing_evidence_rate"],
            },
            "risk": {
                "risk_count": risk_count,
                "severity_completed": completed("severity"),
                "exposure_completed": completed("exposure"),
                "controllability_completed": completed("controllability"),
                "ASIL_generated": completed("asil"),
            },
            "release": {
                "ready_for_draft": ready_for_draft,
                "ready_for_release": release.ready_for_release,
                "blocked_reason": list(release.blockers),
            },
        }

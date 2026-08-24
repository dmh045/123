from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from hara_agent.contracts import (
    CausalAssessmentStatus, ScenarioCausalAssessment,
)
from hara_agent.models import EvidenceKind, ReviewStatus


class CausalContractHarness:
    """Evaluation-only metrics for persisted Scenario causal contracts."""

    def evaluate(
        self,
        assessments: Iterable[ScenarioCausalAssessment | dict[str, Any]],
    ) -> dict[str, Any]:
        parsed: list[ScenarioCausalAssessment] = []
        errors: list[dict[str, str]] = []
        for index, value in enumerate(assessments):
            try:
                assessment = (
                    value if isinstance(value, ScenarioCausalAssessment)
                    else ScenarioCausalAssessment.from_dict(value)
                )
            except (KeyError, TypeError, ValueError) as error:
                errors.append({
                    "index": str(index),
                    "code": type(error).__name__,
                    "reason": str(error),
                })
                continue
            parsed.append(assessment)

        statuses = Counter(item.status.value for item in parsed)
        total_edges = sum(len(item.causal_graph.edges) for item in parsed)
        covered_edges = 0
        unsupported = 0
        for assessment in parsed:
            bindings = {item.edge_id: item for item in assessment.evidence_bindings}
            unsupported += bool(
                assessment.unsupported_links
                or any(
                    item.basis_type is EvidenceKind.ASSUMPTION
                    for item in assessment.evidence_bindings
                )
            )
            for edge in assessment.causal_graph.edges:
                binding = bindings.get(edge.edge_id)
                if (
                    edge.evidence_refs
                    and binding is not None
                    and binding.evidence_refs == edge.evidence_refs
                    and binding.basis_type is not EvidenceKind.ASSUMPTION
                    and binding.status not in {
                        ReviewStatus.REJECTED, ReviewStatus.NOT_APPLICABLE,
                    }
                ):
                    covered_edges += 1
        total = len(parsed)
        return {
            "stage": "causal_contract",
            "classification": "EVALUATION_ONLY",
            "metrics": {
                "assessment_count": total,
                "invalid_contract_count": len(errors),
                "causal_validation_rate": self._rate(
                    statuses[CausalAssessmentStatus.VALIDATED.value], total
                ),
                "causal_gap_rate": self._rate(
                    statuses[CausalAssessmentStatus.CAUSAL_GAP.value], total
                ),
                "unsupported_chain_rate": self._rate(unsupported, total),
                "edge_evidence_coverage": self._rate(covered_edges, total_edges),
                "missing_evidence_rate": self._rate(
                    total_edges - covered_edges, total_edges
                ),
            },
            "status_counts": dict(sorted(statuses.items())),
            "results": [{
                "scenario_id": item.scenario_id,
                "status": item.status.value,
                "edge_count": len(item.causal_graph.edges),
                "unsupported_links": list(item.unsupported_links),
            } for item in parsed],
            "errors": errors,
        }

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 1.0

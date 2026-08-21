from __future__ import annotations

from typing import Iterable

from hara_agent.models import EvidenceRecord, FactProvenance
from hara_agent.services.semantic.scenario_evidence import FactRegistry


class EvidenceIntegrityHarness:
    """Deterministic checks for ProjectFacts registration and authority preservation."""

    def evaluate(
        self,
        expected_records: Iterable[EvidenceRecord],
        registry: FactRegistry,
    ) -> dict[str, object]:
        expected = tuple(expected_records)
        expected_project = tuple(
            record for record in expected
            if record.namespace == "PROJECT"
            and record.provenance is FactProvenance.PROJECT_INPUT
        )
        actual = {record.evidence_ref: record for record in registry.records}
        registered = [record for record in expected if record.evidence_ref in actual]
        registered_project = [
            record for record in expected_project if record.evidence_ref in actual
        ]
        provenance_matches = [
            record for record in registered
            if actual[record.evidence_ref].provenance is record.provenance
            and actual[record.evidence_ref].approval_status is record.approval_status
        ]
        source_matches = [
            record for record in registered
            if actual[record.evidence_ref].source_refs == record.source_refs
        ]
        diagnostics = registry.snapshot(include_values=False)["diagnostics"]
        return {
            "stage": "evidence_integrity",
            "classification": "EVALUATION_ONLY",
            "metrics": {
                "project_fact_registration_rate": self._rate(
                    len(registered_project), len(expected_project)
                ),
                "provenance_preservation_rate": self._rate(
                    len(provenance_matches), len(expected)
                ),
                "source_ref_preservation_rate": self._rate(
                    len(source_matches), len(expected)
                ),
                "duplicate_ref_count": diagnostics["duplicate_ref_count"],
            },
            "diagnostics": {
                "expected_project_fact_count": len(expected_project),
                "registered_project_fact_count": len(registered_project),
                "missing_project_refs": sorted(
                    record.evidence_ref for record in expected_project
                    if record.evidence_ref not in actual
                ),
                "provenance_mismatch_refs": sorted(
                    record.evidence_ref for record in registered
                    if record not in provenance_matches
                ),
                "source_ref_mismatch_refs": sorted(
                    record.evidence_ref for record in registered
                    if record not in source_matches
                ),
            },
            "registry_snapshot": registry.snapshot(include_values=False),
        }

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 1.0

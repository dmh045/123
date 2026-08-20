from __future__ import annotations

from typing import Iterable

from hara_agent.models import EvidenceKind, EvidenceRecord, FactProvenance, ReviewStatus
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
        legacy_authority_leaks = {
            record.evidence_ref for record in registry.records
            if record.provenance is FactProvenance.LEGACY_MIGRATION
            and (
                record.approval_status is not ReviewStatus.PENDING
                or record.kind is EvidenceKind.APPROVED_RULE
            )
        }
        legacy_authority_leaks.update(
            record.evidence_ref for record in expected
            if record.provenance is FactProvenance.LEGACY_MIGRATION
            and record.evidence_ref in actual
            and (
                actual[record.evidence_ref].provenance
                is not FactProvenance.LEGACY_MIGRATION
                or actual[record.evidence_ref].approval_status is not ReviewStatus.PENDING
            )
        )
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
                "legacy_authority_leak_count": len(legacy_authority_leaks),
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
                "legacy_authority_leak_refs": sorted(legacy_authority_leaks),
            },
            "registry_snapshot": registry.snapshot(include_values=False),
        }

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 1.0

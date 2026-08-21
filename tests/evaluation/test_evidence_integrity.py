from hara_agent.evaluation.stages import EvidenceIntegrityHarness
from hara_agent.models import (
    EvidenceKind, EvidenceRecord, FactProvenance, ReviewStatus, SourceRef,
)
from hara_agent.services.semantic.scenario_evidence import FactRegistry


def _project_records():
    source = SourceRef("docx", "ItemDef.docx", "table:atomic-facts")
    return (
        EvidenceRecord(
            "PROJECT.speed.parking.max_kph", 5.0, EvidenceKind.DIRECT_FACT,
            FactProvenance.PROJECT_INPUT, ReviewStatus.PENDING, (source,),
        ),
        EvidenceRecord(
            "PROJECT.driver.outside", {"driver_location": "OUTSIDE"},
            EvidenceKind.DIRECT_FACT, FactProvenance.PROJECT_INPUT,
            ReviewStatus.PENDING, (source,),
        ),
    )


def test_integrity_harness_reports_clean_registration_and_redacted_snapshot():
    expected = _project_records()
    registry = FactRegistry()
    registry.extend(expected)
    report = EvidenceIntegrityHarness().evaluate(expected, registry)

    assert report["metrics"] == {
        "project_fact_registration_rate": 1.0,
        "provenance_preservation_rate": 1.0,
        "source_ref_preservation_rate": 1.0,
        "duplicate_ref_count": 0,
    }
    assert all(
        "value" not in record for record in report["registry_snapshot"]["records"]
    )


def test_integrity_harness_detects_missing_and_changed_metadata():
    expected = _project_records()
    registry = FactRegistry()
    registry.register(EvidenceRecord(
        expected[0].evidence_ref, expected[0].value, expected[0].kind,
        FactProvenance.LLM_INFERENCE, ReviewStatus.FINALIZED,
    ))
    report = EvidenceIntegrityHarness().evaluate(expected, registry)

    assert report["metrics"]["project_fact_registration_rate"] == 0.5
    assert report["metrics"]["provenance_preservation_rate"] == 0.0
    assert report["metrics"]["source_ref_preservation_rate"] == 0.0
    assert report["diagnostics"]["missing_project_refs"] == [
        "PROJECT.driver.outside"
    ]

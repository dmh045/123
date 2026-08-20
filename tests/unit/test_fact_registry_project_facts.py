from hara_agent.models import (
    ConstraintOperator, DriverContextFact, DriverLocation, EvidenceKind,
    FactProvenance, ItemDefinitionFacts, NumericConstraintFact, ReviewStatus,
    SourceRef, SpeedEnvelope,
)
from hara_agent.services.semantic.project_evidence_registry import (
    build_project_evidence_registry,
)


def test_atomic_project_facts_are_registered_independently_with_sources():
    speed_source = SourceRef("docx", "ItemDef.docx", "table:parking-speed")
    numeric_source = SourceRef("docx", "ItemDef.docx", "table:obstacle-height")
    inside_source = SourceRef("docx", "ItemDef.docx", "section:driver-inside")
    outside_source = SourceRef("docx", "ItemDef.docx", "section:driver-outside")
    facts = ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle and parking infrastructure",
        speed_envelopes=[
            SpeedEnvelope("parking", 0, 5, sources=[speed_source]),
        ],
        numeric_constraints=[
            NumericConstraintFact(
                fact_type="obstacle_detection_height",
                parameter="obstacle_height",
                operator=ConstraintOperator.GE,
                value=0.1,
                unit="m",
                context={"operating_mode": "parking"},
                source_refs=[numeric_source],
            ),
        ],
        driver_context_facts=[
            DriverContextFact(
                "driver_context.inside", DriverLocation.INSIDE,
                "direct_control", "driver remains in vehicle", [inside_source],
            ),
            DriverContextFact(
                "driver_context.outside", DriverLocation.OUTSIDE,
                "remote_control", "driver supervises outside vehicle", [outside_source],
            ),
        ],
        sources=[speed_source],
    )

    registry = build_project_evidence_registry(facts)

    minimum = registry.resolve_record("PROJECT.speed.parking.min_kph")
    maximum = registry.resolve_record("PROJECT.speed.parking.max_kph")
    numeric = registry.resolve_record(
        "PROJECT.numeric.obstacle_detection_height.context.operating_mode.parking"
    )
    inside = registry.resolve_record("PROJECT.driver.inside")
    outside = registry.resolve_record("PROJECT.driver.outside")
    assert minimum and maximum and numeric and inside and outside
    assert minimum.value == 0.0 and maximum.value == 5.0
    assert all(record.kind is EvidenceKind.DIRECT_FACT for record in registry.records)
    assert all(record.provenance is FactProvenance.PROJECT_INPUT for record in registry.records)
    assert all(record.approval_status is ReviewStatus.PENDING for record in registry.records)
    assert minimum.source_refs == (speed_source,)
    assert numeric.source_refs == (numeric_source,)
    assert inside.source_refs == (inside_source,)
    assert outside.source_refs == (outside_source,)
    assert inside.evidence_ref != outside.evidence_ref


def test_project_fact_snapshot_redacts_values_by_default():
    source = SourceRef("docx", "ItemDef.docx", "table:speed")
    facts = ItemDefinitionFacts(
        system_description="AVP", item_boundary="vehicle",
        speed_envelopes=[SpeedEnvelope("parking", 0, 5, sources=[source])],
        sources=[source],
    )
    snapshot = build_project_evidence_registry(facts).snapshot()
    assert snapshot["diagnostics"] == {"record_count": 2, "duplicate_ref_count": 0}
    assert all("value" not in record for record in snapshot["records"])


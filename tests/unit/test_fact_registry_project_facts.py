from hara_agent.models import (
    FactProvenance, ItemDefinitionFacts, ReviewStatus, RiskFact, SourceRef,
    SpeedEnvelope,
)
from hara_agent.services.semantic.project_evidence_registry import (
    build_project_evidence_registry,
)


def test_registry_exposes_typed_speed_and_neutral_risk_facts():
    source = SourceRef("item_definition", "ItemDef.docx", "row[1]", "active speed <= 12 km/h")
    risk_source = SourceRef("item_definition", "ItemDef.docx", "row[2]", "DRIVER_STATE = attentive")
    facts = ItemDefinitionFacts(
        system_description="system",
        item_boundary="vehicle",
        speed_envelopes=[SpeedEnvelope("active", 0, 12, sources=[source])],
        risk_facts=[RiskFact(
            "RF-DRIVER", "DRIVER_STATE", "attentive",
            context={"operating_mode": "active"}, source_refs=[risk_source],
        )],
        sources=[source],
    )

    registry = build_project_evidence_registry(facts)
    maximum = registry.resolve_record("PROJECT.speed.active.max_kph")
    driver = registry.resolve_record(
        "PROJECT.risk.driver_state.context.operating_mode.active"
    )

    assert maximum is not None and maximum.value == 12.0
    assert driver is not None and driver.value == "attentive"
    assert driver.provenance is FactProvenance.LLM_INFERENCE
    assert driver.approval_status is ReviewStatus.PENDING


def test_registry_snapshot_is_deterministic():
    source = SourceRef("item_definition", "ItemDef.docx", "row[1]", "active speed <= 12 km/h")
    facts = ItemDefinitionFacts(
        system_description="system", item_boundary="vehicle",
        speed_envelopes=[SpeedEnvelope("active", 0, 12, sources=[source])],
        sources=[source],
    )

    first = build_project_evidence_registry(facts).snapshot()
    second = build_project_evidence_registry(facts).snapshot()
    assert first == second

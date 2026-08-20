import pytest

from hara_agent.models import (
    ConstraintOperator, DriverContextFact, DriverLocation, FactProvenance,
    ItemDefinitionFacts, NumericConstraintFact, ReviewStatus, SourceRef,
)


SOURCE = SourceRef("item_definition", "ItemDef.docx", "table[1].row[2]", "响应时间≤50ms")


def test_numeric_and_driver_facts_are_typed_atomic_project_input():
    numeric = NumericConstraintFact(
        "performance.brake_response", "brake_response_time",
        ConstraintOperator.LE, 50.0, "ms", {"condition": "parking_brake_control"},
        source_refs=[SOURCE],
    )
    driver = DriverContextFact(
        "driver.inside", DriverLocation.INSIDE, "", "", [SOURCE]
    )
    facts = ItemDefinitionFacts.from_dict({
        "system_description": "system", "item_boundary": "boundary",
        "sources": [SOURCE.__dict__],
        "numeric_constraints": [numeric.__dict__],
        "driver_context_facts": [driver.__dict__],
    })
    assert facts.numeric_constraints[0].operator is ConstraintOperator.LE
    assert facts.numeric_constraints[0].provenance is FactProvenance.PROJECT_INPUT
    assert facts.numeric_constraints[0].approval is ReviewStatus.PENDING
    assert facts.numeric_constraints[0].extracted_by == "LLM"
    assert facts.driver_context_facts[0].driver_location is DriverLocation.INSIDE


def test_numeric_fact_rejects_non_atomic_range_and_missing_source():
    with pytest.raises(ValueError, match="value_max"):
        NumericConstraintFact(
            "speed", "speed", ConstraintOperator.RANGE, 0, "km/h",
            value_max=None, source_refs=[SOURCE],
        )
    with pytest.raises(ValueError, match="SourceRef"):
        NumericConstraintFact(
            "performance.response", "response", ConstraintOperator.LE, 50, "ms"
        )

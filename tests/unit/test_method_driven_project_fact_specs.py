from hara_agent.contracts import FactOrigin, FactType, RequiredFactSpec
from hara_agent.models import ProjectFactOutputType
from hara_agent.services.extraction import (
    ProjectFactNormalizer, build_project_fact_spec_batches,
)
from hara_agent.workflow.nodes.item_artifacts import _speed_extraction_modes


def _required(fact_type, origin, unit="", constraints=()):
    return RequiredFactSpec(
        fact_type=fact_type,
        required_for=("EXPOSURE",),
        unit=unit,
        constraints=tuple(constraints),
        condition="",
        origin=origin,
        source_rule_ids=(),
        source_refs=(),
    )


def test_batches_are_compiled_from_modes_and_method_contract_only():
    batches = build_project_fact_spec_batches(
        ("Garage: automated operation", "Highway", "garage"),
        (
            _required(FactType.OCCURRENCE_FREQUENCY, FactOrigin.HUMAN_EVIDENCE),
            _required(FactType.COLLISION_TYPE, FactOrigin.SCENARIO_FACT),
            _required(FactType.FUNCTION, FactOrigin.PROJECT_FACT),
        ),
    )

    speed = batches["mode_speed_envelopes"]
    assert [item.context_hints for item in speed] == [("Garage",), ("Highway",)]
    risk = batches["method_risk_facts"]
    assert [item.fact_type for item in risk] == ["OCCURRENCE_FREQUENCY"]
    assert all("parking" not in repr(item).casefold() for item in (*speed, *risk))


def test_explicit_requested_mode_limits_speed_extraction_scope():
    assert _speed_extraction_modes(
        ("Active",),
        ("OFF", "Standby", "Active", "Override", "Abort", "Finish", "Error"),
    ) == ("Active",)


def test_declared_modes_are_used_when_no_mode_was_requested():
    assert _speed_extraction_modes((), ("search", "parking")) == (
        "search", "parking",
    )


def test_method_risk_fact_normalization_is_neutral_and_source_grounded():
    spec = build_project_fact_spec_batches(
        (),
        (_required(FactType.OCCURRENCE_FREQUENCY, FactOrigin.PROJECT_FACT),),
    )["method_risk_facts"][0]
    assert spec.output_type is ProjectFactOutputType.RISK_FACT
    result = ProjectFactNormalizer().normalize(
        (spec,),
        [{
            "fact_type": "OCCURRENCE_FREQUENCY",
            "status": "FOUND",
            "value": "F",
            "unit": "",
            "context": {},
            "source_block_id": "B1",
        }],
        [{
            "block_id": "B1",
            "location": "table[1].row[2]",
            "text": "OCCURRENCE_FREQUENCY = F",
        }],
        "ItemDef.docx",
    )

    assert not result.failures
    assert result.risk_facts[0].parameter == "OCCURRENCE_FREQUENCY"
    assert result.risk_facts[0].value == "F"
    assert result.risk_facts[0].fact_id.startswith("RF-")
    assert result.risk_facts[0].source_refs[0].location == "table[1].row[2]"


def test_method_decisions_and_executor_outputs_are_not_requested_from_item_definition():
    batches = build_project_fact_spec_batches((), (
        _required(FactType.EXPOSURE, FactOrigin.HUMAN_EVIDENCE),
        _required(
            FactType.AVOIDABILITY_PERCENT,
            FactOrigin.HUMAN_EVIDENCE,
            unit="%",
        ),
    ))

    assert "method_risk_facts" not in batches

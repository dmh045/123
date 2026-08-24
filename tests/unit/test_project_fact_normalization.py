from hara_agent.models import ProjectFactOutputType, ReviewStatus
from hara_agent.services.extraction import (
    ProjectFactNormalizer, RequiredProjectFactSpec,
)


def _speed_spec(name: str, alias: str) -> RequiredProjectFactSpec:
    return RequiredProjectFactSpec(
        f"speed.{name}", ProjectFactOutputType.SPEED_ENVELOPE,
        (alias,), ("km/h", "kph"), (name,),
        ("status", "operator", "value", "unit", "operating_mode", "source_block_id"),
    )


SPECS = (
    _speed_spec("search", "search speed"),
    _speed_spec("control", "control speed"),
    _speed_spec("maneuver", "maneuver speed"),
)
BLOCKS = [
    {"block_id": "S1", "location": "row[1]", "text": "search speed 0-30 kph; control speed 0-7 kph"},
    {"block_id": "S2", "location": "row[2]", "text": "maneuver speed <=5 km/h"},
]


def test_mode_speed_facts_are_atomic_and_source_grounded():
    raw = [
        {"fact_type": "speed.search", "status": "FOUND", "operator": "RANGE",
         "value": 0, "value_max": 30, "unit": "kph", "operating_mode": "search",
         "source_block_id": "S1"},
        {"fact_type": "speed.control", "status": "FOUND", "operator": "RANGE",
         "value": 0, "value_max": 7, "unit": "kph", "operating_mode": "control",
         "source_block_id": "S1"},
        {"fact_type": "speed.maneuver", "status": "FOUND", "operator": "LE",
         "value": 5, "unit": "km/h", "operating_mode": "maneuver",
         "source_block_id": "S2"},
    ]
    result = ProjectFactNormalizer().normalize(SPECS, raw, BLOCKS, "ItemDef.docx")

    assert not result.failures
    assert [(item.operating_mode, item.speed_max_kph) for item in result.speed_envelopes] == [
        ("search", 30), ("control", 7), ("maneuver", 5),
    ]
    assert result.speed_envelopes[-1].sources[0].location == "row[2]"
    assert all(
        item.status is ReviewStatus.FINALIZED
        for item in result.speed_envelopes
    )


def test_unknown_or_support_only_sources_fail_closed():
    spec = (SPECS[0],)
    raw = [{"fact_type": "speed.search", "status": "FOUND", "operator": "LE",
            "value": 30, "unit": "km/h", "operating_mode": "search",
            "source_block_id": "S2"}]
    result = ProjectFactNormalizer().normalize(
        spec, raw, BLOCKS, "ItemDef.docx", {"speed.search": {"S1"}},
    )

    assert not result.speed_envelopes
    assert result.failures[0].code == "UNKNOWN_SOURCE_REF"


def test_invalid_speed_operator_and_unit_fail_closed():
    base = {"fact_type": "speed.search", "status": "FOUND", "value": 30,
            "operating_mode": "search", "source_block_id": "S1"}
    invalid_operator = ProjectFactNormalizer().normalize(
        (SPECS[0],), [{**base, "operator": "ABOUT", "unit": "km/h"}],
        BLOCKS, "ItemDef.docx",
    )
    invalid_unit = ProjectFactNormalizer().normalize(
        (SPECS[0],), [{**base, "operator": "LE", "unit": "mph"}],
        BLOCKS, "ItemDef.docx",
    )

    assert invalid_operator.failures[0].code == "INVALID_OPERATOR"
    assert invalid_unit.failures[0].code == "INVALID_UNIT"

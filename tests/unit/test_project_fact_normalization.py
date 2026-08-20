from hara_agent.models import ConstraintOperator, DriverLocation
from hara_agent.services.extraction import (
    DRIVER_PROJECT_FACT_SPECS, PERFORMANCE_PROJECT_FACT_SPECS,
    SPEED_PROJECT_FACT_SPECS, ProjectFactNormalizer,
)


BLOCKS = [
    {"block_id": "P1", "location": "table[1].row[1]", "text": "泊车制动 | 响应时间≤50ms"},
    {"block_id": "D1", "location": "table[2].row[1]", "text": "位姿状态 | 在驾驶位/不在驾驶位"},
]


def test_normalizer_creates_atomic_numeric_and_exact_source_ref():
    spec = (PERFORMANCE_PROJECT_FACT_SPECS[2],)
    raw = [{"fact_type": "performance.brake_response", "status": "FOUND",
            "parameter": "brake_response_time", "operator": "LE", "value": 50,
            "unit": "ms", "condition": "parking_brake_control",
            "source_block_id": "P1", "source_excerpt": "响应时间≤50ms"}]
    result = ProjectFactNormalizer().normalize(spec, raw, BLOCKS, "ItemDef.docx")
    fact = result.numeric_constraints[0]
    assert fact.operator is ConstraintOperator.LE
    assert fact.value == 50
    assert fact.source_refs[0].location == "table[1].row[1]"
    assert not result.failures


def test_normalizer_keeps_two_driver_locations_as_two_atomic_facts():
    raw = [
        {"fact_type": "driver.inside", "status": "FOUND", "driver_location": "INSIDE",
         "control_mode": "", "condition": "", "source_block_id": "D1"},
        {"fact_type": "driver.outside", "status": "FOUND", "driver_location": "OUTSIDE",
         "control_mode": "", "condition": "", "source_block_id": "D1"},
    ]
    result = ProjectFactNormalizer().normalize(
        DRIVER_PROJECT_FACT_SPECS, raw, BLOCKS, "ItemDef.docx"
    )
    assert [item.driver_location for item in result.driver_context_facts] == [
        DriverLocation.INSIDE, DriverLocation.OUTSIDE
    ]


def test_driver_fact_type_can_only_be_recovered_from_unique_atomic_enum():
    raw = [
        {"status": "FOUND", "driver_location": "driver inside", "control_mode": "",
         "condition": "", "source_block_id": "D1"},
        {"status": "FOUND", "driver_location": "driver outside", "control_mode": "",
         "condition": "", "source_block_id": "D1"},
    ]
    result = ProjectFactNormalizer().normalize(
        DRIVER_PROJECT_FACT_SPECS, raw, BLOCKS, "ItemDef.docx"
    )
    assert [item.fact_type for item in result.driver_context_facts] == [
        "driver.inside", "driver.outside"
    ]
    assert all(
        item["identity_resolution"] == "DETERMINISTIC_DRIVER_ENUM"
        for item in result.coverage
    )


def test_unknown_source_ref_fails_closed_without_fuzzy_correction():
    raw = [{"fact_type": "performance.brake_response", "status": "FOUND",
            "parameter": "brake_response_time", "operator": "LE", "value": 50,
            "unit": "ms", "condition": "parking_brake_control",
            "source_block_id": "P-1", "source_excerpt": "响应时间≤50ms"}]
    result = ProjectFactNormalizer().normalize(
        (PERFORMANCE_PROJECT_FACT_SPECS[2],), raw, BLOCKS, "ItemDef.docx"
    )
    assert not result.numeric_constraints
    assert result.failures[0].code == "UNKNOWN_SOURCE_REF"


def test_support_only_neighbor_is_not_accepted_as_fact_source():
    spec = (PERFORMANCE_PROJECT_FACT_SPECS[2],)
    blocks = [
        *BLOCKS,
        {"block_id": "SUPPORT", "location": "table[1].row[2]", "text": "转向响应≤150ms"},
    ]
    raw = [{"fact_type": "performance.brake_response", "status": "FOUND",
            "parameter": "brake_response_time", "operator": "LE", "value": 150,
            "unit": "ms", "condition": "parking_brake_control",
            "source_block_id": "SUPPORT"}]
    result = ProjectFactNormalizer().normalize(
        spec, raw, blocks, "ItemDef.docx",
        {"performance.brake_response": {"P1"}},
    )
    assert not result.numeric_constraints
    assert result.failures[0].code == "UNKNOWN_SOURCE_REF"


def test_all_three_speed_envelopes_are_normalized_together():
    blocks = [
        {"block_id": "S1", "location": "table[1].row[1]",
         "text": "车速 | 搜索车位0-30kph，控车范围0-7kph"},
        {"block_id": "S2", "location": "table[2].row[1]",
         "text": "泊车时的最大车速 | ≤5km/h"},
    ]
    raw = [
        {"fact_type": "speed.search", "status": "FOUND", "operator": "RANGE",
         "value": 0, "value_max": 30, "unit": "kph", "operating_mode": "search",
         "source_block_id": "S1"},
        {"fact_type": "speed.control", "status": "FOUND", "operator": "RANGE",
         "value": 0, "value_max": 7, "unit": "kph", "operating_mode": "control",
         "source_block_id": "S1"},
        {"fact_type": "speed.parking", "status": "FOUND", "operator": "LE",
         "value": 5, "unit": "km/h", "operating_mode": "parking",
         "source_block_id": "S2"},
    ]
    result = ProjectFactNormalizer().normalize(
        SPEED_PROJECT_FACT_SPECS, raw, blocks, "ItemDef.docx"
    )
    assert [(item.operating_mode, item.speed_max_kph) for item in result.speed_envelopes] == [
        ("search", 30), ("control", 7), ("parking", 5)
    ]


def test_invalid_operator_and_unit_fail_closed():
    base = {"fact_type": "performance.brake_response", "status": "FOUND",
            "parameter": "brake_response_time", "value": 50,
            "condition": "parking_brake_control", "source_block_id": "P1"}
    invalid_operator = ProjectFactNormalizer().normalize(
        (PERFORMANCE_PROJECT_FACT_SPECS[2],),
        [{**base, "operator": "ABOUT", "unit": "ms"}], BLOCKS, "ItemDef.docx",
    )
    invalid_unit = ProjectFactNormalizer().normalize(
        (PERFORMANCE_PROJECT_FACT_SPECS[2],),
        [{**base, "operator": "LE", "unit": "minute"}], BLOCKS, "ItemDef.docx",
    )
    assert invalid_operator.failures[0].code == "INVALID_OPERATOR"
    assert invalid_unit.failures[0].code == "INVALID_UNIT"


def test_duplicate_typed_fact_is_rejected():
    spec = PERFORMANCE_PROJECT_FACT_SPECS[2]
    raw = [{"fact_type": spec.fact_type, "status": "FOUND",
            "parameter": "brake_response_time", "operator": "LE", "value": 50,
            "unit": "ms", "condition": "parking_brake_control", "source_block_id": "P1"}]
    result = ProjectFactNormalizer().normalize(
        (spec, spec), raw, BLOCKS, "ItemDef.docx"
    )
    assert len(result.numeric_constraints) == 1
    assert any(item.code == "DUPLICATE_FACT" for item in result.failures)

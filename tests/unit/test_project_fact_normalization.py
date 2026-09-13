import pytest

import pytest

from hara_agent.models import ConstraintOperator, ProjectFactOutputType, ReviewStatus
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


def test_structural_speed_spec_accepts_multiple_contextual_operating_envelopes():
    spec = RequiredProjectFactSpec(
        "speed.operational_context", ProjectFactOutputType.SPEED_ENVELOPE,
        ("operational speed",), ("km/h", "kph"), (),
        (
            "status", "operator", "value", "unit", "operating_mode",
            "condition", "semantic_scope", "source_block_id", "source_excerpt",
        ),
        structural_kind="OPERATIONAL_SPEED_CANDIDATE",
    )
    blocks = [{
        "block_id": "C1", "location": "table[14].row[15]",
        "text": "车速 | 搜索车位0-30kph，控车范围0-7kph",
    }]
    raw = [
        {
            "fact_type": spec.fact_type, "status": "FOUND",
            "operator": "RANGE", "value": 0, "value_max": 30,
            "unit": "kph", "operating_mode": "search",
            "condition": "parking-space search", "semantic_scope": "OPERATIONAL_SPEED",
            "source_block_id": "C1", "source_excerpt": "搜索车位0-30kph",
        },
        {
            "fact_type": spec.fact_type, "status": "FOUND",
            "operator": "RANGE", "value": 0, "value_max": 7,
            "unit": "kph", "operating_mode": "control",
            "condition": "vehicle control", "semantic_scope": "OPERATIONAL_SPEED",
            "source_block_id": "C1", "source_excerpt": "控车范围0-7kph",
        },
    ]

    result = ProjectFactNormalizer().normalize(
        (spec,), raw, blocks, "ItemDef.docx", {spec.fact_type: {"C1"}},
    )

    assert not result.failures
    assert [item.speed_max_kph for item in result.speed_envelopes] == [30.0, 7.0]
    assert result.coverage == [{
        "fact_type": spec.fact_type, "status": "FOUND", "normalized_count": 2,
        "identity_resolution": "EXPLICIT",
    }]


def test_structural_speed_spec_rejects_signed_controller_capability_range():
    spec = RequiredProjectFactSpec(
        "speed.operational_context", ProjectFactOutputType.SPEED_ENVELOPE,
        ("operational speed",), ("km/h", "kph"), (),
        (
            "status", "operator", "value", "unit", "operating_mode",
            "condition", "semantic_scope", "source_block_id", "source_excerpt",
        ),
        structural_kind="OPERATIONAL_SPEED_CANDIDATE",
    )
    result = ProjectFactNormalizer().normalize(
        (spec,), [{
            "fact_type": spec.fact_type, "status": "FOUND",
            "operator": "RANGE", "value": 0, "value_max": 15,
            "unit": "kph", "operating_mode": "control",
            "condition": "controller capability", "semantic_scope": "OPERATIONAL_SPEED",
            "source_block_id": "C1", "source_excerpt": "车速范围[-15,15]kph",
        }], [{
            "block_id": "C1", "location": "table[13].row[2]",
            "text": "泊车制动控制器能力 | 车速范围[-15,15]kph",
        }], "ItemDef.docx", {spec.fact_type: {"C1"}},
    )

    assert not result.speed_envelopes
    assert result.failures[0].code == "NOT_OPERATIONAL_SPEED"


@pytest.mark.parametrize(("excerpt", "operator", "value", "value_max", "mode"), (
    ("巡航最高车速 | ≤15km/h", "LE", 15, None, "cruise"),
    ("泊车时的最大车速 | ≤5km/h", "LE", 5, None, "parking"),
    ("车位搜索时最大车速 | ≤24km/h", "LE", 24, None, "search"),
    ("控车范围0-7kph", "RANGE", 0, 7, "control"),
))
def test_contextual_speed_examples_keep_exact_sources_and_ranges(
    excerpt, operator, value, value_max, mode,
):
    spec = RequiredProjectFactSpec(
        "speed.operational_context", ProjectFactOutputType.SPEED_ENVELOPE,
        ("operational speed",), ("km/h", "kph"), (),
        (
            "status", "operator", "value", "unit", "operating_mode",
            "condition", "semantic_scope", "source_block_id", "source_excerpt",
        ), structural_kind="OPERATIONAL_SPEED_CANDIDATE",
    )
    raw = {
        "fact_type": spec.fact_type, "status": "FOUND", "operator": operator,
        "value": value, "unit": "km/h", "operating_mode": mode,
        "condition": mode, "semantic_scope": "OPERATIONAL_SPEED",
        "source_block_id": "C1", "source_excerpt": excerpt,
    }
    if value_max is not None:
        raw["value_max"] = value_max
    result = ProjectFactNormalizer().normalize(
        (spec,), [raw],
        ({"block_id": "C1", "location": "row[1]", "text": excerpt},),
        "ItemDef.docx", {spec.fact_type: {"C1"}},
    )

    assert not result.failures
    fact = result.speed_envelopes[0]
    assert fact.sources[0].excerpt == excerpt
    assert fact.speed_max_kph == float(value_max if value_max is not None else value)
    assert not hasattr(fact, "speed_point_kph")


def test_structural_candidate_requires_semantic_confirmation():
    spec = RequiredProjectFactSpec(
        "speed.operational_context", ProjectFactOutputType.SPEED_ENVELOPE,
        ("operational speed",), ("km/h",), (),
        (
            "status", "operator", "value", "unit", "operating_mode",
            "condition", "semantic_scope", "source_block_id", "source_excerpt",
        ), structural_kind="OPERATIONAL_SPEED_CANDIDATE",
    )
    raw = {
        "fact_type": spec.fact_type, "status": "FOUND", "operator": "LE",
        "value": 5, "unit": "km/h", "operating_mode": "parking",
        "condition": "parking", "semantic_scope": "",
        "source_block_id": "C1", "source_excerpt": "速度≤5km/h",
    }
    result = ProjectFactNormalizer().normalize(
        (spec,), [raw],
        ({"block_id": "C1", "location": "row[1]", "text": "速度≤5km/h"},),
        "ItemDef.docx", {spec.fact_type: {"C1"}},
    )

    assert not result.speed_envelopes
    assert result.failures[0].code == "INVALID_SEMANTIC_SCOPE"


@pytest.mark.parametrize(("second_value", "expected_code"), (
    (7, "DUPLICATE_FACT"), (8, "CONFLICTING_FACT"),
))
def test_duplicate_and_conflicting_scoped_speed_facts_are_surfaced(
    second_value, expected_code,
):
    spec = RequiredProjectFactSpec(
        "speed.operational_context", ProjectFactOutputType.SPEED_ENVELOPE,
        ("operational speed",), ("km/h",), (),
        (
            "status", "operator", "value", "unit", "operating_mode",
            "condition", "semantic_scope", "source_block_id", "source_excerpt",
        ), structural_kind="OPERATIONAL_SPEED_CANDIDATE",
    )
    base = {
        "fact_type": spec.fact_type, "status": "FOUND", "operator": "LE",
        "value": 7, "unit": "km/h", "operating_mode": "control",
        "condition": "vehicle control", "semantic_scope": "OPERATIONAL_SPEED",
        "source_block_id": "C1", "source_excerpt": "控车范围≤7km/h",
    }
    result = ProjectFactNormalizer().normalize(
        (spec,), [base, {**base, "value": second_value}],
        ({"block_id": "C1", "location": "row[1]", "text": "控车范围≤7km/h"},),
        "ItemDef.docx", {spec.fact_type: {"C1"}},
    )

    assert len(result.speed_envelopes) == 1
    assert result.failures[0].code == expected_code


def test_unicode_and_legacy_mojibake_operators_are_normalized():
    base = {
        "fact_type": "speed.search", "status": "FOUND", "value": 30,
        "unit": "km/h", "operating_mode": "search", "source_block_id": "S1",
    }

    for operator in ("≤", "≦", "â‰¤", "â‰¦"):
        result = ProjectFactNormalizer().normalize(
            (SPECS[0],), [{**base, "operator": operator}], BLOCKS, "ItemDef.docx",
        )
        assert not result.failures
        assert result.speed_envelopes[0].speed_min_kph == 0
        assert result.speed_envelopes[0].speed_max_kph == 30

    for operator in ("≥", "≧", "â‰¥", "â‰§"):
        assert ProjectFactNormalizer.OPERATOR_ALIASES[operator] is ConstraintOperator.GE

    for operator in ("＞", "ï¼ž"):
        assert ProjectFactNormalizer.OPERATOR_ALIASES[operator] is ConstraintOperator.GT

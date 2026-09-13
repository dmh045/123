from __future__ import annotations

from hara_agent.services.extraction import (
    CoverageFirstContextAssembler,
    DeterministicEvidenceRetriever,
    FactRetrievalSpec,
)


def block(block_id, text, location, kind="table_row"):
    return {"block_id": block_id, "kind": kind, "location": location, "text": text}


def test_table_header_and_adjacent_row_are_bounded_structural_support():
    blocks = [
        block("T-1-R-1", "Item | Description", "table[1].row[1]"),
        block("T-1-R-2", "unrelated row", "table[1].row[2]"),
        block("T-1-R-3", "brake response time ms", "table[1].row[3]"),
        block("T-1-R-4", "brake steady state support", "table[1].row[4]"),
    ]
    spec = FactRetrievalSpec("brake.response", ("brake response",), ("ms",))
    rankings = DeterministicEvidenceRetriever().rank(blocks, (spec,))

    result = CoverageFirstContextAssembler().assemble(
        blocks, (spec,), rankings, task="project_evidence", max_characters=500,
    )

    assert "T-1-R-1" in result.block_ids
    assert "T-1-R-3" in result.block_ids
    assert "T-1-R-4" in result.block_ids
    assert result.selected_characters <= 500


def test_coverage_first_keeps_one_candidate_per_required_fact():
    blocks = [
        block("A-1", "brake response latency ms " + "a" * 20, "paragraph[1]", "paragraph"),
        block("A-2", "brake response latency ms " + "b" * 20, "paragraph[2]", "paragraph"),
        block("A-3", "brake response latency ms " + "c" * 20, "paragraph[3]", "paragraph"),
        block("B-1", "driver outside remote control", "paragraph[4]", "paragraph"),
    ]
    specs = (
        FactRetrievalSpec("brake.response", ("brake response",), ("ms",), ("latency",)),
        FactRetrievalSpec("driver.outside", ("driver outside",), context_hints=("remote",)),
    )
    rankings = DeterministicEvidenceRetriever().rank(blocks, specs)

    result = CoverageFirstContextAssembler().assemble(
        blocks, specs, rankings, task="project_evidence", max_characters=180,
    )

    assert "A-1" in result.block_ids
    assert "B-1" in result.block_ids
    assert len({item for item in result.block_ids if item.startswith("A-")}) < 3
    assert result.selected_characters <= 180
    assert all(item.coverage_status == "COVERED" for item in result.diagnostics.facts)


def test_duplicate_high_scores_do_not_consume_budget_before_other_fact():
    blocks = [
        block(f"A-{index}", "brake response ms " + "x" * 25, f"paragraph[{index}]", "paragraph")
        for index in range(1, 7)
    ] + [block("B-1", "steering error deg", "paragraph[7]", "paragraph")]
    specs = (
        FactRetrievalSpec("brake.response", ("brake response",), ("ms",)),
        FactRetrievalSpec("steering.error", ("steering error",), ("deg",)),
    )
    rankings = DeterministicEvidenceRetriever().rank(blocks, specs)

    result = CoverageFirstContextAssembler().assemble(
        blocks, specs, rankings, task="project_evidence", max_characters=150,
    )

    assert "A-1" in result.block_ids
    assert "B-1" in result.block_ids
    assert result.selected_characters <= 150


def test_structural_speed_retrieval_adds_recall_without_expected_terms_or_values():
    blocks = [
        block("S-1", "车速 | 搜索车位0-30kph，控车范围0-7kph", "table[14].row[15]"),
        block("S-2", "Active | vehicle speed <=20km/h", "table[9].row[4]"),
        block("S-3", "no numeric speed here", "paragraph[1]", "paragraph"),
    ]
    spec = FactRetrievalSpec(
        "speed.context", ("operational speed",), ("km/h", "kph"),
        structural_kind="OPERATIONAL_SPEED_CANDIDATE",
        exclusion_aliases=("Active",),
    )

    ranking = DeterministicEvidenceRetriever().rank(blocks, (spec,))[spec.fact_type]

    assert [item.block_id for item in ranking] == ["S-1"]
    assert "structure:numeric_speed_constraint" in ranking[0].reasons


def test_categorical_allowed_set_retrieval_requires_table_member_separator():
    blocks = [
        block("D-1", "位姿状态 | 在驾驶位/不在驾驶位", "table[14].row[13]"),
        block("D-2", "普通表格 | 单一值", "table[14].row[14]"),
        block("D-3", "状态信号 | CDC | IPB/EPS/VDC", "table[7].row[18]"),
    ]
    spec = FactRetrievalSpec(
        "driver", ("DRIVER_IN_VEHICLE",),
        structural_kind="CATEGORICAL_ALLOWED_SET_CANDIDATE",
    )

    ranking = DeterministicEvidenceRetriever().rank(blocks, (spec,))[spec.fact_type]

    assert [item.block_id for item in ranking] == ["D-1"]


def test_driver_context_candidate_can_be_routed_by_conditional_shape_only():
    blocks = [
        block(
            "D-1", "方式1、人驾且车辆进入区域，当车速≤20km/h时显示路径",
            "paragraph[104]", "paragraph",
        ),
        block("D-2", "普通说明段落", "paragraph[105]", "paragraph"),
    ]
    spec = FactRetrievalSpec(
        "driver", ("DRIVER_IN_VEHICLE",),
        structural_kind="CATEGORICAL_ALLOWED_SET_CANDIDATE",
    )

    ranking = DeterministicEvidenceRetriever().rank(blocks, (spec,))[spec.fact_type]

    assert [item.block_id for item in ranking] == ["D-1"]
    assert ranking[0].reasons == ("structure:conditional_context",)

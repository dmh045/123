from __future__ import annotations

import pytest

from hara_agent.models import (
    FunctionDefinition,
    ItemDefinitionFacts,
    SourceRef,
)
from hara_agent.services.semantic.item_artifact_agent import (
    ItemArtifactExtractionAgent,
)


def _source(excerpt: str) -> SourceRef:
    return SourceRef(
        source_type="item_definition",
        source_id="ItemDef.docx",
        location="table[1].row[2]",
        excerpt=excerpt,
    )


def _facts(excerpt: str) -> ItemDefinitionFacts:
    return ItemDefinitionFacts(
        system_description="AVP system",
        item_boundary="ADS ECU and vehicle interfaces",
        sources=[_source(excerpt)],
    )


def _function(excerpt: str) -> FunctionDefinition:
    return FunctionDefinition(
        function_id="F01",
        name="泊车控制",
        output="车辆运动",
        description="控制车辆泊车",
        sources=[_source(excerpt)],
        confidence=0.9,
    )


def test_source_grounding_accepts_docx_whitespace_and_nfkc_variants() -> None:
    document = "系统支持：\nAVP\u3000泊车控制。"

    ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持: AVP 泊车控制。"),
        [_function("AVP泊车控制。")],
        document,
    )


def test_source_grounding_rejects_paraphrase_and_reports_identity() -> None:
    with pytest.raises(ValueError, match="F01 source excerpt is not present"):
        ItemArtifactExtractionAgent._ensure_source_grounded(
            _facts("系统支持AVP泊车控制。"),
            [_function("系统可以在任何道路自动驾驶。")],
            "系统支持AVP泊车控制。",
        )


def test_source_grounding_rejects_ellipsis_joined_fragments() -> None:
    with pytest.raises(ValueError, match="item_definition source excerpt"):
        ItemArtifactExtractionAgent._ensure_source_grounded(
            _facts("系统支持AVP泊车……最高车速20km/h"),
            [],
            "系统支持AVP泊车。\n最高车速20km/h。",
        )


def test_source_grounding_rebinds_verbatim_sentences_to_real_blocks() -> None:
    first = "本文档为奇瑞EH架构中智驾域的自主代客泊车在功能安全开发中的相关项定义。"
    second = "系统支持在停车场中自主搜寻车位并在平面车位泊入，以及自动召唤泊出。"
    facts = _facts(first + second)
    blocks = [
        {
            "block_id": "P-0070",
            "location": "paragraph[70]",
            "text": first + "该文档的目的是定义和描述ADS功能。",
        },
        {
            "block_id": "T-003-R-0002",
            "location": "table[3].row[2]",
            "text": "ADAS系统 | 自主代客泊车AVP | " + second,
        },
    ]

    repairs = ItemArtifactExtractionAgent._ensure_source_grounded(
        facts,
        [],
        "\n".join(block["text"] for block in blocks),
        blocks,
    )

    assert [source.location for source in facts.sources] == [
        "paragraph[70]",
        "table[3].row[2]",
    ]
    assert [source.excerpt for source in facts.sources] == [first, second]
    assert repairs == [{
        "identity": "item_definition",
        "repair_kind": "sentence_split",
        "original_location": "table[1].row[2]",
        "repaired_locations": ["paragraph[70]", "table[3].row[2]"],
        "source_count": 2,
    }]


def test_source_grounding_rebinds_ordered_omission_to_complete_block() -> None:
    full_text = (
        "当车速低于20km/h时，驾驶员双击拨杆（复用巡航功能的硬开关，也可以是按键，"
        "根据具体车型讨论）激活AVP。如车速大于20km/h时，驾驶员双击拨杆将不能激活AVP，"
        "HMI界面有相应提示原因。"
    )
    shortened = (
        "当车速低于20km/h时，驾驶员双击拨杆激活AVP。"
        "如车速大于20km/h时，驾驶员双击拨杆将不能激活AVP"
    )
    function = _function(shortened)
    blocks = [{
        "block_id": "P-0108",
        "location": "paragraph[108]",
        "text": full_text,
    }]

    repairs = ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持AVP泊车控制。"),
        [function],
        "系统支持AVP泊车控制。\n" + full_text,
        blocks,
    )

    assert function.sources == [SourceRef(
        source_type="item_definition",
        source_id="ItemDef.docx",
        location="paragraph[108]",
        excerpt=full_text,
    )]
    assert repairs == [{
        "identity": "F01",
        "repair_kind": "ordered_omission",
        "original_location": "table[1].row[2]",
        "repaired_locations": ["paragraph[108]"],
        "source_count": 1,
    }]


def test_source_grounding_expands_explicit_ellipsis_across_block_range() -> None:
    texts = [
        "当在AVP巡航过程中，满足以下条件时：",
        "1、遇障碍物车速为零且超时（时间为标定值）；",
        "2、驾驶员干预档位；",
        "3、驾驶员干预加速踏板且超过车速上限；",
        "AVP任务将被取消。",
    ]
    blocks = [{
        "block_id": f"P-{index:04d}",
        "kind": "paragraph",
        "location": f"paragraph[{index}]",
        "text": text,
    } for index, text in enumerate(texts, start=127)]
    function = _function(
        "当在AVP巡航过程中，满足以下条件时：1、遇障碍物车速为零且超时；"
        "2、驾驶员干预档位；...AVP任务将被取消。"
    )

    repairs = ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持AVP泊车控制。"),
        [function],
        "系统支持AVP泊车控制。\n" + "\n".join(texts),
        blocks,
    )

    assert [source.location for source in function.sources] == [
        f"paragraph[{index}]" for index in range(127, 132)
    ]
    assert [source.excerpt for source in function.sources] == texts
    assert repairs == [{
        "identity": "F01",
        "repair_kind": "explicit_ellipsis_range",
        "original_location": "table[1].row[2]",
        "repaired_locations": [
            f"paragraph[{index}]" for index in range(127, 132)
        ],
        "source_count": 5,
    }]


def test_source_grounding_rebinds_each_sentence_with_bounded_omissions() -> None:
    full_texts = [
        (
            "OFF：未生效，不符合AVP基本工作条件的状态且特性不可激活。"
            "AVP的按钮呈现不可用状态，该状态下AVP关闭。"
        ),
        (
            "Standby：特性未生效，该状态下AVP控制依赖的硬件环境和软件环境准备完毕，"
            "AVP的HMI界面可打开。"
        ),
        (
            "Active：特性已生效且特性可去激活，AVP系统接管车辆横控纵控权限，"
            "根据车辆规划路径，进行低速自动驾驶、泊入车位、泊车车位等动作。"
        ),
    ]
    shortened = (
        "OFF：未生效，不符合AVP基本工作条件的状态且特性不可激活。"
        "Standby：特性未生效，硬件软件环境准备完毕，HMI界面可打开。"
        "Active：特性已生效，接管横纵控权限，进行低速自动驾驶、泊入等动作。"
    )
    blocks = [{
        "block_id": f"P-{index:04d}",
        "kind": "paragraph",
        "location": f"paragraph[{index}]",
        "text": text,
    } for index, text in enumerate(full_texts, start=92)]
    function = _function(shortened)

    repairs = ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持AVP泊车控制。"),
        [function],
        "系统支持AVP泊车控制。\n" + "\n".join(full_texts),
        blocks,
    )

    assert [source.location for source in function.sources] == [
        "paragraph[92]", "paragraph[93]", "paragraph[94]",
    ]
    assert [source.excerpt for source in function.sources] == [
        "OFF：未生效，不符合AVP基本工作条件的状态且特性不可激活。",
        full_texts[1],
        full_texts[2],
    ]
    assert repairs[0]["repair_kind"] == "sentence_split"

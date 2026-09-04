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

    repairs = ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持: AVP 泊车控制。"),
        [_function("AVP泊车控制。")],
        document,
    )

    assert repairs == []


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


def test_source_grounding_expands_ellipsis_with_multiple_trailing_fragments() -> None:
    texts = [
        "当在AVP巡航过程中，满足以下条件时：",
        "1、遇障碍物车速为零且超时（时间为标定值）；",
        "2、驾驶员干预档位；",
        "3、驾驶员干预方向盘；",
        "10、驾驶员主动关闭AVP界面；",
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
        "2、驾驶员干预档位；...10、驾驶员主动关闭AVP界面；AVP任务将被取消。"
    )

    repairs = ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持AVP泊车控制。"),
        [function],
        "系统支持AVP泊车控制。\n" + "\n".join(texts),
        blocks,
    )

    assert [source.excerpt for source in function.sources] == texts
    assert repairs[0]["repair_kind"] == "explicit_ellipsis_range"


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


def test_source_grounding_rebinds_unique_anchor_after_boundary_punctuation_change() -> None:
    original = (
        "方式1、人驾且未使用普通导航功能，当车辆进入停车场自构图区域且车速≤20km/h时，"
        "HMI自动显示停车场自构图：a、直接显示规划路径。"
    )
    llm_excerpt = (
        "人驾且未使用普通导航功能，当车辆进入停车场自构图区域且车速≤20km/h时，"
        "HMI自动显示停车场自构图。"
    )
    function = _function(llm_excerpt)
    blocks = [{
        "block_id": "P-0101",
        "kind": "paragraph",
        "location": "paragraph[101]",
        "text": original,
    }]

    repairs = ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持AVP泊车控制。"),
        [function],
        "系统支持AVP泊车控制。\n" + original,
        blocks,
    )

    assert function.sources == [SourceRef(
        source_type="item_definition",
        source_id="ItemDef.docx",
        location="paragraph[101]",
        excerpt=original,
    )]
    assert repairs == [{
        "identity": "F01",
        "repair_kind": "unique_contiguous_anchor_rebind",
        "original_location": "table[1].row[2]",
        "repaired_locations": ["paragraph[101]"],
        "source_count": 1,
    }]


def test_source_grounding_does_not_repair_semantic_rewrite() -> None:
    original = "车速≤20km/h时显示停车场地图："
    function = _function("车速低于30km/h时自动开启AVP。")
    blocks = [{
        "block_id": "P-0101",
        "kind": "paragraph",
        "location": "paragraph[101]",
        "text": original,
    }]

    with pytest.raises(ValueError, match="F01 source excerpt is not present"):
        ItemArtifactExtractionAgent._ensure_source_grounded(
            _facts("系统支持AVP泊车控制。"),
            [function],
            "系统支持AVP泊车控制。\n" + original,
            blocks,
        )


def test_source_grounding_does_not_repair_repeated_anchor() -> None:
    original = "人驾且未使用普通导航功能时，HMI自动显示停车场自构图："
    function = _function("人驾且未使用普通导航功能时，HMI自动显示停车场自构图。")
    blocks = [
        {
            "block_id": f"P-{index:04d}",
            "kind": "paragraph",
            "location": f"paragraph[{index}]",
            "text": original,
        }
        for index in (101, 202)
    ]

    with pytest.raises(ValueError, match="F01 source excerpt is not present"):
        ItemArtifactExtractionAgent._ensure_source_grounded(
            _facts("系统支持AVP泊车控制。"),
            [function],
            "系统支持AVP泊车控制。\n" + "\n".join(
                block["text"] for block in blocks
            ),
            blocks,
        )


def test_source_grounding_does_not_choose_shortest_repeated_sentence_block() -> None:
    first = "系统进入Active状态。"
    second = "系统输出车辆控制请求。"
    function = _function(first + second)
    blocks = [
        {
            "block_id": "P-0101",
            "kind": "paragraph",
            "location": "paragraph[101]",
            "text": first,
        },
        {
            "block_id": "P-0202",
            "kind": "paragraph",
            "location": "paragraph[202]",
            "text": first + "该状态允许车辆运动。",
        },
        {
            "block_id": "P-0303",
            "kind": "paragraph",
            "location": "paragraph[303]",
            "text": second,
        },
    ]

    with pytest.raises(ValueError, match="F01 source excerpt is not present"):
        ItemArtifactExtractionAgent._ensure_source_grounded(
            _facts("系统支持AVP泊车控制。"),
            [function],
            "系统支持AVP泊车控制。\n" + "\n".join(
                block["text"] for block in blocks
            ),
            blocks,
        )


def test_source_grounding_rebinds_unique_punctuation_only_variant() -> None:
    original = "HMI自动显示停车场自构图："
    function = _function("HMI自动显示停车场自构图。")
    blocks = [{
        "block_id": "P-0101",
        "kind": "paragraph",
        "location": "paragraph[101]",
        "text": original,
    }]

    ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持AVP泊车控制。"),
        [function],
        "系统支持AVP泊车控制。\n" + original,
        blocks,
    )

    assert function.sources[0].location == "paragraph[101]"
    assert function.sources[0].excerpt == original


def test_source_grounding_rebinds_unique_omitted_prefix_variant() -> None:
    original = "方式1、人驾且未使用普通导航功能时，HMI自动显示停车场自构图："
    function = _function("人驾且未使用普通导航功能时，HMI自动显示停车场自构图。")
    blocks = [{
        "block_id": "P-0101",
        "kind": "paragraph",
        "location": "paragraph[101]",
        "text": original,
    }]

    ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持AVP泊车控制。"),
        [function],
        "系统支持AVP泊车控制。\n" + original,
        blocks,
    )

    assert function.sources[0].location == "paragraph[101]"
    assert function.sources[0].excerpt == original


def test_source_grounding_rebinds_split_fragment_with_omission_and_punctuation() -> None:
    first = (
        "当车辆在停车场自构图区域内，且自车在车位内时，用户打开AVP界面时，"
        "将显示如下HMI界面；此时，用户可选择目标位置。"
    )
    second = (
        "用户点击停车场自构图可行驶区域（路径）后，HMI界面将显示用户点击生成的"
        "召唤点和规划路径；左侧界面将提示用户激活AVP。"
    )
    llm_excerpt = (
        "当车辆在停车场自构图区域内，且自车在车位内时，用户打开AVP界面时，"
        "将显示如下HMI界面；用户点击停车场自构图可行驶区域后，HMI界面将显示"
        "用户点击生成的召唤点和规划路径。"
    )
    function = _function(llm_excerpt)
    blocks = [
        {
            "block_id": "P-0116",
            "kind": "paragraph",
            "location": "paragraph[116]",
            "text": first,
        },
        {
            "block_id": "P-0117",
            "kind": "paragraph",
            "location": "paragraph[117]",
            "text": second,
        },
    ]

    repairs = ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持AVP泊车控制。"),
        [function],
        "系统支持AVP泊车控制。\n" + first + "\n" + second,
        blocks,
    )

    assert [source.location for source in function.sources] == [
        "paragraph[116]", "paragraph[117]",
    ]
    assert [source.excerpt for source in function.sources] == [
        "当车辆在停车场自构图区域内，且自车在车位内时，用户打开AVP界面时，"
        "将显示如下HMI界面；",
        second,
    ]
    assert repairs[0]["repair_kind"] == "sentence_split"


def test_source_grounding_rebinds_unique_one_sided_anchor_with_omissions() -> None:
    original = (
        "Active：特性已生效且特性可去激活，AVP系统接管车辆横控纵控权限，"
        "根据车辆规划路径，进行低速自动驾驶、泊入车位、泊车车位等动作。"
    )
    function = _function("Active：特性已生效，接管横纵控权限。")
    blocks = [{
        "block_id": "P-0094",
        "kind": "paragraph",
        "location": "paragraph[94]",
        "text": original,
    }]

    ItemArtifactExtractionAgent._ensure_source_grounded(
        _facts("系统支持AVP泊车控制。"),
        [function],
        "系统支持AVP泊车控制。\n" + original,
        blocks,
    )

    assert function.sources[0].location == "paragraph[94]"
    assert function.sources[0].excerpt == original

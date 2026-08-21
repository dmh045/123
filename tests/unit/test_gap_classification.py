from __future__ import annotations

import pytest

from hara_agent.evaluation.models import (
    ExpectedProjectFact,
    ExtractionEvaluationInput,
    GapClassification,
)
from hara_agent.evaluation.stages import ExtractionEvaluationHarness
from hara_agent.models import FactProvenance, SourceRef
from hara_agent.services.extraction import DocumentBlock


def test_gap_classification_is_stable_and_complete():
    assert [item.value for item in GapClassification] == [
        "PRESENT_EXPLICIT",
        "PRESENT_DERIVABLE",
        "EXTRACTOR_MISSED",
        "ATOMICIZATION_FAILED",
        "ROUTING_MISSED",
        "NORMALIZATION_LOSS",
        "METHOD_DEFINED",
        "SOURCE_NOT_PROVIDED",
    ]


@pytest.mark.parametrize(
    ("provenance", "derivable", "with_source", "expected"),
    [
        (FactProvenance.PROJECT_INPUT, False, True, "PRESENT_EXPLICIT"),
        (FactProvenance.PROJECT_INPUT, True, True, "PRESENT_DERIVABLE"),
        (FactProvenance.METHOD_CONTRACT, False, False, "METHOD_DEFINED"),
        (FactProvenance.PROJECT_INPUT, False, False, "SOURCE_NOT_PROVIDED"),
    ],
)
def test_fact_authority_and_source_drive_gap_classification(
    provenance, derivable, with_source, expected,
):
    block = DocumentBlock("B-1", "row", "row[1]", "parameter x value 1")
    source = SourceRef("synthetic", "synthetic", "row[1]", block.text)
    fact = ExpectedProjectFact(
        fact_id="x",
        field="risk_facts",
        value={"fact_type": "x", "parameter": "x", "value": 1},
        source=source if with_source else None,
        source_block_ids=("B-1",) if with_source else (),
        derivable=derivable,
        provenance=provenance,
    )
    project = {
        "risk_facts": [{
            "fact_type": "x", "parameter": "x", "value": 1,
            "source_refs": [{
                "source_type": "synthetic", "source_id": "synthetic",
                "location": "row[1]", "excerpt": block.text,
            }],
        }]
    } if expected != "SOURCE_NOT_PROVIDED" else {"risk_facts": []}

    report = ExtractionEvaluationHarness().evaluate(ExtractionEvaluationInput(
        source_id="synthetic",
        source_blocks=[block] if with_source else [],
        expected_facts=[fact],
        project_facts=project,
        routed_block_ids={"B-1"} if with_source else set(),
    ))

    assert report["facts"][0]["classification"] == expected

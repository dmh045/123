import pytest

from hara_agent.infrastructure.llm import LLMResponse
from hara_agent.models import (
    FunctionDefinition, GuidewordAssessment, ReviewStatus, SourceRef,
)
from hara_agent.services.semantic import GuidewordApplicabilityAgent, MalfunctionHazardAgent


def _candidate(identifier: str, guideword: str) -> dict:
    return {
        "malfunction_id": identifier,
        "guideword": guideword,
        "description": f"{guideword} deviation",
        "functional_effect": f"{guideword} effect",
        "vehicle_level_hazard": f"{guideword} hazardous vehicle state",
        "causal_chain": [f"{guideword} deviation", f"{guideword} vehicle effect"],
        "confidence": 0.8,
        "status": "PENDING",
    }


def _inputs():
    source = SourceRef("item_definition", "item.docx", "p1", "function evidence")
    function = FunctionDefinition(
        "F001", "localization", "vehicle pose", "provide vehicle pose",
        sources=[source], status=ReviewStatus.FINALIZED,
    )
    assessments = [
        GuidewordAssessment(
            "F001", guideword, True, "semantically applicable",
            sources=[source], status=ReviewStatus.FINALIZED, confidence=0.8,
        )
        for guideword in ("loss", "too late")
    ]
    return function, assessments


def test_missing_guideword_is_repaired_once_and_then_strictly_validated():
    class Client:
        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                assert "每一个Guideword至少返回一个" in request.user_prompt
                return LLMResponse(
                    data={"candidates": [_candidate("M1", "loss")]}, model="fake",
                )
            assert request.metadata["coverage_repair"] is True
            assert "MissingGuidewords=['too late']" in request.user_prompt
            return LLMResponse(
                data={"candidates": [_candidate("M2", "too late")]}, model="fake",
            )

    function, assessments = _inputs()
    client = Client()
    candidates, audit = MalfunctionHazardAgent(client).generate(function, assessments)

    assert {item.guideword for item in candidates} == {"loss", "too late"}
    assert audit["coverage_repair_count"] == 1
    assert audit["coverage_missing_before_repair"] == ["too late"]
    assert audit["llm_call_count"] == 2
    assert all(item.status is ReviewStatus.FINALIZED for item in candidates)


def test_coverage_repair_fails_closed_when_missing_identity_remains_missing():
    class Client:
        def complete_json(self, request):
            if request.metadata.get("coverage_repair"):
                return LLMResponse(data={"candidates": []}, model="fake")
            return LLMResponse(
                data={"candidates": [_candidate("M1", "loss")]}, model="fake",
            )

    function, assessments = _inputs()
    with pytest.raises(ValueError, match="缺少Malfunction候选"):
        MalfunctionHazardAgent(Client()).generate(function, assessments)


def test_blanket_guideword_selection_is_audited_without_overriding_semantics():
    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [{
                "guideword": guideword,
                "applicable": True,
                "rationale": "the output has this deviation dimension",
                "confidence": 0.8,
                "status": "PENDING",
            } for guideword in ("loss", "too late")]}, model="fake")

    function, _ = _inputs()
    assessments, audit = GuidewordApplicabilityAgent(Client()).assess(
        function, ["loss", "too late"],
    )

    assert all(item.applicable for item in assessments)
    assert all(item.status is ReviewStatus.FINALIZED for item in assessments)
    assert audit["blanket_applicability"] is True

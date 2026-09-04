import pytest

from hara_agent.infrastructure.llm import LLMResponse
from hara_agent.models import (
    FunctionDefinition, GuidewordAssessment, GuidewordDisposition,
    ReviewStatus, SourceRef,
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
        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            if request.metadata.get("coverage_repair"):
                return LLMResponse(data={"candidates": []}, model="fake")
            return LLMResponse(
                data={"candidates": [_candidate("M1", "loss")]}, model="fake",
            )

    function, assessments = _inputs()
    client = Client()
    with pytest.raises(ValueError, match="缺少Malfunction候选"):
        MalfunctionHazardAgent(client).generate(function, assessments)

    # Initial generation, grouped repair, then one targeted repair for the
    # still-missing guideword. Only a genuinely unresolved identity fails.
    assert len(client.requests) == 3


def test_grouped_repair_keeps_partial_success_and_targets_only_remaining_identity():
    class Client:
        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return LLMResponse(data={"candidates": []}, model="fake")
            if len(self.requests) == 2:
                return LLMResponse(
                    data={"candidates": [_candidate("M1", "loss")]}, model="fake",
                )
            assert request.metadata["guideword_count"] == 1
            assert "MissingGuidewords=['too late']" in request.user_prompt
            return LLMResponse(
                data={"candidates": [_candidate("M2", "too late")]}, model="fake",
            )

    function, assessments = _inputs()
    candidates, audit = MalfunctionHazardAgent(Client()).generate(function, assessments)

    assert {item.guideword for item in candidates} == {"loss", "too late"}
    assert audit["coverage_missing_after_grouped_repair"] == ["too late"]
    assert audit["coverage_repair_count"] == 2
    assert audit["llm_call_count"] == 3


@pytest.mark.parametrize(
    ("chain", "expected"),
    [
        ("output lost -> maneuver unavailable", ["output lost", "maneuver unavailable"]),
        ('["output lost", "maneuver unavailable"]', ["output lost", "maneuver unavailable"]),
    ],
)
def test_explicit_scalar_causal_chain_is_safely_normalized(chain, expected):
    function, assessments = _inputs()
    raw = _candidate("M1", "loss")
    raw["causal_chain"] = chain

    candidate = MalfunctionHazardAgent._parse(function, raw, assessments[0])

    assert candidate.causal_chain == expected


def test_unsegmented_scalar_causal_chain_is_rejected_without_character_splitting():
    function, assessments = _inputs()
    raw = _candidate("M1", "loss")
    raw["causal_chain"] = "output loss causes maneuver unavailability"

    with pytest.raises(ValueError, match="no explicit segment boundary"):
        MalfunctionHazardAgent._parse(function, raw, assessments[0])


def test_f01_style_batch_normalizes_format_only_variations_without_repair_call():
    guidewords = [
        "always active", "as well as", "different to", "incomplete",
        "loss", "too early", "too late", "unintended",
    ]
    source = SourceRef("item_definition", "item.docx", "p1", "function evidence")
    function = FunctionDefinition(
        "F01", "parking control", "vehicle motion", "control parking motion",
        sources=[source], status=ReviewStatus.FINALIZED,
    )
    assessments = [
        GuidewordAssessment(
            "F01", guideword, True, "semantically applicable",
            sources=[source], status=ReviewStatus.FINALIZED, confidence=0.8,
        )
        for guideword in guidewords
    ]

    class Client:
        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            candidates = []
            for index, guideword in enumerate(guidewords):
                item = _candidate("" if index == 0 else "M", guideword.upper())
                item["description"] = f"{guideword} deviation"
                item["causal_chain"] = (
                    f"{guideword} function deviation → {guideword} vehicle hazard"
                )
                candidates.append(item)
            return LLMResponse(data={"candidates": candidates}, model="fake")

    client = Client()
    candidates, audit = MalfunctionHazardAgent(client).generate(function, assessments)

    assert len(candidates) == len(guidewords)
    assert {item.guideword for item in candidates} == set(guidewords)
    assert len({item.malfunction_id for item in candidates}) == len(guidewords)
    assert all(len(item.causal_chain) == 2 for item in candidates)
    assert audit["coverage_repair_count"] == 0
    assert audit["llm_call_count"] == 1
    assert len(client.requests) == 1


def test_blanket_guideword_selection_is_audited_without_overriding_semantics():
    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [{
                "guideword": guideword,
                "applicable": True,
                "disposition": "DOWNSTREAM_CANDIDATE",
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


def test_no_credible_hazard_skips_malfunction_llm_call():
    class Client:
        def complete_json(self, request):
            raise AssertionError("no-hazard disposition must not call the LLM")

    function, _ = _inputs()
    assessment = GuidewordAssessment(
        "F001",
        "different to",
        True,
        "the difference cannot change vehicle-level behavior",
        sources=list(function.sources),
        status=ReviewStatus.FINALIZED,
        confidence=0.8,
        disposition=GuidewordDisposition.NO_CREDIBLE_HAZARD,
    )

    candidates, audit = MalfunctionHazardAgent(Client()).generate(
        function, [assessment],
    )

    assert candidates == []
    assert audit["skipped"] is True
    assert audit["skip_reason"] == "no_credible_hazard_guidewords"
    assert audit["llm_call_count"] == 0

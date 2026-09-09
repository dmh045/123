from types import SimpleNamespace

import pytest

from hara_agent.contracts import Guideword, SourceRef as MethodSourceRef
from hara_agent.infrastructure.llm import LLMOutputLimitError, LLMResponse
from hara_agent.models import (
    FunctionDefinition,
    GuidewordAssessment,
    GuidewordDisposition,
    ReviewStatus,
    SourceRef,
)
from hara_agent.services.semantic import GuidewordApplicabilityAgent
from hara_agent.services.semantic.malfunction_guideword_gate import (
    validate_malfunction_guideword_gate,
)
from hara_agent.workflow.nodes.hazop import assess_guidewords
from hara_agent.workflow.state import HARAState, WorkflowStage


def _function(function_id: str = "F01") -> FunctionDefinition:
    """Construct a fully-evidenced FunctionDefinition for guideword assessment tests.

    The function is FINALIZED with source evidence, so that _parse() can
    propagate ReviewStatus.FINALIZED when rationale and sources are both
    present — this is the precondition for Case 6 (system-owned ReviewStatus).
    """
    return FunctionDefinition(
        function_id=function_id,
        name="parking motion control",
        output="longitudinal and lateral motion request",
        description="control vehicle motion during automated parking",
        preconditions=["AVP is active"],
        triggers=["a valid trajectory is available"],
        odd_constraints=["parking area"],
        fallback_behavior="request a controlled stop",
        consequences=["vehicle motion can deviate from the trajectory"],
        sources=[SourceRef(
            "item_definition",
            "item.docx",
            "Detailed function description / motion control",
            "The system controls longitudinal and lateral vehicle motion.",
        )],
        status=ReviewStatus.FINALIZED,
        confidence=0.9,
    )


def _method_guideword(name: str, description: str, order: int) -> Guideword:
    source = MethodSourceRef.create(
        workbook="template.xlsx",
        template_hash="template-hash",
        sheet="Guidewords",
        range=f"A{order + 1}:B{order + 1}",
        raw_text=f"{name}\t{description}",
    )
    return Guideword(f"GW-{order:03d}", name, description, order, source)


def test_full_semantic_context_and_high_applicability_self_check_use_one_call():
    guidewords = [
        _method_guideword("loss", "Complete absence of the intended output", 1),
        _method_guideword("too late", "The intended output occurs later than required", 2),
        _method_guideword("reverse", "The output direction is opposite to intended", 3),
        _method_guideword("always active", "The output remains active continuously", 4),
    ]

    class Client:
        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            return LLMResponse(data={"assessments": [
                {
                    "guideword": item.guideword_id,
                    "applicable": item.name != "always active",
                    "disposition": (
                        "DOWNSTREAM_CANDIDATE"
                        if item.name != "always active" else "NOT_APPLICABLE"
                    ),
                    "rationale": f"checked {item.name} against the function output",
                    "confidence": 0.8,
                }
                for item in guidewords
            ]}, model="fake")

    client = Client()
    assessments, audit = GuidewordApplicabilityAgent(client).assess(
        _function(), guidewords,
    )

    assert len(client.requests) == 1
    prompt = client.requests[0].user_prompt
    assert "Complete absence of the intended output" in prompt
    assert "a valid trajectory is available" in prompt
    assert "request a controlled stop" in prompt
    assert "Detailed function description / motion control" in prompt
    assert len(assessments) == 4
    assert [item.guideword_id for item in assessments] == [
        "GW-001", "GW-002", "GW-003", "GW-004",
    ]
    assert audit["near_blanket_applicability"] is True
    assert audit["additional_llm_calls"] == 0
    assert client.requests[0].max_tokens == 4096
    assert audit["output_limit_retry"] is False


def _guideword_payload(guidewords):
    return {"assessments": [
        {
            "guideword": item.guideword_id,
            "applicable": False,
            "disposition": "NOT_APPLICABLE",
            "rationale": "The function has no matching deviation dimension.",
            "confidence": 0.8,
        }
        for item in guidewords
    ]}


def _real_provider_guidewords() -> list[Guideword]:
    names = [
        "No/Loss", "More", "Less", "Reverse/Opposite",
        "As well as/Other than", "Stuck", "Early", "Late",
    ]
    stable_ids = [
        "GW-NO-LOSS", "GW-MORE", "GW-LESS", "GW-REVERSE",
        "GW-OTHER", "GW-STUCK", "GW-EARLY", "GW-LATE",
    ]
    return [
        Guideword(
            stable_id, name, "Bounded guideword definition", index,
            _method_guideword(name, "Bounded guideword definition", index).source_ref,
        )
        for index, (stable_id, name) in enumerate(zip(stable_ids, names, strict=True), start=1)
    ]


def test_output_limit_retries_once_at_8192_and_preserves_eight_assessments():
    guidewords = [
        _method_guideword(f"gw-{index}", "A bounded deviation dimension", index)
        for index in range(8)
    ]

    class Client:
        config = SimpleNamespace(max_tokens=32768)

        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise LLMOutputLimitError(
                    "truncated",
                    diagnostics={
                        "finish_reason": "length",
                        "prompt_tokens": 1200,
                        "completion_tokens": 4096,
                    },
                )
            return LLMResponse(data=_guideword_payload(guidewords), model="fake")

    client = Client()
    assessments, audit = GuidewordApplicabilityAgent(client).assess(
        _function(), guidewords,
    )

    assert len(assessments) == 8
    assert [request.max_tokens for request in client.requests] == [4096, 8192]
    assert audit["llm_call_count"] == 2
    assert audit["additional_llm_calls"] == 1
    assert audit["output_limit_retry"] is True
    assert audit["output_limit_retry_audit"] == {
        "function_id": "F01",
        "initial_max_tokens": 4096,
        "retry_max_tokens": 8192,
        "attempt": 2,
        "failed_attempt": 1,
        "finish_reason": "length",
        "prompt_tokens": 1200,
        "completion_tokens": 4096,
    }


def test_output_limit_retry_stops_after_second_length_failure():
    guidewords = [_method_guideword("loss", "Complete absence", 1)]

    class Client:
        config = SimpleNamespace(max_tokens=32768)

        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            raise LLMOutputLimitError(
                "truncated",
                diagnostics={"finish_reason": "length"},
            )

    client = Client()
    with pytest.raises(LLMOutputLimitError) as caught:
        GuidewordApplicabilityAgent(client).assess(_function(), guidewords)

    assert len(client.requests) == 2
    assert [request.max_tokens for request in client.requests] == [4096, 8192]
    assert caught.value.diagnostics["output_limit_retry"] is True


def test_configured_provider_limit_4096_fails_closed_without_expansion():
    guidewords = [_method_guideword("loss", "Complete absence", 1)]

    class Client:
        config = SimpleNamespace(max_tokens=4096)

        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            raise LLMOutputLimitError(
                "truncated",
                diagnostics={"finish_reason": "length"},
            )

    client = Client()
    with pytest.raises(LLMOutputLimitError) as caught:
        GuidewordApplicabilityAgent(client).assess(_function(), guidewords)

    assert len(client.requests) == 1
    assert client.requests[0].max_tokens == 4096
    assert caught.value.diagnostics["output_limit_retry"] is False


def test_guideword_request_schema_is_exact_for_expected_identity_and_count():
    guidewords = [
        _method_guideword("loss", "Complete absence", 1),
        _method_guideword("too late", "Delayed output", 2),
    ]

    class Client:
        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            return LLMResponse(data=_guideword_payload(guidewords), model="fake")

    client = Client()
    GuidewordApplicabilityAgent(client).assess(_function(), guidewords)
    schema = client.requests[0].response_schema
    assessment_schema = schema["properties"]["assessments"]
    assert assessment_schema["minItems"] == 2
    assert assessment_schema["maxItems"] == 2
    assert assessment_schema["items"]["properties"]["guideword"]["enum"] == [
        "GW-001", "GW-002",
    ]
    assert assessment_schema["items"]["properties"]["rationale"]["maxLength"] == 240


def test_real_provider_shaped_stable_ids_bind_to_display_names_and_pass_downstream_gate():
    guidewords = [
        _method_guideword(name, "Bounded guideword definition", index)
        for index, name in enumerate((
            "No/Loss", "More", "Less", "Reverse/Opposite",
            "As well as/Other than", "Stuck", "Early", "Late",
        ), start=1)
    ]
    stable_ids = [
        "GW-NO-LOSS", "GW-MORE", "GW-LESS", "GW-REVERSE",
        "GW-OTHER", "GW-STUCK", "GW-EARLY", "GW-LATE",
    ]
    guidewords = [
        Guideword(stable_id, item.name, item.description, item.order, item.source_ref)
        for stable_id, item in zip(stable_ids, guidewords, strict=True)
    ]

    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [
                {
                    "guideword": guideword.guideword_id,
                    "applicable": True,
                    "disposition": (
                        "DOWNSTREAM_CANDIDATE" if index == 0 else "NO_CREDIBLE_HAZARD"
                    ),
                    "rationale": "The fixed Function has a bounded deviation dimension.",
                    "confidence": 0.8,
                }
                for index, guideword in enumerate(guidewords)
            ]}, model="fake")

    assessments, audit = GuidewordApplicabilityAgent(Client()).assess(
        _function(), guidewords,
    )

    assert audit["coverage_count"] == 8
    assert [item.guideword_id for item in assessments] == stable_ids
    assert [item.guideword for item in assessments] == [item.name for item in guidewords]
    assessment = assessments[0]
    assert validate_malfunction_guideword_gate(
        function_id="F01",
        guideword=assessment.guideword,
        guideword_id=assessment.guideword_id,
        assessment=assessment,
        scheduled_guideword_ids=set(stable_ids),
    ) is assessment


@pytest.mark.parametrize("payload, message", [
    ([{"guideword": "GW-UNKNOWN"}], "unknown guideword_id"),
    ([{"guideword": "GW-001"}, {"guideword": "GW-001"}], "identity duplicate"),
    ([{"guideword": ""}], "missing guideword_id"),
    ([{"guideword": "GW-001", "guideword_name": "More"}], "expected_name='No/Loss'"),
])
def test_provider_identity_contract_fails_closed(payload, message):
    guidewords = [
        Guideword(
            "GW-001", "No/Loss", "Complete absence", 1,
            _method_guideword("No/Loss", "Complete absence", 1).source_ref,
        ),
    ]

    class Client:
        def complete_json(self, _request):
            return LLMResponse(data={"assessments": [
                {
                    **item,
                    "applicable": False,
                    "disposition": "NOT_APPLICABLE",
                    "rationale": "Not applicable for this fixed test.",
                    "confidence": 0.8,
                }
                for item in payload
            ]}, model="fake")

    with pytest.raises(ValueError, match=message):
        GuidewordApplicabilityAgent(Client()).assess(_function(), guidewords)


@pytest.mark.parametrize("bad_disposition", [
    "NO_CREDIBLE_HAZARD", "DOWNSTREAM_CANDIDATE",
])
def test_invalid_false_applicability_item_is_salvaged_without_recalling_siblings(
    bad_disposition,
):
    guidewords = _real_provider_guidewords()
    calls = []

    class Client:
        def complete_json(self, request):
            calls.append(request)
            if len(calls) == 1:
                return LLMResponse(data={"assessments": [
                    {
                        "guideword": guideword.guideword_id,
                        "applicable": False,
                        "disposition": (
                            bad_disposition if index == 7 else "NOT_APPLICABLE"
                        ),
                        "rationale": f"initial sibling {index}",
                        "confidence": 0.8,
                    }
                    for index, guideword in enumerate(guidewords)
                ]}, model="fake")
            assert request.metadata["item_repair"] is True
            assert request.metadata["guideword_id"] == "GW-LATE"
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-LATE",
                "applicable": False,
                "disposition": "NOT_APPLICABLE",
                "rationale": "repaired item only",
                "confidence": 0.8,
            }]}, model="fake")

    assessments, audit = GuidewordApplicabilityAgent(Client()).assess(
        _function(), guidewords,
    )

    assert len(calls) == 2
    assert calls[0].metadata.get("item_repair") is None
    assert len(assessments) == 8
    assert [item.guideword_id for item in assessments] == [
        item.guideword_id for item in guidewords
    ]
    assert [item.rationale for item in assessments[:-1]] == [
        f"initial sibling {index}" for index in range(7)
    ]
    assert assessments[-1].rationale == "repaired item only"
    assert audit["item_salvage_attempted_count"] == 1
    assert audit["item_salvage_repaired_count"] == 1
    assert audit["llm_call_count"] == 2


def test_true_not_applicable_item_is_salvaged_once():
    guidewords = _real_provider_guidewords()

    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, request):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(data={"assessments": [
                    {
                        "guideword": guideword.guideword_id,
                        "applicable": True,
                        "disposition": (
                            "NOT_APPLICABLE" if index == 0 else "DOWNSTREAM_CANDIDATE"
                        ),
                        "rationale": "initial assessment",
                        "confidence": 0.8,
                    }
                    for index, guideword in enumerate(guidewords)
                ]}, model="fake")
            assert request.metadata["guideword_id"] == "GW-NO-LOSS"
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-NO-LOSS",
                "applicable": True,
                "disposition": "DOWNSTREAM_CANDIDATE",
                "rationale": "repaired legal assessment",
                "confidence": 0.8,
            }]}, model="fake")

    client = Client()
    assessments, audit = GuidewordApplicabilityAgent(client).assess(_function(), guidewords)
    assert client.calls == 2
    assert assessments[0].disposition is GuidewordDisposition.DOWNSTREAM_CANDIDATE
    assert audit["item_salvage_repaired_count"] == 1


def test_item_salvage_second_invalid_response_fails_closed_without_third_call():
    guidewords = _real_provider_guidewords()

    class Client:
        def __init__(self):
            self.calls = 0

        def complete_json(self, _request):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(data={"assessments": [
                    {
                        "guideword": guideword.guideword_id,
                        "applicable": False,
                        "disposition": (
                            "NO_CREDIBLE_HAZARD" if index == 0 else "NOT_APPLICABLE"
                        ),
                        "rationale": "initial assessment",
                        "confidence": 0.8,
                    }
                    for index, guideword in enumerate(guidewords)
                ]}, model="fake")
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-NO-LOSS",
                "applicable": False,
                "disposition": "NO_CREDIBLE_HAZARD",
                "rationale": "still invalid",
                "confidence": 0.8,
            }]}, model="fake")

    client = Client()
    with pytest.raises(ValueError, match="GUIDEWORD_ITEM_REPAIR_CONTRACT_VIOLATION"):
        GuidewordApplicabilityAgent(client).assess(_function(), guidewords)
    assert client.calls == 2


def test_no_credible_hazard_is_retained_but_does_not_enter_malfunction_generation():
    guidewords = [
        _method_guideword("loss", "Complete absence of the intended output", 1),
        _method_guideword("different to", "Output differs from the intended value", 2),
    ]

    class Client:
        def __init__(self):
            self.requests = []

        def complete_json(self, request):
            self.requests.append(request)
            return LLMResponse(data={"assessments": [
                {
                    "guideword": "GW-001",
                    "applicable": True,
                    "disposition": "DOWNSTREAM_CANDIDATE",
                    "rationale": "loss can remove the vehicle motion request",
                    "confidence": 0.8,
                },
                {
                    "guideword": "GW-002",
                    "applicable": True,
                    "disposition": "NO_CREDIBLE_HAZARD",
                    "rationale": "the stated difference cannot change vehicle behavior",
                    "confidence": 0.8,
                },
            ]}, model="fake")

    client = Client()
    assessments, audit = GuidewordApplicabilityAgent(client).assess(
        _function(), guidewords,
    )

    assert len(client.requests) == 1
    by_name = {item.guideword: item for item in assessments}
    assert by_name["loss"].enters_downstream is True
    assert by_name["different to"].disposition is GuidewordDisposition.NO_CREDIBLE_HAZARD
    assert by_name["different to"].enters_downstream is False
    assert audit["downstream_candidate_count"] == 1
    assert audit["no_credible_hazard_count"] == 1


def test_hazop_matrix_persists_negative_decisions_and_audits_zero_match():
    functions = [_function("F01"), _function("F02")]

    class Agent:
        def __init__(self):
            self.calls = 0

        def assess(self, function, guidewords):
            self.calls += 1
            assessments = [
                GuidewordAssessment(
                    function_id=function.function_id,
                    guideword=guideword,
                    applicable=guideword == "loss",
                    rationale=(
                        "loss can suppress the output"
                        if guideword == "loss"
                        else "the output has no reversible semantic dimension"
                    ),
                    sources=list(function.sources),
                    status=ReviewStatus.FINALIZED,
                    confidence=0.8,
                )
                for guideword in guidewords
            ]
            return assessments, {
                "function_id": function.function_id,
                "blanket_applicability": False,
                "near_blanket_applicability": False,
            }

    state = assess_guidewords(
        HARAState(run_id="guideword-matrix"),
        Agent(),
        functions,
        ["loss", "reverse"],
    )

    assert state.stage is WorkflowStage.HAZOP
    assert len(state.guideword_assessments) == 4
    assert state.malfunctions == []
    negative = [
        item for item in state.guideword_assessments
        if item["guideword"] == "reverse"
    ]
    assert len(negative) == 2
    assert all(item["applicable"] is False for item in negative)
    assert all(item["rationale"] for item in negative)
    coverage = next(
        item for item in state.audit_trail
        if item["event"] == "guideword_global_coverage_audited"
    )
    assert coverage["unmatched_guidewords"] == ["reverse"]
    assert coverage["action"] == "audit_only_no_forced_match"


def test_case1_applicable_true_downstream_candidate_passes():
    guidewords = [_method_guideword("loss", "Complete absence of the intended output", 1)]

    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-001",
                "applicable": True,
                "disposition": "DOWNSTREAM_CANDIDATE",
                "rationale": "loss removes the output",
                "confidence": 0.8,
            }]}, model="fake")

    assessments, audit = GuidewordApplicabilityAgent(Client()).assess(_function(), guidewords)
    assert assessments[0].disposition is GuidewordDisposition.DOWNSTREAM_CANDIDATE
    assert assessments[0].enters_downstream is True
    assert assessments[0].applicable is True


def test_case2_applicable_true_no_credible_hazard_passes_but_not_downstream():
    guidewords = [_method_guideword("different to", "Output differs from the intended value", 1)]

    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-001",
                "applicable": True,
                "disposition": "NO_CREDIBLE_HAZARD",
                "rationale": "difference cannot form credible vehicle hazard",
                "confidence": 0.7,
            }]}, model="fake")

    assessments, audit = GuidewordApplicabilityAgent(Client()).assess(_function(), guidewords)
    assert assessments[0].disposition is GuidewordDisposition.NO_CREDIBLE_HAZARD
    assert assessments[0].enters_downstream is False
    assert assessments[0].applicable is True


def test_case3_applicable_false_not_applicable_passes():
    guidewords = [_method_guideword("always active", "The output remains active continuously", 1)]

    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-001",
                "applicable": False,
                "disposition": "NOT_APPLICABLE",
                "rationale": "no semantic dimension for always-active in this function",
                "confidence": 0.9,
            }]}, model="fake")

    assessments, audit = GuidewordApplicabilityAgent(Client()).assess(_function(), guidewords)
    assert assessments[0].disposition is GuidewordDisposition.NOT_APPLICABLE
    assert assessments[0].applicable is False


def test_case4_pending_with_applicable_false_deterministic_repair_to_not_applicable():
    guidewords = [_method_guideword("always active", "The output remains active continuously", 1)]

    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-001",
                "applicable": False,
                "disposition": "NOT_APPLICABLE",
                "rationale": "insufficient evidence to determine applicability",
                "confidence": 0.5,
            }]}, model="fake")

    assessments, audit = GuidewordApplicabilityAgent(Client()).assess(_function(), guidewords)
    assert assessments[0].disposition is GuidewordDisposition.NOT_APPLICABLE
    assert assessments[0].applicable is False
    assert audit["parse_error_count"] == 0


def test_case5_pending_with_applicable_true_fails_closed():
    import pytest
    guidewords = [_method_guideword("loss", "Complete absence of the intended output", 1)]

    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-001",
                "applicable": True,
                "disposition": "PENDING",
                "rationale": "evidence is ambiguous",
                "confidence": 0.4,
            }]}, model="fake")

    with pytest.raises(ValueError, match="PROVIDER_INVALID_GUIDEWORD_DISPOSITION"):
        GuidewordApplicabilityAgent(Client()).assess(_function(), guidewords)


def test_case6_llm_does_not_return_status_system_sets_review_status():
    guidewords = [
        _method_guideword("loss", "Complete absence of the intended output", 1),
        _method_guideword("always active", "The output remains active continuously", 2),
    ]
    function_with_sources = _function()

    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [
                {
                    "guideword": "GW-001",
                    "applicable": True,
                    "disposition": "DOWNSTREAM_CANDIDATE",
                    "rationale": "loss removes the output",
                    "confidence": 0.8,
                },
                {
                    "guideword": "GW-002",
                    "applicable": False,
                    "disposition": "NOT_APPLICABLE",
                    "rationale": "no semantic dimension",
                    "confidence": 0.9,
                },
            ]}, model="fake")

    assessments, audit = GuidewordApplicabilityAgent(Client()).assess(function_with_sources, guidewords)
    by_name = {item.guideword: item for item in assessments}
    assert by_name["loss"].status is ReviewStatus.FINALIZED
    assert by_name["always active"].status is ReviewStatus.FINALIZED


def test_case7_missing_disposition_with_applicable_true_fails_closed():
    import pytest
    guidewords = [_method_guideword("loss", "Complete absence of the intended output", 1)]

    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-001",
                "applicable": True,
                "rationale": "loss removes the output",
                "confidence": 0.8,
            }]}, model="fake")

    with pytest.raises(ValueError, match="PROVIDER_INVALID_GUIDEWORD_DISPOSITION"):
        GuidewordApplicabilityAgent(Client()).assess(_function(), guidewords)


def test_case8_missing_disposition_with_applicable_false_deterministic_repair():
    guidewords = [_method_guideword("always active", "The output remains active continuously", 1)]

    class Client:
        def complete_json(self, request):
            return LLMResponse(data={"assessments": [{
                "guideword": "GW-001",
                "applicable": False,
                "rationale": "no semantic dimension",
                "confidence": 0.9,
            }]}, model="fake")

    assessments, audit = GuidewordApplicabilityAgent(Client()).assess(_function(), guidewords)
    assert assessments[0].disposition is GuidewordDisposition.NOT_APPLICABLE
    assert assessments[0].applicable is False
    assert audit["parse_error_count"] == 1
    repair = audit["parse_errors"][0]["disposition_repair"]
    assert repair["raw"] == "<missing>"
    assert repair["repaired"] == "NOT_APPLICABLE"


def test_prompt_does_not_suggest_pending_as_disposition():
    assert "PENDING" not in GuidewordApplicabilityAgent.SYSTEM_PROMPT or (
        "PENDING不是disposition" in GuidewordApplicabilityAgent.SYSTEM_PROMPT
    )
    assert "证据不足时标记PENDING" not in GuidewordApplicabilityAgent.SYSTEM_PROMPT


def test_response_schema_enumerates_disposition_and_forbids_extra_properties():
    schema = GuidewordApplicabilityAgent.RESPONSE_SCHEMA
    assert schema["additionalProperties"] is False
    item_schema = schema["properties"]["assessments"]["items"]
    assert item_schema["additionalProperties"] is False
    disposition_schema = item_schema["properties"]["disposition"]
    assert set(disposition_schema["enum"]) == {
        "DOWNSTREAM_CANDIDATE", "NOT_APPLICABLE", "NO_CREDIBLE_HAZARD",
    }
    assert "status" not in item_schema["properties"]
    assert "source" not in item_schema["properties"]


def test_is_complete_list_item_structural_check_allows_pending_disposition():
    from hara_agent.infrastructure.llm.openai_compatible import OpenAICompatibleClient
    item_pending = {
        "guideword": "loss",
        "applicable": False,
        "disposition": "PENDING",
        "rationale": "some rationale",
        "confidence": 0.5,
    }
    item_valid = {
        "guideword": "loss",
        "applicable": True,
        "disposition": "DOWNSTREAM_CANDIDATE",
        "rationale": "some rationale",
        "confidence": 0.8,
    }
    item_no_disposition = {
        "guideword": "loss",
        "applicable": True,
        "rationale": "some rationale",
        "confidence": 0.8,
    }
    assert OpenAICompatibleClient._is_complete_list_item(item_pending, "GuidewordAssessmentList")
    assert OpenAICompatibleClient._is_complete_list_item(item_valid, "GuidewordAssessmentList")
    assert not OpenAICompatibleClient._is_complete_list_item(item_no_disposition, "GuidewordAssessmentList")

from __future__ import annotations

import io
import urllib.error
from unittest.mock import patch

import pytest

from hara_agent.config import LLMConfig
from hara_agent.infrastructure.llm import LLMQuotaExceededError, LLMRequest
from hara_agent.infrastructure.llm.openai_compatible import (
    OpenAICompatibleClient,
    TransientLLMError,
)


def _http_error(detail: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/v1/chat/completions",
        429,
        "Too Many Requests",
        {},
        io.BytesIO(detail.encode("utf-8")),
    )


def test_account_quota_exhaustion_is_non_transient_and_exposes_reset_time():
    detail = (
        '{"error":{"code":"AccountQuotaExceeded","message":'
        '"You have exceeded the weekly usage quota. It will reset at '
        '2026-08-24 00:00:00 +0800 CST. We recommend waiting."}}'
    )
    with patch("urllib.request.urlopen", side_effect=_http_error(detail)):
        with pytest.raises(LLMQuotaExceededError) as raised:
            OpenAICompatibleClient._http_transport(
                "https://example.invalid/v1/chat/completions", {}, b"{}", 1.0,
            )

    assert raised.value.provider_code == "AccountQuotaExceeded"
    assert raised.value.reset_at == "2026-08-24 00:00:00 +0800 CST"


def test_normal_http_429_remains_transient():
    detail = '{"error":{"code":"RateLimitExceeded","message":"Try again later."}}'
    with patch("urllib.request.urlopen", side_effect=_http_error(detail)):
        with pytest.raises(TransientLLMError) as raised:
            OpenAICompatibleClient._http_transport(
                "https://example.invalid/v1/chat/completions", {}, b"{}", 1.0,
            )

    assert raised.value.category == "http_429"


def test_quota_exhaustion_bypasses_configured_retries():
    calls = 0

    def transport(_url, _headers, _body, _timeout):
        nonlocal calls
        calls += 1
        raise LLMQuotaExceededError(
            provider_code="AccountQuotaExceeded",
            reset_at="2026-08-24 00:00:00 +0800 CST",
        )

    client = OpenAICompatibleClient(LLMConfig(
        provider="openai-compatible",
        base_url="https://example.invalid/v1",
        model="fake",
        api_key="fake",
        max_retries=5,
    ), transport=transport)
    request = LLMRequest(
        task="assess_scenario_feasibility",
        system_prompt="system",
        user_prompt="user",
        schema_name="ScenarioFeasibilityAssessmentV2List",
        prompt_version="test",
    )

    with pytest.raises(LLMQuotaExceededError, match="reset_at=2026-08-24"):
        client.complete_json(request)

    assert calls == 1

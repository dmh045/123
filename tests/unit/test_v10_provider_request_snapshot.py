import hashlib

import pytest

from hara_agent.contracts import InMemoryCausalMechanismCatalog
from hara_agent.services.semantic import ScenarioFeasibilityAgent
from scripts.eval_scenario_real_provider import _synthetic_fixtures


class CaptureClient:
    class Config:
        scenario_batch_max_chars = 12000
        scenario_batch_max_items = 12
        scenario_max_split_depth = 8

    config = Config()

    def __init__(self):
        self.request = None

    def complete_json(self, request):
        self.request = request
        raise RuntimeError("capture-only")


def _capture(scenario, mechanism):
    malfunction, *_ = _synthetic_fixtures()
    client = CaptureClient()
    agent = ScenarioFeasibilityAgent(
        client, assessment_contract="v2",
        mechanism_catalog=InMemoryCausalMechanismCatalog((mechanism,)),
    )
    with pytest.raises(RuntimeError, match="capture-only"):
        agent.assess(malfunction, [scenario])
    return client.request


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_negative_and_positive_v10_request_snapshots_are_deterministic():
    _, negative, positive, negative_mechanism, positive_mechanism = _synthetic_fixtures()
    negative_request = _capture(negative, negative_mechanism)
    positive_request = _capture(positive, positive_mechanism)

    assert _sha(negative_request.system_prompt) == "b5810af277a1e21b6d8ab69e11e66f7b372d9c254efc1888f42f9e3f8c52f0fd"
    assert _sha(negative_request.user_prompt) == "544bcad44d3d4f61f8b0875cb7b7875d73557c3e1e18c7f7469ef046cf8de586"
    assert _sha(positive_request.system_prompt) == "b5810af277a1e21b6d8ab69e11e66f7b372d9c254efc1888f42f9e3f8c52f0fd"
    assert _sha(positive_request.user_prompt) == "1f8d0f1091512d3e0fc1466ab38970d251b9225380d759bf5e51614377b46923"
    assert negative_request.response_schema == positive_request.response_schema
    assert negative_request.metadata["provider_response_constraint"] == "PROMPT_JSON_SCHEMA"
    assert negative_request.metadata["strict_no_format_retry"] is True


def test_requests_contain_only_runtime_catalog_and_no_evaluation_gold():
    _, negative, positive, negative_mechanism, positive_mechanism = _synthetic_fixtures()
    negative_request = _capture(negative, negative_mechanism)
    positive_request = _capture(positive, positive_mechanism)

    assert negative_mechanism.mechanism_id in negative_request.user_prompt
    assert positive_mechanism.mechanism_id not in negative_request.user_prompt
    assert positive_mechanism.mechanism_id in positive_request.user_prompt
    assert "SCN.positive_chain_contract" in positive_request.user_prompt
    assert "expected mechanism selection" not in positive_request.user_prompt.lower()
    assert "expected_binding" not in positive_request.user_prompt.lower()
    assert "causal_gold" not in positive_request.user_prompt.lower()
    assert positive_request.response_format is None

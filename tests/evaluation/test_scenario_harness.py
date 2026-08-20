from hara_agent.models import MalfunctionCandidate, ScenarioCandidate, ScenarioFeasibilityAssessment
from hara_agent.services.semantic.scenario_evidence import (
    ScenarioEvidenceContractError, ScenarioEvidenceErrorCode,
)

from hara_agent.evaluation.models import ScenarioEvaluationInput
from hara_agent.evaluation.stages import ScenarioEvaluationHarness


class SequenceAgent:
    PROMPT_VERSION = "scenario-feasibility-v9"

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)

    def assess(self, malfunction, scenarios):
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return [outcome], {}


def inputs(repeat):
    return ScenarioEvaluationInput(
        malfunction=MalfunctionCandidate(
            "MF-1", "FUN-1", "loss", "loss", "effect", "hazard",
            ["loss", "effect"],
        ),
        scenarios=[ScenarioCandidate(
            "SCN-1", "Parking", "atomic", "atomic", {}, semantic_fingerprint="fp-1",
        )], repeat=repeat,
    )


def valid():
    return ScenarioFeasibilityAssessment(
        "MF-1", "SCN-1", True, True, False, [], "breakpoint",
        confidence=0.8, breakpoint="I_TO_H",
    )


def contract_error():
    return ScenarioEvidenceContractError(
        "failure", code=ScenarioEvidenceErrorCode.CAUSAL_FALSE_WITH_DIMENSIONS,
        hop="risk_dimension_changes", reason="causal=false requires no dimension changes",
        malfunction_id="MF-1", scenario_id="SCN-1", semantic_fingerprint="fp-1",
        invalid_evidence_refs=[], basis_type="", claim="",
    )


def test_harness_collects_valid_and_contract_failures_without_voting():
    report = ScenarioEvaluationHarness(
        SequenceAgent([valid(), contract_error(), valid()])
    ).evaluate(inputs(3))
    assert report["classification"] == "EVALUATION_ONLY"
    assert report["repeat"] == {
        "requested": 3, "valid": 2, "valid_ratio": 2 / 3,
        "stability_sample_sufficient": True,
    }
    assert report["contract_errors"]["by_code"] == {"CAUSAL_FALSE_WITH_DIMENSIONS": 1}
    assert len(report["results"]) == 2
    assert len(report["errors"]) == 1

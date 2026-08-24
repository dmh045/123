from __future__ import annotations

from dataclasses import asdict
from typing import Any

from hara_agent.services.semantic import ScenarioEvidenceContractError
from hara_agent.services.semantic.scenario_contract import SCENARIO_CONTRACT_VERSION
from hara_agent.services.semantic.scenario_evidence import SCENARIO_ASSESSMENT_CONTRACT_VERSION

from hara_agent.evaluation.metrics.scenario import scenario_repeat_metrics
from hara_agent.evaluation.models import EvaluationAttempt, ScenarioEvaluationInput


class ScenarioEvaluationHarness:
    def __init__(self, agent):
        self.agent = agent

    def evaluate(self, request: ScenarioEvaluationInput) -> dict[str, Any]:
        if request.repeat <= 0:
            raise ValueError("Scenario evaluation repeat必须大于0")
        attempts: list[EvaluationAttempt] = []
        audits: list[dict[str, Any]] = []
        successful_attempts = 0
        for _ in range(request.repeat):
            try:
                results, audit = self.agent.assess(request.malfunction, request.scenarios)
                audits.append(audit)
                successful_attempts += 1
                attempts.extend(EvaluationAttempt(
                    valid=True, scenario_id=item.scenario_id,
                    semantic_fingerprint=next(
                        scenario.semantic_fingerprint for scenario in request.scenarios
                        if scenario.scenario_id == item.scenario_id
                    ), result=item,
                ) for item in results)
            except ScenarioEvidenceContractError as error:
                scenario_id = getattr(error, "scenario_id", "") or request.scenarios[0].scenario_id
                fingerprint = next(
                    (
                        scenario.semantic_fingerprint for scenario in request.scenarios
                        if scenario.scenario_id == scenario_id
                    ),
                    request.scenarios[0].semantic_fingerprint,
                )
                attempts.append(EvaluationAttempt(
                    valid=False, scenario_id=scenario_id,
                    semantic_fingerprint=fingerprint, error=error,
                ))
        metrics = scenario_repeat_metrics(attempts)
        prompt_version = getattr(
            self.agent, "prompt_version", getattr(self.agent, "PROMPT_VERSION", "")
        )
        assessment_version = getattr(
            self.agent, "assessment_contract_version", SCENARIO_ASSESSMENT_CONTRACT_VERSION
        )
        return {
            "stage": "scenario",
            "classification": "EVALUATION_ONLY",
            "contract_versions": {
                "scenario": SCENARIO_CONTRACT_VERSION,
                "assessment": assessment_version,
                "prompt": prompt_version,
                "provider_schema": getattr(self.agent, "schema_name", "ScenarioFeasibilityAssessmentList"),
            },
            "input": {
                "malfunction_id": request.malfunction.malfunction_id,
                "scenario_ids": [item.scenario_id for item in request.scenarios],
                "semantic_fingerprints": [item.semantic_fingerprint for item in request.scenarios],
                **request.metadata,
            },
            "repeat": {
                "requested": request.repeat,
                "valid": metrics["valid_attempt_count"],
                "valid_ratio": metrics["valid_attempt_ratio"],
                "stability_sample_sufficient": metrics["stability_sample_sufficient"],
            },
            "contract_errors": {
                "total": metrics["evidence_contract_error_count"],
                "by_code": metrics["error_counts_by_code"],
                "cross_field_invariant": metrics["cross_field_invariant_error_count"],
                "unresolved_evidence_ref": metrics["unresolved_evidence_ref_count"],
                "evidence_kind_mismatch": metrics["evidence_kind_mismatch_count"],
                "assumption_violation": metrics["assumption_violation_count"],
            },
            "contract_metrics": {
                "schema_valid_attempt_count": successful_attempts,
                "contract_valid_attempt_count": successful_attempts,
                "provider_payload_error_count": 0,
            },
            "stability": {
                key: metrics[key] for key in (
                    "causal_agreement_ratio", "physical_agreement_ratio",
                    "functional_agreement_ratio", "risk_dimension_jaccard_min",
                    "risk_dimension_jaccard_avg",
                )
            },
            "results": [
                {
                    "scenario_id": item.scenario_id,
                    "semantic_fingerprint": item.semantic_fingerprint,
                    **(
                        item.result.to_dict()
                        if hasattr(item.result, "to_dict")
                        else asdict(item.result)
                    ),
                }
                for item in attempts if item.valid
            ],
            "errors": [
                {
                    "code": item.error.code.value,
                    "hop": getattr(item.error, "hop", getattr(item.error, "edge_id", "")),
                    "reason": getattr(item.error, "reason", str(item.error)),
                    "scenario_id": item.scenario_id,
                    "semantic_fingerprint": item.semantic_fingerprint,
                    "invalid_evidence_refs": getattr(item.error, "invalid_evidence_refs", []),
                    "basis_type": getattr(item.error, "basis_type", ""),
                }
                for item in attempts if not item.valid
            ],
            "audits": audits,
        }

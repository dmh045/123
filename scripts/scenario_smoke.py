#!/usr/bin/env python3
"""Small real-provider Scenario/downstream smoke; never a production HARA run."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.config import LLMConfig
from hara_agent.domains import default_domain_registry
from hara_agent.infrastructure.llm import create_llm_client
from hara_agent.models import MalfunctionCandidate, ReviewStatus
from hara_agent.services.analysis import (
    DomainScenarioCandidateService, DomainScoringService, FTTIService,
    SafetyGoalCatalogService, TemplateASILService,
)
from hara_agent.services.extraction import TemplateInputReader
from hara_agent.services.reporting import HARAExcelRenderer
from hara_agent.services.semantic import ScenarioFeasibilityAgent
from hara_agent.services.semantic import ScenarioEvidenceContractError
from hara_agent.services.validation import DownstreamPreflightService
from hara_agent.workflow import CheckpointRepository, HARAState
from hara_agent.workflow.nodes import (
    aggregate_safety_goals, assess_scenarios, pass_quality_gate, score_structured_scenarios,
)
from hara_agent.evaluation.metrics.scenario import scenario_repeat_metrics
from hara_agent.evaluation.models import EvaluationAttempt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TEST/SMOKE only: deterministic Scenario Provider/downstream validation",
    )
    parser.add_argument("--domain", required=True)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--run-dir", type=Path, default=Path("runtime/agent"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--malfunction-count", type=int, default=1)
    parser.add_argument("--scenario-count", type=int, default=3)
    parser.add_argument("--malfunction-id", action="append", default=[])
    parser.add_argument("--scenario-id", action="append", default=[])
    parser.add_argument("--ego-speed-kph", type=float)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--scenario-contract", choices=["v1", "v2"], default="v1")
    return parser


def _select(values, identities: list[str], count: int, key):
    if identities:
        by_id = {key(value): value for value in values}
        missing = [identity for identity in identities if identity not in by_id]
        if missing:
            raise ValueError(f"Smoke指定ID不存在: {missing}")
        return [by_id[identity] for identity in identities]
    if count <= 0:
        raise ValueError("Smoke count必须大于0")
    return list(values[:count])


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime = default_domain_registry().create(args.domain, require_approved=False)
    profile = runtime.profile
    policy = runtime.policy
    template_inputs = TemplateInputReader().read(args.template)
    renderer = HARAExcelRenderer()
    renderer.contract.validate(args.template)

    source = CheckpointRepository(args.run_dir).load(args.run_id)
    state = HARAState.from_dict(source.to_dict())  # isolated copy; source checkpoint is read-only
    malfunctions = [MalfunctionCandidate(
        malfunction_id=str(item["malfunction_id"]),
        function_id=str(item["function_id"]),
        guideword=str(item["guideword"]),
        description=str(item["description"]),
        functional_effect=str(item["functional_effect"]),
        vehicle_level_hazard=str(item["vehicle_level_hazard"]),
        causal_chain=list(item["causal_chain"]),
        status=ReviewStatus(item.get("status", "PENDING")),
        confidence=float(item.get("confidence", 0.0)),
        model_local_id=str(item.get("model_local_id", "")),
    ) for item in state.malfunctions]
    selected_malfunctions = _select(
        malfunctions, args.malfunction_id, args.malfunction_count,
        lambda item: item.malfunction_id,
    )
    selected_ids = {item.malfunction_id for item in selected_malfunctions}
    state.malfunctions = [item for item in state.malfunctions if item["malfunction_id"] in selected_ids]

    scenario_candidates = DomainScenarioCandidateService(policy)
    candidates, _ = scenario_candidates.generate(
        ego_speed_kph=args.ego_speed_kph,
    )
    selected_scenarios = _select(
        candidates, args.scenario_id, args.scenario_count, lambda item: item.scenario_id,
    )
    llm_config = LLMConfig.from_env()
    client = create_llm_client(llm_config)
    agent = ScenarioFeasibilityAgent(client, assessment_contract=args.scenario_contract)
    if args.repeat <= 0:
        raise ValueError("--repeat必须大于0")
    fingerprint_material = {
        "malfunction_ids": [item.malfunction_id for item in selected_malfunctions],
        "scenario_ids": [item.scenario_id for item in selected_scenarios],
        "prompt_version": agent.prompt_version,
        "assessment_contract_version": agent.assessment_contract_version,
        "schema_name": agent.schema_name,
        "mechanism_catalog_fingerprint": agent.mechanism_catalog_fingerprint,
        "contract_cache_fingerprint": agent.contract_cache_fingerprint,
        "model": llm_config.model,
        "thinking": llm_config.scenario_thinking,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    if args.repeat > 1:
        if len(selected_malfunctions) != 1 or len(selected_scenarios) != 1:
            raise ValueError("--repeat稳定性诊断要求恰好1个malfunction × 1个atomic scenario")
        repeated = []
        evaluation_attempts = []
        for _ in range(args.repeat):
            try:
                items, _ = agent.assess(selected_malfunctions[0], selected_scenarios)
                repeated.append(items[0])
                evaluation_attempts.append(EvaluationAttempt(
                    True, items[0].scenario_id, selected_scenarios[0].semantic_fingerprint,
                    result=items[0],
                ))
            except ScenarioEvidenceContractError as exc:
                evaluation_attempts.append(EvaluationAttempt(
                    False, exc.scenario_id, exc.semantic_fingerprint, error=exc,
                ))
        metrics = scenario_repeat_metrics(evaluation_attempts)
        physical = [item.physically_feasible for item in repeated]
        functional = [item.functionally_relevant for item in repeated]
        causal = [item.causally_relevant for item in repeated]
        print(json.dumps({
            "classification": "TEST / SMOKE — NOT FOR RELEASE",
            "smoke_can_release": False,
            "smoke_input_fingerprint": fingerprint,
            "pair": {
                "malfunction_id": selected_malfunctions[0].malfunction_id,
                "scenario_id": selected_scenarios[0].scenario_id,
                "semantic_fingerprint": selected_scenarios[0].semantic_fingerprint,
            },
            "repeat_count": args.repeat,
            "valid_attempt_count": metrics["valid_attempt_count"],
            "valid_attempt_ratio": metrics["valid_attempt_ratio"],
            "stability_sample_sufficient": metrics["stability_sample_sufficient"],
            "evidence_contract_error_count": metrics["evidence_contract_error_count"],
            "error_counts_by_code": metrics["error_counts_by_code"],
            "cross_field_invariant_error_count": metrics["cross_field_invariant_error_count"],
            "causal_true_count": causal.count(True),
            "causal_false_count": causal.count(False),
            "causal_agreement_ratio": metrics["causal_agreement_ratio"],
            "physical_agreement_ratio": metrics["physical_agreement_ratio"],
            "functional_agreement_ratio": metrics["functional_agreement_ratio"],
            "risk_dimension_jaccard_min": metrics["risk_dimension_jaccard_min"],
            "risk_dimension_jaccard_avg": metrics["risk_dimension_jaccard_avg"],
            "hazard_nonempty_count": sum(bool(item.hazardous_event) for item in repeated),
            "unsupported_claim_count": metrics["assumption_violation_count"],
            "assumption_hop_count": sum(
                sum(hop.get("basis_type") == "ASSUMPTION" for hop in item.causal_chain.values())
                for item in repeated
            ),
            "unresolved_evidence_ref_count": metrics["unresolved_evidence_ref_count"],
            "evidence_kind_mismatch_count": metrics["evidence_kind_mismatch_count"],
            "evidence_contract_errors": [str(item.error) for item in evaluation_attempts if item.error],
            "results": [item.__dict__ for item in repeated],
        }, ensure_ascii=False, indent=2, default=str))
        return 0

    safety_goal_catalog = SafetyGoalCatalogService(profile)
    DownstreamPreflightService().validate(state)
    assess_scenarios(state, agent, selected_malfunctions, selected_scenarios, max_workers=1)
    score_structured_scenarios(
        state, DomainScoringService(policy, template_inputs.scoring_standards),
        TemplateASILService(str(args.template)), FTTIService(),
    )
    aggregate_safety_goals(state, safety_goal_catalog)
    pass_quality_gate(state)
    output = None
    if args.render:
        output = args.output or Path("output") / f"HARA_Smoke_{fingerprint[:12]}.xlsx"
        renderer.render(state, args.template, output, smoke=True)

    summary_event = next(
        item for item in reversed(state.audit_trail)
        if item.get("event") == "scenario_feasibility_summary"
    )
    print(json.dumps({
        "classification": "TEST / SMOKE — NOT FOR RELEASE",
        "smoke_can_release": False,
        "smoke_input_fingerprint": fingerprint,
        "inputs": fingerprint_material,
        "scenario_assessments": state.item_definition.get("scenario_assessments", []),
        "metrics": summary_event,
        "risk_count": len(state.risk_results),
        "safety_goal_count": len(state.safety_goals),
        "quality_gate_would_publish_full_run": state.can_publish,
        "output": str(output) if output else None,
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

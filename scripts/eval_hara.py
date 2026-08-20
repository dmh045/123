#!/usr/bin/env python3
"""Unified stage evaluation CLI (evaluation only; never mutates production inputs)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.evaluation.models import (
    ExpectedProjectFact,
    ExtractionEvaluationInput,
    ScenarioEvaluationInput,
)
from hara_agent.evaluation.stages import ExtractionEvaluationHarness, ScenarioEvaluationHarness
from hara_agent.config import LLMConfig
from hara_agent.domains import default_domain_registry
from hara_agent.infrastructure.llm import create_llm_client
from hara_agent.models import MalfunctionCandidate, ReviewStatus
from hara_agent.services.analysis import AVPScenarioCandidateService
from hara_agent.services.extraction import DocumentReader
from hara_agent.services.semantic import ItemEvidenceRouter
from hara_agent.services.semantic import ScenarioFeasibilityAgent
from hara_agent.workflow import CheckpointRepository


def _parser():
    parser = argparse.ArgumentParser(description="HARA evaluation harness (evaluation only)")
    parser.add_argument("--stage", required=True, choices=["scenario", "extraction"])
    parser.add_argument("--domain")
    parser.add_argument("--run-dir", type=Path, default=Path("runtime/agent"))
    parser.add_argument("--run-id")
    parser.add_argument("--malfunction-id")
    parser.add_argument("--scenario-id")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--ego-speed-kph", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--item", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--project-facts", type=Path)
    parser.add_argument("--scenario-contract", choices=["v1", "v2"], default="v1")
    return parser


def _scenario_report(args, parser):
    missing = [
        name for name, value in (
            ("--domain", args.domain), ("--run-id", args.run_id),
            ("--malfunction-id", args.malfunction_id), ("--scenario-id", args.scenario_id),
        ) if not value
    ]
    if missing:
        parser.error("scenario stage requires " + ", ".join(missing))
    runtime = default_domain_registry().create(args.domain, require_approved=False)
    state = CheckpointRepository(args.run_dir).load(args.run_id)
    malfunction_data = next(
        item for item in state.malfunctions if item["malfunction_id"] == args.malfunction_id
    )
    malfunction = MalfunctionCandidate(
        malfunction_id=malfunction_data["malfunction_id"],
        function_id=malfunction_data["function_id"], guideword=malfunction_data["guideword"],
        description=malfunction_data["description"],
        functional_effect=malfunction_data["functional_effect"],
        vehicle_level_hazard=malfunction_data["vehicle_level_hazard"],
        causal_chain=list(malfunction_data["causal_chain"]),
        status=ReviewStatus(malfunction_data.get("status", "PENDING")),
        confidence=float(malfunction_data.get("confidence", 0.0)),
        model_local_id=malfunction_data.get("model_local_id", ""),
    )
    candidates, _ = AVPScenarioCandidateService(runtime.policy).generate(
        ego_speed_kph=args.ego_speed_kph,
    )
    scenario = next(item for item in candidates if item.scenario_id == args.scenario_id)
    agent = ScenarioFeasibilityAgent(
        create_llm_client(LLMConfig.from_env()),
        assessment_contract=args.scenario_contract,
    )
    report = ScenarioEvaluationHarness(agent).evaluate(ScenarioEvaluationInput(
        malfunction=malfunction, scenarios=[scenario], repeat=args.repeat,
        metadata={"requested_scenario_contract": args.scenario_contract},
    ))
    return report


def _project_facts(args, parser):
    if args.project_facts:
        payload = json.loads(args.project_facts.read_text(encoding="utf-8"))
        if isinstance(payload.get("item_definition"), dict):
            payload = payload["item_definition"].get("typed", payload["item_definition"])
        return payload.get("typed", payload)
    if args.run_id:
        return CheckpointRepository(args.run_dir).load(args.run_id).item_definition.get("typed", {})
    parser.error("extraction stage requires --project-facts or --run-id")


def _extraction_report(args, parser):
    fixture_path = args.fixture or (
        ROOT / "src/hara_agent/evaluation/fixtures/extraction/legacy_avp/itemdef_grounded.json"
    )
    if not fixture_path.is_file():
        parser.error(f"extraction fixture does not exist: {fixture_path}")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    item_path = args.item or (ROOT / str(fixture.get("source_path", "")))
    if not item_path.is_file():
        parser.error(f"extraction source does not exist: {item_path}")
    artifact = DocumentReader().read(item_path)
    block_dicts = [
        {
            "block_id": block.block_id,
            "kind": block.kind,
            "location": block.location,
            "text": block.text,
        }
        for block in artifact.blocks
    ]
    router = ItemEvidenceRouter()
    routed_ids = set()
    routed_ids_by_task = {}
    routing_diagnostics_by_task = {}
    for task in ("odd_repair", "project_evidence"):
        routing_result = router.retrieve(block_dicts, task)
        routed = routing_result.routed
        routing_diagnostics_by_task[task] = routing_result.diagnostics.to_dict()
        if routed:
            routed_ids.update(routed.block_ids)
            routed_ids_by_task[task] = set(routed.block_ids)
        else:
            routed_ids_by_task[task] = set()
    request = ExtractionEvaluationInput(
        source_id=artifact.source_id,
        source_blocks=artifact.blocks,
        expected_facts=[ExpectedProjectFact.from_dict(item) for item in fixture["facts"]],
        project_facts=_project_facts(args, parser),
        routed_block_ids=routed_ids,
        routed_block_ids_by_task=routed_ids_by_task,
        routing_diagnostics_by_task=routing_diagnostics_by_task,
        metadata={
            "fixture_id": fixture.get("fixture_id", fixture_path.stem),
            "fixture_classification": fixture.get("classification", ""),
            "fixture_path": str(fixture_path.resolve()),
        },
    )
    return ExtractionEvaluationHarness().evaluate(request)


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    report = (
        _scenario_report(args, parser)
        if args.stage == "scenario"
        else _extraction_report(args, parser)
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

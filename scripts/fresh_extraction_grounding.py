#!/usr/bin/env python3
"""Run extraction-only fresh-provider grounding validation for P0-1.9."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.config import LLMConfig
from hara_agent.evaluation.models import ExpectedProjectFact, ExtractionEvaluationInput
from hara_agent.evaluation.stages import ExtractionEvaluationHarness
from hara_agent.infrastructure.llm import LLMRequest, LLMResponse, create_llm_client
from hara_agent.models import ItemDefinitionFacts
from hara_agent.services.analysis import ProjectFactResolver, UnresolvedProjectContextError
from hara_agent.services.extraction import DocumentReader, ValidatedArtifactCache
from hara_agent.services.semantic import (
    ItemArtifactExtractionAgent,
    ItemEvidenceRouter,
    ItemSupplementAgent,
    TargetedProjectFactExtractionAgent,
)
from hara_agent.workflow import HARAState
from hara_agent.workflow.nodes import extract_item_artifacts, read_item_document


ALLOWED_EXTRACTION_TASKS = {
    "extract_core_item_artifacts",
    "supplement_odd_repair",
    "supplement_project_evidence",
}
PRESENT_CLASSIFICATIONS = {"PRESENT_EXPLICIT", "PRESENT_DERIVABLE"}


@dataclass
class ExtractionCallAudit:
    task: str
    schema_name: str
    prompt_version: str
    max_tokens: int | None
    block_ids: list[str]
    request_id: str = ""
    model: str = ""
    transport_attempts: int = 0
    usage: dict = field(default_factory=dict)


class ExtractionOnlyClient:
    """Reject any accidental downstream LLM task and record provider calls."""

    def __init__(self, client):
        self.client = client
        self.config = client.config
        self.calls: list[ExtractionCallAudit] = []

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        if request.task not in ALLOWED_EXTRACTION_TASKS and not request.task.startswith(
            "extract_targeted_project_facts:"
        ):
            raise RuntimeError(
                f"P0-1.9 prohibits non-extraction provider task: {request.task}"
            )
        response = self.client.complete_json(request)
        usage = dict(response.usage or {})
        self.calls.append(ExtractionCallAudit(
            task=request.task,
            schema_name=request.schema_name,
            prompt_version=request.prompt_version,
            max_tokens=request.max_tokens,
            block_ids=[str(item) for item in request.metadata.get("block_ids", [])],
            request_id=response.request_id,
            model=response.model,
            transport_attempts=int(usage.get("transport_attempts", 1)),
            usage={
                key: value for key, value in usage.items()
                if key in {
                    "prompt_tokens", "completion_tokens", "total_tokens",
                    "input_tokens", "output_tokens", "reasoning_tokens",
                    "transport_attempts", "transient_error_counts",
                    "finish_reason", "latency_seconds", "max_tokens",
                }
            },
        ))
        return response


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P0-1.9 extraction-only fresh-provider grounding validation"
    )
    parser.add_argument("--item", type=Path, default=ROOT / "input/ItemDef.docx")
    parser.add_argument(
        "--fixture", type=Path,
        default=(
            ROOT
            / "src/hara_agent/evaluation/fixtures/extraction/legacy_avp/itemdef_grounded.json"
        ),
    )
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--max-workers", type=int, default=4)
    return parser


def _load_local_env(path: Path) -> None:
    """Load a local dotenv file without logging or returning secret values."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _safe_provider(config: LLMConfig) -> dict:
    parsed = urlsplit(config.base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "configured"
    return {
        "provider": config.provider,
        "base_url_origin": origin,
        "base_url_sha256": hashlib.sha256(config.base_url.encode("utf-8")).hexdigest(),
        "model": config.model,
        "timeout_seconds": config.timeout_seconds,
        "max_tokens": config.max_tokens,
        "max_retries": config.max_retries,
        "retry_backoff_seconds": config.retry_backoff_seconds,
        "extraction_thinking": config.extraction_thinking,
    }


def _route(artifact, router: ItemEvidenceRouter):
    block_dicts = [
        {
            "block_id": block.block_id,
            "kind": block.kind,
            "location": block.location,
            "text": block.text,
        }
        for block in artifact.blocks
    ]
    routed_ids, by_task, diagnostics = set(), {}, {}
    for task in ("odd_repair", "project_evidence"):
        result = router.retrieve(block_dicts, task)
        diagnostics[task] = result.diagnostics.to_dict()
        ids = set(result.routed.block_ids) if result.routed else set()
        routed_ids.update(ids)
        by_task[task] = ids
    return routed_ids, by_task, diagnostics


def _speed_resolutions(
    facts: ItemDefinitionFacts, expected: list[ExpectedProjectFact],
) -> tuple[list[dict], list[dict]]:
    modes = []
    for item in expected:
        if item.field != "speed_envelopes":
            continue
        mode = str(item.context.get("operating_mode", item.context.get("mode", "")))
        expected_value = item.value.get("speed_max_kph") if isinstance(item.value, dict) else None
        modes.append((mode, expected_value))
    diagnostics, harness_values = [], []
    for mode, expected_value in modes:
        try:
            value = ProjectFactResolver().resolve_speed_context(facts, mode).to_dict()
            diagnostic = {
                "operating_mode": mode,
                "expected_speed_kph": expected_value,
                "resolved": True,
                **value,
            }
        except UnresolvedProjectContextError as error:
            diagnostic = {
                "operating_mode": mode,
                "expected_speed_kph": expected_value,
                "resolved": False,
                **error.to_dict(),
            }
        diagnostics.append(diagnostic)
        harness_values.append(diagnostic)
    return diagnostics, harness_values


def _fact_results(report: dict, expected_by_id: dict[str, ExpectedProjectFact]) -> list[dict]:
    results = []
    for item in report["facts"]:
        expected = expected_by_id[item["fact_id"]]
        selected = set(item.get("selected_block_ids", []))
        observed = item.get("observed")
        router_covered = bool(selected & set(expected.source_block_ids))
        # Core extraction receives the complete Item Definition. Contextual
        # speed evidence therefore reached the main prompt even when the
        # optional odd-repair route did not select its later table rows.
        prompt_context_included = router_covered or item["field"] == "speed_envelopes"
        results.append({
            "fact_id": item["fact_id"],
            "field": item["field"],
            "classification": item["classification"],
            "source_block_ids": list(expected.source_block_ids),
            "source_exists": bool(item.get("grounded")),
            "router_covered": router_covered,
            "prompt_context_included": prompt_context_included,
            "extractor_returned": observed is not None,
            "normalizer_preserved": bool(item.get("normalization_match")),
            "source_ref_correct": bool(item.get("source_ref_match")),
            "context_preserved": bool(item.get("context_match")),
            "effective_provenance": (
                observed.get("provenance", "PROJECT_INPUT")
                if isinstance(observed, dict) else None
            ),
            "explicit_provenance": bool(
                isinstance(observed, dict) and "provenance" in observed
            ),
            "observed": observed,
            "reason": item.get("reason", ""),
            "retrieval_diagnostics": item.get("retrieval_diagnostics", {}),
        })
    return results


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.max_workers <= 32:
        raise ValueError("max-workers must be between 1 and 32")
    if not args.item.is_file() or not args.fixture.is_file():
        raise FileNotFoundError("item or grounded fixture does not exist")
    run_id = args.run_id or datetime.now().strftime("p0-1-9-fresh-%Y%m%d-%H%M%S")
    output = args.output or ROOT / "runtime/evaluation" / f"{run_id}.json"
    cache_dir = args.cache_dir or ROOT / "runtime/evaluation/cache" / run_id

    _load_local_env(args.env_file)
    config = LLMConfig.from_env()
    config.validate()
    audited_client = ExtractionOnlyClient(create_llm_client(config))
    router = ItemEvidenceRouter()
    state = read_item_document(HARAState(run_id=run_id), str(args.item))
    state = extract_item_artifacts(
        state,
        ItemArtifactExtractionAgent(audited_client),
        ItemSupplementAgent(audited_client),
        router,
        targeted_agent=TargetedProjectFactExtractionAgent(audited_client),
        max_workers=args.max_workers,
        cache=ValidatedArtifactCache(cache_dir, mode="refresh"),
    )

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    expected = [ExpectedProjectFact.from_dict(item) for item in fixture["facts"]]
    expected_by_id = {item.fact_id: item for item in expected}
    artifact = DocumentReader().read(args.item)
    routed_ids, routed_by_task, routing_diagnostics = _route(artifact, router)
    project_facts = state.to_dict()["item_definition"]["typed"]
    typed_facts = ItemDefinitionFacts.from_dict(project_facts)
    speed_results, harness_resolutions = _speed_resolutions(typed_facts, expected)
    harness_report = ExtractionEvaluationHarness().evaluate(ExtractionEvaluationInput(
        source_id=artifact.source_id,
        source_blocks=artifact.blocks,
        expected_facts=expected,
        project_facts=project_facts,
        routed_block_ids=routed_ids,
        routed_block_ids_by_task=routed_by_task,
        routing_diagnostics_by_task=routing_diagnostics,
        production_speed_resolutions=harness_resolutions,
        metadata={
            "fixture_id": fixture.get("fixture_id", args.fixture.stem),
            "fixture_classification": fixture.get("classification", ""),
            "fixture_path": str(args.fixture.resolve()),
            "fixture_age": "fresh_provider_output",
            "checkpoint_kind": "typed_current",
        },
    ))
    facts = _fact_results(harness_report, expected_by_id)
    extract_timing = next(
        item for item in state.audit_trail if item.get("event") == "extract_timing"
    )
    provider_calls = [call.__dict__ for call in audited_client.calls]
    forbidden_calls = [
        call["task"] for call in provider_calls
        if call["task"] not in ALLOWED_EXTRACTION_TASKS
        and not call["task"].startswith("extract_targeted_project_facts:")
    ]
    present_count = sum(
        item["classification"] in PRESENT_CLASSIFICATIONS for item in facts
    )
    raw_returned = [item for item in facts if item["extractor_returned"]]
    targeted_calls = [
        call for call in provider_calls
        if call["task"].startswith("extract_targeted_project_facts:")
    ]
    typed_counts = {
        "speed_envelopes": len(project_facts.get("speed_envelopes", [])),
        "numeric_constraints": len(project_facts.get("numeric_constraints", [])),
        "driver_context_facts": len(project_facts.get("driver_context_facts", [])),
    }
    legacy_leakage = [
        item["fact_id"] for item in facts
        if item["effective_provenance"] == "LEGACY_MIGRATION"
    ]
    validation_ready = (
        bool(provider_calls)
        and not extract_timing.get("cache_hit")
        and not forbidden_calls
        and len(targeted_calls) == 3
        and {item["task"].rsplit(":", 1)[1] for item in targeted_calls}
        == {"speed", "performance", "driver"}
        and not harness_report["gap_counts"].get("ROUTING_MISSED", 0)
    )
    extraction_production_ready = validation_ready and not any(
        harness_report["gap_counts"].get(name, 0)
        for name in ("EXTRACTOR_MISSED", "ATOMICIZATION_FAILED", "NORMALIZATION_LOSS")
    ) and all(item.get("resolved") for item in speed_results) and (
        present_count == len(expected) == 10
        and typed_counts == {
            "speed_envelopes": 3,
            "numeric_constraints": 5,
            "driver_context_facts": 2,
        }
        and harness_report["field_metrics"].get("source_ref_accuracy") == 1.0
    )
    report = {
        "report_schema_version": "p0-1.9-atomic-project-facts-v1",
        "run_id": run_id,
        "atomic_project_facts_status": (
            "READY" if extraction_production_ready else "NOT_READY"
        ),
        "validation_status": "READY" if validation_ready else "NOT_READY",
        "extraction_production_status": (
            "READY" if extraction_production_ready else "NOT_READY"
        ),
        "next_blocker": (
            None if extraction_production_ready else
            "P0-1.9 schema-guided/targeted extraction and deterministic atomic normalization"
        ),
        "fresh_extraction": True,
        "cache_hit": bool(extract_timing.get("cache_hit")),
        "fresh_provider_call_confirmed": bool(provider_calls) and not extract_timing.get("cache_hit"),
        "provider": {
            **_safe_provider(config),
            "logical_call_count": len(provider_calls),
            "targeted_category_call_count": len(targeted_calls),
            "transport_attempt_count": sum(
                int(call.get("transport_attempts", 0)) for call in provider_calls
            ),
            "calls": provider_calls,
            "forbidden_downstream_calls": forbidden_calls,
        },
        "cache": {
            "mode": "refresh",
            "directory": str(cache_dir.resolve()),
            "status": extract_timing.get("cache_status"),
            "key": extract_timing.get("cache_key"),
        },
        "expected_fact_count": len(expected),
        "extracted_fact_count": present_count,
        "typed_fact_counts": typed_counts,
        "raw_extractor_returned_fact_count": len(raw_returned),
        "raw_extractor_source_ref_accuracy": (
            sum(item["source_ref_correct"] for item in raw_returned) / len(raw_returned)
            if raw_returned else None
        ),
        **harness_report["field_metrics"],
        "gap_counts": harness_report["gap_counts"],
        "speed_envelope_results": [
            item for item in facts if item["field"] == "speed_envelopes"
        ],
        "project_fact_resolver_results": speed_results,
        "performance_fact_results": [
            item for item in facts if item["field"] == "numeric_constraints"
        ],
        "driver_context_results": [
            item for item in facts if item["field"] == "driver_context_facts"
        ],
        "router_diagnostics": routing_diagnostics,
        "router_selected_blocks": {
            task: sorted(ids) for task, ids in routed_by_task.items()
        },
        "production_context": harness_report["production_context"],
        "legacy_profile_loaded": False,
        "legacy_profile_leakage": legacy_leakage,
        "before_after": {
            "old_checkpoint": {
                "ROUTING_MISSED": 0,
                "EXTRACTOR_MISSED": 7,
                "NORMALIZATION_LOSS": 3,
                "field_recall": 0.0,
                "grounded_field_recall": 0.0,
                "source_ref_accuracy": None,
                "context_preservation_rate": None,
                "normalization_accuracy": 0.0,
            },
            "fresh_extraction": {
                **harness_report["gap_counts"],
                **harness_report["field_metrics"],
            },
        },
        "facts": facts,
        "project_facts": project_facts,
        "extraction_audit_trail": state.audit_trail,
        "constraints": {
            "main_extraction_prompt_modified": False,
            "targeted_atomic_prompt_added": True,
            "router_architecture_or_budget_modified": False,
            "retrieval_specs_extended": True,
            "scenario_semantics_modified": False,
            "full_hara_executed": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "run_id": run_id,
        "output": str(output.resolve()),
        "fresh_provider_call_confirmed": report["fresh_provider_call_confirmed"],
        "cache_hit": report["cache_hit"],
        "provider_call_count": len(provider_calls),
        "gap_counts": report["gap_counts"],
        "field_metrics": harness_report["field_metrics"],
        "resolver_results": speed_results,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

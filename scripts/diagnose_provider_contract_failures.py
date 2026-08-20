#!/usr/bin/env python3
"""Classify captured P0-2c2 failures without invoking a Provider."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.services.semantic.provider_contract_conformance import (
    diagnose_captured_attempt_record,
    summarize_provider_failure_layers,
)
from hara_agent.services.semantic.scenario_provider_schema import (
    SCENARIO_V2_SERIALIZATION_PROFILE,
    scenario_v2_provider_schema_fingerprint,
)


DEFAULT_REPORTS = (
    "p0-2c2-synth-neg-20260819-001.json",
    "p0-2c2-synth-pos-20260819-001.json",
    "p0-2c2-order-a-20260819-002.json",
    "p0-2c2-order-b-20260819-002.json",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir", type=Path, default=ROOT / "runtime" / "evaluation"
    )
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "runtime" / "evaluation" /
        "p0-2c3-provider-contract-diagnostics-20260819.json",
    )
    return parser


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = _parser().parse_args()
    reports = [_read(args.input_dir / name) for name in DEFAULT_REPORTS]
    subreasons: Counter[str] = Counter()
    attempts = []
    run_summaries = []
    for report in reports:
        run_attempts = []
        for attempt in report.get("attempts", []):
            diagnostic = diagnose_captured_attempt_record(attempt)
            subreasons.update(diagnostic.error_codes)
            item = {
                "run_id": report.get("run_id"),
                "attempt_id": attempt.get("attempt_id"),
                "logical_attempt": attempt.get("logical_attempt"),
                "scenario_id": attempt.get("scenario_id"),
                "parser_error": attempt.get("error"),
                "provider_causal_verdict": attempt.get("provider_causal_verdict"),
                "raw_provider_body_persisted": False,
                "conformance": diagnostic.to_dict(),
            }
            attempts.append(item)
            run_attempts.append(item)
        run_summaries.append({
            "run_id": report.get("run_id"),
            "logical_calls": report.get("logical_calls"),
            "provider_call_count": report.get("provider_call_count"),
            "transport_attempts": report.get("transport_attempts"),
            "transport_retries": report.get("transport_retries"),
            "finish_reason_distribution": report.get("finish_reason_distribution"),
            "failure_layers": summarize_provider_failure_layers(report),
            "attempt_count": len(run_attempts),
        })
    payload = {
        "report_schema_version": "p0-2c3-provider-contract-diagnostics-v1",
        "classification": "DETERMINISTIC_OFFLINE_DIAGNOSTICS",
        "source_run_ids": [report.get("run_id") for report in reports],
        "raw_capture_audit": {
            "complete_raw_provider_bodies_available": False,
            "reason": (
                "P0-2c2 public audit persisted parsed assessment summaries and typed errors; "
                "positive calls timed out before a body existed."
            ),
            "future_test_only_raw_capture_required": True,
        },
        "invalid_v2_assessment_subreason_distribution": dict(sorted(subreasons.items())),
        "run_summaries": run_summaries,
        "attempts": attempts,
        "synthetic_positive_root_cause": {
            "root_cause": "PROVIDER_READ_TIMEOUT",
            "causal_chain": [
                "Provider returned no body within 180 seconds",
                "existing transport retried once",
                "LLMTimeoutError after 2 transport attempts",
                "single-item batch could not split",
                "ScenarioAdaptiveBatchError surfaced",
            ],
            "schema_failure": False,
            "local_char_budget_failure": False,
            "semantic_retry": False,
        },
        "structured_output_audit": {
            "provider_platform_documented_structured_output_beta": True,
            "provider_platform_source": "https://www.volcengine.com/docs/82379/1958523",
            "configured_chat_route_model_specific_json_schema_verified": False,
            "native_structured_output_supported": False,
            "basis": (
                "P0-2c2 request bodies contained no response_format/json_schema; the current "
                "glm chat route has no successful model-specific native-schema response on record."
            ),
            "selected_constraint": "PROMPT_JSON_SCHEMA",
        },
        "provider_schema": {
            "serialization_profile": SCENARIO_V2_SERIALIZATION_PROFILE,
            "sha256": scenario_v2_provider_schema_fingerprint(),
            "derived_from_canonical_python_contract": True,
            "additional_properties": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

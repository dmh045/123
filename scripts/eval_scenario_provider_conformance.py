#!/usr/bin/env python3
"""P0-2c3 gated real-Provider schema smoke; synthetic Scenario-only data."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_scenario_real_provider import (
    RecordingScenarioClient, _load_local_env, _provider_metadata,
    _public_call_audit, _run_experiment, _synthetic_fixtures, _write_report,
)
from hara_agent.config import LLMConfig
from hara_agent.contracts import InMemoryCausalMechanismCatalog
from hara_agent.infrastructure.llm import create_llm_client
from hara_agent.services.semantic.provider_contract_conformance import (
    summarize_provider_failure_layers,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "runtime" / "evaluation"
    )
    parser.add_argument("--timestamp")
    parser.add_argument(
        "--execute", action="store_true",
        help="Make bounded real Provider calls; otherwise write a zero-call manifest.",
    )
    return parser


def _raw_calls(recording, start: int) -> list[dict[str, Any]]:
    result = []
    for call in recording.calls[start:]:
        audit = _public_call_audit(call)
        audit["raw_provider_payload"] = (
            call.response.data if call.response is not None else None
        )
        audit["raw_capture_scope"] = "TEST_ONLY_SYNTHETIC"
        result.append(audit)
    return result


def _finalize_report(report, raw_calls):
    report["report_schema_version"] = "p0-2c3-scenario-provider-smoke-v1"
    report["classification"] = "TEST_ONLY_SYNTHETIC_PROVIDER_SMOKE"
    report["provider_calls"] = raw_calls
    report["provider_call_count"] = len(raw_calls)
    report["transport_attempts"] = sum(item["transport_attempts"] for item in raw_calls)
    report["transport_retries"] = sum(
        max(0, item["transport_attempts"] - 1) for item in raw_calls
    )
    report["finish_reason_distribution"] = dict(sorted(Counter(
        item["finish_reason"] for item in raw_calls
    ).items()))
    report["failure_layers"] = summarize_provider_failure_layers(report)
    report["semantic_retry_calls"] = 0
    return report


def _negative_ready(metrics):
    gold = metrics.get("gold", {})
    return (
        metrics.get("attempt_count") == 3
        and metrics.get("schema_valid_rate") == 1.0
        and metrics.get("contract_valid_rate") == 1.0
        and gold.get("causal_gold_agreement") == 1.0
        and gold.get("breakpoint_agreement") == 1.0
    )


def _positive_ready(metrics):
    gold = metrics.get("gold", {})
    return (
        metrics.get("attempt_count") == 3
        and metrics.get("schema_valid_rate") == 1.0
        and metrics.get("contract_valid_rate") == 1.0
        and gold.get("causal_gold_agreement") == 1.0
        and gold.get("mechanism_selection_agreement") == 1.0
        and gold.get("mechanism_version_agreement") == 1.0
        and gold.get("required_binding_accuracy") == 1.0
        and gold.get("mixed_direct_derived_attempt_rate") == 1.0
    )


def main() -> int:
    args = _parser().parse_args()
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    _load_local_env(args.env_file)
    config = LLMConfig.from_env()
    config.validate()
    manifest = {
        "report_schema_version": "p0-2c3-smoke-manifest-v1",
        "classification": "TEST_ONLY_SYNTHETIC_PROVIDER_SMOKE",
        "provider": _provider_metadata(config),
        "full_hara_run": False,
        "production_default": "v9/v1",
        "formal_avp_mechanism_count": 0,
        "native_structured_output_supported": False,
        "provider_response_constraint": "PROMPT_JSON_SCHEMA",
        "semantic_retry_enabled": False,
    }
    if not args.execute:
        path = output_dir / f"p0-2c3-dry-run-{timestamp}.json"
        _write_report(path, {**manifest, "provider_logical_calls": 0})
        print(path)
        return 0

    malfunction, negative, positive, negative_mech, positive_mech = _synthetic_fixtures()
    recording = RecordingScenarioClient(create_llm_client(config))
    negative_run_id = f"p0-2c3-synth-neg-{timestamp}"
    negative_start = len(recording.calls)
    negative_path = output_dir / f"{negative_run_id}.json"
    negative_report = _run_experiment(
        run_id=negative_run_id, client=recording, malfunction=malfunction,
        scenarios=[negative], contract="v2",
        catalog=InMemoryCausalMechanismCatalog((negative_mech,)), repeat=3,
        gold_by_id={negative.scenario_id: {
            "causally_relevant": False, "breakpoint": "I_TO_H",
            "required_edges": ["M_TO_B", "B_TO_I"],
        }}, progress_path=negative_path,
    )
    negative_report = _finalize_report(
        negative_report, _raw_calls(recording, negative_start)
    )
    _write_report(negative_path, negative_report)
    negative_metrics = negative_report["scenario_metrics"][negative.scenario_id]
    if not _negative_ready(negative_metrics):
        summary_path = output_dir / f"p0-2c3-summary-{timestamp}.json"
        _write_report(summary_path, {
            **manifest,
            "run_ids": [negative_run_id],
            "negative": {"status": "NOT_READY", "metrics": negative_metrics},
            "positive": {"status": "NOT_RUN_NEGATIVE_GATE_FAILED", "metrics": None},
            "real_provider_schema_smoke": "NOT_READY",
            "primary_blocker": "NEGATIVE_SCHEMA_OR_CONTRACT_GATE_FAILED",
        })
        print(summary_path)
        return 2

    positive_run_id = f"p0-2c3-synth-pos-{timestamp}"
    positive_start = len(recording.calls)
    positive_path = output_dir / f"{positive_run_id}.json"
    bindings = {
        "transition_contract": "SCN.positive_chain_contract",
        "ttc": "DERIVED.ttc_s",
    }
    positive_report = _run_experiment(
        run_id=positive_run_id, client=recording, malfunction=malfunction,
        scenarios=[positive], contract="v2",
        catalog=InMemoryCausalMechanismCatalog((positive_mech,)), repeat=3,
        gold_by_id={positive.scenario_id: {
            "causally_relevant": True, "breakpoint": "NONE",
            "required_edges": ["M_TO_B", "B_TO_I", "I_TO_H", "H_TO_HARM"],
            "mechanism_id": positive_mech.mechanism_id,
            "mechanism_version": positive_mech.version,
            "bindings": bindings,
        }}, progress_path=positive_path,
    )
    positive_report = _finalize_report(
        positive_report, _raw_calls(recording, positive_start)
    )
    _write_report(positive_path, positive_report)
    positive_metrics = positive_report["scenario_metrics"][positive.scenario_id]
    positive_ready = _positive_ready(positive_metrics)
    summary_path = output_dir / f"p0-2c3-summary-{timestamp}.json"
    _write_report(summary_path, {
        **manifest,
        "run_ids": [negative_run_id, positive_run_id],
        "negative": {"status": "READY", "metrics": negative_metrics},
        "positive": {
            "status": "READY" if positive_ready else "NOT_READY",
            "metrics": positive_metrics,
        },
        "provider_totals": {
            "logical_calls": negative_report["logical_calls"] + positive_report["logical_calls"],
            "provider_calls": len(recording.calls),
            "transport_attempts": sum(
                _public_call_audit(item)["transport_attempts"] for item in recording.calls
            ),
            "transport_retries": sum(
                max(0, _public_call_audit(item)["transport_attempts"] - 1)
                for item in recording.calls
            ),
        },
        "real_provider_schema_smoke": "READY" if positive_ready else "NOT_READY",
        "primary_blocker": "NONE" if positive_ready else "POSITIVE_SCHEMA_OR_CONTRACT_GATE_FAILED",
    })
    print(summary_path)
    return 0 if positive_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())

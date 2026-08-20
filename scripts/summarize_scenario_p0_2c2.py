#!/usr/bin/env python3
"""Assemble split P0-2c2 runs without making additional Provider calls."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.evaluation.metrics.scenario_real_provider import (
    build_v1_v2_differential,
    compare_order_attempts,
    summarize_provider_attempts,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--synthetic-negative", type=Path, required=True)
    parser.add_argument("--synthetic-positive", type=Path, required=True)
    parser.add_argument("--order-a", type=Path, required=True)
    parser.add_argument("--order-b", type=Path, required=True)
    parser.add_argument("--real-avp-v2", type=Path)
    parser.add_argument("--real-avp-v1", type=Path)
    parser.add_argument("--real-blocked-reason", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--authorized-logical-budget", type=int, default=26)
    parser.add_argument("--aborted-logical-calls", type=int, default=0)
    return parser


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _only_scenario_id(report: dict[str, Any]) -> str:
    scenario_ids = sorted({str(item["scenario_id"]) for item in report["attempts"]})
    if len(scenario_ids) != 1:
        raise ValueError(f"expected one scenario identity, got {scenario_ids}")
    return scenario_ids[0]


def _metrics(report: dict[str, Any]) -> dict[str, Any]:
    scenario_id = _only_scenario_id(report)
    attempts = [
        item for item in report["attempts"] if item["scenario_id"] == scenario_id
    ]
    metrics = summarize_provider_attempts(attempts)
    # Gold definitions are deliberately not inferred from Provider output.  The
    # originating controlled run already computed them from deterministic fixture
    # constants, so retain that audit result while recomputing stability metrics.
    original = report.get("scenario_metrics", {}).get(scenario_id, {})
    if "gold" in original:
        metrics["gold"] = original["gold"]
    return metrics


def _provider_totals(reports: list[dict[str, Any]]) -> dict[str, Any]:
    finishes: Counter[str] = Counter()
    for report in reports:
        finishes.update(report.get("finish_reason_distribution", {}))
    return {
        "completed_logical_calls": sum(int(item.get("logical_calls", 0)) for item in reports),
        "provider_calls": sum(int(item.get("provider_call_count", 0)) for item in reports),
        "transport_attempts": sum(int(item.get("transport_attempts", 0)) for item in reports),
        "transport_retries": sum(int(item.get("transport_retries", 0)) for item in reports),
        "format_retry_calls": sum(int(item.get("format_retry_calls", 0)) for item in reports),
        "finish_reason_distribution": dict(sorted(finishes.items())),
    }


def _acceptance(summary: dict[str, Any]) -> dict[str, Any]:
    negative = summary["synthetic_negative"]["metrics"]
    positive = summary["synthetic_positive"]["metrics"]
    order = summary["batch_order"]["comparison"]
    real = summary["real_avp_v2"].get("metrics") or {}
    neg_gold = negative.get("gold", {})
    pos_gold = positive.get("gold", {})
    schema_ready = all(
        metrics.get("attempt_count") == 5
        and metrics.get("schema_valid_rate") == 1.0
        for metrics in (negative, positive)
    )
    semantic_ready = (
        schema_ready
        and negative.get("contract_valid_rate") == 1.0
        and neg_gold.get("causal_gold_agreement") == 1.0
        and neg_gold.get("breakpoint_agreement") == 1.0
        and positive.get("contract_valid_rate") == 1.0
        and pos_gold.get("causal_gold_agreement") == 1.0
        and pos_gold.get("mechanism_selection_agreement") == 1.0
        and pos_gold.get("mechanism_version_agreement") == 1.0
        and pos_gold.get("required_binding_accuracy") == 1.0
        and order.get("order_causal_agreement") == 1.0
        and order.get("order_mechanism_agreement") == 1.0
        and order.get("order_binding_agreement") == 1.0
    )
    real_observable = (
        real.get("schema_valid_rate") == 1.0
        and (real.get("contract_valid_rate") or 0.0) >= 0.8
    )
    knowledge_blocked = summary["real_avp_v2"].get(
        "available_production_mechanism_count"
    ) == 0
    expressiveness_gap = knowledge_blocked and bool(
        real.get("provider_causal_distribution", {}).get("false", 0)
    )
    if not schema_ready:
        blocker = "PROVIDER_SCHEMA_INSTABILITY"
    elif not semantic_ready:
        blocker = "PROVIDER_SEMANTIC_INSTABILITY"
    elif not real_observable:
        blocker = "KNOWLEDGE_SUBSTRATE_BLOCKED" if knowledge_blocked else (
            "PROVIDER_SEMANTIC_INSTABILITY"
        )
    elif expressiveness_gap:
        blocker = "CONTRACT_EXPRESSIVENESS_GAP"
    elif knowledge_blocked:
        blocker = "KNOWLEDGE_SUBSTRATE_BLOCKED"
    else:
        blocker = "NONE"
    return {
        "provider_schema_stability": "READY" if schema_ready else "NOT_READY",
        "controlled_semantic_stability": "READY" if semantic_ready else "NOT_READY",
        "real_avp_scenario_evidence": "OBSERVATIONAL_ONLY",
        "real_avp_observability_gate_passed": real_observable,
        "contract_expressiveness_gap_observed": expressiveness_gap,
        "knowledge_substrate_blocked": knowledge_blocked,
        "primary_blocker": blocker,
    }


def main() -> int:
    args = _parser().parse_args()
    manifest = _read(args.manifest)
    reports = {
        "synthetic_negative": _read(args.synthetic_negative),
        "synthetic_positive": _read(args.synthetic_positive),
        "order_a": _read(args.order_a),
        "order_b": _read(args.order_b),
    }
    if bool(args.real_avp_v2) != bool(args.real_avp_v1):
        raise ValueError("real AVP v1 and v2 reports must be supplied together")
    if args.real_avp_v2:
        reports["real_avp_v2"] = _read(args.real_avp_v2)
        reports["real_avp_v1"] = _read(args.real_avp_v1)
    elif not args.real_blocked_reason:
        raise ValueError("provide real reports or --real-blocked-reason")
    totals = _provider_totals(list(reports.values()))
    observed_budget_use = totals["completed_logical_calls"] + args.aborted_logical_calls
    if observed_budget_use > args.authorized_logical_budget:
        raise ValueError(
            f"logical budget exceeded: {observed_budget_use} > "
            f"{args.authorized_logical_budget}"
        )
    negative_metrics = _metrics(reports["synthetic_negative"])
    positive_metrics = _metrics(reports["synthetic_positive"])
    real_v2_metrics = (
        _metrics(reports["real_avp_v2"]) if "real_avp_v2" in reports else None
    )
    real_v1_metrics = (
        _metrics(reports["real_avp_v1"]) if "real_avp_v1" in reports else None
    )
    comparison = compare_order_attempts(
        reports["order_a"]["attempts"], reports["order_b"]["attempts"]
    )
    summary: dict[str, Any] = {
        "report_schema_version": "p0-2c2-summary-v1",
        "classification": "EVALUATION_ONLY",
        "provider": manifest["provider"],
        "real_scenario": manifest["real_scenario"],
        "run_ids": [item["run_id"] for item in reports.values()],
        "synthetic_negative": {
            "run_id": reports["synthetic_negative"]["run_id"],
            "metrics": negative_metrics,
        },
        "synthetic_positive": {
            "run_id": reports["synthetic_positive"]["run_id"],
            "metrics": positive_metrics,
        },
        "batch_order": {
            "order_a_run_id": reports["order_a"]["run_id"],
            "order_b_run_id": reports["order_b"]["run_id"],
            "order_a_metrics": reports["order_a"]["scenario_metrics"],
            "order_b_metrics": reports["order_b"]["scenario_metrics"],
            "comparison": comparison,
        },
        "real_avp_v2": {
            "run_id": reports.get("real_avp_v2", {}).get("run_id"),
            "status": (
                "COMPLETED" if real_v2_metrics is not None
                else "NOT_COMPLETED_EXTERNAL_LIMIT"
            ),
            "blocked_reason": args.real_blocked_reason or None,
            "available_production_mechanism_count": 0,
            "metrics": real_v2_metrics,
        },
        "real_avp_v1": {
            "run_id": reports.get("real_avp_v1", {}).get("run_id"),
            "status": (
                "COMPLETED" if real_v1_metrics is not None
                else "NOT_COMPLETED_EXTERNAL_LIMIT"
            ),
            "blocked_reason": args.real_blocked_reason or None,
            "metrics": real_v1_metrics,
        },
        "v1_v2_differential": (
            build_v1_v2_differential(real_v1_metrics, real_v2_metrics)
            if real_v1_metrics is not None and real_v2_metrics is not None
            else {
                "classification": "OBSERVATIONAL_ONLY",
                "status": "NOT_AVAILABLE",
                "blocked_reason": args.real_blocked_reason,
                "semantic_correctness_conclusion": None,
            }
        ),
        "provider_totals": {
            **totals,
            "aborted_unreported_logical_calls": args.aborted_logical_calls,
            "authorized_logical_budget": args.authorized_logical_budget,
            "observed_budget_use": observed_budget_use,
        },
        "architecture": {
            "prompt_modified": False,
            "provider_parameters_modified": False,
            "formal_avp_mechanism_added": False,
            "production_default": "v9/v1",
            "full_hara_run": False,
        },
    }
    summary["acceptance"] = _acceptance(summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

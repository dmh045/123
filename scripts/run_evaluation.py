"""Thin generic entry point for reusable offline evaluation modules."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.evaluation.pre_r4_selector import run_pre_r4_selector_audit
from hara_agent.evaluation.pre_full_r4 import (
    run_calculation_input_readiness, run_project_fact_recall_audit,
    write_baseline_verification, write_method_authority_clarification,
    write_pre_full_r4_closure, write_supported_smoke_report,
)
from hara_agent.evaluation.p5h2 import run_speed_context_consumption_audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluation", choices=(
        "pre-r4-selector", "project-fact-recall",
        "calculation-input-readiness", "p5e-baseline",
        "method-authority-clarification", "supported-r4-smoke",
        "pre-full-r4-closure",
        "p5h2-speed-audit",
    ))
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()
    if args.evaluation == "pre-r4-selector":
        result = run_pre_r4_selector_audit(ROOT)
        detail = f"groups={result['parent_groups']} decision={result['decision']}"
    elif args.evaluation == "project-fact-recall":
        result = run_project_fact_recall_audit(ROOT)
        detail = (
            f"speed_facts={len(result['normalization']['speed_envelopes'])} "
            f"failures={len(result['normalization']['failures'])}"
        )
    elif args.evaluation == "calculation-input-readiness":
        result = run_calculation_input_readiness(ROOT)
        detail = f"groups={result['all_groups']['groups']} input_pipeline_ready=True"
    elif args.evaluation == "p5e-baseline":
        result = write_baseline_verification(ROOT)
        detail = f"groups={result['groups']['total']} historical_intact={result['historical_hashes_intact']}"
    elif args.evaluation == "method-authority-clarification":
        result = write_method_authority_clarification(ROOT)
        detail = f"remaining_gaps={result['true_remaining_method_gaps']}"
    elif args.evaluation == "supported-r4-smoke":
        if not args.run_id:
            parser.error("supported-r4-smoke requires --run-id")
        result = write_supported_smoke_report(ROOT, args.run_id)
        detail = (
            f"executed={result['scope']['executed']} decision={result['decision']}"
        )
    elif args.evaluation == "p5h2-speed-audit":
        result = run_speed_context_consumption_audit(ROOT)
        detail = (
            "before=" + result["before"]["decision"]
            + " after=" + result["after"]["decision"]
        )
    else:
        if not args.run_id:
            parser.error("pre-full-r4-closure requires --run-id")
        result = write_pre_full_r4_closure(ROOT, args.run_id)
        detail = f"decision={result['final_decisions']['full_r4']}"
    provider_calls = result.get(
        "provider_calls",
        result.get("provider", {}).get("calls", 0),
    )
    print(f"evaluation={args.evaluation} provider_calls={provider_calls} {detail}")


if __name__ == "__main__":
    main()

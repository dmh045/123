from __future__ import annotations

import argparse
import json
from pathlib import Path

from hara_agent.application import HARAApplication
from hara_agent.config import RunConfig
from hara_agent.config import LLMConfig
from hara_agent.domains import default_domain_registry
from hara_agent.services.extraction import TemplateInputReader
from hara_agent.services.reporting import HARATemplateContract


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hara-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="运行HARA Agent主链")
    analyze.add_argument("--item", required=True, type=Path)
    analyze.add_argument("--template", required=True, type=Path)
    analyze.add_argument("--output", required=True, type=Path)
    analyze.add_argument("--domain", required=True)
    analyze.add_argument("--run-dir", type=Path, default=Path("runtime/agent"))
    analyze.add_argument("--run-id", default="hara-run")
    analyze.add_argument("--resume", action="store_true")
    analyze.add_argument("--allow-draft", action="store_true")
    analyze.add_argument("--ego-speed-kph", type=float)
    analyze.add_argument("--ego-speed-source", default="")
    analyze.add_argument("--operating-mode")
    analyze.add_argument("--allow-aggregate-speed-fallback", action="store_true")
    analyze.add_argument("--allow-legacy-speed-fallback", action="store_true")
    analyze.add_argument("--max-workers", type=int, default=4)
    doctor = subparsers.add_parser("doctor", help="检查新Agent生产运行条件，不执行分析")
    doctor.add_argument("--template", required=True, type=Path)
    doctor.add_argument("--domain", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        checks = {}
        try:
            profile = default_domain_registry().create(
                args.domain, require_approved=False
            ).profile
            checks["domain_profile"] = {
                "ok": True,
                "version": profile.version,
                "approval_status": profile.approval_status,
                "approved": profile.is_approved,
            }
        except Exception as exc:
            checks["domain_profile"] = {"ok": False, "error": str(exc)}
        try:
            HARATemplateContract().validate(args.template)
            template_inputs = TemplateInputReader().read(args.template)
            checks["template"] = {
                "ok": True,
                "guideword_count": len(template_inputs.guidewords),
                "scenario_dimension_count": len(template_inputs.scenario_dimensions),
                "scoring_standard_counts": template_inputs.scoring_standards.counts,
            }
        except Exception as exc:
            checks["template"] = {"ok": False, "error": str(exc)}
        try:
            LLMConfig.from_env().validate()
            checks["llm"] = {"ok": True}
        except Exception as exc:
            checks["llm"] = {"ok": False, "error": str(exc)}
        ready_for_draft = all(value.get("ok") for value in checks.values())
        ready_for_release = ready_for_draft and checks["domain_profile"].get("approved", False)
        print(json.dumps({
            "ready_for_draft": ready_for_draft,
            "ready_for_release": ready_for_release,
            "checks": checks,
        }, ensure_ascii=False, indent=2))
        return 0 if ready_for_draft else 2
    config = RunConfig(
        item_path=args.item,
        template_path=args.template,
        output_path=args.output,
        run_dir=args.run_dir,
        domain=args.domain,
        resume=args.resume,
        allow_draft=args.allow_draft,
        run_id=args.run_id,
        ego_speed_kph=args.ego_speed_kph,
        ego_speed_source=args.ego_speed_source,
        operating_mode=args.operating_mode,
        allow_aggregate_speed_fallback=args.allow_aggregate_speed_fallback,
        allow_legacy_speed_fallback=args.allow_legacy_speed_fallback,
        max_workers=args.max_workers,
    )
    result = HARAApplication.from_env(config).run()
    print(json.dumps({
        "run_id": result.state.run_id,
        "stage": result.state.stage.value,
        "interrupted": result.interrupted,
        "reason": result.reason,
        "can_publish": result.state.can_publish,
        "risk_count": len(result.state.risk_results),
        "safety_goal_count": len(result.state.safety_goals),
    }, ensure_ascii=False, indent=2))
    if result.reason == "draft_report_generated_pending_review":
        return 0
    return 2 if result.interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())

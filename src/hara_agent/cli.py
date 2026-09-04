from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from hara_agent.application import HARAApplication
from hara_agent.config import RunConfig
from hara_agent.config import LLMConfig, load_local_env
from hara_agent.contracts import (
    CompileStatus, TemplateRole, TemplateRoleConfirmation,
)
from hara_agent.template import TemplateRoleCompiler, TemplateRoleManifestStore
from hara_agent.method_sources import MethodSourceResolver
from hara_agent.workflow import ReviewArtifactReader, render_review


def _role_compiler() -> TemplateRoleCompiler:
    manifest_root = os.getenv(
        "HARA_TEMPLATE_ROLE_MANIFEST_DIR",
        "runtime/agent/template-role-manifests",
    )
    return TemplateRoleCompiler(
        manifest_store=TemplateRoleManifestStore(manifest_root)
    )


def _parse_role_selections(values: list[str]) -> dict[str, dict[str, str]]:
    selections: dict[str, dict[str, str]] = {}
    for value in values:
        role_name, separator, location = value.partition("=")
        sheet, location_separator, region = location.rpartition("!")
        if not separator or not location_separator or not sheet or not region:
            raise ValueError(
                f"Invalid --select {value!r}; expected ROLE=SHEET!A1:B9"
            )
        role = TemplateRole(role_name.strip())
        if role.value in selections:
            raise ValueError(f"Duplicate role selection: {role.value}")
        selections[role.value] = {
            "sheet": sheet.strip(),
            "region": region.strip().replace("$", ""),
        }
    return selections


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hara-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="运行HARA Agent主链")
    analyze.add_argument("--item", required=True, type=Path)
    analyze.add_argument("--template", type=Path)
    analyze.add_argument(
        "--method-baseline", type=Path,
        default=Path("method_assets/fusa_baseline_v1/manifest.yaml"),
    )
    analyze.add_argument(
        "--report-template", type=Path,
        default=Path("references/HARA_Template_AI_20260327.xlsx"),
    )
    analyze.add_argument("--output", required=True, type=Path)
    analyze.add_argument("--run-dir", type=Path, default=Path("runtime/agent"))
    analyze.add_argument("--run-id", default="hara-run")
    analyze.add_argument("--resume", action="store_true")
    analyze.add_argument("--allow-draft", action="store_true")
    analyze.add_argument("--ego-speed-kph", type=float)
    analyze.add_argument("--ego-speed-source", default="")
    analyze.add_argument("--operating-mode")
    analyze.add_argument("--allow-aggregate-speed-fallback", action="store_true")
    analyze.add_argument("--max-workers", type=int, default=4)
    doctor = subparsers.add_parser("doctor", help="检查模板和运行配置，不执行分析")
    doctor.add_argument("--template", type=Path)
    doctor.add_argument(
        "--method-baseline", type=Path,
        default=Path("method_assets/fusa_baseline_v1/manifest.yaml"),
    )
    doctor.add_argument(
        "--report-template", type=Path,
        default=Path("references/HARA_Template_AI_20260327.xlsx"),
    )
    confirm = subparsers.add_parser(
        "confirm-template-role",
        help="一次性确认有歧义的模板角色，并按模板哈希保存系统清单",
    )
    confirm.add_argument("--template", required=True, type=Path)
    confirm.add_argument(
        "--select", action="append", required=True,
        metavar="ROLE=SHEET!A1:B9",
    )
    confirm.add_argument("--confirmed-by", required=True)
    confirm.add_argument("--rationale", default="")
    review = subparsers.add_parser(
        "review", help="只读查看运行中间审计 artifacts，不执行 HARA 或 LLM"
    )
    review.add_argument("--run-id", required=True)
    review.add_argument("--function")
    review.add_argument("--malfunction")
    review.add_argument("--scenario")
    review.add_argument("--all", action="store_true", dest="all_records")
    review.add_argument("--limit", type=int, default=3)
    review.add_argument("--feasible", action="store_true")
    review.add_argument("--infeasible", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_local_env()
    args = build_parser().parse_args(argv)
    if args.command == "confirm-template-role":
        compiler = _role_compiler()
        snapshot = compiler.scanner.scan(args.template)
        confirmation = TemplateRoleConfirmation(
            template_hash=snapshot.source_hash,
            selected_regions=_parse_role_selections(args.select),
            confirmed_by=args.confirmed_by,
            confirmed_at=datetime.now(timezone.utc).isoformat(),
            rationale=args.rationale,
        )
        method = compiler.compile_method(
            args.template,
            confirmation=confirmation,
            use_manifest=False,
        )
        print(json.dumps({
            "template_hash": snapshot.source_hash,
            "compile_status": method.compile_status.value,
            "engineering_rules_compiled": method.engineering_rules_compiled,
            "confirmed_roles": sorted(confirmation.selected_regions),
            "manifest": str(compiler.manifest_store.path_for(snapshot.source_hash)),
            "blocking_diagnostics": [
                item.message for item in method.blocking_diagnostics
            ],
        }, ensure_ascii=False, indent=2))
        return 0 if not method.blocking_diagnostics else 2
    if args.command == "review":
        if args.limit < 0:
            raise ValueError("--limit must not be negative")
        if args.feasible and args.infeasible:
            raise ValueError("--feasible and --infeasible are mutually exclusive")
        reader = ReviewArtifactReader(
            args.run_id,
            os.getenv("HARA_REVIEW_ARTIFACT_DIR", "runtime/review"),
        )
        print(render_review(
            reader,
            function_id=args.function or "",
            malfunction_id=args.malfunction or "",
            scenario_id=args.scenario or "",
            all_records=args.all_records,
            limit=args.limit,
            feasible=args.feasible,
            infeasible=args.infeasible,
        ))
        for warning in reader.warnings:
            print(f"WARNING: {warning}")
        return 0
    if args.command == "doctor":
        checks = {}
        try:
            resolution = MethodSourceResolver().resolve(
                template_path=args.template,
                baseline_manifest_path=args.method_baseline,
                report_template_path=(
                    args.template if args.template is not None else args.report_template
                ),
            )
            method = resolution.method
            method_ok = (
                method.compile_status is not CompileStatus.NOT_READY
                and method.engineering_rules_compiled
            )
            checks["method_contract"] = {
                "ok": method_ok,
                "template_hash": method.metadata["template_hash"],
                "method_source_kind": resolution.source_kind.value,
                "method_source_hash": method.metadata.get("method_source_hash", method.metadata["template_hash"]),
                "report_template_hash": resolution.report_template_hash,
                "contract_version": method.contract_version,
                "compiler_version": method.compiler_version,
                "compile_status": method.compile_status.value,
                "guideword_count": len(method.guidewords.guidewords),
                "scenario_dimension_count": len(method.scenario_model.dimensions),
                "required_fact_count": len(method.required_fact_specs),
                "warning_codes": sorted({
                    item.code.value for item in method.warnings
                }),
                "blocking_diagnostics": [
                    item.message for item in method.blocking_diagnostics
                ],
            }
        except Exception as exc:
            checks["method_contract"] = {"ok": False, "error": str(exc)}
        try:
            LLMConfig.from_env().validate()
            checks["llm"] = {"ok": True}
        except Exception as exc:
            checks["llm"] = {"ok": False, "error": str(exc)}
        ready_for_draft = all(value.get("ok") for value in checks.values())
        ready_for_release = False
        print(json.dumps({
            "ready_for_draft": ready_for_draft,
            "ready_for_release": ready_for_release,
            "release_blockers": [
                "Release requires run-specific fact, risk, SG, and Safe-State approvals"
            ],
            "checks": checks,
        }, ensure_ascii=False, indent=2))
        return 0 if ready_for_draft else 2
    config = RunConfig(
        item_path=args.item,
        template_path=args.template,
        output_path=args.output,
        run_dir=args.run_dir,
        method_baseline_path=args.method_baseline,
        report_template_path=args.report_template,
        resume=args.resume,
        allow_draft=args.allow_draft,
        run_id=args.run_id,
        ego_speed_kph=args.ego_speed_kph,
        ego_speed_source=args.ego_speed_source,
        operating_mode=args.operating_mode,
        allow_aggregate_speed_fallback=args.allow_aggregate_speed_fallback,
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

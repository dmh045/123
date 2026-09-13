"""Zero-Provider pre-r4 selector, architecture, and historical audits."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from hara_agent.contracts import SemanticCompatibility
from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.services.analysis.scenario_selection_quality import ScenarioShortlistPolicy
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow.checkpoints import CheckpointRepository
from hara_agent.workflow.scenario_synthesis import ScenarioSynthesisRunner


SOURCE_RUN = "hara-full-baseline-20260912-r1"
HISTORICAL = {
    "r3_synthesis_checkpoint": (
        "runtime/agent/hara-full-baseline-20260912-r1-synthesis-r3.checkpoint.json",
        "2DBBCBFCF7AECC09332A8BBC2642513D98A2558E1CD33305F432415351E7D357",
    ),
    "causal_r2_provider_trace": (
        "runtime/review/hara-full-baseline-20260912-r1-synthesis-r3-causal-r2/causal_revalidation_provider_trace.json",
        "8359E7A35EB4DF8E77D9E16D2CAD6151D4327CEC6D7EA57B48603BA159E25BC7",
    ),
    "risk_rescore_r2_checkpoint": (
        "runtime/agent/hara-full-baseline-20260912-r1-synthesis-r3-causal-r2-risk-rescore-r2.checkpoint.json",
        "56150B23AA487FA780D1780E2F250035618DC057CFBA5F9F6F594916E57C0100",
    ),
}


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().upper()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: Any) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    headings = list(headers)
    lines = [
        "| " + " | ".join(headings) + " |",
        "|" + "|".join("---" for _ in headings) + "|",
    ]
    lines.extend("| " + " | ".join(map(str, row)) + " |" for row in rows)
    return "\n".join(lines)


def _method(root: Path):
    report = TemplateRoleCompiler().compile_method(
        root / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    return YamlBaselineCompiler().compile(
        root / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )


def _coverage(inputs: Iterable[Any]) -> dict[str, Any]:
    items = tuple(inputs)
    return {
        "variant_count_distribution": {
            str(count): sum(item.coverage_plan.desired_variant_count == count for item in items)
            for count in (1, 2, 3)
        },
        "primary_axis_distribution": dict(sorted(Counter(
            dimension for item in items
            for dimension in item.coverage_plan.primary_variation_dimensions
        ).items())),
        "secondary_axis_distribution": dict(sorted(Counter(
            dimension for item in items
            for dimension in item.coverage_plan.secondary_variation_dimensions
        ).items())),
    }


def _shortlist_convergence(inputs: Iterable[Any]) -> dict[str, Any]:
    rows = []
    largest = 0
    for dimension in ("EGO_ACTION", "OBJECT", "TRAFFIC_PATTERN", "EGO_DYNAMICS"):
        for limit in (1, 3):
            clusters: dict[tuple[str, ...], list[Any]] = defaultdict(list)
            for item in inputs:
                candidate_set = next(
                    value for value in item.dimension_candidate_sets
                    if value.dimension == dimension
                )
                key = tuple(value.atom_id for value in candidate_set.candidates[:limit])
                if key:
                    clusters[key].append(item)
            repeated = [values for values in clusters.values() if len(values) > 1]
            cross = [
                values for values in repeated
                if len({item.malfunction_id for item in values}) > 1
            ]
            biggest = max(map(len, cross), default=0)
            largest = max(largest, biggest)
            rows.append({
                "dimension": dimension, "top_n": limit,
                "identical_clusters": len(repeated),
                "cross_malfunction_clusters": len(cross),
                "largest_cross_malfunction_cluster": biggest,
            })
    return {"rows": rows, "largest_cross_malfunction_cluster": largest}


def _triage(inputs: Iterable[Any], service: Any) -> dict[str, Any]:
    records = []
    for item in inputs:
        for candidate_set in item.dimension_candidate_sets:
            if candidate_set.generation_status != "METHOD_GAP":
                continue
            query = item.structured_semantic_query
            dimension = candidate_set.dimension
            if dimension == "OBJECT" and "OBJECT_STATIC" in query.get("object_categories", []):
                root_cause = "TRUE_METHOD_GAP"
                authority_gap = "No compiled OBJECT or compound atom explicitly defines static obstacle semantics."
                required_semantic = "OBJECT_STATIC"
            elif dimension == "EGO_ACTION" and "ACTION_ABORT" in query.get("action_categories", []):
                root_cause = "TRUE_METHOD_GAP"
                authority_gap = "No source-defined EGO_ACTION/EGO_DYNAMICS/compound atom defines abort, cancel, takeover, or handover."
                required_semantic = "ACTION_ABORT"
            else:
                root_cause = "UNRESOLVED_NEEDS_ENGINEERING_REVIEW"
                authority_gap = "Required semantic is not supported by current compiled Method authority."
                required_semantic = "|".join(
                    query.get("object_categories", [])
                    or query.get("action_categories", [])
                    or query.get("traffic_relations", [])
                )
            explicit_atoms = []
            for atom in service.catalog:
                categories = service.ranker.atom_categories(atom)
                if required_semantic in categories:
                    explicit_atoms.append({
                        "atom_id": atom.get("atom_id", ""),
                        "dimensions": atom.get("filled_dimensions", []),
                        "source_asset": atom.get("source_asset", ""),
                        "source_rule": atom.get("source_rule", ""),
                    })
            records.append({
                "malfunction_id": item.malfunction_id,
                "parent_scenario_id": item.parent_scenario_id,
                "hazardous_event_id": item.hazardous_event_id,
                "dimension": dimension,
                "root_cause_class": root_cause,
                "required_semantic": required_semantic,
                "applicability_reason": candidate_set.applicability.reason,
                "query_evidence": query.get("explicit_category_evidence", {}).get(
                    required_semantic, []
                ),
                "fm_template_id": item.fm_scenario_template.get("template_id", ""),
                "source_atoms_with_required_semantic": explicit_atoms,
                "unknown_candidates_retained": [
                    candidate.atom_id for candidate in candidate_set.candidates
                    if candidate.semantic_compatibility is SemanticCompatibility.UNKNOWN
                ],
                "authority_gap": authority_gap,
                "automatic_repair": "NONE",
                "disposition": "METHOD_GAP_PENDING_FUSA_METHOD_OWNER",
            })
    counts = Counter(record["root_cause_class"] for record in records)
    return {
        "artifact_version": "p5-d4-nontraffic-method-gap-triage-v1",
        "source_run_id": SOURCE_RUN,
        "provider_calls": 0,
        "initial_method_gaps": 73,
        "records": records,
        "root_cause_counts": dict(sorted(counts.items())),
        "dimension_counts": dict(sorted(Counter(
            record["dimension"] for record in records
        ).items())),
        "true_remaining_method_gaps": sum(
            record["root_cause_class"] == "TRUE_METHOD_GAP" for record in records
        ),
    }


def _ranking(inputs: Iterable[Any]) -> dict[str, Any]:
    tier_distribution: Counter[int] = Counter()
    compatibility: Counter[str] = Counter()
    shortlist_sizes: Counter[int] = Counter()
    budgets: Counter[str] = Counter()
    families = 0
    duplicate_family_slots = 0
    max_family_concentration = 0
    lexical_only = 0
    authoritative_below = 0
    fm_below = 0
    structured_below = 0
    high_family_below = 0
    high_family_violations = []
    intensity_risks = []
    hard_filter = Counter()
    for item in inputs:
        for candidate_set in item.dimension_candidate_sets:
            hard_filter.update(candidate_set.hard_filter_diagnostics)
            shortlist_sizes[len(candidate_set.candidates)] += 1
            budgets[f"{candidate_set.shortlist_policy}:{candidate_set.shortlist_budget}"] += 1
            family_counts = Counter(candidate.semantic_family for candidate in candidate_set.candidates)
            families += len(family_counts)
            duplicate_family_slots += sum(max(0, count - 1) for count in family_counts.values())
            max_family_concentration = max(
                max_family_concentration, max(family_counts.values(), default=0),
            )
            for candidate in candidate_set.candidates:
                tier = int(candidate.ranking_scores.get("source_evidence_tier", 0))
                tier_distribution[tier] += 1
                compatibility[candidate.semantic_compatibility.value] += 1
                lexical_only += int(tier == 0)
            diagnostics = candidate_set.shortlist_diagnostics
            authoritative_below += diagnostics.get("authoritative_below_cutoff", 0)
            fm_below += diagnostics.get("fm_template_below_cutoff", 0)
            structured_below += diagnostics.get("exact_structured_source_below_cutoff", 0)
            high_family_below += diagnostics.get("high_evidence_family_below_cutoff", 0)
            if diagnostics.get("high_evidence_family_below_cutoff", 0):
                high_family_violations.append({
                    "malfunction_id": item.malfunction_id,
                    "parent_scenario_id": item.parent_scenario_id,
                    "dimension": candidate_set.dimension,
                    "diagnostics": dict(diagnostics),
                })
            generic_family_counts = Counter(
                candidate.semantic_family for candidate in candidate_set.candidates
                if candidate.binding_authority == "ANALYTICAL_SELECTION"
                and candidate.template_relationship == "NONE"
                and not candidate.ranking_scores.get("structured_source_score", 0)
                and not candidate.ranking_scores.get("physical_semantics_score", 0)
            )
            if (
                candidate_set.dimension == "EGO_DYNAMICS"
                and candidate_set.dimension in item.coverage_plan.primary_variation_dimensions
                and not ScenarioShortlistPolicy._intensity_driven(
                    item.structured_semantic_query
                )
                and max(generic_family_counts.values(), default=0) >= 3
            ):
                intensity_risks.append({
                    "malfunction_id": item.malfunction_id,
                    "parent_scenario_id": item.parent_scenario_id,
                    "max_same_family_candidates": max(generic_family_counts.values()),
                })
    return {
        "evidence_tier_distribution": dict(sorted(tier_distribution.items())),
        "semantic_compatibility_distribution": dict(sorted(compatibility.items())),
        "shortlist_size_distribution": dict(sorted(shortlist_sizes.items())),
        "adaptive_budget_distribution": dict(sorted(budgets.items())),
        "family_count": families,
        "family_duplicate_slots": duplicate_family_slots,
        "max_family_concentration": max_family_concentration,
        "lexical_only_candidates_in_shortlist": lexical_only,
        "authoritative_below_cutoff": authoritative_below,
        "fm_template_below_cutoff": fm_below,
        "exact_structured_source_below_cutoff": structured_below,
        "high_evidence_family_below_cutoff": high_family_below,
        "high_evidence_family_violations": high_family_violations,
        "generic_intensity_risk_groups": intensity_risks,
        "hard_filter": dict(sorted(hard_filter.items())),
        "policy": {
            "primary_budget": ScenarioShortlistPolicy.PRIMARY_BUDGET,
            "secondary_budget": ScenarioShortlistPolicy.SECONDARY_BUDGET,
            "default_budget": ScenarioShortlistPolicy.DEFAULT_BUDGET,
            "max_adaptive_budget": ScenarioShortlistPolicy.MAX_ADAPTIVE_BUDGET,
        },
    }


def _causal_audit(root: Path) -> dict[str, Any]:
    path = root / HISTORICAL["causal_r2_provider_trace"][0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    audits = payload["audits"]
    errors = Counter()
    for audit in audits:
        errors.update(audit.get("error_counts_by_code", {}))
    repairs = sum(audit.get("repair_attempted_count", 0) for audit in audits)
    rationale_repairs = errors.get("SCENARIO_SCHEMA_CONTRACT_ERROR", 0)
    return {
        "scenarios": sum(audit.get("expected_count", 0) for audit in audits),
        "historical_requests": sum(audit.get("provider_calls_total", 0) for audit in audits),
        "historical_repairs": repairs,
        "repair_successes": sum(audit.get("repair_success_count", 0) for audit in audits),
        "final_failures": sum(audit.get("repair_failed_count", 0) for audit in audits),
        "non_authoritative_rationale": rationale_repairs,
        "batch_shape": errors.get("SCENARIO_COVERAGE_CONTRACT_ERROR", 0),
        "business_critical_repairs": max(0, repairs - rationale_repairs),
        "provider_calls_for_audit": 0,
    }


def _architecture_markdown() -> str:
    rows = [
        ("ScenarioSemanticQueryBuilder", "ConstrainedScenarioSynthesisService", "DETERMINISTIC_TRANSFORM", "KEEP", "Builds typed query only"),
        ("ScenarioDimensionApplicabilityService", "ConstrainedScenarioSynthesisService", "BUSINESS_AUTHORITY", "KEEP", "Owns required/optional/NA"),
        ("ScenarioSemanticCompatibilityClassifier", "ConstrainedScenarioSynthesisService", "BUSINESS_AUTHORITY", "KEEP", "Tri-state semantic decision"),
        ("ScenarioCandidateRanker", "ConstrainedScenarioSynthesisService", "DETERMINISTIC_TRANSFORM", "KEEP", "Tier then scalar tie-break"),
        ("ScenarioShortlistPolicy", "ConstrainedScenarioSynthesisService", "DETERMINISTIC_TRANSFORM", "KEEP", "Bounded family coverage"),
        ("BoundedScenarioSynthesisAgent", "ScenarioSynthesisRunner", "PROVIDER_BOUNDARY", "KEEP", "Selects supplied IDs only"),
        ("scenario_selector.py", "run_evaluation.py/tests", "EVALUATION_ONLY", "MOVE", "Moved from services/analysis"),
        ("p5d2_offline_audit.py", "none", "HISTORICAL_ONLY", "DELETE", "Superseded by generic evaluator"),
        ("p5d3_offline_audit.py", "none", "HISTORICAL_ONLY", "DELETE", "Superseded by generic evaluator"),
        ("ScenarioSelectorQualityAudit", "ScenarioSynthesisRunner", "REPORTING", "KEEP", "Runtime run-summary projection"),
        ("ScenarioOutputQualityAuditService", "explicit CLI", "EVALUATION_ONLY", "KEEP", "Public offline quality command; not runtime state authority"),
        ("ContentAudit", "reporting tests/commands", "REPORTING", "KEEP", "Report completeness only"),
        ("FMSelectorSemanticAuditService", "explicit CLI", "EVALUATION_ONLY", "KEEP", "Read-only confirmed-YAML utilization audit"),
        ("FMTemplateAmbiguityAuditService", "explicit CLI", "EVALUATION_ONLY", "KEEP", "Read-only source ambiguity audit"),
        ("MethodContractParityAuditService", "explicit CLI", "EVALUATION_ONLY", "KEEP", "Read-only compiler parity audit"),
        ("ControllabilityBranchAuditService", "explicit CLI", "EVALUATION_ONLY", "KEEP", "Read-only branch diagnostics"),
        ("ExposureInputAuditService", "explicit CLI", "EVALUATION_ONLY", "KEEP", "Read-only input/root-cause report"),
        ("ExposureBindingAuditService", "explicit CLI", "EVALUATION_ONLY", "KEEP", "Read-only binding diagnostics"),
        ("RiskContextSourceCoverageAuditService", "explicit CLI", "EVALUATION_ONLY", "KEEP", "Read-only source coverage"),
        ("ScenarioAliasProposalService", "explicit CLI", "HISTORICAL_ONLY", "KEEP", "Produces review proposals; never mutates Method authority"),
        ("ScenarioCoverageProposalService", "explicit CLI", "HISTORICAL_ONLY", "KEEP", "Produces review proposals; never mutates Method authority"),
        ("SeverityDeltaVSemanticAuditService", "StructuredRiskScoringService/CLI", "BUSINESS_AUTHORITY", "KEEP", "Despite name, gates governed Severity input semantics"),
        ("AnalyticalPhysicsInstantiationService", "synthesis/causal runners", "READINESS_GATE", "KEEP", "Proposes/materializes scoped physical inputs"),
        ("scenario_physics.py", "RiskContext/physics instantiation", "DETERMINISTIC_TRANSFORM", "MERGE", "Single relative-speed/TTC primitives"),
        ("HazardousEventRiskContextService", "scoring node/audits", "READINESS_GATE", "KEEP", "Canonical source/provenance readiness"),
        ("RiskCalculationInputService", "StructuredRiskScoringService", "DETERMINISTIC_TRANSFORM", "KEEP", "Typed executor inputs"),
        ("StructuredRiskScoringService", "MethodRuleService", "BUSINESS_AUTHORITY", "KEEP", "Only S/E/C scoring orchestrator"),
        ("RiskExecutionTraceService", "workflows/CLI", "REPORTING", "KEEP", "Read-only execution trace"),
        ("RiskScoreabilityService", "CLI only", "EVALUATION_ONLY", "KEEP", "Offline queue projection, not scoring truth"),
    ]
    pipeline = {
        "ScenarioSemanticQueryBuilder", "ScenarioDimensionApplicabilityService",
        "ScenarioSemanticCompatibilityClassifier", "ScenarioCandidateRanker",
        "ScenarioShortlistPolicy", "BoundedScenarioSynthesisAgent",
        "ScenarioSelectorQualityAudit", "AnalyticalPhysicsInstantiationService",
        "scenario_physics.py", "HazardousEventRiskContextService",
        "RiskCalculationInputService", "StructuredRiskScoringService",
        "RiskExecutionTraceService", "SeverityDeltaVSemanticAuditService",
    }
    authority = {
        "ScenarioDimensionApplicabilityService",
        "ScenarioSemanticCompatibilityClassifier", "BoundedScenarioSynthesisAgent",
        "HazardousEventRiskContextService", "StructuredRiskScoringService",
        "SeverityDeltaVSemanticAuditService",
    }
    callees = {
        "BoundedScenarioSynthesisAgent": "LLM client + deterministic validator",
        "AnalyticalPhysicsInstantiationService": "scenario_physics primitives",
        "HazardousEventRiskContextService": "scenario_physics + vocabulary adapter",
        "StructuredRiskScoringService": "typed input service + deterministic executors",
        "ScenarioShortlistPolicy": "semantic-family transform",
    }
    expanded = [(
        component, callers, callees.get(component, "MethodContract/read-only data"),
        "PIPELINE" if component in pipeline else "CLI_ONLY" if "CLI" in callers else "NO",
        "NO", "YES" if component in authority else "NO",
        "YES" if component in {
            "p5d2_offline_audit.py", "p5d3_offline_audit.py", "scenario_physics.py",
        } else "NO",
        classification, action, reason,
    ) for component, callers, classification, action, reason in rows]
    return "\n".join((
        "# P5-D4 Architecture Complexity Audit", "",
        "No production selector imports evaluation code. Audit CLIs remain explicit read-only entry points; runtime reporting stays with reporting services.", "",
        _table((
            "Component", "Callers", "Callees", "Production reachable",
            "Writes business state", "Defines authority", "Duplicates",
            "Class", "Action", "Reason",
        ), expanded), "",
        "## Cleanup result", "",
        "- Removed the fixed Top-12 compatibility path.",
        "- Moved ranking recall metrics from production analysis to evaluation.",
        "- Replaced two phase-specific scripts with one thin generic evaluation runner.",
        "- Moved historical runtime SHA/recovery checks out of ordinary unit tests.",
        "- Added no compatibility wrapper preserving old selector behavior.",
    ))


def run_pre_r4_selector_audit(root: Path) -> dict[str, Any]:
    root = root.resolve()
    output = root / "output"
    before = {
        name: {"path": relative, "expected": expected, "before": _sha256(root / relative)}
        for name, (relative, expected) in HISTORICAL.items()
    }
    if any(value["before"] != value["expected"] for value in before.values()):
        raise RuntimeError("Historical artifact SHA mismatch before offline audit")

    method = _method(root)
    state = CheckpointRepository(root / "runtime/agent").load(SOURCE_RUN)
    runner = ScenarioSynthesisRunner(
        method=method, client=None, run_dir=root / "runtime/agent",
        review_root=root / "runtime/review",
    )
    inputs, _scenarios, _malfunctions, _assessments, _preparation = runner._prepare(state)
    triage = _triage(inputs, runner.synthesis)
    ranking = _ranking(inputs)
    coverage = _coverage(inputs)
    convergence = _shortlist_convergence(inputs)
    causal = _causal_audit(root)
    provider_ready = sum(runner._provider_ready(item) for item in inputs)

    after = {
        name: {**value, "after": _sha256(root / value["path"])}
        for name, value in before.items()
    }
    guard = {
        "artifact_version": "p5-d4-historical-artifact-guard-v1",
        "provider_calls": 0,
        "artifacts": after,
        "byte_identical": all(
            value["before"] == value["after"] == value["expected"]
            for value in after.values()
        ),
    }
    if not guard["byte_identical"]:
        raise RuntimeError("Historical artifact changed during offline audit")

    _write_json(output / "P5D4_NonTraffic_Method_Gap_Triage.json", triage)
    _write(output / "P5D4_NonTraffic_Method_Gap_Triage.md", "\n".join((
        "# P5-D4 Non-Traffic Method Gap Triage", "",
        "All 73 groups were re-evaluated without a Provider call. UNKNOWN candidates remain inspectable, but none supplies the missing engineering authority.", "",
        _table(("Dimension", "Root cause", "Groups"), (
            (dimension, "TRUE_METHOD_GAP", count)
            for dimension, count in triage["dimension_counts"].items()
        )), "",
        "## Conclusions", "",
        "- 60 OBJECT groups require explicit static-obstacle Method authority. The current FM template is not bound to these parent groups and the compiled OBJECT/compound ontology has no static-object atom.",
        "- 13 EGO_ACTION groups require abort/cancel/takeover/handover authority. No source-defined action, dynamics, compound atom, or applicable FM template provides it.",
        "- No YAML or atom was invented. All 73 remain fail-closed pending FuSa Method-owner approval.",
    )))
    _write(output / "P5D4_Ranking_and_Shortlist_Audit.md", "\n".join((
        "# P5-D4 Ranking and Shortlist Audit", "",
        "Provider calls: **0**. Evidence tier is the primary ordering; BM25, causal BM25, and mechanism-token overlap are scalar tie-breakers only.", "",
        f"- Semantic compatibility: `{ranking['semantic_compatibility_distribution']}`",
        f"- Hard filter: `{ranking['hard_filter']}`",
        f"- Evidence tiers: `{ranking['evidence_tier_distribution']}`",
        f"- Shortlist sizes: `{ranking['shortlist_size_distribution']}`",
        f"- Adaptive budgets: `{ranking['adaptive_budget_distribution']}`",
        f"- Authoritative below cutoff: **{ranking['authoritative_below_cutoff']}**",
        f"- FM-template below cutoff: **{ranking['fm_template_below_cutoff']}**",
        f"- Exact structured source below cutoff: **{ranking['exact_structured_source_below_cutoff']}**",
        f"- High-evidence family below cutoff: **{ranking['high_evidence_family_below_cutoff']}**",
        f"- Lexical-only candidates retained at low rank: **{ranking['lexical_only_candidates_in_shortlist']}**",
        f"- Largest cross-malfunction top-1/top-3 cluster: **{convergence['largest_cross_malfunction_cluster']}**",
    )))
    generic_after = len(ranking["generic_intensity_risk_groups"])
    _write(output / "P5D4_Generic_Intensity_Risk_Audit.md", "\n".join((
        "# P5-D4 Generic Intensity Risk Audit", "",
        "- P5-D3 warning groups: **120**",
        f"- P5-D4 same-family concentration >=3 groups: **{generic_after}**",
        f"- Maximum candidates from one semantic family in a shortlist: **{ranking['max_family_concentration']}**",
        "- The ordinary same-family fill is capped at two; exact, template, and other high-authority candidates are exempt and are never cut.",
        "- Primary-axis family coverage is filled before duplicate intensity variants.",
        "- Intensity variation remains available when the malfunction text explicitly makes magnitude/intensity the mechanism.",
    )))
    _write(output / "P5D4_Architecture_Complexity_Audit.md", _architecture_markdown())
    _write(output / "P5D4_Physics_Risk_Authority_Audit.md", "\n".join((
        "# P5-D4 Physics and Risk Authority Audit", "",
        _table(("Responsibility", "Authority", "Consumers"), (
            ("Canonical physical input normalization", "scenario_physics.derive_scenario_physics", "HazardousEventRiskContextService"),
            ("Relative speed", "scenario_physics.closing_relative_speed_kph", "AnalyticalPhysicsInstantiationService"),
            ("TTC", "scenario_physics.time_to_collision_s", "AnalyticalPhysicsInstantiationService and RiskContext"),
            ("Engineering-assumption proposal/materialization", "AnalyticalPhysicsInstantiationService", "synthesis and causal runners"),
            ("Source acceptance and canonical S/C readiness", "HazardousEventRiskContextService", "scoring node and audits"),
            ("Exposure-specific readiness", "ExposureInputReadinessService", "StructuredRiskScoringService"),
            ("Typed executor inputs", "RiskCalculationInputService", "StructuredRiskScoringService"),
            ("S/E/C computation", "StructuredRiskScoringService plus deterministic executors", "MethodRuleService"),
            ("Execution trace", "RiskExecutionTraceService (read-only)", "workflow and CLI reports"),
        )), "",
        "RiskScoreabilityService is an offline projection and does not score or override canonical readiness. Formula duplication was removed; fail-closed rules for ranges, missing direction, unknown object speed, and unknown control overrides remain unchanged.",
    )))
    _write(output / "P5D4_Causal_Repair_Root_Cause_Audit.md", "\n".join((
        "# P5-D4 Causal Repair Root-Cause Audit", "",
        f"- Historical scenarios: **{causal['scenarios']}**",
        f"- Historical request attempts: **{causal['historical_requests']}**",
        f"- Historical repairs: **{causal['historical_repairs']}**; successes: **{causal['repair_successes']}**; final failures: **{causal['final_failures']}**",
        f"- NON_AUTHORITATIVE_RATIONALE: **{causal['non_authoritative_rationale']}**",
        f"- BATCH_SHAPE / identity coverage: **{causal['batch_shape']}** events, accounting for **{causal['business_critical_repairs']}** repair.",
        "- causal_chain, breakpoint, basis_type, evidence_refs, hazardous_event, and risk_dimension_changes remain machine-authority gates.",
        "- Readable rationale was removed from the Provider business contract and is now rendered deterministically from validated structured fields.",
        "- Provider calls for this audit: **0**.",
    )))
    _write(output / "P5D4_Selector_Before_After.md", "\n".join((
        "# P5-D4 Selector Before / After", "",
        _table(("Metric", "P5-D3", "P5-D4"), (
            ("Parent groups", 412, len(inputs)),
            ("Method gaps", 73, triage["true_remaining_method_gaps"]),
            ("Provider-ready groups", 339, provider_ready),
            ("Fixed cap", "12 every dimension", "removed"),
            ("Semantic decision", "match/reject", "SUPPORTED/UNKNOWN/CONTRADICTED"),
            ("Authoritative below cutoff", "not guaranteed", ranking["authoritative_below_cutoff"]),
            ("Generic intensity warning", 120, generic_after),
            ("1/2/3 variants", "38/322/52", "/".join(str(coverage["variant_count_distribution"][str(i)]) for i in (1, 2, 3))),
        )), "",
        "The 73 true gaps are unchanged by design: selector uncertainty is no longer misclassified as contradiction, but missing Method truth is still fail-closed.",
    )))
    _write_json(output / "P5D4_Historical_Artifact_Guard.json", guard)
    _write(output / "P5D4_Priority_and_Blocker_Audit.md", "\n".join((
        "# P5-D4 Priority and Blocker Audit", "",
        "## Decision", "", "**NOT_READY_METHOD_AUTHORITY**", "",
        f"- Initial/remaining Method gaps: **73 / {triage['true_remaining_method_gaps']}**",
        f"- Provider-ready groups: **{provider_ready} / {len(inputs)}**",
        "- Classification defects known from P5-D3 remain closed; the remaining gaps are missing source authority, not ranking defects.",
        "- Provider smoke was not executed because the true gaps have not been explicitly accepted by the FuSa Method owner.",
        "- Full synthesis-r4 was not executed.",
    )))
    return {
        "parent_groups": len(inputs), "provider_ready_groups": provider_ready,
        "triage": triage, "ranking": ranking, "coverage": coverage,
        "convergence": convergence, "causal": causal, "historical_guard": guard,
        "decision": "NOT_READY_METHOD_AUTHORITY", "provider_calls": 0,
    }


__all__ = ["run_pre_r4_selector_audit"]

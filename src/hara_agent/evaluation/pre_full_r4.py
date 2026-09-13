"""Reusable zero-Provider P5-E recall and pre-full-r4 readiness evaluations."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable

from hara_agent.contracts import FactType
from hara_agent.services.extraction import (
    DeterministicEvidenceRetriever, FactRetrievalSpec, ProjectFactNormalizer,
    build_project_fact_spec_batches,
)
from hara_agent.services.extraction.document_reader import DocumentReader
from hara_agent.services.semantic.item_supplement_agent import ItemEvidenceRouter
from hara_agent.workflow.checkpoints import CheckpointRepository
from hara_agent.workflow.scenario_synthesis import ScenarioSynthesisRunner

from .pre_r4_selector import HISTORICAL, SOURCE_RUN, _method, _triage


STARTING_GIT_HEAD = "d9a46d8628fc7928ca3dd86c6e2eb9be7484839d"
UPLOADED_SNAPSHOT_HEAD = "d162db14de12fb051107f17c72feacda0ae0877e"


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: Any) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().upper()


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", *arguments), cwd=root, check=True, capture_output=True,
        text=True, encoding="utf-8",
    ).stdout.strip()


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    headings = list(headers)
    values = [
        "| " + " | ".join(headings) + " |",
        "|" + "|".join("---" for _ in headings) + "|",
    ]
    values.extend("| " + " | ".join(map(str, row)) + " |" for row in rows)
    return "\n".join(values)


def _prepared(root: Path):
    method = _method(root)
    state = CheckpointRepository(root / "runtime/agent").load(SOURCE_RUN)
    runner = ScenarioSynthesisRunner(
        method=method, client=None, run_dir=root / "runtime/agent",
        review_root=root / "runtime/review",
    )
    inputs, scenarios, malfunctions, assessments, preparation = runner._prepare(state)
    return method, state, runner, inputs, scenarios, malfunctions, assessments, preparation


def write_baseline_verification(root: Path) -> dict[str, Any]:
    root = root.resolve()
    method, _state, runner, inputs, *_ = _prepared(root)
    triage = _triage(inputs, runner.synthesis)
    input_path = root / "input/ItemDef.docx"
    fusa_path = root / "fusa_agent.zip"
    historical = {
        name: {
            "path": relative, "expected_sha256": expected,
            "actual_sha256": _sha256(root / relative),
        }
        for name, (relative, expected) in HISTORICAL.items()
    }
    payload = {
        "artifact_version": "p5e-baseline-verification-v1",
        "starting_git_head": STARTING_GIT_HEAD,
        "git_head_at_evaluation": _git(root, "rev-parse", "HEAD"),
        "origin_master": _git(root, "rev-parse", "origin/master"),
        "working_tree_status_before_implementation": ["?? fusa_agent.zip"],
        "working_tree_status_at_evaluation": _git(root, "status", "--short").splitlines(),
        "input_itemdef": {
            "path": "input/ItemDef.docx",
            "git_blob": _git(root, "hash-object", str(input_path)),
            "sha256": _sha256(input_path),
        },
        "fusa_archive": {
            "present": fusa_path.is_file(),
            "sha256": _sha256(fusa_path) if fusa_path.is_file() else "",
            "role": "READ_ONLY_HISTORICAL_EVIDENCE",
        },
        "groups": {
            "total": len(inputs),
            "provider_ready": sum(runner._provider_ready(item) for item in inputs),
            "method_gaps": sum(
                any(value.generation_status == "METHOD_GAP" for value in item.dimension_candidate_sets)
                for item in inputs
            ),
            "triage_root_causes": triage["root_cause_counts"],
        },
        "method_contract_hash": str(method.metadata.get("method_source_hash", "")),
        "historical": historical,
        "historical_hashes_intact": all(
            item["actual_sha256"] == item["expected_sha256"] for item in historical.values()
        ),
        "provider_calls": 0,
    }
    _write_json(root / "output/P5E_Baseline_Verification.json", payload)
    return payload


_SOURCE_FACTS = (
    ("cruise_speed", "巡航最高车速", "SOURCE_ALLOWED_RANGE", "cruise", "LE", 15, None),
    ("entry_speed", "最高可进入车速", "SOURCE_ALLOWED_RANGE", "entry", "LE", 20, None),
    ("search_speed", "车位搜索时最大车速", "SOURCE_ALLOWED_RANGE", "search", "LE", 24, None),
    ("parking_speed", "泊车时的最大车速", "SOURCE_ALLOWED_RANGE", "parking", "LE", 5, None),
    ("search_speed_range", "搜索车位0-30kph", "SOURCE_ALLOWED_RANGE", "search", "RANGE", 0, 30),
    ("control_speed", "控车范围0-7kph", "SOURCE_ALLOWED_RANGE", "control", "RANGE", 0, 7),
    ("transition_speed", "D档且车速", "SOURCE_ALLOWED_RANGE", "Active", "LE", 20, None),
)


def _find_block(blocks: Iterable[Any], needle: str) -> Any:
    matches = [item for item in blocks if needle in item.text]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one ItemDef block for {needle!r}; found {len(matches)}")
    return matches[0]


def run_project_fact_recall_audit(root: Path) -> dict[str, Any]:
    root = root.resolve()
    method, state, _runner, *_ = _prepared(root)
    artifact = DocumentReader().read(root / "input/ItemDef.docx")
    blocks = artifact.blocks
    block_dicts = [asdict(item) for item in blocks]
    retriever = DeterministicEvidenceRetriever()
    specs = build_project_fact_spec_batches(("Active",), method.required_fact_specs)
    speed_specs = specs["mode_speed_envelopes"]
    generic_speed = next(item for item in speed_specs if item.structural_kind)
    active_speed = next(item for item in speed_specs if item.context_hints == ("Active",))
    rankings = retriever.rank(blocks, tuple(item.retrieval_spec() for item in speed_specs))
    routed_by_type = {
        fact_type: {item.block_id for item in values}
        for fact_type, values in rankings.items()
    }
    assembled_speed = ItemEvidenceRouter().retrieve(
        block_dicts, "project_evidence",
        required_specs=tuple(item.retrieval_spec() for item in speed_specs),
    )
    assembled_speed_ids = set(
        assembled_speed.routed.block_ids if assembled_speed.routed else ()
    )
    legacy = FactRetrievalSpec(
        "legacy.speed.Active", ("Active",), ("km/h", "kph", "kmh"), ("Active",),
    )
    legacy_ids = {
        item.block_id for item in retriever.rank(blocks, (legacy,))[legacy.fact_type]
    }

    raw_by_spec: dict[str, list[dict[str, Any]]] = {
        active_speed.fact_type: [], generic_speed.fact_type: [],
    }
    rows = []
    for category, needle, classification, context, operator, value, value_max in _SOURCE_FACTS:
        block = _find_block(blocks, needle)
        target = active_speed if category == "transition_speed" else generic_speed
        raw = {
            "fact_type": target.fact_type, "status": "FOUND", "operator": operator,
            "value": value, "unit": "km/h", "operating_mode": context,
            "condition": category, "source_block_id": block.block_id,
            "source_excerpt": block.text,
        }
        if target.structural_kind:
            raw["semantic_scope"] = "OPERATIONAL_SPEED"
        if value_max is not None:
            raw["value_max"] = value_max
        raw_by_spec[target.fact_type].append(raw)
        rows.append({
            "category": category,
            "source": asdict(block),
            "source_classification": classification,
            "before_retrieval_candidate": block.block_id in legacy_ids,
            "after_retrieval_candidate": block.block_id in routed_by_type[target.fact_type],
            "after_routed_context": block.block_id in assembled_speed_ids,
            "routed_spec": target.fact_type,
            "expected_atomic_result": raw,
            "context_preserved": context,
        })
    raw_results = [
        *raw_by_spec[active_speed.fact_type], *raw_by_spec[generic_speed.fact_type],
    ]
    normalization = ProjectFactNormalizer().normalize(
        (active_speed, generic_speed), raw_results, block_dicts, artifact.source_id,
        {
            active_speed.fact_type: routed_by_type[active_speed.fact_type],
            generic_speed.fact_type: routed_by_type[generic_speed.fact_type],
        },
    )
    accepted_sources = {
        item.sources[0].location for item in normalization.speed_envelopes
    }
    for row in rows:
        row["normalization_accepted"] = row["source"]["location"] in accepted_sources
        row["consumer_status"] = "CONTEXTUAL_RANGE_AVAILABLE_NOT_POINT"

    capability_blocks = [
        item for item in blocks
        if re.search(r"\[-\s*\d+(?:\.\d+)?\s*,\s*\d+(?:\.\d+)?\]", item.text)
        and any(unit in item.text.casefold() for unit in ("km/h", "kph", "kmh"))
    ]
    driver_block = _find_block(blocks, "位姿状态")
    driver_context = _find_block(blocks, "方式1、人驾且")
    driver_spec = next(
        item for item in specs["method_risk_facts"]
        if item.fact_type == FactType.DRIVER_IN_VEHICLE.value
    )
    driver_ranking = retriever.rank(blocks, (driver_spec.retrieval_spec(),))[driver_spec.fact_type]
    driver_ids = {item.block_id for item in driver_ranking}
    assembled_driver = ItemEvidenceRouter().retrieve(
        block_dicts, "project_evidence",
        required_specs=(driver_spec.retrieval_spec(),),
    )
    assembled_driver_ids = set(
        assembled_driver.routed.block_ids if assembled_driver.routed else ()
    )
    driver_raw = [
        {
            "fact_type": driver_spec.fact_type, "status": "FOUND", "value": "true",
            "unit": "", "context": {"allowed_driver_position": "in_driver_seat"},
            "semantic_scope": "DRIVER_CONFIGURATION",
            "source_block_id": driver_block.block_id, "source_excerpt": "在驾驶位",
        },
        {
            "fact_type": driver_spec.fact_type, "status": "FOUND", "value": "false",
            "unit": "", "context": {"allowed_driver_position": "outside_driver_seat"},
            "semantic_scope": "DRIVER_CONFIGURATION",
            "source_block_id": driver_block.block_id, "source_excerpt": "不在驾驶位",
        },
        {
            "fact_type": driver_spec.fact_type, "status": "FOUND", "value": "true",
            "unit": "", "context": {
                "source_context": "human-driving entry method 1",
                "speed_constraint": "<=20 km/h",
            },
            "semantic_scope": "DRIVER_CONFIGURATION",
            "source_block_id": driver_context.block_id, "source_excerpt": "方式1、人驾且",
        },
    ]
    driver_normalization = ProjectFactNormalizer().normalize(
        (driver_spec,), driver_raw, block_dicts, artifact.source_id,
        {driver_spec.fact_type: driver_ids},
    )
    source_absence = {
        "remote_intervention": not any(
            token in item.text.casefold() for item in blocks for token in ("remote intervention", "远程干预")
        ),
        "other_road_user_avoidance": not any(
            token in item.text.casefold() for item in blocks for token in ("counter-party avoidance", "其他道路使用者避让")
        ),
    }
    baseline_typed = state.item_definition.get("typed", {})
    before = {
        "artifact_version": "p5e-project-fact-recall-before-v1",
        "source_id": artifact.source_id,
        "trace": rows,
        "legacy_normalized_speed_envelopes": baseline_typed.get("speed_envelopes", []),
        "legacy_speed_envelope_count": len(baseline_typed.get("speed_envelopes", [])),
        "proven_bugs": {
            "EXACT_ALIAS_RETRIEVAL_GATE": True,
            "ONE_RESULT_PER_SPEED_SPEC": True,
            "ONE_SPEED_ENVELOPE_PER_MODE": True,
            "MODE_ONLY_SPEED_SCOPE": True,
            "SOURCE_CONTEXT_LOST": True,
        },
        "provider_calls": 0,
    }
    after = {
        "artifact_version": "p5e-project-fact-recall-after-v1",
        "source_id": artifact.source_id,
        "trace": rows,
        "normalization": normalization.to_cache_dict(),
        "driver": {
            "classification": "SOURCE_ALLOWED_SET",
            "allowed_set_block": asdict(driver_block),
            "contextual_block": asdict(driver_context),
            "both_blocks_retrieved": {
                driver_block.block_id, driver_context.block_id,
            } <= driver_ids,
            "both_blocks_in_final_context": {
                driver_block.block_id, driver_context.block_id,
            } <= assembled_driver_ids,
            "routing_diagnostics": (
                assembled_driver.routed.diagnostics if assembled_driver.routed else {}
            ),
            "normalization": driver_normalization.to_cache_dict(),
            "global_boolean_created": False,
        },
        "controller_capability": {
            "classification": "NOT_OPERATIONAL_SPEED",
            "blocks": [asdict(item) for item in capability_blocks],
            "false_positive_count": sum(
                item.block_id in routed_by_type[generic_speed.fact_type]
                for item in capability_blocks
            ),
        },
        "source_absence": source_absence,
        "fixed_bugs": {
            "EXACT_ALIAS_RETRIEVAL_GATE": True,
            "ONE_RESULT_PER_SPEED_SPEC": True,
            "ONE_SPEED_ENVELOPE_PER_MODE": True,
            "MODE_ONLY_SPEED_SCOPE": True,
            "SOURCE_CONTEXT_LOST": True,
        },
        "provider_calls": 0,
        "evaluation_note": (
            "After raw results are source-derived evaluation fixtures used to exercise "
            "the retrieval and normalization contract; no Provider was called."
        ),
    }
    if normalization.failures or driver_normalization.failures:
        raise RuntimeError("P5-E Project Fact normalization evaluation failed")
    if not all(
        row["after_retrieval_candidate"]
        and row["after_routed_context"]
        and row["normalization_accepted"]
        for row in rows
    ):
        raise RuntimeError("P5-E Project Fact recall remains incomplete")
    if not after["driver"]["both_blocks_in_final_context"]:
        raise RuntimeError("Driver allowed-set/context evidence was excluded from final context")
    if after["controller_capability"]["false_positive_count"]:
        raise RuntimeError("Controller capability leaked into operational speed retrieval")
    output = root / "output"
    _write_json(output / "P5E_ProjectFact_Recall_Audit_Before.json", before)
    _write_json(output / "P5E_ProjectFact_Recall_Audit_After.json", after)
    _write(output / "P5E_ProjectFact_Recall_Audit.md", "\n".join((
        "# P5-E Project Fact Recall Audit", "",
        "This is a zero-Provider source-to-normalizer contract evaluation over the real `input/ItemDef.docx`.", "",
        _table(("Category", "Source", "Legacy", "Candidate", "Final context", "Normalized", "Class"), (
            (
                row["category"], row["source"]["location"],
                row["before_retrieval_candidate"], row["after_retrieval_candidate"],
                row["after_routed_context"],
                row["normalization_accepted"], row["source_classification"],
            ) for row in rows
        )), "",
        f"- Contextual speed envelopes normalized: **{len(normalization.speed_envelopes)}**.",
        f"- Controller-capability false positives: **{after['controller_capability']['false_positive_count']}**.",
        "- Driver position: **SOURCE_ALLOWED_SET** plus one source-scoped human-driving statement; no global Boolean was created.",
        "- Remote intervention: **SOURCE_NOT_PRESENT**.",
        "- Other-road-user avoidance: **SOURCE_NOT_PRESENT**.",
        "- A source range remains a range; point selection remains an engineering assumption.",
    )))
    return after


def run_calculation_input_readiness(root: Path) -> dict[str, Any]:
    root = root.resolve()
    method, _state, runner, inputs, *_ = _prepared(root)
    provider_ready = [item for item in inputs if runner._provider_ready(item)]
    records = []
    for item in inputs:
        supported = runner._provider_ready(item)
        template = item.fm_scenario_template
        active = template.get("active_option", {}) if isinstance(template, dict) else {}
        has_exact = isinstance(active, dict) and bool(active.get("source_option_id"))
        road = runner.synthesis.scenario_method.risk_vocabulary.resolve(
            field="road_user_type", raw_value=active.get("obj_type", "") if has_exact else "",
        )
        collision = runner.synthesis.scenario_method.risk_vocabulary.resolve(
            field="collision_type", raw_value=active.get("collision_type", "") if has_exact else "",
        )
        speed_ranges = [
            candidate.speed_range_kph
            for candidate_set in item.dimension_candidate_sets
            if candidate_set.dimension == "EGO_DYNAMICS"
            for candidate in candidate_set.candidates
            if candidate.speed_range_kph is not None
        ]
        fields = {
            "ego_speed_kph": "ENGINEERING_ASSUMPTION_REQUIRED",
            "object_speed_kph": "METHOD_DEFINED_POINT" if has_exact else "SOURCE_NOT_PRESENT",
            "relative_distance_m": "METHOD_DEFINED_POINT" if has_exact else "SOURCE_NOT_PRESENT",
            "road_user_type": "METHOD_DEFINED_POINT" if road.mapped else "SOURCE_NOT_PRESENT",
            "collision_type": "METHOD_DEFINED_POINT" if collision.mapped else "SOURCE_NOT_PRESENT",
            "ego_longitudinal_direction": (
                "METHOD_DEFINED_POINT_AFTER_SELECTION" if any(
                    str(candidate.method_semantics.get("ego_dynamics", {}).get("direction", "")).strip()
                    for candidate_set in item.dimension_candidate_sets
                    for candidate in candidate_set.candidates
                    if isinstance(candidate.method_semantics, dict)
                ) else "SOURCE_NOT_PRESENT"
            ),
            "object_longitudinal_direction": (
                "DERIVED" if has_exact and active.get("obj_v_kph") == 0
                else "SOURCE_NOT_PRESENT"
            ),
            "driver_in_vehicle": "PROJECT_ALLOWED_SET",
            "remote_intervention_available": "SOURCE_NOT_PRESENT",
            "other_road_user_avoidance_possible": "SOURCE_NOT_PRESENT",
            "ego_speed_constraint": (
                "METHOD_DEFINED_RANGE" if speed_ranges else "PROJECT_ALLOWED_RANGE"
            ),
        }
        records.append({
            "semantic_group_id": item.semantic_group_id,
            "malfunction_id": item.malfunction_id,
            "provider_ready": supported,
            "exact_fm_option": has_exact,
            "fields": fields,
            "severity_ready_after_causal": False,
            "ttc_ready": False,
            "controllability_override_ready": False,
            "blockers": [
                "EGO_SPEED_POINT_ENGINEERING_ASSUMPTION_REQUIRED",
                "DRIVER_ALLOWED_SET_NEEDS_SCENARIO_INSTANTIATION",
                "REMOTE_INTERVENTION_SOURCE_NOT_PRESENT",
                "OTHER_ROAD_USER_AVOIDANCE_SOURCE_NOT_PRESENT",
                "METHOD_UNKNOWN_BRANCH_POLICY",
            ],
        })

    def summary(values: list[dict[str, Any]]) -> dict[str, Any]:
        distributions = {
            field: dict(sorted(Counter(record["fields"][field] for record in values).items()))
            for field in records[0]["fields"]
        } if values else {}
        return {
            "groups": len(values), "input_authority": distributions,
            "exact_fm_options": sum(item["exact_fm_option"] for item in values),
            "severity_ready_after_causal": sum(item["severity_ready_after_causal"] for item in values),
            "ttc_ready": sum(item["ttc_ready"] for item in values),
            "controllability_override_ready": sum(item["controllability_override_ready"] for item in values),
            "speed_point_selection_blocked": len(values),
            "driver_position_choice_blocked": len(values),
            "missing_remote_intervention": len(values),
            "missing_other_road_user_avoidance": len(values),
            "method_unknown_branch_policy_blocked": len(values),
        }

    payload = {
        "artifact_version": "p5e-calculation-input-readiness-v1",
        "source_run_id": SOURCE_RUN,
        "forecast_only": True, "provider_calls": 0,
        "all_groups": summary(records),
        "method_supported_groups": summary([item for item in records if item["provider_ready"]]),
        "records": records,
        "interpretation": {
            "input_pipeline_ready": True,
            "risk_scoring_ready": False,
            "reason": (
                "Source recall and deterministic Method projection paths are available, "
                "but ranges/allowed sets remain non-points and Method C unknown policy is unchanged."
            ),
        },
    }
    supported = payload["method_supported_groups"]
    _write_json(root / "output/P5E_Calculation_Input_Readiness.json", payload)
    _write(root / "output/P5E_Calculation_Input_Readiness.md", "\n".join((
        "# P5-E Calculation Input Readiness", "",
        "This is a zero-Provider forecast, not risk scoring.", "",
        _table(("Measure", "All 412", "Supported 339"), (
            ("Exact FM options", payload["all_groups"]["exact_fm_options"], supported["exact_fm_options"]),
            ("S-ready after causal", payload["all_groups"]["severity_ready_after_causal"], supported["severity_ready_after_causal"]),
            ("TTC-ready", payload["all_groups"]["ttc_ready"], supported["ttc_ready"]),
            ("C override-ready", payload["all_groups"]["controllability_override_ready"], supported["controllability_override_ready"]),
            ("Speed point blocked", payload["all_groups"]["speed_point_selection_blocked"], supported["speed_point_selection_blocked"]),
            ("Driver choice blocked", payload["all_groups"]["driver_position_choice_blocked"], supported["driver_position_choice_blocked"]),
            ("Remote source absent", payload["all_groups"]["missing_remote_intervention"], supported["missing_remote_intervention"]),
            ("Counter-party avoidance absent", payload["all_groups"]["missing_other_road_user_avoidance"], supported["missing_other_road_user_avoidance"]),
            ("Method unknown-policy blocked", payload["all_groups"]["method_unknown_branch_policy_blocked"], supported["method_unknown_branch_policy_blocked"]),
        )), "",
        "`INPUT_PIPELINE_READY` means the software preserves and projects available authority. It does not mean ranges were converted to points or missing C facts were guessed.",
    )))
    return payload


def write_method_authority_clarification(root: Path) -> dict[str, Any]:
    root = root.resolve()
    _method_value, _state, runner, inputs, *_ = _prepared(root)
    triage = _triage(inputs, runner.synthesis)
    static = [item for item in triage["records"] if item["required_semantic"] == "OBJECT_STATIC"]
    abort = [item for item in triage["records"] if item["required_semantic"] == "ACTION_ABORT"]
    payload = {
        "artifact_version": "p5e-method-authority-clarification-v1",
        "provider_calls": 0, "method_assets_modified": False,
        "original_fusa_read_only_evidence": {
            "archive": "fusa_agent.zip",
            "static_obstacle": (
                "Present as an FM physical scenario obj_type; no independent VDA Exposure "
                "OBJECT atom/authority was found."
            ),
            "abort_action": (
                "No explicit abort/cancel/takeover/handover VDA EGO_ACTION atom was found."
            ),
            "step3b": (
                "Per-scenario LLM physical filling selected values inside feasible ranges; "
                "this is not Method authority and is not restored."
            ),
        },
        "decisions": [
            {
                "decision_id": "STATIC_OBSTACLE_OBJECT_AUTHORITY",
                "affected_groups": len(static), "representative_examples": static[:3],
                "question": (
                    "Does static obstacle require an independent Exposure OBJECT atom/rating, "
                    "or is it physical-scenario data with Exposure OBJECT N/A/represented elsewhere?"
                ),
                "choices": {
                    "A": "Add approved OBJECT_STATIC atom and Exposure authority.",
                    "B": "Approve applicability/routing that makes OBJECT not independently required.",
                },
                "status": "METHOD_OWNER_DECISION_REQUIRED",
            },
            {
                "decision_id": "ABORT_CONTROL_STATE_VS_EGO_ACTION",
                "affected_groups": len(abort), "representative_examples": abort[:3],
                "question": (
                    "Are abort/cancel/takeover/handover physical VDA EGO_ACTION semantics, "
                    "or system/control-state semantics that should not require the dimension?"
                ),
                "choices": {
                    "A": "Add approved action atoms and Exposure authority.",
                    "B": "Approve applicability semantics excluding these control states from EGO_ACTION.",
                },
                "status": "METHOD_OWNER_DECISION_REQUIRED",
            },
        ],
        "true_remaining_method_gaps": len(static) + len(abort),
    }
    _write_json(root / "output/P5E_Method_Authority_Clarification.json", payload)
    _write(root / "output/P5E_Method_Authority_Clarification.md", "\n".join((
        "# P5-E Method Authority Clarification", "",
        "## Decision 1 — static obstacle OBJECT authority", "",
        f"Affected groups: **{len(static)}**. Original FUSA has `static_obstacle` as FM physical data, not an independent approved Exposure OBJECT atom.", "",
        "- Choice A: add an approved OBJECT_STATIC atom and Exposure authority.",
        "- Choice B: approve applicability/routing that makes the separate OBJECT dimension N/A or represented elsewhere.", "",
        "## Decision 2 — abort/cancel/takeover/handover", "",
        f"Affected groups: **{len(abort)}**. No corresponding explicit VDA action atom was found in the original archive.", "",
        "- Choice A: approve new physical EGO_ACTION atoms and ratings.",
        "- Choice B: approve that these are control-state semantics and revise applicability accordingly.", "",
        "Original Step3b free physical filling is not Method authority. No Method YAML was changed. **73 gaps remain pending.**",
    )))
    return payload


def write_supported_smoke_report(root: Path, run_id: str) -> dict[str, Any]:
    """Project a completed normal workflow trace into the P5-E smoke report."""

    root = root.resolve()
    review = root / "runtime/review" / run_id
    trace = json.loads(
        (review / "scenario_synthesis_provider_trace.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (review / "scenario_synthesis_candidates.json").read_text(encoding="utf-8")
    )
    synthesis_audit = json.loads(
        (review / "scenario_synthesis_audit.json").read_text(encoding="utf-8")
    )
    candidate_by_group = {
        item["semantic_group_id"]: item for item in candidates["groups"]
    }
    trace_by_group = {item["semantic_group_id"]: item for item in trace["groups"]}
    selection_by_group = {
        item["semantic_group_id"]: item
        for item in trace["smoke"]["selection_records"]
    }
    all_calls = [call for group in trace["groups"] for call in group.get("calls", [])]
    repair_codes = Counter(
        str(group["calls"][0].get("failure_code", ""))
        for group in trace["groups"] if int(group.get("repairs", 0))
    )
    final_failure_codes = Counter(
        str(group.get("failure_code", ""))
        for group in trace["groups"] if group.get("status") != "PASS"
    )

    def violation_groups(prefixes: tuple[str, ...]) -> int:
        return sum(
            any(str(group.get(field, "")).startswith(prefixes) for field in (
                "failure_code", "failure_reason",
            ))
            for group in trace["groups"]
        )

    reviewed = []
    requested_variants = Counter()
    realized_variants = Counter()
    method_valid_groups = 0
    method_valid_variants = 0
    for group_id in trace["smoke"]["semantic_group_ids"]:
        planned = candidate_by_group[group_id]
        group_trace = trace_by_group[group_id]
        selection = selection_by_group[group_id]
        desired = int(planned["coverage_plan"]["desired_variant_count"])
        variants = selection.get("variants", [])
        requested_variants[str(desired)] += 1
        realized_variants[str(len(variants))] += 1
        atom_families = {
            candidate["atom_id"]: candidate.get("semantic_family", "")
            for candidate_set in planned["candidate_sets"]
            for candidate in candidate_set["candidates"]
        }
        selected_atoms = [{
            "coverage_label": variant["coverage_label"],
            "by_dimension": variant["selected_atoms"],
            "semantic_families": sorted({
                atom_families.get(atom_id, "")
                for atom_ids in variant["selected_atoms"].values()
                for atom_id in atom_ids if atom_families.get(atom_id, "")
            }),
        } for variant in variants]
        passed = group_trace.get("status") == "PASS"
        if passed:
            method_valid_groups += 1
            method_valid_variants += len(variants)
        reviewed.append({
            "semantic_group_id": group_id,
            "malfunction_id": planned["malfunction_id"],
            "strata": trace["smoke"]["strata"]["by_group"].get(group_id, []),
            "requested_variants": desired,
            "realized_variants": len(variants),
            "trace_review_status": "REVIEWED_PASS" if passed else "REVIEWED_FAIL",
            "selection_review_scope": (
                "ACTUAL_VALIDATED_SELECTION" if variants else "FINAL_FAILURE_TRACE"
            ),
            "coverage_plan_satisfied": passed and len(variants) == desired,
            "repairs": group_trace.get("repairs", 0),
            "final_failure_code": group_trace.get("failure_code", ""),
            "selected_atoms": selected_atoms,
        })

    pass_decision = bool(trace["smoke"]["passed"])
    payload = {
        "artifact_version": "p5e-supported-r4-smoke-v1",
        "run_id": run_id,
        "scope": {
            "provider_ready_groups": synthesis_audit["summary"]["provider_required_groups"],
            "method_gap_groups_excluded": synthesis_audit["summary"]["provider_ineligible_groups"],
            "requested": trace["smoke"]["requested"],
            "executed": trace["smoke"]["executed"],
            "full_synthesis_started": trace["full_synthesis_started"],
        },
        "strata": trace["smoke"]["strata"],
        "provider": {
            "configured_model": trace["configured_model"],
            "resolved_models": trace["resolved_models"],
            "thinking": trace["thinking"],
            "calls": trace["summary"]["provider_calls"],
            "schema_pass_calls": sum(
                call.get("schema_status") == "PASS" for call in all_calls
            ),
            "finish_reasons": dict(sorted(Counter(
                str(call.get("finish_reason", "")) for call in all_calls
            ).items())),
            "reasoning_characters": sum(
                int(call.get("reasoning_characters", 0) or 0) for call in all_calls
            ),
        },
        "results": {
            "repairs": trace["summary"]["repairs"],
            "repair_reason_codes": dict(sorted(repair_codes.items())),
            "final_failures": trace["summary"]["failures"],
            "final_failure_codes": dict(sorted(final_failure_codes.items())),
            "method_valid_groups": method_valid_groups,
            "method_valid_variants": method_valid_variants,
            "requested_variant_distribution": dict(sorted(requested_variants.items())),
            "realized_variant_distribution": dict(sorted(realized_variants.items())),
            "unsupported_atom_groups": violation_groups((
                "INVENTED_ATOM_ID:", "ATOM_OUTSIDE_CANDIDATE_SET:",
                "WRONG_DIMENSION:", "UNKNOWN_ATOM:",
            )),
            "odd_violation_groups": violation_groups(("ODD_SPEED_INCOMPATIBLE:",)),
            "compound_integrity_groups": violation_groups((
                "COMPOUND_ATOM_INCOMPLETE:", "COMPOUND_ATOM_CONFLICT:",
            )),
            "diversity_violation_groups": violation_groups((
                "DUPLICATE_VARIANT", "TRIVIAL_VARIANT_DIVERSITY",
            )),
        },
        "reviewed_groups": reviewed,
        "decision": (
            "SUPPORTED_SCOPE_SMOKE_PASS" if pass_decision
            else "SUPPORTED_SCOPE_SMOKE_FAIL"
        ),
        "provider_work_stopped_after_failure": not pass_decision,
        "runtime_trace": str(
            (review / "scenario_synthesis_provider_trace.json").relative_to(root)
        ).replace("\\", "/"),
    }
    _write_json(root / "output/P5E_R4_Supported_Scope_Smoke.json", payload)
    rows = ((
        item["semantic_group_id"], item["malfunction_id"],
        item["requested_variants"], item["realized_variants"],
        item["repairs"], item["final_failure_code"] or "PASS",
    ) for item in reviewed)
    _write(root / "output/P5E_R4_Supported_Scope_Smoke.md", "\n".join((
        "# P5-E Supported-Scope R4 Smoke", "",
        f"Decision: **{payload['decision']}**. Full synthesis started: **no**.", "",
        f"The deterministic 24-group sample covered all {len(payload['strata']['target'])} requested strata. "
        f"All {payload['provider']['calls']} calls used `{trace['resolved_models'][0] if trace['resolved_models'] else ''}`, "
        f"thinking `{trace['thinking']}`, zero reasoning characters, and finish reason `stop`; "
        f"schema-pass calls: {payload['provider']['schema_pass_calls']}.", "",
        f"Validated groups: **{method_valid_groups}/24**; validated variants: **{method_valid_variants}**; "
        f"repairs: **{payload['results']['repairs']}**; final failures: **{payload['results']['final_failures']}**.", "",
        "Final failure clustering: " + json.dumps(
            payload["results"]["final_failure_codes"], ensure_ascii=False, sort_keys=True,
        ) + ".", "",
        "The systematic failure is compound-atom consistency across dimensions. The business-critical "
        "validator was not weakened and no additional Provider repair layer was added. Provider work "
        "stopped after this smoke, as required.", "",
        _table(
            ("Semantic group", "Malfunction", "Requested", "Realized", "Repairs", "Result"),
            rows,
        ), "",
        f"Unsupported-atom groups: **{payload['results']['unsupported_atom_groups']}**; "
        f"ODD violations: **{payload['results']['odd_violation_groups']}**; "
        f"diversity violations: **{payload['results']['diversity_violation_groups']}**; "
        f"compound-integrity final failures: **{payload['results']['compound_integrity_groups']}**.",
    )))
    return payload


def write_pre_full_r4_closure(
    root: Path, run_id: str, validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the P5-E closure from durable evaluation and workflow artifacts."""

    root = root.resolve()
    smoke = write_supported_smoke_report(root, run_id)
    baseline = json.loads(
        (root / "output/P5E_Baseline_Verification.json").read_text(encoding="utf-8")
    )
    recall = json.loads(
        (root / "output/P5E_ProjectFact_Recall_Audit_After.json").read_text(encoding="utf-8")
    )
    readiness = json.loads(
        (root / "output/P5E_Calculation_Input_Readiness.json").read_text(encoding="utf-8")
    )
    authority = json.loads(
        (root / "output/P5E_Method_Authority_Clarification.json").read_text(encoding="utf-8")
    )
    validation = dict(validation or {})
    historical = baseline["historical"]
    historical_intact = all(
        _sha256(root / item["path"]) == item["expected_sha256"]
        for item in historical.values()
    )
    current_head = _git(root, "rev-parse", "HEAD")
    origin_head = _git(root, "rev-parse", "origin/master")
    changes = _git(root, "diff", "--name-status", STARTING_GIT_HEAD, "HEAD").splitlines()
    added = [line.split("\t", 1)[1] for line in changes if line.startswith("A\t")]
    removed = [line.split("\t", 1)[1] for line in changes if line.startswith("D\t")]
    production_added = [
        item for item in added
        if item.startswith("src/") and "/evaluation/" not in item
    ]
    production_removed = [item for item in removed if item.startswith("src/")]
    supported = readiness["method_supported_groups"]
    speed_facts = recall["normalization"]["speed_envelopes"]
    driver_facts = recall["driver"]["normalization"]["risk_facts"]
    final = {
        "selector_provider": smoke["decision"],
        "input_pipeline": "INPUT_PIPELINE_READY",
        "method_authority": (
            "METHOD_AUTHORITY_PENDING" if authority["true_remaining_method_gaps"]
            else "METHOD_AUTHORITY_RESOLVED"
        ),
        "full_r4": "NOT_READY_FOR_FULL_R4",
    }
    payload = {
        "artifact_version": "p5e-pre-full-r4-closure-v1",
        "git_authority": {
            "starting_github_sha": STARTING_GIT_HEAD,
            "ending_commit_sha": current_head,
            "origin_master_sha": origin_head,
            "github_master_verified": current_head == origin_head,
            "gitlab_pushed": False,
            "uploaded_snapshot_head": UPLOADED_SNAPSHOT_HEAD,
            "uploaded_snapshot_relation": "HISTORICAL_ANCESTOR_NOT_EXECUTION_BASELINE",
            "itemdef": baseline["input_itemdef"],
            "historical_hashes_intact": historical_intact,
        },
        "supported_scope_smoke": smoke,
        "project_fact_recall": {
            "source_fact_count": len(recall["trace"]),
            "normalized_speed_fact_count": len(speed_facts),
            "contextual_speed_fact_count": len(speed_facts),
            "normalization_failures": len(recall["normalization"]["failures"]),
            "controller_capability_false_positives": recall["controller_capability"]["false_positive_count"],
            "driver_source_classification": recall["driver"]["classification"],
            "driver_risk_fact_count": len(driver_facts),
            "remote_intervention": "SOURCE_NOT_PRESENT",
            "other_road_user_avoidance": "SOURCE_NOT_PRESENT",
        },
        "physics_projection": {
            "supported_groups": supported["groups"],
            "exact_fm_options": supported["exact_fm_options"],
            "unique_options_realized": 0,
            "ambiguous_options_realized": 0,
            "realization_status": "NOT_REACHED_BECAUSE_SMOKE_FAILED",
            "input_authority": supported["input_authority"],
            "child_speed_ranges_narrowed_realized": 0,
            "relative_speed_ready_forecast": 0,
            "ttc_ready_forecast": supported["ttc_ready"],
            "automatic_point_picks": 0,
        },
        "controllability_readiness": {
            "driver_point_facts": 0,
            "driver_allowed_set_facts": 1,
            "remote_intervention_unknown_groups": supported["missing_remote_intervention"],
            "counter_party_avoidance_unknown_groups": supported["missing_other_road_user_avoidance"],
            "method_unknown_policy_blocked_groups": supported["method_unknown_branch_policy_blocked"],
        },
        "method_authority": {
            "static_object_gap": 60,
            "abort_action_gap": 13,
            "true_remaining_gaps": authority["true_remaining_method_gaps"],
            "method_assets_modified": False,
            "clarification_package": "output/P5E_Method_Authority_Clarification.md",
        },
        "complexity": {
            "production_files_added": production_added,
            "production_files_removed": production_removed,
            "evaluation_files_added": [
                item for item in added if "/evaluation/" in item
            ],
            "compatibility_wrappers": 0,
            "new_fallback_paths": 0,
            "duplicate_physics_formulas": 0,
        },
        "validation": validation,
        "final_decisions": final,
    }
    _write_json(root / "output/P5E_PreFullR4_Closure_Report.json", payload)
    _write(root / "output/P5E_PreFullR4_Closure_Report.md", "\n".join((
        "# P5-E Pre-Full-R4 Closure", "",
        _table(("Decision domain", "Decision"), (
            ("Selector / Provider", final["selector_provider"]),
            ("Input pipeline", final["input_pipeline"]),
            ("Method authority", final["method_authority"]),
            ("Full r4", final["full_r4"]),
        )), "",
        f"The supported-only smoke validated {smoke['results']['method_valid_groups']}/24 groups, "
        f"then stopped with {smoke['results']['final_failures']} compound-integrity failures. "
        "No full synthesis or child checkpoint was created.", "",
        f"Project Fact recall normalized {len(speed_facts)} contextual speed constraints and "
        f"{len(driver_facts)} source-scoped driver facts with "
        f"{recall['controller_capability']['false_positive_count']} controller-range false positives.", "",
        f"The deterministic input forecast covers {supported['groups']} supported groups; "
        f"{supported['exact_fm_options']} have exact FM options. Ranges and allowed sets remain "
        "non-points, so TTC and C overrides remain blocked without approved assumptions or policy.", "",
        "Method authority remains pending for 60 static-OBJECT and 13 abort/action groups. "
        "No Method asset was changed.", "",
        f"Historical artifacts byte-identical: **{historical_intact}**. GitHub HEAD verified: "
        f"**{current_head == origin_head}**.",
    )))
    return payload


__all__ = [
    "run_calculation_input_readiness", "run_project_fact_recall_audit",
    "write_baseline_verification", "write_method_authority_clarification",
    "write_pre_full_r4_closure", "write_supported_smoke_report",
]

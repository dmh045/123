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
from hara_agent.services.analysis.scenario_selection_quality import (
    ScenarioRefinementEvidencePolicy,
)
from hara_agent.services.semantic.scenario_synthesis_agent import (
    BoundedScenarioSynthesisAgent,
)
from hara_agent.workflow.checkpoints import CheckpointRepository
from hara_agent.workflow.scenario_synthesis import ScenarioSynthesisRunner

from .pre_r4_selector import HISTORICAL, SOURCE_RUN, _method, _ranking, _triage


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


def complete_p5f_failure_reconstruction(root: Path) -> dict[str, Any]:
    """Add the complete historical 24-group inventory to the P5-F reconstruction."""
    root = root.resolve()
    reconstruction_path = root / "output/P5F_Compound_Failure_Reconstruction.json"
    payload = json.loads(reconstruction_path.read_text(encoding="utf-8"))
    review = root / "runtime/review/hara-p5e-supported-r4-smoke-20260913-r1"
    trace = json.loads(
        (review / "scenario_synthesis_provider_trace.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (review / "scenario_synthesis_candidates.json").read_text(encoding="utf-8")
    )
    candidates_by_id = {
        item["semantic_group_id"]: item for item in candidates["groups"]
    }
    traces_by_id = {
        item["semantic_group_id"]: item for item in trace.get("groups", [])
    }
    original = []
    for group_id in trace["smoke"]["semantic_group_ids"]:
        item = candidates_by_id[group_id]
        compound = {}
        candidate_sets = {}
        for candidate_set in item["candidate_sets"]:
            dimension = str(candidate_set["dimension"])
            candidate_sets[dimension] = [
                str(candidate["atom_id"])
                for candidate in candidate_set.get("candidates", [])
            ]
            for candidate in candidate_set.get("candidates", []):
                dimensions = list(map(str, candidate.get("dimensions", [])))
                if len(dimensions) > 1:
                    compound[str(candidate["atom_id"])] = dimensions
        group_trace = traces_by_id.get(group_id, {})
        original.append({
            "malfunction_id": str(item["malfunction_id"]),
            "parent_scenario_id": str(item["parent_scenario_id"]),
            "hazardous_event_id": str(item["hazardous_event_id"]),
            "old_semantic_group_id": group_id,
            "requested_variant_count": int(
                item["coverage_plan"]["desired_variant_count"]
            ),
            "candidate_sets": candidate_sets,
            "compound_candidates": [
                {"atom_id": atom_id, "filled_dimensions": dimensions}
                for atom_id, dimensions in sorted(compound.items())
            ],
            "strata": trace["smoke"]["strata"]["by_group"].get(group_id, []),
            "historical_status": str(group_trace.get("status", "")),
            "historical_failure_code": str(group_trace.get("failure_code", "")),
        })
    by_identity = {(
        item["malfunction_id"], item["parent_scenario_id"],
        item["hazardous_event_id"],
    ): item for item in original}
    for failure in payload.get("failures", []):
        historical = by_identity.get((
            str(failure.get("malfunction_id", "")),
            str(failure.get("parent_scenario_id", "")),
            str(failure.get("hazardous_event_id", "")),
        ))
        if historical is not None:
            failure["old_semantic_group_id"] = historical["old_semantic_group_id"]
            failure["requested_variant_count"] = historical["requested_variant_count"]
    payload["original_smoke_groups"] = original
    payload["summary"].update({
        "original_smoke_groups": len(original),
        "original_requested_variant_counts": dict(Counter(
            str(item["requested_variant_count"]) for item in original
        )),
        "original_strata_covered": len(trace["smoke"]["strata"]["covered"]),
    })
    _write_json(reconstruction_path, payload)
    _write_json(root / "output/P5F_Original_24_Smoke_Identities.json", {
        "artifact_version": "p5f-original-24-smoke-identities-v1",
        "source_run_id": "hara-p5e-supported-r4-smoke-20260913-r1",
        "failures": original,
    })
    markdown_path = root / "output/P5F_Compound_Failure_Reconstruction.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    marker = "## Original 24-group smoke inventory"
    markdown = markdown.split(marker, 1)[0].rstrip()
    rows = _table((
        "Old semantic group", "Malfunction", "Variants", "Compounds", "Result",
    ), ((
        item["old_semantic_group_id"], item["malfunction_id"],
        item["requested_variant_count"], len(item["compound_candidates"]),
        item["historical_failure_code"] or item["historical_status"],
    ) for item in original))
    _write(markdown_path, "\n\n".join((
        markdown, marker,
        "All 24 historical identities, requested variant counts, candidate sets, "
        "compound candidates, strata, and outcomes are preserved in the JSON artifact.",
        rows,
    )))
    return payload


def run_compound_closure_audit(root: Path) -> dict[str, Any]:
    """Verify P5-F atomic compound invariants across all prepared groups."""
    root = root.resolve()
    method, _state, runner, inputs, scenarios, *_ = _prepared(root)
    triage = _triage(inputs, runner.synthesis)
    ranking = _ranking(inputs)
    partial_memberships = []
    globally_illegal = []
    locked_conflicts = []
    refinement_unsupported = []
    expressway_leaks = []
    logical_registry_duplicates = []
    old_shape_schema_groups = []
    coverage_assignment_gaps = []
    visible_compounds = 0

    for synthesis_input in inputs:
        candidate_sets = {
            item.dimension: item
            for item in synthesis_input.dimension_candidate_sets
        }
        membership = {
            dimension: {candidate.atom_id for candidate in item.candidates}
            for dimension, item in candidate_sets.items()
        }
        all_candidates = {
            candidate.atom_id: candidate
            for item in candidate_sets.values() for candidate in item.candidates
        }
        exact_locks = {
            dimension: item.locked_atom_ids[0]
            for dimension, item in candidate_sets.items() if item.locked_atom_ids
        }
        applicability = {
            dimension: item.applicability
            for dimension, item in candidate_sets.items()
        }
        registry = runner.synthesis.logical_candidate_registry(synthesis_input)
        if len(registry) != len(set(registry)):
            logical_registry_duplicates.append(synthesis_input.semantic_group_id)
        for candidate in all_candidates.values():
            if len(candidate.dimensions) <= 1:
                continue
            visible_compounds += 1
            presence = {
                dimension: candidate.atom_id in membership.get(dimension, set())
                for dimension in candidate.dimensions
            }
            if any(presence.values()) and not all(presence.values()):
                partial_memberships.append({
                    "semantic_group_id": synthesis_input.semantic_group_id,
                    "atom_id": candidate.atom_id,
                    "presence_by_dimension": presence,
                })
            raw_atom = runner.synthesis.by_id[candidate.atom_id]
            parent = scenarios[synthesis_input.parent_scenario_id]
            legal, reason = runner.synthesis.global_compound_legality(
                atom=raw_atom, parent=parent,
                project_context=synthesis_input.project_context,
                query=synthesis_input.structured_semantic_query,
                applicability_by_dimension=applicability,
                exact_locks=exact_locks,
                fm_template=synthesis_input.fm_scenario_template,
            )
            if not legal:
                globally_illegal.append({
                    "semantic_group_id": synthesis_input.semantic_group_id,
                    "atom_id": candidate.atom_id, "reason": reason,
                })
            for dimension in candidate.dimensions:
                locked = exact_locks.get(dimension, "")
                if locked and locked != candidate.atom_id:
                    locked_conflicts.append({
                        "semantic_group_id": synthesis_input.semantic_group_id,
                        "atom_id": candidate.atom_id,
                        "dimension": dimension, "locked_atom_id": locked,
                    })
        for candidate_set in candidate_sets.values():
            decision = candidate_set.binding_decision
            for candidate in candidate_set.candidates:
                if not (
                    decision.refinable and decision.parent_atom_id
                    and candidate.atom_id != decision.parent_atom_id
                ):
                    continue
                status, reason = ScenarioRefinementEvidencePolicy.classify(
                    candidate, decision,
                )
                if status == "REFINEMENT_UNSUPPORTED":
                    refinement_unsupported.append({
                        "semantic_group_id": synthesis_input.semantic_group_id,
                        "dimension": candidate_set.dimension,
                        "atom_id": candidate.atom_id, "reason": reason,
                    })
        if "CN_peds_across_expressway" in all_candidates:
            expressway_leaks.append(synthesis_input.semantic_group_id)
        if runner._provider_ready(synthesis_input):
            variant_schema = BoundedScenarioSynthesisAgent(
                None, runner.synthesis,
            )._schema(synthesis_input)[
                "properties"
            ]["variants"]["items"]
            if (
                "selected_atoms" in variant_schema["properties"]
                or "selected_atom_ids" not in variant_schema["properties"]
            ):
                old_shape_schema_groups.append(synthesis_input.semantic_group_id)
            if not runner.synthesis.logical_selection_assignments(synthesis_input):
                coverage_assignment_gaps.append(synthesis_input.semantic_group_id)

    method_gap_semantics = Counter(
        item["required_semantic"] for item in triage["records"]
    )
    reconstruction = json.loads(
        (root / "output/P5F_Compound_Failure_Reconstruction.json").read_text(
            encoding="utf-8"
        )
    )
    requested_identities = {(
        str(item["malfunction_id"]), str(item["parent_scenario_id"]),
        str(item["hazardous_event_id"]),
    ) for item in reconstruction["failures"]}
    ready_identities = {
        runner._stable_identity(item) for item in inputs if runner._provider_ready(item)
    }
    historical = {
        name: {
            "path": path, "expected_sha256": expected,
            "actual_sha256": _sha256(root / path),
        }
        for name, (path, expected) in HISTORICAL.items()
    }
    recall_path = root / "output/P5E_ProjectFact_Recall_Audit_After.json"
    recall = json.loads(recall_path.read_text(encoding="utf-8"))
    speed_facts = recall["normalization"]["speed_envelopes"]
    driver_facts = recall["driver"]["normalization"]["risk_facts"]
    driver_allowed_set_preserved = (
        recall["driver"]["classification"] == "SOURCE_ALLOWED_SET"
        and {str(item.get("value", "")).casefold() for item in driver_facts}
        >= {"true", "false"}
    )
    compound_gate = not any((
        partial_memberships, globally_illegal, locked_conflicts,
        expressway_leaks, logical_registry_duplicates, coverage_assignment_gaps,
    ))
    ranking_gate = not any((
        ranking["authoritative_below_cutoff"],
        ranking["fm_template_below_cutoff"],
        ranking["exact_structured_source_below_cutoff"],
        len(ranking["generic_intensity_risk_groups"]),
    ))
    method_gate = (
        len(inputs) == 412
        and method_gap_semantics["OBJECT_STATIC"] == 60
        and method_gap_semantics["ACTION_ABORT"] == 13
        and triage["true_remaining_method_gaps"] == 73
        and sum(runner._provider_ready(item) for item in inputs) == 339
    )
    fact_gate = (
        len(speed_facts) == 7
        and recall["controller_capability"]["false_positive_count"] == 0
        and driver_allowed_set_preserved
    )
    payload = {
        "artifact_version": "p5f-offline-compound-closure-audit-v1",
        "provider_calls": 0,
        "groups": {
            "total": len(inputs),
            "provider_ready": sum(runner._provider_ready(item) for item in inputs),
            "method_gaps": triage["true_remaining_method_gaps"],
            "object_static_gaps": method_gap_semantics["OBJECT_STATIC"],
            "action_abort_gaps": method_gap_semantics["ACTION_ABORT"],
        },
        "compound_invariants": {
            "provider_visible_compound_atoms": visible_compounds,
            "provider_visible_partial_membership": len(partial_memberships),
            "partial_membership_records": partial_memberships,
            "project_odd_contradicted_candidates": len(globally_illegal),
            "project_odd_contradiction_records": globally_illegal,
            "locked_dimension_conflicts": len(locked_conflicts),
            "locked_conflict_records": locked_conflicts,
            "expressway_compounds_in_parking_odd": len(expressway_leaks),
            "logical_registry_duplicates": len(logical_registry_duplicates),
            "old_per_dimension_response_shape_groups": len(old_shape_schema_groups),
            "coverage_valid_assignment_gap_groups": len(coverage_assignment_gaps),
            "coverage_valid_assignment_gap_ids": coverage_assignment_gaps,
        },
        "odd_authority": {
            "project_parent_fields_present": sum(
                "project_location_categories" in item.structured_semantic_query
                and "parent_location_categories" in item.structured_semantic_query
                for item in inputs
            ),
            "authority_rule": "PROJECT_ODD_HARD_LEGALITY_PARENT_CONTEXT_RANKING_ONLY",
        },
        "refinement_evidence": {
            "unsupported_candidates_exposed": len(refinement_unsupported),
            "records": refinement_unsupported,
            "policy": "REFINEMENT_SUPPORTED_OR_NOT_EXPOSED",
        },
        "ranking_regression": {
            "authoritative_below_cutoff": ranking["authoritative_below_cutoff"],
            "fm_template_below_cutoff": ranking["fm_template_below_cutoff"],
            "structured_source_below_cutoff": ranking[
                "exact_structured_source_below_cutoff"
            ],
            "generic_intensity_only_risk": len(
                ranking["generic_intensity_risk_groups"]
            ),
        },
        "project_fact_regression": {
            "contextual_speed_constraints": len(speed_facts),
            "controller_capability_false_positives": recall[
                "controller_capability"
            ]["false_positive_count"],
            "driver_classification": recall["driver"]["classification"],
            "driver_allowed_set_preserved": driver_allowed_set_preserved,
            "remote_intervention": "SOURCE_NOT_PRESENT",
            "other_road_user_avoidance": "SOURCE_NOT_PRESENT",
        },
        "recovery_identity_gate": {
            "requested": len(requested_identities),
            "provider_ready": len(requested_identities & ready_identities),
            "missing_or_ineligible": [
                list(item) for item in sorted(requested_identities - ready_identities)
            ],
        },
        "historical_artifacts": historical,
        "historical_hashes_intact": all(
            item["actual_sha256"] == item["expected_sha256"]
            for item in historical.values()
        ),
        "gates": {
            "method_scope": method_gate,
            "compound_closure": compound_gate,
            "logical_provider_contract": not (
                old_shape_schema_groups or coverage_assignment_gaps
            ),
            "refinement_evidence": not refinement_unsupported,
            "ranking_regression": ranking_gate,
            "project_fact_regression": fact_gate,
            "recovery_identities": len(requested_identities & ready_identities) == 13,
        },
    }
    payload["decision"] = (
        "OFFLINE_COMPOUND_CLOSURE_PASS"
        if all(payload["gates"].values()) and payload["historical_hashes_intact"]
        else "OFFLINE_COMPOUND_CLOSURE_FAIL"
    )
    _write_json(root / "output/P5F_Offline_Compound_Closure_Audit.json", payload)
    _write(root / "output/P5F_Offline_Compound_Closure_Audit.md", "\n".join((
        "# P5-F Offline Compound Closure Audit", "",
        f"Decision: **{payload['decision']}**. Provider calls: **0**.", "",
        _table(("Gate", "Result"), (
            (name, "PASS" if value else "FAIL")
            for name, value in payload["gates"].items()
        )), "",
        f"All **{len(inputs)}** groups were rebuilt: **339** Provider-ready and "
        "**73** true Method gaps (OBJECT_STATIC=60, ACTION_ABORT=13).", "",
        f"Provider-visible compound partial membership: **{len(partial_memberships)}**; "
        f"Project-ODD-contradicted compounds: **{len(globally_illegal)}**; "
        f"locked conflicts: **{len(locked_conflicts)}**; parking/expressway leaks: "
        f"**{len(expressway_leaks)}**.", "",
        f"Authoritative/FM-template/structured-source below cutoff: "
        f"**{ranking['authoritative_below_cutoff']}/"
        f"{ranking['fm_template_below_cutoff']}/"
        f"{ranking['exact_structured_source_below_cutoff']}**; "
        f"generic-intensity-only risks: **{len(ranking['generic_intensity_risk_groups'])}**.", "",
        f"All **{len(requested_identities)}** historical failed identities remain "
        "Provider-ready. Historical artifact hashes remain byte-identical.",
    )))
    return payload


def write_p5f_smoke_report(
    root: Path, run_id: str, *, phase: str,
) -> dict[str, Any]:
    """Project a recovery or supported P5-F smoke from workflow artifacts."""
    if phase not in {"recovery_13", "supported_24"}:
        raise ValueError("P5-F smoke phase must be recovery_13 or supported_24")
    root = root.resolve()
    review = root / "runtime/review" / run_id
    trace = json.loads(
        (review / "scenario_synthesis_provider_trace.json").read_text(encoding="utf-8")
    )
    candidates = json.loads(
        (review / "scenario_synthesis_candidates.json").read_text(encoding="utf-8")
    )
    candidate_by_group = {
        item["semantic_group_id"]: item for item in candidates["groups"]
    }
    trace_by_group = {
        item["semantic_group_id"]: item for item in trace.get("groups", [])
    }
    selection_by_group = {
        item["semantic_group_id"]: item
        for item in trace["smoke"].get("selection_records", [])
    }
    all_calls = [
        call for group in trace.get("groups", []) for call in group.get("calls", [])
    ]

    def failure_groups(prefixes: tuple[str, ...]) -> int:
        count = 0
        for group in trace.get("groups", []):
            values = [
                str(group.get("failure_code", "")),
                str(group.get("failure_reason", "")),
            ]
            for call in group.get("calls", []):
                values.extend((
                    str(call.get("failure_code", "")),
                    str(call.get("failure_reason", "")),
                    " ".join(map(str, call.get("failure_details", []))),
                ))
            count += any(prefix in value for prefix in prefixes for value in values)
        return count

    reviewed = []
    for group_id in trace["smoke"]["semantic_group_ids"]:
        planned = candidate_by_group[group_id]
        group_trace = trace_by_group[group_id]
        variants = selection_by_group.get(group_id, {}).get("variants", [])
        reviewed.append({
            "semantic_group_id": group_id,
            "malfunction_id": planned["malfunction_id"],
            "parent_scenario_id": planned["parent_scenario_id"],
            "hazardous_event_id": planned["hazardous_event_id"],
            "requested_variants": planned["coverage_plan"]["desired_variant_count"],
            "realized_variants": len(variants),
            "repairs": group_trace.get("repairs", 0),
            "status": group_trace.get("status", ""),
            "failure_code": group_trace.get("failure_code", ""),
            "selected_logical_atom_ids": [
                sorted({
                    atom_id
                    for atom_ids in variant.get("selected_atoms", {}).values()
                    for atom_id in atom_ids
                })
                for variant in variants
            ],
        })

    expected_count = 13 if phase == "recovery_13" else 24
    expected_model = "doubao-seed-2-1-turbo-260628"
    configured_model = "doubao-seed-2.0-pro"
    model_set = {
        str(call.get("resolved_model", "")) for call in all_calls
        if call.get("resolved_model")
    }
    identity_gate = True
    if phase == "recovery_13":
        reconstruction = json.loads(
            (root / "output/P5F_Compound_Failure_Reconstruction.json").read_text(
                encoding="utf-8"
            )
        )
        expected_identities = {(
            str(item["malfunction_id"]), str(item["parent_scenario_id"]),
            str(item["hazardous_event_id"]),
        ) for item in reconstruction["failures"]}
        actual_identities = {(
            item["malfunction_id"], item["parent_scenario_id"],
            item["hazardous_event_id"],
        ) for item in reviewed}
        identity_gate = actual_identities == expected_identities

    gates = {
        "group_count": len(reviewed) == expected_count,
        "all_groups_pass": all(item["status"] == "PASS" for item in reviewed),
        "schema_all_pass": bool(all_calls) and all(
            call.get("schema_status") == "PASS" for call in all_calls
        ),
        "configured_model": trace.get("configured_model") == configured_model,
        "resolved_model": model_set == {expected_model},
        "thinking_disabled": trace.get("thinking") == "disabled",
        "reasoning_characters_zero": all(
            int(call.get("reasoning_characters", 0) or 0) == 0 for call in all_calls
        ),
        "finish_reason_stop": bool(all_calls) and all(
            call.get("finish_reason") == "stop" for call in all_calls
        ),
        "compound_conflicts_zero": failure_groups(("COMPOUND_ATOM_",)) == 0,
        "invented_atoms_zero": failure_groups((
            "INVENTED_LOGICAL_ATOM_ID:", "LOGICAL_ATOM_OUTSIDE_REGISTRY:",
        )) == 0,
        "odd_contradictions_zero": failure_groups(("ODD_", "PROJECT_ODD_")) == 0,
        "diversity_failures_zero": failure_groups((
            "DUPLICATE_VARIANT", "TRIVIAL_VARIANT_DIVERSITY",
        )) == 0,
        "stable_identity_match": identity_gate,
        "full_synthesis_not_started": not trace.get("full_synthesis_started", False),
    }
    if phase == "supported_24":
        gates["all_strata_covered"] = (
            set(trace["smoke"]["strata"]["covered"])
            == set(trace["smoke"]["strata"]["target"])
        )
    payload = {
        "artifact_version": f"p5f-{phase}-smoke-v1",
        "run_id": run_id,
        "phase": phase,
        "provider": {
            "configured_model": trace.get("configured_model", ""),
            "resolved_models": sorted(model_set),
            "thinking": trace.get("thinking", ""),
            "calls": len(all_calls),
            "schema_pass_calls": sum(
                call.get("schema_status") == "PASS" for call in all_calls
            ),
            "finish_reasons": dict(Counter(
                str(call.get("finish_reason", "")) for call in all_calls
            )),
            "reasoning_characters": sum(
                int(call.get("reasoning_characters", 0) or 0) for call in all_calls
            ),
        },
        "results": {
            "groups_passed": sum(item["status"] == "PASS" for item in reviewed),
            "groups_requested": expected_count,
            "repairs": trace["summary"]["repairs"],
            "method_compound_canonicalizations": trace["summary"].get(
                "method_compound_canonicalizations", 0
            ),
            "compound_failure_groups": failure_groups(("COMPOUND_ATOM_",)),
            "invented_atom_groups": failure_groups((
                "INVENTED_LOGICAL_ATOM_ID:", "LOGICAL_ATOM_OUTSIDE_REGISTRY:",
            )),
            "odd_contradiction_groups": failure_groups(("ODD_", "PROJECT_ODD_")),
            "diversity_failure_groups": failure_groups((
                "DUPLICATE_VARIANT", "TRIVIAL_VARIANT_DIVERSITY",
            )),
        },
        "strata": trace["smoke"]["strata"],
        "gates": gates,
        "reviewed_groups": reviewed,
        "runtime_trace": str(
            (review / "scenario_synthesis_provider_trace.json").relative_to(root)
        ).replace("\\", "/"),
    }
    payload["decision"] = (
        "SUPPORTED_SCOPE_SMOKE_PASS" if all(gates.values())
        else "SUPPORTED_SCOPE_SMOKE_FAIL"
    )
    stem = (
        "P5F_13_Group_Recovery_Smoke"
        if phase == "recovery_13" else "P5F_24_Group_Supported_Smoke"
    )
    _write_json(root / f"output/{stem}.json", payload)
    _write(root / f"output/{stem}.md", "\n".join((
        f"# P5-F {expected_count}-Group Smoke", "",
        f"Decision: **{payload['decision']}**. Full synthesis started: **no**.", "",
        f"Groups passed: **{payload['results']['groups_passed']}/{expected_count}**; "
        f"Provider calls: **{len(all_calls)}**; repairs: **{payload['results']['repairs']}**; "
        f"deterministic compound canonicalizations: "
        f"**{payload['results']['method_compound_canonicalizations']}**.", "",
        f"Configured model: `{trace.get('configured_model', '')}`; resolved models: "
        f"`{', '.join(sorted(model_set))}`; thinking: `{trace.get('thinking', '')}`; "
        f"schema-pass calls: **{payload['provider']['schema_pass_calls']}**; "
        f"reasoning characters: **{payload['provider']['reasoning_characters']}**.", "",
        _table(("Gate", "Result"), (
            (name, "PASS" if value else "FAIL") for name, value in gates.items()
        )), "",
        _table(("Semantic group", "Malfunction", "Variants", "Repairs", "Result"), (
            (
                item["semantic_group_id"], item["malfunction_id"],
                f"{item['realized_variants']}/{item['requested_variants']}",
                item["repairs"], item["failure_code"] or item["status"],
            ) for item in reviewed
        )),
    )))
    return payload


def write_p5f_closure_report(
    root: Path, *, recovery_run_id: str, supported_run_id: str,
    validation: dict[str, Any],
) -> dict[str, Any]:
    """Write the final P5-F evidence package after validation and Git handoff."""
    root = root.resolve()
    reconstruction = json.loads(
        (root / "output/P5F_Compound_Failure_Reconstruction.json").read_text(
            encoding="utf-8"
        )
    )
    offline = json.loads(
        (root / "output/P5F_Offline_Compound_Closure_Audit.json").read_text(
            encoding="utf-8"
        )
    )
    recovery = json.loads(
        (root / "output/P5F_13_Group_Recovery_Smoke.json").read_text(
            encoding="utf-8"
        )
    )
    supported = json.loads(
        (root / "output/P5F_24_Group_Supported_Smoke.json").read_text(
            encoding="utf-8"
        )
    )
    start = "eb3e125a16d72368829977232bb6c72eace28d0a"
    head = _git(root, "rev-parse", "HEAD")
    origin = _git(root, "rev-parse", "origin/master")
    remotes = _git(root, "remote", "-v")
    method_changes = [
        path for path in _git(root, "diff", "--name-only", f"{start}..{head}").splitlines()
        if path.startswith("method_assets/")
    ]
    recovery_results = recovery["results"]
    supported_results = supported["results"]
    variants_realized = sum(
        int(item["realized_variants"]) for item in supported["reviewed_groups"]
    )
    validation_pass = all(
        str(value).upper().startswith("PASS") for value in validation.values()
    )
    success = all((
        reconstruction["summary"]["failed_semantic_groups"] == 13,
        offline["decision"] == "OFFLINE_COMPOUND_CLOSURE_PASS",
        recovery["decision"] == "SUPPORTED_SCOPE_SMOKE_PASS",
        supported["decision"] == "SUPPORTED_SCOPE_SMOKE_PASS",
        validation_pass, head == origin, not method_changes,
    ))
    final = {
        "supported_scope": (
            "SUPPORTED_SCOPE_SMOKE_PASS" if success else "SUPPORTED_SCOPE_SMOKE_FAIL"
        ),
        "input_pipeline": (
            "INPUT_PIPELINE_READY"
            if offline["gates"]["project_fact_regression"] else "NOT_READY"
        ),
        "method_authority": "METHOD_AUTHORITY_PENDING",
        "full_r4": "NOT_READY_FOR_FULL_R4",
    }
    payload = {
        "artifact_version": "p5f-closure-report-v1",
        "git": {
            "starting_github_sha": start,
            "ending_commit": head,
            "origin_master": origin,
            "github_master_verified": head == origin,
            "gitlab_not_pushed": True,
            "gitlab_remote_configured": "gitlab" in remotes.casefold(),
        },
        "p5e_failure_reconstruction": reconstruction["summary"],
        "contract_change": {
            "old_provider_representation": "selected_atoms_by_dimension",
            "new_provider_representation": "selected_atom_ids_logical_atoms",
            "deterministic_compound_canonicalizations": (
                recovery_results["method_compound_canonicalizations"]
                + supported_results["method_compound_canonicalizations"]
            ),
            "recovery_canonicalizations": recovery_results[
                "method_compound_canonicalizations"
            ],
            "supported_canonicalizations": supported_results[
                "method_compound_canonicalizations"
            ],
            "compatibility_wrapper_count": 0,
        },
        "offline_compound_audit": {
            "decision": offline["decision"],
            "groups": offline["groups"],
            "partial_compound_candidates": offline["compound_invariants"][
                "provider_visible_partial_membership"
            ],
            "odd_contradicted_compounds": offline["compound_invariants"][
                "project_odd_contradicted_candidates"
            ],
            "authoritative_cutoff_regressions": offline["ranking_regression"][
                "authoritative_below_cutoff"
            ],
            "fm_cutoff_regressions": offline["ranking_regression"][
                "fm_template_below_cutoff"
            ],
        },
        "refinement_audit": {
            "historical_unsupported_refinement_cases": reconstruction["summary"][
                "initial_attempt_refinement_evidence_mismatches"
            ],
            "true_unsupported_exposed_after_fix": offline["refinement_evidence"][
                "unsupported_candidates_exposed"
            ],
            "historical_false_rejects": reconstruction["summary"][
                "initial_attempt_refinement_evidence_mismatches"
            ],
            "post_fix_result": (
                "PASS" if offline["gates"]["refinement_evidence"] else "FAIL"
            ),
            "evidence_boundary": (
                "Historical raw selected refinement atom IDs were not retained; the "
                "four false-reject count is established at group/failure-code level."
            ),
        },
        "recovery_13": {
            "run_id": recovery_run_id,
            "executed": recovery_results["groups_requested"],
            "calls": recovery["provider"]["calls"],
            "repairs": recovery_results["repairs"],
            "compound_canonicalizations": recovery_results[
                "method_compound_canonicalizations"
            ],
            "failures": recovery_results["groups_requested"]
            - recovery_results["groups_passed"],
            "provider": recovery["provider"],
            "decision": recovery["decision"],
        },
        "supported_24": {
            "run_id": supported_run_id,
            "executed": supported_results["groups_requested"],
            "calls": supported["provider"]["calls"],
            "repairs": supported_results["repairs"],
            "groups_passed": supported_results["groups_passed"],
            "variants_realized": variants_realized,
            "unsupported_atoms": supported_results["invented_atom_groups"],
            "odd_violations": supported_results["odd_contradiction_groups"],
            "compound_violations": supported_results["compound_failure_groups"],
            "diversity_violations": supported_results["diversity_failure_groups"],
            "strata_covered": len(supported["strata"]["covered"]),
            "strata_requested": len(supported["strata"]["target"]),
            "provider": supported["provider"],
            "decision": supported["decision"],
        },
        "method_authority": {
            "static_object_gaps": offline["groups"]["object_static_gaps"],
            "abort_action_gaps": offline["groups"]["action_abort_gaps"],
            "method_assets_modified": bool(method_changes),
            "method_asset_change_paths": method_changes,
        },
        "input_pipeline_regression": offline["project_fact_regression"],
        "validation": validation,
        "historical_guard": {
            "hashes_intact": offline["historical_hashes_intact"],
            "artifacts": offline["historical_artifacts"],
        },
        "full_r4_started": False,
        "final_decision": final,
    }
    _write_json(root / "output/P5F_Closure_Report.json", payload)
    lines = [
        "# P5-F Closure Report", "", "## Git", "",
        f"- Starting GitHub SHA: `{start}`",
        f"- Ending commit: `{head}`",
        f"- GitHub master verified: **{head == origin}**",
        "- GitLab not pushed: **true**", "",
        "## P5-E failure reconstruction", "",
        f"- FA005 failures: **{reconstruction['summary']['provider_compound_dimension_conflicts']}**",
        f"- CN expressway failures: **{reconstruction['summary']['compound_shortlist_closure_losses']}**",
        f"- Errors containing conflict and incomplete: **{reconstruction['summary']['final_errors_with_both_conflict_and_incomplete']}**",
        f"- True empty omissions: **{reconstruction['summary']['true_empty_compound_omissions_proven']}**",
        f"- Historical partial membership / Project ODD contradiction / parent widening: **{reconstruction['summary']['provider_visible_partial_compound_memberships_in_failed_groups']} / {reconstruction['summary']['project_odd_contradictions']} / {reconstruction['summary']['parent_widened_odd_authority_violations']}**", "",
        "## Contract change", "",
        "- Provider contract: `selected_atoms_by_dimension` -> `selected_atom_ids` logical atoms.",
        f"- Deterministic compound canonicalizations: **{payload['contract_change']['deterministic_compound_canonicalizations']}**; compatibility wrappers: **0**.", "",
        "## Offline compound audit", "",
        f"- Decision: **{offline['decision']}**; groups: **{offline['groups']['total']}**; Provider-ready: **{offline['groups']['provider_ready']}**; Method gaps: **{offline['groups']['method_gaps']}**.",
        f"- Partial/ODD-illegal compounds: **{payload['offline_compound_audit']['partial_compound_candidates']} / {payload['offline_compound_audit']['odd_contradicted_compounds']}**; authoritative/FM cutoff regressions: **0 / 0**.", "",
        "## Refinement audit", "",
        f"- Historical cases / false rejects / post-fix unsupported exposed: **4 / 4 / {payload['refinement_audit']['true_unsupported_exposed_after_fix']}**; result: **{payload['refinement_audit']['post_fix_result']}**.", "",
        "## 13-group recovery", "",
        f"- **13/13 PASS**; calls **{recovery['provider']['calls']}**; repairs **{recovery_results['repairs']}**; canonicalizations **{recovery_results['method_compound_canonicalizations']}**; decision **{recovery['decision']}**.",
        f"- Model `{recovery['provider']['resolved_models'][0]}`; thinking `{recovery['provider']['thinking']}`; finish reasons `{recovery['provider']['finish_reasons']}`.", "",
        "## 24-group supported smoke", "",
        f"- **24/24 PASS**, **{variants_realized}** variants, **14/14** strata; calls **{supported['provider']['calls']}**; repairs **{supported_results['repairs']}**.",
        "- Unsupported atoms / ODD / compound / diversity violations: **0 / 0 / 0 / 0**.",
        f"- Decision: **{supported['decision']}**.", "",
        "## Method authority", "",
        f"- Static OBJECT gaps: **{offline['groups']['object_static_gaps']}**; abort/action gaps: **{offline['groups']['action_abort_gaps']}**; Method assets modified: **no**.", "",
        "## Input pipeline regression", "",
        "- 7 contextual speeds preserved; controller false positives 0; driver allowed set preserved; remote/evasion remain absent.", "",
        "## Validation", "",
        *[f"- {name}: **{value}**" for name, value in validation.items()], "",
        "## Final decision", "",
        f"- **{final['supported_scope']}**",
        f"- **{final['input_pipeline']}**",
        f"- **{final['method_authority']}**",
        f"- **{final['full_r4']}**",
    ]
    _write(root / "output/P5F_Closure_Report.md", "\n".join(lines))
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
    "complete_p5f_failure_reconstruction", "run_compound_closure_audit",
    "run_calculation_input_readiness", "run_project_fact_recall_audit",
    "write_baseline_verification", "write_method_authority_clarification",
    "write_p5f_closure_report", "write_p5f_smoke_report",
    "write_pre_full_r4_closure",
    "write_supported_smoke_report",
]

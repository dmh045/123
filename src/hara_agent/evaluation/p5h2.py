"""Zero-Provider P5-H2 contextual-speed handoff audit."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.services.extraction import DocumentReader
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow.scenario_synthesis import ScenarioSynthesisRunner
from hara_agent.workflow.state import HARAState

from .pre_full_r4 import _SOURCE_FACTS, _find_block


EXPECTED_SOURCE_RUN = "hara-full-baseline-20260912-r1"
DIAGNOSTIC_SOURCE_RUN = "hara-p1-final-validation-r2"


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def _write_json(path: Path, payload: Any) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().upper()


def _method(root: Path) -> Any:
    report = TemplateRoleCompiler().compile_method(
        root / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    return YamlBaselineCompiler().compile(
        root / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )


def _source_speed_envelopes(root: Path) -> list[dict[str, Any]]:
    artifact = DocumentReader().read(root / "input/ItemDef.docx")
    result = []
    for category, needle, _classification, context, operator, value, value_max in _SOURCE_FACTS:
        block = _find_block(artifact.blocks, needle)
        result.append({
            "operating_mode": context,
            "speed_min_kph": 0.0 if operator == "LE" else float(value),
            "speed_max_kph": float(value if value_max is None else value_max),
            "condition": category,
            "unit": "km/h",
            "sources": [{
                "source_type": "item_definition",
                "source_id": artifact.source_id,
                "location": block.location,
                "excerpt": block.text,
            }],
            "provenance": "PROJECT_INPUT",
            "status": "FINALIZED",
        })
    return result


def _candidate_range(candidate: Any) -> list[float | None] | None:
    value = getattr(candidate, "speed_range_kph", None)
    return list(value) if value is not None else None


def _legacy_active_envelope(
    source_envelopes: list[dict[str, Any]],
) -> dict[str, Any]:
    source = next(
        (
            item for item in source_envelopes
            if str(item.get("operating_mode", "")).strip().upper() == "ACTIVE"
        ),
        None,
    )
    if source is None:
        raise RuntimeError("ItemDef source has no Active speed envelope")
    legacy = deepcopy(source)
    legacy["condition"] = ""
    legacy["speed_min_kph"] = 0.0
    legacy["speed_max_kph"] = 20.0
    return legacy


def _input_record(item: Any, source_envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    query = item.structured_semantic_query
    context = item.contextual_speed
    candidate_sets = {
        candidate_set.dimension: candidate_set
        for candidate_set in item.dimension_candidate_sets
    }
    ego_action = candidate_sets.get("EGO_ACTION")
    ego_dynamics = candidate_sets.get("EGO_DYNAMICS")
    # Scope matching remains owned by the synthesis service.  The audit joins
    # the exact source references returned by that service instead of
    # reimplementing its decision.
    source_locations = {
        str(ref.get("location", ""))
        for ref in context.get("source_refs", []) if isinstance(ref, dict)
    }
    matched_sources = [
        envelope for envelope in source_envelopes
        if any(
            isinstance(ref, dict) and str(ref.get("location", "")) in source_locations
            for ref in envelope.get("sources", [])
        )
    ]
    parent_speed = item.parent_scenario.get("facts", {}).get(
        "ego_speed_constraint", {}
    )
    project_range = [context.get("min_kph"), context.get("max_kph")]
    method_ranges = list(dict.fromkeys(
        tuple(value) for candidate in getattr(ego_dynamics, "candidates", ())
        if (value := _candidate_range(candidate)) is not None
    ))
    intersections = []
    if context.get("status") == "RESOLVED":
        for method_range in method_ranges:
            lowers = [
                value for value in (project_range[0], method_range[0])
                if value is not None
            ]
            uppers = [
                value for value in (project_range[1], method_range[1])
                if value is not None
            ]
            lower = max(lowers) if lowers else None
            upper = min(uppers) if uppers else None
            if lower is None or upper is None or lower <= upper:
                intersections.append([lower, upper])
    return {
        "semantic_group_id": item.semantic_group_id,
        "malfunction_id": item.malfunction_id,
        "function_id": item.function_id,
        "parent_scenario_id": item.parent_scenario_id,
        "hazardous_event_id": item.hazardous_event_id,
        "source_speed_envelopes": matched_sources,
        "source_locations": sorted(source_locations),
        "normalized_operating_modes": sorted({
            str(value.get("operating_mode", "")).strip().upper()
            for value in matched_sources
        }),
        "normalized_conditions": sorted({
            str(value.get("condition", "")).strip().upper()
            for value in matched_sources
        }),
        "structured_semantics": {
            key: deepcopy(query.get(key, [])) for key in (
                "action_categories", "object_categories", "traffic_relations",
                "road_relations", "declared_operational_contexts",
                "function_speed_constraints",
            )
        },
        "ego_action_candidates": [
            value.atom_id for value in getattr(ego_action, "candidates", ())
        ],
        "ego_dynamics_candidates": [
            value.atom_id for value in getattr(ego_dynamics, "candidates", ())
        ],
        "parent_speed_range": parent_speed,
        "context_match": context.get("status") == "RESOLVED"
        and context.get("classification") == "CONTEXTUAL_SPEED_CONSUMED",
        "classification": context.get("classification", "CONTEXT_MATCH_MISSING"),
        "match_basis": context.get("match_basis", ""),
        "selected_context": context.get("selected_context", ""),
        "project_contextual_range": project_range,
        "method_atom_ranges": [list(value) for value in method_ranges],
        "candidate_intersections": intersections,
        "report_visible_range": project_range,
        "automatic_point_pick": False,
    }


def _summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    classifications = Counter(str(item["classification"]) for item in rows)
    contexts: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        context = str(item.get("selected_context", "") or "UNRESOLVED")
        visible = item.get("report_visible_range", [])
        contexts[context][str(visible)] += 1
        if len(examples[context]) < 3:
            examples[context].append({
                key: item[key] for key in (
                    "semantic_group_id", "malfunction_id", "function_id",
                    "selected_context", "classification", "match_basis",
                    "parent_speed_range", "project_contextual_range",
                    "method_atom_ranges", "candidate_intersections",
                    "report_visible_range",
                )
            })
    return {
        "groups": len(rows),
        "classification_counts": dict(sorted(classifications.items())),
        "context_range_distribution": {
            key: dict(sorted(value.items())) for key, value in sorted(contexts.items())
        },
        "context_examples": dict(sorted(examples.items())),
        "contextual_envelopes_consumed": classifications["CONTEXTUAL_SPEED_CONSUMED"],
        "broad_active_fallback_count": classifications["PARENT_BROAD_RANGE_FALLBACK"],
        "source_conflicts": classifications["SOURCE_CONFLICT"],
        "automatic_point_picks": sum(bool(item["automatic_point_pick"]) for item in rows),
    }


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        f"# {payload['title']}", "",
        f"- Provider calls: **{payload['provider_calls']}**",
        f"- Requested source run: `{payload['requested_source_run_id']}`",
        f"- Evaluated source run: `{payload['evaluated_source_run_id']}`",
        f"- Source authority: **{payload['source_authority']}**",
        f"- Groups: **{summary['groups']}**",
        f"- Contextual envelopes consumed: **{summary['contextual_envelopes_consumed']}**",
        f"- Broad Active fallback: **{summary['broad_active_fallback_count']}**",
        f"- Source conflicts: **{summary['source_conflicts']}**",
        f"- Automatic point picks: **{summary['automatic_point_picks']}**", "",
        "## Classification", "",
        "| Classification | Count |", "|---|---:|",
    ]
    lines.extend(
        f"| {name} | {count} |"
        for name, count in summary["classification_counts"].items()
    )
    lines.extend(("", f"Decision: **{payload['decision']}**"))
    return "\n".join(lines)


def run_speed_context_consumption_audit(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    requested = root / "runtime/agent" / f"{EXPECTED_SOURCE_RUN}.checkpoint.json"
    source_run = EXPECTED_SOURCE_RUN if requested.is_file() else DIAGNOSTIC_SOURCE_RUN
    source_path = root / "runtime/agent" / f"{source_run}.checkpoint.json"
    if not source_path.is_file():
        raise FileNotFoundError(
            "Neither the current P5-F source checkpoint nor the historical "
            "diagnostic parent checkpoint is available"
        )
    state = HARAState.read_committed(json.loads(source_path.read_text(encoding="utf-8")))
    method = _method(root)
    runner = ScenarioSynthesisRunner(
        method=method, client=None,
        run_dir=root / "runtime/agent", review_root=root / "runtime/review",
    )
    source_envelopes = _source_speed_envelopes(root)

    before_state = deepcopy(state)
    before_typed = before_state.item_definition.setdefault("typed", {})
    legacy_envelopes = [_legacy_active_envelope(source_envelopes)]
    before_typed["speed_envelopes"] = deepcopy(legacy_envelopes)
    before_typed["speed_min_kph"] = 0.0
    before_typed["speed_max_kph"] = 20.0
    before_inputs, *_ = runner._prepare(before_state)
    after_state = deepcopy(state)
    typed = after_state.item_definition.setdefault("typed", {})
    typed["speed_envelopes"] = deepcopy(source_envelopes)
    typed["speed_min_kph"] = 0.0
    typed["speed_max_kph"] = 30.0
    after_inputs, *_ = runner._prepare(after_state)

    source_authority = (
        "CURRENT_P5F_SOURCE"
        if source_run == EXPECTED_SOURCE_RUN
        else "HISTORICAL_PARENT_STATE_DIAGNOSTIC_ONLY"
    )
    common = {
        "artifact_version": "p5h2-speed-context-consumption-audit-v1",
        "provider_calls": 0,
        "requested_source_run_id": EXPECTED_SOURCE_RUN,
        "evaluated_source_run_id": source_run,
        "source_checkpoint": str(source_path.relative_to(root)),
        "source_checkpoint_sha256": _sha256(source_path),
        "source_authority": source_authority,
        "source_contextual_envelopes": source_envelopes,
    }
    before_records = [_input_record(item, legacy_envelopes) for item in before_inputs]
    after_records = [_input_record(item, source_envelopes) for item in after_inputs]
    before = {
        **common,
        "title": "P5-H2 Speed Context Consumption Audit — Before",
        "evaluation_model": "LEGACY_PARENT_ACTIVE_ONLY",
        "records": before_records,
        "summary": _summary(before_records),
        "decision": "CONTEXTUAL_SPEED_NOT_ROUTED",
    }
    after_summary = _summary(after_records)
    after = {
        **common,
        "title": "P5-H2 Speed Context Consumption Audit — After",
        "evaluation_model": "CURRENT_CONTEXTUAL_SPEED_ROUTING",
        "records": after_records,
        "summary": after_summary,
        "decision": (
            "CONTEXTUAL_SPEED_HANDOFF_READY"
            if source_authority == "CURRENT_P5F_SOURCE"
            and after_summary["contextual_envelopes_consumed"] > 0
            and after_summary["automatic_point_picks"] == 0
            else "NOT_READY_CURRENT_P5F_SOURCE_MISSING"
        ),
    }
    outputs = root / "output"
    for stem, payload in (
        ("P5H2_Speed_Context_Consumption_Audit_Before", before),
        ("P5H2_Speed_Context_Consumption_Audit_After", after),
    ):
        _write_json(outputs / f"{stem}.json", payload)
        _write(outputs / f"{stem}.md", _markdown(payload))
    return {"before": before, "after": after}


__all__ = ["run_speed_context_consumption_audit"]

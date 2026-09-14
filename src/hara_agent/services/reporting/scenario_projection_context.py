from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


ScenarioProjectionKey = tuple[str, str, str]


def _key(item: Mapping[str, Any]) -> ScenarioProjectionKey:
    key = (
        str(item.get("malfunction_id", "")),
        str(item.get("parent_scenario_id", "")),
        str(item.get("hazardous_event_id", "")),
    )
    if not all(key):
        raise ValueError(f"Scenario projection context key is incomplete: {key}")
    return key


def _records(path: str | Path, field: str) -> list[dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    values = payload.get(field, [])
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ValueError(f"Scenario projection artifact has invalid {field}: {source}")
    return values


def load_scenario_projection_contexts(
    *,
    synthesis_candidates_path: str | Path,
    speed_context_audit_path: str | Path,
) -> dict[ScenarioProjectionKey, dict[str, Any]]:
    """Join deterministic synthesis preparation with its audited speed handoff.

    This adapter does not select Method atoms or create analytical children. It
    only exposes already-recorded group identity, coverage intent, structured
    query facts, and contextual speed to the report projection boundary.
    """
    candidates = _records(synthesis_candidates_path, "groups")
    speeds = _records(speed_context_audit_path, "records")
    candidate_by_key = {_key(item): item for item in candidates}
    speed_by_key = {_key(item): item for item in speeds}
    if len(candidate_by_key) != len(candidates):
        raise ValueError("Scenario synthesis candidate artifact contains duplicate projection keys")
    if len(speed_by_key) != len(speeds):
        raise ValueError("Speed context audit contains duplicate projection keys")
    if candidate_by_key.keys() != speed_by_key.keys():
        missing_speed = sorted(candidate_by_key.keys() - speed_by_key.keys())
        missing_candidates = sorted(speed_by_key.keys() - candidate_by_key.keys())
        raise ValueError(
            "Scenario projection artifacts do not cover the same logical groups: "
            f"missing_speed={missing_speed[:5]}, missing_candidates={missing_candidates[:5]}"
        )

    contexts: dict[ScenarioProjectionKey, dict[str, Any]] = {}
    semantic_group_ids: set[str] = set()
    for key, candidate in candidate_by_key.items():
        speed = speed_by_key[key]
        semantic_group_id = str(candidate.get("semantic_group_id", ""))
        if not semantic_group_id:
            raise ValueError(f"Scenario projection group lacks semantic_group_id: {key}")
        if semantic_group_id in semantic_group_ids:
            raise ValueError(f"Duplicate semantic_group_id in scenario projection: {semantic_group_id}")
        semantic_group_ids.add(semantic_group_id)

        visible_range = speed.get("report_visible_range", [])
        if (
            not isinstance(visible_range, list)
            or len(visible_range) != 2
            or not all(isinstance(value, (int, float)) for value in visible_range)
        ):
            raise ValueError(f"Scenario projection speed range is invalid: {key}")
        query = dict(candidate.get("structured_semantic_query", {}))
        selected_context = str(speed.get("selected_context", ""))
        speed_context = {
            "status": "RESOLVED",
            "classification": str(speed.get("classification", "")),
            "selected_context": selected_context,
            "match_basis": str(speed.get("match_basis", "")),
            "min_kph": float(visible_range[0]),
            "max_kph": float(visible_range[1]),
            "display_kind": "UPPER_BOUND"
            if float(visible_range[0]) == 0
            and selected_context in {
                "ENTRY", "CRUISE", "MAXIMUM_SPEED_DURING_PARKING", "PARKING"
            }
            else "RANGE",
            "source_expressions": [
                str(source.get("excerpt", ""))
                for envelope in speed.get("source_speed_envelopes", [])
                if isinstance(envelope, Mapping)
                for source in envelope.get("sources", [])
                if isinstance(source, Mapping) and str(source.get("excerpt", ""))
            ],
            "source_refs": [
                dict(source)
                for envelope in speed.get("source_speed_envelopes", [])
                if isinstance(envelope, Mapping)
                for source in envelope.get("sources", [])
                if isinstance(source, Mapping)
            ],
        }
        query["contextual_speed"] = dict(speed_context)
        contexts[key] = {
            "semantic_group_id": semantic_group_id,
            "structured_semantic_query": query,
            "coverage_plan": dict(candidate.get("coverage_plan", {})),
            "contextual_speed": speed_context,
        }
    return contexts


__all__ = ["ScenarioProjectionKey", "load_scenario_projection_contexts"]

from __future__ import annotations

import threading
import sys
import time
from dataclasses import asdict, replace

from hara_agent.models import (
    FunctionDefinition, ItemDefinitionFacts, ReviewStatus, SourceRef,
)
from hara_agent.services.extraction import ValidatedArtifactCache
from hara_agent.services.extraction import PROJECT_FACT_SPEC_BATCHES
from hara_agent.services.semantic import (
    ItemArtifactExtractionAgent,
    ItemEvidenceRouter,
    ItemSupplementAgent,
    TargetedProjectFactExtractionAgent,
)
from hara_agent.services.semantic.item_definition_agent import ItemDefinitionExtractionAgent
from hara_agent.workflow.state import HARAState, WorkflowStage

from .parallel import ordered_parallel_map


ARTIFACT_SCHEMA_VERSION = "validated-item-artifact-v3-atomic-project-facts"


def _facts_from_dict(value: dict) -> ItemDefinitionFacts:
    return ItemDefinitionFacts.from_dict(value)


def _function_from_dict(value: dict) -> FunctionDefinition:
    payload = dict(value)
    payload["sources"] = [SourceRef(**item) for item in payload.get("sources", [])]
    payload["status"] = ReviewStatus(payload.get("status", ReviewStatus.PENDING.value))
    return FunctionDefinition(**payload)


def _missing_core_fields(facts: ItemDefinitionFacts) -> list[str]:
    missing = []
    if not facts.operating_modes:
        missing.append("operating_modes")
    if not facts.odd_locations:
        missing.append("odd.locations")
    if not facts.odd_road_types:
        missing.append("odd.road_types")
    if not facts.odd_weather_conditions:
        missing.append("odd.weather_conditions")
    if not facts.odd_road_surfaces:
        missing.append("odd.road_surfaces")
    if facts.speed_min_kph is None:
        missing.append("odd.speed_range_kph")
    return missing


def _merge_supplements(
    facts: ItemDefinitionFacts, patches: dict[str, dict],
) -> tuple[ItemDefinitionFacts, list[str]]:
    warnings = []
    values = {
        "operating_modes": list(facts.operating_modes),
        "odd_locations": list(facts.odd_locations),
        "odd_road_types": list(facts.odd_road_types),
        "odd_weather_conditions": list(facts.odd_weather_conditions),
        "odd_road_surfaces": list(facts.odd_road_surfaces),
        "speed_min_kph": facts.speed_min_kph,
        "speed_max_kph": facts.speed_max_kph,
        "speed_envelopes": list(facts.speed_envelopes),
        "numeric_constraints": list(facts.numeric_constraints),
        "driver_context_facts": list(facts.driver_context_facts),
        "performance_parameters": list(facts.performance_parameters),
        "driver_contexts": list(facts.driver_contexts),
        "exposure_inputs": list(facts.exposure_inputs),
    }
    odd_patch = patches.get("odd_repair", {})
    odd = odd_patch.get("odd", {}) if isinstance(odd_patch, dict) else {}
    if not isinstance(odd, dict):
        odd = {}
        warnings.append("局部ODD补抽取返回的odd不是object，已忽略")
    list_mappings = (
        ("operating_modes", odd_patch.get("operating_modes") if isinstance(odd_patch, dict) else None),
        ("odd_locations", odd.get("locations")),
        ("odd_road_types", odd.get("road_types")),
        ("odd_weather_conditions", odd.get("weather_conditions")),
        ("odd_road_surfaces", odd.get("road_surfaces")),
    )
    for field, candidate in list_mappings:
        if not values[field] and candidate is not None:
            values[field] = [str(item) for item in ItemDefinitionExtractionAgent._list(candidate, field)]
    if values["speed_min_kph"] is None and "speed_range_kph" in odd:
        speed_min, speed_max, warning = ItemDefinitionExtractionAgent._speed_range(
            odd.get("speed_range_kph")
        )
        values["speed_min_kph"], values["speed_max_kph"] = speed_min, speed_max
        if warning:
            warnings.append(warning)

    project_patch = patches.get("project_evidence", {})
    if isinstance(project_patch, dict):
        for field in ("performance_parameters", "driver_contexts", "exposure_inputs"):
            candidate = project_patch.get(field)
            if candidate is not None:
                values[field] = ItemDefinitionExtractionAgent._list(candidate, field)
    else:
        warnings.append("项目证据补抽取不是object，已忽略")

    return replace(facts, **values, status=ReviewStatus.PENDING), warnings


def _merge_targeted_project_facts(
    facts: ItemDefinitionFacts, payloads: dict[str, dict],
) -> ItemDefinitionFacts:
    speed_envelopes, numeric_constraints, driver_context_facts = [], [], []
    for category in PROJECT_FACT_SPEC_BATCHES:
        payload = payloads.get(category, {})
        speed_envelopes.extend(payload.get("speed_envelopes", []))
        numeric_constraints.extend(payload.get("numeric_constraints", []))
        driver_context_facts.extend(payload.get("driver_context_facts", []))
    serialized = asdict(facts)
    serialized.update({
        "speed_envelopes": speed_envelopes,
        "numeric_constraints": numeric_constraints,
        "driver_context_facts": driver_context_facts,
    })
    return ItemDefinitionFacts.from_dict(serialized)


def extract_item_artifacts(
    state: HARAState,
    artifact_agent: ItemArtifactExtractionAgent,
    supplement_agent: ItemSupplementAgent,
    router: ItemEvidenceRouter,
    *,
    targeted_agent: TargetedProjectFactExtractionAgent | None = None,
    max_workers: int = 2,
    progress=None,
    cache: ValidatedArtifactCache | None = None,
) -> HARAState:
    """Run one full-document extraction, then only targeted repair calls."""
    stage_started = time.monotonic()
    cache_save_elapsed = 0.0
    document_text = str(state.item_definition.get("text", ""))
    source_id = str(state.item_definition.get("source_id", ""))
    client_config = getattr(artifact_agent.client, "config", None)
    cache_material = {
        "document_text": document_text,
        "source_id": source_id,
        "main_prompt_version": artifact_agent.PROMPT_VERSION,
        "supplement_prompt_version": supplement_agent.PROMPT_VERSION,
        "targeted_prompt_version": (
            targeted_agent.PROMPT_VERSION if targeted_agent else "disabled"
        ),
        "router_version": "item-evidence-router-v2-schema-guided",
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "provider": getattr(client_config, "provider", "custom-client"),
        "base_url": getattr(client_config, "base_url", ""),
        "model": getattr(client_config, "model", type(artifact_agent.client).__name__),
        "extraction_thinking": getattr(client_config, "extraction_thinking", "default"),
    }
    cache_key = cache.key(cache_material) if cache else ""
    cache_lookup_started = time.monotonic()
    if cache and cache_key:
        cached_payload, cache_status = cache.load_with_status(cache_key)
    else:
        cached_payload, cache_status = None, "cache_not_configured"
    cache_lookup_elapsed = time.monotonic() - cache_lookup_started
    if cached_payload is None:
        print(
            f"[HARA] artifact cache miss key={cache_key or 'none'} reason={cache_status}",
            file=sys.stderr,
            flush=True,
        )
    else:
        print(
            f"[HARA] artifact cache hit key={cache_key}",
            file=sys.stderr,
            flush=True,
        )
    core_cached = False
    cached_core = cached_payload.get("core") if isinstance(cached_payload, dict) else None
    if isinstance(cached_core, dict):
        try:
            facts = _facts_from_dict(cached_core["facts"])
            functions = [_function_from_dict(item) for item in cached_core["functions"]]
            artifact_agent.validator.ensure_valid(functions)
            core_cached = True
            main_audit = {
                "task": "extract_core_item_artifacts",
                "prompt_version": artifact_agent.PROMPT_VERSION,
                "model": cache_material["model"],
                "cache_hit": True,
                "cache_key": cache_key,
                "elapsed_seconds": 0.0,
                "llm_call_count": 0,
                "input_characters": len(document_text),
            }
        except (KeyError, TypeError, ValueError):
            core_cached = False
            print(
                f"[HARA] artifact cache miss key={cache_key} "
                "reason=invalid_cached_artifact",
                file=sys.stderr,
                flush=True,
            )
    core_started = time.monotonic()
    if not core_cached:
        facts, functions, main_audit = artifact_agent.extract(document_text, source_id)
        main_audit.update({"cache_hit": False, "cache_key": cache_key})
        cached_payload = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "core": {
                "facts": asdict(facts),
                "functions": [asdict(item) for item in functions],
            },
            "supplements": {},
            "targeted_project_facts": {},
        }
        if cache and cache_key:
            save_started = time.monotonic()
            cache.save(cache_key, cached_payload)
            cache_save_elapsed += time.monotonic() - save_started
    core_elapsed = time.monotonic() - core_started
    if progress:
        progress("item_artifacts_main", 1, 1)

    routing_started = time.monotonic()
    missing_before = _missing_core_fields(facts)
    blocks = list(state.item_definition.get("blocks", []))
    routed = []
    routing_diagnostics = []
    cached_supplements = (
        dict(cached_payload.get("supplements", {}))
        if isinstance(cached_payload, dict) else {}
    )
    if missing_before and "odd_repair" not in cached_supplements:
        odd_result = router.retrieve(blocks, "odd_repair")
        routing_diagnostics.append(odd_result.diagnostics.to_dict())
        if odd_result.routed:
            routed.append(odd_result.routed)
    project_result = (
        router.retrieve(blocks, "project_evidence")
        if "project_evidence" not in cached_supplements else None
    )
    if project_result:
        routing_diagnostics.append(project_result.diagnostics.to_dict())
        if project_result.routed:
            routed.append(project_result.routed)
    routing_elapsed = time.monotonic() - routing_started

    patches, supplement_audits = dict(cached_supplements), []
    supplement_wall_started = time.monotonic()
    if routed:
        cache_lock = threading.Lock()

        def extract_and_cache(item):
            result = supplement_agent.extract(item, source_id)
            if cache and cache_key and isinstance(cached_payload, dict):
                with cache_lock:
                    cached_payload.setdefault("supplements", {})[item.task] = result[0]
                    save_started = time.monotonic()
                    cache.save(cache_key, cached_payload)
                    nonlocal_cache_save[0] += time.monotonic() - save_started
            return result

        nonlocal_cache_save = [0.0]
        results = ordered_parallel_map(
            routed,
            extract_and_cache,
            max_workers=min(max_workers, 2),
            on_progress=(
                lambda done, total: progress("item_artifacts_supplement", done, total)
            ) if progress else None,
        )
        for route, (patch, audit) in zip(routed, results):
            patches[route.task] = patch
            supplement_audits.append(audit)
        cache_save_elapsed += nonlocal_cache_save[0]
    supplement_wall_elapsed = time.monotonic() - supplement_wall_started
    merge_started = time.monotonic()
    if patches:
        facts, merge_warnings = _merge_supplements(facts, patches)
    else:
        merge_warnings = []
    merge_elapsed = time.monotonic() - merge_started

    targeted_audits = []
    targeted_payloads = (
        dict(cached_payload.get("targeted_project_facts", {}))
        if isinstance(cached_payload, dict) else {}
    )
    if targeted_agent is not None:
        targeted_routes = []
        for category, specs in PROJECT_FACT_SPEC_BATCHES.items():
            if category in targeted_payloads:
                continue
            route_result = router.retrieve(
                blocks,
                "project_evidence",
                required_specs=tuple(spec.retrieval_spec() for spec in specs),
            )
            routing_diagnostics.append(route_result.diagnostics.to_dict())
            if route_result.routed is not None:
                targeted_routes.append((category, specs, route_result.routed))
        if targeted_routes:
            targeted_lock = threading.Lock()
            targeted_save_elapsed = [0.0]

            def extract_targeted(item):
                category, specs, route = item
                normalized, audit = targeted_agent.extract(
                    category, specs, route, source_id
                )
                payload = normalized.to_cache_dict()
                if cache and cache_key and isinstance(cached_payload, dict):
                    with targeted_lock:
                        cached_payload.setdefault("targeted_project_facts", {})[category] = payload
                        save_started = time.monotonic()
                        cache.save(cache_key, cached_payload)
                        targeted_save_elapsed[0] += time.monotonic() - save_started
                return category, payload, audit

            targeted_results = ordered_parallel_map(
                targeted_routes,
                extract_targeted,
                max_workers=min(max_workers, 3),
                on_progress=(
                    lambda done, total: progress("targeted_project_facts", done, total)
                ) if progress else None,
            )
            for category, payload, audit in targeted_results:
                targeted_payloads[category] = payload
                targeted_audits.append(audit)
            cache_save_elapsed += targeted_save_elapsed[0]
        facts = _merge_targeted_project_facts(facts, targeted_payloads)
    total_elapsed = time.monotonic() - stage_started
    supplement_elapsed = {
        audit["task"].removeprefix("supplement_"): audit.get("elapsed_seconds", 0.0)
        for audit in supplement_audits
    }
    llm_calls = int(main_audit.get("llm_call_count", 0)) + sum(
        int(audit.get("llm_call_count", 0)) for audit in supplement_audits
    ) + sum(int(audit.get("llm_call_count", 0)) for audit in targeted_audits)
    llm_elapsed = float(main_audit.get("elapsed_seconds", 0.0)) + sum(
        float(audit.get("elapsed_seconds", 0.0)) for audit in supplement_audits
    ) + sum(float(audit.get("elapsed_seconds", 0.0)) for audit in targeted_audits)
    transient_error_counts: dict[str, int] = {}
    for audit in [main_audit, *supplement_audits, *targeted_audits]:
        usage = audit.get("usage", {})
        counts = usage.get("transient_error_counts", {}) if isinstance(usage, dict) else {}
        if isinstance(counts, dict):
            for error_type, count in counts.items():
                transient_error_counts[str(error_type)] = (
                    transient_error_counts.get(str(error_type), 0) + int(count)
                )
    local_elapsed = max(0.0, total_elapsed - core_elapsed - supplement_wall_elapsed)
    print(
        "[HARA] extract timing "
        f"core_item_artifact={core_elapsed:.1f}s evidence_routing={routing_elapsed:.1f}s "
        f"supplement_odd_repair={supplement_elapsed.get('odd_repair', 0.0):.1f}s "
        f"supplement_project_evidence={supplement_elapsed.get('project_evidence', 0.0):.1f}s "
        f"supplement_parallel_wall={supplement_wall_elapsed:.1f}s "
        f"merge_validate={merge_elapsed:.1f}s cache_lookup={cache_lookup_elapsed:.1f}s "
        f"cache_save={cache_save_elapsed:.1f}s total={total_elapsed:.1f}s "
        f"llm_calls={llm_calls} transient_errors={transient_error_counts}",
        file=sys.stderr,
        flush=True,
    )

    state.item_definition["typed"] = asdict(facts)
    state.functions = [asdict(item) for item in functions]
    state.record(
        "item_artifacts_extracted",
        **main_audit,
        missing_core_fields_before_supplement=missing_before,
        missing_core_fields_after_supplement=_missing_core_fields(facts),
        supplement_task_count=len(routed),
        routing_diagnostics=routing_diagnostics,
        merge_warnings=merge_warnings,
    )
    for audit in supplement_audits:
        state.record("item_artifact_supplement_extracted", **audit)
    for audit in targeted_audits:
        state.record("targeted_project_facts_extracted", **audit)
    state.record(
        "extract_timing",
        elapsed_seconds=round(total_elapsed, 3),
        llm_calls=llm_calls,
        llm_elapsed_seconds=round(llm_elapsed, 3),
        local_elapsed_seconds=round(local_elapsed, 3),
        core_item_artifact_seconds=round(core_elapsed, 3),
        evidence_routing_seconds=round(routing_elapsed, 3),
        supplement_task_elapsed_seconds=supplement_elapsed,
        supplement_parallel_wall_seconds=round(supplement_wall_elapsed, 3),
        merge_validate_seconds=round(merge_elapsed, 3),
        cache_lookup_seconds=round(cache_lookup_elapsed, 3),
        cache_save_seconds=round(cache_save_elapsed, 3),
        transient_error_counts=transient_error_counts,
        cache_status=cache_status,
        cache_hit=core_cached,
        cache_key=cache_key,
    )

    state.pending_reviews.append({
        "field": "item_definition",
        "reason": "Item Definition/ODD语义抽取尚未完成工程确认",
    })
    if functions:
        state.pending_reviews.append({
            "field": "functions",
            "reason": f"{len(functions)}个Function候选尚未完成工程确认",
        })
    state.stage = WorkflowStage.FUNCTIONS
    return state

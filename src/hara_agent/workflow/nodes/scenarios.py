from __future__ import annotations

from dataclasses import asdict
import sys
from typing import Sequence

from hara_agent.contracts import FactOrigin, RequiredFactSpec
from hara_agent.models import ItemDefinitionFacts, MalfunctionCandidate, ScenarioCandidate
from hara_agent.services.semantic import (
    ScenarioFeasibilityAgent, ScenarioRiskFactAgent, build_project_evidence_registry,
)
from hara_agent.workflow.state import HARAState, WorkflowStage

from .parallel import ordered_parallel_map


def assess_scenarios(state: HARAState, agent: ScenarioFeasibilityAgent,
                     malfunctions: list[MalfunctionCandidate],
                     candidates: list[ScenarioCandidate],
                     max_workers: int = 1,
                     progress=None,
                     risk_fact_agent: ScenarioRiskFactAgent | None = None,
                     required_fact_specs: Sequence[RequiredFactSpec] = ()) -> HARAState:
    assessments = []
    typed = state.item_definition.get("typed", {})
    project_registry = (
        build_project_evidence_registry(ItemDefinitionFacts.from_dict(typed))
        if isinstance(typed, dict) and typed else None
    )
    batches = ordered_parallel_map(
        malfunctions,
        lambda malfunction: agent.assess(
            malfunction, candidates, project_registry=project_registry
        ),
        max_workers=max_workers,
        on_progress=(lambda done, total: progress("scenario", done, total)) if progress else None,
    )
    audits = []
    for items, audit in batches:
        assessments.extend(items)
        audits.append(audit)
        state.record("scenario_feasibility_assessed", **audit)
    retained_ids = {item.scenario_id for item in assessments if item.retain}
    state.scenarios = [item for item in candidates if item.scenario_id in retained_ids]
    serialized_assessments = [asdict(item) for item in assessments]
    state.item_definition["scenario_assessments"] = serialized_assessments
    if risk_fact_agent is not None:
        risk_facts, risk_fact_audit = risk_fact_agent.interpret(
            serialized_assessments,
            state.scenarios,
            malfunctions,
            required_fact_specs,
        )
        typed_facts = ItemDefinitionFacts.from_dict(state.item_definition["typed"])
        requested_types = {
            item.fact_type.value
            for item in required_fact_specs
            if item.origin is FactOrigin.SCENARIO_FACT
        }
        requested_pairs = {
            (str(item.get("malfunction_id", "")), str(item.get("scenario_id", "")))
            for item in serialized_assessments
            if item.get("malfunction_id") and item.get("scenario_id")
        }
        replaced_ids = {
            item.fact_id
            for item in typed_facts.risk_facts
            if item.parameter in requested_types
            and (
                item.context.get("malfunction_id", ""),
                item.context.get("scenario_id", ""),
            ) in requested_pairs
        }
        if replaced_ids or risk_facts:
            typed_facts.risk_facts = [
                item for item in typed_facts.risk_facts if item.fact_id not in replaced_ids
            ]
            typed_facts.method_risk_fact_bindings = [
                item for item in typed_facts.method_risk_fact_bindings
                if item.source_fact_id not in replaced_ids
            ]
            typed_facts.risk_facts.extend(risk_facts)
            state.item_definition["typed"] = asdict(typed_facts)
        if risk_facts:
            state.pending_reviews.append({
                "field": "scenario_risk_facts",
                "reason": (
                    f"{len(risk_facts)} evidence-grounded scenario facts require engineering approval"
                ),
            })
        state.record(
            "scenario_risk_facts_interpreted",
            replaced_stale_fact_count=len(replaced_ids),
            **risk_fact_audit,
        )
    pending_assessments = sum(item.status.value == "PENDING" for item in assessments)
    pending_candidates = sum(item.status.value == "PENDING" for item in state.scenarios)
    if pending_assessments or pending_candidates:
        state.pending_reviews.append({
            "field": "scenarios",
            "reason": (
                f"{pending_candidates}个场景候选和{pending_assessments}个可行性结论尚未完成工程确认"
            ),
        })
    state.stage = WorkflowStage.SCORING
    state.record(
        "scenario_candidates_selected",
        candidate_count=len(candidates),
        retained_count=len(state.scenarios),
        assessment_count=len(assessments),
    )
    initial_batches = sum(item.get("initial_batch_count", 0) for item in audits)
    adaptive_splits = sum(item.get("adaptive_split_count", 0) for item in audits)
    leaf_batches = sum(item.get("leaf_batch_count", 0) for item in audits)
    total_calls = sum(item.get("llm_calls", 0) for item in audits)
    retry_calls = sum(item.get("retry_calls", 0) for item in audits)
    total_timeouts = sum(item.get("timeout_count", 0) for item in audits)
    output_limits = sum(item.get("output_limit_count", 0) for item in audits)
    schema_errors = sum(item.get("schema_error_count", 0) for item in audits)
    coverage_errors = sum(item.get("coverage_error_count", 0) for item in audits)
    single_failures = sum(item.get("single_item_failures", 0) for item in audits)
    all_chars = [value for item in audits for value in item.get("leaf_input_chars", [])]
    all_items = [value for item in audits for value in item.get("leaf_items", [])]
    completion_tokens = [value for item in audits for value in item.get("completion_tokens", [])]
    reasoning_chars = [value for item in audits for value in item.get("reasoning_characters", [])]
    transport_counts = {}
    for audit in audits:
        for category, count in audit.get("transport_error_counts", {}).items():
            transport_counts[category] = transport_counts.get(category, 0) + int(count)
    format_metrics = {
        key: sum(item.get(key, 0) for item in audits)
        for key in (
            "json_contract_errors", "format_retry_calls", "format_retry_successes",
            "format_retry_failures", "markdown_fence_normalizations",
        )
    }
    print(
        "[HARA] scenario feasibility summary "
        f"malfunctions={len(malfunctions)} scenario_candidates={len(candidates)} "
        f"scenario_pairs={len(malfunctions) * len(candidates)} "
        f"initial_batches={initial_batches} adaptive_splits={adaptive_splits} "
        f"leaf_batches={leaf_batches} llm_calls={total_calls} retry_calls={retry_calls} "
        f"timeouts={total_timeouts} output_limits={output_limits} "
        f"incomplete_reads={transport_counts.get('incomplete_read', 0)} "
        f"remote_disconnects={transport_counts.get('remote_disconnect', 0)} "
        f"connection_resets={transport_counts.get('connection_reset', 0)} "
        f"transport_retries={sum(transport_counts.values())} "
        f"json_contract_errors={format_metrics['json_contract_errors']} "
        f"format_retry_calls={format_metrics['format_retry_calls']} "
        f"max_leaf_items={max(all_items, default=0)} max_leaf_chars={max(all_chars, default=0)}",
        file=sys.stderr,
        flush=True,
    )
    state.record(
        "scenario_feasibility_summary",
        malfunction_count=len(malfunctions),
        scenario_candidate_count=len(candidates),
        scenario_pair_count=len(malfunctions) * len(candidates),
        llm_calls=total_calls,
        actual_llm_calls=total_calls,
        retry_calls=retry_calls,
        initial_batch_count=initial_batches,
        adaptive_split_count=adaptive_splits,
        leaf_batch_count=leaf_batches,
        timeout_count=total_timeouts,
        output_limit_count=output_limits,
        schema_error_count=schema_errors,
        coverage_error_count=coverage_errors,
        single_item_failures=single_failures,
        incomplete_reads=transport_counts.get("incomplete_read", 0),
        remote_disconnects=transport_counts.get("remote_disconnect", 0),
        connection_resets=transport_counts.get("connection_reset", 0),
        transport_retries=sum(transport_counts.values()),
        transport_failures=sum(item.get("transport_failures", 0) for item in audits),
        transport_error_counts=transport_counts,
        **format_metrics,
        max_leaf_items=max(all_items, default=0),
        min_leaf_items=min(all_items, default=0),
        average_leaf_items=(round(sum(all_items) / len(all_items), 1) if all_items else 0),
        max_leaf_chars=max(all_chars, default=0),
        min_leaf_chars=min(all_chars, default=0),
        average_leaf_chars=(round(sum(all_chars) / len(all_chars), 1) if all_chars else 0),
        max_completion_tokens=max(completion_tokens, default=0),
        average_completion_tokens=(
            round(sum(completion_tokens) / len(completion_tokens), 1) if completion_tokens else 0
        ),
        max_reasoning_characters=max(reasoning_chars, default=0),
        average_reasoning_characters=(
            round(sum(reasoning_chars) / len(reasoning_chars), 1) if reasoning_chars else 0
        ),
    )
    return state

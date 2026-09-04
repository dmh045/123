from __future__ import annotations

from dataclasses import asdict
import sys

from hara_agent.models import FunctionDefinition, GuidewordAssessment
from hara_agent.services.semantic import MalfunctionHazardAgent
from hara_agent.workflow.state import HARAState, WorkflowStage
from hara_agent.workflow.review_artifacts import ReviewArtifactWriter

from .parallel import ordered_parallel_map


def derive_malfunctions(state: HARAState, agent: MalfunctionHazardAgent,
                        functions: list[FunctionDefinition],
                        assessments: list[GuidewordAssessment],
                        max_workers: int = 1,
                        progress=None,
                        review_artifact_writer: ReviewArtifactWriter | None = None) -> HARAState:
    candidates = []
    def generate(function):
        related = [item for item in assessments if item.function_id == function.function_id]
        return agent.generate(function, related)

    batches = ordered_parallel_map(
        functions,
        generate,
        max_workers=max_workers,
        on_progress=(lambda done, total: progress("malfunction", done, total)) if progress else None,
    )
    audits = []
    for items, audit in batches:
        candidates.extend(items)
        audits.append(audit)
        state.record("malfunction_hazard_candidates_generated", **audit)
    normalized = []
    seen_ids = set()
    function_counts = {}
    for item in candidates:
        model_local_id = item.malfunction_id
        function_counts[item.function_id] = function_counts.get(item.function_id, 0) + 1
        authoritative_id = f"MF-{item.function_id}-{function_counts[item.function_id]:03d}"
        if authoritative_id in seen_ids:
            raise ValueError(f"确定性Malfunction ID冲突: {authoritative_id}")
        seen_ids.add(authoritative_id)
        item.malfunction_id = authoritative_id
        item.model_local_id = model_local_id
        value = asdict(item)
        normalized.append(value)
        if review_artifact_writer is not None:
            review_artifact_writer.record_malfunction(item)
        state.record(
            "malfunction_identity_normalized",
            function_id=item.function_id,
            model_local_id=model_local_id,
            authoritative_id=authoritative_id,
        )
    state.malfunctions = normalized
    pending_count = sum(item.status.value == "PENDING" for item in candidates)
    if pending_count:
        state.pending_reviews.append({
            "field": "malfunctions",
            "reason": f"{pending_count}个Malfunction/Hazard候选尚未完成工程确认",
        })
    skip_reasons = {
        reason: sum(audit.get("skip_reason") == reason for audit in audits)
        for reason in (
            "no_applicable_guidewords",
            "no_complete_applicable_guidewords",
            "no_credible_hazard_guidewords",
        )
    }
    llm_called = sum(not audit.get("skipped", False) for audit in audits)
    coverage_repairs = sum(
        int(audit.get("coverage_repair_count", 0)) for audit in audits
    )
    actual_llm_calls = sum(
        int(audit.get("llm_call_count", int(not audit.get("skipped", False))))
        for audit in audits
    )
    print(
        "[HARA] malfunction derivation summary "
        f"functions={len(functions)} llm_called={llm_called} "
        f"actual_llm_calls={actual_llm_calls} coverage_repairs={coverage_repairs} "
        f"skipped_no_applicable={skip_reasons['no_applicable_guidewords']} "
        f"skipped_incomplete={skip_reasons['no_complete_applicable_guidewords']} "
        f"skipped_no_hazard={skip_reasons['no_credible_hazard_guidewords']} "
        f"candidate_count={len(candidates)}",
        file=sys.stderr,
        flush=True,
    )
    state.record(
        "malfunction_derivation_summary",
        function_count=len(functions),
        llm_called=llm_called,
        actual_llm_calls=actual_llm_calls,
        coverage_repair_count=coverage_repairs,
        skipped_no_applicable=skip_reasons["no_applicable_guidewords"],
        skipped_incomplete=skip_reasons["no_complete_applicable_guidewords"],
        skipped_no_hazard=skip_reasons["no_credible_hazard_guidewords"],
        candidate_count=len(candidates),
    )
    state.stage = WorkflowStage.MALFUNCTIONS
    return state

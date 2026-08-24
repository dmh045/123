from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from hara_agent.contracts import RequiredFactSpec
from hara_agent.infrastructure.llm import LLMClient
from hara_agent.models import (
    FunctionDefinition,
    GuidewordAssessment,
    MalfunctionCandidate,
    ReviewStatus,
    ScenarioCandidate,
    SourceRef,
)
from hara_agent.services.semantic import (
    ItemArtifactExtractionAgent,
    ItemEvidenceRouter,
    ItemSupplementAgent,
    TargetedProjectFactExtractionAgent,
    GuidewordApplicabilityAgent,
    MalfunctionHazardAgent,
    ScenarioFeasibilityAgent,
    ScenarioRiskFactAgent,
)
from hara_agent.services.analysis import (
    ASILLookupService,
    MethodRiskFactBindingService,
    SafetyGoalService,
    ScenarioScoringService,
)
from hara_agent.services.reporting import HARAExcelRenderer
from hara_agent.services.validation import DownstreamPreflightService
from hara_agent.services.extraction import ValidatedArtifactCache

from .checkpoints import CheckpointRepository
from .graph import WorkflowGraph
from .nodes import (
    assess_guidewords,
    assess_scenarios,
    aggregate_safety_goals,
    derive_malfunctions,
    extract_item_artifacts,
    read_item_document,
    render_excel_report,
    pass_quality_gate,
    score_structured_scenarios,
)
from .state import HARAState, WorkflowStage


@dataclass(frozen=True)
class SemanticWorkflowInputs:
    item_path: str | Path
    guidewords: list[str]
    scenario_candidates: list[ScenarioCandidate] = field(default_factory=list)
    scenario_candidate_factory: Callable[[HARAState], tuple[list[ScenarioCandidate], dict]] | None = None
    project_context_preflight: Callable[[HARAState], dict] | None = None
    max_workers: int = 4
    progress: Callable[[str, int, int], None] | None = None
    stage_progress: Callable[[str, str, float], None] | None = None
    artifact_cache: ValidatedArtifactCache | None = None
    required_fact_specs: tuple[RequiredFactSpec, ...] = ()
    requested_operating_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticWorkflowAgents:
    client: LLMClient
    guidewords: GuidewordApplicabilityAgent
    malfunctions: MalfunctionHazardAgent
    scenarios: ScenarioFeasibilityAgent
    risk_facts: ScenarioRiskFactAgent | None = None


@dataclass(frozen=True)
class RiskWorkflowServices:
    scoring: ScenarioScoringService
    asil_table: ASILLookupService
    safety_goals: SafetyGoalService
    risk_fact_binding: MethodRiskFactBindingService | None = None


@dataclass(frozen=True)
class ReportingWorkflowConfig:
    template_path: str | Path
    output_path: str | Path
    renderer: HARAExcelRenderer


def _sources(values: list[dict]) -> list[SourceRef]:
    return [SourceRef(**value) for value in values]


def _function(value: dict) -> FunctionDefinition:
    payload = dict(value)
    payload["sources"] = _sources(payload.get("sources", []))
    payload["status"] = ReviewStatus(payload.get("status", ReviewStatus.PENDING.value))
    return FunctionDefinition(**payload)


def _assessment(value: dict) -> GuidewordAssessment:
    payload = dict(value)
    payload["sources"] = _sources(payload.get("sources", []))
    payload["status"] = ReviewStatus(payload.get("status", ReviewStatus.PENDING.value))
    return GuidewordAssessment(**payload)


def _malfunction(value: dict) -> MalfunctionCandidate:
    payload = dict(value)
    payload["sources"] = _sources(payload.get("sources", []))
    payload["status"] = ReviewStatus(payload.get("status", ReviewStatus.PENDING.value))
    return MalfunctionCandidate(**payload)


def _scenario_candidates(state: HARAState, inputs: SemanticWorkflowInputs) -> list[ScenarioCandidate]:
    if inputs.scenario_candidate_factory is None:
        return inputs.scenario_candidates
    candidates, audit = inputs.scenario_candidate_factory(state)
    state.record("scenario_candidates_prepared", **audit)
    return candidates


def _assess_guidewords_after_project_context_preflight(
    state: HARAState,
    inputs: SemanticWorkflowInputs,
    agents: SemanticWorkflowAgents,
) -> HARAState:
    if inputs.project_context_preflight is not None:
        audit = inputs.project_context_preflight(state)
        state.record("project_context_preflight_passed", **audit)
    return assess_guidewords(
        state,
        agents.guidewords,
        [_function(value) for value in state.functions],
        inputs.guidewords,
        max_workers=inputs.max_workers,
        progress=inputs.progress,
    )


def build_semantic_frontend_graph(
    inputs: SemanticWorkflowInputs,
    agents: SemanticWorkflowAgents,
    checkpoint_repository: CheckpointRepository | None = None,
) -> WorkflowGraph:
    """Compose the reusable evidence-extraction and scenario-selection slice."""
    graph = WorkflowGraph(checkpoint_repository, progress=inputs.stage_progress)
    graph.add_node(
        WorkflowStage.INITIALIZE,
        lambda state: read_item_document(state, str(inputs.item_path)),
    )
    graph.add_node(
        WorkflowStage.EXTRACT,
        lambda state: extract_item_artifacts(
            state,
            ItemArtifactExtractionAgent(
                agents.client,
            ),
            ItemSupplementAgent(agents.client),
            ItemEvidenceRouter(),
            targeted_agent=TargetedProjectFactExtractionAgent(agents.client),
            max_workers=inputs.max_workers,
            progress=inputs.progress,
            cache=inputs.artifact_cache,
            required_fact_specs=inputs.required_fact_specs,
            requested_operating_modes=inputs.requested_operating_modes,
        ),
    )
    graph.add_node(
        WorkflowStage.FUNCTIONS,
        lambda state: _assess_guidewords_after_project_context_preflight(
            state, inputs, agents,
        ),
    )
    graph.add_node(
        WorkflowStage.HAZOP,
        lambda state: derive_malfunctions(
            state,
            agents.malfunctions,
            [_function(value) for value in state.functions],
            [_assessment(value["guideword_assessment"]) for value in state.malfunctions],
            max_workers=inputs.max_workers,
            progress=inputs.progress,
        ),
    )
    graph.add_node(
        WorkflowStage.MALFUNCTIONS,
        lambda state: assess_scenarios(
            state,
            agents.scenarios,
            [_malfunction(value) for value in state.malfunctions],
            _scenario_candidates(state, inputs),
            max_workers=inputs.max_workers,
            progress=inputs.progress,
            risk_fact_agent=agents.risk_facts,
            required_fact_specs=inputs.required_fact_specs,
        ),
    )
    return graph


def build_hara_agent_graph(
    inputs: SemanticWorkflowInputs,
    agents: SemanticWorkflowAgents,
    risk_services: RiskWorkflowServices,
    checkpoint_repository: CheckpointRepository | None = None,
    reporting: ReportingWorkflowConfig | None = None,
) -> WorkflowGraph:
    """Compose the production path through scoring, quality gate, and reporting."""
    graph = build_semantic_frontend_graph(inputs, agents, checkpoint_repository)
    scenario_node = graph.nodes[WorkflowStage.MALFUNCTIONS]
    downstream_preflight = DownstreamPreflightService()
    graph.nodes[WorkflowStage.MALFUNCTIONS] = lambda state: (
        downstream_preflight.validate(state), scenario_node(state)
    )[1]
    graph.add_node(
        WorkflowStage.SCORING,
        lambda state: score_structured_scenarios(
            state,
            risk_services.scoring,
            risk_services.asil_table,
            risk_services.risk_fact_binding,
        ),
    )
    graph.add_node(
        WorkflowStage.SAFETY_GOALS,
        lambda state: aggregate_safety_goals(state, risk_services.safety_goals),
    )
    graph.add_node(WorkflowStage.QUALITY_GATE, pass_quality_gate)
    if reporting is not None:
        graph.add_node(
            WorkflowStage.RENDER,
            lambda state: render_excel_report(
                state,
                reporting.renderer,
                reporting.template_path,
                reporting.output_path,
            ),
        )
    return graph

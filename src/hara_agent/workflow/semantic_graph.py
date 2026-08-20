from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from hara_agent.models import (
    FunctionDefinition,
    GuidewordAssessment,
    MalfunctionCandidate,
    ReviewStatus,
    ScenarioCandidate,
    SourceRef,
)
from hara_agent.services.semantic import (
    FunctionExtractionAgent,
    ItemArtifactExtractionAgent,
    ItemDefinitionExtractionAgent,
    ItemEvidenceRouter,
    ItemSupplementAgent,
    TargetedProjectFactExtractionAgent,
    GuidewordApplicabilityAgent,
    MalfunctionHazardAgent,
    ScenarioFeasibilityAgent,
)
from hara_agent.services.analysis import (
    DomainScoringService,
    FTTIService,
    SafetyGoalCatalogService,
    TemplateASILService,
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
    extract_functions,
    extract_typed_item_definition,
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
    max_workers: int = 4
    progress: Callable[[str, int, int], None] | None = None
    stage_progress: Callable[[str, str, float], None] | None = None
    artifact_cache: ValidatedArtifactCache | None = None


@dataclass(frozen=True)
class SemanticWorkflowAgents:
    item_definition: ItemDefinitionExtractionAgent
    functions: FunctionExtractionAgent
    guidewords: GuidewordApplicabilityAgent
    malfunctions: MalfunctionHazardAgent
    scenarios: ScenarioFeasibilityAgent


@dataclass(frozen=True)
class RiskWorkflowServices:
    scoring: DomainScoringService
    asil_table: TemplateASILService
    ftti: FTTIService
    safety_goals: SafetyGoalCatalogService


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


def build_semantic_frontend_graph(
    inputs: SemanticWorkflowInputs,
    agents: SemanticWorkflowAgents,
    checkpoint_repository: CheckpointRepository | None = None,
) -> WorkflowGraph:
    """Compose evidence extraction through scenario selection.

    Scoring is deliberately outside this graph slice until its Agent node is
    connected. Call ``run(..., stop_before={WorkflowStage.SCORING})`` during
    the incremental migration.
    """
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
                agents.item_definition.client,
                agents.functions.validator,
            ),
            ItemSupplementAgent(agents.item_definition.client),
            ItemEvidenceRouter(),
            targeted_agent=TargetedProjectFactExtractionAgent(agents.item_definition.client),
            max_workers=inputs.max_workers,
            progress=inputs.progress,
            cache=inputs.artifact_cache,
        ),
    )
    graph.add_node(
        WorkflowStage.ITEM_DEFINITION,
        lambda state: extract_functions(
            state,
            agents.functions,
            state.item_definition["text"],
            state.item_definition["source_id"],
        ),
    )
    graph.add_node(
        WorkflowStage.FUNCTIONS,
        lambda state: assess_guidewords(
            state,
            agents.guidewords,
            [_function(value) for value in state.functions],
            inputs.guidewords,
            max_workers=inputs.max_workers,
            progress=inputs.progress,
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
    """Compose the migrated Agent path through the engineering quality gate."""
    graph = build_semantic_frontend_graph(inputs, agents, checkpoint_repository)
    scenario_node = graph.nodes[WorkflowStage.MALFUNCTIONS]
    downstream_preflight = DownstreamPreflightService(risk_services.safety_goals)
    graph.nodes[WorkflowStage.MALFUNCTIONS] = lambda state: (
        downstream_preflight.validate(state), scenario_node(state)
    )[1]
    graph.add_node(
        WorkflowStage.SCORING,
        lambda state: score_structured_scenarios(
            state,
            risk_services.scoring,
            risk_services.asil_table,
            risk_services.ftti,
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

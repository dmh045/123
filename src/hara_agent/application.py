from __future__ import annotations

import sys
import os

from hara_agent.config import LLMConfig, RunConfig
from hara_agent.domains import default_domain_registry
from hara_agent.infrastructure.llm import LLMClient, create_llm_client
from hara_agent.services.analysis import (
    DomainScenarioCandidateService,
    DomainScoringService,
    FTTIService,
    SafetyGoalCatalogService,
    TemplateASILService,
    ProjectFactResolver,
    SpeedResolutionResult,
    UnresolvedProjectContextError,
)
from hara_agent.models import ItemDefinitionFacts, SourceRef
from hara_agent.services.extraction import TemplateInputReader, ValidatedArtifactCache
from hara_agent.services.reporting import HARAExcelRenderer
from hara_agent.services.semantic import (
    FunctionExtractionAgent,
    ItemDefinitionExtractionAgent,
    GuidewordApplicabilityAgent,
    MalfunctionHazardAgent,
    ScenarioFeasibilityAgent,
)
from hara_agent.workflow import (
    CheckpointRepository,
    HARAState,
    ReportingWorkflowConfig,
    RiskWorkflowServices,
    SemanticWorkflowAgents,
    SemanticWorkflowInputs,
    WorkflowRunResult,
    build_hara_agent_graph,
)


class HARAApplication:
    """Dependency assembly for the migrated Agent path."""

    def __init__(self, config: RunConfig, llm_client: LLMClient):
        self.config = config
        self.llm_client = llm_client

    @classmethod
    def from_env(cls, config: RunConfig, llm_config: LLMConfig | None = None) -> "HARAApplication":
        config.validate()
        return cls(config, create_llm_client(llm_config or LLMConfig.from_env()))

    def run(self) -> WorkflowRunResult:
        self.config.validate()
        if not self.config.domain:
            raise ValueError("必须显式提供domain；迁移期禁止仅凭关键词静默选择Domain Profile")
        runtime = default_domain_registry().create(
            self.config.domain,
            profile_path=self.config.domain_profile_path,
            require_approved=False,
        )
        profile = runtime.profile
        policy = runtime.policy
        template_inputs = TemplateInputReader().read(self.config.template_path)
        candidate_service = DomainScenarioCandidateService(policy)

        def report_batch(name: str, completed: int, total: int) -> None:
            print(
                f"[HARA] {name}: {completed}/{total} completed "
                f"(max_workers={self.config.max_workers})",
                file=sys.stderr,
                flush=True,
            )

        def report_stage(event: str, stage: str, elapsed: float) -> None:
            suffix = f" elapsed={elapsed:.1f}s" if event != "started" else ""
            print(f"[HARA] stage={stage} {event}{suffix}", file=sys.stderr, flush=True)

        def prepare_candidates(state: HARAState):
            return self.prepare_scenario_candidates(state, candidate_service)
        checkpoints = CheckpointRepository(self.config.run_dir)
        renderer = HARAExcelRenderer()
        renderer.contract.validate(self.config.template_path)
        if self.config.resume:
            state = checkpoints.load(self.config.run_id)
        else:
            state = HARAState(
                run_id=self.config.run_id,
                domain=profile.name,
                profile_version=profile.version,
            )
            state.record(
                "template_inputs_loaded",
                template_path=str(template_inputs.source_path),
                guideword_count=len(template_inputs.guidewords),
                scenario_dimension_count=len(template_inputs.scenario_dimensions),
                scoring_standard_counts=template_inputs.scoring_standards.counts,
            )
        graph = build_hara_agent_graph(
            SemanticWorkflowInputs(
                item_path=self.config.item_path,
                guidewords=template_inputs.guidewords,
                scenario_candidate_factory=prepare_candidates,
                max_workers=self.config.max_workers,
                progress=report_batch,
                stage_progress=report_stage,
                artifact_cache=ValidatedArtifactCache(
                    os.getenv("HARA_ARTIFACT_CACHE_DIR", "runtime/agent/artifact-cache"),
                    os.getenv("HARA_ARTIFACT_CACHE_MODE", "readwrite"),
                ),
            ),
            SemanticWorkflowAgents(
                item_definition=ItemDefinitionExtractionAgent(self.llm_client),
                functions=FunctionExtractionAgent(self.llm_client),
                guidewords=GuidewordApplicabilityAgent(self.llm_client),
                malfunctions=MalfunctionHazardAgent(self.llm_client),
                scenarios=ScenarioFeasibilityAgent(self.llm_client),
            ),
            RiskWorkflowServices(
                scoring=DomainScoringService(policy, template_inputs.scoring_standards),
                asil_table=TemplateASILService(str(self.config.template_path)),
                ftti=FTTIService(),
                safety_goals=SafetyGoalCatalogService(profile),
            ),
            checkpoint_repository=checkpoints,
            reporting=ReportingWorkflowConfig(
                template_path=self.config.template_path,
                output_path=self.config.output_path,
                renderer=renderer,
            ),
        )
        result = graph.run(state)
        if (
            self.config.allow_draft
            and result.interrupted
            and result.reason == "pending_engineering_review"
        ):
            output = HARAExcelRenderer().render(
                result.state,
                self.config.template_path,
                self.config.output_path,
                draft=True,
            )
            result.state.record(
                "draft_excel_report_rendered",
                output_path=str(output),
                pending_review_count=len(result.state.pending_reviews),
            )
            checkpoints.save(result.state)
            return WorkflowRunResult(
                result.state,
                True,
                "draft_report_generated_pending_review",
            )
        return result

    def resolve_project_speed_context(self, state: HARAState) -> SpeedResolutionResult | None:
        operating_mode = str(self.config.operating_mode or "").strip()
        if not operating_mode:
            raise UnresolvedProjectContextError(
                operating_mode,
                "production Scenario Candidate preparation requires explicit structured operating_mode",
            )
        if self.config.ego_speed_kph is not None:
            sources = []
            if self.config.ego_speed_source:
                sources.append(SourceRef(
                    "project_input",
                    self.config.ego_speed_source,
                    "CLI --ego-speed-kph",
                    f"ego_speed_kph={self.config.ego_speed_kph:g}",
                ))
            return ProjectFactResolver.explicit_project_speed(
                operating_mode,
                self.config.ego_speed_kph,
                sources=sources,
            )
        typed = state.item_definition.get("typed", {})
        if not isinstance(typed, dict) or not typed:
            if self.config.allow_legacy_speed_fallback:
                return None
            raise UnresolvedProjectContextError(
                operating_mode, "typed ProjectFacts are unavailable"
            )
        facts = ItemDefinitionFacts.from_dict(typed)
        try:
            return ProjectFactResolver().resolve_speed_context(
                facts,
                operating_mode,
                allow_aggregate_fallback=self.config.allow_aggregate_speed_fallback,
            )
        except UnresolvedProjectContextError:
            if self.config.allow_legacy_speed_fallback and not facts.speed_envelopes:
                return None
            raise

    def prepare_scenario_candidates(
        self,
        state: HARAState,
        candidate_service: DomainScenarioCandidateService,
    ):
        typed = state.item_definition.get("typed", {})
        resolution = self.resolve_project_speed_context(state)
        return candidate_service.generate(
            operating_mode=(
                resolution.operating_mode
                if resolution is not None else str(self.config.operating_mode or "")
            ),
            speed_resolution=resolution,
            allow_legacy_speed_fallback=self.config.allow_legacy_speed_fallback,
            project_driver_contexts=list(typed.get("driver_contexts", [])),
            project_exposure_inputs=list(typed.get("exposure_inputs", [])),
        )

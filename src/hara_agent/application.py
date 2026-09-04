from __future__ import annotations

import sys
import os

from hara_agent.config import LLMConfig, RunConfig
from hara_agent.contracts import CompileStatus
from hara_agent.infrastructure.llm import LLMClient, create_llm_client
from hara_agent.services.analysis import (
    MethodContractASILService,
    MethodRuleScoringService,
    MethodRiskFactBindingService,
    MethodScenarioCandidateService,
    MethodSafetyGoalService,
    ProjectFactResolver,
    SpeedResolutionResult,
    UnresolvedProjectContextError,
)
from hara_agent.models import ItemDefinitionFacts, SourceRef
from hara_agent.services.extraction import ValidatedArtifactCache
from hara_agent.services.reporting import HARAExcelRenderer
from hara_agent.services.semantic import (
    GuidewordApplicabilityAgent,
    MalfunctionHazardAgent,
    ScenarioFeasibilityAgent,
    ScenarioRiskFactAgent,
)
from hara_agent.workflow import (
    CheckpointRepository,
    HARAState,
    ReportingWorkflowConfig,
    RiskWorkflowServices,
    SemanticWorkflowAgents,
    SemanticWorkflowInputs,
    WorkflowRunResult,
    ReviewArtifactWriter,
    build_hara_agent_graph,
)
from hara_agent.method_sources import MethodSourceKind, MethodSourceResolver


class HARAApplication:
    """Dependency assembly for the template-driven HARA runtime."""

    def __init__(self, config: RunConfig, llm_client: LLMClient):
        self.config = config
        self.llm_client = llm_client

    @classmethod
    def from_env(cls, config: RunConfig, llm_config: LLMConfig | None = None) -> "HARAApplication":
        config.validate()
        return cls(config, create_llm_client(llm_config or LLMConfig.from_env()))

    def run(self) -> WorkflowRunResult:
        self.config.validate()
        resolution = MethodSourceResolver().resolve(
            template_path=self.config.template_path,
            baseline_manifest_path=self.config.method_baseline_path,
            report_template_path=(
                self.config.template_path
                if self.config.template_path is not None
                else self.config.report_template_path
            ),
        )
        method = resolution.method
        method_ref = {
            "template_hash": method.metadata["template_hash"],
            "contract_version": method.contract_version,
            "compiler_version": method.compiler_version,
            "compile_status": method.compile_status.value,
            "engineering_rules_compiled": method.engineering_rules_compiled,
            "source_kind": resolution.source_kind.value,
            "method_source_hash": method.metadata.get("method_source_hash", method.metadata["template_hash"]),
            "report_template_hash": resolution.report_template_hash,
        }
        # Preserve the compiled template semantics.  The semantic agent still
        # accepts plain strings for tests/embedders, but production must not
        # discard the normative description and source binding here.
        guidewords = list(method.guidewords.guidewords)
        candidate_service = MethodScenarioCandidateService(method)

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
        review_artifact_writer = ReviewArtifactWriter(
            self.config.run_id,
            os.getenv("HARA_REVIEW_ARTIFACT_DIR", "runtime/review"),
        )
        renderer = HARAExcelRenderer(
            method.report_contract,
            # This is the independently resolved report-layout hash. In
            # Template mode it happens to equal the method-source hash; in
            # YAML mode it intentionally does not.
            template_hash=resolution.report_template_hash,
        )
        if self.config.resume:
            state = checkpoints.load(self.config.run_id)
            checkpoint_hash = state.method_contract.get("template_hash")
            if not checkpoint_hash and state.stage.value != "initialize":
                raise ValueError(
                    "Checkpoint predates MethodContract template binding; "
                    "restart the run instead of reusing unbound derived outputs"
                )
            if checkpoint_hash and checkpoint_hash != method_ref["template_hash"]:
                raise ValueError(
                    "Checkpoint template hash does not match the active MethodContract: "
                    f"checkpoint={checkpoint_hash}, active={method_ref['template_hash']}"
                )
            state.method_contract = method_ref
        else:
            state = HARAState(
                run_id=self.config.run_id,
                method_contract=method_ref,
            )
            state.record(
                "method_contract_compiled",
                template_path=(str(self.config.template_path) if self.config.template_path else ""),
                method_baseline_path=(
                    str(self.config.method_baseline_path)
                    if resolution.source_kind is MethodSourceKind.YAML_BASELINE else ""
                ),
                report_template_path=str(resolution.report_template_path),
                **method_ref,
                guideword_count=len(guidewords),
                scenario_dimension_count=len(method.scenario_model.dimensions),
                scoring_standard_counts={
                    "severity_rules": len(method.severity.rules),
                    "exposure_duration_rules": len(method.exposure.duration_rules),
                    "exposure_frequency_rules": len(method.exposure.frequency_rules),
                    "controllability_rules": len(method.controllability.criteria),
                },
                required_fact_types=[
                    item.fact_type.value for item in method.required_fact_specs
                ],
                warning_codes=sorted({item.code.value for item in method.warnings}),
            )
        graph = build_hara_agent_graph(
            SemanticWorkflowInputs(
                item_path=self.config.item_path,
                guidewords=guidewords,
                scenario_candidate_factory=prepare_candidates,
                project_context_preflight=(
                    lambda state: self.resolve_project_speed_context(state).to_dict()
                ),
                max_workers=self.config.max_workers,
                progress=report_batch,
                stage_progress=report_stage,
                artifact_cache=ValidatedArtifactCache(
                    os.getenv("HARA_ARTIFACT_CACHE_DIR", "runtime/agent/artifact-cache"),
                    os.getenv("HARA_ARTIFACT_CACHE_MODE", "readwrite"),
                ),
                required_fact_specs=method.required_fact_specs,
                method_contract=method,
                requested_operating_modes=(
                    (str(self.config.operating_mode),)
                    if self.config.operating_mode else ()
                ),
                review_artifact_writer=review_artifact_writer,
            ),
            SemanticWorkflowAgents(
                client=self.llm_client,
                guidewords=GuidewordApplicabilityAgent(self.llm_client),
                malfunctions=MalfunctionHazardAgent(
                    self.llm_client,
                    component_categories=(
                        tuple(sorted({
                            category
                            for rule in method.structured_risk_method.exposure.domain_rules
                            for category in rule.component_categories
                        }))
                        if method.structured_risk_method is not None else ()
                    ),
                ),
                scenarios=ScenarioFeasibilityAgent(self.llm_client),
                risk_facts=ScenarioRiskFactAgent(self.llm_client),
            ),
            RiskWorkflowServices(
                scoring=MethodRuleScoringService(method),
                asil_table=MethodContractASILService(method),
                safety_goals=MethodSafetyGoalService(method),
                risk_fact_binding=MethodRiskFactBindingService(method),
            ),
            checkpoint_repository=checkpoints,
            reporting=ReportingWorkflowConfig(
                template_path=resolution.report_template_path,
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
            output = renderer.render(
                result.state,
                resolution.report_template_path,
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

    def resolve_project_speed_context(self, state: HARAState) -> SpeedResolutionResult:
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
            raise UnresolvedProjectContextError(
                operating_mode, "typed ProjectFacts are unavailable"
            )
        facts = ItemDefinitionFacts.from_dict(typed)
        return ProjectFactResolver().resolve_speed_context(
            facts,
            operating_mode,
            allow_aggregate_fallback=self.config.allow_aggregate_speed_fallback,
        )

    def prepare_scenario_candidates(
        self,
        state: HARAState,
        candidate_service: MethodScenarioCandidateService,
    ):
        typed = state.item_definition.get("typed", {})
        if not isinstance(typed, dict) or not typed:
            raise UnresolvedProjectContextError(
                str(self.config.operating_mode or ""),
                "typed ProjectFacts are unavailable",
            )
        facts = ItemDefinitionFacts.from_dict(typed)
        resolution = self.resolve_project_speed_context(state)
        return candidate_service.generate(
            project_facts=facts,
            operating_mode=resolution.operating_mode,
            speed_resolution=resolution,
        )

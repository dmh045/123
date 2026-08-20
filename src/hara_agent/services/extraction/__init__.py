from .document_reader import DocumentArtifact, DocumentBlock, DocumentReader
from .template_inputs import TemplateInputReader, TemplateInputs
from .template_scoring import (
    TemplateScoreLevel,
    TemplateScoringStandards,
    scoring_standards_from_method_contract,
)
from .artifact_cache import ValidatedArtifactCache
from .targeted_verification import (
    DeterministicSourceVerifier,
    RequiredFactQuery,
    VerifiedSourceBlock,
    block_value,
    normalize_source_text,
    verify_missing_fact,
)
from .evidence_retrieval import (
    DEFAULT_CONTEXT_CHARACTER_BUDGET,
    PROJECT_EVIDENCE_SPECS,
    ContextAssemblyResult,
    CoverageFirstContextAssembler,
    DeterministicEvidenceRetriever,
    FactRetrievalSpec,
    RoutingDiagnostics,
    ScoredEvidenceBlock,
)

__all__ = [
    "DocumentArtifact", "DocumentBlock", "DocumentReader",
    "TemplateInputReader", "TemplateInputs", "TemplateScoreLevel", "TemplateScoringStandards",
    "scoring_standards_from_method_contract",
    "ValidatedArtifactCache",
    "DeterministicSourceVerifier", "RequiredFactQuery", "VerifiedSourceBlock",
    "block_value", "normalize_source_text", "verify_missing_fact",
    "DEFAULT_CONTEXT_CHARACTER_BUDGET", "PROJECT_EVIDENCE_SPECS",
    "ContextAssemblyResult", "CoverageFirstContextAssembler",
    "DeterministicEvidenceRetriever", "FactRetrievalSpec", "RoutingDiagnostics",
    "ScoredEvidenceBlock", "ProjectFactNormalizationFailure",
    "ProjectFactNormalizationResult", "ProjectFactNormalizer",
    "RequiredProjectFactSpec", "SPEED_PROJECT_FACT_SPECS",
    "PERFORMANCE_PROJECT_FACT_SPECS", "DRIVER_PROJECT_FACT_SPECS",
    "PROJECT_FACT_SPEC_BATCHES",
]
from .project_fact_normalization import (
    ProjectFactNormalizationFailure, ProjectFactNormalizationResult, ProjectFactNormalizer,
)
from .project_fact_specs import (
    DRIVER_PROJECT_FACT_SPECS, PERFORMANCE_PROJECT_FACT_SPECS,
    PROJECT_FACT_SPEC_BATCHES, SPEED_PROJECT_FACT_SPECS, RequiredProjectFactSpec,
)

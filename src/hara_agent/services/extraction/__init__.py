from .document_reader import DocumentArtifact, DocumentBlock, DocumentReader
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
    ContextAssemblyResult,
    CoverageFirstContextAssembler,
    DeterministicEvidenceRetriever,
    FactRetrievalSpec,
    RoutingDiagnostics,
    ScoredEvidenceBlock,
)

__all__ = [
    "DocumentArtifact", "DocumentBlock", "DocumentReader",
    "ValidatedArtifactCache",
    "DeterministicSourceVerifier", "RequiredFactQuery", "VerifiedSourceBlock",
    "block_value", "normalize_source_text", "verify_missing_fact",
    "DEFAULT_CONTEXT_CHARACTER_BUDGET",
    "ContextAssemblyResult", "CoverageFirstContextAssembler",
    "DeterministicEvidenceRetriever", "FactRetrievalSpec", "RoutingDiagnostics",
    "ScoredEvidenceBlock", "ProjectFactNormalizationFailure",
    "ProjectFactNormalizationResult", "ProjectFactNormalizer",
    "RequiredProjectFactSpec", "build_project_fact_spec_batches",
]
from .project_fact_normalization import (
    ProjectFactNormalizationFailure, ProjectFactNormalizationResult, ProjectFactNormalizer,
)
from .project_fact_specs import RequiredProjectFactSpec, build_project_fact_spec_batches

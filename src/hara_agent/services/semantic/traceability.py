from __future__ import annotations

from hara_agent.models import FunctionDefinition, GuidewordAssessment, SourceRef


def deduplicate_sources(sources: list[SourceRef]) -> list[SourceRef]:
    """Stable SourceRef deduplication without mutating upstream evidence."""
    result, seen = [], set()
    for source in sources:
        identity = (source.source_type, source.source_id, source.location, source.excerpt)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(source)
    return result


def traceable_sources(sources: list[SourceRef]) -> list[SourceRef]:
    return deduplicate_sources([
        source for source in sources
        if source.source_id.strip() and source.location.strip()
    ])


def resolve_guideword_sources(function: FunctionDefinition) -> list[SourceRef]:
    return traceable_sources(list(function.sources))


def resolve_malfunction_sources(
    function: FunctionDefinition,
    assessment: GuidewordAssessment | None,
) -> tuple[list[SourceRef], str]:
    if assessment is not None:
        guideword_sources = traceable_sources(list(assessment.sources))
        if guideword_sources:
            return guideword_sources, "guideword"
    function_sources = traceable_sources(list(function.sources))
    if function_sources:
        return function_sources, "function"
    return [], "unresolved"

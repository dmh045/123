from __future__ import annotations

import pytest

from hara_agent.models import (
    FactProvenance,
    ItemDefinitionFacts,
    SourceRef,
    SpeedEnvelope,
)
from hara_agent.services.analysis import (
    ProjectContextResolutionStatus,
    ProjectFactResolver,
    UnresolvedProjectContextError,
)


def _source() -> SourceRef:
    return SourceRef("item_definition", "ItemDef.docx", "table[14]", "mode speeds")


def _mode_facts() -> ItemDefinitionFacts:
    source = _source()
    return ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle motion control",
        speed_envelopes=[
            SpeedEnvelope("search", 0, 30, sources=[source]),
            SpeedEnvelope("parking", 0, 5, sources=[source]),
            SpeedEnvelope("control", 0, 7, sources=[source]),
        ],
        sources=[source],
    )


@pytest.mark.parametrize(
    ("operating_mode", "expected_speed"),
    [("parking", 5.0), ("search", 30.0), ("control", 7.0)],
)
def test_structured_mode_selects_matching_speed_envelope(operating_mode, expected_speed):
    result = ProjectFactResolver().resolve_speed_context(_mode_facts(), operating_mode)

    assert result.resolved_value == expected_speed
    assert result.operating_mode == operating_mode
    assert result.resolution_source == "SpeedEnvelope"
    assert result.provenance is FactProvenance.PROJECT_INPUT
    assert result.fallback_used is False
    assert result.resolution_status is ProjectContextResolutionStatus.RESOLVED


def test_unknown_context_fails_closed_with_typed_diagnostic():
    with pytest.raises(UnresolvedProjectContextError) as captured:
        ProjectFactResolver().resolve_speed_context(_mode_facts(), "highway")

    assert captured.value.to_dict()["resolution_status"] == "UNRESOLVED_PROJECT_CONTEXT"
    assert captured.value.to_dict()["operating_mode"] == "highway"


def test_multiple_contexts_cannot_be_collapsed_by_aggregate_fallback():
    with pytest.raises(UnresolvedProjectContextError, match="cannot collapse"):
        ProjectFactResolver().resolve_speed_context(
            _mode_facts(), "highway", allow_aggregate_fallback=True,
        )


def test_aggregate_fallback_is_explicit_and_diagnostic():
    source = _source()
    facts = ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle motion control",
        speed_min_kph=0,
        speed_max_kph=30,
        sources=[source],
    )

    result = ProjectFactResolver().resolve_speed_context(
        facts, "parking", allow_aggregate_fallback=True,
    )

    assert result.resolved_value == 30.0
    assert result.resolution_source == "AggregateSpeedEnvelope"
    assert result.provenance is FactProvenance.DERIVED
    assert result.fallback_used is True
    assert result.resolution_status is ProjectContextResolutionStatus.RESOLVED_AGGREGATE_FALLBACK


def test_legacy_profile_speed_never_claims_project_input_provenance():
    result = ProjectFactResolver.legacy_migration_speed("parking", 5, _source())

    assert result.provenance is FactProvenance.LEGACY_MIGRATION
    assert result.fallback_used is True
    assert result.resolution_status is ProjectContextResolutionStatus.RESOLVED_LEGACY_FALLBACK
    assert result.to_dict()["provenance"] == "LEGACY_MIGRATION"

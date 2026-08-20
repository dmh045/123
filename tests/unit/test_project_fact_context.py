from __future__ import annotations

import pytest

from hara_agent.models import ItemDefinitionFacts, SourceRef, SpeedEnvelope
from hara_agent.models import FactProvenance
from hara_agent.domains import load_domain_profile
from hara_agent.services.analysis import ProjectFactResolutionError, ProjectFactResolver


def project_facts() -> ItemDefinitionFacts:
    source = SourceRef("item_definition", "ItemDef.docx", "table[14].row[15]", "speeds")
    return ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle motion control",
        speed_min_kph=0,
        speed_max_kph=30,
        speed_envelopes=[
            SpeedEnvelope("search", 0, 30, sources=[source]),
            SpeedEnvelope("parking", 0, 5, sources=[source]),
            SpeedEnvelope("control", 0, 7, sources=[source]),
        ],
        sources=[source],
    )


def test_parking_context_does_not_inherit_search_global_maximum():
    assert ProjectFactResolver().resolve_speed_kph(project_facts(), "parking") == 5


def test_unknown_mode_fails_closed_by_default():
    with pytest.raises(ProjectFactResolutionError, match="not authorized"):
        ProjectFactResolver().resolve_speed_kph(project_facts(), "highway")


def test_aggregate_fallback_requires_explicit_authorization():
    source = SourceRef("item_definition", "ItemDef.docx", "table[14].row[15]", "speeds")
    aggregate_only = ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle motion control",
        speed_min_kph=0,
        speed_max_kph=30,
        sources=[source],
    )
    envelope = ProjectFactResolver().resolve_speed_envelope(
        aggregate_only, "highway", allow_aggregate_fallback=True,
    )
    assert envelope.speed_max_kph == 30
    assert envelope.condition == "explicit aggregate compatibility fallback"


def test_unapproved_migration_profile_is_labelled_legacy_assumption():
    profile = load_domain_profile("avp", require_approved=False)
    assert profile.approval_status == "migration_baseline"
    assert profile.fact_provenance is FactProvenance.LEGACY_MIGRATION

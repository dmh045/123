from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from hara_agent.application import HARAApplication
from hara_agent.config import RunConfig
from hara_agent.domains import default_domain_registry
from hara_agent.models import (
    FactProvenance,
    ItemDefinitionFacts,
    ScenarioCandidate,
    SourceRef,
    SpeedEnvelope,
)
from hara_agent.services.analysis import (
    DomainScenarioCandidateService,
    UnresolvedProjectContextError,
)
from hara_agent.services.semantic.scenario_contract import SCENARIO_CONTRACT_VERSION
from hara_agent.workflow import HARAState


def _config(**changes) -> RunConfig:
    base = RunConfig(
        item_path=Path("ItemDef.docx"),
        template_path=Path("template.xlsx"),
        output_path=Path("output.xlsx"),
        run_dir=Path("runtime"),
        domain="avp",
        operating_mode="parking",
    )
    return replace(base, **changes)


def _facts(*, contextual: bool = True, aggregate: bool = True) -> ItemDefinitionFacts:
    source = SourceRef("item_definition", "ItemDef.docx", "table[14]", "mode speeds")
    return ItemDefinitionFacts(
        system_description="AVP",
        item_boundary="vehicle motion control",
        speed_min_kph=0 if aggregate else None,
        speed_max_kph=30 if aggregate else None,
        speed_envelopes=(
            [
                SpeedEnvelope("search", 0, 30, sources=[source]),
                SpeedEnvelope("parking", 0, 5, sources=[source]),
                SpeedEnvelope("control", 0, 7, sources=[source]),
            ]
            if contextual else []
        ),
        driver_contexts=[{
            "context_id": "remote_driver",
            "driver_position": "outside",
            "driver_state": "remote monitoring",
            "direct_vehicle_control": False,
            "intervention_channels": ["remote_stop"],
        }],
        sources=[source],
    )


def _state(facts: ItemDefinitionFacts) -> HARAState:
    return HARAState(
        run_id="project-context-test",
        item_definition={"source_id": "ItemDef.docx", "typed": asdict(facts)},
    )


def _service() -> DomainScenarioCandidateService:
    runtime = default_domain_registry().create("avp", require_approved=False)
    return DomainScenarioCandidateService(runtime.policy)


@pytest.mark.parametrize(
    ("operating_mode", "expected_speed"),
    [("parking", 5.0), ("search", 30.0), ("control", 7.0)],
)
def test_application_resolves_structured_mode_without_llm(operating_mode, expected_speed):
    app = HARAApplication(_config(operating_mode=operating_mode), object())

    result = app.resolve_project_speed_context(_state(_facts()))

    assert result is not None
    assert result.resolved_value == expected_speed
    assert result.operating_mode == operating_mode


def test_application_candidate_path_uses_parking_envelope_and_provenance():
    app = HARAApplication(_config(), object())

    candidates, audit = app.prepare_scenario_candidates(_state(_facts()), _service())

    assert candidates
    assert audit["ego_speed_kph"] == 5.0
    assert audit["speed_resolution"]["resolution_source"] == "SpeedEnvelope"
    assert all(item.operating_mode == "parking" for item in candidates)
    assert all(item.facts["ego_speed_kph"] == 5.0 for item in candidates)
    assert all(
        item.fact_provenance["ego_speed_kph"]["provenance"] == "PROJECT_INPUT"
        for item in candidates
    )


def test_application_fails_closed_without_or_with_unknown_structured_mode():
    with pytest.raises(UnresolvedProjectContextError):
        HARAApplication(_config(operating_mode=None), object()).resolve_project_speed_context(
            _state(_facts())
        )
    with pytest.raises(UnresolvedProjectContextError):
        HARAApplication(_config(operating_mode="highway"), object()).resolve_project_speed_context(
            _state(_facts())
        )


def test_application_aggregate_fallback_requires_explicit_authorization():
    state = _state(_facts(contextual=False, aggregate=True))
    with pytest.raises(UnresolvedProjectContextError):
        HARAApplication(_config(), object()).resolve_project_speed_context(state)

    result = HARAApplication(
        _config(allow_aggregate_speed_fallback=True), object()
    ).resolve_project_speed_context(state)
    assert result is not None
    assert result.provenance is FactProvenance.DERIVED
    assert result.fallback_used is True


def test_legacy_profile_fallback_is_explicit_and_preserves_migration_provenance():
    state = _state(_facts(contextual=False, aggregate=False))
    app = HARAApplication(_config(allow_legacy_speed_fallback=True), object())

    candidates, audit = app.prepare_scenario_candidates(state, _service())

    assert audit["speed_source_status"] == "MIGRATION_FALLBACK"
    assert audit["speed_resolution"]["provenance"] == "LEGACY_MIGRATION"
    assert all(
        item.fact_provenance["ego_speed_kph"]["provenance"] == "LEGACY_MIGRATION"
        for item in candidates
    )


def test_scenario_context_and_provenance_survive_checkpoint_roundtrip():
    candidate = ScenarioCandidate(
        scenario_id="SCN-PARKING",
        operating_scenario="parking",
        situational_description="parking",
        situational_detailing="speed 5 km/h",
        facts={"ego_speed_kph": 5.0, "operating_mode": "parking"},
        operating_mode="parking",
        context_resolution={"ego_speed_kph": {"resolved_value": 5.0}},
        fact_provenance={"ego_speed_kph": {"provenance": "PROJECT_INPUT"}},
        source_scenario_id="VRU_crossing",
        atomic_variant="crossing",
        semantic_fingerprint="abc123",
        scenario_contract_version=SCENARIO_CONTRACT_VERSION,
    )
    state = HARAState(run_id="roundtrip", scenarios=[candidate])

    restored = HARAState.from_dict(state.to_dict()).scenarios[0]

    assert restored.operating_mode == "parking"
    assert restored.context_resolution == candidate.context_resolution
    assert restored.fact_provenance == candidate.fact_provenance
    assert restored.source_scenario_id == "VRU_crossing"
    assert restored.atomic_variant == "crossing"
    assert restored.semantic_fingerprint == "abc123"

from __future__ import annotations

from pathlib import Path

import pytest

from hara_agent.method_sources import YamlBaselineCompiler
from hara_agent.services.analysis import RiskVocabularyAdapter
from hara_agent.template import TemplateRoleCompiler


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def adapter() -> RiskVocabularyAdapter:
    report = TemplateRoleCompiler().compile_method(
        ROOT / "references/HARA_Template_AI_20260327.xlsx"
    ).report_contract
    method = YamlBaselineCompiler().compile(
        ROOT / "method_assets/fusa_baseline_v1/manifest.yaml",
        report_contract=report,
    )
    return RiskVocabularyAdapter(method)


@pytest.mark.parametrize(("field", "raw", "canonical"), [
    ("road_user_type", "passenger_car", "VEHICLE"),
    ("road_user_type", "pedestrian", "PEDESTRIAN"),
    ("collision_type", "front", "FRONTAL"),
    ("collision_type", "rear", "REAR_END"),
    ("collision_type", "side", "SIDE"),
])
def test_exact_source_supported_vocabulary_mapping(adapter, field, raw, canonical):
    resolution = adapter.resolve(field=field, raw_value=raw)
    assert resolution.canonical_value == canonical
    assert resolution.mapping_rule_id
    assert resolution.mapping_source
    assert resolution.source_ref
    assert resolution.method_contract_hash


@pytest.mark.parametrize("raw", ["static_obstacle", "none", "unknown"])
def test_unapproved_road_user_values_never_fall_back_to_vehicle(adapter, raw):
    assert adapter.resolve(field="road_user_type", raw_value=raw).mapped is False

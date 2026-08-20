from pathlib import Path

import pytest

from hara_agent.contracts import FactType
from hara_agent.models import (
    FactProvenance,
    ItemDefinitionFacts,
    MethodRiskFactBinding,
    ReviewStatus,
    RiskFact,
    SourceRef,
)
from hara_agent.services.analysis import (
    MethodRiskFactBindingService,
    MethodRuleScoringService,
)
from hara_agent.template import TemplateRoleCompiler


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "references" / "HARA_Template_AI_20260327.xlsx"


def _method():
    return TemplateRoleCompiler().compile_method(TEMPLATE, use_manifest=False)


def _source(field: str) -> SourceRef:
    return SourceRef(
        "human_confirmation", "review-42", field, f"confirmed {field}"
    )


def _risk_fact(
    fact_type: FactType,
    value: str | float,
    method_hash: str,
    *,
    unit: str = "",
    context: dict[str, str] | None = None,
    approval: ReviewStatus = ReviewStatus.FINALIZED,
) -> tuple[RiskFact, MethodRiskFactBinding]:
    suffix = "-".join(f"{key}-{value}" for key, value in sorted((context or {}).items()))
    fact_id = f"RF-{fact_type.value}-{suffix or 'GLOBAL'}"
    fact = RiskFact(
        fact_id=fact_id,
        parameter=fact_type.value.casefold(),
        value=value,
        unit=unit,
        context=context or {},
        source_refs=[_source(fact_type.value)],
        provenance=FactProvenance.PROJECT_INPUT,
        approval=approval,
        produced_by="source_extraction",
    )
    binding = MethodRiskFactBinding(
        source_fact_id=fact_id,
        target_fact_type=fact_type.value,
        method_contract_hash=method_hash,
        source_refs=[_source(f"binding:{fact_type.value}")],
        provenance=FactProvenance.HUMAN_CONFIRMATION,
        approval=approval,
        binding_method="engineering_review",
    )
    return fact, binding


def _project_facts(
    entries: list[tuple[RiskFact, MethodRiskFactBinding]],
) -> ItemDefinitionFacts:
    return ItemDefinitionFacts(
        system_description="system",
        item_boundary="boundary",
        risk_facts=[item[0] for item in entries],
        method_risk_fact_bindings=[item[1] for item in entries],
        sources=[SourceRef("item_definition", "item.docx", "p1", "system")],
    )


def test_hash_bound_risk_facts_feed_compiled_rules_with_exact_provenance():
    method = _method()
    method_hash = str(method.metadata["template_hash"])
    facts = _project_facts([
        _risk_fact(FactType.COLLISION_TYPE, "VEHICLE_TO_ROAD_USER", method_hash),
        _risk_fact(FactType.ROAD_USER_TYPE, "PEDESTRIAN", method_hash),
        _risk_fact(FactType.SPEED_UNSPECIFIED, 20.0, method_hash, unit="km/h"),
        _risk_fact(FactType.EXPOSURE, "F", method_hash),
        _risk_fact(FactType.OCCURRENCE_FREQUENCY, "MONTHLY_OR_MORE", method_hash),
        _risk_fact(FactType.AVOIDABILITY_PERCENT, 99.5, method_hash, unit="%"),
    ])

    bound = MethodRiskFactBindingService(method).bind(
        facts, {"operating_mode": "parking", "scenario_id": "SCN-1"}
    )
    scored = MethodRuleScoringService(method).score(
        {**bound.values, "_fact_provenance": bound.provenance}, "hazard"
    )

    assert bound.audit["missing_fact_types"] == ["DURATION_PERCENT"]
    assert bound.values["exposure_method"] == "F"
    assert bound.provenance["collision_type"]["provenance"] == "HUMAN_CONFIRMATION"
    assert scored["severity"]["severity_score"] == "S2"
    assert scored["severity"]["engineering_status"] == "PENDING"
    assert scored["exposure"]["engineering_status"] == "FINALIZED"
    assert scored["controllability"]["engineering_status"] == "FINALIZED"


def test_different_template_binding_does_not_invalidate_reusable_exact_fact():
    method = _method()
    fact = _risk_fact(FactType.EXPOSURE, "F", "0" * 64)

    bound = MethodRiskFactBindingService(method).bind(
        _project_facts([fact]), {"operating_mode": "parking"}
    )

    assert bound.values["exposure_method"] == "F"
    assert bound.audit["template_hash_mismatch_count"] == 1
    assert bound.audit["automatic_binding_count"] == 1
    assert "EXPOSURE" not in bound.audit["missing_fact_types"]


def test_more_specific_context_wins_and_equal_specificity_conflict_fails_closed():
    method = _method()
    method_hash = str(method.metadata["template_hash"])
    global_fact = _risk_fact(FactType.EXPOSURE, "T", method_hash)
    mode_fact = _risk_fact(
        FactType.EXPOSURE, "F", method_hash, context={"operating_mode": "parking"}
    )
    bound = MethodRiskFactBindingService(method).bind(
        _project_facts([global_fact, mode_fact]), {"operating_mode": "parking"}
    )
    assert bound.values["exposure_method"] == "F"

    driver_fact = _risk_fact(
        FactType.EXPOSURE, "T", method_hash,
        context={"driver_context_id": "remote"},
    )
    conflicted = MethodRiskFactBindingService(method).bind(
        _project_facts([mode_fact, driver_fact]),
        {"operating_mode": "parking", "driver_context_id": "remote"},
    )
    assert "exposure_method" not in conflicted.values
    assert conflicted.audit["conflicting_fact_types"] == ["EXPOSURE"]


def test_noncanonical_parameter_requires_separate_confirmed_binding():
    method = _method()
    fact = RiskFact(
        fact_id="RF-SPEED-1",
        parameter="measured vehicle speed",
        value=20.0,
        unit="km/h",
        source_refs=[_source("vehicle speed")],
        provenance=FactProvenance.PROJECT_INPUT,
        approval=ReviewStatus.FINALIZED,
    )

    bound = MethodRiskFactBindingService(method).bind(
        ItemDefinitionFacts(
            system_description="system",
            item_boundary="boundary",
            risk_facts=[fact],
            sources=[SourceRef("item_definition", "item.docx", "p1", "system")],
        ),
        {"operating_mode": "parking"},
    )

    assert "speed_unspecified_kph" not in bound.values
    assert bound.audit["automatic_binding_count"] == 0
    assert "SPEED_UNSPECIFIED" in bound.audit["missing_fact_types"]


def test_finalized_llm_risk_fact_is_rejected_by_model():
    with pytest.raises(ValueError, match="cannot rely on LLM inference alone"):
        RiskFact(
            fact_id="RF-EXPOSURE",
            parameter="exposure method",
            value="F",
            source_refs=[_source("EXPOSURE")],
            provenance=FactProvenance.LLM_INFERENCE,
            approval=ReviewStatus.FINALIZED,
        )
